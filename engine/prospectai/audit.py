"""E5 -- auditoria tecnica de um site. O coracao do sistema.

Regra que atravessa o arquivo inteiro (invariante I1): campo que nao foi medido
fica None, nunca False. 'Nao tem WhatsApp' e 'nao consegui verificar' sao coisas
diferentes, e confundir as duas faz o sistema afirmar bobagem na mensagem.

Educacao tecnica: User-Agent identificado, 1 requisicao por dominio a cada 3s,
robots.txt respeitado. Voce e o convidado no site dos outros.
"""

from __future__ import annotations

import asyncio
import re
import socket
import ssl
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from .normalize import sem_acento

UA = "ProspectAI/0.1 (auditoria tecnica de site; +contato no rodape do relatorio)"
TIMEOUT = httpx.Timeout(15.0, connect=8.0)

TITULOS_GENERICOS = {
    "", "home", "inicio", "início", "index", "site", "meu site", "novo site",
    "documento sem titulo", "documento sem título", "untitled", "untitled document",
    "página inicial", "pagina inicial", "web site", "website", "seu site aqui",
}

# Assinaturas de plataforma: (nome, regex no HTML/headers, e_plano_gratuito)
PLATAFORMAS: tuple[tuple[str, str, bool], ...] = (
    ("Wix (plano gratuito)", r"wixsite\.com|wix-?free", True),
    ("Site automático (Placeweb)", r"placeweb\.site", True),
    ("Site do Google Meu Negócio", r"business\.site|negocio\.site", True),
    ("Kyte", r"kyte\.site", True),
    ("Webnode", r"webnode\.(com|page)", True),
    ("Site123", r"site123\.(me|com)", True),
    ("Wix", r"wix\.com|_wixCssImports|X-Wix-", False),
    ("Google Sites", r"sites\.google\.com|gstatic\.com/sites", True),
    ("Blogspot", r"blogspot\.com|blogger\.com", True),
    ("WordPress", r"/wp-content/|/wp-includes/|wp-json", False),
    ("Squarespace", r"squarespace\.com|static1\.squarespace", False),
    ("Webflow", r"webflow\.(com|io)", False),
    ("Shopify", r"cdn\.shopify\.com|Shopify\.theme", False),
    ("Nuvemshop", r"tiendanube|nuvemshop", False),
    ("Loja Integrada", r"lojaintegrada", False),
    ("Tray", r"tray\.com\.br|traycommerce", False),
    ("VTEX", r"vtexassets|vtexcommercestable", False),
    ("Wordpress.com", r"wp\.com|wordpress\.com", True),
)

RE_WHATSAPP = re.compile(r"wa\.me/|api\.whatsapp\.com|web\.whatsapp\.com", re.I)
RE_ANALYTICS = re.compile(
    r"googletagmanager\.com|google-analytics\.com|gtag\(|\bga\(|plausible\.io|umami", re.I
)
RE_PIXEL = re.compile(r"connect\.facebook\.net|fbq\(|tiktok\.com/i18n/pixel|hotjar", re.I)
RE_ANO = re.compile(r"(?:©|&copy;|copyright)[^0-9]{0,40}((?:19|20)\d{2})", re.I)
RE_ANO_SOLTO = re.compile(r"\b(20[0-2]\d)\b")
RE_CARDAPIO = re.compile(r"card[aá]pio|menu|catalogo|catálogo", re.I)


@dataclass
class Auditoria:
    """Espelha a tabela `auditoria`. None = nao medido."""
    site_url: str | None = None
    http_status: int | None = None
    tempo_resposta_ms: int | None = None
    cadeia_redirect: str | None = None
    tls_valido: bool | None = None
    tls_expira_em: str | None = None
    tls_dias_restantes: int | None = None
    tem_viewport: bool | None = None
    tem_whatsapp: bool | None = None
    tem_analytics: bool | None = None
    tem_pixel: bool | None = None
    plataforma: str | None = None
    plano_gratuito: bool | None = None
    ano_copyright: int | None = None
    titulo: str | None = None
    titulo_generico: bool | None = None
    cardapio_em_pdf: bool | None = None
    html_bytes: int | None = None
    imagens_sem_lazy: int | None = None
    # PageSpeed: preenchido por psi.py, num passo separado e opcional. Fica
    # None quando nao foi coletado -- e os sinais tratam None como "nao medido".
    psi_mobile_score: int | None = None
    psi_lcp_ms: int | None = None
    psi_cls: float | None = None
    psi_coletado_em: str | None = None
    tem_link_social: dict = field(default_factory=dict)
    telefones_no_site: list = field(default_factory=list)
    emails_no_site: list = field(default_factory=list)
    # Texto visivel normalizado (sem acento, minusculo) e todos os digitos da
    # pagina. Servem para provar posse do dominio em E4 (cidade, CNPJ, CEP)
    # sem guardar o HTML inteiro.
    texto_norm: str = ""
    digitos_pagina: str = ""
    erro: str | None = None

    def dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# TLS
# --------------------------------------------------------------------------
def checar_tls(host: str, porta: int = 443, timeout: float = 8.0) -> tuple[bool | None, str | None, int | None]:
    """(valido, expira_em_iso, dias_restantes). None = nao consegui verificar."""
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, porta), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
    except ssl.SSLCertVerificationError:
        return False, None, None          # certificado invalido: isso E uma medicao
    except (socket.timeout, socket.gaierror, ConnectionError, OSError):
        return None, None, None           # nao deu para verificar: nao afirma nada
    if not cert:
        return None, None, None

    bruto = cert.get("notAfter")
    if not bruto:
        return True, None, None
    try:
        expira = datetime.strptime(bruto, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    except ValueError:
        return True, None, None
    dias = (expira - datetime.now(timezone.utc)).days
    return True, expira.date().isoformat(), dias


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
def _detectar_plataforma(html: str, headers: dict) -> tuple[str | None, bool | None]:
    alvo = html[:400_000] + " " + " ".join(f"{k}:{v}" for k, v in headers.items())
    for nome, padrao, gratuito in PLATAFORMAS:
        if re.search(padrao, alvo, re.I):
            return nome, gratuito
    return None, None


def _ano_copyright(sopa: BeautifulSoup, html: str) -> int | None:
    """Ano do rodape. Procura o padrao de copyright; se nao achar, o maior ano
    plausivel dentro do <footer>."""
    m = RE_ANO.search(html)
    if m:
        return int(m.group(1))
    rodape = sopa.find("footer")
    if rodape:
        anos = [int(a) for a in RE_ANO_SOLTO.findall(rodape.get_text(" ", strip=True))]
        if anos:
            return max(anos)
    return None


def _cardapio_em_pdf(sopa: BeautifulSoup) -> bool:
    """Link cujo texto fala de cardapio/menu e aponta para PDF ou imagem."""
    for a in sopa.find_all("a", href=True):
        href = a["href"].lower()
        texto = a.get_text(" ", strip=True)
        if RE_CARDAPIO.search(texto) or RE_CARDAPIO.search(href):
            if href.endswith((".pdf", ".jpg", ".jpeg", ".png", ".webp")):
                return True
    return False


def _links_sociais(sopa: BeautifulSoup) -> dict[str, str]:
    achados: dict[str, str] = {}
    for a in sopa.find_all("a", href=True):
        href = a["href"]
        for rede, padrao in (("instagram", "instagram.com"),
                             ("facebook", "facebook.com"),
                             ("tiktok", "tiktok.com"),
                             ("youtube", "youtube.com/")):
            if padrao in href.lower() and rede not in achados:
                achados[rede] = href
    return achados


def _telefones(html: str) -> list[str]:
    """Telefones aparentes no HTML -- serve para provar posse do dominio (E4)
    e para recuperar contato quando a fonte nao trouxe."""
    brutos = re.findall(r"(?:\+?55\s?)?\(?\d{2}\)?[\s.-]?9?\d{4}[\s.-]?\d{4}", html)
    limpos = {re.sub(r"\D", "", b)[-11:] for b in brutos}
    return sorted(t for t in limpos if len(t) >= 10)


RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
# Nome de arquivo com @ (logo@2x.png) e endereco de exemplo de tema pronto
# casam com o regex e nao sao contato de ninguem.
_EMAIL_FALSO = re.compile(
    r"\.(png|jpe?g|gif|webp|svg|css|js)$|@(example|exemplo|sentry|domain|dominio)\.|"
    r"^(seu|your|nome|name|email|usuario|user)@", re.I,
)


def _emails(html: str) -> list[str]:
    """E-mails publicados no HTML (mailto e texto). Mesmo uso dos telefones:
    so viram contato quando o site foi ACEITO como da empresa."""
    achados = {e.lower().rstrip(".") for e in RE_EMAIL.findall(html)}
    return sorted(e for e in achados if not _EMAIL_FALSO.search(e))[:10]


# --------------------------------------------------------------------------
# robots.txt
# --------------------------------------------------------------------------
_robots_cache: dict[str, RobotFileParser | None] = {}


async def _pode_buscar(cli: httpx.AsyncClient, url: str) -> bool:
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    if base not in _robots_cache:
        rp: RobotFileParser | None = RobotFileParser()
        try:
            resp = await cli.get(urljoin(base, "/robots.txt"), timeout=8.0)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            else:
                rp = None                       # sem robots.txt = liberado
        except Exception:
            rp = None
        _robots_cache[base] = rp
    rp = _robots_cache[base]
    return True if rp is None else rp.can_fetch(UA, url)


# --------------------------------------------------------------------------
# Auditoria
# --------------------------------------------------------------------------
async def buscar_pagina(cli: httpx.AsyncClient, url: str) -> httpx.Response:
    return await cli.get(url, follow_redirects=True)


async def auditar(url: str, cli: httpx.AsyncClient | None = None) -> Auditoria:
    """Audita uma URL. Nunca levanta excecao: erro vira o campo `erro` (I1)."""
    a = Auditoria(site_url=url)
    proprio = cli is None
    if cli is None:
        cli = httpx.AsyncClient(
            headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"},
            timeout=TIMEOUT, follow_redirects=True, verify=False,
        )
    try:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
            a.site_url = url

        host = urlparse(url).hostname or ""

        # DNS antes de tudo: dominio morto e dor maxima e nao adianta insistir.
        try:
            await asyncio.get_running_loop().getaddrinfo(host, None)
        except socket.gaierror:
            a.erro = "dns_nao_resolve"
            return a

        if not await _pode_buscar(cli, url):
            a.erro = "bloqueado_por_robots"
            return a

        t0 = time.monotonic()
        try:
            resp = await buscar_pagina(cli, url)
        except httpx.TimeoutException:
            a.erro = "timeout"
            return a
        except httpx.HTTPError as exc:
            a.erro = f"http_error: {type(exc).__name__}"
            return a
        a.tempo_resposta_ms = int((time.monotonic() - t0) * 1000)
        a.http_status = resp.status_code
        a.cadeia_redirect = " -> ".join(str(r.url) for r in resp.history) or None
        a.site_url = str(resp.url)

        # TLS medido no host final (depois dos redirects).
        host_final = resp.url.host or host
        if resp.url.scheme == "https":
            a.tls_valido, a.tls_expira_em, a.tls_dias_restantes = checar_tls(host_final)
        else:
            a.tls_valido = False               # serviu em http e nao redirecionou

        if resp.status_code >= 400:
            return a                           # 4xx/5xx: sinal forte, nao parseia

        conteudo = resp.content or b""
        a.html_bytes = len(conteudo)
        html = resp.text or ""
        sopa = BeautifulSoup(html, "html.parser")

        a.tem_viewport = bool(sopa.find("meta", attrs={"name": re.compile("viewport", re.I)}))
        a.tem_whatsapp = bool(RE_WHATSAPP.search(html))
        a.tem_analytics = bool(RE_ANALYTICS.search(html))
        a.tem_pixel = bool(RE_PIXEL.search(html))
        a.plataforma, a.plano_gratuito = _detectar_plataforma(html, dict(resp.headers))
        a.ano_copyright = _ano_copyright(sopa, html)

        titulo = (sopa.title.string or "").strip() if sopa.title and sopa.title.string else ""
        a.titulo = titulo[:200]
        a.titulo_generico = titulo.strip().lower() in TITULOS_GENERICOS

        a.cardapio_em_pdf = _cardapio_em_pdf(sopa)
        a.tem_link_social = _links_sociais(sopa)
        a.telefones_no_site = _telefones(html)
        a.emails_no_site = _emails(html)
        # So o texto visivel: digito de script/base64 geraria falso positivo
        # na prova de posse.
        visivel = sopa.get_text(" ", strip=True)
        a.texto_norm = sem_acento(visivel).lower()[:30_000]
        a.digitos_pagina = re.sub(r"\D", "", visivel)[:20_000]

        imagens = sopa.find_all("img")
        a.imagens_sem_lazy = sum(
            1 for img in imagens if (img.get("loading") or "").lower() != "lazy"
        )
        return a
    except Exception as exc:                   # rede e um lugar hostil
        a.erro = f"{type(exc).__name__}: {exc}"[:300]
        return a
    finally:
        if proprio:
            await cli.aclose()


async def auditar_muitos(urls: list[str], concorrencia: int = 12) -> dict[str, Auditoria]:
    """Audita em paralelo, com limite por dominio para nao martelar ninguem."""
    limite = asyncio.Semaphore(concorrencia)
    ultimo_por_host: dict[str, float] = {}
    trava = asyncio.Lock()

    async with httpx.AsyncClient(
        headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"},
        timeout=TIMEOUT, follow_redirects=True, verify=False,
    ) as cli:
        async def uma(url: str) -> tuple[str, Auditoria]:
            async with limite:
                host = urlparse(url if "://" in url else "https://" + url).hostname or ""
                async with trava:
                    agora = time.monotonic()
                    espera = 3.0 - (agora - ultimo_por_host.get(host, 0.0))
                    if espera > 0:
                        await asyncio.sleep(espera)
                    ultimo_por_host[host] = time.monotonic()
                return url, await auditar(url, cli)

        resultados = await asyncio.gather(*(uma(u) for u in urls), return_exceptions=True)

    saida: dict[str, Auditoria] = {}
    for r in resultados:
        if isinstance(r, tuple):
            saida[r[0]] = r[1]
    return saida
