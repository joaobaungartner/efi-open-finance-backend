"""Cadastro de adquirentes e histórico de vínculos de terminais."""

import sqlalchemy as sa
from alembic import op

revision = "0006_gestao_terminais"
down_revision = "0005_pix_investigations"
branch_labels = None
depends_on = None


def cadastro():
    return (
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(), nullable=False),
    )


def upgrade():
    op.create_table(
        "gestao_clientes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("codigo", sa.String(40), nullable=False, unique=True),
        sa.Column("nome", sa.String(120), nullable=False),
        *cadastro(),
    )
    instituicoes = op.create_table(
        "gestao_instituicoes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("codigo", sa.String(24), nullable=False, unique=True),
        sa.Column("nome", sa.String(80), nullable=False),
    )
    op.bulk_insert(
        instituicoes,
        [
            {"codigo": "stone", "nome": "Stone"},
            {"codigo": "pagbank", "nome": "PagBank"},
            {"codigo": "rede", "nome": "Rede"},
            {"codigo": "c6", "nome": "C6"},
        ],
    )
    op.create_table(
        "gestao_contas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "instituicao_id", sa.Integer(), sa.ForeignKey("gestao_instituicoes.id"), nullable=False
        ),
        sa.Column("referencia", sa.String(80), nullable=False),
        sa.Column("ambiente", sa.String(12), nullable=False),
        *cadastro(),
        sa.UniqueConstraint(
            "instituicao_id", "ambiente", "referencia", name="uq_gestao_conta_escopo"
        ),
        sa.CheckConstraint(
            "ambiente IN ('simulacao', 'homologacao', 'producao')", name="ck_gestao_ambiente"
        ),
    )
    op.create_table(
        "gestao_terminais",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conta_id", sa.Integer(), sa.ForeignKey("gestao_contas.id"), nullable=False),
        sa.Column("identificador", sa.String(80), nullable=False),
        sa.Column("apelido", sa.String(120), nullable=False),
        *cadastro(),
        sa.UniqueConstraint("conta_id", "identificador", name="uq_gestao_terminal_escopo"),
    )
    op.create_table(
        "gestao_vinculos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "terminal_id", sa.Integer(), sa.ForeignKey("gestao_terminais.id"), nullable=False
        ),
        sa.Column("cliente_id", sa.Integer(), sa.ForeignKey("gestao_clientes.id"), nullable=False),
        sa.Column("inicio", sa.DateTime(), nullable=False),
        sa.Column("fim", sa.DateTime()),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.Column("motivo", sa.String(240)),
        sa.UniqueConstraint("terminal_id", "inicio", name="uq_gestao_vinculo_inicio"),
        sa.CheckConstraint("fim IS NULL OR fim > inicio", name="ck_gestao_vinculo_intervalo"),
    )
    op.create_index("ix_gestao_vinculos_cliente_id", "gestao_vinculos", ["cliente_id"])
    op.create_table(
        "gestao_auditoria",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entidade", sa.String(24), nullable=False),
        sa.Column("entidade_id", sa.Integer(), nullable=False),
        sa.Column("acao", sa.String(32), nullable=False),
        sa.Column("ocorrido_em", sa.DateTime(), nullable=False),
        sa.Column("origem", sa.String(32), nullable=False),
        sa.Column("detalhes", sa.String(512), nullable=False),
    )
    dialect = op.get_context().dialect.name
    if dialect == "postgresql":
        # A singleton integer range provides equality by overlap without btree_gist.
        op.execute("""
            ALTER TABLE gestao_vinculos ADD CONSTRAINT ex_gestao_vinculo_vigencia
            EXCLUDE USING gist (
                int8range(terminal_id, terminal_id, '[]') WITH &&,
                tsrange(inicio, fim, '[)') WITH &&
            )
        """)
    elif dialect == "sqlite":
        for action, existing in (("INSERT", ""), ("UPDATE", "AND v.id <> OLD.id")):
            op.execute(f"""
                CREATE TRIGGER gestao_vinculos_sem_sobreposicao_{action.lower()}
                BEFORE {action} ON gestao_vinculos
                WHEN EXISTS (
                    SELECT 1 FROM gestao_vinculos v
                    WHERE v.terminal_id = NEW.terminal_id {existing}
                      AND (NEW.fim IS NULL OR v.inicio < NEW.fim)
                      AND (v.fim IS NULL OR NEW.inicio < v.fim)
                )
                BEGIN SELECT RAISE(ABORT, 'gestao_vinculo_sobreposto'); END
            """)


def downgrade():
    for name in ("auditoria", "vinculos", "terminais", "contas", "instituicoes", "clientes"):
        op.drop_table(f"gestao_{name}")
