"""Fonte Overture Maps (places): telefone e site de negocios, gratis e sem chave.

Base aberta mantida pela Overture Maps Foundation (Meta, Microsoft, Amazon,
TomTom...). Na regiao de Campos, 97% dos registros vem das paginas de empresa
do Facebook. Licenca CDLA-Permissive-2.0 (uso comercial permitido, com
atribuicao), CC0 e Apache-2.0 em partes menores.

Medido em 14/10/2026 na area urbana de Campos dos Goytacazes (9.318 negocios):
86% com telefone valido, 55% com celular, 27% com site proprio cadastrado.
O OpenStreetMap, na mesma cidade, deu 16% com telefone.

Leitura remota: os arquivos ficam num bucket S3 publico e o DuckDB baixa so os
pedacos (row groups) cujo `bbox` cai na caixa pedida -- uma cidade leva ~20 s
e nao exige baixar a base inteira.

Cuidado (I1): "sem link" aqui quer dizer que a PAGINA da empresa nao informa
site, nao que a empresa nao tem site. Quem afirma "nao tem site" e a auditoria
(discover.py), nunca esta fonte sozinha.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

import httpx

from ..discover import UA, dominio_de, eh_agregador, eh_rede_nacional
from ..normalize import eh_movel, normalizar_nome, normalizar_telefone, sem_acento
from ..ramos import Ramo

BUCKET = "overturemaps-us-west-2"
LISTAGEM = f"https://{BUCKET}.s3.amazonaws.com/?list-type=2&prefix=release/&delimiter=/"
ATRIBUICAO = ("Dados de lugares: Overture Maps Foundation (overturemaps.org), "
              "licença CDLA-Permissive-2.0")

# --------------------------------------------------------------------------
# Classificacao do "site"
# --------------------------------------------------------------------------
# Construtor gratuito, site gerado automaticamente ou cardapio de plataforma:
# existe uma pagina, mas nao e site proprio -- para quem vende site, e lead.
GRATUITOS = {
    "placeweb.site", "negocio.site", "business.site", "wixsite.com", "wix.com",
    "webnode.com", "webnode.page", "blogspot.com", "blogspot.com.br", "wordpress.com",
    "kyte.site", "site123.me", "lojavirtualnuvem.com.br", "menudino.com",
    "instadelivery.com.br", "ola.click", "yooga.app", "cardapioweb.com",
    "goomer.app", "anota.ai", "ifood.com.br", "sites.google.com",
}
# Pagina de links / atalho: nao e site de jeito nenhum.
PERFIS_EXTRA = {"instabio.cc", "linkme.bio", "whats.link", "bio.site", "bit.ly",
                "taplink.cc", "wa.link", "facebook.com.br", "linktr.ee"}

# Dominio de rede, banco ou orgao publico: nao e lead de negocio local.
DOMINIOS_FORA = re.compile(
    r"(^|\.)(gov\.br|jus\.br|leg\.br|mil\.br|edu\.br|ipiranga\.com\.br|shell\.com(\.br)?|"
    r"postospetrobras\.com\.br|petrobras\.com\.br|ultragaz\.com\.br|americanas\.com\.br|"
    r"boticario\.com\.br|bradesco\.com\.br|itau\.com\.br|santander\.com\.br|bb\.com\.br|"
    r"caixa\.gov\.br|bobs\.com\.br|burgerking\.com\.br|mcdonalds\.com\.br|universal\.org|"
    r"correios\.com\.br|magazineluiza\.com\.br|casasbahia\.com\.br|estacio\.br|"
    r"postoipiranganaweb\.com\.br|vivo\.com\.br|claro\.com\.br|tim\.com\.br|"
    r"drogasil\.com\.br|drogaraia\.com\.br|paguemenos\.com\.br|subway\.com|"
    r"cacaushow\.com\.br|oboticario\.com\.br|havaianas\.com\.br|riachuelo\.com\.br|"
    r"renner\.com\.br|cea\.com\.br|localiza\.com|unimed\.coop\.br)$"
)


def _no_conjunto(dominio: str, conjunto: set[str]) -> bool:
    return any(dominio == d or dominio.endswith("." + d) for d in conjunto)


def classificar_site(links: Iterable[str] | None) -> tuple[str, str | None]:
    """('proprio'|'gratuito'|'perfil'|'nenhum', url escolhida).

    Prioridade: site proprio > gratuito > perfil. Uma empresa com Instagram e
    site proprio conta como tendo site.
    """
    proprio = gratuito = perfil = None
    for url in links or []:
        dom = (dominio_de(url) or "").lower()
        if not dom:
            continue
        if eh_agregador(url) or _no_conjunto(dom, PERFIS_EXTRA):
            perfil = perfil or url
        elif _no_conjunto(dom, GRATUITOS):
            gratuito = gratuito or url
        else:
            proprio = proprio or url
    if proprio:
        return "proprio", proprio
    if gratuito:
        return "gratuito", gratuito
    if perfil:
        return "perfil", perfil
    return "nenhum", None


def instagram_de(links: Iterable[str] | None) -> str | None:
    for url in links or []:
        if "instagram.com" in (url or "").lower():
            return url
    return None


# --------------------------------------------------------------------------
# Categorias: o que e negocio e em que grupo cai
# --------------------------------------------------------------------------
# Lugar que nao e cliente: igreja, orgao publico, praca, banco...
NAO_NEGOCIO = {
    "christian_place_of_worship", "religious_organization", "buddhist_place_of_worship",
    "historic_site", "beach", "park", "river", "lake", "mountain", "waterfall",
    "public_plaza", "government_office", "police_station", "fire_station", "courthouse",
    "military_site", "jail_or_prison", "bridge", "monument", "sculpture_statue",
    "lighthouse", "geographic_entities", "national_park", "garden", "playground",
    "public_fountain", "public_restroom", "recreational_trail_or_path",
    "community_and_government", "government_department", "public_utility", "atm",
    "bank_or_credit_union", "airport", "train_station", "public_transit_facility_or_service",
    "political_organization", "labor_union", "civic_organization",
    "social_or_community_service", "youth_organization", "food_bank", "fairgrounds",
    "stadium_arena", "sport_field", "sport_court", "skate_park", "campus_building",
    "library", "apartment", "condominium", "electric_utility_provider",
    "water_utility_provider", "emergency_department", "zoo", "cultural_center",
    "community_center", "college_university", "hospital", "parking", "radio_station",
}
TAXONOMIA_NAO_NEGOCIO = {"public_school", "religious_school", "medical_research_institute"}

GRUPOS: dict[str, tuple[str, ...]] = {
    "Restaurantes, bares e lanchonetes": (
        "restaurant", "casual_eatery", "bar", "fast_food_restaurant", "smoothie_juice_bar",
        "cafe", "coffee_shop", "food_and_drink", "food_service", "food_truck_stand",
        "brewery", "lounge", "alcoholic_beverage_venue", "non_alcoholic_beverage_venue",
        "dance_club"),
    "Mercados e alimentos": (
        "food_and_beverage_store", "convenience_store", "warehouse_club_store", "superstore",
        "farmers_market", "market", "wholesaler"),
    "Beleza e estética": (
        "personal_or_beauty_service", "personal_care_and_beauty_store", "wellness_service"),
    "Roupas, calçados e acessórios": (
        "fashion_and_apparel_store", "department_store", "second_hand_store", "discount_store"),
    "Automotivo": (
        "automotive_service", "vehicle_parts_store", "auto_dealer", "vehicle_dealer",
        "vehicle_service", "gas_station", "fueling_station"),
    "Odontologia": ("dental_clinic",),
    "Clínicas e saúde": (
        "health_care", "specialized_health_care", "diagnostics_imaging_or_lab_service",
        "behavioral_or_mental_health_clinic", "physical_medicine_and_rehabilitation",
        "outpatient_care_facility", "medical_service", "surgery",
        "reproductive_perinatal_and_womens_care", "vision_or_eye_care_clinic",
        "pediatric_clinic", "complementary_and_alternative_medicine",
        "primary_care_or_general_clinic", "specialized_medical_facility",
        "emergency_or_urgent_care_facility", "senior_living_facility"),
    "Farmácias": ("pharmacy_and_drug_store",),
    "Construção, casa e reformas": (
        "hardware_home_and_garden_store", "home_service", "building_or_construction_service",
        "design_service"),
    "Imobiliárias": ("real_estate_service", "housing_or_property_service"),
    "Advocacia": ("attorney_or_law_firm", "legal_service"),
    "Contabilidade e finanças": ("financial_service",),
    "Educação e cursos": (
        "place_of_learning", "specialty_school", "elementary_school", "preschool",
        "high_school", "middle_school", "education", "educational_service",
        "tutoring_service", "educational_facility"),
    "Academias e esportes": (
        "gym", "fitness_studio", "sport_or_fitness_facility", "sporting_goods_store",
        "sport_or_recreation_club", "sports_and_recreation", "sport_league", "sport_team",
        "swimming_pool"),
    "Pet e veterinária": ("animal_and_pet_store", "animal_or_pet_service"),
    "Hotéis e pousadas": (
        "hotel", "lodging", "bed_and_breakfast", "inn", "private_lodging", "resort",
        "campground"),
    "Eventos e festas": (
        "event_or_party_service", "music_venue", "event_venue", "arts_and_entertainment"),
    "Lojas diversas": (
        "shopping", "electronics_store", "flowers_and_gifts_store",
        "arts_crafts_and_hobby_store", "specialty_store", "books_music_and_video_store",
        "office_supply_store", "toys_and_games_store", "musical_instrument_and_pro_audio_store",
        "shopping_mall"),
    "Indústria e B2B": (
        "manufacturer", "supplier_or_distributor", "b2b_service",
        "b2b_industrial_and_machine_service", "b2b_energy_and_utility_service",
        "b2b_office_and_professional_service", "b2b_transportation_and_storage_service",
        "b2b_science_and_technology_service", "agricultural_service", "farm",
        "industrial_facility_or_service"),
}
_GRUPO_DE = {cat: g for g, cats in GRUPOS.items() for cat in cats}
OUTROS = "Outros serviços"


def grupo(categoria: str | None) -> str:
    return _GRUPO_DE.get(categoria or "", OUTROS)


def eh_negocio(categoria: str | None, taxonomia: str | None) -> bool:
    if not categoria:
        return False                           # sem categoria: nao da para abordar
    return categoria not in NAO_NEGOCIO and (taxonomia or "") not in TAXONOMIA_NAO_NEGOCIO


# Ramo do catalogo -> categorias do Overture. Taxonomia (fina) primeiro, depois
# categoria basica: pizzaria e padaria sao subtipos de restaurante/lanchonete.
RAMOS_TAXONOMIA: dict[str, tuple[str, ...]] = {
    "pizzaria": ("pizza_restaurant",),
    "padaria": ("bakery", "patisserie", "cake_shop"),
    "otica": ("eyewear_store", "optician", "eyewear_and_optician"),
    "contabilidade": ("accountant", "bookkeeper", "tax_preparation_service"),
    "construtora": ("contractor", "general_contractor", "architect", "engineering_services"),
}
RAMOS_CATEGORIA: dict[str, tuple[str, ...]] = {
    "restaurante": GRUPOS["Restaurantes, bares e lanchonetes"],
    "clinica_odonto": ("dental_clinic",),
    "clinica_medica": GRUPOS["Clínicas e saúde"],
    "veterinaria": ("animal_or_pet_service", "animal_and_pet_store"),
    "salao": ("personal_or_beauty_service", "wellness_service"),
    "academia": ("gym", "fitness_studio", "sport_or_fitness_facility"),
    "oficina": ("automotive_service", "vehicle_parts_store", "vehicle_service"),
    "imobiliaria": ("real_estate_service",),
    "hotel": GRUPOS["Hotéis e pousadas"],
    "advocacia": ("attorney_or_law_firm", "legal_service"),
    "farmacia": ("pharmacy_and_drug_store",),
    "loja_roupas": ("fashion_and_apparel_store",),
    "materiais_construcao": ("hardware_home_and_garden_store",),
    "construtora": ("building_or_construction_service",),
    "escola": GRUPOS["Educação e cursos"],
}


def ramo_de(categoria: str | None, taxonomia: str | None, ramos: list[Ramo]) -> str | None:
    chaves = [r.chave for r in ramos]
    for chave in chaves:
        if (taxonomia or "") in RAMOS_TAXONOMIA.get(chave, ()):
            return chave
    for chave in chaves:
        # Taxonomia fina de outro ramo pedido tem prioridade sobre a categoria
        # basica: pizza nao conta como 'restaurante' se 'pizzaria' foi pedida.
        if (categoria or "") in RAMOS_CATEGORIA.get(chave, ()):
            if chave == "loja_roupas" and taxonomia in RAMOS_TAXONOMIA["otica"]:
                continue
            return chave
    return None


# --------------------------------------------------------------------------
# Consulta remota
# --------------------------------------------------------------------------
_versao_cache: dict[str, str] = {}


def versao_atual(timeout: float = 30.0) -> str:
    """Release mais recente publicado no bucket (ex.: '2026-09-23.1')."""
    if "v" not in _versao_cache:
        with httpx.Client(timeout=timeout) as cli:
            xml = cli.get(LISTAGEM).text
        versoes = sorted(re.findall(r"<Prefix>release/([^<]+)/</Prefix>", xml))
        if not versoes:
            raise RuntimeError("Nao achei nenhum release no bucket do Overture.")
        _versao_cache["v"] = versoes[-1]
    return _versao_cache["v"]


def buscar_caixa(xmin: float, xmax: float, ymin: float, ymax: float,
                 versao: str | None = None) -> list[dict[str, Any]]:
    """Lugares cujo ponto cai na caixa. So os pedacos necessarios trafegam."""
    import duckdb

    versao = versao or versao_atual()
    url = (f"s3://{BUCKET}/release/{versao}/theme=places/type=place/*.parquet")
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    cur = con.execute(f"""
        SELECT id, names.primary AS nome, basic_category AS categoria,
               taxonomy.primary AS taxonomia, operating_status AS status, confidence,
               websites, socials, phones, emails,
               addresses[1].freeform AS endereco, addresses[1].locality AS cidade,
               addresses[1].postcode AS cep, addresses[1].region AS uf,
               (bbox.xmin + bbox.xmax) / 2 AS lng, (bbox.ymin + bbox.ymax) / 2 AS lat,
               sources[1].dataset AS origem
          FROM read_parquet('{url}', hive_partitioning=1)
         WHERE bbox.xmin BETWEEN ? AND ? AND bbox.ymin BETWEEN ? AND ?
    """, [xmin, xmax, ymin, ymax])
    nomes = [d[0] for d in cur.description]
    linhas = [dict(zip(nomes, l)) for l in cur.fetchall()]
    con.close()
    return linhas


def caixa_em_volta(lat: float, lng: float, raio_km: float) -> tuple[float, float, float, float]:
    from math import cos, radians
    dlat = raio_km / 111.0
    dlng = raio_km / (111.0 * max(cos(radians(lat)), 0.1))
    return lng - dlng, lng + dlng, lat - dlat, lat + dlat


def mesma_cidade(a: str | None, b: str | None) -> bool:
    return bool(a and b) and normalizar_nome(a) == normalizar_nome(b)


# --------------------------------------------------------------------------
# Linha do Overture -> empresa do ProspectAI
# --------------------------------------------------------------------------
@dataclass
class Telefones:
    principal: str | None
    celular: str | None


def telefones(brutos: Iterable[str] | None) -> Telefones:
    validos = [t for t in (normalizar_telefone(b) for b in brutos or []) if t]
    celular = next((t for t in validos if eh_movel(t)), None)
    return Telefones(principal=celular or (validos[0] if validos else None), celular=celular)


def eh_rede(nome: str | None, link: str | None) -> bool:
    dom = (dominio_de(link) or "").lower() if link else ""
    return bool(nome and eh_rede_nacional(nome)) or bool(dom and DOMINIOS_FORA.search(dom))


def para_empresa(linha: dict[str, Any], cidade: str, uf: str,
                 ramo: str | None = None) -> dict[str, Any] | None:
    """Mesmo formato que overpass.coletar devolve. None = nao e lead."""
    nome = (linha.get("nome") or "").strip()
    if not nome or not eh_negocio(linha.get("categoria"), linha.get("taxonomia")):
        return None
    tipo, link = classificar_site(linha.get("websites"))
    if eh_rede(nome, link):
        return None
    tel = telefones(linha.get("phones"))
    return {
        "nome": nome,
        "nome_norm": normalizar_nome(nome),
        "ramo": ramo,
        "categoria_fonte": linha.get("taxonomia") or linha.get("categoria"),
        "endereco": linha.get("endereco"),
        "bairro": None,
        "cidade": linha.get("cidade") or cidade,
        "uf": uf,
        "cep": linha.get("cep"),
        "lat": linha.get("lat"),
        "lng": linha.get("lng"),
        "telefone": tel.principal,
        "telefone_origem": "overture" if tel.principal else None,
        "email": (linha.get("emails") or [None])[0],
        # Perfil (Instagram, linktree) tambem vai: o E4 reconhece agregador e
        # continua procurando site proprio por chute de dominio.
        "website_declarado": link,
        "instagram": instagram_de(list(linha.get("websites") or [])
                                  + list(linha.get("socials") or [])),
        "facebook": next((s for s in linha.get("socials") or [] if "facebook.com" in s), None),
        "fonte": "overture",
        "fonte_id": linha["id"],
        "fonte_payload": {"categoria": linha.get("categoria"),
                          "taxonomia": linha.get("taxonomia"),
                          "site_tipo": tipo, "confianca": linha.get("confidence"),
                          "origem": linha.get("origem")},
    }


def coletar(cidade: str, uf: str, ramos: list[Ramo], raio_km: float = 0) -> list[dict[str, Any]]:
    """Semente E2 grátis: empresas da cidade nos ramos pedidos, com telefone e site.

    raio_km = 0: so o municipio (pelo campo de cidade do endereco). Acima de 0,
    tudo dentro do raio, inclusive cidades vizinhas.
    """
    from .overpass import geocodificar

    local = geocodificar(cidade, uf)
    if local is None:
        raise RuntimeError(f"Nao consegui localizar '{cidade}/{uf}'.")
    caixa = caixa_em_volta(local.lat, local.lng, raio_km or 25.0)
    saida = []
    for linha in buscar_caixa(*caixa):
        if not raio_km and not mesma_cidade(linha.get("cidade"), cidade):
            continue
        ramo = ramo_de(linha.get("categoria"), linha.get("taxonomia"), ramos)
        if ramos and ramo is None:
            continue
        empresa = para_empresa(linha, cidade, uf, ramo)
        if empresa:
            saida.append(empresa)
    return saida


# --------------------------------------------------------------------------
# Verificacao rapida de site no ar (para o painel; a auditoria completa e o E5)
# --------------------------------------------------------------------------
async def _status_site(cli: httpx.AsyncClient, url: str) -> str:
    try:
        r = await cli.get(url)
    except httpx.TimeoutException:
        return "nao_respondeu"                 # lento ou bloqueando: nao conclui nada
    except httpx.ConnectError:
        return "fora_do_ar"                    # dominio morto ou servidor recusando
    except httpx.HTTPError:
        return "nao_respondeu"
    if r.status_code in (401, 403, 429):
        return "no_ar"                         # existe; so nao gosta de robo
    if r.status_code >= 400:
        return "fora_do_ar"
    final = (r.url.host or "").lower()
    if final.endswith(("google.com", "business.site", "negocio.site")) and "maps" in str(r.url):
        return "fora_do_ar"                    # site do Google Meu Negocio desligado
    if eh_estacionado(r.text[:20_000]):
        return "fora_do_ar"                    # responde 200, mas e pagina de dominio a venda
    return "no_ar"


# Pagina de dominio estacionado ou a venda: responde 200 e engana o teste de
# "no ar". Medido em 14/10/2026: todo *.placeweb.site virou isso.
RE_ESTACIONADO = re.compile(
    r"this domain (may be|is) for sale|buy this domain|domain is parked|"
    r"dom[ií]nio (est[aá] )?[àa] venda|este dom[ií]nio pode estar [àa] venda|"
    r"sedoparking|parkingcrew|bodis\.com|hugedomains\.com|dan\.com/buy-domain|"
    r"afternic\.com|domain-for-sale",
    re.I,
)


def eh_estacionado(html: str) -> bool:
    return bool(RE_ESTACIONADO.search(html or ""))


def verificar_sites(urls: list[str], concorrencia: int = 24,
                    progresso=None) -> dict[str, str]:
    """url -> no_ar | fora_do_ar | nao_respondeu. Uma requisicao por site."""
    async def tudo() -> dict[str, str]:
        limite = asyncio.Semaphore(concorrencia)
        feitos = 0
        saida: dict[str, str] = {}
        async with httpx.AsyncClient(
            headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"},
            timeout=httpx.Timeout(10.0, connect=6.0), follow_redirects=True, verify=False,
        ) as cli:
            async def um(url: str) -> None:
                nonlocal feitos
                alvo = url if url.startswith(("http://", "https://")) else "http://" + url
                async with limite:
                    saida[url] = await _status_site(cli, alvo)
                feitos += 1
                if progresso and feitos % 200 == 0:
                    progresso("sites", feitos, len(urls))
            await asyncio.gather(*(um(u) for u in dict.fromkeys(urls)))
        return saida
    return asyncio.run(tudo())


# --------------------------------------------------------------------------
# Painel: a regiao inteira, pronta para a pagina online
# --------------------------------------------------------------------------
REGIOES = {
    # Norte e Noroeste Fluminense
    "norte-fluminense": (-42.10, -40.90, -22.45, -21.10),
}


def _cidade_canonica(bruto: str | None, conhecidas: dict[str, str]) -> str | None:
    if not bruto:
        return None
    chave = normalizar_nome(bruto)
    if chave not in conhecidas:
        # primeira grafia vista vira a oficial; titulo se veio em CAIXA ALTA
        from ..crm import titulo
        conhecidas[chave] = titulo(bruto.strip()) or bruto.strip()
    return conhecidas[chave]


_LIXO = re.compile(r"[�\x00-\x08\x0b-\x1f\x7f]")


def limpo(texto: str | None) -> str | None:
    """Tira o caractere de substituicao (U+FFFD) e controles que vem da fonte.

    Alguns nomes chegam com acento corrompido ("Ilha Gastr�"); melhor um
    nome sem a letra do que um arquivo que a pagina publicada recusa.
    """
    if texto is None:
        return None
    t = re.sub(r"\s+", " ", _LIXO.sub("", texto)).strip()
    return t or None


def montar_painel(regiao: str = "norte-fluminense", verificar: bool = True,
                  progresso=None) -> dict[str, Any]:
    """Junta, classifica e (opcional) testa os sites. Formato compacto para web."""
    versao = versao_atual()
    linhas = buscar_caixa(*REGIOES[regiao], versao=versao)

    cidades: dict[str, str] = {}
    # Grafias com acento primeiro, para a oficial sair "Macaé" e nao "MACAE".
    linhas.sort(key=lambda l: (sem_acento(l.get("cidade") or "") == (l.get("cidade") or "")))
    itens = []
    for l in linhas:
        nome = limpo(l.get("nome"))
        if not nome or not eh_negocio(l.get("categoria"), l.get("taxonomia")):
            continue
        if l.get("status") not in (None, "open"):
            continue
        tipo, link = classificar_site(l.get("websites"))
        if eh_rede(nome, link):
            continue
        tel = telefones(l.get("phones"))
        itens.append({
            "nome": nome, "grupo": grupo(l.get("categoria")),
            "cidade": _cidade_canonica(limpo(l.get("cidade")), cidades) or "Sem cidade",
            "endereco": limpo(l.get("endereco")), "telefone": tel.principal,
            "celular": bool(tel.celular), "site": link if tipo in ("proprio", "gratuito") else None,
            "site_tipo": tipo, "perfil": link if tipo == "perfil" else None,
            "instagram": instagram_de(list(l.get("websites") or []) + list(l.get("socials") or [])),
            "confianca": round(float(l.get("confidence") or 0), 2),
        })

    if verificar:
        status = verificar_sites([i["site"] for i in itens if i["site"]], progresso=progresso)
        for i in itens:
            i["site_status"] = status.get(i["site"]) if i["site"] else None

    return {"versao": versao, "gerado_em": datetime.now().isoformat(timespec="minutes"),
            "regiao": regiao, "atribuicao": ATRIBUICAO, "itens": itens}
