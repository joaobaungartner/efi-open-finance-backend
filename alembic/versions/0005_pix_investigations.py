"""Adicionar histórico de investigações Pix."""

import sqlalchemy as sa
from alembic import op

revision = "0005_pix_investigations"
down_revision = "0004_pix_error_diagnostics"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "efi_pix_investigations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("efi_pix_payments.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_status", sa.String(40), nullable=False),
        sa.Column("result", sa.String(80), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
    )
    op.create_index(
        "ix_efi_pix_investigations_payment_id", "efi_pix_investigations", ["payment_id"]
    )


def downgrade():
    op.drop_index("ix_efi_pix_investigations_payment_id", table_name="efi_pix_investigations")
    op.drop_table("efi_pix_investigations")
