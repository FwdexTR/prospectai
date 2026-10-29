"""Normalizacao de texto, telefone e metricas de similaridade.

Usado por:
  - E3 matching (casar CNPJ com ficha do Google)
  - E4 descoberta de site (provar posse do dominio)
Sem dependencia externa de proposito: rapidfuzz/lxml costumam nao ter wheel
para Python novo, e o que precisamos aqui cabe em stdlib.
"""

from __future__ import annotations

import re
import unicodedata
from math import asin, cos, radians, sin, sqrt

# Sufixos societarios e ruido que atrapalham a comparacao de nomes.
# "RESTAURANTE SABOR & CIA LTDA ME" e "Sabor & Cia" tem que casar.
_SUFIXOS = {
    "ltda", "me", "epp", "eireli", "sa", "s/a", "cia", "mei",
    "microempreendedor", "individual", "sociedade", "empresarial",
}
_RUIDO = {
    "comercio", "comercial", "servicos", "servico", "industria", "industrial",
    "de", "do", "da", "dos", "das", "e", "em", "para", "com",
    "grupo", "rede", "casa", "the",
}

_NAO_ALFANUM = re.compile(r"[^a-z0-9\s]")
_ESPACOS = re.compile(r"\s+")


def sem_acento(texto: str) -> str:
    """Remove acentos preservando as letras: 'Sertão' -> 'Sertao'."""
    decomposto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn")


def normalizar_nome(texto: str | None) -> str:
    """Forma canonica de um nome de empresa para comparacao.

    'RESTAURANTE SABOR & CIA LTDA - ME' -> 'restaurante sabor'
    """
    if not texto:
        return ""
    t = sem_acento(texto).lower()
    t = _NAO_ALFANUM.sub(" ", t)
    tokens = [tok for tok in _ESPACOS.split(t) if tok]
    tokens = [tok for tok in tokens if tok not in _SUFIXOS and tok not in _RUIDO]
    return " ".join(tokens)


def tokens(texto: str | None) -> set[str]:
    """Conjunto de tokens significativos (>= 2 chars) do nome normalizado."""
    return {tok for tok in normalizar_nome(texto).split() if len(tok) >= 2}


def token_set_ratio(a: str | None, b: str | None) -> float:
    """Similaridade 0..1 entre dois nomes, por interseccao de tokens.

    Usa o menor conjunto como denominador: 'Otica Sao Jorge' contra
    'Otica Sao Jorge Centro' da 1.0, porque um e prefixo conceitual do outro.
    Isso e proposital -- filiais e sufixos de bairro nao devem quebrar o match.
    """
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    intersecao = len(ta & tb)
    return intersecao / min(len(ta), len(tb))


# DDDs que existem de verdade. A faixa "11 a 99" aceitava 30, municipio
# nenhum -- e numero invalido no banco vira mensagem nao entregue depois.
DDDS_VALIDOS = frozenset({
    "11", "12", "13", "14", "15", "16", "17", "18", "19",          # SP
    "21", "22", "24",                                              # RJ
    "27", "28",                                                    # ES
    "31", "32", "33", "34", "35", "37", "38",                      # MG
    "41", "42", "43", "44", "45", "46",                            # PR
    "47", "48", "49",                                              # SC
    "51", "53", "54", "55",                                        # RS
    "61", "62", "64", "63", "65", "66", "67", "68", "69",          # CO/N
    "71", "73", "74", "75", "77", "79",                            # BA/SE
    "81", "87", "82", "83", "84", "85", "88", "86", "89",          # NE
    "91", "93", "94", "92", "97", "95", "96", "98", "99",          # N
})


def so_digitos(texto: str | None) -> str:
    return re.sub(r"\D", "", texto or "")


def normalizar_telefone(bruto: str | None, ddd_padrao: str | None = None) -> str | None:
    """Devolve o telefone em E.164 brasileiro (+55DDDNNNNNNNN) ou None.

    Aceita '(22) 99876-5432', '2299876543', '+55 22 99876-5432', '22998765432'.
    Rejeita o que nao tiver tamanho plausivel -- numero invalido no banco vira
    mensagem nao entregue depois, entao e melhor descartar aqui.
    """
    d = so_digitos(bruto)
    if not d:
        return None
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    if len(d) in (8, 9) and ddd_padrao:
        d = so_digitos(ddd_padrao) + d
    # Celular no formato anterior ao nono digito (2016). Pelo plano de
    # numeracao da Anatel, fixo comeca com 2-5 e celular com 6-9. A Receita
    # guarda muito cadastro antigo assim, e sem o 9 o WhatsApp nao acha ninguem.
    if len(d) == 10 and d[2] in "6789":
        d = d[:2] + "9" + d[2:]
    if len(d) not in (10, 11):
        return None
    if d[:2] not in DDDS_VALIDOS:
        return None
    # Celular tem 11 digitos e o nono digito e sempre 9.
    if len(d) == 11 and d[2] != "9":
        return None
    return "+55" + d


def eh_movel(e164: str | None) -> bool:
    """Celular brasileiro: 11 digitos apos o +55, comecando com 9."""
    d = so_digitos(e164)
    if d.startswith("55"):
        d = d[2:]
    return len(d) == 11 and d[2] == "9"


def sufixo_telefone(bruto: str | None) -> str | None:
    """Ultimos 8 digitos -- o que comparamos para nao depender de DDD/nono digito."""
    d = so_digitos(bruto)
    return d[-8:] if len(d) >= 8 else None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia em metros entre dois pontos."""
    r = 6371000.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


def slug(texto: str | None) -> str:
    """Slug para chutar dominio: 'Sabor & Cia' -> 'saborecia'."""
    return normalizar_nome(texto).replace(" ", "")
