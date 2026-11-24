"""Adicionar diagnosticos HTTP das solicitacoes Pix."""

import sqlalchemy as sa
from alembic import op

revision = "0004_pix_error_diagnostics"
down_revision = "0003_pix_id_proprio"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column(
        "efi_pix_payments",
        sa.Column(
            "efi_http_status",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.add_column(
        "efi_pix_payments",
        sa.Column(
            "efi_error_code",
            sa.String(80),
            nullable=True,
        ),
    )

def downgrade():
    op.drop_column(
        "efi_pix_payments",
        "efi_error_code",
    )

    op.drop_column(
        "efi_pix_payments",
        "efi_http_status",
    )
