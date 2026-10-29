"""API HTTP: serve as paginas de prova (E8) e alimenta a interface web.

    uvicorn prospectai.api:app --reload

Duas responsabilidades distintas no mesmo processo, de proposito:
  - /a/{token}  e publico e visitado pelo LEAD. Registra a visita.
  - /api/*      e privado e consumido pela sua interface.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import crm, db, execucoes, outreach, psi
from .base_api import router as router_base
from .config import carregar
from .pipeline import _payload, _reconstruir, executar
from .proof import renderizar
from .ramos import listar, resolver
from .signals import detectar

app = FastAPI(title="ProspectAI", version="0.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"], allow_headers=["*"],
)
app.include_router(router_base)


def con() -> sqlite3.Connection:
    c = db.conectar()
    db.inicializar(c)
    return c


# ==========================================================================
# PUBLICO -- a pagina de prova que o lead abre
# ==========================================================================
@app.get("/a/{token}", response_class=HTMLResponse)
def pagina_prova(token: str, request: Request) -> HTMLResponse:
    c = con()
    visita = outreach.registrar_visita_prova(
        c, token, request.headers.get("user-agent")
    )
    if visita is None:
        raise HTTPException(404, "analise nao encontrada")

    linha = db.um(c, """
        SELECT e.*, a.id AS auditoria_id FROM empresa e
          JOIN auditoria a ON a.id = ?
         WHERE e.id = ?
    """, (visita["auditoria_id"], visita["empresa_id"]))
    if linha is None:
        raise HTTPException(404, "analise nao encontrada")

    aud_row = db.um(c, "SELECT * FROM auditoria WHERE id = ?", (visita["auditoria_id"],))
    auditoria, desc = _reconstruir(aud_row)
    sinais, _ = detectar(dict(linha), auditoria, desc, _payload(linha))

    cfg = carregar()
    html = renderizar(
        nome_empresa=linha["nome"], ramo=linha["ramo"], sinais=sinais,
        auditoria=auditoria, remetente_nome=cfg.remetente_nome,
        remetente_whatsapp=cfg.remetente_whatsapp,
        # So o site ACEITO aparece como alvo. `_reconstruir` ja devolveu
        # auditoria=None para dominio suspeito; a URL segue a mesma regra.
        site_url=desc.url if desc.confianca >= 2 else None, token=token,
    )
    return HTMLResponse(html, headers={"X-Robots-Tag": "noindex"})


@app.get("/a/{token}/nao-quero", response_class=HTMLResponse)
def opt_out_publico(token: str) -> HTMLResponse:
    """Opt-out em um clique. I2: terminal, imediato, sem confirmacao."""
    c = con()
    linha = db.um(c, "SELECT empresa_id FROM prova WHERE token = ?", (token,))
    if linha is None:
        raise HTTPException(404, "nao encontrado")
    outreach.aplicar_opt_out(c, linha["empresa_id"], "clique na pagina de analise")
    return HTMLResponse("""<!doctype html><html lang="pt-BR"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pronto</title><style>body{font:16px/1.6 system-ui;max-width:34rem;margin:20vh auto;
padding:0 1rem;color:#1a1a1a}@media(prefers-color-scheme:dark){body{background:#141414;color:#e8e8e8}}
</style></head><body><h1>Pronto.</h1>
<p>Voce nao vai receber mais nenhum contato nosso. Desculpe o incomodo.</p>
</body></html>""")


# ==========================================================================
# CAMPANHAS
# ==========================================================================
class NovaCampanha(BaseModel):
    cidade: str = Field(min_length=2)
    uf: str = Field(min_length=2, max_length=2)
    ramos: list[str] = Field(min_length=1)
    raio_km: float = 0
    limite_fila: int = 30
    medir_pagespeed: bool = True
    fonte: str = "auto"             # auto (Places ou Overture) | base | overture | osm


def _rodar(execucao_id: str, req: NovaCampanha) -> None:
    progresso = execucoes.progresso_de(execucao_id)
    c = con()
    try:
        resumo = executar(
            cidade=req.cidade, uf=req.uf, chaves_ramos=req.ramos,
            raio_km=req.raio_km, cfg=carregar(), con=c,
            limite_fila=req.limite_fila, progresso=progresso, fonte=req.fonte,
        )
        if req.medir_pagespeed:
            execucoes.atualizar(execucao_id, etapa="pagespeed", feito=0, total=1)
            resumo["pagespeed"] = psi.enriquecer_campanha(c, resumo["campanha_id"])
            # PSI muda os sinais de dor, entao o score precisa ser refeito.
            from .pipeline import pontuar, preparar_abordagem
            resumo["score"] = pontuar(c, resumo["campanha_id"])
            resumo["fila"] = preparar_abordagem(
                c, resumo["campanha_id"], carregar(), req.limite_fila
            )
        execucoes.atualizar(execucao_id, estado="concluida", resumo=resumo)
    except Exception as exc:
        execucoes.atualizar(execucao_id, estado="erro", erro=f"{type(exc).__name__}: {exc}")
    finally:
        c.close()


@app.post("/api/campanhas")
def criar_campanha(req: NovaCampanha, tarefas: BackgroundTasks) -> dict:
    for chave in req.ramos:
        if resolver(chave) is None:
            raise HTTPException(400, f"ramo desconhecido: {chave}")
    if req.fonte not in ("auto", "base", "overture", "osm"):
        raise HTTPException(400, f"fonte invalida: {req.fonte}")
    execucao_id = execucoes.nova("campanha")
    tarefas.add_task(_rodar, execucao_id, req)
    return {"execucao_id": execucao_id}


@app.get("/api/execucoes/{execucao_id}")
def ver_execucao(execucao_id: str) -> dict:
    est = execucoes.ver(execucao_id)
    if est is None:
        raise HTTPException(404, "execucao nao encontrada")
    return est


@app.get("/api/campanhas")
def listar_campanhas() -> list[dict]:
    linhas = db.buscar(con(), """
        SELECT c.*,
               (SELECT COUNT(*) FROM campanha_empresa ce WHERE ce.campanha_id = c.id) AS empresas,
               (SELECT COUNT(DISTINCT ce.empresa_id) FROM campanha_empresa ce
                  JOIN mensagem m ON m.empresa_id = ce.empresa_id
                 WHERE ce.campanha_id = c.id) AS com_mensagem
          FROM campanha c ORDER BY c.id DESC
    """)
    return [dict(l) | {"ramos": json.loads(l["ramos"])} for l in linhas]


# ==========================================================================
# LEADS
# ==========================================================================
@app.get("/api/campanhas/{campanha_id}/leads")
def leads(campanha_id: int, faixa: str | None = None, limite: int = 200) -> list[dict]:
    linhas = db.buscar(con(), """
        SELECT e.id, e.nome, e.ramo, e.endereco, e.bairro, e.telefone,
               s.total, s.faixa, s.dor, s.dinheiro, s.timing,
               a.site_url, a.site_encontrado_como, a.site_confianca,
               a.psi_mobile_score, a.erro AS auditoria_erro,
               l.estado AS lead_estado,
               (SELECT GROUP_CONCAT(tipo, '|') FROM sinal
                 WHERE empresa_id = e.id AND eixo='dor') AS dores,
               (SELECT COUNT(*) FROM mensagem m WHERE m.empresa_id = e.id) AS msgs,
               (SELECT primeira_visita_em FROM prova p WHERE p.empresa_id = e.id
                 ORDER BY id DESC LIMIT 1) AS prova_aberta,
               (SELECT MAX(opt_out) FROM contato c WHERE c.empresa_id = e.id) AS opt_out
          FROM empresa e
          JOIN score s ON s.id = (SELECT id FROM score WHERE empresa_id = e.id
                                   ORDER BY id DESC LIMIT 1)
          JOIN auditoria a ON a.id = s.auditoria_id
     LEFT JOIN lead l ON l.empresa_id = e.id
          JOIN campanha_empresa ce ON ce.empresa_id = e.id
         WHERE ce.campanha_id = ? AND (? IS NULL OR s.faixa = ?)
      ORDER BY s.total DESC LIMIT ?
    """, (campanha_id, faixa, faixa, limite))
    return [
        dict(l) | {"dores": (l["dores"] or "").split("|") if l["dores"] else []}
        for l in linhas
    ]


@app.get("/api/leads/{empresa_id}")
def lead(empresa_id: int) -> dict:
    c = con()
    e = db.um(c, "SELECT * FROM empresa WHERE id = ?", (empresa_id,))
    if e is None:
        raise HTTPException(404, "lead nao encontrado")

    aud = db.um(c, "SELECT * FROM auditoria WHERE empresa_id = ? "
                   "ORDER BY coletado_em DESC, id DESC LIMIT 1", (empresa_id,))
    sc = db.um(c, "SELECT * FROM score WHERE empresa_id = ? ORDER BY id DESC LIMIT 1",
               (empresa_id,))
    sinais = db.buscar(c, "SELECT * FROM sinal WHERE empresa_id = ? ORDER BY eixo, peso DESC",
                       (empresa_id,))
    msgs = db.buscar(c, "SELECT * FROM mensagem WHERE empresa_id = ? ORDER BY toque",
                     (empresa_id,))
    contatos = db.buscar(c, "SELECT * FROM contato WHERE empresa_id = ?", (empresa_id,))
    respostas = db.buscar(c, "SELECT * FROM resposta WHERE empresa_id = ? "
                             "ORDER BY recebida_em DESC", (empresa_id,))
    prova = db.um(c, "SELECT * FROM prova WHERE empresa_id = ? ORDER BY id DESC LIMIT 1",
                  (empresa_id,))
    lead_row = db.um(c, "SELECT * FROM lead WHERE empresa_id = ?", (empresa_id,))
    inscricoes = db.buscar(c, """
        SELECT i.*, cd.nome AS cadencia FROM inscricao i
          JOIN cadencia cd ON cd.id = i.cadencia_id
         WHERE i.empresa_id = ? ORDER BY i.inscrita_em DESC""", (empresa_id,))
    cfg = carregar()

    return {
        "empresa": dict(e),
        "auditoria": dict(aud) if aud else None,
        "score": dict(sc) if sc else None,
        "sinais": [dict(s) | {"evidencia": json.loads(s["evidencia"])
                              if s["evidencia"] else None} for s in sinais],
        "mensagens": [dict(m) for m in msgs],
        "contatos": [dict(x) for x in contatos],
        "respostas": [dict(r) for r in respostas],
        "prova": (dict(prova) | {"url": f"{cfg.base_url}/a/{prova['token']}"})
                 if prova else None,
        "lead": dict(lead_row) if lead_row else None,
        "atividades": crm.atividades(c, empresa_id),
        "inscricoes": [dict(i) for i in inscricoes],
    }


# ==========================================================================
# FILA DE ENVIO E ACOES
# ==========================================================================
@app.get("/api/fila")
def ver_fila(limite: int = 30) -> dict:
    c = con()
    itens = outreach.fila(c, limite)
    return {
        "itens": itens,
        "envio_habilitado": (db.estado_sistema(c, "envio_habilitado") or "false") == "true",
        "pausa_motivo": db.estado_sistema(c, "pausa_motivo"),
        "limite_dia": outreach.limite_do_dia(c),
        "enviados_hoje": outreach.enviados_hoje(c),
        "dentro_da_janela": outreach.dentro_da_janela(),
        "circuit_breaker": outreach.circuit_breakers(c).motivo,
    }


class MarcarEnviada(BaseModel):
    forcar: bool = True          # estrada manual: voce ja apertou enviar
    provider_id: str | None = None


@app.post("/api/mensagens/{mensagem_id}/enviada")
def marcar_enviada(mensagem_id: int, req: MarcarEnviada) -> dict:
    p = outreach.marcar_enviada(con(), mensagem_id, req.provider_id, req.forcar)
    if not p.ok:
        raise HTTPException(409, p.motivo or "envio bloqueado")
    return {"ok": True}


class NovaResposta(BaseModel):
    texto: str = Field(min_length=1)
    mensagem_id: int | None = None


@app.post("/api/leads/{empresa_id}/resposta")
def registrar_resposta(empresa_id: int, req: NovaResposta) -> dict:
    cl = outreach.registrar_resposta(con(), empresa_id, req.texto, req.mensagem_id)
    return {"classe": cl.classe, "confianca": cl.confianca,
            "por": cl.por, "trecho": cl.trecho}


@app.post("/api/leads/{empresa_id}/opt-out")
def opt_out(empresa_id: int) -> dict:
    outreach.aplicar_opt_out(con(), empresa_id, "marcado manualmente")
    return {"ok": True}


class Desfecho(BaseModel):
    estado: str
    valor: float | None = None
    motivo_perda: str | None = None


@app.post("/api/leads/{empresa_id}/estado")
def mudar_estado(empresa_id: int, req: Desfecho) -> dict:
    """Etapa movida na mao (funil). Cancela os toques agendados quando voce
    assume a conversa -- antes, o follow-up automatico seguia saindo."""
    try:
        crm.mudar_estado(con(), empresa_id, req.estado, req.valor, req.motivo_perda)
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc                  # I2
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


# ==========================================================================
# PAINEL
# ==========================================================================
@app.get("/api/painel")
def painel() -> dict:
    c = con()
    def n(sql: str, p: tuple = ()) -> int:
        linha = db.um(c, sql, p)
        return int(linha["n"]) if linha else 0

    return {
        "empresas": n("SELECT COUNT(*) AS n FROM empresa"),
        "sem_site": n("SELECT COUNT(DISTINCT empresa_id) AS n FROM sinal WHERE tipo='SEM_SITE'"),
        "faixa_a": n("SELECT COUNT(*) AS n FROM score s WHERE s.faixa='A' "
                     "AND s.id=(SELECT id FROM score WHERE empresa_id=s.empresa_id ORDER BY id DESC LIMIT 1)"),
        "faixa_b": n("SELECT COUNT(*) AS n FROM score s WHERE s.faixa='B' "
                     "AND s.id=(SELECT id FROM score WHERE empresa_id=s.empresa_id ORDER BY id DESC LIMIT 1)"),
        "enviadas": n("SELECT COUNT(*) AS n FROM mensagem WHERE enviada_em IS NOT NULL"),
        "respostas": n("SELECT COUNT(*) AS n FROM resposta"),
        "provas_abertas": n("SELECT COUNT(*) AS n FROM prova WHERE primeira_visita_em IS NOT NULL"),
        "opt_outs": n("SELECT COUNT(DISTINCT empresa_id) AS n FROM contato WHERE opt_out=1"),
        "em_conversa": n("SELECT COUNT(*) AS n FROM lead WHERE estado='EM_CONVERSA'"),
        "fechados": n("SELECT COUNT(*) AS n FROM lead WHERE estado='FECHADO'"),
        "reveladas": n("SELECT COUNT(*) AS n FROM empresa WHERE revelada_em IS NOT NULL"),
        "tarefas_vencidas": n("SELECT COUNT(*) AS n FROM atividade WHERE concluida_em IS NULL "
                              "AND vence_em IS NOT NULL AND vence_em < datetime('now')"),
        "em_cadencia": n("SELECT COUNT(*) AS n FROM inscricao WHERE estado = 'ATIVA'"),
        "alertas": outreach.alertas(c),
        "config": {"tem_places": carregar().tem_places},
    }


@app.get("/api/ramos")
def ramos() -> list[dict]:
    return [{"chave": r.chave, "rotulo": r.rotulo, "oferta": r.oferta,
             "gancho": r.gancho, "ticket": list(r.ticket)} for r in listar()]


class Interruptor(BaseModel):
    habilitado: bool
    motivo: str | None = None


@app.post("/api/sistema/envio")
def alternar_envio(req: Interruptor) -> dict:
    """I6: o kill switch. Lido do banco antes de cada envio individual."""
    c = con()
    db.definir_estado(c, "envio_habilitado", "true" if req.habilitado else "false")
    db.definir_estado(c, "pausa_motivo", req.motivo or
                      ("" if req.habilitado else "pausado manualmente"))
    return {"envio_habilitado": req.habilitado}


@app.get("/")
def raiz() -> RedirectResponse:
    return RedirectResponse("/docs")
