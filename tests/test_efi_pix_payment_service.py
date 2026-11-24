from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from database.database import Base
from models.efi_pix_payment_model import EfiPixPaymentModel
from repositories.efi_pix_payment_repository import (
    EfiPixPaymentRepository,
    PixIdempotencyConflict,
)
from services.efi_api_client import EfiConfigurationError
from services.efi_pix_payment_service import EfiPixPaymentService


class FakeEfiClient:
    def __init__(self):
        self.settings = SimpleNamespace(
            efi_environment="homologation"
        )
        self.calls = []
        self.error = None
        self.response = {
            "identificadorPagamento": "urn:efi:teste-123",
            "redirectURI": "https://example.com/autorizar",
        }

    def start_pix(self, payload, *, idempotency_key):
        self.calls.append(idempotency_key)

        if self.error is not None:
            raise self.error

        return self.response


@pytest.fixture
def setup_service():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        repo = EfiPixPaymentRepository(db)
        client = FakeEfiClient()
        service = EfiPixPaymentService(repo, client)

        yield service, repo, client

    engine.dispose()


def payload(valor="10.00"):
    return {
        "pagador": {"idParticipante": "teste", "cpf": "52998224725"},
        "favorecido": {"chave": "chave-teste"},
        "pagamento": {"valor": valor},
    }


def enviar(service, key="pix-001", valor="10.00"):
    return service.submit(
        idempotency_key=key,
        origin_institution="stone-test",
        payload=payload(valor),
    )


def test_envio_valido(setup_service):
    service, repo, client = setup_service

    result = enviar(service)

    assert result["status"] == "awaiting_authorization"
    assert result["efi_payment_id"] == "urn:efi:teste-123"
    assert len(client.calls) == 1


def test_mesma_chave_nao_reenvia(setup_service):
    service, repo, client = setup_service

    first = enviar(service)
    second = enviar(service)

    assert first["id"] == second["id"]
    assert len(client.calls) == 1

    count = repo.session.scalar(
        select(func.count()).select_from(EfiPixPaymentModel)
    )
    assert count == 1


def test_mesma_chave_dados_diferentes(setup_service):
    service, repo, client = setup_service

    enviar(service)

    with pytest.raises(PixIdempotencyConflict):
        enviar(service, valor="20.00")

    assert len(client.calls) == 1


def test_timeout_nao_reenvia(setup_service):
    service, repo, client = setup_service
    client.error = httpx.ReadTimeout("timeout de teste")

    first = enviar(service)
    second = enviar(service)

    assert first["status"] == "unknown"
    assert second["status"] == "unknown"
    assert len(client.calls) == 1


def test_resposta_incompleta(setup_service):
    service, repo, client = setup_service
    client.response = {"identificadorPagamento": "urn:teste"}

    result = enviar(service)

    assert result["status"] == "unknown"
    assert len(client.calls) == 1


def test_falha_ao_gravar_retorno(setup_service, monkeypatch):
    service, repo, client = setup_service

    def falhar(*args, **kwargs):
        raise RuntimeError("Falha simulada no banco")

    monkeypatch.setattr(
        repo, "mark_awaiting_authorization", falhar
    )

    first = enviar(service)
    second = enviar(service)

    assert first["status"] == "unknown"
    assert second["status"] == "unknown"
    assert len(client.calls) == 1


def test_bloqueia_producao(setup_service):
    service, repo, client = setup_service
    client.settings.efi_environment = "production"

    with pytest.raises(EfiConfigurationError):
        enviar(service)

    assert client.calls == []
    assert repo.get_by_idempotency_key("pix-001") is None


def test_valor_invalido_nao_cria_pagamento(setup_service):
    service, repo, client = setup_service

    with pytest.raises(ValueError):
        enviar(service, valor="10.001")

    assert client.calls == []
    assert repo.get_by_idempotency_key("pix-001") is None
