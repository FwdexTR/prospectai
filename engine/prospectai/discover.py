"""E4 -- descoberta e confirmacao do site oficial da empresa.

A regra mais importante do sistema inteiro esta aqui:

    Falso positivo e pior que nao achar.

Se atribuirmos o site de outra empresa a este lead, a mensagem vai dizer
"auditei o site de voces" apontando para o site errado. Isso queima o lead,
queima sua reputacao na cidade e nao tem conserto. Por isso exigimos PROVA DE
POSSE: o dominio so e aceito se o proprio conteudo dele confirmar que pertence
aquela empresa. Na duvida, SEM_SITE.
"""

from __future__ import annotations

import asyncio
import re
import socket
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

import httpx

from .audit import UA, Auditoria, auditar
from .normalize import normalizar_nome, slug, so_digitos, sufixo_telefone, tokens

# Dominios que NAO sao site proprio. Se o "website" da empresa e um destes, ela
# nao tem site -- tem um perfil. Isso e um lead melhor que "sem site nenhum":
# o dono ja entendeu que precisa de link e ja tentou resolver.
AGREGADORES = {
    "instagram.com", "facebook.com", "fb.com", "m.facebook.com", "linktr.ee",
    "linkbio.co", "beacons.ai", "bio.link", "linkbio.com", "campsite.bio",
    "wa.me", "api.whatsapp.com", "chat.whatsapp.com", "t.me",
    "ifood.com.br", "rappi.com.br", "aiqfome.com", "goomer.app", "anota.ai",
    "doctoralia.com.br", "boaconsulta.com", "agendarconsulta.com",
    "olx.com.br", "mercadolivre.com.br", "shopee.com.br", "elo7.com.br",
    "booking.com", "airbnb.com.br", "airbnb.com", "despegar.com.br",
    "google.com", "goo.gl", "maps.app.goo.gl", "sites.google.com",
    "youtube.com", "youtu.be", "tiktok.com", "linkedin.com", "twitter.com", "x.com",
    "tripadvisor.com.br", "tripadvisor.com", "yelp.com",
    "blogspot.com", "wordpress.com", "wixsite.com",
    # Pagina de links e atalhos (aparecem muito no Overture/Facebook)
    "instabio.cc", "linkme.bio", "whats.link", "bio.site", "bit.ly", "taplink.cc",
    "wa.link", "facebook.com.br",
}

# Redes nacionais e franquias: aparecem na coleta mas nao sao lead local --
# quem decide o site nao esta na sua cidade.
REDES_NACIONAIS = {
    "mcdonald", "burger king", "bobs", "subway", "spoleto", "girafas", "outback",
    "habibs", "china in box", "dominos", "pizza hut", "starbucks", "kfc",
    "giraffas", "madero", "coco bambu", "divino fogao", "vivenda do camarao",
    "smart fit", "bluefit", "selfit", "panobianco",
    "drogasil", "droga raia", "pacheco", "pague menos", "drogaria sao paulo",
    "americanas", "magazine luiza", "casas bahia", "ponto frio", "renner",
    "riachuelo", "c&a", "marisa", "havan", "lojas cem",
    "ibis", "mercure", "novotel", "accor", "bourbon", "blue tree",
    "sheraton", "four points", "marriott", "hilton", "radisson", "ramada",
    "holiday inn", "intercity", "nobile", "transamerica", "windsor",
    "best western", "comfort", "quality inn", "slaviero", "wyndham",
    "carrefour", "assai", "atacadao", "sendas", "guanabara",
    "oticas carol", "chilli beans", "cacau show", "kopenhagen", "the bakers",
    "unimed", "amil", "hapvida", "odontoprev", "sorridents", "orthodontic",
}


@dataclass
class Descoberta:
    url: str | None = None
    como: str = "nao_encontrado"          # declarado | busca | chute | nao_encontrado
    confianca: int = 0
    provas: list[str] = field(default_factory=list)
    agregador: str | None = None          # dominio de perfil, quando for o caso
    candidatos_testados: list[str] = field(default_factory=list)


def dominio_de(url: str | None) -> str | None:
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host or None


def eh_agregador(url: str | None) -> str | None:
    """Devolve o dominio do agregador, se a URL for perfil em vez de site."""
    dom = dominio_de(url)
    if not dom:
        return None
    for agregador in AGREGADORES:
        if dom == agregador or dom.endswith("." + agregador):
            return agregador
    return None


def eh_rede_nacional(nome: str) -> bool:
    alvo = normalizar_nome(nome)
    return any(rede in alvo for rede in REDES_NACIONAIS)


def url_plausivel(bruto: str | None) -> str | None:
    """Limpa o que as fontes trazem. O OSM tem coisa como 'spetaopelinca'
    (sem TLD), que nunca vai resolver -- descartar cedo economiza requisicao."""
    if not bruto:
        return None
    texto = bruto.strip().strip("<>\"' ")
    if not texto or " " in texto:
        return None
    if "://" not in texto:
        texto = "https://" + texto
    host = urlparse(texto).hostname or ""
    if "." not in host or len(host) < 4:
        return None
    if not re.match(r"^[a-z0-9.-]+$", host, re.I):
        return None
    return texto


def _dns_existe(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None)
        return True
    except socket.gaierror:
        return False


def gerar_chutes(nome: str, cidade: str | None = None) -> list[str]:
    """Dominios plausiveis a partir do nome. Barato: so vai pra rede o que
    passar no DNS."""
    base = slug(nome)
    if len(base) < 4 or len(base) > 30:
        return []
    candidatos = [f"{base}.com.br", f"{base}.com"]
    if cidade:
        c = slug(cidade)[:12]
        if c:
            candidatos.append(f"{base}{c}.com.br")
    vivos = []
    for dom in candidatos:
        if _dns_existe(dom):
            vivos.append("https://" + dom)
    return vivos


def provar_posse(
    auditoria: Auditoria,
    nome: str,
    telefone: str | None = None,
    cnpj: str | None = None,
    cep: str | None = None,
    cidade: str | None = None,
    uf: str | None = None,
    declarado: bool = False,
) -> tuple[list[str], bool]:
    """Provas de posse do dominio, agrupadas em CATEGORIAS INDEPENDENTES.

    Descoberto na pratica (teste T3 em Campos/RJ): 'titulo' e 'dominio' NAO sao
    provas independentes -- as duas so dizem "o nome bate". Um bar chamado
    Alaska casava com alaska.org (site do estado americano) e somava 2 pontos
    a partir de UMA unica coincidencia. Mister Motel casava com um site
    holandes pelo mesmo motivo.

    Agora a categoria `nome` vale no maximo 1 ponto, e nunca sozinha: e preciso
    ao menos uma prova de outra categoria, ou seja, um fato que ligue o dominio
    a ESTE negocio NESTA cidade.

    Devolve (lista de provas, tem_prova_independente).
    """
    provas: list[str] = []

    # --- categoria NOME (vale no maximo 1 ponto, nunca decide sozinha) -------
    prova_nome = None
    t_nome, t_titulo = tokens(nome), tokens(auditoria.titulo)
    if t_nome and t_titulo:
        comuns = t_nome & t_titulo
        significativos = {t for t in comuns if len(t) >= 4}
        if significativos and len(comuns) >= min(len(t_nome), 2):
            prova_nome = "titulo"
    if prova_nome is None:
        dom = dominio_de(auditoria.site_url) or ""
        raiz = dom.split(".")[0]
        if len(raiz) >= 5 and slug(nome).startswith(raiz[:5]):
            prova_nome = "dominio"
    if prova_nome:
        provas.append(prova_nome)

    # --- categoria CONTATO (forte: so o dono publica isso) ------------------
    independentes = 0

    alvo = sufixo_telefone(telefone)
    if alvo and any(alvo in t for t in auditoria.telefones_no_site):
        provas.append("telefone")
        independentes += 1

    digitos_cnpj = so_digitos(cnpj)
    if len(digitos_cnpj) == 14 and digitos_cnpj in auditoria.digitos_pagina:
        provas.append("cnpj")
        independentes += 1

    digitos_cep = so_digitos(cep)
    if len(digitos_cep) == 8 and digitos_cep in auditoria.digitos_pagina:
        provas.append("cep")
        independentes += 1

    # --- categoria LOCAL ----------------------------------------------------
    # A cidade aparecer no texto visivel e independente do nome, e e o que
    # separa a padaria de Campos de um site estrangeiro homonimo.
    #
    # Cuidado com a normalizacao: `normalizar_nome` remove stopwords, entao
    # "Campos dos Goytacazes" vira "campos goytacazes", que NUNCA e substring
    # do texto da pagina ("campos dos goytacazes"). Comparamos por token, com
    # limite de palavra, e exigimos TODOS os tokens significativos -- so
    # "campos" casaria com qualquer pagina que fale de campo de futebol.
    if cidade and auditoria.texto_norm:
        significativos = [t for t in tokens(cidade) if len(t) >= 4]
        if significativos and all(
            re.search(rf"\b{re.escape(t)}\b", auditoria.texto_norm) for t in significativos
        ):
            provas.append("cidade")
            independentes += 1

    # --- categoria DECLARACAO ----------------------------------------------
    # A fonte (OSM/Places) afirmou que este e o site. E um terceiro dizendo,
    # nao nos deduzindo -- vale como prova independente, ainda que fraca.
    if declarado:
        provas.append("declarado")
        independentes += 1

    return provas, independentes > 0


async def descobrir(
    nome: str,
    website_declarado: str | None,
    telefone: str | None = None,
    cidade: str | None = None,
    uf: str | None = None,
    cnpj: str | None = None,
    cep: str | None = None,
    cli: httpx.AsyncClient | None = None,
    usar_chutes: bool = True,
) -> tuple[Descoberta, Auditoria | None]:
    """Devolve (descoberta, auditoria do site aceito).

    Ordem: declarado -> chute por dominio. A busca web entra depois, como
    adaptador opcional -- chute + DNS ja resolve boa parte sem depender de
    ninguem.
    """
    d = Descoberta()

    # Caso 1: o "site" e um perfil em agregador. Nao e site, e sinal proprio.
    agregador = eh_agregador(website_declarado)
    if agregador:
        d.agregador = agregador
        d.como = "nao_encontrado"
        d.url = None
        # Segue tentando achar site proprio por chute -- ter Instagram nao
        # impede a empresa de ter um site que a fonte nao conhecia.

    candidatos: list[tuple[str, str]] = []
    if not agregador:
        limpo = url_plausivel(website_declarado)
        if limpo:
            candidatos.append((limpo, "declarado"))
    if usar_chutes:
        candidatos.extend((u, "chute") for u in gerar_chutes(nome, cidade))

    melhor: tuple[int, bool, Descoberta, Auditoria] | None = None
    for url, como in candidatos:
        d.candidatos_testados.append(url)
        auditoria = await auditar(url, cli)
        if auditoria.erro or (auditoria.http_status or 500) >= 400:
            continue
        provas, independente = provar_posse(
            auditoria, nome, telefone, cnpj, cep, cidade, uf,
            declarado=(como == "declarado"),
        )
        pontos = len(provas)
        if melhor is None or (independente, pontos) > (melhor[1], melhor[0]):
            achado = Descoberta(
                url=auditoria.site_url, como=como, confianca=pontos,
                provas=provas, agregador=agregador,
                candidatos_testados=list(d.candidatos_testados),
            )
            melhor = (pontos, independente, achado, auditoria)

    # A regra: >= 2 provas E ao menos uma independente do nome.
    # Sem a prova independente, nome batendo e coincidencia -- e coincidencia
    # aqui significa mandar mensagem citando o site de outra empresa.
    if melhor and melhor[0] >= 2 and melhor[1]:
        return melhor[2], melhor[3]
    if melhor and melhor[0] >= 1:
        suspeito = melhor[2]
        suspeito.como = "suspeito"          # nao usa em mensagem; vai pra revisao
        return suspeito, melhor[3]

    d.confianca = 0
    d.como = "nao_encontrado"
    d.url = None
    return d, None


async def descobrir_muitos(
    empresas: list[dict],
    concorrencia: int = 8,
    progresso: Callable[[int, int], None] | None = None,
) -> dict[int, tuple[Descoberta, Auditoria | None]]:
    """Roda a descoberta em lote. Chave do retorno: indice da empresa na lista.

    `progresso` e chamado a cada empresa concluida. Sem ele a barra da interface
    ficava parada em 0 durante a etapa mais demorada da cadeia -- que parece
    travamento, ainda que esteja tudo certo.
    """
    limite = asyncio.Semaphore(concorrencia)
    concluidas = 0

    async with httpx.AsyncClient(
        headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"},
        timeout=httpx.Timeout(15.0, connect=8.0),
        follow_redirects=True, verify=False,
    ) as cli:
        async def uma(i: int, e: dict):
            nonlocal concluidas
            async with limite:
                r = await descobrir(
                    nome=e["nome"],
                    website_declarado=e.get("website_declarado"),
                    telefone=e.get("telefone"),
                    cidade=e.get("cidade"),
                    uf=e.get("uf"),
                    cnpj=e.get("cnpj"),
                    cep=e.get("cep"),
                    cli=cli,
                )
            concluidas += 1
            if progresso:
                progresso(concluidas, len(empresas))
            return i, r

        pares = await asyncio.gather(
            *(uma(i, e) for i, e in enumerate(empresas)), return_exceptions=True
        )

    return {i: r for i, r in (p for p in pares if isinstance(p, tuple))}
