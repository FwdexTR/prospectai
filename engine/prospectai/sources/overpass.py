"""E2 -- coleta de empresas no OpenStreetMap (Nominatim + Overpass).

Dado aberto, sem chave de API e sem restricao contratual de armazenamento
(diferente da Places API). Cobertura no interior e irregular: e por isso que o
pipeline reporta a contagem por ramo -- voce precisa saber quando o OSM nao
serve para aquele nicho e partir para o Places.

Politica de uso respeitada de proposito:
  - User-Agent identificado com forma de contato
  - no maximo 1 requisicao por segundo no Nominatim
  - timeout declarado na propria query do Overpass
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable

import httpx

from ..normalize import normalizar_nome, normalizar_telefone
from ..ramos import Ramo

NOMINATIM = "https://nominatim.openstreetmap.org/search"
ESPELHOS_OVERPASS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
UA = "ProspectAI/0.1 (prospeccao B2B local; contato via github)"

_ultimo_nominatim = 0.0


@dataclass
class Local:
    nome: str
    lat: float
    lng: float
    area_id: int | None      # id de area do Overpass, quando ha relacao administrativa


def geocodificar(cidade: str, uf: str, timeout: float = 30.0) -> Local | None:
    """Resolve 'Campos dos Goytacazes/RJ' em coordenada + area do Overpass."""
    global _ultimo_nominatim
    espera = 1.0 - (time.monotonic() - _ultimo_nominatim)
    if espera > 0:
        time.sleep(espera)

    params = {
        "q": f"{cidade}, {uf}, Brasil",
        "format": "jsonv2",
        "limit": 5,
        "addressdetails": 1,
    }
    with httpx.Client(headers={"User-Agent": UA}, timeout=timeout) as cli:
        resp = cli.get(NOMINATIM, params=params)
        _ultimo_nominatim = time.monotonic()
        resp.raise_for_status()
        resultados = resp.json()

    if not resultados:
        return None

    # Preferimos a relacao administrativa: ela vira uma area no Overpass e
    # delimita o municipio de verdade, em vez de um raio chutado.
    melhor = None
    for r in resultados:
        if r.get("osm_type") == "relation":
            melhor = r
            break
    melhor = melhor or resultados[0]

    area_id = None
    if melhor.get("osm_type") == "relation":
        area_id = 3600000000 + int(melhor["osm_id"])

    return Local(
        nome=melhor.get("display_name", f"{cidade}/{uf}"),
        lat=float(melhor["lat"]),
        lng=float(melhor["lon"]),
        area_id=area_id,
    )


def _montar_query(local: Local, ramos: Iterable[Ramo], raio_km: float) -> str:
    """Monta a query do Overpass QL.

    Com raio  -> 'around' a partir do centro (pega municipios vizinhos).
    Sem raio  -> area administrativa exata do municipio.
    """
    filtros = []
    for ramo in ramos:
        for tag in ramo.osm:
            chave, _, valor = tag.partition("=")
            filtros.append((chave, valor, ramo.chave))

    if raio_km > 0 or local.area_id is None:
        raio_m = int((raio_km or 5) * 1000)
        escopo = f"(around:{raio_m},{local.lat},{local.lng})"
        cabecalho = ""
    else:
        escopo = "(area.alvo)"
        cabecalho = f"area({local.area_id})->.alvo;"

    corpo = "\n".join(
        f'  nwr["{chave}"="{valor}"]{escopo};' for chave, valor, _ in filtros
    )
    return f"[out:json][timeout:180];\n{cabecalho}\n(\n{corpo}\n);\nout center tags;"


def _tag_para_ramo(tags: dict[str, str], ramos: list[Ramo]) -> tuple[str | None, str | None]:
    """Descobre a qual ramo do catalogo o elemento pertence."""
    for ramo in ramos:
        for tag in ramo.osm:
            chave, _, valor = tag.partition("=")
            if tags.get(chave) == valor:
                return ramo.chave, f"{chave}={valor}"
    return None, None


def _montar_endereco(tags: dict[str, str]) -> str | None:
    rua = tags.get("addr:street")
    numero = tags.get("addr:housenumber")
    if not rua:
        return None
    return f"{rua}, {numero}" if numero else rua


def coletar(
    cidade: str,
    uf: str,
    ramos: list[Ramo],
    raio_km: float = 0,
    timeout: float = 240.0,
) -> tuple[Local, list[dict[str, Any]]]:
    """Devolve (local resolvido, lista de empresas cruas prontas para o banco)."""
    local = geocodificar(cidade, uf)
    if local is None:
        raise RuntimeError(f"Nao consegui localizar '{cidade}/{uf}' no Nominatim.")

    query = _montar_query(local, ramos, raio_km)

    erro_final: Exception | None = None
    dados = None
    for espelho in ESPELHOS_OVERPASS:
        try:
            with httpx.Client(headers={"User-Agent": UA}, timeout=timeout) as cli:
                resp = cli.post(espelho, content=query.encode("utf-8"))
                resp.raise_for_status()
                dados = resp.json()
            break
        except Exception as exc:                      # espelho fora do ar / sobrecarga
            erro_final = exc
            continue
    if dados is None:
        raise RuntimeError(f"Todos os espelhos do Overpass falharam: {erro_final}")

    empresas: list[dict[str, Any]] = []
    for el in dados.get("elements", []):
        tags = el.get("tags") or {}
        nome = tags.get("name")
        if not nome:
            continue                                   # sem nome nao da para abordar

        ramo_chave, categoria = _tag_para_ramo(tags, ramos)
        centro = el.get("center") or {}
        lat = el.get("lat") or centro.get("lat")
        lng = el.get("lon") or centro.get("lon")

        telefone = normalizar_telefone(
            tags.get("contact:phone") or tags.get("phone") or tags.get("contact:mobile")
        )
        site = (
            tags.get("website")
            or tags.get("contact:website")
            or tags.get("url")
            or tags.get("contact:facebook")
        )

        empresas.append({
            "nome": nome,
            "nome_norm": normalizar_nome(nome),
            "ramo": ramo_chave,
            "categoria_fonte": categoria,
            "endereco": _montar_endereco(tags),
            "bairro": tags.get("addr:suburb") or tags.get("addr:neighbourhood"),
            "cidade": tags.get("addr:city") or cidade,
            "uf": tags.get("addr:state") or uf,
            "cep": tags.get("addr:postcode"),
            "lat": lat,
            "lng": lng,
            "telefone": telefone,
            "telefone_origem": "osm" if telefone else None,
            "email": tags.get("contact:email") or tags.get("email"),
            "website_declarado": site,
            "instagram": tags.get("contact:instagram"),
            "facebook": tags.get("contact:facebook"),
            "fonte": "osm",
            "fonte_id": f"{el.get('type')}/{el.get('id')}",
            "fonte_payload": tags,
        })

    return local, empresas
