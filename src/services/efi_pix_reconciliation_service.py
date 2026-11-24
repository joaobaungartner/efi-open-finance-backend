from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlsplit

from repositories.efi_pix_payment_repository import EfiPixPaymentRepository
from services.efi_api_client import EfiConfigurationError, EfiOpenFinanceClient


class PixReconciliationError(RuntimeError):
    """Resposta incompleta ou insegura para conciliação."""


class EfiPixReconciliationService:
    """Diagnóstico somente de leitura para pagamentos unknown."""

    def __init__(
        self,
        repository: EfiPixPaymentRepository,
        client: EfiOpenFinanceClient,
    ):
        self.repository = repository
        self.client = client

    def inspect_unknown(
        self,
        *,
        idempotency_key: str,
        inicio: str,
        fim: str,
        quantidade: int = 100,
        max_paginas: int = 20,
    ) -> dict:
        if self.client.settings.efi_environment != "homologation":
            raise EfiConfigurationError(
                "Reconciliação limitada à Homologação"
            )

        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ValueError("Chave de idempotência inválida")

        if type(quantidade) is not int or not 1 <= quantidade <= 100:
            raise ValueError("Quantidade inválida")

        if type(max_paginas) is not int or not 1 <= max_paginas <= 50:
            raise ValueError("Limite de páginas inválido")

        payment = self.repository.get_by_idempotency_key(
            idempotency_key
        )

        if payment is None:
            raise ValueError("Pagamento local não encontrado")

        if payment.status != "unknown":
            raise ValueError(
                "Somente pagamentos unknown podem ser inspecionados"
            )

        if not payment.id_proprio:
            raise PixReconciliationError(
                "Pagamento sem idProprio persistido"
            )

        matches = []
        items_seen = 0
        reported_total = None

        for pagina in range(1, max_paginas + 1):
            data = self.client.list_pix_payments(
                inicio,
                fim,
                pagina=pagina,
                quantidade=quantidade,
            )

            if (
                not isinstance(data, dict)
                or not isinstance(data.get("pagamentos"), list)
            ):
                raise PixReconciliationError(
                    "Resposta Efí sem lista de pagamentos"
                )

            items = data["pagamentos"]

            if any(not isinstance(item, dict) for item in items):
                raise PixReconciliationError(
                    "Pagamento Efí com formato inválido"
                )

            items_seen += len(items)

            matches.extend(
                item
                for item in items
                if item.get("idProprio") == payment.id_proprio
            )

            if "total" in data:
                total = data["total"]

                if type(total) is int and total >= 0:
                    pass
                elif isinstance(total, str) and total.isdecimal():
                    total = int(total)
                else:
                    raise PixReconciliationError(
                        "Total de pagamentos inválido"
                    )

                if reported_total is None:
                    reported_total = total
                elif total != reported_total:
                    raise PixReconciliationError(
                        "Total mudou durante a paginação"
                    )

            next_link = data.get("proximo")

            if next_link is None:
                if (
                    reported_total is not None
                    and items_seen != reported_total
                ):
                    raise PixReconciliationError(
                        "Consulta Efí incompleta"
                    )
                break

            if not isinstance(next_link, str) or not next_link:
                raise PixReconciliationError(
                    "Paginação Efí inválida"
                )

            url = urlsplit(next_link)

            if (
                url.scheme
                or url.netloc
                or url.fragment
                or url.path not in (
                    "",
                    "/pagamentos/pix",
                    "/v1/pagamentos/pix",
                )
            ):
                raise PixReconciliationError(
                    "Link de paginação não permitido"
                )

            params = parse_qs(url.query)

            if params.get("pagina") != [str(pagina + 1)]:
                raise PixReconciliationError(
                    "Próxima página inconsistente"
                )

            expected_params = {
                "inicio": inicio,
                "fim": fim,
                "quantidade": str(quantidade),
            }

            for name, expected in expected_params.items():
                if (
                    name in params
                    and params[name] != [expected]
                ):
                    raise PixReconciliationError(
                        "Parâmetros da paginação foram alterados"
                    )

        else:
            raise PixReconciliationError(
                "Limite de páginas atingido; consulta incompleta"
            )

        result = {
            "local_payment_id": payment.id,
            "id_proprio": payment.id_proprio,
            "local_status": payment.status,
            "paginas_consultadas": pagina,
            "correspondencias": len(matches),
        }

        if not matches:
            return {
                **result,
                "diagnostico": "nao_encontrado_no_periodo",
            }

        if len(matches) != 1:
            return {
                **result,
                "diagnostico": "ambiguo",
            }

        remote = matches[0]

        identifier = remote.get("identificadorPagamento")
        remote_status = remote.get("status")
        value = remote.get("valor")

        if (
            not isinstance(identifier, str)
            or not identifier
            or not isinstance(remote_status, str)
            or not remote_status
            or not isinstance(value, str)
        ):
            return {
                **result,
                "diagnostico": "dados_remotos_incompletos",
            }

        try:
            remote_amount = Decimal(value)
        except InvalidOperation:
            return {
                **result,
                "diagnostico": "dados_remotos_incompletos",
            }

        if not remote_amount.is_finite():
            return {
                **result,
                "diagnostico": "dados_remotos_incompletos",
            }

        result.update(
            efi_payment_id=identifier,
            efi_status=remote_status,
        )

        if (
            payment.efi_payment_id
            and payment.efi_payment_id != identifier
        ):
            return {
                **result,
                "diagnostico": "identificador_divergente",
            }

        if remote_amount != payment.amount:
            return {
                **result,
                "diagnostico": "valor_divergente",
            }

        return {
            **result,
            "diagnostico": "correspondencia_consistente",
        }
