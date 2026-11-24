# Todo modelo novo precisa ser importado aqui para o Alembic detectar.
from .efi_webhook_event_model import EfiWebhookEventModel
from .efi_pix_payment_model import EfiPixPaymentModel

__all__ = ["EfiWebhookEventModel", "EfiPixPaymentModel"]
