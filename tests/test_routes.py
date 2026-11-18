import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

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
