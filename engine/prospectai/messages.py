"""E9 -- geracao da mensagem e o validador que a segura.

O gerador padrao e DETERMINISTICO: monta a frase a partir do sintoma medido.
Isso nao e limitacao, e desenho -- o que faz a mensagem funcionar e o fato
especifico ("o site levou 8,4s"), nao a prosa. Um adaptador de LLM pode ser
plugado depois (`gerar_com_llm`), mas passa pelo MESMO validador.

O validador e a materializacao da invariante I1: mensagem que cita fato fora de
`fatos_usados` nao sai. Nunca.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .normalize import sem_acento
from .ramos import CATALOGO
from .signals import Sinal

LIMITE_CARACTERES = 480

# Ordem de preferencia do sintoma principal: o que rende a frase mais concreta
# vem primeiro, nao necessariamente o de maior peso.
PRIORIDADE_SINTOMA = [
    "SITE_MORTO", "SITE_QUEBRADO", "SEM_HTTPS", "LENTO", "LCP_RUIM",
    "RESPOSTA_LENTA", "NAO_RESPONSIVO", "SITE_ABANDONADO", "CARDAPIO_EM_PDF",
    "CONSTRUTOR_GRATUITO", "PAGINA_PESADA", "IMAGENS_SEM_LAZY",
    "TITULO_GENERICO", "SEM_WHATSAPP", "SEM_MEDICAO",
    "SITE_E_PERFIL", "SEM_SITE",
]

# Jargao de vendedor. Se aparecer, a mensagem e reprovada.
PROIBIDAS = [
    "parceria", "oportunidade unica", "oportunidade única",
    "estou entrando em contato", "gostaria de apresentar", "somos referencia",
    "somos referência", "nossa empresa oferece", "prezado", "prezada",
    "venho por meio deste", "espero que esteja bem", "tudo bem com voce",
    "solucoes inovadoras", "soluções inovadoras", "alavancar",
]

# Promessa de resultado: nao temos como garantir, entao nao promete.
PROMESSAS = [
    "vai dobrar", "vai triplicar", "garanto", "garantia de resultado",
    "com certeza vai", "aumenta suas vendas em", "resultado garantido",
]

RE_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF⬀-⯿]"
)


@dataclass
class Remetente:
    nome: str
    cidade: str
    whatsapp: str | None = None
    profissao: str = "desenvolvedor"


@dataclass
class Mensagem:
    texto: str
    toque: int
    template_id: str
    fatos_usados: dict[str, Any] = field(default_factory=dict)
    gerada_por: str = "template"
    validador_ok: bool = False
    validador_erros: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Frases por sintoma. Cada uma devolve (frase, fatos citados).
# --------------------------------------------------------------------------
def _frase(sinal: Sinal, nome: str, ev: dict) -> tuple[str, dict]:
    """Frase do sintoma, em portugues correto e acentuado.

    Isto vai direto para o WhatsApp de um cliente: texto sem acento chega com
    cara de disparo automatico e derruba a resposta antes de ser lido.
    """
    t = sinal.tipo

    if t == "SITE_MORTO":
        return (f"o endereço {ev.get('url')} não está no ar — o domínio não responde",
                {"url": ev.get("url")})
    if t == "SITE_QUEBRADO":
        return (f"o site de vocês está retornando erro {ev.get('status')} em vez de abrir",
                {"status": ev.get("status")})
    if t == "SEM_HTTPS":
        return ("o site está sem certificado de segurança válido, então o navegador "
                "mostra o aviso de \"Não seguro\" para quem entra",
                {"tls_valido": False})
    if t == "LENTO":
        return (f"o site tirou {ev.get('psi_mobile')}/100 no teste de velocidade do "
                f"Google no celular", {"psi_mobile": ev.get("psi_mobile")})
    if t == "LCP_RUIM":
        return (f"a página leva {ev.get('lcp_ms', 0)/1000:.1f}s para mostrar o conteúdo "
                f"principal no celular", {"lcp_ms": ev.get("lcp_ms")})
    if t == "RESPOSTA_LENTA":
        return (f"o site levou {ev.get('tempo_resposta_ms', 0)/1000:.1f}s só para "
                f"responder no meu teste", {"tempo_resposta_ms": ev.get("tempo_resposta_ms")})
    if t == "NAO_RESPONSIVO":
        return ("abri o site no celular e ele não está adaptado para tela pequena",
                {"tem_viewport": False})
    if t == "SITE_ABANDONADO":
        return (f"o rodapé do site ainda marca {ev.get('ano_copyright')}",
                {"ano_copyright": ev.get("ano_copyright")})
    if t == "CARDAPIO_EM_PDF":
        return ("o cardápio abre como PDF, que no celular fica difícil de ler e de "
                "pedir", {"cardapio_em_pdf": True})
    if t == "CONSTRUTOR_GRATUITO":
        return (f"o site está num plano gratuito do {ev.get('plataforma')}, com marca "
                f"d'água de terceiro na página de vocês", {"plataforma": ev.get("plataforma")})
    if t == "PAGINA_PESADA":
        return (f"a home carrega {ev.get('html_bytes', 0)/1024:.0f}KB de código",
                {"html_bytes": ev.get("html_bytes")})
    if t == "IMAGENS_SEM_LAZY":
        return (f"a home carrega {ev.get('imagens_sem_lazy')} imagens de uma vez, sem "
                f"otimização", {"imagens_sem_lazy": ev.get("imagens_sem_lazy")})
    if t == "TITULO_GENERICO":
        return (f"o título que aparece no Google está como \"{ev.get('titulo')}\", "
                f"em vez do nome de vocês", {"titulo": ev.get("titulo")})
    if t == "SEM_WHATSAPP":
        return ("o site não tem botão de WhatsApp — quem entra pelo celular precisa "
                "copiar o número na mão", {"tem_whatsapp": False})
    if t == "SEM_MEDICAO":
        return ("o site não tem nenhuma ferramenta de medição instalada, então não dá "
                "para saber quantas pessoas entram", {"tem_analytics": False})
    if t == "SITE_E_PERFIL":
        return (f"o link de vocês leva para o {ev.get('perfil')} em vez de um site próprio",
                {"perfil": ev.get("perfil")})
    if t == "SEM_SITE":
        n = len(ev.get("candidatos_testados") or [])
        if n:
            return (f"procurei o site de vocês e não achei — testei {n} endereços "
                    f"prováveis e nenhum é de vocês", {"candidatos_testados": n})
        return ("procurei um site oficial de vocês e não encontrei nenhum",
                {"sem_site": True})
    return ("", {})


def escolher_sintomas(sinais: list[Sinal]) -> list[Sinal]:
    """Sintomas de dor, na ordem em que rendem frase concreta."""
    dores = [s for s in sinais if s.eixo == "dor"]
    ordem = {t: i for i, t in enumerate(PRIORIDADE_SINTOMA)}
    return sorted(dores, key=lambda s: ordem.get(s.tipo, 999))


# --------------------------------------------------------------------------
# Validador -- as regras duras da spec
# --------------------------------------------------------------------------
def validar(texto: str, fatos: dict, toque: int, sintomas: list[str]) -> list[str]:
    erros: list[str] = []
    # As regras sao escritas sem acento; o texto que vai para o cliente E
    # acentuado. Comparar na forma sem acento evita que "aqui é o" falhe numa
    # regra escrita como "aqui e o".
    baixo = sem_acento(texto).lower()

    if len(texto) > LIMITE_CARACTERES:
        erros.append(f"R1: {len(texto)} caracteres (limite {LIMITE_CARACTERES})")

    # R2: precisa citar ao menos um fato medido; se o fato tem numero, o numero
    # precisa aparecer no texto.
    if not fatos and toque == 1:
        erros.append("R2: nenhum fato medido citado")
    else:
        numericos = [v for v in fatos.values() if isinstance(v, (int, float))
                     and not isinstance(v, bool)]
        if numericos:
            digitos_texto = set(re.findall(r"\d+", texto))
            achou = any(
                str(int(n)) in digitos_texto
                or any(str(int(n))[:2] in d for d in digitos_texto)
                or f"{n/1000:.1f}".replace(".", ",") in texto
                or f"{n/1024:.0f}" in texto
                for n in numericos
            )
            if not achou:
                erros.append("R2: fato numerico medido nao aparece no texto")

    for termo in PROIBIDAS:
        if termo in baixo:
            erros.append(f"R3: jargao proibido ({termo!r})")
    for termo in PROMESSAS:
        if termo in baixo:
            erros.append(f"R4: promessa de resultado ({termo!r})")

    if len(RE_EMOJI.findall(texto)) > 1:
        erros.append("R5: mais de 1 emoji")

    if "?" not in texto and toque < 3:
        erros.append("R6: nao termina em pergunta")

    if not re.search(r"\b(aqui e o|aqui e a|meu nome e|sou o|sou a)\b", baixo):
        erros.append("R7: remetente nao se identifica")

    # R8/R9: coerencia sintoma x texto. Se nao tem site, nao pode falar de
    # lentidao do site.
    if "SEM_SITE" in sintomas:
        for proibido in ("site de voces esta", "seu site esta lento", "velocidade do site"):
            if proibido in baixo:
                erros.append(f"R9: incoerente com SEM_SITE ({proibido!r})")

    if toque == 1 and not re.search(r"nao mando mais|nao te incomodo mais|so falar", baixo):
        erros.append("R10: toque 1 sem frase de opt-out")

    return erros


# --------------------------------------------------------------------------
# Gerador deterministico
# --------------------------------------------------------------------------
def _limpar(texto: str) -> str:
    return re.sub(r"[ \t]+", " ", texto).strip()


def gerar(
    nome: str,
    ramo: str | None,
    sinais: list[Sinal],
    remetente: Remetente,
    prova_url: str | None,
    toque: int = 1,
) -> Mensagem:
    sintomas = escolher_sintomas(sinais)
    if not sintomas:
        return Mensagem("", toque, "sem_sintoma", {}, "template", False,
                        ["sem sintoma de dor medido"])

    principal = sintomas[0]
    frase, fatos = _frase(principal, nome, principal.evidencia or {})
    r = CATALOGO.get(ramo or "")
    link = f"\n\nMontei uma análise rápida disso: {prova_url}" if prova_url else ""

    if toque == 1:
        texto = _limpar(f"""Oi! Aqui é o {remetente.nome}, sou {remetente.profissao} \
aqui de {remetente.cidade}.

Dei uma olhada em {nome} e {frase}.{link}

Se fizer sentido, te explico em dois minutos como resolve — posso mandar? \
Se não quiser receber contato, é só falar que eu não mando mais.""")
        template_id = f"t1_{principal.tipo.lower()}"

    elif toque == 2:
        # O toque 2 tem que trazer angulo NOVO. Quando so ha um sintoma medido
        # (tipico de quem nao tem site: nao ha o que auditar), repetir a mesma
        # frase soa como cobranca. Nesse caso o angulo novo vem do gancho do
        # ramo -- a dor tipica do segmento, que e verdadeira sem ser medida
        # naquela empresa, e por isso entra como contexto e nao como afirmacao.
        secundario = sintomas[1] if len(sintomas) > 1 else None
        if secundario:
            frase2, fatos2 = _frase(secundario, nome, secundario.evidencia or {})
            fatos = {**fatos, **fatos2}
            miolo = f"Uma coisa que esqueci de citar: {frase2}."
        elif r:
            # Angulo do segmento: a mensagem deixa de citar a medicao e passa a
            # citar o ramo. `fatos` acompanha -- e a lista do que ESTA mensagem
            # afirma, nao um historico. Herdar o numero do toque 1 fazia o
            # validador cobrar um numero que o texto nao usa mais.
            miolo = (f"Pensando melhor no caso de vocês: o que mais vejo em "
                     f"{r.rotulo.lower()} por aqui é {r.gancho}.")
            fatos = {"gancho_do_ramo": r.chave}
        else:
            miolo = f"Voltando ao que comentei: {frase}."

        oferta = f" No caso de vocês isso seria {r.oferta.lower()}." if r else ""
        texto = _limpar(f"""Oi {nome}, aqui é o {remetente.nome} de novo.

{miolo}{oferta}

Quer que eu monte um exemplo e te mostre funcionando?""")
        template_id = f"t2_{(secundario or principal).tipo.lower()}"

    else:
        texto = _limpar(f"""Oi! Aqui é o {remetente.nome} — última mensagem, prometo, \
não quero incomodar.

Deixo a análise salva caso um dia seja útil{': ' + prova_url if prova_url else ''}.
Se precisar de {r.oferta.lower() if r else 'site ou sistema'}, é só chamar.

Sucesso para vocês!""")
        fatos = {"encerramento": True}      # nao afirma nada medido
        template_id = "t3_encerramento"

    erros = validar(texto, fatos, toque, [s.tipo for s in sintomas])
    return Mensagem(
        texto=texto, toque=toque, template_id=template_id, fatos_usados=fatos,
        gerada_por="template", validador_ok=not erros, validador_erros=erros,
    )


def gerar_sequencia(
    nome: str, ramo: str | None, sinais: list[Sinal], remetente: Remetente,
    prova_url: str | None,
) -> list[Mensagem]:
    """Os tres toques de uma vez (E9): assim o toque 2 nunca repete o argumento
    do toque 1, e voce revisa a sequencia inteira antes de enviar."""
    return [gerar(nome, ramo, sinais, remetente, prova_url, t) for t in (1, 2, 3)]


# Adaptador de LLM: mesma assinatura, mesmo validador. Ativa quando houver
# chave configurada; ate la o determinístico e o caminho principal.
GeradorLLM = Callable[[str, str | None, list[Sinal], Remetente, str | None, int], str]
_gerador_llm: GeradorLLM | None = None


def registrar_llm(fn: GeradorLLM) -> None:
    global _gerador_llm
    _gerador_llm = fn
