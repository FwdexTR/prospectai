"""E2 -- coleta pela Google Places API (New).

Por que existe: o OpenStreetMap nao tem telefone nem contagem de avaliacoes na
maior parte do interior brasileiro (medido em Campos/RJ: 16% com telefone).
Sem telefone nao ha WhatsApp, e sem numero de avaliacoes nao ha eixo dinheiro --
ou seja, sem esta fonte o sistema vira uma lista de nomes.

TERMOS DE USO (E2 da spec, e nao e detalhe):
  A Places API restringe montar banco permanente do conteudo dela. O desenho
  aqui respeita isso:
    - `empresa` guarda o place_id (permitido) e os SINAIS DERIVADOS que nos
      calculamos ("tem_muitos_reviews", "sem_website").
    - o conteudo bruto vive em `places_cache` com TTL de 30 dias e e
      re-consultado quando expira.
  Isso tambem te obriga a manter o dado fresco, o que e bom de qualquer jeito.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from typing import Any, Iterable

import httpx

from ..normalize import normalizar_nome, normalizar_telefone
from ..ramos import Ramo

BASE = "https://places.googleapis.com/v1/places"
TTL_CACHE_DIAS = 30

CAMPOS = ",".join(
    "places." + c for c in (
        "id", "displayName", "formattedAddress", "location", "primaryType",
        "types", "nationalPhoneNumber", "internationalPhoneNumber",
        "websiteUri", "rating", "userRatingCount", "businessStatus",
        "regularOpeningHours", "photos", "addressComponents",
    )
)

SQL_CACHE = """
CREATE TABLE IF NOT EXISTS places_cache (
    place_id   TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    buscado_em TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class SemChave(RuntimeError):
    pass


def chave() -> str:
    k = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    if not k:
        raise SemChave(
            "GOOGLE_PLACES_API_KEY nao definida. "
            "Crie a chave no Google Cloud (Places API New) e coloque em prospectai/.env"
        )
    return k


def preparar_cache(con: sqlite3.Connection) -> None:
    con.executescript(SQL_CACHE)
    con.commit()


def _grid(lat: float, lng: float, raio_km: float, passo_km: float = 1.5) -> list[tuple[float, float]]:
    """Divide a area em celulas -- searchNearby devolve no maximo 20 por chamada,
    entao varrer uma cidade exige grid (E2 da spec)."""
    if raio_km <= 0:
        raio_km = 5.0
    graus_lat = passo_km / 111.0
    graus_lng = passo_km / (111.0 * max(math.cos(math.radians(lat)), 0.1))
    n = max(int(raio_km / passo_km), 1)
    pontos = []
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            plat, plng = lat + i * graus_lat, lng + j * graus_lng
            dist = math.hypot(i * passo_km, j * passo_km)
            if dist <= raio_km:
                pontos.append((plat, plng))
    return pontos


def _buscar_proximo(
    cli: httpx.Client, lat: float, lng: float, raio_m: float, tipos: list[str]
) -> list[dict[str, Any]]:
    corpo = {
        "includedTypes": tipos,
        "maxResultCount": 20,
        "locationRestriction": {
            "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": raio_m}
        },
        "languageCode": "pt-BR",
        "regionCode": "BR",
    }
    resp = cli.post(f"{BASE}:searchNearby", json=corpo)
    if resp.status_code == 429:
        time.sleep(2)
        resp = cli.post(f"{BASE}:searchNearby", json=corpo)
    resp.raise_for_status()
    return resp.json().get("places", [])


def _buscar_texto(cli: httpx.Client, consulta: str, lat: float, lng: float,
                  raio_m: float) -> list[dict[str, Any]]:
    """Fallback para ramos que a Places nao cobre por `type` (ex.: otica)."""
    achados, token = [], None
    for _ in range(3):                                    # ate 60 resultados
        corpo: dict[str, Any] = {
            "textQuery": consulta,
            "languageCode": "pt-BR",
            "regionCode": "BR",
            "locationBias": {
                "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": raio_m}
            },
        }
        if token:
            corpo["pageToken"] = token
        resp = cli.post(f"{BASE}:searchText", json=corpo)
        resp.raise_for_status()
        dados = resp.json()
        achados.extend(dados.get("places", []))
        token = dados.get("nextPageToken")
        if not token:
            break
        time.sleep(1.5)                                   # o token leva um tempo para valer
    return achados


def _para_empresa(p: dict[str, Any], ramo: Ramo, cidade: str, uf: str) -> dict[str, Any] | None:
    nome = (p.get("displayName") or {}).get("text")
    if not nome:
        return None
    if p.get("businessStatus") in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
        return None                                       # nao prospectar empresa fechada

    loc = p.get("location") or {}
    telefone = normalizar_telefone(
        p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber")
    )

    cep = bairro = None
    for comp in p.get("addressComponents", []):
        tipos = comp.get("types", [])
        if "postal_code" in tipos:
            cep = comp.get("longText")
        elif "sublocality" in tipos or "sublocality_level_1" in tipos:
            bairro = comp.get("longText")

    horarios = p.get("regularOpeningHours") or {}

    return {
        "nome": nome,
        "nome_norm": normalizar_nome(nome),
        "ramo": ramo.chave,
        "categoria_fonte": p.get("primaryType"),
        "endereco": p.get("formattedAddress"),
        "bairro": bairro,
        "cidade": cidade,
        "uf": uf,
        "cep": cep,
        "lat": loc.get("latitude"),
        "lng": loc.get("longitude"),
        "telefone": telefone,
        "telefone_origem": "places" if telefone else None,
        "website_declarado": p.get("websiteUri"),
        "fonte": "places",
        "fonte_id": p.get("id"),
        # Sinais derivados -- e o que pode ser guardado sem prazo.
        "fonte_payload": {
            "rating": p.get("rating"),
            "userRatingCount": p.get("userRatingCount"),
            "temHorario": bool(horarios.get("periods")),
            "numFotos": len(p.get("photos") or []),
            "businessStatus": p.get("businessStatus"),
        },
    }


def coletar(
    cidade: str,
    uf: str,
    lat: float,
    lng: float,
    ramos: Iterable[Ramo],
    raio_km: float = 5.0,
    con: sqlite3.Connection | None = None,
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    api_key = chave()
    if con is not None:
        preparar_cache(con)

    cabecalhos = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": CAMPOS,
        "Content-Type": "application/json",
    }
    celulas = _grid(lat, lng, raio_km)
    raio_celula = 1.5 * 1000 * 0.75

    vistos: dict[str, dict[str, Any]] = {}
    with httpx.Client(headers=cabecalhos, timeout=timeout) as cli:
        for ramo in ramos:
            tipos = [t for t in ramo.places if t]
            brutos: list[dict[str, Any]] = []

            if tipos:
                for plat, plng in celulas:
                    try:
                        brutos.extend(_buscar_proximo(cli, plat, plng, raio_celula, tipos))
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code in (400, 403):
                            raise                        # chave/permissao: erro real
                        continue                         # celula falhou, segue

            # Complementa com busca textual: pega o que o `type` nao classifica.
            try:
                brutos.extend(
                    _buscar_texto(cli, f"{ramo.rotulo} em {cidade} {uf}",
                                  lat, lng, max(raio_km, 5) * 1000)
                )
            except httpx.HTTPStatusError:
                pass

            for p in brutos:
                pid = p.get("id")
                if not pid or pid in vistos:
                    continue
                empresa = _para_empresa(p, ramo, cidade, uf)
                if empresa:
                    vistos[pid] = empresa
                    if con is not None:
                        con.execute(
                            "INSERT OR REPLACE INTO places_cache (place_id, payload, buscado_em) "
                            "VALUES (?, ?, datetime('now'))",
                            (pid, json.dumps(p, ensure_ascii=False)),
                        )
        if con is not None:
            con.commit()

    return list(vistos.values())
