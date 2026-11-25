from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.database import Base


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


class Datas:
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class Cadastro(Datas):
    ativo: Mapped[bool] = mapped_column(default=True)


class Cliente(Cadastro, Base):
    __tablename__ = "gestao_clientes"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(40), unique=True)
    nome: Mapped[str] = mapped_column(String(120))


class Instituicao(Base):
    __tablename__ = "gestao_instituicoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(24), unique=True)
    nome: Mapped[str] = mapped_column(String(80))


class Conta(Cadastro, Base):
    __tablename__ = "gestao_contas"
    __table_args__ = (
        UniqueConstraint("instituicao_id", "ambiente", "referencia", name="uq_gestao_conta_escopo"),
        CheckConstraint(
            "ambiente IN ('simulacao', 'homologacao', 'producao')", name="ck_gestao_ambiente"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instituicao_id: Mapped[int] = mapped_column(ForeignKey("gestao_instituicoes.id"))
    referencia: Mapped[str] = mapped_column(String(80))
    ambiente: Mapped[str] = mapped_column(String(12))


class Terminal(Cadastro, Base):
    __tablename__ = "gestao_terminais"
    __table_args__ = (
        UniqueConstraint("conta_id", "identificador", name="uq_gestao_terminal_escopo"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conta_id: Mapped[int] = mapped_column(ForeignKey("gestao_contas.id"))
    identificador: Mapped[str] = mapped_column(String(80))
    apelido: Mapped[str] = mapped_column(String(120))


class Vinculo(Base):
    __tablename__ = "gestao_vinculos"
    __table_args__ = (
        UniqueConstraint("terminal_id", "inicio", name="uq_gestao_vinculo_inicio"),
        CheckConstraint("fim IS NULL OR fim > inicio", name="ck_gestao_vinculo_intervalo"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    terminal_id: Mapped[int] = mapped_column(ForeignKey("gestao_terminais.id"))
    cliente_id: Mapped[int] = mapped_column(ForeignKey("gestao_clientes.id"), index=True)
    inicio: Mapped[datetime] = mapped_column(DateTime)
    fim: Mapped[datetime | None] = mapped_column(DateTime)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    motivo: Mapped[str | None] = mapped_column(String(240))


class Auditoria(Base):
    __tablename__ = "gestao_auditoria"

    id: Mapped[int] = mapped_column(primary_key=True)
    entidade: Mapped[str] = mapped_column(String(24))
    entidade_id: Mapped[int] = mapped_column()
    acao: Mapped[str] = mapped_column(String(32))
    ocorrido_em: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    origem: Mapped[str] = mapped_column(String(32), default="chave_admin_compartilhada")
    detalhes: Mapped[str] = mapped_column(String(512))
