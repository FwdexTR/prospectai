"""E5 (parte 2) -- PageSpeed Insights.

Separado do auditor porque e lento (10 a 30s por site) e depende de servico
externo. Roda em passo proprio, com cache de 30 dias, e o resultado entra na
auditoria mais recente.

Funciona sem chave em volume baixo; com chave (gratuita) a cota sobe muito.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime
from typing import Any

import httpx

ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


async def medir(url: str, cli: httpx.AsyncClient) -> dict[str, Any] | None:
    params = {
        "url": url,
        "strategy": "mobile",
        "category": "performance",
        "locale": "pt_BR",
    }
    chave = os.environ.get("PAGESPEED_API_KEY", "").strip()
    if chave:
        params["key"] = chave

    try:
        resp = await cli.get(ENDPOINT, params=params, timeout=90.0)
        if resp.status_code == 429:
            return None                       # cota estourada: nao inventa numero
        resp.raise_for_status()
        dados = resp.json()
    except (httpx.HTTPError, ValueError):
        return None

    lh = dados.get("lighthouseResult") or {}
    cats = lh.get("categories") or {}
    audits = lh.get("audits") or {}

    perf = (cats.get("performance") or {}).get("score")
    lcp = (audits.get("largest-contentful-paint") or {}).get("numericValue")
    cls = (audits.get("cumulative-layout-shift") or {}).get("numericValue")

    if perf is None:
        return None
    return {
        "psi_mobile_score": int(round(perf * 100)),
        "psi_lcp_ms": int(lcp) if lcp is not None else None,
        "psi_cls": round(cls, 3) if cls is not None else None,
        "psi_coletado_em": datetime.now().isoformat(timespec="seconds"),
    }


async def medir_muitos(urls: list[str], concorrencia: int = 3) -> dict[str, dict]:
    """Concorrencia baixa de proposito: a API do PSI e pesada e rate-limita."""
    limite = asyncio.Semaphore(concorrencia)
    async with httpx.AsyncClient() as cli:
        async def uma(u: str):
            async with limite:
                return u, await medir(u, cli)
        pares = await asyncio.gather(*(uma(u) for u in urls), return_exceptions=True)
    return {u: r for u, r in (p for p in pares if isinstance(p, tuple)) if r}


def enriquecer_campanha(con: sqlite3.Connection, campanha_id: int) -> dict[str, int]:
    """Mede PSI dos sites ACEITOS da campanha e grava na auditoria mais recente.

    So sites aceitos (confianca >= 2): medir velocidade de dominio suspeito
    seria medir o site de outra empresa.
    """
    linhas = con.execute("""
        SELECT a.id, a.site_url FROM auditoria a
          JOIN campanha_empresa ce ON ce.empresa_id = a.empresa_id
         WHERE ce.campanha_id = ? AND a.site_confianca >= 2
           AND a.site_url IS NOT NULL AND a.erro IS NULL
           AND a.psi_coletado_em IS NULL
      GROUP BY a.empresa_id
    """, (campanha_id,)).fetchall()

    if not linhas:
        return {"medidos": 0, "falhas": 0}

    urls = [l["site_url"] for l in linhas]
    resultados = asyncio.run(medir_muitos(urls))

    medidos = 0
    for l in linhas:
        r = resultados.get(l["site_url"])
        if not r:
            continue
        con.execute(
            "UPDATE auditoria SET psi_mobile_score=?, psi_lcp_ms=?, psi_cls=?, "
            "psi_coletado_em=? WHERE id=?",
            (r["psi_mobile_score"], r["psi_lcp_ms"], r["psi_cls"],
             r["psi_coletado_em"], l["id"]),
        )
        medidos += 1
    con.commit()
    return {"medidos": medidos, "falhas": len(linhas) - medidos}
