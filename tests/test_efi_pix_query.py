import pytest

from config.settings import Settings
from services.efi_api_client import (
    EfiConfigurationError,
    EfiOpenFinanceClient,
)


def make_client(environment="homologation"):
    settings = Settings(
        admin_api_key="admin-test-key-" + "0" * 32,
        efi_webhook_hash="webhook-test-hash-" + "0" * 32,
        efi_environment=environment,
    )
    return EfiOpenFinanceClient(settings)


def test_consulta_pagamentos(monkeypatch):
    client = make_client()
    calls = []

    def fake_request(method, endpoint, body=None, **kwargs):
        calls.append((method, endpoint, kwargs))
        return {
            "pagamentos": [
                {
                    "identificadorPagamento": "urn:efi:teste-001",
                    "status": "pendente",
                    "valor": "10.00",
                }
            ],
            "total": 1,
            "proximo": None,
        }

    monkeypatch.setattr(client, "_request", fake_request)

    result = client.list_pix_payments(
        "2026-11-01",
        "2026-11-23",
        pagina=1,
        quantidade=20,
    )

    assert result["pagamentos"][0]["status"] == "pendente"
    assert len(calls) == 1
    assert calls[0][0] == "GET"
    assert calls[0][1] == "/v1/pagamentos/pix"
    assert calls[0][2]["params"] == {
        "inicio": "2026-11-01",
        "fim": "2026-11-23",
        "pagina": 1,
        "quantidade": 20,
    }


@pytest.mark.parametrize(
    "inicio,fim",
    [
        ("2026-02-30", "2026-03-01"),
        ("2026-12-01", "2026-11-01"),
        ("01/11/2026", "23/11/2026"),
    ],
)
def test_rejeita_periodo_invalido(inicio, fim):
    client = make_client()

    with pytest.raises(ValueError):
        client.list_pix_payments(inicio, fim)


def test_rejeita_paginacao_invalida():
    client = make_client()

    with pytest.raises(ValueError):
        client.list_pix_payments(
            "2026-11-01", "2026-11-23", pagina=0
        )

    with pytest.raises(ValueError):
        client.list_pix_payments(
            "2026-11-01", "2026-11-23", quantidade=1000
        )


def test_consulta_bloqueada_em_producao():
    client = make_client("production")

    with pytest.raises(EfiConfigurationError):
        client.list_pix_payments(
            "2026-11-01", "2026-11-23"
        )
