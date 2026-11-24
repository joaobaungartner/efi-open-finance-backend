from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database.database import Base
from repositories.efi_pix_payment_repository import EfiPixPaymentRepository
from services.efi_api_client import EfiConfigurationError
from services.efi_pix_reconciliation_service import (
    EfiPixReconciliationService,
    PixReconciliationError,
)


class FakeListClient:
    def __init__(self):
        self.settings = SimpleNamespace(
            efi_environment="homologation"
        )
        self.pages = {}
        self.calls = []

    def list_pix_payments(
        self, inicio, fim, *, pagina, quantidade
    ):
        self.calls.append(pagina)
        return self.pages.get(
            pagina,
            {
                "pagamentos": [],
                "total": 0,
                "proximo": None,
            },
        )


def bank_payment(
    payment, *, valor="10.00", identifier="urn:efi:001"
):
    return {
        "idProprio": payment.id_proprio,
        "identificadorPagamento": identifier,
        "valor": valor,
        "status": "pendente",
    }


@pytest.fixture
def prepared():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        repo = EfiPixPaymentRepository(db)

        payment, _ = repo.reserve(
            idempotency_key="pix-001",
            request_hash="a" * 64,
            amount=Decimal("10.00"),
            origin_institution="stone-test",
        )

        assert repo.claim_for_submission("pix-001")
        assert repo.mark_unknown("pix-001")

        client = FakeListClient()
        service = EfiPixReconciliationService(repo, client)

        yield repo, payment, client, service

    engine.dispose()


def inspect(service, **kwargs):
    return service.inspect_unknown(
        idempotency_key="pix-001",
        inicio="2026-11-01",
        fim="2026-11-24",
        **kwargs,
    )


def test_sem_resultados_nao_significa_falha(prepared):
    repo, payment, client, service = prepared

    result = inspect(service)

    assert (
        result["diagnostico"]
        == "nao_encontrado_no_periodo"
    )
    assert repo.get_by_idempotency_key(
        "pix-001"
    ).status == "unknown"
    assert client.calls == [1]


def test_correspondencia_nao_altera_estado(prepared):
    repo, payment, client, service = prepared

    client.pages[1] = {
        "pagamentos": [bank_payment(payment)],
        "total": 1,
        "proximo": None,
    }

    result = inspect(service)

    assert (
        result["diagnostico"]
        == "correspondencia_consistente"
    )
    assert result["efi_status"] == "pendente"
    assert repo.get_by_idempotency_key(
        "pix-001"
    ).status == "unknown"


def test_paginacao_ate_ultima_pagina(prepared):
    repo, payment, client, service = prepared

    client.pages[1] = {
        "pagamentos": [{"idProprio": "outro"}],
        "total": 2,
        "proximo": (
            "/pagamentos/pix?"
            "inicio=2026-11-01&fim=2026-11-24"
            "&quantidade=100&pagina=2"
        ),
    }

    client.pages[2] = {
        "pagamentos": [bank_payment(payment)],
        "total": 2,
        "proximo": None,
    }

    assert (
        inspect(service)["diagnostico"]
        == "correspondencia_consistente"
    )
    assert client.calls == [1, 2]


def test_correspondencia_duplicada_e_ambigua(prepared):
    repo, payment, client, service = prepared

    client.pages[1] = {
        "pagamentos": [
            bank_payment(payment),
            bank_payment(payment),
        ],
        "total": 2,
        "proximo": None,
    }

    assert inspect(service)["diagnostico"] == "ambiguo"


def test_valor_divergente(prepared):
    repo, payment, client, service = prepared

    client.pages[1] = {
        "pagamentos": [
            bank_payment(payment, valor="11.00")
        ],
        "total": 1,
        "proximo": None,
    }

    assert inspect(service)["diagnostico"] == "valor_divergente"


def test_identificador_efi_divergente(prepared):
    repo, payment, client, service = prepared

    payment.efi_payment_id = "urn:efi:outro"
    repo.session.commit()

    client.pages[1] = {
        "pagamentos": [bank_payment(payment)],
        "total": 1,
        "proximo": None,
    }

    assert (
        inspect(service)["diagnostico"]
        == "identificador_divergente"
    )


def test_dados_remotos_incompletos(prepared):
    repo, payment, client, service = prepared

    remote = bank_payment(payment)
    del remote["valor"]

    client.pages[1] = {
        "pagamentos": [remote],
        "total": 1,
        "proximo": None,
    }

    assert (
        inspect(service)["diagnostico"]
        == "dados_remotos_incompletos"
    )


@pytest.mark.parametrize(
    "proximo",
    [
        "https://outro-site.test/?pagina=2",
        "/pagamentos/pix?pagina=1",
    ],
)
def test_rejeita_paginacao_insegura(prepared, proximo):
    repo, payment, client, service = prepared

    client.pages[1] = {
        "pagamentos": [],
        "proximo": proximo,
    }

    with pytest.raises(PixReconciliationError):
        inspect(service)


def test_limite_nao_retorna_resultado_parcial(prepared):
    repo, payment, client, service = prepared

    client.pages[1] = {
        "pagamentos": [],
        "proximo": "/pagamentos/pix?pagina=2",
    }

    with pytest.raises(PixReconciliationError, match="Limite"):
        inspect(service, max_paginas=1)


def test_total_incompativel_bloqueia_diagnostico(prepared):
    repo, payment, client, service = prepared

    client.pages[1] = {
        "pagamentos": [],
        "total": 2,
        "proximo": None,
    }

    with pytest.raises(
        PixReconciliationError, match="incompleta"
    ):
        inspect(service)


def test_apenas_status_unknown(prepared):
    repo, payment, client, service = prepared

    payment.status = "awaiting_authorization"
    repo.session.commit()

    with pytest.raises(ValueError, match="unknown"):
        inspect(service)

    assert client.calls == []


def test_bloqueia_producao(prepared):
    repo, payment, client, service = prepared

    client.settings.efi_environment = "production"

    with pytest.raises(EfiConfigurationError):
        inspect(service)

    assert client.calls == []


def test_bloqueia_registro_legado_sem_id_proprio(prepared):
    repo, payment, client, service = prepared

    payment.id_proprio = None
    repo.session.commit()

    with pytest.raises(
        PixReconciliationError, match="idProprio"
    ):
        inspect(service)

    assert client.calls == []
