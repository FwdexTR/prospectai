"""E6 -- pontuacao.

    score = raiz(dor) x raiz(dinheiro) x timing

O produto (e nao a soma) e a decisao central: empresa com dor 100 e dinheiro 0
TEM que dar zero. Numa soma ponderada ela apareceria no topo e voce gastaria
semanas conversando com quem nao pode pagar -- que e o erro classico deste tipo
de sistema.
"""

from __future__ import annotations

from dataclasses import dataclass

from .signals import Sinal

VERSAO_MODELO = "v1"

# Usado quando a fonte nao mediu NADA sobre capacidade de pagar (modo OSM).
# Nao e um chute sobre a empresa: e a admissao de que o eixo esta ausente.
DINHEIRO_NEUTRO = 25.0
TETO_TIMING = 1.5


@dataclass
class Resultado:
    dor: float
    dinheiro: float
    timing: float
    total: float
    faixa: str
    dinheiro_medido: bool
    versao_modelo: str = VERSAO_MODELO

    @property
    def provisorio(self) -> bool:
        """Score sem eixo dinheiro medido: ordena, mas nao prioriza de verdade."""
        return not self.dinheiro_medido


def _satura(valor: float, teto: float = 100.0) -> float:
    return max(0.0, min(valor, teto))


def calcular(sinais: list[Sinal], dinheiro_medido: bool) -> Resultado:
    dor = _satura(sum(s.peso for s in sinais if s.eixo == "dor"))

    if dinheiro_medido:
        dinheiro = _satura(sum(s.peso for s in sinais if s.eixo == "dinheiro"))
    else:
        dinheiro = DINHEIRO_NEUTRO

    multiplicadores = [s.peso for s in sinais if s.eixo == "timing"]
    timing = min(max(multiplicadores) if multiplicadores else 1.0, TETO_TIMING)

    if dinheiro_medido:
        # Escala 0..150. O produto e o ponto: dor 100 x dinheiro 0 = 0.
        total = (dor ** 0.5) * (dinheiro ** 0.5) * timing
        faixa = "A" if total >= 75 else "B" if total >= 50 else "C" if total >= 30 else "D"
    else:
        # Sem eixo dinheiro, aplicar a raiz sobre uma constante so comprimiria
        # a escala (teto viraria 50) e jogaria todo mundo para a faixa C.
        # Ordenamos pela dor pura, na mesma escala 0..100, e deixamos claro que
        # o score e provisorio.
        total = dor * timing
        # Ninguem entra na faixa A sem saber se pode pagar: faixa A significa
        # "para este eu faco protótipo de graca", e isso custa 4 horas suas.
        faixa = "B" if total >= 40 else "C" if total >= 25 else "D"

    return Resultado(
        dor=round(dor, 1),
        dinheiro=round(dinheiro, 1),
        timing=round(timing, 2),
        total=round(total, 1),
        faixa=faixa,
        dinheiro_medido=dinheiro_medido,
    )


def explicar(sinais: list[Sinal], r: Resultado) -> str:
    """Uma linha legivel do porque deste score -- vai para a tela e o relatorio."""
    dores = [s.tipo for s in sinais if s.eixo == "dor"]
    partes = [f"dor {r.dor:.0f} ({', '.join(dores[:3]) or 'nenhuma'})"]
    partes.append(
        f"dinheiro {r.dinheiro:.0f}" + ("" if r.dinheiro_medido else " [nao medido]")
    )
    if r.timing > 1.0:
        partes.append(f"timing x{r.timing}")
    return " | ".join(partes)
