from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from config.settings import get_settings
from entities.efi_pix_request import PixByKeyRequest
from entities.efi_pix_simulation import SIMULATED_PARTICIPANTS, PixSimulationRequest
from middlewares.require_admin_api_key import require_admin_api_key

NO_STORE = {"Cache-Control": "no-store"}


class SimulationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(
                    status_code=422,
                    detail="Use Stone ou PagBank, um alias @example.invalid e um valor positivo com duas casas decimais.",
                    headers=NO_STORE,
                ) from None

        return handle


router = APIRouter(
    prefix="/internal/efi/pix",
    tags=["Efí Pix - simulação"],
    dependencies=[Depends(require_admin_api_key)],
    route_class=SimulationRoute,
)


@router.post("/simulacoes")
def simular_pix(request: PixSimulationRequest, response: Response):
    if not get_settings().dry_run:
        raise HTTPException(status_code=409, detail="Simulação requer DRY_RUN", headers=NO_STORE)

    payload = PixByKeyRequest.model_validate({
        "pagador": {"idParticipante": request.instituicao, "cpf": "00000000191"},
        "favorecido": {"chave": request.favorecido},
        "pagamento": {"valor": request.valor},
    })
    response.headers.update(NO_STORE)
    return {
        "modo": "DRY_RUN",
        "instituicao": SIMULATED_PARTICIPANTS[request.instituicao],
        "previa": {
            "pagador": {"idParticipante": payload.pagador.idParticipante, "cpf": "[omitido]"},
            "favorecido": {"chave": "***@example.invalid"},
            "pagamento": {"valor": payload.pagamento.valor},
        },
        "etapas": [
            "Validar o formato da solicitação e identificar a instituição pagadora.",
            "Reservar a solicitação, gerar o ID próprio e reivindicar o envio com idempotência.",
            "Autenticar via OAuth/mTLS e enviar a iniciação à Efí.",
            "Obter a autorização do pagador na instituição e acompanhar o resultado.",
            "Confirmar a liquidação por evidência financeira e conciliar com o extrato.",
        ],
        "envio_bancario": False,
        "pagamento_persistido": False,
    }
