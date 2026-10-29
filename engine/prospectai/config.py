"""Configuracao. Le prospectai/.env sem depender de biblioteca externa."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent
ARQUIVO_ENV = RAIZ / ".env"


def carregar_env(caminho: Path | None = None) -> None:
    alvo = caminho or ARQUIVO_ENV
    if not alvo.exists():
        return
    for linha in alvo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        os.environ.setdefault(chave.strip(), valor.strip().strip("'\""))


@dataclass
class Config:
    remetente_nome: str
    remetente_cidade: str
    remetente_whatsapp: str | None
    remetente_profissao: str
    base_url: str
    places_key: str | None

    @property
    def tem_places(self) -> bool:
        return bool(self.places_key)


def carregar() -> Config:
    carregar_env()
    return Config(
        remetente_nome=os.environ.get("REMETENTE_NOME", "").strip() or "(configure REMETENTE_NOME)",
        remetente_cidade=os.environ.get("REMETENTE_CIDADE", "").strip() or "sua cidade",
        remetente_whatsapp=os.environ.get("REMETENTE_WHATSAPP", "").strip() or None,
        remetente_profissao=os.environ.get("REMETENTE_PROFISSAO", "desenvolvedor").strip(),
        base_url=os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/"),
        places_key=os.environ.get("GOOGLE_PLACES_API_KEY", "").strip() or None,
    )
