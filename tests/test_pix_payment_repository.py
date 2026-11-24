from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from database.database import Base
from models.efi_pix_payment_model import EfiPixPaymentModel
from repositories.efi_pix_payment_repository import (
    EfiPixPaymentRepository,
    PixIdempotencyConflict,
)


@pytest.fixture
def repo():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        yield EfiPixPaymentRepository(db)

    engine.dispose()


def reservar(repo, chave="pix-001", hash_value="a" * 64, valor="10.00"):
    return repo.reserve(
        idempotency_key=chave,
        request_hash=hash_value,
        amount=Decimal(valor),
        origin_institution="stone-test",
    )


def test_cria_reserva(repo):
    payment, created = reservar(repo)

    assert created is True
    assert payment.id is not None
    assert payment.status == "draft"


def test_mesma_solicitacao_nao_duplica(repo):
    first, first_created = reservar(repo)
    second, second_created = reservar(repo)

    assert first_created is True
    assert second_created is False
    assert first.id == second.id

    total = repo.session.scalar(
        select(func.count()).select_from(EfiPixPaymentModel)
    )
    assert total == 1


def test_mesma_chave_com_hash_diferente(repo):
    reservar(repo)

    with pytest.raises(PixIdempotencyConflict):
        reservar(repo, hash_value="b" * 64)


def test_mesma_chave_com_valor_diferente(repo):
    reservar(repo)

    with pytest.raises(PixIdempotencyConflict):
        reservar(repo, valor="20.00")


def test_chaves_diferentes_criam_registros(repo):
    first, _ = reservar(repo, chave="pix-001")
    second, _ = reservar(repo, chave="pix-002")

    assert first.id != second.id


def test_rejeita_fracoes_de_centavo(repo):
    with pytest.raises(ValueError, match="Valor monetário"):
        reservar(repo, valor="10.001")


def test_claim_for_submission_somente_uma_vez(repo):
    reservar(repo)

    assert repo.claim_for_submission("pix-001") is True
    assert repo.claim_for_submission("pix-001") is False

    payment = repo.get_by_idempotency_key("pix-001")
    assert payment.status == "submitting"


def test_resposta_efi_aguarda_autorizacao(repo):
    reservar(repo)
    assert repo.claim_for_submission("pix-001") is True

    assert repo.mark_awaiting_authorization(
        "pix-001",
        efi_payment_id="urn:efi:teste-123",
        redirect_uri="https://example.com/autorizar",
    ) is True

    payment = repo.get_by_idempotency_key("pix-001")

    assert payment.status == "awaiting_authorization"
    assert payment.efi_payment_id == "urn:efi:teste-123"
    assert payment.redirect_uri == "https://example.com/autorizar"

    # Mesmo após receber a resposta, não permite um novo envio.
    assert repo.claim_for_submission("pix-001") is False


def test_timeout_bloqueia_reenvio(repo):
    reservar(repo)
    assert repo.claim_for_submission("pix-001") is True

    assert repo.mark_unknown("pix-001") is True

    payment = repo.get_by_idempotency_key("pix-001")
    assert payment.status == "unknown"

    assert repo.claim_for_submission("pix-001") is False
    assert repo.mark_unknown("pix-001") is False


def test_nao_aceita_redirect_inseguro(repo):
    reservar(repo)
    repo.claim_for_submission("pix-001")

    with pytest.raises(ValueError, match="URL de autorização"):
        repo.mark_awaiting_authorization(
            "pix-001",
            efi_payment_id="urn:efi:teste",
            redirect_uri="http://example.com/autorizar",
        )

    payment = repo.get_by_idempotency_key("pix-001")
    assert payment.status == "submitting"


def test_unknown_nao_vira_autorizacao_automaticamente(repo):
    reservar(repo)
    repo.claim_for_submission("pix-001")
    repo.mark_unknown("pix-001")

    assert repo.mark_awaiting_authorization(
        "pix-001",
        efi_payment_id="urn:efi:teste",
        redirect_uri="https://example.com/autorizar",
    ) is False

    payment = repo.get_by_idempotency_key("pix-001")
    assert payment.status == "unknown"


def test_nao_marca_unknown_apos_receber_autorizacao(repo):
    reservar(repo)
    repo.claim_for_submission("pix-001")

    repo.mark_awaiting_authorization(
        "pix-001",
        efi_payment_id="urn:efi:teste",
        redirect_uri="https://example.com/autorizar",
    )

    assert repo.mark_unknown("pix-001") is False

    payment = repo.get_by_idempotency_key("pix-001")
    assert payment.status == "awaiting_authorization"


def test_id_proprio_unico_entre_solicitacoes(repo):
    primeiro, _ = reservar(repo, chave="pix-001")
    segundo, _ = reservar(repo, chave="pix-002")

    assert primeiro.id_proprio.startswith("v7-")
    assert segundo.id_proprio.startswith("v7-")
    assert primeiro.id_proprio != segundo.id_proprio

    repetido, criado = reservar(repo, chave="pix-001")

    assert criado is False
    assert repetido.id_proprio == primeiro.id_proprio


def test_registro_legado_sem_id_nao_pode_ser_enviado(repo):
    pagamento = EfiPixPaymentModel(
        idempotency_key="pix-legado",
        request_hash="a" * 64,
        id_proprio=None,
        amount=Decimal("10.00"),
        origin_institution="stone-test",
        status="draft",
    )

    repo.session.add(pagamento)
    repo.session.commit()

    assert repo.claim_for_submission("pix-legado") is False

    atualizado = repo.get_by_idempotency_key("pix-legado")
    assert atualizado.status == "draft"
    assert atualizado.id_proprio is None
