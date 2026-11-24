# Todo modelo novo precisa ser importado aqui para o Alembic detectar.
from .efi_pix_investigation_model import EfiPixInvestigationModel
from .efi_pix_payment_model import EfiPixPaymentModel
from .efi_webhook_event_model import EfiWebhookEventModel

__all__ = ["EfiPixInvestigationModel", "EfiPixPaymentModel", "EfiWebhookEventModel"]
