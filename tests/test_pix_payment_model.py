from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.database import Base
from models.efi_pix_payment_model import EfiPixPaymentModel


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        yield db

    engine.dispose()


def criar_pagamento(chave="teste-001", valor="10.25"):
    return EfiPixPaymentModel(
        idempotency_key=chave,
        request_hash="a" * 64,
        amount=Decimal(valor),
        origin_institution="stone-test",
    )


def test_criar_pagamento(session):
    pagamento = criar_pagamento()

    session.add(pagamento)
    session.commit()
    session.refresh(pagamento)

    assert pagamento.id is not None
    assert pagamento.amount == Decimal("10.25")
    assert pagamento.status == "draft"
    assert pagamento.created_at is not None
    assert pagamento.updated_at is not None


def test_idempotency_key_unica(session):
    session.add(criar_pagamento())
    session.commit()

    session.add(criar_pagamento(valor="20.00"))

    with pytest.raises(IntegrityError):
        session.commit()

    session.rollback()


@pytest.mark.parametrize("valor", ["0.00", "-0.01"])
def test_valor_deve_ser_positivo(session, valor):
    session.add(criar_pagamento(valor=valor))

    with pytest.raises(IntegrityError):
        session.commit()

    session.rollback()

def test_id_proprio_opcional(session):
    pagamento = criar_pagamento(chave="pix-sem-id-proprio")

    session.add(pagamento)
    session.commit()
    session.refresh(pagamento)

    assert pagamento.id_proprio is None

def test_id_proprio_unico(session):
    primeiro = criar_pagamento(chave="pix-001")
    primeiro.id_proprio = "v7-operacao-001"

    session.add(primeiro)
    session.commit()

    segundo = criar_pagamento(chave="pix-002")
    segundo.id_proprio = "v7-operacao-001"

    session.add(segundo)

    with pytest.raises(IntegrityError):
        session.commit()

    session.rollback()
