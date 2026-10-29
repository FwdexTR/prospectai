"""Rotas do modulo de prospeccao B2B: base, listas, CRM, tarefas e cadencias.

Incluido em api.py. Tudo aqui e privado (consumido pela sua interface).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import base, crm, db, execucoes
from .config import carregar
from .ramos import listar as listar_ramos

router = APIRouter(prefix="/api")


def con() -> sqlite3.Connection:
    c = db.conectar()
    db.inicializar(c)
    return c


def _base(fn, *args, **kwargs):
    """Base ausente vira 503 com a instrucao de como resolver."""
    try:
        return fn(*args, **kwargs)
    except base.BaseIndisponivel as exc:
        raise HTTPException(503, str(exc)) from exc


def _csv(nome: str, linhas) -> StreamingResponse:
    return StreamingResponse(
        (l.encode("utf-8") for l in linhas), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


# ==========================================================================
# BASE
# ==========================================================================
class Consulta(BaseModel):
    filtros: dict[str, Any] = Field(default_factory=dict)
    pagina: int = 1
    por_pagina: int = 50
    ordem: str = "padrao"


@router.get("/base/status")
def status_base() -> dict:
    caminho = base.caminho_base()
    if caminho is None:
        return {"disponivel": False}
    meta = _base(base.meta)
    try:
        meta["arquivos"] = json.loads(meta.get("arquivos") or "{}")
    except ValueError:
        pass
    return {"disponivel": True, "arquivo": caminho.name, "meta": meta}


@router.post("/base/busca")
def buscar(req: Consulta) -> dict:
    f = base.Filtros.de_dict(req.filtros)
    r = _base(base.buscar, f, req.pagina, req.por_pagina, req.ordem)
    no_crm = crm.empresas_reveladas(con(), [i["cnpj"] for i in r["itens"]])
    for i in r["itens"]:
        i["crm"] = no_crm.get(i["cnpj"])
    return r


@router.get("/base/empresa/{cnpj}")
def ficha(cnpj: str) -> dict:
    e = _base(base.ficha, cnpj)
    if e is None:
        raise HTTPException(404, "CNPJ não está na base")
    c = con()
    e["crm"] = crm.empresas_reveladas(c, [e["cnpj"]]).get(e["cnpj"])
    e["listas"] = [dict(l) for l in db.buscar(c, """
        SELECT l.id, l.nome FROM lista l JOIN lista_item i ON i.lista_id = l.id
         WHERE i.cnpj = ? ORDER BY l.nome""", (e["cnpj"],))]
    return e


@router.get("/base/cnaes")
def cnaes(q: str = "") -> list[dict]:
    return _base(base.opcoes_cnae, q)


@router.get("/base/municipios")
def municipios(q: str = "", uf: str = "") -> list[dict]:
    ufs = [u for u in uf.upper().split(",") if u] or None
    return _base(base.opcoes_municipio, q, ufs)


@router.get("/base/atalhos")
def atalhos() -> list[dict]:
    """Ramos do catalogo como atalho de CNAE ('Oficinas' = 4520, 4530, 4543)."""
    return [{"chave": r.chave, "rotulo": r.rotulo, "cnaes": list(r.cnaes)}
            for r in listar_ramos() if r.cnaes]


@router.post("/base/analise")
def analise(req: Consulta) -> dict:
    return _base(base.analisar, base.Filtros.de_dict(req.filtros))


@router.post("/base/exportar")
def exportar(req: Consulta) -> StreamingResponse:
    f = base.Filtros.de_dict(req.filtros)
    _base(base.conectar)                       # falha antes de abrir o download
    return _csv("empresas.csv", base.exportar_csv(f))


class Carteira(BaseModel):
    texto: str | None = None                   # CNPJs colados
    cnpjs: list[str] = Field(default_factory=list)
    lista_id: int | None = None


@router.post("/base/perfil")
def perfil(req: Carteira) -> dict:
    alvo = list(req.cnpjs)
    invalidos: list[str] = []
    if req.texto:
        validos, invalidos = crm.extrair_cnpjs(req.texto)
        alvo += validos
    if req.lista_id:
        alvo += crm.itens_da_lista(con(), req.lista_id)
    if not alvo:
        raise HTTPException(400, "nenhum CNPJ válido informado")
    r = _base(base.perfil_icp, alvo)
    r["invalidos"] = invalidos
    return r


# ==========================================================================
# LISTAS
# ==========================================================================
class NovaLista(BaseModel):
    nome: str = Field(min_length=1)
    descricao: str | None = None
    cnpjs: list[str] = Field(default_factory=list)
    filtros: dict[str, Any] | None = None      # cria a partir de uma busca
    texto: str | None = None                   # CNPJs colados
    limite: int = Field(default=5000, ge=1, le=base.LIMITE_EXPORTACAO)


def _cnpjs_do_pedido(cnpjs: list[str], filtros: dict | None, texto: str | None,
                     limite: int) -> tuple[list[str], list[str]]:
    alvo, invalidos = list(cnpjs), []
    if texto:
        validos, invalidos = crm.extrair_cnpjs(texto)
        alvo += validos
    if filtros is not None:
        alvo += _base(base.cnpjs, base.Filtros.de_dict(filtros), limite)
    return alvo, invalidos


@router.get("/listas")
def listas() -> list[dict]:
    return crm.listas(con())


@router.post("/listas")
def criar_lista(req: NovaLista) -> dict:
    c = con()
    alvo, invalidos = _cnpjs_do_pedido(req.cnpjs, req.filtros, req.texto, req.limite)
    lid = crm.criar_lista(c, req.nome, req.descricao, req.filtros)
    novos = crm.adicionar_itens(c, lid, alvo)
    return {"id": lid, "adicionados": novos, "invalidos": invalidos}


@router.get("/listas/{lista_id}")
def ver_lista(lista_id: int, pagina: int = 1, por_pagina: int = 100) -> dict:
    c = con()
    l = crm.lista(c, lista_id)
    if l is None:
        raise HTTPException(404, "lista não encontrada")
    todos = crm.itens_da_lista(c, lista_id)
    pagina = max(1, pagina)
    fatia = todos[(pagina - 1) * por_pagina: pagina * por_pagina]
    try:
        dados = {f["cnpj"]: f for f in base.fichas(fatia)}
        base_ok = True
    except base.BaseIndisponivel:
        dados, base_ok = {}, False
    no_crm = crm.empresas_reveladas(c, fatia)
    itens = [(dados.get(x) or {"cnpj": x, "fora_da_base": base_ok}) | {"crm": no_crm.get(x)}
             for x in fatia]
    reveladas = crm.empresas_reveladas(c, todos)
    l["filtros"] = json.loads(l["filtros"]) if l.get("filtros") else None
    return l | {"total": len(todos), "reveladas": len(reveladas), "pagina": pagina,
                "por_pagina": por_pagina, "itens": itens}


class Itens(BaseModel):
    cnpjs: list[str] = Field(default_factory=list)
    filtros: dict[str, Any] | None = None
    texto: str | None = None
    limite: int = Field(default=5000, ge=1, le=base.LIMITE_EXPORTACAO)


@router.post("/listas/{lista_id}/itens")
def adicionar(lista_id: int, req: Itens) -> dict:
    c = con()
    if crm.lista(c, lista_id) is None:
        raise HTTPException(404, "lista não encontrada")
    alvo, invalidos = _cnpjs_do_pedido(req.cnpjs, req.filtros, req.texto, req.limite)
    return {"adicionados": crm.adicionar_itens(c, lista_id, alvo), "invalidos": invalidos}


@router.post("/listas/{lista_id}/remover")
def remover(lista_id: int, req: Itens) -> dict:
    return {"removidos": crm.remover_itens(con(), lista_id, req.cnpjs)}


@router.delete("/listas/{lista_id}")
def excluir_lista(lista_id: int) -> dict:
    crm.excluir_lista(con(), lista_id)
    return {"ok": True}


@router.get("/listas/{lista_id}/exportar")
def exportar_lista(lista_id: int) -> StreamingResponse:
    itens = crm.itens_da_lista(con(), lista_id)
    _base(base.conectar)
    return _csv(f"lista-{lista_id}.csv", base.exportar_csv(lista=itens))


@router.post("/listas/{lista_id}/revelar")
def revelar_lista(lista_id: int) -> dict:
    c = con()
    itens = crm.itens_da_lista(c, lista_id)
    r = _base(crm.revelar, c, itens)
    return {k: len(v) for k, v in r.items()}


# ==========================================================================
# REVELAR E ENRIQUECER
# ==========================================================================
class Cnpjs(BaseModel):
    cnpjs: list[str] = Field(min_length=1, max_length=5000)


@router.post("/revelar")
def revelar(req: Cnpjs) -> dict:
    return _base(crm.revelar, con(), req.cnpjs)


class Alvo(BaseModel):
    empresa_ids: list[int] = Field(default_factory=list)
    lista_id: int | None = None


def _ids_do_alvo(c: sqlite3.Connection, alvo: Alvo) -> list[int]:
    ids = list(alvo.empresa_ids)
    if alvo.lista_id:
        ids += [v["empresa_id"] for v in
                crm.empresas_reveladas(c, crm.itens_da_lista(c, alvo.lista_id)).values()]
    return list(dict.fromkeys(ids))


def _rodar_enriquecimento(execucao_id: str, ids: list[int]) -> None:
    c = con()
    try:
        r = crm.enriquecer(c, ids, execucoes.progresso_de(execucao_id))
        execucoes.atualizar(execucao_id, estado="concluida", resumo=r)
    except Exception as exc:                    # noqa: BLE001 -- vai para a tela
        execucoes.atualizar(execucao_id, estado="erro", erro=f"{type(exc).__name__}: {exc}")
    finally:
        c.close()


@router.post("/enriquecer")
def enriquecer(req: Alvo, tarefas: BackgroundTasks) -> dict:
    ids = _ids_do_alvo(con(), req)
    if not ids:
        raise HTTPException(400, "nenhuma empresa revelada para enriquecer")
    if len(ids) > 2000:
        raise HTTPException(400, f"{len(ids)} empresas de uma vez é demais; divida em lotes de 2000")
    execucao_id = execucoes.nova("enriquecimento")
    tarefas.add_task(_rodar_enriquecimento, execucao_id, ids)
    return {"execucao_id": execucao_id, "empresas": len(ids)}


# ==========================================================================
# CRM: funil, valor, atividades e tarefas
# ==========================================================================
@router.get("/crm/funil")
def funil(busca: str | None = None) -> dict:
    c = con()
    return {"etapas": list(crm.ESTADOS_MANUAIS), "leads": crm.funil(c, busca),
            "contagens": None if busca else crm.contagens_funil(c)}


class Valor(BaseModel):
    valor: float | None = None


@router.post("/leads/{empresa_id}/valor")
def valor(empresa_id: int, req: Valor) -> dict:
    crm.definir_valor(con(), empresa_id, req.valor)
    return {"ok": True}


class NovaAtividade(BaseModel):
    tipo: str = "nota"
    titulo: str = Field(min_length=1, max_length=200)
    texto: str | None = None
    vence_em: str | None = None                # 'AAAA-MM-DD HH:MM' ou 'AAAA-MM-DD'


@router.get("/leads/{empresa_id}/atividades")
def atividades(empresa_id: int) -> list[dict]:
    return crm.atividades(con(), empresa_id)


@router.post("/leads/{empresa_id}/atividades")
def criar_atividade(empresa_id: int, req: NovaAtividade) -> dict:
    vence = req.vence_em.replace("T", " ") if req.vence_em else None
    try:
        aid = crm.criar_atividade(con(), empresa_id, req.tipo, req.titulo, req.texto, vence)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(404, "empresa não encontrada") from exc
    return {"id": aid}


class Conclusao(BaseModel):
    desfazer: bool = False


@router.post("/atividades/{atividade_id}/concluir")
def concluir(atividade_id: int, req: Conclusao) -> dict:
    crm.concluir_atividade(con(), atividade_id, req.desfazer)
    return {"ok": True}


@router.delete("/atividades/{atividade_id}")
def excluir_atividade(atividade_id: int) -> dict:
    crm.excluir_atividade(con(), atividade_id)
    return {"ok": True}


@router.get("/tarefas")
def tarefas(todas: bool = False) -> list[dict]:
    return crm.tarefas(con(), todas)


# ==========================================================================
# CADENCIAS
# ==========================================================================
class Passo(BaseModel):
    canal: str
    espera_dias: int = Field(default=0, ge=0, le=60)
    assunto: str | None = None
    corpo: str


class CadenciaIn(BaseModel):
    nome: str = Field(min_length=1)
    descricao: str | None = None
    ativa: bool = True
    passos: list[Passo] = Field(min_length=1, max_length=3)


@router.get("/cadencias")
def cadencias() -> list[dict]:
    return crm.cadencias(con())


@router.get("/cadencias/variaveis")
def variaveis() -> dict:
    return crm.VARIAVEIS


@router.post("/cadencias")
def criar_cadencia(req: CadenciaIn) -> dict:
    try:
        cid = crm.salvar_cadencia(con(), req.nome, [p.model_dump() for p in req.passos],
                                  req.descricao, req.ativa)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": cid}


@router.get("/cadencias/{cadencia_id}")
def ver_cadencia(cadencia_id: int) -> dict:
    c = crm.cadencia(con(), cadencia_id)
    if c is None:
        raise HTTPException(404, "cadência não encontrada")
    return c


@router.put("/cadencias/{cadencia_id}")
def editar_cadencia(cadencia_id: int, req: CadenciaIn) -> dict:
    c = con()
    if crm.cadencia(c, cadencia_id) is None:
        raise HTTPException(404, "cadência não encontrada")
    try:
        crm.salvar_cadencia(c, req.nome, [p.model_dump() for p in req.passos],
                            req.descricao, req.ativa, cadencia_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": cadencia_id}


@router.delete("/cadencias/{cadencia_id}")
def excluir_cadencia(cadencia_id: int) -> dict:
    c = con()
    if db.um(c, "SELECT 1 FROM inscricao WHERE cadencia_id = ?", (cadencia_id,)):
        raise HTTPException(409, "cadência já tem empresas inscritas; pause em vez de excluir")
    c.execute("DELETE FROM cadencia WHERE id = ?", (cadencia_id,))
    c.commit()
    return {"ok": True}


@router.get("/cadencias/{cadencia_id}/previa")
def previa(cadencia_id: int, empresa_id: int) -> list[dict]:
    try:
        return crm.previa(con(), cadencia_id, empresa_id, carregar())
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


class Inscricao(Alvo):
    revelar_lista: bool = True                 # revela os itens da lista antes


@router.post("/cadencias/{cadencia_id}/inscrever")
def inscrever(cadencia_id: int, req: Inscricao) -> dict:
    c = con()
    if req.lista_id and req.revelar_lista:
        _base(crm.revelar, c, crm.itens_da_lista(c, req.lista_id))
    ids = _ids_do_alvo(c, req)
    if not ids:
        raise HTTPException(400, "nenhuma empresa para inscrever")
    try:
        return crm.inscrever(c, cadencia_id, ids, carregar())
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
