from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import URL, create_engine, event, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import app
from config.settings import get_settings
from database import database
from models.gestao import Auditoria, Vinculo

PREFIX = "/internal/gestao/v1"
INICIO = "2025-01-01T12:00:00Z"
TRANSFERENCIA = "2025-02-01T12:00:00Z"


@pytest.fixture(scope="module")
def sqlite_schema(tmp_path_factory):
    path = tmp_path_factory.mktemp("gestao") / "gestao.sqlite3"
    engine = create_engine(URL.create("sqlite", database=str(path)))

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON").close()

    config = Config("alembic.ini")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(database, "engine", engine)
        command.upgrade(config, "head")
        command.check(config)
        command.downgrade(config, "0005_pix_investigations")
        assert not any(name.startswith("gestao_") for name in inspect(engine).get_table_names())
        assert "efi_pix_investigations" in inspect(engine).get_table_names()
        command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def gestao(sqlite_schema, monkeypatch):
    def session():
        with Session(sqlite_schema, autoflush=False) as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, database.get_db, session)
    monkeypatch.setattr(get_settings(), "dry_run", True)
    headers = {"X-Admin-Key": get_settings().admin_api_key.get_secret_value()}
    with TestClient(app, base_url="http://testserver" + PREFIX, headers=headers) as client:
        yield client


def post(client, path, **body):
    response = client.post(path, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def catalogo(client, prefixo):
    a = post(client, "/clientes", codigo=f"{prefixo}-a", nome="Cliente fictício A")
    b = post(client, "/clientes", codigo=f"{prefixo}-b", nome="Cliente fictício B")
    instituicoes = client.get("/instituicoes").json()["itens"]
    stone = next(row for row in instituicoes if row["codigo"] == "stone")
    conta = post(
        client,
        "/contas-adquirencia",
        instituicao_id=stone["id"],
        referencia=f"{prefixo}-contrato",
        ambiente="simulacao",
    )
    terminal = post(
        client, "/terminais", conta_id=conta["id"], identificador="T-01", apelido=prefixo
    )
    return a, b, conta, terminal


def test_catalogo_paginacao_seguranca_e_auditoria(gestao, sqlite_schema):
    assert gestao.get("/instituicoes", headers={"X-Admin-Key": "invalida"}).status_code == 401
    assert gestao.get("/instituicoes", headers=[(b"x-admin-key", b"\xff")]).status_code == 401
    a, _, conta, terminal = catalogo(gestao, "cadastro")
    assert {row["codigo"] for row in gestao.get("/instituicoes").json()["itens"]} == {
        "stone",
        "pagbank",
        "rede",
        "c6",
    }
    assert (
        gestao.post(
            "/clientes", json={"codigo": a["codigo"].lower(), "nome": "Conflito"}
        ).status_code
        == 409
    )
    assert gestao.get(f"/clientes/{a['id']}").json()["nome"] == a["nome"]
    assert gestao.patch(f"/clientes/{a['id']}", json={"nome": None}).status_code == 422
    result = gestao.patch(
        f"/clientes/{a['id']}", json={"nome": "Cliente fictício editado", "ativo": False}
    )
    assert result.status_code == 200 and result.json()["ativo"] is False
    data = gestao.get(
        "/clientes", params={"busca": "cadastro", "ativo": False, "quantidade": 1}
    ).json()
    assert data["total"] == 1 and data["itens"][0]["nome"] == "Cliente fictício editado"
    assert (
        gestao.get("/clientes", params={"busca": "cadastro", "quantidade": 1, "pagina": 2}).json()[
            "total"
        ]
        == 2
    )
    origem = conta["instituicao"]["id"]
    body = {"instituicao_id": origem, "referencia": conta["referencia"], "ambiente": "producao"}
    assert gestao.post("/contas-adquirencia", json=body).status_code == 422
    assert (
        gestao.post("/contas-adquirencia", json=body | {"ambiente": "simulacao"}).status_code == 409
    )
    segunda = post(
        gestao,
        "/contas-adquirencia",
        **(body | {"referencia": "contrato-2", "ambiente": "simulacao"}),
    )
    cadastro = {
        "conta_id": conta["id"],
        "identificador": terminal["identificador"],
        "apelido": "Segundo",
    }
    assert gestao.post("/terminais", json=cadastro).status_code == 409
    assert (
        post(gestao, "/terminais", **(cadastro | {"conta_id": segunda["id"]}))["id"]
        != terminal["id"]
    )
    assert gestao.post("/terminais", json=cadastro | {"conta_id": 999999}).status_code == 404
    assert (
        gestao.patch(f"/terminais/{terminal['id']}", json={"ativo": False}).json()["ativo"] is False
    )
    filtrados = gestao.get(
        "/terminais", params={"instituicao_id": origem, "busca": "T-01", "ativo": False}
    ).json()
    assert [row["id"] for row in filtrados["itens"]] == [terminal["id"]]
    with Session(sqlite_schema) as db:
        audit = db.scalars(
            select(Auditoria)
            .where(Auditoria.entidade == "cliente", Auditoria.entidade_id == a["id"])
            .order_by(Auditoria.id)
        ).all()
        assert [entry.acao for entry in audit] == ["cadastro", "desativacao"]
        assert {entry.origem for entry in audit} == {"chave_admin_compartilhada"}
        assert "Cliente fictício" not in audit[1].detalhes


def test_historico_transferencia_e_consulta_temporal(gestao, sqlite_schema):
    a, b, _, terminal = catalogo(gestao, "vigencia")
    path = f"/terminais/{terminal['id']}"
    antigo = post(gestao, path + "/vinculos", cliente_id=a["id"], inicio=INICIO)
    fora_da_faixa = "0001-01-01T00:00:00+01:00"
    for consulta in (path, "/terminais"):
        assert gestao.get(consulta, params={"em": fora_da_faixa}).status_code == 422
    assert (
        gestao.post(
            path + "/vinculos", json={"cliente_id": a["id"], "inicio": fora_da_faixa}
        ).status_code
        == 422
    )
    assert (
        gestao.post(
            path + "/vinculos", json={"cliente_id": b["id"], "inicio": TRANSFERENCIA}
        ).status_code
        == 409
    )
    assert (
        gestao.post(
            path + "/vinculos", json={"cliente_id": b["id"], "inicio": TRANSFERENCIA, "fim": INICIO}
        ).status_code
        == 422
    )
    assert gestao.get(path, params={"em": "2025-01-01T12:00:00"}).status_code == 422
    assert gestao.get(path, params={"em": "2024-12-31T23:59:59Z"}).json()["vinculo"] is None
    novo = post(
        gestao,
        path + "/transferencias",
        cliente_id=b["id"],
        vinculo_anterior_id=antigo["id"],
        fim_anterior=None,
        efetiva_em="2025-02-01T09:00:00-03:00",
        motivo="Troca fictícia",
    )
    assert novo["inicio"] == TRANSFERENCIA
    assert gestao.get(path, params={"em": INICIO}).json()["vinculo"]["cliente"]["id"] == a["id"]
    assert (
        gestao.get(path, params={"em": TRANSFERENCIA}).json()["vinculo"]["cliente"]["id"] == b["id"]
    )
    assert (
        gestao.get("/terminais", params={"cliente_id": a["id"], "em": INICIO}).json()["total"] == 1
    )
    assert (
        gestao.get("/terminais", params={"cliente_id": a["id"], "em": TRANSFERENCIA}).json()[
            "total"
        ]
        == 0
    )
    assert gestao.get("/resumo").json()["terminais_vinculados"] >= 1
    history = gestao.get(path + "/vinculos").json()["itens"]
    assert [row["id"] for row in history] == [novo["id"], antigo["id"]]
    assert history[1]["fim"] == TRANSFERENCIA
    fechado = gestao.post(
        path + "/transferencias",
        json={
            "cliente_id": b["id"],
            "vinculo_anterior_id": antigo["id"],
            "fim_anterior": TRANSFERENCIA,
            "efetiva_em": "2025-01-15T12:00:00Z",
            "motivo": "Tentativa de alterar histórico",
        },
    )
    assert fechado.status_code == 409
    assert gestao.get(path + "/vinculos").json()["itens"] == history
    with Session(sqlite_schema) as db:
        events = db.scalars(
            select(Auditoria.acao)
            .where(
                Auditoria.entidade == "terminal",
                Auditoria.entidade_id == terminal["id"],
            )
            .order_by(Auditoria.id)
        ).all()
        assert events == ["cadastro", "criacao_vinculo", "transferencia"]
        db.add(Vinculo(terminal_id=terminal["id"], cliente_id=a["id"], inicio=datetime(2025, 2, 2)))
        with pytest.raises(IntegrityError, match="gestao_vinculo_sobreposto"):
            db.flush()


def test_transferencias_concorrentes_sqlite(gestao):
    a, b, _, terminal = catalogo(gestao, "concorrencia")
    path = f"/terminais/{terminal['id']}"
    antigo = post(gestao, path + "/vinculos", cliente_id=a["id"], inicio=INICIO)
    barrier = Barrier(2, timeout=10)

    def request(date):
        barrier.wait()
        return gestao.post(
            path + "/transferencias",
            json={
                "cliente_id": b["id"],
                "vinculo_anterior_id": antigo["id"],
                "fim_anterior": None,
                "efetiva_em": date,
                "motivo": "Teste concorrente",
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(request, [TRANSFERENCIA, "2025-03-01T12:00:00Z"])) == [201, 409]
    assert gestao.get(path + "/vinculos").json()["total"] == 2


def test_todas_as_rotas_gestao_exigem_chave():
    with TestClient(app) as client:
        for route in app.routes:
            if route.path.startswith(PREFIX):
                for method in route.methods:
                    response = client.request(method, route.path.replace("{id}", "1"))
                    assert response.status_code == 401, (method, route.path)


def test_transferencia_reverte_vinculos_se_auditoria_falhar(gestao, sqlite_schema):
    a, b, _, terminal = catalogo(gestao, "rollback")
    path = f"/terminais/{terminal['id']}"
    antigo = post(gestao, path + "/vinculos", cliente_id=a["id"], inicio=INICIO)
    body = {
        "cliente_id": b["id"],
        "vinculo_anterior_id": antigo["id"],
        "fim_anterior": None,
        "efetiva_em": TRANSFERENCIA,
        "motivo": " ",
    }
    assert gestao.post(path + "/transferencias", json=body).status_code == 422

    def falhar_auditoria(mapper, connection, auditoria):
        if auditoria.acao == "transferencia":
            raise RuntimeError("Falha de auditoria proposital")

    event.listen(Auditoria, "before_insert", falhar_auditoria)
    try:
        with pytest.raises(RuntimeError, match="Falha de auditoria proposital"):
            gestao.post(path + "/transferencias", json=body | {"motivo": "Justificativa fictícia"})
    finally:
        event.remove(Auditoria, "before_insert", falhar_auditoria)

    assert gestao.get(path + "/vinculos").json()["itens"] == [antigo]
    with Session(sqlite_schema) as db:
        assert db.scalars(
            select(Auditoria.acao)
            .where(Auditoria.entidade == "terminal", Auditoria.entidade_id == terminal["id"])
            .order_by(Auditoria.id)
        ).all() == ["cadastro", "criacao_vinculo"]
