from contextlib import contextmanager
from json import dumps

from fastapi import HTTPException
from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from models.gestao import Auditoria, Cliente, Conta, Terminal, Vinculo, utc_now


@contextmanager
def gravacao(db):
    try:
        with db.begin():
            if db.get_bind().dialect.name == "sqlite":
                # Reserve the writer before reading the state to be changed.
                db.execute(text("BEGIN IMMEDIATE"))
            yield
    except IntegrityError as exc:
        code = getattr(exc.orig, "sqlstate", None)
        sqlite_code = getattr(exc.orig, "sqlite_errorname", "")
        if code in {"23505", "23P01"} or sqlite_code in {
            "SQLITE_CONSTRAINT_UNIQUE",
            "SQLITE_CONSTRAINT_TRIGGER",
        }:
            raise HTTPException(409, "Código existente ou período de vínculo sobreposto") from None
        raise
    except OperationalError as exc:
        if getattr(exc.orig, "sqlite_errorname", "") in {"SQLITE_BUSY", "SQLITE_LOCKED"}:
            raise HTTPException(
                503, "Banco ocupado. Consulte o estado antes de tentar novamente."
            ) from None
        raise


def obter(db, model, id, *, bloquear=False):
    registro = db.get(model, id, with_for_update=bloquear)
    if registro is None:
        raise HTTPException(404, "Registro não encontrado")
    return registro


def auditar(db, entidade, id, acao, **detalhes):
    db.add(Auditoria(entidade=entidade, entidade_id=id, acao=acao, detalhes=dumps(detalhes)))


def vigente(instante):
    return Vinculo.inicio <= instante, or_(Vinculo.fim.is_(None), Vinculo.fim > instante)


def vinculo_em(db, terminal_id, instante):
    encontrados = db.scalars(
        select(Vinculo).where(Vinculo.terminal_id == terminal_id, *vigente(instante)).limit(2)
    ).all()
    if len(encontrados) != 1:
        return None
    return encontrados[0]


def validar_destino(db, terminal_id, cliente_id):
    terminal = obter(db, Terminal, terminal_id, bloquear=True)
    conta = obter(db, Conta, terminal.conta_id, bloquear=True)
    cliente = obter(db, Cliente, cliente_id, bloquear=True)
    if not (terminal.ativo and conta.ativo and cliente.ativo):
        raise HTTPException(409, "Terminal, conta e cliente de destino precisam estar ativos")


def criar_vinculo(db, terminal_id, cliente_id, inicio, fim, motivo):
    registro = Vinculo(
        terminal_id=terminal_id, cliente_id=cliente_id, inicio=inicio, fim=fim, motivo=motivo
    )
    db.add(registro)
    db.flush()
    return registro


def transferir(db, terminal_id, cliente_id, instante, vinculo_anterior_id, fim_anterior, motivo):
    validar_destino(db, terminal_id, cliente_id)
    anterior = vinculo_em(db, terminal_id, instante)
    if anterior is None or (anterior.id, anterior.fim) != (vinculo_anterior_id, fim_anterior):
        raise HTTPException(
            409, "O vínculo na data efetiva mudou ou está ausente. Consulte novamente."
        )
    if anterior.fim is not None and anterior.fim <= utc_now():
        raise HTTPException(409, "Um vínculo histórico encerrado não pode ser alterado")
    if anterior.cliente_id == cliente_id or instante == anterior.inicio:
        raise HTTPException(
            409, "Escolha outro cliente e um instante posterior ao início do vínculo"
        )

    fim_original = anterior.fim
    anterior.fim = instante
    db.flush()
    novo = criar_vinculo(db, terminal_id, cliente_id, instante, fim_original, motivo)
    auditar(
        db,
        "terminal",
        terminal_id,
        "transferencia",
        vinculo_anterior_id=anterior.id,
        vinculo_id=novo.id,
        cliente_anterior_id=anterior.cliente_id,
        cliente_id=cliente_id,
        efetiva_em=instante.isoformat() + "Z",
        fim_anterior=fim_original.isoformat() + "Z" if fim_original else None,
    )
    return novo
