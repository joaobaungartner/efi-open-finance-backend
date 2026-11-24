import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha512
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, create_engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from commit_proxy import commit_proxy
from config.settings import Settings
from database import database
from models.efi_pix_payment_model import EfiPixPaymentModel
from repositories.efi_pix_payment_repository import EfiPixPaymentRepository, PixIdempotencyConflict
from services.efi_api_client import EfiOpenFinanceClient
from services.efi_pix_payment_service import EfiPixPaymentService

HOST = "pix-postgres"
http_client = httpx.Client


@pytest.fixture(scope="module")
def postgres():
    name = os.environ.get("PIX_IT_DATABASE")
    if name is None:
        pytest.skip("Execute com o runner PostgreSQL descartável")
    assert Path("/.dockerenv").is_file(), "A suíte PostgreSQL exige o container de testes"
    password = os.environ.get("PIX_IT_PASSWORD", "")
    assert re.fullmatch(r"pix_it_[0-9a-f]{32}", name)
    assert re.fullmatch(r"[0-9a-f]{32}", password)
    url = URL.create(
        "postgresql+psycopg",
        username=name,
        password=password,
        host=HOST,
        port=5432,
        database=name,
        query={"sslmode": "disable", "gssencmode": "disable", "connect_timeout": "3"},
    )
    engine = create_engine(url, pool_pre_ping=True)
    try:
        # Only readiness is retried. Payment submission is never retried here.
        deadline = time.monotonic() + 45
        while True:
            try:
                with engine.connect() as conn:
                    assert conn.execute(text("SELECT current_database(), current_user")).one() == (
                        name,
                        name,
                    )
                    assert conn.dialect.server_version_info[0] == 16
                break
            except OperationalError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
        with patch.object(database, "engine", engine):
            command.upgrade(Config("alembic.ini"), "head")
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def block_http(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("HTTP externo bloqueado")

    monkeypatch.setattr(httpx, "Client", deny)
    monkeypatch.setattr(httpx, "AsyncClient", deny)


class EfiFake:
    settings = SimpleNamespace(efi_environment="homologation")

    def __init__(self, timeout=False):
        self.timeout = timeout
        self.posts = []
        self.lock = Lock()

    def start_pix(self, payload, *, idempotency_key):
        with self.lock:
            self.posts.append((payload["pagamento"]["idProprio"], idempotency_key))
        if self.timeout:
            raise httpx.ReadTimeout("timeout fictício")
        return {
            "identificadorPagamento": f"urn:efi:teste:{payload['pagamento']['idProprio']}",
            "redirectURI": "https://banco.example/authorize",
        }


def submit(repo, client, key, amount="10.00"):
    return EfiPixPaymentService(repo, client).submit(
        idempotency_key=key,
        origin_institution="participante-teste",
        payload={
            "pagador": {"idParticipante": "teste", "cpf": "52998224725"},
            "favorecido": {"chave": "chave-ficticia"},
            "pagamento": {"valor": amount},
        },
    )


def records(engine, key):
    with Session(engine) as session:
        return session.scalars(
            select(EfiPixPaymentModel).where(EfiPixPaymentModel.idempotency_key == key)
        ).all()


def replay(engine, client, key):
    with Session(engine) as session:
        return submit(EfiPixPaymentRepository(session), client, key)


def cut_submission(engine, client, key, method, when):
    with commit_proxy((HOST, 5432)) as proxy:
        via_proxy = create_engine(
            engine.url.set(host="127.0.0.1", port=proxy.server_address[1]),
            pool_pre_ping=True,
        )
        try:
            with Session(via_proxy) as session:
                repo = EfiPixPaymentRepository(session)
                operation = getattr(repo, method)

                def with_cut(*args, **kwargs):
                    with proxy.cut_commit(when):
                        return operation(*args, **kwargs)

                with patch.object(repo, method, with_cut):
                    return submit(repo, client, key)
        finally:
            via_proxy.dispose()


@pytest.mark.parametrize(
    ("method", "when", "status"),
    [
        ("reserve", "before", None),
        ("reserve", "after", "draft"),
        ("claim_for_submission", "after", "submitting"),
    ],
)
def test_reserva_e_reivindicacao(postgres, method, when, status):
    key = f"a-{method}-{when}"
    client = EfiFake()
    with pytest.raises(OperationalError):
        cut_submission(postgres, client, key, method, when)
    rows = records(postgres, key)
    assert client.posts == []
    if status is None:
        assert rows == []
        # Only an explicit resubmission of a request that was never reserved can send.
        assert replay(postgres, client, key)["status"] == "awaiting_authorization"
        assert replay(postgres, client, key)["status"] == "awaiting_authorization"
        assert len(records(postgres, key)) == len(client.posts) == 1
    else:
        assert len(rows) == 1 and rows[0].status == status
        assert replay(postgres, client, key)["status"] == status
        assert client.posts == []


@pytest.mark.parametrize("when", ["before", "after"])
def test_persistencia_autorizacao(postgres, when):
    key = f"b-{when}"
    client = EfiFake()
    result = cut_submission(postgres, client, key, "mark_awaiting_authorization", when)
    [stored] = records(postgres, key)
    expected = "unknown" if when == "before" else "awaiting_authorization"
    assert stored.status == result["status"] == expected
    assert stored.efi_error_code == ("local_persistence_error" if when == "before" else None)
    assert stored.efi_http_status is None
    assert stored.redirect_uri == (None if when == "before" else "https://banco.example/authorize")
    assert stored.efi_payment_id == (None if when == "before" else f"urn:efi:teste:{stored.id_proprio}")
    assert replay(postgres, client, key)["status"] == expected
    assert len(client.posts) == 1


@pytest.mark.parametrize("when", ["before", "after"])
def test_persistencia_unknown(postgres, when):
    key = f"c-{when}"
    client = EfiFake(timeout=True)
    with pytest.raises(OperationalError):
        cut_submission(postgres, client, key, "mark_unknown", when)
    [stored] = records(postgres, key)
    expected = "submitting" if when == "before" else "unknown"
    assert stored.status == expected
    assert stored.efi_error_code == (None if when == "before" else "efi_timeout")
    assert stored.efi_http_status is None
    assert replay(postgres, client, key)["status"] == expected
    assert len(client.posts) == 1


@pytest.mark.parametrize("second_amount", ["10.00", "20.00"])
def test_concorrencia(postgres, second_amount):
    key = f"d-{second_amount}"
    client = EfiFake()
    start = Barrier(2, timeout=10)

    def send(amount):
        with Session(postgres) as session:
            # Acquire both independent connections before the race.
            pid = session.scalar(text("SELECT pg_backend_pid()"))
            start.wait()
            try:
                return pid, submit(EfiPixPaymentRepository(session), client, key, amount)
            except PixIdempotencyConflict as exc:
                return pid, exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(send, "10.00")
        second = executor.submit(send, second_amount)
        (first_pid, a), (second_pid, b) = first.result(timeout=20), second.result(timeout=20)
    assert first_pid != second_pid
    [stored] = records(postgres, key)
    assert stored.status == "awaiting_authorization"
    assert stored.id_proprio
    expected_key = sha512(f"efi-pix-v1:{stored.id_proprio}".encode("ascii")).hexdigest()[:72]
    assert client.posts == [(stored.id_proprio, expected_key)]
    if second_amount == "10.00":
        assert a["id"] == b["id"] == stored.id
        assert a["id_proprio"] == b["id_proprio"] == stored.id_proprio
    else:
        assert sum(isinstance(item, PixIdempotencyConflict) for item in (a, b)) == 1


@pytest.mark.parametrize("oauth", [True, False], ids=["oauth", "post-pix"])
def test_separacao_oauth_e_pix(postgres, monkeypatch, oauth):
    key = f"e-{oauth}"
    calls = []

    def respond(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/oauth/token" and not oauth:
            return httpx.Response(200, json={"access_token": "token-ficticio"})
        return httpx.Response(401, json={"mensagem": "dado-ficticio"})

    client = EfiOpenFinanceClient(
        Settings(_env_file=None, efi_client_id="ficticio", efi_client_secret="ficticio")
    )
    monkeypatch.setattr(client, "_ssl_context", lambda: True)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: http_client(transport=httpx.MockTransport(respond), **kwargs)
    )
    assert replay(postgres, client, key)["status"] == "unknown"
    assert replay(postgres, client, key)["status"] == "unknown"
    [stored] = records(postgres, key)
    assert stored.efi_http_status == 401
    assert stored.efi_payment_id is stored.redirect_uri is None
    assert stored.efi_error_code == ("efi_oauth_http_401" if oauth else "efi_http_401")
    assert calls == (["/v1/oauth/token"] if oauth else ["/v1/oauth/token", "/v1/pagamentos/pix"])
