"""Registro das tarefas em segundo plano (campanha, enriquecimento).

A interface acompanha o progresso por /api/execucoes/{id}. Fica num modulo
proprio para api.py e base_api.py usarem o mesmo registro sem import circular.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Callable

EXECUCOES: dict[str, dict[str, Any]] = {}
TRAVA = threading.Lock()


def nova(tipo: str) -> str:
    execucao_id = uuid.uuid4().hex[:12]
    with TRAVA:
        EXECUCOES[execucao_id] = {"tipo": tipo, "estado": "rodando", "etapa": "iniciando",
                                  "feito": 0, "total": 1}
    return execucao_id


def atualizar(execucao_id: str, **campos: Any) -> None:
    with TRAVA:
        EXECUCOES[execucao_id].update(campos)


def progresso_de(execucao_id: str) -> Callable[[str, int, int], None]:
    def progresso(etapa: str, feito: int, total: int) -> None:
        atualizar(execucao_id, etapa=etapa, feito=feito, total=total)
    return progresso


def ver(execucao_id: str) -> dict[str, Any] | None:
    with TRAVA:
        est = EXECUCOES.get(execucao_id)
        return dict(est) if est else None
