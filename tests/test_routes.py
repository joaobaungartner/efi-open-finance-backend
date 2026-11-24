from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app import app
from config.settings import get_settings
from database.database import Base, engine, get_db


@pytest.fixture

def client():
    import models  # noqa: F401

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)

    def override():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def test_health_and_redirect(client):
    assert client.get("/health").json()["status"] == "OK"
    assert "Retorno recebido" in client.get(
        "/openfinance/retorno?identificadorPagamento=urn%3Ateste"
    ).text
    assert "não concluída" in client.get("/openfinance/retorno?erro=acesso_negado").text


def test_webhook_rejects_missing_or_wrong_hash(client):
    payload = {"identificadorPagamento": "urn:efi:1", "status": "aceito", "tipo": "pagamento"}
    assert client.post("/webhooks/efi", json=payload).status_code == 401
    assert client.post("/webhooks/efi?hmac=errado", json=payload).status_code == 401


def test_webhook_idempotency(client):
    hmac = get_settings().efi_webhook_hash.get_secret_value()
    payload = {
        "identificadorPagamento": "urn:efi:pagamento-123",
        "status": "aceito",
        "valor": "9.90",
        "tipo": "pagamento",
    }
    first = client.post("/webhooks/efi", params={"hmac": hmac}, json=payload)
    assert first.status_code == 200 and first.json() == {"received": True, "new_event": True}
    retry = client.post("/webhooks/efi", params={"hmac": hmac}, json=payload)
    assert retry.status_code == 200 and retry.json()["new_event"] is False
    changed = {**payload, "status": "rejeitado"}
    assert client.post("/webhooks/efi", params={"hmac": hmac}, json=changed).json()["new_event"] is True


def test_rejects_invalid_payload(client):
    hmac = get_settings().efi_webhook_hash.get_secret_value()
    assert client.post("/webhooks/efi", params={"hmac": hmac}, content=b"garbage").status_code == 400
    assert client.post("/webhooks/efi", params={"hmac": hmac}, json={"status": "aceito", "tipo": "pagamento"}).status_code == 422


def test_admin_protected(client):
    assert client.get("/internal/efi/participantes").status_code == 401
    assert client.get("/internal/efi/config", headers={"X-Admin-Key": "wrong"}).status_code == 401



def test_admin_participants_and_redacted_config(client, monkeypatch):
    from services.efi_api_client import EfiOpenFinanceClient

    monkeypatch.setattr(EfiOpenFinanceClient, "get_participants", lambda self: {
        "participantes": [{"identificador": "stone-id", "nome": "Stone e Ton", "cnpj": "secret"}]
    })
    monkeypatch.setattr(EfiOpenFinanceClient, "get_config", lambda self: {
        "redirectURL": "https://example.com/openfinance/retorno",
        "webhookSecurity": {"type": "hmac", "hash": "never-return"},
    })
    headers = {"X-Admin-Key": get_settings().admin_api_key.get_secret_value()}
    result = client.get("/internal/efi/participantes", headers=headers)
    assert result.status_code == 200
    assert result.json() == {"participantes": [{"identificador": "stone-id", "nome": "Stone e Ton"}]}
    result = client.get("/internal/efi/config", headers=headers)
    assert result.status_code == 200
    assert "hash" not in result.json()["webhookSecurity"]


def test_webhook_oversize(client):
    response = client.post("/webhooks/efi", content=b"x" * 70000)
    assert response.status_code == 413


def test_webhook_verificacao_efi(client):
    from sqlalchemy.orm import Session
    from models.efi_webhook_event_model import EfiWebhookEventModel

    hmac = get_settings().efi_webhook_hash.get_secret_value()
    url = "/webhooks/efi"

    # Sem autenticação, a verificação deve falhar.
    response = client.post(url, json={})
    assert response.status_code == 401

    # HMAC incorreto também deve ser rejeitado.
    response = client.post(url, params={"hmac": "incorreto"}, json={})
    assert response.status_code == 401

    # Verificação autenticada enviada pela Efí.
    response = client.post(url, params={"hmac": hmac}, json={})
    assert response.status_code == 200
    assert response.json() == {
        "received": True,
        "verification": True,
    }

    # Uma notificação financeira incompleta não deve ser aceita.
    response = client.post(
        url,
        params={"hmac": hmac},
        json={"status": "aceito", "tipo": "pagamento"},
    )
    assert response.status_code == 422

    # Nenhum desses testes deve criar um evento financeiro.
    with Session(engine) as session:
        assert session.query(EfiWebhookEventModel).count() == 0


def _criar_pix_para_rota(chave, *, unknown=False):
    from decimal import Decimal
    from sqlalchemy.orm import Session
    from repositories.efi_pix_payment_repository import (
        EfiPixPaymentRepository,
    )

    with Session(engine) as db:
        repo = EfiPixPaymentRepository(db)

        payment, _ = repo.reserve(
            idempotency_key=chave,
            request_hash="a" * 64,
            amount=Decimal("10.00"),
            origin_institution="stone-test",
        )

        payment_id = payment.id
        id_proprio = payment.id_proprio

        if unknown:
            assert repo.claim_for_submission(chave)
            assert repo.mark_unknown(chave)

        return payment_id, id_proprio


def _admin_headers():
    return {
        "X-Admin-Key": (
            get_settings().admin_api_key.get_secret_value()
        )
    }


def test_pix_leitura_exige_admin(client):
    url = "/internal/efi/pix/pagamentos"

    assert client.get(url).status_code == 401
    assert client.get(
        url, headers={"X-Admin-Key": "incorreta"}
    ).status_code == 401

    result = client.get(url, headers=_admin_headers())

    assert result.status_code == 200
    assert result.json()["total"] == 0


def test_pix_listagem_filtro_paginacao(client):
    _criar_pix_para_rota("pix-lista-001")
    _criar_pix_para_rota("pix-lista-002", unknown=True)

    result = client.get(
        "/internal/efi/pix/pagamentos",
        params={"status": "unknown", "quantidade": 1},
        headers=_admin_headers(),
    )

    assert result.status_code == 200

    data = result.json()

    assert data["total"] == 1
    assert len(data["pagamentos"]) == 1
    assert data["pagamentos"][0]["status"] == "unknown"
    assert data["pagamentos"][0]["valor"] == "10.00"

    # Não expor chave de idempotência nem URL de autorização.
    assert "idempotency_key" not in data["pagamentos"][0]
    assert "redirect_uri" not in data["pagamentos"][0]


def test_pix_inspecao_unknown_preserva_pagamento(client, monkeypatch):
    from services.efi_api_client import EfiOpenFinanceClient
    from models.efi_pix_payment_model import EfiPixPaymentModel
    from sqlalchemy.orm import Session

    payment_id, id_proprio = _criar_pix_para_rota(
        "pix-rota-unknown", unknown=True
    )

    chamadas = []

    def consulta_falsa(
        self, inicio, fim, *, pagina, quantidade
    ):
        chamadas.append(pagina)

        return {
            "pagamentos": [
                {
                    "idProprio": id_proprio,
                    "identificadorPagamento": "urn:efi:teste",
                    "valor": "10.00",
                    "status": "pendente",
                }
            ],
            "total": 1,
            "proximo": None,
        }

    monkeypatch.setattr(
        EfiOpenFinanceClient,
        "list_pix_payments",
        consulta_falsa,
    )

    url = (
        f"/internal/efi/pix/pagamentos/"
        f"{payment_id}/reconciliacao"
    )

    params = {
        "inicio": "2026-11-01",
        "fim": "2026-11-24",
    }

    assert client.get(url, params=params).status_code == 401
    assert chamadas == []

    response = client.get(
        url,
        params=params,
        headers=_admin_headers(),
    )

    assert response.status_code == 200
    assert (
        response.json()["diagnostico"]
        == "correspondencia_consistente"
    )
    assert chamadas == [1]
    assert response.headers["Cache-Control"] == "no-store"

    with Session(engine) as db:
        payment = db.get(EfiPixPaymentModel, payment_id)

        assert payment.status == "unknown"
        assert payment.efi_payment_id is None

    historico = client.get(
        f"/internal/efi/pix/pagamentos/{payment_id}/investigacoes",
        headers=_admin_headers(),
    ).json()
    assert historico["total"] == 1
    assert historico["investigacoes"][0]["resultado"] == "correspondencia_consistente"


def test_pix_inspecao_rejeita_estado_diferente(client, monkeypatch):
    from services.efi_api_client import EfiOpenFinanceClient

    payment_id, _ = _criar_pix_para_rota(
        "pix-rota-draft"
    )

    def nao_consultar(*args, **kwargs):
        raise AssertionError("Consulta bancária indevida")

    monkeypatch.setattr(
        EfiOpenFinanceClient,
        "list_pix_payments",
        nao_consultar,
    )

    response = client.get(
        f"/internal/efi/pix/pagamentos/{payment_id}/reconciliacao",
        params={
            "inicio": "2026-11-01",
            "fim": "2026-11-24",
        },
        headers=_admin_headers(),
    )

    assert response.status_code == 409


def test_pix_inspecao_mascara_falha_externa(client, monkeypatch):
    import httpx
    from services.efi_api_client import EfiOpenFinanceClient

    payment_id, _ = _criar_pix_para_rota(
        "pix-rota-timeout", unknown=True
    )

    def falhar(*args, **kwargs):
        raise httpx.ReadTimeout("detalhe_bancario_privado")

    monkeypatch.setattr(
        EfiOpenFinanceClient,
        "list_pix_payments",
        falhar,
    )

    response = client.get(
        f"/internal/efi/pix/pagamentos/{payment_id}/reconciliacao",
        params={
            "inicio": "2026-11-01",
            "fim": "2026-11-24",
        },
        headers=_admin_headers(),
    )

    assert response.status_code == 502
    assert "detalhe_bancario_privado" not in response.text
    historico = client.get(
        f"/internal/efi/pix/pagamentos/{payment_id}/investigacoes",
        headers=_admin_headers(),
    )
    assert "detalhe_bancario_privado" not in historico.text
    [entry] = historico.json()["investigacoes"]
    assert entry["resultado"] == "falha_comunicacao_efi"
    assert entry["finalizada_em"]


def test_pix_parametros_invalidos(client):
    url = "/internal/efi/pix/pagamentos"

    response = client.get(
        url,
        params={"quantidade": 101},
        headers=_admin_headers(),
    )

    assert response.status_code == 422

    response = client.get(
        "/internal/efi/pix/pagamentos/1/reconciliacao",
        params={"inicio": "2026-11-24", "fim": "2026-11-01"},
        headers=_admin_headers(),
    )
    assert response.status_code == 422


def test_pix_pendentes_filtram_updated_at_e_status(client):
    from models.efi_pix_payment_model import EfiPixPaymentModel

    now = datetime.now(UTC)
    with Session(engine) as db:
        for index, status in enumerate(("draft", "submitting", "unknown", "awaiting_authorization")):
            db.add(
                EfiPixPaymentModel(
                    idempotency_key=f"privada-{status}",
                    request_hash="a" * 64,
                    amount=10,
                    origin_institution="privado",
                    redirect_uri="https://privado.example/autorizar",
                    status=status,
                    created_at=now - timedelta(days=1),
                    updated_at=now - timedelta(minutes=61 - index),
                )
            )
        db.add(
            EfiPixPaymentModel(
                idempotency_key="privada-recente",
                request_hash="b" * 64,
                amount=10,
                origin_institution="privado",
                status="unknown",
                created_at=now - timedelta(days=1),
                updated_at=now,
            )
        )
        db.commit()

    url = "/internal/efi/pix/pagamentos/pendentes"
    assert client.get(url).status_code == 401
    response = client.get(url, params={"quantidade": 2}, headers=_admin_headers())
    assert response.status_code == 200
    assert response.json()["total"] == 4
    assert [p["status"] for p in response.json()["pagamentos"]] == ["draft", "submitting"]
    assert "privad" not in response.text

    response = client.get(url, params={"pagina": 2, "quantidade": 2}, headers=_admin_headers())
    unknown, awaiting = response.json()["pagamentos"]
    assert unknown["status"] == "unknown"
    assert awaiting["status"] == "awaiting_authorization"
    assert "sem confirmação de liquidação" in awaiting["descricao"]
    assert awaiting["updated_at"].endswith("+00:00")

    response = client.get(
        url, params={"status": "unknown", "idade_minima_minutos": 0}, headers=_admin_headers()
    )
    assert response.json()["total"] == 2
    response = client.get(url, params={"idade_minima_minutos": 120}, headers=_admin_headers())
    assert response.json()["total"] == 0
    assert client.get(
        url, params={"idade_minima_minutos": -1}, headers=_admin_headers()
    ).status_code == 422


def test_pix_investiga_submitting_audita_ambiguidade_e_ausencia(client, monkeypatch):
    from models.efi_pix_investigation_model import EfiPixInvestigationModel
    from models.efi_pix_payment_model import EfiPixPaymentModel
    from repositories.efi_pix_payment_repository import EfiPixPaymentRepository
    from services.efi_api_client import EfiOpenFinanceClient

    key = "chave-local-privada"
    payment_id, id_proprio = _criar_pix_para_rota(key)
    with Session(engine) as db:
        assert EfiPixPaymentRepository(db).claim_for_submission(key)
        original_updated_at = db.get(EfiPixPaymentModel, payment_id).updated_at

    def nao_enviar(*args, **kwargs):
        pytest.fail("Uma investigação não deve iniciar Pix")

    calls = []

    def consulta(*args, **kwargs):
        with Session(engine) as db:
            entry = db.scalar(
                select(EfiPixInvestigationModel)
                .order_by(EfiPixInvestigationModel.id.desc())
                .limit(1)
            )
            assert entry.result == "iniciada"
            assert entry.observed_status == "submitting"
            assert entry.finished_at is None
        calls.append(kwargs["pagina"])
        match = {
            "idProprio": id_proprio,
            "identificadorPagamento": "identificador-externo-privado",
            "status": "estado-externo-privado",
            "valor": "10.00",
        }
        if len(calls) == 1:
            return {"pagamentos": [match, match], "total": 2, "proximo": None}
        return {"pagamentos": [], "total": 0, "proximo": None}

    monkeypatch.setattr(EfiOpenFinanceClient, "start_pix", nao_enviar)
    monkeypatch.setattr(EfiOpenFinanceClient, "list_pix_payments", consulta)
    base = f"/internal/efi/pix/pagamentos/{payment_id}"
    params = {"inicio": "2026-11-01", "fim": "2026-11-24"}
    assert client.post(f"{base}/reconciliacao", params=params).status_code == 401
    assert client.get(f"{base}/investigacoes").status_code == 401
    assert calls == []

    for expected in ("ambiguo", "nao_encontrado_no_periodo"):
        response = client.post(f"{base}/reconciliacao", params=params, headers=_admin_headers())
        assert response.status_code == 200
        assert response.json()["diagnostico"] == expected
        assert response.json()["local_status"] == "submitting"
        assert "privad" not in response.text

    response = client.get(f"{base}/investigacoes", headers=_admin_headers(), params={"quantidade": 1})
    assert response.status_code == 200
    assert response.json()["total"] == 2
    [entry] = response.json()["investigacoes"]
    assert entry["resultado"] == "nao_encontrado_no_periodo"
    assert entry["estado_observado"] == "submitting"
    assert entry["payment_id"] == payment_id
    assert entry["origem"] == "api_admin"
    assert entry["inicio"] == params["inicio"]
    assert entry["fim"] == params["fim"]
    assert entry["iniciada_em"] <= entry["finalizada_em"]
    assert "privad" not in response.text
    assert calls == [1, 1]
    with Session(engine) as db:
        payment = db.get(EfiPixPaymentModel, payment_id)
        assert payment.status == "submitting"
        assert payment.updated_at == original_updated_at
        assert payment.efi_payment_id is None


def test_pix_investigacao_inexistente_retorna_404(client):
    base = "/internal/efi/pix/pagamentos/999"
    assert client.post(
        f"{base}/reconciliacao",
        params={"inicio": "2026-11-01", "fim": "2026-11-24"},
        headers=_admin_headers(),
    ).status_code == 404
    assert client.get(f"{base}/investigacoes", headers=_admin_headers()).status_code == 404


@pytest.mark.parametrize("falha_finalizacao", [False, True])
def test_pix_falha_ao_gravar_auditoria_preserva_pagamento(client, monkeypatch, falha_finalizacao):
    from models.efi_pix_investigation_model import EfiPixInvestigationModel
    from models.efi_pix_payment_model import EfiPixPaymentModel
    from services.efi_api_client import EfiOpenFinanceClient

    payment_id, _ = _criar_pix_para_rota("falha-auditoria", unknown=True)
    with Session(engine) as db:
        updated_at = db.get(EfiPixPaymentModel, payment_id).updated_at
    calls = []

    def consulta(*args, **kwargs):
        calls.append(kwargs["pagina"])
        return {"pagamentos": [], "total": 0, "proximo": None}

    def falhar_no_commit(session):
        for entry in session.identity_map.values():
            if isinstance(entry, EfiPixInvestigationModel) and (
                (entry.finished_at is not None) == falha_finalizacao
            ):
                raise SQLAlchemyError("falha fictícia na auditoria")

    monkeypatch.setattr(EfiOpenFinanceClient, "list_pix_payments", consulta)
    event.listen(Session, "before_commit", falhar_no_commit)
    try:
        with pytest.raises(SQLAlchemyError, match="falha fictícia na auditoria"):
            client.post(
                f"/internal/efi/pix/pagamentos/{payment_id}/reconciliacao",
                params={"inicio": "2026-11-01", "fim": "2026-11-24"},
                headers=_admin_headers(),
            )
    finally:
        event.remove(Session, "before_commit", falhar_no_commit)

    assert calls == ([1] if falha_finalizacao else [])
    with Session(engine) as db:
        entries = db.scalars(select(EfiPixInvestigationModel)).all()
        if falha_finalizacao:
            [entry] = entries
            assert entry.result == "iniciada"
            assert entry.finished_at is None
        else:
            assert entries == []
        payment = db.get(EfiPixPaymentModel, payment_id)
        assert payment.status == "unknown"
        assert payment.updated_at == updated_at
