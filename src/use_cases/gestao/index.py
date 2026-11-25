from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from config.settings import get_settings
from database.database import get_db
from entities.gestao import (
    EditarCliente,
    EditarTerminal,
    InstanteUTC,
    NovaConta,
    NovoCliente,
    NovoTerminal,
    NovoVinculo,
    Transferencia,
)
from middlewares.require_admin_api_key import require_admin_api_key
from models.gestao import Cliente, Conta, Instituicao, Terminal, Vinculo, utc_now
from services.gestao_vinculos import (
    auditar,
    criar_vinculo,
    gravacao,
    obter,
    transferir,
    validar_destino,
    vigente,
)


class GestaoRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            chave = request.headers.get("X-Admin-Key")
            if chave and not chave.isascii():
                raise HTTPException(401, "Não autorizado")
            try:
                response = await handler(request)
            except RequestValidationError:
                raise HTTPException(
                    422,
                    "Confira os campos informados e use datas com fuso horário.",
                    headers={"Cache-Control": "no-store"},
                ) from None
            response.headers["Cache-Control"] = "no-store"
            return response

        return handle


router = APIRouter(
    prefix="/internal/gestao/v1",
    tags=["Gestão de maquininhas"],
    dependencies=[Depends(require_admin_api_key)],
    route_class=GestaoRoute,
)
Db = Annotated[Session, Depends(get_db)]
Identidade = Annotated[int, Path(gt=0)]
Pagina = Annotated[int, Query(ge=1, le=100000)]
Quantidade = Annotated[int, Query(ge=1, le=100)]
Busca = Annotated[str, Query(min_length=1, max_length=120)]
FiltroId = Annotated[int, Query(gt=0)]


def iso(value):
    return value.isoformat() + "Z" if value is not None else None


def cadastro(row):
    return {
        "id": row.id,
        "ativo": row.ativo,
        "criado_em": iso(row.criado_em),
        "atualizado_em": iso(row.atualizado_em),
    }


def cliente(row):
    return {**cadastro(row), "codigo": row.codigo, "nome": row.nome}


def instituicao(row):
    return {"id": row.id, "codigo": row.codigo, "nome": row.nome}


def conta(row, origem):
    return {
        **cadastro(row),
        "instituicao": instituicao(origem),
        "referencia": row.referencia,
        "ambiente": row.ambiente,
    }


def vinculo(row, dono):
    if row is None:
        return None
    return {
        "id": row.id,
        "terminal_id": row.terminal_id,
        "cliente": cliente(dono),
        "inicio": iso(row.inicio),
        "fim": iso(row.fim),
        "criado_em": iso(row.criado_em),
        "motivo": row.motivo,
    }


def terminal(row, contrato, origem, relacao, dono):
    return {
        **cadastro(row),
        "identificador": row.identificador,
        "apelido": row.apelido,
        "conta": conta(contrato, origem),
        "vinculo": vinculo(relacao, dono),
    }


def paginar(db, statement, pagina, quantidade, serializar):
    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    rows = db.execute(statement.offset((pagina - 1) * quantidade).limit(quantidade))
    return {
        "pagina": pagina,
        "quantidade": quantidade,
        "total": total,
        "itens": [serializar(*row) for row in rows],
    }


def editar(db, row, entrada, entidade):
    campos = entrada.model_dump(exclude_unset=True)
    if "codigo" in campos:
        campos["codigo"] = campos["codigo"].upper()
    alterados = {key: value for key, value in campos.items() if getattr(row, key) != value}
    if alterados:
        for key, value in alterados.items():
            setattr(row, key, value)
        acao = "desativacao" if alterados.get("ativo") is False else "alteracao"
        detalhes = {"campos": sorted(alterados)}
        if "ativo" in alterados:
            detalhes["ativo"] = row.ativo
        auditar(db, entidade, row.id, acao, **detalhes)
        db.flush()


@router.get("/clientes")
def listar_clientes(
    db: Db,
    busca: Busca | None = None,
    ativo: bool | None = None,
    pagina: Pagina = 1,
    quantidade: Quantidade = 20,
):
    statement = select(Cliente).order_by(Cliente.id)
    if busca:
        statement = statement.where(
            or_(
                Cliente.nome.icontains(busca, autoescape=True),
                Cliente.codigo.icontains(busca, autoescape=True),
            )
        )
    if ativo is not None:
        statement = statement.where(Cliente.ativo == ativo)
    return paginar(db, statement, pagina, quantidade, cliente)


@router.post("/clientes", status_code=201)
def cadastrar_cliente(entrada: NovoCliente, db: Db):
    with gravacao(db):
        row = Cliente(codigo=entrada.codigo.upper(), nome=entrada.nome)
        db.add(row)
        db.flush()
        auditar(db, "cliente", row.id, "cadastro")
        return cliente(row)


@router.get("/clientes/{id}")
def consultar_cliente(id: Identidade, db: Db):
    return cliente(obter(db, Cliente, id))


@router.patch("/clientes/{id}")
def alterar_cliente(id: Identidade, entrada: EditarCliente, db: Db):
    with gravacao(db):
        row = obter(db, Cliente, id, bloquear=True)
        editar(db, row, entrada, "cliente")
        return cliente(row)


@router.get("/instituicoes")
def listar_instituicoes(db: Db):
    return {
        "itens": [
            instituicao(row) for row in db.scalars(select(Instituicao).order_by(Instituicao.id))
        ]
    }


@router.get("/contas-adquirencia")
def listar_contas(
    db: Db, instituicao_id: FiltroId | None = None, pagina: Pagina = 1, quantidade: Quantidade = 20
):
    statement = select(Conta, Instituicao).join(Instituicao).order_by(Conta.id)
    if instituicao_id is not None:
        statement = statement.where(Conta.instituicao_id == instituicao_id)
    return paginar(db, statement, pagina, quantidade, conta)


@router.post("/contas-adquirencia", status_code=201)
def cadastrar_conta(entrada: NovaConta, db: Db):
    if get_settings().dry_run and entrada.ambiente != "simulacao":
        raise HTTPException(422, "Cadastre somente contas fictícias no ambiente de simulação")
    with gravacao(db):
        origem = obter(db, Instituicao, entrada.instituicao_id)
        row = Conta(**entrada.model_dump())
        db.add(row)
        db.flush()
        auditar(db, "conta", row.id, "cadastro", instituicao_id=origem.id)
        return conta(row, origem)


def consulta_terminais(instante):
    # A scalar ID only resolves an association when precisely one interval matches.
    candidato = aliased(Vinculo)
    unico = (
        select(func.min(candidato.id))
        .where(
            candidato.terminal_id == Terminal.id,
            candidato.inicio <= instante,
            or_(candidato.fim.is_(None), candidato.fim > instante),
        )
        .having(func.count() == 1)
        .correlate(Terminal)
        .scalar_subquery()
    )
    return (
        select(Terminal, Conta, Instituicao, Vinculo, Cliente)
        .select_from(Terminal)
        .join(Conta)
        .join(Instituicao)
        .outerjoin(Vinculo, Vinculo.id == unico)
        .outerjoin(Cliente, Cliente.id == Vinculo.cliente_id)
    )


def detalhe_terminal(db, id, instante):
    row = db.execute(consulta_terminais(instante).where(Terminal.id == id)).one_or_none()
    if row is None:
        raise HTTPException(404, "Terminal não encontrado")
    return terminal(*row)


@router.get("/terminais")
def listar_terminais(
    db: Db,
    busca: Busca | None = None,
    instituicao_id: FiltroId | None = None,
    cliente_id: FiltroId | None = None,
    ativo: bool | None = None,
    em: InstanteUTC | None = None,
    pagina: Pagina = 1,
    quantidade: Quantidade = 20,
):
    statement = consulta_terminais(em or utc_now()).order_by(Terminal.id)
    if busca:
        statement = statement.where(
            or_(
                Terminal.identificador.icontains(busca, autoescape=True),
                Terminal.apelido.icontains(busca, autoescape=True),
            )
        )
    if instituicao_id is not None:
        statement = statement.where(Conta.instituicao_id == instituicao_id)
    if cliente_id is not None:
        statement = statement.where(Vinculo.cliente_id == cliente_id)
    if ativo is not None:
        statement = statement.where(Terminal.ativo == ativo)
    return paginar(db, statement, pagina, quantidade, terminal)


@router.post("/terminais", status_code=201)
def cadastrar_terminal(entrada: NovoTerminal, db: Db):
    with gravacao(db):
        contrato = obter(db, Conta, entrada.conta_id, bloquear=True)
        if not contrato.ativo:
            raise HTTPException(409, "A conta precisa estar ativa")
        row = Terminal(**entrada.model_dump())
        db.add(row)
        db.flush()
        auditar(db, "terminal", row.id, "cadastro", conta_id=contrato.id)
        return detalhe_terminal(db, row.id, utc_now())


@router.get("/terminais/{id}")
def consultar_terminal(id: Identidade, db: Db, em: InstanteUTC | None = None):
    return detalhe_terminal(db, id, em or utc_now())


@router.patch("/terminais/{id}")
def alterar_terminal(id: Identidade, entrada: EditarTerminal, db: Db):
    with gravacao(db):
        row = obter(db, Terminal, id, bloquear=True)
        editar(db, row, entrada, "terminal")
        return detalhe_terminal(db, id, utc_now())


@router.get("/terminais/{id}/vinculos")
def listar_vinculos(id: Identidade, db: Db, pagina: Pagina = 1, quantidade: Quantidade = 20):
    obter(db, Terminal, id)
    statement = (
        select(Vinculo, Cliente)
        .join(Cliente)
        .where(Vinculo.terminal_id == id)
        .order_by(Vinculo.inicio.desc(), Vinculo.id.desc())
    )
    return paginar(db, statement, pagina, quantidade, vinculo)


@router.post("/terminais/{id}/vinculos", status_code=201)
def cadastrar_vinculo(id: Identidade, entrada: NovoVinculo, db: Db):
    with gravacao(db):
        validar_destino(db, id, entrada.cliente_id)
        row = criar_vinculo(
            db,
            id,
            entrada.cliente_id,
            entrada.inicio,
            entrada.fim,
            entrada.motivo,
        )
        auditar(db, "terminal", id, "criacao_vinculo", vinculo_id=row.id, cliente_id=row.cliente_id)
        return vinculo(row, obter(db, Cliente, row.cliente_id))


@router.post("/terminais/{id}/transferencias", status_code=201)
def transferir_terminal(id: Identidade, entrada: Transferencia, db: Db):
    with gravacao(db):
        row = transferir(
            db,
            id,
            entrada.cliente_id,
            entrada.efetiva_em,
            entrada.vinculo_anterior_id,
            entrada.fim_anterior,
            entrada.motivo,
        )
        return vinculo(row, obter(db, Cliente, row.cliente_id))


@router.get("/resumo")
def resumo(db: Db):
    clientes, clientes_ativos, terminais, terminais_ativos, vinculados = db.execute(
        select(
            select(func.count()).select_from(Cliente).scalar_subquery(),
            select(func.count()).where(Cliente.ativo).scalar_subquery(),
            select(func.count()).select_from(Terminal).scalar_subquery(),
            select(func.count()).where(Terminal.ativo).scalar_subquery(),
            select(func.count(func.distinct(Vinculo.terminal_id)))
            .where(*vigente(utc_now()))
            .scalar_subquery(),
        )
    ).one()
    return {
        "clientes": clientes,
        "clientes_ativos": clientes_ativos,
        "terminais": terminais,
        "terminais_ativos": terminais_ativos,
        "terminais_vinculados": vinculados,
        "terminais_sem_cliente": terminais - vinculados,
    }
