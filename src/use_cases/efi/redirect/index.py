from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["Efí callbacks"])


@router.get("/openfinance/retorno", response_class=PlainTextResponse)
def efi_redirect(
    identificadorPagamento: str | None = Query(default=None, max_length=300),
    erro: str | None = Query(default=None, max_length=200),
):
    # Não confirmar sucesso financeiro por retorno do navegador.
    # Não ecoar identificador nem erro na página: evita exposição de dados.
    if erro:
        return "Autorização não concluída. Consulte o status no painel."
    if identificadorPagamento:
        return "Retorno recebido. O pagamento ainda precisa ser confirmado pela API."
    return "Endpoint de retorno Efí disponível."
