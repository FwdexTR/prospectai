"""Acesso ao banco. SQLite agora; o SQL foi escrito para portar a Postgres."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

RAIZ = Path(__file__).resolve().parent.parent.parent   # .../prospectai
CAMINHO_BANCO = RAIZ / "dados" / "prospectai.db"
SCHEMA = Path(__file__).resolve().parent / "schema.sql"


def conectar(caminho: Path | str | None = None) -> sqlite3.Connection:
    destino = Path(caminho) if caminho else CAMINHO_BANCO
    destino.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(destino, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")    # leitura concorrente com a API
    return con


def inicializar(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    _migrar(con)
    con.commit()


# Colunas que entraram depois da primeira versao. `CREATE TABLE IF NOT EXISTS`
# nao altera tabela existente, entao banco antigo precisa do ALTER.
_COLUNAS_NOVAS: dict[str, tuple[tuple[str, str], ...]] = {
    "empresa": (("revelada_em", "TEXT"),),
    "contato": (("validacao", "TEXT"),),
    "lead": (("valor_estimado", "REAL"),),
    "mensagem": (
        ("canal", "TEXT NOT NULL DEFAULT 'whatsapp'"),
        ("assunto", "TEXT"),
        ("espera_dias", "INTEGER"),
        ("inscricao_id", "INTEGER REFERENCES inscricao(id)"),
    ),
}


def _migrar(con: sqlite3.Connection) -> None:
    for tabela, colunas in _COLUNAS_NOVAS.items():
        existentes = {l[1] for l in con.execute(f"PRAGMA table_info({tabela})")}
        for nome, tipo in colunas:
            if nome not in existentes:
                con.execute(f"ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}")


def inserir(con: sqlite3.Connection, tabela: str, dados: dict[str, Any]) -> int:
    """INSERT simples; serializa dict/list para JSON automaticamente."""
    limpos = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
              for k, v in dados.items() if v is not None}
    colunas = ", ".join(limpos)
    marcas = ", ".join("?" for _ in limpos)
    cur = con.execute(
        f"INSERT INTO {tabela} ({colunas}) VALUES ({marcas})", tuple(limpos.values())
    )
    return int(cur.lastrowid or 0)


def upsert_empresa(con: sqlite3.Connection, dados: dict[str, Any]) -> int:
    """Insere a empresa ou devolve o id da que ja existe (mesma fonte+fonte_id).

    Coleta e idempotente: rodar duas vezes a mesma campanha nao duplica nada.
    """
    fonte, fonte_id = dados.get("fonte"), dados.get("fonte_id")
    if fonte_id:
        linha = con.execute(
            "SELECT id FROM empresa WHERE fonte = ? AND fonte_id = ?", (fonte, fonte_id)
        ).fetchone()
        if linha:
            return int(linha["id"])
    return inserir(con, "empresa", dados)


def buscar(con: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return con.execute(sql, tuple(params)).fetchall()


def um(con: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return con.execute(sql, tuple(params)).fetchone()


def estado_sistema(con: sqlite3.Connection, chave: str) -> str | None:
    linha = um(con, "SELECT valor FROM sistema_estado WHERE chave = ?", (chave,))
    return linha["valor"] if linha else None


def definir_estado(con: sqlite3.Connection, chave: str, valor: str) -> None:
    con.execute(
        "INSERT INTO sistema_estado (chave, valor, atualizado_em) "
        "VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor, "
        "atualizado_em = excluded.atualizado_em",
        (chave, valor),
    )
    con.commit()
