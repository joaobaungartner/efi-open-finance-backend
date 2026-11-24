"""Adicionar identificador próprio às solicitações Pix.

Revision ID: 0003_pix_id_proprio
Revises: 0002_pix_payments
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_pix_id_proprio"
down_revision = "0002_pix_payments"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column(
        "efi_pix_payments",
        sa.Column(
            "id_proprio",
            sa.String(100),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_efi_pix_payments_id_proprio",
        "efi_pix_payments",
        ["id_proprio"],
        unique=True,
    )

def downgrade():
    op.drop_index(
        "ix_efi_pix_payments_id_proprio",
        table_name="efi_pix_payments",
    )

    op.drop_column(
        "efi_pix_payments",
        "id_proprio",
    )
