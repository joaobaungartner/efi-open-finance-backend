"""Criar tabela de solicitações Pix da Efí.

Revision ID: 0002_pix_payments
Revises: 0001_efi_events
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_pix_payments"
down_revision = "0001_efi_events"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "efi_pix_payments",

        sa.Column("id", sa.Integer, primary_key=True),

        sa.Column(
            "idempotency_key",
            sa.String(128),
            nullable=False,
        ),

        sa.Column(
            "request_hash",
            sa.String(64),
            nullable=False,
        ),

        sa.Column(
            "efi_payment_id",
            sa.String(300),
            nullable=True,
        ),

        sa.Column(
            "status",
            sa.String(40),
            nullable=False,
            server_default="draft",
        ),

        sa.Column(
            "amount",
            sa.Numeric(18, 2),
            nullable=False,
        ),

        sa.Column(
            "origin_institution",
            sa.String(120),
            nullable=False,
        ),

        sa.Column(
            "redirect_uri",
            sa.Text,
            nullable=True,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),

        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),

        sa.CheckConstraint(
            "amount > 0",
            name="ck_efi_pix_payments_amount_positive",
        ),
    )

    op.create_index(
        "ix_efi_pix_payments_idempotency_key",
        "efi_pix_payments",
        ["idempotency_key"],
        unique=True,
    )

    op.create_index(
        "ix_efi_pix_payments_efi_payment_id",
        "efi_pix_payments",
        ["efi_payment_id"],
        unique=True,
    )


def downgrade():
    op.drop_index(
        "ix_efi_pix_payments_efi_payment_id",
        table_name="efi_pix_payments",
    )

    op.drop_index(
        "ix_efi_pix_payments_idempotency_key",
        table_name="efi_pix_payments",
    )

    op.drop_table("efi_pix_payments")
