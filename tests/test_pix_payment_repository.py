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
