"""Orquestracao da cadeia E0 -> E9, com persistencia.

Cada etapa grava o que produziu antes da proxima comecar. Se a rede cair no
meio, o que ja foi medido continua no banco -- rodar de novo retoma em vez de
recomecar (a coleta e idempotente por (fonte, fonte_id)).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from typing import Any, Callable

from . import db, score as mod_score
from .audit import Auditoria
from .config import Config
from .discover import Descoberta, descobrir_muitos, eh_rede_nacional
from .messages import Remetente, gerar_sequencia
from .normalize import eh_movel, normalizar_nome, normalizar_telefone
from .proof import novo_token
from .ramos import Ramo, resolver
from .signals import detectar
from .sources import overpass

Progresso = Callable[[str, int, int], None]


def _nada(etapa: str, feito: int, total: int) -> None:
    pass


def _payload(empresa_row: sqlite3.Row | dict) -> dict:
    bruto = empresa_row["fonte_payload"] if "fonte_payload" in empresa_row.keys() else None
    if not bruto:
        return {}
    try:
        return json.loads(bruto) if isinstance(bruto, str) else dict(bruto)
    except (json.JSONDecodeError, TypeError):
        return {}


def coletar(
    con: sqlite3.Connection,
    cidade: str,
    uf: str,
    ramos: list[Ramo],
    raio_km: float,
    cfg: Config,
    progresso: Progresso = _nada,
    fonte: str = "auto",
    partes_receita: int = 10,
) -> tuple[int, dict[str, Any]]:
    """E1/E2 -- semente. Devolve (campanha_id, estatisticas)."""
    progresso("coleta", 0, 1)

    fonte_usada = fonte
    if fonte == "base":
        brutas = _coletar_da_base(cidade, uf, ramos)
    elif fonte == "receita":
        from pathlib import Path as _Path

        from .sources import receita
        brutas = receita.coletar(
            cidade, uf, ramos, cache=_Path(db.RAIZ) / "dados" / "receita_cache",
            progresso=progresso, partes=partes_receita,
        )
    elif fonte == "places" or (fonte == "auto" and cfg.tem_places):
        from .sources import places
        local = overpass.geocodificar(cidade, uf)
        if local is None:
            raise RuntimeError(f"Nao localizei {cidade}/{uf}")
        brutas = places.coletar(
            cidade, uf, local.lat, local.lng, ramos,
            raio_km=raio_km or 8.0, con=con,
        )
        fonte_usada = "places"
    elif fonte == "osm":
        _, brutas = overpass.coletar(cidade, uf, ramos, raio_km=raio_km)
    else:
        # Sem chave do Google, o padrao e o Overture: gratis, sem cadastro e,
        # medido em Campos, 86% com telefone contra 16% do OpenStreetMap.
        from .sources import overture
        brutas = overture.coletar(cidade, uf, ramos, raio_km=raio_km)
        fonte_usada = "overture"

    campanha_id = db.inserir(con, "campanha", {
        "nome": f"{cidade}/{uf} - {', '.join(r.chave for r in ramos)}",
        "cidade": cidade, "uf": uf, "raio_km": raio_km,
        "ramos": [r.chave for r in ramos],
    })

    redes = 0
    inseridas = 0
    for e in brutas:
        if eh_rede_nacional(e["nome"]):
            redes += 1                     # franquia nacional nao e lead local
            continue
        e["campanha_id"] = campanha_id
        empresa_id = db.upsert_empresa(con, e)
        inseridas += 1
        con.execute(
            "INSERT OR IGNORE INTO campanha_empresa (campanha_id, empresa_id) "
            "VALUES (?, ?)", (campanha_id, empresa_id),
        )
        con.execute(
            "INSERT OR IGNORE INTO lead (empresa_id, estado) VALUES (?, 'NOVO')",
            (empresa_id,),
        )
        if e.get("telefone"):
            con.execute(
                "INSERT OR IGNORE INTO contato (empresa_id, canal, valor, origem, eh_movel) "
                "VALUES (?, 'whatsapp', ?, ?, ?)",
                (empresa_id, e["telefone"], e.get("telefone_origem"),
                 int(eh_movel(e["telefone"]))),
            )
    con.commit()
    progresso("coleta", 1, 1)

    return campanha_id, {
        "fonte": fonte_usada,
        "brutas": len(brutas),
        "redes_filtradas": redes,
        "inseridas": inseridas,
    }


def _coletar_da_base(cidade: str, uf: str, ramos: list[Ramo]) -> list[dict[str, Any]]:
    """Semente E1 a partir da base importada (base.py): instantaneo, sem download."""
    from . import base
    from .crm import _empresa_de_registro

    municipios = [m for m in base.opcoes_municipio(cidade, [uf])
                  if normalizar_nome(m["nome"]) == normalizar_nome(cidade)]
    if not municipios:
        raise RuntimeError(f"{cidade}/{uf} nao esta na base de empresas.")
    prefixos = [p for r in ramos for p in r.cnaes]
    if not prefixos:
        raise RuntimeError("Nenhum dos ramos escolhidos tem CNAE mapeado.")
    filtros = base.Filtros(municipios=[municipios[0]["codigo"]], cnaes=prefixos)
    return [_empresa_de_registro(r)
            for r in base.registros(base.cnpjs(filtros, limite=8000)).values()]


def auditar_campanha(
    con: sqlite3.Connection, campanha_id: int, progresso: Progresso = _nada
) -> dict[str, Any]:
    """E4 + E5 -- descoberta de site e auditoria tecnica."""
    linhas = db.buscar(
        con, "SELECT e.* FROM empresa e JOIN campanha_empresa ce ON ce.empresa_id = e.id "
             "WHERE ce.campanha_id = ? ORDER BY e.id", (campanha_id,)
    )
    empresas = [dict(l) for l in linhas]
    if not empresas:
        return {"auditadas": 0}

    progresso("auditoria", 0, len(empresas))
    # Empresa de demonstracao tem nome ficticio: chutar dominio com ele so
    # acertaria o site de alguma empresa real parecida. Fica sem rede.
    reais = [(i, e) for i, e in enumerate(empresas) if e.get("fonte") != "demo"]
    achados = asyncio.run(descobrir_muitos(
        [e for _, e in reais], progresso=lambda f, tot: progresso("auditoria", f, tot)
    ))
    resultados = {reais[j][0]: r for j, r in achados.items()}

    stats = {"aceitos": 0, "suspeitos": 0, "sem_site": 0, "perfil": 0, "erros": 0}
    for i, e in enumerate(empresas):
        d, a = resultados.get(i, (Descoberta(), None))
        _gravar_auditoria(con, e["id"], d, a)

        if d.confianca >= 2:
            stats["aceitos"] += 1
        elif d.como == "suspeito":
            stats["suspeitos"] += 1
        else:
            stats["sem_site"] += 1
        if d.agregador:
            stats["perfil"] += 1
        if a and a.erro:
            stats["erros"] += 1

        # Telefone recuperado do proprio site: resgata lead que a fonte trouxe
        # sem contato (metade da base, no modo OSM).
        # So do site ACEITO -- telefone tirado de dominio suspeito e telefone
        # de outra empresa, e mandar mensagem para ele e o pior erro possivel.
        if a and a.telefones_no_site and not e.get("telefone") and d.confianca >= 2:
            _gravar_telefone_do_site(con, e["id"], a.telefones_no_site)

        progresso("auditoria", i + 1, len(empresas))

    con.commit()
    stats["auditadas"] = len(empresas)
    return stats


def _gravar_auditoria(
    con: sqlite3.Connection, empresa_id: int, d: Descoberta, a: Auditoria | None
) -> int:
    dados: dict[str, Any] = {
        "empresa_id": empresa_id,
        "site_url": d.url,
        "site_encontrado_como": d.como,
        "site_confianca": d.confianca,
        "site_provas": d.provas,
    }
    if a is not None:
        bruto = asdict(a)
        # Campos de trabalho da auditoria: servem a prova de posse (E4) e a
        # recuperacao de telefone, mas nao sao colunas da tabela.
        for descartar in ("texto_norm", "digitos_pagina", "telefones_no_site",
                          "emails_no_site", "site_url"):
            bruto.pop(descartar, None)
        for k, v in bruto.items():
            dados[k] = int(v) if isinstance(v, bool) else v
        dados["tem_link_social"] = a.tem_link_social or None
    return db.inserir(con, "auditoria", dados)


def _gravar_telefone_do_site(
    con: sqlite3.Connection, empresa_id: int, telefones: list[str]
) -> None:
    for bruto in telefones:
        e164 = normalizar_telefone(bruto)
        if e164 and eh_movel(e164):          # so celular: fixo nao tem WhatsApp
            con.execute(
                "INSERT OR IGNORE INTO contato (empresa_id, canal, valor, origem, eh_movel) "
                "VALUES (?, 'whatsapp', ?, 'site', 1)",
                (empresa_id, e164),
            )
            con.execute("UPDATE empresa SET telefone = COALESCE(telefone, ?), "
                        "telefone_origem = COALESCE(telefone_origem, 'site') WHERE id = ?",
                        (e164, empresa_id))
            return


def pontuar(
    con: sqlite3.Connection, campanha_id: int, progresso: Progresso = _nada
) -> dict[str, Any]:
    """E6 -- sinais e score, sobre a auditoria mais recente de cada empresa."""
    linhas = db.buscar(con, """
        SELECT e.*, a.id AS auditoria_id
          FROM empresa e
          JOIN campanha_empresa ce ON ce.empresa_id = e.id
          JOIN auditoria a ON a.id = (
                SELECT id FROM auditoria WHERE empresa_id = e.id
                 ORDER BY coletado_em DESC, id DESC LIMIT 1)
         WHERE ce.campanha_id = ?
    """, (campanha_id,))
    return _pontuar_linhas(con, linhas, progresso)


def pontuar_empresas(
    con: sqlite3.Connection, empresa_ids: list[int], progresso: Progresso = _nada
) -> dict[str, Any]:
    """O mesmo E6, para empresas reveladas da base (sem campanha)."""
    if not empresa_ids:
        return {"pontuadas": 0, "faixas": {"A": 0, "B": 0, "C": 0, "D": 0}}
    marcas = ", ".join("?" for _ in empresa_ids)
    linhas = db.buscar(con, f"""
        SELECT e.*, a.id AS auditoria_id
          FROM empresa e
          JOIN auditoria a ON a.id = (
                SELECT id FROM auditoria WHERE empresa_id = e.id
                 ORDER BY coletado_em DESC, id DESC LIMIT 1)
         WHERE e.id IN ({marcas})
    """, empresa_ids)
    return _pontuar_linhas(con, linhas, progresso)


def _pontuar_linhas(
    con: sqlite3.Connection, linhas: list[sqlite3.Row], progresso: Progresso = _nada
) -> dict[str, Any]:
    faixas = {"A": 0, "B": 0, "C": 0, "D": 0}
    for i, linha in enumerate(linhas):
        e = dict(linha)
        aud_row = db.um(con, "SELECT * FROM auditoria WHERE id = ?", (e["auditoria_id"],))
        a, d = _reconstruir(aud_row)

        sinais, dinheiro_medido = detectar(e, a, d, _payload(linha))
        r = mod_score.calcular(sinais, dinheiro_medido)

        con.execute("DELETE FROM sinal WHERE empresa_id = ?", (e["id"],))
        for s in sinais:
            db.inserir(con, "sinal", {
                "empresa_id": e["id"], "auditoria_id": e["auditoria_id"],
                "tipo": s.tipo, "eixo": s.eixo, "valor": s.valor,
                "evidencia": s.evidencia, "peso": s.peso,
            })
        db.inserir(con, "score", {
            "empresa_id": e["id"], "auditoria_id": e["auditoria_id"],
            "dor": r.dor, "dinheiro": r.dinheiro, "timing": r.timing,
            "total": r.total, "faixa": r.faixa, "versao_modelo": r.versao_modelo,
        })
        faixas[r.faixa] += 1
        progresso("score", i + 1, len(linhas))

    con.commit()
    return {"pontuadas": len(linhas), "faixas": faixas}


def _reconstruir(row: sqlite3.Row | None) -> tuple[Auditoria | None, Descoberta]:
    """Volta da linha do banco para os objetos que o resto do sistema consome.

    PONTO UNICO DE ESTRANGULAMENTO da invariante I1. Se o dominio nao foi
    aceito (confianca < 2), a medicao foi feita no site de OUTRA empresa e a
    auditoria NAO sai daqui -- nem para os sinais, nem para o score, nem para o
    laudo. Sem isso o laudo dizia "site nao encontrado" e, tres linhas abaixo,
    "certificado valido" do dominio suspeito.

    Os dados continuam gravados na tabela (servem a revisao humana); o que este
    portao controla e o que pode ser USADO.
    """
    if row is None:
        return None, Descoberta()
    r = dict(row)
    provas = json.loads(r["site_provas"]) if r.get("site_provas") else []
    d = Descoberta(
        url=r.get("site_url"), como=r.get("site_encontrado_como") or "nao_encontrado",
        confianca=r.get("site_confianca") or 0, provas=provas,
    )
    if d.confianca < 2:
        return None, d

    def b(chave: str) -> bool | None:
        v = r.get(chave)
        return None if v is None else bool(v)

    a = Auditoria(
        site_url=r.get("site_url"), http_status=r.get("http_status"),
        tempo_resposta_ms=r.get("tempo_resposta_ms"), tls_valido=b("tls_valido"),
        tls_expira_em=r.get("tls_expira_em"), tls_dias_restantes=r.get("tls_dias_restantes"),
        tem_viewport=b("tem_viewport"), tem_whatsapp=b("tem_whatsapp"),
        tem_analytics=b("tem_analytics"), tem_pixel=b("tem_pixel"),
        plataforma=r.get("plataforma"), plano_gratuito=b("plano_gratuito"),
        ano_copyright=r.get("ano_copyright"), titulo=r.get("titulo"),
        titulo_generico=b("titulo_generico"), cardapio_em_pdf=b("cardapio_em_pdf"),
        html_bytes=r.get("html_bytes"), imagens_sem_lazy=r.get("imagens_sem_lazy"),
        psi_mobile_score=r.get("psi_mobile_score"), psi_lcp_ms=r.get("psi_lcp_ms"),
        erro=r.get("erro"),
    )
    return a, d


def preparar_abordagem(
    con: sqlite3.Connection, campanha_id: int, cfg: Config, limite: int = 30
) -> dict[str, Any]:
    """E7 + E8 + E9 -- fila do dia, prova hospedada e os tres toques.

    I2: opt-out verificado aqui (primeira das tres verificacoes).
    """
    remetente = Remetente(
        nome=cfg.remetente_nome, cidade=cfg.remetente_cidade,
        whatsapp=cfg.remetente_whatsapp, profissao=cfg.remetente_profissao,
    )

    linhas = db.buscar(con, """
        SELECT e.*, s.total, s.faixa, s.id AS score_id, a.id AS auditoria_id,
               c.id AS contato_id, c.valor AS contato_valor
          FROM empresa e
          JOIN score s ON s.id = (SELECT id FROM score WHERE empresa_id = e.id
                                   ORDER BY calculado_em DESC, id DESC LIMIT 1)
          JOIN auditoria a ON a.id = s.auditoria_id
          JOIN campanha_empresa ce ON ce.empresa_id = e.id
     LEFT JOIN contato c ON c.empresa_id = e.id AND c.canal = 'whatsapp'
                        AND c.opt_out = 0 AND c.eh_movel = 1
         WHERE ce.campanha_id = ?
           AND s.faixa IN ('A','B')
           AND a.erro IS NULL
           AND NOT EXISTS (SELECT 1 FROM contato x WHERE x.empresa_id = e.id
                                                     AND x.opt_out = 1)
           AND NOT EXISTS (SELECT 1 FROM mensagem m WHERE m.empresa_id = e.id)
      GROUP BY e.id
      ORDER BY s.total DESC
    """, (campanha_id,))

    preparados = sem_contato = 0
    por_ramo: dict[str, int] = {}

    for linha in linhas:
        if preparados >= limite:
            break
        e = dict(linha)
        # Diversificacao: no maximo 3 do mesmo ramo por lote (E7).
        ramo = e.get("ramo") or "?"
        if por_ramo.get(ramo, 0) >= 3:
            continue
        if not e.get("contato_valor"):
            sem_contato += 1
            continue

        aud_row = db.um(con, "SELECT * FROM auditoria WHERE id = ?", (e["auditoria_id"],))
        a, d = _reconstruir(aud_row)
        sinais, _ = detectar(e, a, d, _payload(linha))

        token = novo_token()
        db.inserir(con, "prova", {
            "empresa_id": e["id"], "auditoria_id": e["auditoria_id"], "token": token,
        })
        prova_url = f"{cfg.base_url}/a/{token}"

        for msg in gerar_sequencia(e["nome"], e.get("ramo"), sinais, remetente, prova_url):
            if not msg.texto:
                continue
            try:
                db.inserir(con, "mensagem", {
                    "empresa_id": e["id"], "contato_id": e.get("contato_id"),
                    "template_id": msg.template_id, "toque": msg.toque,
                    "texto": msg.texto, "fatos_usados": msg.fatos_usados,
                    "validador_ok": int(msg.validador_ok),
                    "validador_erros": msg.validador_erros or None,
                    "gerada_por": msg.gerada_por, "estado": "AGENDADA",
                })
            except sqlite3.IntegrityError:
                pass                       # I3: ja existe mensagem deste template

        con.execute(
            "UPDATE lead SET estado='SELECIONADO', estado_desde=datetime('now') "
            "WHERE empresa_id = ?", (e["id"],)
        )
        por_ramo[ramo] = por_ramo.get(ramo, 0) + 1
        preparados += 1

    con.commit()
    return {"preparados": preparados, "sem_contato": sem_contato,
            "elegiveis": len(linhas)}


def executar(
    cidade: str, uf: str, chaves_ramos: list[str], raio_km: float, cfg: Config,
    con: sqlite3.Connection, limite_fila: int = 30, progresso: Progresso = _nada,
    fonte: str = "auto", partes_receita: int = 10,
) -> dict[str, Any]:
    """A cadeia inteira, de ponta a ponta."""
    ramos = [r for r in (resolver(c) for c in chaves_ramos) if r]
    if not ramos:
        raise ValueError(f"Nenhum ramo valido em {chaves_ramos}")

    inicio = datetime.now()
    campanha_id, st_coleta = coletar(
        con, cidade, uf, ramos, raio_km, cfg, progresso, fonte, partes_receita
    )
    st_audit = auditar_campanha(con, campanha_id, progresso)
    st_score = pontuar(con, campanha_id, progresso)
    st_fila = preparar_abordagem(con, campanha_id, cfg, limite_fila)

    return {
        "campanha_id": campanha_id,
        "cidade": f"{cidade}/{uf}",
        "ramos": [r.chave for r in ramos],
        "coleta": st_coleta,
        "auditoria": st_audit,
        "score": st_score,
        "fila": st_fila,
        "segundos": round((datetime.now() - inicio).total_seconds(), 1),
    }
