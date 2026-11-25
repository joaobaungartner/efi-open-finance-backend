from datetime import UTC, date, datetime, timedelta
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.database import get_db
from middlewares.require_admin_api_key import require_admin_api_key
from models.efi_pix_investigation_model import EfiPixInvestigationModel
from models.efi_pix_payment_model import EfiPixPaymentModel
from repositories.efi_pix_payment_repository import EfiPixPaymentRepository
from services.efi_api_client import (
    EfiConfigurationError,
    EfiOpenFinanceClient,
    EfiUnexpectedResponse,
)
from services.efi_pix_reconciliation_service import (
    INVESTIGABLE_STATUSES,
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
STATUS_DESCRIPTIONS = {
    "draft": "Solicitação reservada sem confirmação de envio",
    "submitting": "Envio reivindicado com resultado potencialmente incerto",
    "unknown": "Resultado da solicitação indeterminado",
    "awaiting_authorization": "Autorização pendente, sem confirmação de liquidação",
}
NO_STORE = {"Cache-Control": "no-store"}
HTTP_DIAGNOSTICS = {
    "http_400", "http_401", "http_403", "http_422", "rate_limited",
    "server_error", "http_unexpected", "invalid_response", "timeout",
    "transport_error", "submission_unexpected_error",
}
DIAGNOSTIC_CODES = {
    *(f"efi_{code}" for code in HTTP_DIAGNOSTICS if code != "submission_unexpected_error"),
    *(f"efi_oauth_{code}" for code in HTTP_DIAGNOSTICS),
    "local_configuration_error", "local_validation_error",
    "local_persistence_error", "submission_unexpected_error",
}


def _utc_iso(value: datetime) -> str:
    # SQLite devolve timestamps sem fuso. A aplicação os grava em UTC.
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


@router.get("/pagamentos")
def listar_pagamentos(
    response: Response,
    status: PixStatus | None = None,
    pagina: int = Query(1, ge=1, le=100000),
    quantidade: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Lista pagamentos locais sem iniciar transferências."""
    response.headers.update(NO_STORE)
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
                "diagnostico": (
                    {
                        "codigo": p.efi_error_code,
                        "http_status": p.efi_http_status,
                    }
                    if p.efi_error_code in DIAGNOSTIC_CODES
                    else None
                ),
            }
            for p in pagamentos
        ],
    }


@router.get("/pagamentos/pendentes")
def listar_pendentes(
    response: Response,
    status: PixStatus | None = None,
    idade_minima_minutos: int = Query(15, ge=0, le=525600),
    pagina: int = Query(1, ge=1, le=100000),
    quantidade: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Lista solicitações locais cuja última atualização atingiu a idade mínima."""
    response.headers.update(NO_STORE)
    inicio = datetime.now(UTC) - timedelta(minutes=idade_minima_minutos)
    filtro = (
        EfiPixPaymentModel.status.in_([status] if status else tuple(STATUS_DESCRIPTIONS)),
        EfiPixPaymentModel.updated_at <= inicio,
    )
    pagamentos = db.scalars(
        select(EfiPixPaymentModel)
        .where(*filtro)
        .order_by(EfiPixPaymentModel.updated_at, EfiPixPaymentModel.id)
        .offset((pagina - 1) * quantidade)
        .limit(quantidade)
    )
    return {
        "pagina": pagina,
        "quantidade": quantidade,
        "idade_minima_minutos": idade_minima_minutos,
        "total": db.scalar(select(func.count()).select_from(EfiPixPaymentModel).where(*filtro)),
        "pagamentos": [
            {
                "id": payment.id,
                "status": payment.status,
                "descricao": STATUS_DESCRIPTIONS[payment.status],
                "updated_at": _utc_iso(payment.updated_at),
            }
            for payment in pagamentos
        ],
    }


@router.get("/pagamentos/{payment_id}/reconciliacao", deprecated=True)
@router.post("/pagamentos/{payment_id}/reconciliacao")
def inspecionar_pagamento(
    response: Response,
    payment_id: int = Path(gt=0),
    inicio: date = Query(...),
    fim: date = Query(...),
    quantidade: int = Query(50, ge=1, le=100),
    max_paginas: int = Query(20, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """Consulta a Efí e grava uma investigação local sem alterar o pagamento."""
    response.headers.update(NO_STORE)
    if get_settings().dry_run:
        raise HTTPException(
            status_code=409, detail="Reconciliação remota indisponível em DRY_RUN", headers=NO_STORE
        )
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

    if payment.status not in INVESTIGABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="Pagamento precisa estar em submitting ou unknown",
        )

    investigation = EfiPixInvestigationModel(
        payment_id=payment.id,
        observed_status=payment.status,
        source="api_admin",
        result="iniciada",
        period_start=inicio,
        period_end=fim,
    )
    db.add(investigation)
    db.flush()
    investigation_id = investigation.id
    # Preserva o estado observado e encerra a transação antes do acesso remoto.
    db.expunge(payment)
    db.commit()

    service = EfiPixReconciliationService(EfiPixPaymentRepository(db), EfiOpenFinanceClient())
    result = None
    try:
        result = service.inspect_payment(
            payment,
            inicio=inicio.isoformat(),
            fim=fim.isoformat(),
            quantidade=quantidade,
            max_paginas=max_paginas,
        )
    except (EfiUnexpectedResponse, PixReconciliationError):
        investigation.result = "consulta_efi_inconclusiva"
    except httpx.HTTPError:
        investigation.result = "falha_comunicacao_efi"
    except EfiConfigurationError:
        investigation.result = "configuracao_efi_indisponivel"
    else:
        investigation.result = result["diagnostico"]

    investigation.finished_at = datetime.now(UTC)
    db.commit()
    if result is None:
        raise HTTPException(
            status_code=502,
            detail="Falha na consulta de reconciliação Efí",
            headers=NO_STORE,
        )
    return {
        "investigacao_id": investigation_id,
        "local_payment_id": payment.id,
        "local_status": payment.status,
        "diagnostico": result["diagnostico"],
        "paginas_consultadas": result["paginas_consultadas"],
        "correspondencias": result["correspondencias"],
    }


@router.get("/pagamentos/{payment_id}/investigacoes")
def listar_investigacoes(
    response: Response,
    payment_id: int = Path(gt=0),
    pagina: int = Query(1, ge=1, le=100000),
    quantidade: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Consulta o histórico administrativo local."""
    response.headers.update(NO_STORE)
    if db.get(EfiPixPaymentModel, payment_id) is None:
        raise HTTPException(status_code=404, detail="Pagamento não encontrado")
    filtro = EfiPixInvestigationModel.payment_id == payment_id
    total = db.scalar(select(func.count()).select_from(EfiPixInvestigationModel).where(filtro))
    investigations = db.scalars(
        select(EfiPixInvestigationModel)
        .where(filtro)
        .order_by(EfiPixInvestigationModel.id.desc())
        .offset((pagina - 1) * quantidade)
        .limit(quantidade)
    )
    return {
        "pagina": pagina,
        "quantidade": quantidade,
        "total": total,
        "investigacoes": [
            {
                "id": entry.id,
                "payment_id": entry.payment_id,
                "iniciada_em": _utc_iso(entry.started_at),
                "finalizada_em": _utc_iso(entry.finished_at) if entry.finished_at else None,
                "estado_observado": entry.observed_status,
                "resultado": entry.result,
                "origem": entry.source,
                "inicio": entry.period_start.isoformat(),
                "fim": entry.period_end.isoformat(),
            }
            for entry in investigations
        ],
    }
