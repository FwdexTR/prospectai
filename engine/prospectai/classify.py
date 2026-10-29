"""E11 -- classificacao da resposta.

Regex ANTES de qualquer coisa, e isso e proposital: falso negativo em OPT_OUT e
inaceitavel. Perder um lead por excesso de cuidado nao custa nada; insistir com
quem pediu para parar custa sua reputacao na cidade -- e, em cidade media, sua
reputacao e o seu canal de vendas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .normalize import sem_acento

OPT_OUT = "OPT_OUT"
INTERESSE = "INTERESSE"
DUVIDA = "DUVIDA"
NEGATIVO = "NEGATIVO"
FORA_DO_ALVO = "FORA_DO_ALVO"
AUTOMATICO = "AUTOMATICO"

# Generoso de proposito. Na duvida, marca opt-out.
RE_OPT_OUT = re.compile(
    r"n[ao]o (tenho|temos|ha|há) interesse"
    r"|n[ao]o (quero|queremos|desejo|preciso|precisamos)"
    r"|\b(pare|para) de (mandar|enviar|me mandar)"
    r"|\bnao me (mande|envie|procure|perturbe|incomode)"
    r"|\b(remov\w*|descadastr\w*|sair da lista|me tira|me tire)"
    r"|\bbloquear\b|\bdenunciar\b|\bspam\b"
    r"|\bnao insista\b|\bpara com isso\b",
    re.I,
)

RE_INTERESSE = re.compile(
    r"\bquanto (custa|fica|sai|seria)\b|\bqual (o )?(valor|preco)\b"
    r"|\bme manda\b|\bmanda (ai|o|a|mais)\b|\bpode mandar\b|\bmande\b"
    r"|\btenho interesse\b|\bme interessa\b|\bgostei\b"
    r"|\bvamos conversar\b|\bpodemos (conversar|falar)\b"
    r"|\bquero (saber|ver|entender)\b|\bmarca(r)? (uma )?(reuniao|conversa)\b"
    r"|\bque horas\b|\bpode (vir|passar) (aqui|aqui)\b",
    re.I,
)

RE_NEGATIVO = re.compile(
    r"\bja (tenho|temos)\b|\bja possu\w+\b|\bja fiz\b|\bja contratei\b"
    r"|\bnao precisamos\b|\bnao precisa\b|\bnao e o momento\b"
    r"|\btemos (agencia|fornecedor|alguem)\b|\bja tem quem (faca|cuida)\b"
    r"|\bmais para frente\b|\bdepois eu (vejo|falo)\b",
    re.I,
)

RE_FORA_DO_ALVO = re.compile(
    r"\bnumero errado\b|\bengano\b|\bnao sou\b|\bnao e (aqui|essa empresa)\b"
    r"|\bfechamos\b|\bencerramos\b|\bnao trabalho mais\b|\bvendi (a|o)\b",
    re.I,
)

RE_AUTOMATICO = re.compile(
    r"\bmensagem automatica\b|\bresposta automatica\b|\bestamos fora do (ar|expediente)\b"
    r"|\bnosso horario de (atendimento|funcionamento)\b"
    r"|\bem breve (responderemos|retornaremos)\b|\bobrigado pelo contato\b.*\bretornaremos\b"
    r"|\bdigite \d\b|\bescolha uma (opcao|das opcoes)\b",
    re.I,
)


@dataclass
class Classificacao:
    classe: str
    confianca: float
    por: str                      # regex | heuristica
    trecho: str | None = None     # o que disparou, para voce conferir


def classificar(texto: str) -> Classificacao:
    """Classe da resposta. Nunca levanta excecao; texto vazio vira DUVIDA."""
    if not texto or not texto.strip():
        return Classificacao(DUVIDA, 0.0, "regex")

    alvo = sem_acento(texto).lower()

    # Ordem importa: opt-out primeiro, sempre.
    for regra, classe, conf in (
        (RE_OPT_OUT, OPT_OUT, 0.95),
        (RE_AUTOMATICO, AUTOMATICO, 0.85),
        (RE_FORA_DO_ALVO, FORA_DO_ALVO, 0.85),
        (RE_INTERESSE, INTERESSE, 0.85),
        (RE_NEGATIVO, NEGATIVO, 0.80),
    ):
        m = regra.search(alvo)
        if m:
            return Classificacao(classe, conf, "regex", m.group(0))

    # Heuristica fraca: pergunta sem sinal claro costuma ser duvida real.
    if "?" in texto:
        return Classificacao(DUVIDA, 0.5, "heuristica")
    if len(texto.strip()) <= 4:
        # "ok", "blz", "vlw" -- reconhecimento, nao interesse.
        return Classificacao(DUVIDA, 0.4, "heuristica")
    return Classificacao(DUVIDA, 0.3, "heuristica")


def conta_como_resposta(c: Classificacao) -> bool:
    """AUTOMATICO nao conta: nao houve humano do outro lado, entao o follow-up
    segue e o circuit breaker de taxa de resposta nao e afetado."""
    return c.classe != AUTOMATICO
