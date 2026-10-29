"""Base de empresas (CNPJ) -- a camada de dados da prospeccao B2B.

E o que Apollo e Leads2B chamam de "database": milhoes de estabelecimentos
pesquisaveis por filtro. Fica num arquivo DuckDB separado do SQLite:

  - SQLite (prospectai.db)    -> o que VOCE faz: listas, CRM, cadencias, envios
  - DuckDB (base_cnpj.duckdb) -> o que EXISTE: o cadastro da Receita, so leitura

Separar evita duas coisas: o import mensal (dezenas de milhoes de linhas)
travar o CRM, e uma agregacao pesada da analise de mercado disputar lock com a
fila de envio.

I7 vale aqui tambem: a tabela de socios nao existe nesta base.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Iterator

import duckdb

from .db import RAIZ
from .normalize import sem_acento, so_digitos

ARQ_BASE = RAIZ / "dados" / "base_cnpj.duckdb"
ARQ_DEMO = RAIZ / "dados" / "base_demo.duckdb"

SITUACOES = {"01": "Nula", "02": "Ativa", "03": "Suspensa", "04": "Inapta", "08": "Baixada"}
PORTES = {"00": "Não informado", "01": "Microempresa", "03": "Pequeno porte", "05": "Demais"}

UFS = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia",
    "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás",
    "MA": "Maranhão", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul",
    "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná",
    "PE": "Pernambuco", "PI": "Piauí", "RJ": "Rio de Janeiro",
    "RN": "Rio Grande do Norte", "RS": "Rio Grande do Sul", "RO": "Rondônia",
    "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo", "SE": "Sergipe",
    "TO": "Tocantins",
}

# E-mail repetido em muitos CNPJs quase sempre e do escritorio de
# contabilidade que fez o cadastro, nao da empresa. Medido na base: o mesmo
# endereco aparece em centenas de estabelecimentos.
LIMITE_EMAIL_PROPRIO = 3

# Teto de exportacao, o mesmo da Apollo (500 paginas de 100). Acima disso a
# busca esta larga demais para virar abordagem.
LIMITE_EXPORTACAO = 50_000


class BaseIndisponivel(RuntimeError):
    """Nenhuma base importada ainda (nem a de demonstracao)."""


# ==========================================================================
# Conexao
# ==========================================================================
_trava = threading.Lock()
_estado: dict[str, Any] = {"con": None, "caminho": None, "mtime": None}


def caminho_base() -> Path | None:
    """A base real tem precedencia; a de demonstracao e o plano B."""
    env = os.environ.get("BASE_CNPJ", "").strip()
    if env:
        return Path(env)
    if ARQ_BASE.exists():
        return ARQ_BASE
    if ARQ_DEMO.exists():
        return ARQ_DEMO
    return None


def conectar() -> duckdb.DuckDBPyConnection:
    """Cursor so-leitura. Reabre sozinho se o arquivo da base mudou.

    Cada chamada devolve um cursor proprio: a API atende requisicoes em
    threads diferentes e cursor de DuckDB nao e compartilhavel entre elas.
    """
    with _trava:
        caminho = caminho_base()
        if caminho is None or not caminho.exists():
            raise BaseIndisponivel(
                "Nenhuma base de empresas. Rode `py -m prospectai.cli base demo` "
                "ou importe a da Receita com `base importar`."
            )
        mtime = caminho.stat().st_mtime
        if (_estado["con"] is None or _estado["caminho"] != caminho
                or _estado["mtime"] != mtime):
            if _estado["con"] is not None:
                _estado["con"].close()
            _estado.update(con=duckdb.connect(str(caminho), read_only=True),
                           caminho=caminho, mtime=mtime)
        return _estado["con"].cursor()


def fechar() -> None:
    """Solta o arquivo. No Windows nao da para substituir arquivo aberto."""
    with _trava:
        if _estado["con"] is not None:
            _estado["con"].close()
        _estado.update(con=None, caminho=None, mtime=None)


def _dicts(cur: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    nomes = [d[0] for d in cur.description]
    return [dict(zip(nomes, linha)) for linha in cur.fetchall()]


def meta() -> dict[str, str]:
    cur = conectar()
    return dict(cur.execute("SELECT chave, valor FROM meta").fetchall())


# ==========================================================================
# Filtros
# ==========================================================================
@dataclass
class Filtros:
    q: str = ""
    ufs: list[str] = field(default_factory=list)
    municipios: list[str] = field(default_factory=list)      # codigos da RFB
    cnaes: list[str] = field(default_factory=list)           # prefixos
    cnaes_excluir: list[str] = field(default_factory=list)
    secundarios: bool = False        # CNAE tambem casa nas atividades secundarias
    portes: list[str] = field(default_factory=list)          # 00 01 03 05
    mei: str = "todos"               # todos | so_mei | sem_mei
    simples: str = "todos"           # todos | sim | nao
    capital_min: float | None = None
    capital_max: float | None = None
    abertura_de: str | None = None   # AAAA-MM-DD
    abertura_ate: str | None = None
    situacoes: list[str] = field(default_factory=lambda: ["02"])
    so_matriz: bool = False
    com_telefone: bool = False
    com_celular: bool = False
    com_email: bool = False
    email_proprio: bool = False      # descarta e-mail compartilhado (contador)
    bairro: str = ""

    @classmethod
    def de_dict(cls, d: dict[str, Any] | None) -> "Filtros":
        """Tolerante: chave desconhecida e ignorada, lista vazia vira padrao."""
        d = d or {}
        validos = {f.name for f in fields(cls)}
        f = cls(**{k: v for k, v in d.items() if k in validos and v is not None})
        f.ufs = [u.upper() for u in f.ufs if u.upper() in UFS]
        f.cnaes = [so_digitos(c) for c in f.cnaes if so_digitos(c)]
        f.cnaes_excluir = [so_digitos(c) for c in f.cnaes_excluir if so_digitos(c)]
        f.portes = [p for p in f.portes if p in PORTES]
        f.situacoes = [s for s in f.situacoes if s in SITUACOES] or ["02"]
        return f

    def para_dict(self) -> dict[str, Any]:
        return asdict(self)


def _termos(q: str) -> list[str]:
    limpo = sem_acento(q).lower()
    return [t for t in re.split(r"[^a-z0-9]+", limpo) if len(t) >= 2][:6]


def _onde(f: Filtros) -> tuple[str, list[Any]]:
    """Monta o WHERE com parametros. Nenhum valor vai concatenado no SQL."""
    partes: list[str] = []
    p: list[Any] = []

    def em(coluna: str, valores: list[str]) -> None:
        partes.append(f"{coluna} IN ({', '.join('?' for _ in valores)})")
        p.extend(valores)

    q = (f.q or "").strip()
    if q:
        digitos = so_digitos(q)
        if len(digitos) >= 8 and len(digitos) >= len(re.sub(r"[\s./-]", "", q)):
            partes.append("starts_with(cnpj, ?)")
            p.append(digitos)
        else:
            for t in _termos(q):
                partes.append("contains(busca, ?)")
                p.append(t)

    if f.situacoes:
        em("situacao", f.situacoes)
    if f.ufs:
        em("uf", f.ufs)
    if f.municipios:
        em("municipio_codigo", f.municipios)

    if f.cnaes:
        casos = ["starts_with(cnae, ?)" for _ in f.cnaes]
        p.extend(f.cnaes)
        if f.secundarios:
            casos += ["contains(cnaes_sec, ?)" for _ in f.cnaes]
            p.extend("," + c for c in f.cnaes)
        partes.append("(" + " OR ".join(casos) + ")")
    for c in f.cnaes_excluir:
        partes.append("NOT starts_with(cnae, ?)")
        p.append(c)

    if f.portes:
        em("porte", f.portes)
    if f.mei == "so_mei":
        partes.append("mei")
    elif f.mei == "sem_mei":
        partes.append("NOT coalesce(mei, false)")
    if f.simples == "sim":
        partes.append("simples")
    elif f.simples == "nao":
        partes.append("NOT coalesce(simples, false)")

    if f.capital_min is not None:
        partes.append("capital_social >= ?")
        p.append(float(f.capital_min))
    if f.capital_max is not None:
        partes.append("capital_social <= ?")
        p.append(float(f.capital_max))
    if f.abertura_de:
        partes.append("data_abertura >= CAST(? AS DATE)")
        p.append(f.abertura_de)
    if f.abertura_ate:
        partes.append("data_abertura <= CAST(? AS DATE)")
        p.append(f.abertura_ate)

    if f.so_matriz:
        partes.append("matriz")
    if f.com_telefone:
        partes.append("telefone1 IS NOT NULL")
    if f.com_celular:
        partes.append("celular IS NOT NULL")
    if f.com_email or f.email_proprio:
        partes.append("email IS NOT NULL")
    if f.email_proprio:
        partes.append(f"email_qtd <= {LIMITE_EMAIL_PROPRIO}")
    if f.bairro.strip():
        partes.append("contains(lower(strip_accents(coalesce(bairro, ''))), ?)")
        p.append(sem_acento(f.bairro.strip()).lower())

    return (" AND ".join(partes) or "TRUE"), p


# ==========================================================================
# Busca
# ==========================================================================
ORDENS = {
    "padrao": "cnpj",
    "nome": "nome, cnpj",
    "recentes": "data_abertura DESC NULLS LAST, cnpj",
    "antigas": "data_abertura ASC NULLS LAST, cnpj",
    "capital": "capital_social DESC NULLS LAST, cnpj",
}

_COLUNAS_LISTA = """
    e.cnpj, e.nome, e.razao_social, e.nome_fantasia, e.cnae,
    c.descricao AS cnae_descricao, e.municipio, e.uf, e.bairro, e.porte, e.mei,
    e.simples, e.capital_social, e.data_abertura, e.situacao, e.matriz,
    coalesce(e.celular, e.telefone1) AS telefone, e.celular IS NOT NULL AS tem_celular,
    e.email IS NOT NULL AS tem_email, e.email_qtd, e.fonte
"""


def buscar(f: Filtros, pagina: int = 1, por_pagina: int = 50,
           ordem: str = "padrao") -> dict[str, Any]:
    cur = conectar()
    onde, p = _onde(f)
    total = cur.execute(f"SELECT count(*) FROM estabelecimento WHERE {onde}", p).fetchone()[0]

    por_pagina = max(1, min(int(por_pagina), 200))
    pagina = max(1, int(pagina))
    cur.execute(f"""
        SELECT {_COLUNAS_LISTA}
          FROM (SELECT * FROM estabelecimento WHERE {onde}
                 ORDER BY {ORDENS.get(ordem, ORDENS['padrao'])}
                 LIMIT ? OFFSET ?) e
     LEFT JOIN cnae c ON c.codigo = e.cnae
      ORDER BY {ORDENS.get(ordem, ORDENS['padrao'])}
    """, [*p, por_pagina, (pagina - 1) * por_pagina])
    return {"total": int(total), "pagina": pagina, "por_pagina": por_pagina,
            "itens": [_serializar(i) for i in _dicts(cur)]}


def contar(f: Filtros) -> int:
    onde, p = _onde(f)
    return int(conectar().execute(
        f"SELECT count(*) FROM estabelecimento WHERE {onde}", p).fetchone()[0])


def cnpjs(f: Filtros, limite: int = LIMITE_EXPORTACAO) -> list[str]:
    """Os CNPJs de uma busca, para virar lista. Mesmo teto da exportacao."""
    onde, p = _onde(f)
    linhas = conectar().execute(
        f"SELECT cnpj FROM estabelecimento WHERE {onde} ORDER BY cnpj LIMIT ?",
        [*p, int(limite)],
    ).fetchall()
    return [l[0] for l in linhas]


def _serializar(d: dict[str, Any]) -> dict[str, Any]:
    """DATE do DuckDB nao e JSON. Tambem traduz codigos para rotulos."""
    saida = {}
    for k, v in d.items():
        saida[k] = v.isoformat() if hasattr(v, "isoformat") else v
    if "porte" in saida:
        saida["porte_rotulo"] = "MEI" if saida.get("mei") else PORTES.get(saida["porte"] or "", None)
    if "situacao" in saida:
        saida["situacao_rotulo"] = SITUACOES.get(saida["situacao"] or "", saida["situacao"])
    return saida


# ==========================================================================
# Ficha da empresa
# ==========================================================================
def ficha(cnpj: str) -> dict[str, Any] | None:
    cur = conectar()
    cnpj = so_digitos(cnpj)
    cur.execute("""
        SELECT e.*, c.descricao AS cnae_descricao, n.descricao AS natureza_descricao,
               m.descricao AS motivo_descricao
          FROM estabelecimento e
     LEFT JOIN cnae c     ON c.codigo = e.cnae
     LEFT JOIN natureza n ON n.codigo = e.natureza
     LEFT JOIN motivo m   ON m.codigo = e.motivo_situacao
         WHERE e.cnpj = ?
    """, [cnpj])
    linhas = _dicts(cur)
    if not linhas:
        return None
    e = _serializar(linhas[0])

    secundarios = [c for c in (e.pop("cnaes_sec") or "").split(",") if c]
    e["cnaes_secundarios"] = []
    if secundarios:
        cur.execute("SELECT codigo, descricao FROM cnae WHERE codigo IN "
                    f"({', '.join('?' for _ in secundarios)})", secundarios)
        desc = dict(cur.fetchall())
        e["cnaes_secundarios"] = [{"codigo": c, "descricao": desc.get(c)} for c in secundarios]

    cur.execute("""
        SELECT cnpj, nome, municipio, uf, situacao, matriz FROM estabelecimento
         WHERE cnpj_basico = ? AND cnpj <> ? ORDER BY matriz DESC, cnpj LIMIT 20
    """, [e["cnpj_basico"], cnpj])
    e["outras_unidades"] = [_serializar(x) for x in _dicts(cur)]
    e.pop("busca", None)
    return e


def fichas(lista: list[str]) -> list[dict[str, Any]]:
    """Varias empresas de uma vez (itens de uma lista), com o resumo da busca."""
    alvo = [so_digitos(c) for c in lista if so_digitos(c)]
    if not alvo:
        return []
    cur = conectar()
    cur.execute(f"""
        SELECT {_COLUNAS_LISTA}
          FROM estabelecimento e LEFT JOIN cnae c ON c.codigo = e.cnae
         WHERE e.cnpj IN (SELECT unnest(?::VARCHAR[]))
    """, [alvo])
    return [_serializar(i) for i in _dicts(cur)]


def registros(lista: list[str]) -> dict[str, dict[str, Any]]:
    """Linhas cruas por CNPJ, numa consulta so -- para revelar no CRM."""
    alvo = [so_digitos(c) for c in lista if so_digitos(c)]
    if not alvo:
        return {}
    cur = conectar()
    cur.execute("""
        SELECT e.*, c.descricao AS cnae_descricao FROM estabelecimento e
     LEFT JOIN cnae c ON c.codigo = e.cnae
         WHERE e.cnpj IN (SELECT unnest(?::VARCHAR[]))
    """, [alvo])
    return {r["cnpj"]: _serializar(r) for r in _dicts(cur)}


def registro_completo(cnpj: str) -> dict[str, Any] | None:
    return registros([cnpj]).get(so_digitos(cnpj))


# ==========================================================================
# Autocompletar
# ==========================================================================
def opcoes_cnae(q: str, limite: int = 30) -> list[dict[str, Any]]:
    cur = conectar()
    digitos = so_digitos(q)
    if digitos:
        cur.execute("SELECT codigo, descricao FROM cnae WHERE starts_with(codigo, ?) "
                    "ORDER BY codigo LIMIT ?", [digitos, limite])
    else:
        termos = _termos(q)
        if not termos:
            return []
        cond = " AND ".join("contains(lower(strip_accents(descricao)), ?)" for _ in termos)
        cur.execute(f"SELECT codigo, descricao FROM cnae WHERE {cond} "
                    "ORDER BY codigo LIMIT ?", [*termos, limite])
    return _dicts(cur)


def opcoes_municipio(q: str, ufs: list[str] | None = None, limite: int = 20) -> list[dict]:
    termos = _termos(q)
    if not termos:
        return []
    cur = conectar()
    cond = " AND ".join("contains(lower(strip_accents(nome)), ?)" for _ in termos)
    p: list[Any] = list(termos)
    if ufs:
        cond += f" AND uf IN ({', '.join('?' for _ in ufs)})"
        p.extend(u.upper() for u in ufs)
    cur.execute(f"""
        SELECT codigo, nome, uf, estabelecimentos FROM municipio WHERE {cond}
      ORDER BY (lower(strip_accents(nome)) = ?) DESC, estabelecimentos DESC LIMIT ?
    """, [*p, " ".join(termos), limite])
    return _dicts(cur)


# ==========================================================================
# Analise de mercado
# ==========================================================================
def analisar(f: Filtros) -> dict[str, Any]:
    """Distribuicoes para escolher onde atacar (a 'Analise de Mercado' da Leads2B)."""
    cur = conectar()
    onde, p = _onde(f)
    filtrado = f"(SELECT * FROM estabelecimento WHERE {onde})"

    resumo = _dicts(cur.execute(f"""
        SELECT count(*) AS total,
               count(telefone1) AS com_telefone,
               count(celular) AS com_celular,
               count(email) AS com_email,
               count(*) FILTER (WHERE email IS NOT NULL AND email_qtd <= {LIMITE_EMAIL_PROPRIO})
                   AS com_email_proprio,
               count(*) FILTER (WHERE mei) AS mei,
               count(*) FILTER (WHERE simples) AS simples,
               -- "Recem-aberta" e relativo a data da base, nao a hoje: a base
               -- pode ter meses, e contra hoje o numero cairia para perto de zero.
               count(*) FILTER (WHERE data_abertura >= (SELECT max(data_abertura)
                                FROM estabelecimento) - INTERVAL 90 DAY) AS abertas_90d,
               median(capital_social) FILTER (WHERE capital_social > 0) AS capital_mediano
          FROM {filtrado}
    """, p))[0]

    def grupo(sql: str) -> list[dict[str, Any]]:
        return [_serializar(x) for x in _dicts(cur.execute(sql, p))]

    return {
        "resumo": _serializar(resumo),
        "por_uf": grupo(f"SELECT uf, count(*) AS n FROM {filtrado} GROUP BY uf ORDER BY n DESC"),
        "por_municipio": grupo(f"""
            SELECT municipio_codigo AS codigo, any_value(municipio) AS nome,
                   any_value(uf) AS uf, count(*) AS n
              FROM {filtrado} GROUP BY municipio_codigo ORDER BY n DESC LIMIT 15"""),
        "por_cnae": grupo(f"""
            SELECT t.cnae AS codigo, c.descricao, t.n FROM
              (SELECT cnae, count(*) AS n FROM {filtrado} GROUP BY cnae
                ORDER BY n DESC LIMIT 15) t
         LEFT JOIN cnae c ON c.codigo = t.cnae ORDER BY t.n DESC"""),
        "por_porte": grupo(f"""
            SELECT CASE WHEN mei THEN 'MEI' ELSE porte END AS porte, count(*) AS n
              FROM {filtrado} GROUP BY 1 ORDER BY n DESC"""),
        "por_ano": grupo(f"""
            SELECT year(data_abertura) AS ano, count(*) AS n FROM {filtrado}
             WHERE year(data_abertura) >= (SELECT year(max(data_abertura))
                                             FROM estabelecimento) - 14
          GROUP BY 1 ORDER BY 1"""),
        "por_capital": grupo(f"""
            SELECT CASE
                     WHEN capital_social IS NULL OR capital_social <= 0 THEN '0 ou não informado'
                     WHEN capital_social < 10000   THEN 'até 10 mil'
                     WHEN capital_social < 50000   THEN '10 a 50 mil'
                     WHEN capital_social < 100000  THEN '50 a 100 mil'
                     WHEN capital_social < 500000  THEN '100 a 500 mil'
                     WHEN capital_social < 1000000 THEN '500 mil a 1 mi'
                     ELSE 'acima de 1 mi' END AS faixa,
                   min(coalesce(capital_social, 0)) AS ordem, count(*) AS n
              FROM {filtrado} GROUP BY 1 ORDER BY ordem"""),
    }


# ==========================================================================
# Perfil de cliente ideal (ICP) a partir de uma carteira
# ==========================================================================
def _cobertura(contagem: list[tuple[str, int]], total: int, alvo: float,
               maximo: int) -> list[str]:
    """Os menores grupos que, somados, cobrem `alvo` da carteira."""
    escolhidos, soma = [], 0
    for chave, n in contagem:
        if chave is None or len(escolhidos) >= maximo:
            break
        escolhidos.append(chave)
        soma += n
        if total and soma / total >= alvo:
            break
    return escolhidos


def perfil_icp(lista: list[str]) -> dict[str, Any]:
    """O que as empresas da carteira tem em comum, como filtros prontos.

    E o "analise sua carteira de clientes" da Leads2B: voce cola os CNPJs de
    quem ja comprou, e o sistema devolve a busca que acha os parecidos.
    """
    alvo = [so_digitos(c) for c in lista if so_digitos(c)]
    cur = conectar()
    filtro = "cnpj IN (SELECT unnest(?::VARCHAR[]))"
    total = cur.execute(f"SELECT count(*) FROM estabelecimento WHERE {filtro}",
                        [alvo]).fetchone()[0] if alvo else 0
    if not total:
        return {"encontradas": 0, "informadas": len(alvo), "filtros": None}

    def contagem(expr: str) -> list[tuple[str, int]]:
        return cur.execute(f"SELECT {expr} AS k, count(*) AS n FROM estabelecimento "
                           f"WHERE {filtro} GROUP BY 1 ORDER BY n DESC", [alvo]).fetchall()

    cnaes4 = contagem("left(cnae, 4)")
    ufs = contagem("uf")
    municipios = contagem("municipio_codigo")
    portes = contagem("CASE WHEN mei THEN 'MEI' ELSE porte END")
    capital = cur.execute(f"SELECT quantile_cont(capital_social, 0.25) FROM estabelecimento "
                          f"WHERE {filtro} AND capital_social > 0", [alvo]).fetchone()[0]

    sugestao = Filtros(
        cnaes=_cobertura(cnaes4, total, 0.7, 6),
        ufs=_cobertura(ufs, total, 0.8, 4),
        portes=[k for k, _ in portes if k in PORTES],
        mei="sem_mei" if not any(k == "MEI" for k, _ in portes) else "todos",
    )
    concentrados = _cobertura(municipios, total, 0.7, 6)
    if sum(n for k, n in municipios if k in concentrados) / total >= 0.7:
        sugestao.municipios = concentrados
    if capital:
        sugestao.capital_min = float(round(capital, -3))

    # O prefixo de 4 digitos (classe) nao tem descricao propria na tabela da
    # RFB, que so traz subclasses. Rotulo = a subclasse mais comum da carteira.
    exemplo: dict[str, str] = {}
    for cod, _ in contagem("cnae"):
        exemplo.setdefault(cod[:4], cod)
    descricoes = dict(cur.execute(
        "SELECT codigo, descricao FROM cnae WHERE codigo IN (SELECT unnest(?::VARCHAR[]))",
        [list(exemplo.values())]).fetchall())
    nomes_mun = dict(cur.execute(
        "SELECT codigo, nome || '/' || uf FROM municipio WHERE codigo IN "
        "(SELECT unnest(?::VARCHAR[]))", [[k for k, _ in municipios[:10]]]).fetchall())

    parecidos = contar(sugestao)
    return {
        "encontradas": int(total), "informadas": len(alvo),
        "cnaes": [{"codigo": k, "descricao": descricoes.get(exemplo.get(k, "")), "n": n}
                  for k, n in cnaes4[:8]],
        "ufs": [{"uf": k, "n": n} for k, n in ufs[:8]],
        "municipios": [{"codigo": k, "nome": nomes_mun.get(k, k), "n": n}
                       for k, n in municipios[:8]],
        "portes": [{"porte": k, "n": n} for k, n in portes],
        "filtros": sugestao.para_dict(),
        "parecidos_na_base": parecidos,
    }


# ==========================================================================
# Exportacao
# ==========================================================================
_CAB_CSV = ("cnpj;nome_fantasia;razao_social;cnae;cnae_descricao;situacao;abertura;"
            "porte;mei;simples;capital_social;logradouro;numero;complemento;bairro;"
            "cep;municipio;uf;telefone1;telefone2;celular;email;email_compartilhado\n")


def _csv(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "sim" if v else "nao"
    t = str(v.isoformat() if hasattr(v, "isoformat") else v)
    if any(ch in t for ch in ';"\n\r'):
        t = '"' + t.replace('"', '""') + '"'
    return t


def exportar_csv(f: Filtros | None = None, lista: list[str] | None = None,
                 limite: int = LIMITE_EXPORTACAO) -> Iterator[str]:
    """CSV com ';' e BOM -- abre direto no Excel em portugues."""
    cur = conectar()
    if lista is not None:
        onde, p = "cnpj IN (SELECT unnest(?::VARCHAR[]))", [[so_digitos(c) for c in lista]]
    else:
        onde, p = _onde(f or Filtros())
    cur.execute(f"""
        SELECT e.cnpj, e.nome_fantasia, e.razao_social, e.cnae, c.descricao, e.situacao,
               e.data_abertura, CASE WHEN e.mei THEN 'MEI' ELSE e.porte END, e.mei,
               e.simples, e.capital_social,
               trim(coalesce(e.tipo_logradouro, '') || ' ' || coalesce(e.logradouro, '')),
               e.numero, e.complemento, e.bairro, e.cep, e.municipio, e.uf,
               e.telefone1, e.telefone2, e.celular, e.email,
               e.email IS NOT NULL AND e.email_qtd > {LIMITE_EMAIL_PROPRIO}
          FROM (SELECT * FROM estabelecimento WHERE {onde}) e
     LEFT JOIN cnae c ON c.codigo = e.cnae
      ORDER BY e.cnpj LIMIT ?
    """, [*p, int(limite)])
    yield "﻿" + _CAB_CSV
    while True:
        bloco = cur.fetchmany(2000)
        if not bloco:
            break
        yield "".join(";".join(_csv(v) for v in linha) + "\n" for linha in bloco)
