from datetime import date
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.database import get_db
from middlewares.require_admin_api_key import require_admin_api_key
from models.efi_pix_payment_model import EfiPixPaymentModel
from repositories.efi_pix_payment_repository import EfiPixPaymentRepository
from services.efi_api_client import (
    EfiConfigurationError,
    EfiOpenFinanceClient,
)
from services.efi_pix_reconciliation_service import (
    EfiPixReconciliationService,
    PixReconciliationError,
)


router = APIRouter(
    prefix="/internal/efi/pix",
    tags=["Efí Pix - consultas internas"],
    dependencies=[Depends(require_admin_api_key)],
)

PixStatus = Literal[
    "draft",
    "submitting",
    "awaiting_authorization",
    "unknown",
]


@router.get("/pagamentos")
def listar_pagamentos(
    status: PixStatus | None = None,
    pagina: int = Query(1, ge=1, le=100000),
    quantidade: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Lista pagamentos locais sem iniciar transferências."""
    consulta = select(EfiPixPaymentModel)
    contagem = select(func.count()).select_from(EfiPixPaymentModel)

    if status is not None:
        filtro = EfiPixPaymentModel.status == status
        consulta = consulta.where(filtro)
        contagem = contagem.where(filtro)

    total = db.scalar(contagem) or 0

    pagamentos = db.scalars(
        consulta
        .order_by(EfiPixPaymentModel.id.desc())
        .offset((pagina - 1) * quantidade)
        .limit(quantidade)
    ).all()

    return {
        "pagina": pagina,
        "quantidade": quantidade,
        "total": total,
        "pagamentos": [
            {
                "id": p.id,
                "id_proprio": p.id_proprio,
                "efi_payment_id": p.efi_payment_id,
                "status": p.status,
                "valor": str(p.amount),
                "origin_institution": p.origin_institution,
                "created_at": p.created_at.isoformat(),
            }
            for p in pagamentos
        ],
    }


@router.get("/pagamentos/{payment_id}/reconciliacao")
def inspecionar_pagamento(
    payment_id: int = Path(gt=0),
    inicio: date = Query(...),
    fim: date = Query(...),
    quantidade: int = Query(50, ge=1, le=100),
    max_paginas: int = Query(20, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """Inspeciona pagamento unknown sem alterar seu estado."""
    if fim < inicio:
        raise HTTPException(
            status_code=422,
            detail="Data final anterior à inicial",
        )

    payment = db.get(EfiPixPaymentModel, payment_id)

    if payment is None:
        raise HTTPException(
            status_code=404,
            detail="Pagamento não encontrado",
        )

    if payment.status != "unknown":
        raise HTTPException(
            status_code=409,
            detail="Pagamento não está em estado unknown",
        )

    service = EfiPixReconciliationService(
        EfiPixPaymentRepository(db),
        EfiOpenFinanceClient(),
    )

    try:
        return service.inspect_unknown(
            idempotency_key=payment.idempotency_key,
            inicio=inicio.isoformat(),
            fim=fim.isoformat(),
            quantidade=quantidade,
            max_paginas=max_paginas,
        )

    except (
        httpx.HTTPError,
        EfiConfigurationError,
        PixReconciliationError,
    ) as exc:
        raise HTTPException(
            status_code=502,
            detail="Falha na consulta de reconciliação Efí",
        ) from exc
