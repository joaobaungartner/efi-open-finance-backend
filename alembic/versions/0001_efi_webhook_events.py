"""Tabela de eventos Efí.

Revision ID: 0001_efi_events
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_efi_events"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "efi_webhook_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("identifier", sa.String(300), nullable=False),
        sa.Column("status", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("amount", sa.String(50), nullable=True),
        sa.Column("end_to_end_id", sa.String(100), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_efi_webhook_events_payload_hash", "efi_webhook_events", ["payload_hash"], unique=True)
    op.create_index("ix_efi_webhook_events_identifier", "efi_webhook_events", ["identifier"])


def downgrade():
    op.drop_index("ix_efi_webhook_events_identifier", table_name="efi_webhook_events")
    op.drop_index("ix_efi_webhook_events_payload_hash", table_name="efi_webhook_events")
    op.drop_table("efi_webhook_events")
