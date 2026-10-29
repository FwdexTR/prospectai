"""E6 -- deteccao de sinais. Regras deterministicas, sem LLM.

Cada sinal e uma linha na tabela `sinal` com o eixo, o peso e a EVIDENCIA: o
dado bruto que provou o sinal. Sem evidencia a mensagem nao pode citar numero
(invariante I1) e voce nao consegue defender o que afirmou se o cliente
perguntar.

Um sinal so dispara sobre fato MEDIDO. `tem_whatsapp is None` (nao consegui
verificar) nunca vira "nao tem WhatsApp".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .audit import Auditoria
from .discover import Descoberta
from .ramos import CATALOGO

ANO_ATUAL = date.today().year


@dataclass
class Sinal:
    tipo: str
    eixo: str                  # dor | dinheiro | timing
    peso: float
    valor: str | None = None
    evidencia: dict[str, Any] | None = None


# --------------------------------------------------------------------------
# EIXO DOR
# --------------------------------------------------------------------------
def sinais_dor(a: Auditoria | None, d: Descoberta) -> list[Sinal]:
    s: list[Sinal] = []

    # Sem site / site que e perfil: os dois maiores, e sao exclusivos entre si.
    #
    # PORTAO CRITICO (invariante I1): se o dominio nao foi ACEITO (confianca
    # < 2), tudo que medimos nele foi medido no site de OUTRA empresa. Nenhum
    # sinal derivado da auditoria pode sair daqui -- senao o lead vira
    # "SEM_SITE + site lento", que e contraditorio e, pior, falso.
    if d.confianca < 2:
        if d.agregador:
            s.append(Sinal("SITE_E_PERFIL", "dor", 35,
                           d.agregador, {"perfil": d.agregador}))
        else:
            s.append(Sinal("SEM_SITE", "dor", 40, None,
                           {"candidatos_testados": d.candidatos_testados[:5]}))
        return s

    if a is None:
        return s

    if a.erro == "dns_nao_resolve":
        s.append(Sinal("SITE_MORTO", "dor", 40, a.site_url,
                       {"url": a.site_url, "erro": "DNS nao resolve"}))
        return s                                   # nao ha mais o que medir

    if a.http_status is not None and a.http_status >= 400:
        s.append(Sinal("SITE_QUEBRADO", "dor", 40, str(a.http_status),
                       {"url": a.site_url, "status": a.http_status}))
        return s

    if a.tls_valido is False:
        s.append(Sinal("SEM_HTTPS", "dor", 20, None,
                       {"url": a.site_url, "tls_valido": False}))

    if a.tem_viewport is False:
        s.append(Sinal("NAO_RESPONSIVO", "dor", 25, None,
                       {"detalhe": "sem <meta name=viewport>"}))

    if a.psi_mobile_score is not None and a.psi_mobile_score < 50:
        s.append(Sinal("LENTO", "dor", 25, str(a.psi_mobile_score),
                       {"psi_mobile": a.psi_mobile_score}))

    if a.psi_lcp_ms is not None and a.psi_lcp_ms > 4000:
        s.append(Sinal("LCP_RUIM", "dor", 20, f"{a.psi_lcp_ms/1000:.1f}s",
                       {"lcp_ms": a.psi_lcp_ms}))

    # Sem PSI ainda da para medir lentidao pelo tempo de resposta do servidor.
    if a.psi_mobile_score is None and (a.tempo_resposta_ms or 0) > 3000:
        s.append(Sinal("RESPOSTA_LENTA", "dor", 15, f"{a.tempo_resposta_ms}ms",
                       {"tempo_resposta_ms": a.tempo_resposta_ms}))

    if a.ano_copyright is not None and a.ano_copyright <= ANO_ATUAL - 2:
        s.append(Sinal("SITE_ABANDONADO", "dor", 20, str(a.ano_copyright),
                       {"ano_copyright": a.ano_copyright, "ano_atual": ANO_ATUAL}))

    if a.plano_gratuito:
        s.append(Sinal("CONSTRUTOR_GRATUITO", "dor", 15, a.plataforma,
                       {"plataforma": a.plataforma}))

    if a.cardapio_em_pdf:
        s.append(Sinal("CARDAPIO_EM_PDF", "dor", 15, None,
                       {"detalhe": "cardapio/catalogo servido como PDF ou imagem"}))

    if a.titulo_generico:
        s.append(Sinal("TITULO_GENERICO", "dor", 10, a.titulo,
                       {"titulo": a.titulo}))

    if a.tem_whatsapp is False:
        s.append(Sinal("SEM_WHATSAPP", "dor", 10, None,
                       {"detalhe": "nenhum link wa.me na pagina"}))

    if a.tem_analytics is False and a.tem_pixel is False:
        s.append(Sinal("SEM_MEDICAO", "dor", 10, None,
                       {"detalhe": "sem Analytics e sem Pixel"}))

    # Pagina pesada: numero concreto e facil de explicar para leigo.
    if a.html_bytes and a.html_bytes > 500_000:
        s.append(Sinal("PAGINA_PESADA", "dor", 10, f"{a.html_bytes/1024:.0f}KB",
                       {"html_bytes": a.html_bytes}))

    if a.imagens_sem_lazy is not None and a.imagens_sem_lazy >= 40:
        s.append(Sinal("IMAGENS_SEM_LAZY", "dor", 10, str(a.imagens_sem_lazy),
                       {"imagens_sem_lazy": a.imagens_sem_lazy}))

    return s


# --------------------------------------------------------------------------
# EIXO DINHEIRO
# --------------------------------------------------------------------------
def sinais_dinheiro(empresa: dict, payload: dict) -> tuple[list[Sinal], bool]:
    """Devolve (sinais, houve_medicao).

    `houve_medicao=False` significa que a fonte nao trouxe NADA sobre
    capacidade de pagar -- tipico do OpenStreetMap. Nesse caso o score usa
    baseline neutro e a faixa fica limitada a B (ver score.py). Fingir que
    dinheiro=0 zeraria todo mundo; fingir que e alto seria mentira.
    """
    s: list[Sinal] = []
    mediu = False

    reviews = payload.get("userRatingCount")
    if reviews is not None:
        mediu = True
        peso = min(reviews / 2.0, 40.0)
        if peso > 0:
            s.append(Sinal("VOLUME_REVIEWS", "dinheiro", peso, str(reviews),
                           {"num_reviews": reviews, "nota": payload.get("rating")}))

    if payload.get("temHorario"):
        mediu = True
        fotos = payload.get("numFotos") or 0
        if fotos >= 5:
            s.append(Sinal("PERFIL_COMPLETO_GOOGLE", "dinheiro", 5, str(fotos),
                           {"num_fotos": fotos, "tem_horario": True}))

    # --- vindos da Receita Federal (nulos ate existir o import do dump) ----
    porte = (empresa.get("porte") or "").upper()
    if porte:
        mediu = True
        if "MEI" not in porte:
            s.append(Sinal("NAO_MEI", "dinheiro", 15, porte, {"porte": porte}))

    capital = empresa.get("capital_social")
    if capital is not None:
        mediu = True
        if capital >= 50_000:
            s.append(Sinal("CAPITAL_RELEVANTE", "dinheiro", 10, f"R$ {capital:,.0f}",
                           {"capital_social": capital}))

    # Ramo de ticket alto: nao e medicao da empresa, e do segmento. So conta
    # como bonus quando ja houve alguma medicao real.
    ramo = CATALOGO.get(empresa.get("ramo") or "")
    if ramo and ramo.peso_cnae > 0:
        s.append(Sinal("RAMO_TICKET_ALTO", "dinheiro", ramo.peso_cnae, ramo.chave,
                       {"ramo": ramo.rotulo}))

    return s, mediu


# --------------------------------------------------------------------------
# EIXO TIMING (multiplicadores; o maior vence, teto 1.5)
# --------------------------------------------------------------------------
def sinais_timing(empresa: dict, a: Auditoria | None) -> list[Sinal]:
    s: list[Sinal] = []

    abertura = empresa.get("data_abertura")
    if abertura:
        try:
            d0 = datetime.fromisoformat(str(abertura)[:10]).date()
            meses = (date.today() - d0).days / 30.4
            if 3 <= meses <= 12:
                s.append(Sinal("RECEM_ABERTA", "timing", 1.4, f"{meses:.0f} meses",
                               {"data_abertura": str(abertura)}))
        except ValueError:
            pass

    if a and a.tls_dias_restantes is not None and 0 < a.tls_dias_restantes < 30:
        s.append(Sinal("CERTIFICADO_VENCENDO", "timing", 1.2,
                       f"{a.tls_dias_restantes} dias",
                       {"tls_dias_restantes": a.tls_dias_restantes}))

    return s


def detectar(
    empresa: dict, auditoria: Auditoria | None, descoberta: Descoberta, payload: dict
) -> tuple[list[Sinal], bool]:
    """Todos os sinais de uma empresa. Devolve (sinais, dinheiro_medido)."""
    dor = sinais_dor(auditoria, descoberta)
    dinheiro, mediu = sinais_dinheiro(empresa, payload or {})
    timing = sinais_timing(empresa, auditoria)
    return dor + dinheiro + timing, mediu
