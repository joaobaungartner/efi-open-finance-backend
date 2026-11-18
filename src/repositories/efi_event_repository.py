from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.efi_webhook_event_model import EfiWebhookEventModel


class EfiEventRepository:
    def __init__(self, session: Session):
        self.session = session

    def save_if_new(self, event: EfiWebhookEventModel) -> bool:
        self.session.add(event)
        try:
            self.session.commit()
            return True
        except IntegrityError:
            self.session.rollback()
            # Duplicata é esperada quando a Efí reenvia um webhook.
            existing = (
                self.session.query(EfiWebhookEventModel.id)
                .filter(EfiWebhookEventModel.payload_hash == event.payload_hash)
                .first()
            )
            if existing is not None:
                return False
            raise
