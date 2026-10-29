"""Importador da base aberta de CNPJ da Receita Federal para o DuckDB.

Diferente de sources/receita.py, que varre os ZIPs a cada busca e guarda so o
que casou com a campanha, aqui o cadastro inteiro vira uma base pesquisavel
(base.py). E a mesma fonte que sustenta os "+26 milhoes de CNPJs" da Leads2B.

Fonte oficial (conferida em 13/10/2026 no dados.gov.br, conjunto "CNPJ"):
o antigo dadosabertos.rfb.gov.br nao responde mais; os arquivos estao num
compartilhamento Nextcloud da RFB, lido aqui por WebDAV. Release mensal,
~7,8 GB em ZIPs.

Regras:
  - I7: Socios*.zip nao e baixado nem importado. Razao social de MEI pode
    conter CPF -- os digitos sao removidos na importacao.
  - Falha de import aborta e nao deixa base pela metade: tudo e montado num
    arquivo temporario e so substitui a base atual no fim (spec, E1).
  - Base parcial (faltando partes) so com permissao explicita, e fica marcada
    na tabela `meta` para a interface avisar.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import duckdb
import httpx

from . import base
from .db import RAIZ
from .normalize import DDDS_VALIDOS

WEBDAV = "https://arquivos.receitafederal.gov.br/public.php/webdav"
# Token do link publico publicado pela RFB no dados.gov.br. Nao e segredo:
# e o identificador do compartilhamento, usado como usuario no WebDAV.
TOKEN_PUBLICO = "YggdBLfdninEJX9"

PASTA_DOWNLOADS = RAIZ / "dados" / "receita"

PARTES = 10
TABELAS_AUX = ("Cnaes", "Municipios", "Naturezas", "Motivos")
# Socios fica de fora de proposito (I7). Paises e Qualificacoes nao sao usados.
ESPERADOS = (
    [f"Estabelecimentos{i}.zip" for i in range(PARTES)]
    + [f"Empresas{i}.zip" for i in range(PARTES)]
    + ["Simples.zip"]
    + [f"{t}.zip" for t in TABELAS_AUX]
)

Progresso = Callable[[str, int, int], None]


def _nada(etapa: str, feito: int, total: int) -> None:
    pass


class ImportacaoIncompleta(RuntimeError):
    pass


# ==========================================================================
# Download
# ==========================================================================
def _propfind(cli: httpx.Client, caminho: str) -> list[tuple[str, int]]:
    r = cli.request("PROPFIND", f"{WEBDAV}/{caminho}", headers={"Depth": "1"})
    if r.status_code != 207:
        raise RuntimeError(f"WebDAV da Receita devolveu HTTP {r.status_code} em /{caminho}")
    ns = {"d": "DAV:"}
    itens = []
    for resp in ET.fromstring(r.content).findall("d:response", ns):
        href = resp.findtext("d:href", default="", namespaces=ns)
        tamanho = resp.findtext(".//d:getcontentlength", default="0", namespaces=ns)
        itens.append((href.rstrip("/").rsplit("/", 1)[-1], int(tamanho or 0)))
    return itens


def _cliente(timeout: float = 120.0) -> httpx.Client:
    return httpx.Client(auth=(TOKEN_PUBLICO, ""), timeout=timeout, follow_redirects=True)


def versoes_disponiveis() -> list[str]:
    """Pastas AAAA-MM do compartilhamento, mais recente por ultimo."""
    with _cliente(60.0) as cli:
        nomes = [n for n, _ in _propfind(cli, "")]
    return sorted(n for n in nomes if re.fullmatch(r"\d{4}-\d{2}", n))


def arquivos_da_versao(versao: str) -> list[tuple[str, int]]:
    with _cliente(60.0) as cli:
        return [(n, t) for n, t in _propfind(cli, versao) if n in ESPERADOS]


def baixar_versao(versao: str | None = None, pasta: Path | None = None,
                  progresso: Progresso = _nada) -> Path:
    """Baixa os ZIPs de uma versao (padrao: a mais recente), com retomada."""
    versao = versao or versoes_disponiveis()[-1]
    destino = (pasta or PASTA_DOWNLOADS) / versao
    destino.mkdir(parents=True, exist_ok=True)
    arquivos = arquivos_da_versao(versao)
    total = sum(t for _, t in arquivos)
    feito = 0
    with _cliente() as cli:
        for nome, tamanho in arquivos:
            alvo = destino / nome
            ja = alvo.stat().st_size if alvo.exists() else 0
            if ja == tamanho:
                feito += tamanho
                progresso("download", feito, total)
                continue
            headers = {"Range": f"bytes={ja}-"} if 0 < ja < tamanho else {}
            with cli.stream("GET", f"{WEBDAV}/{versao}/{nome}", headers=headers) as r:
                if r.status_code not in (200, 206):
                    raise RuntimeError(f"{nome}: HTTP {r.status_code}")
                modo = "ab" if r.status_code == 206 else "wb"
                if modo == "wb":
                    ja = 0
                with open(alvo, modo) as f:
                    for bloco in r.iter_bytes(1024 * 1024):
                        f.write(bloco)
                        ja += len(bloco)
                        progresso(f"download {nome}", feito + ja, total)
            if alvo.stat().st_size != tamanho:
                raise RuntimeError(f"{nome}: baixou {alvo.stat().st_size} de {tamanho} bytes")
            feito += tamanho
    return destino


# ==========================================================================
# Inventario
# ==========================================================================
@dataclass
class Inventario:
    pasta: Path
    presentes: dict[str, Path] = field(default_factory=dict)
    faltando: list[str] = field(default_factory=list)
    corrompidos: list[str] = field(default_factory=list)
    versao: str | None = None

    def partes(self, prefixo: str) -> list[Path]:
        return [p for n, p in sorted(self.presentes.items())
                if re.fullmatch(rf"{prefixo}\d*\.zip", n)]

    @property
    def completo(self) -> bool:
        return not self.faltando and not self.corrompidos

    def resumo(self) -> dict:
        return {
            "estabelecimentos": f"{len(self.partes('Estabelecimentos'))}/{PARTES}",
            "empresas": f"{len(self.partes('Empresas'))}/{PARTES}",
            "simples": "Simples.zip" in self.presentes,
            "auxiliares": [t for t in TABELAS_AUX if f"{t}.zip" in self.presentes],
            "faltando": self.faltando, "corrompidos": self.corrompidos,
        }


def _versao_do_nome(nome_interno: str) -> str | None:
    """'K3241.K03200Y1.D60808.ESTABELE' -> '2026-08-08'.

    A RFB grava so o ultimo digito do ano; vale para a decada de 2020.
    """
    m = re.search(r"\.D(\d)(\d{2})(\d{2})\.", nome_interno)
    if not m:
        return None
    return f"{2020 + int(m.group(1))}-{m.group(2)}-{m.group(3)}"


def inventariar(pasta: Path) -> Inventario:
    inv = Inventario(pasta=pasta)
    for nome in ESPERADOS:
        caminho = pasta / nome
        if not caminho.exists():
            inv.faltando.append(nome)
            continue
        try:
            with zipfile.ZipFile(caminho) as z:
                membros = z.namelist()
            if len(membros) != 1:
                raise zipfile.BadZipFile(f"{len(membros)} arquivos dentro")
            inv.versao = inv.versao or _versao_do_nome(membros[0])
            inv.presentes[nome] = caminho
        except zipfile.BadZipFile:
            inv.corrompidos.append(nome)          # download interrompido
    return inv


# ==========================================================================
# Importacao
# ==========================================================================
COL_EST = (
    "cnpj_basico", "cnpj_ordem", "cnpj_dv", "matriz_filial", "nome_fantasia",
    "situacao", "data_situacao", "motivo", "cidade_exterior", "pais",
    "data_inicio", "cnae_principal", "cnae_sec", "tipo_logradouro", "logradouro",
    "numero", "complemento", "bairro", "cep", "uf", "municipio",
    "ddd1", "tel1", "ddd2", "tel2", "ddd_fax", "fax", "email",
    "situacao_especial", "data_situacao_especial",
)
COL_EMP = ("cnpj_basico", "razao_social", "natureza", "qualificacao", "capital_social",
           "porte", "ente_federativo")
COL_SIMPLES = ("cnpj_basico", "opcao_simples", "data_opcao_simples",
               "data_exclusao_simples", "opcao_mei", "data_opcao_mei", "data_exclusao_mei")
COL_AUX = ("codigo", "descricao")


def _extrair(zip_: Path, pasta_tmp: Path) -> Path:
    """Extrai o CSV convertendo latin-1 -> UTF-8.

    O leitor latin-1 do DuckDB recusa o arquivo inteiro quando aparece byte da
    faixa 0x80-0x9F (medido com dado real). Decodificar como latin-1 nunca
    falha e nao perde nada, entao a conversao fica aqui.
    """
    with zipfile.ZipFile(zip_) as z:
        membro = z.namelist()[0]
        destino = pasta_tmp / (zip_.stem + ".csv")
        with z.open(membro) as origem, open(destino, "wb") as saida:
            while bloco := origem.read(16 * 1024 * 1024):
                saida.write(bloco.decode("latin-1").encode("utf-8"))
    return destino


def _ler_csv(caminho: Path, colunas: tuple[str, ...]) -> str:
    """Expressao read_csv do DuckDB para o layout da RFB (';', sem cabecalho).

    auto_detect=false: com as colunas declaradas, o "sniffer" so atrapalha --
    uma linha torta no comeco do arquivo derruba a deteccao inteira.
    """
    arq = caminho.as_posix().replace("'", "''")
    cols = ", ".join(f"'{c}': 'VARCHAR'" for c in colunas)
    return (f"read_csv('{arq}', delim=';', quote='\"', escape='\"', header=false, "
            f"auto_detect=false, columns={{{cols}}}, null_padding=true, "
            f"strict_mode=false, ignore_errors=true, store_rejects=true)")


_DDDS_SQL = ", ".join(f"'{d}'" for d in sorted(DDDS_VALIDOS))

# Mesma regra de normalize.normalizar_telefone, em SQL: rodar o normalizador
# Python em dezenas de milhoes de linhas levaria horas. O teste de paridade
# garante que as duas versoes concordam.
MACROS = f"""
CREATE OR REPLACE TEMP MACRO _tel_dig(ddd, num) AS
    ltrim(regexp_replace(coalesce(ddd, ''), '[^0-9]', '', 'g'), '0')
    || regexp_replace(coalesce(num, ''), '[^0-9]', '', 'g');
CREATE OR REPLACE TEMP MACRO _tel_nono(d) AS
    CASE WHEN length(d) = 10 AND substr(d, 3, 1) IN ('6', '7', '8', '9')
         THEN substr(d, 1, 2) || '9' || substr(d, 3) ELSE d END;
CREATE OR REPLACE TEMP MACRO _tel_ok(d) AS
    length(d) IN (10, 11) AND substr(d, 1, 2) IN ({_DDDS_SQL})
    AND (length(d) = 10 OR substr(d, 3, 1) = '9');
CREATE OR REPLACE TEMP MACRO tel_e164(ddd, num) AS
    CASE WHEN _tel_ok(_tel_nono(_tel_dig(ddd, num)))
         THEN '+55' || _tel_nono(_tel_dig(ddd, num)) END;
CREATE OR REPLACE TEMP MACRO data_rfb(t) AS
    TRY_CAST(TRY_STRPTIME(nullif(trim(t), ''), '%Y%m%d') AS DATE);
CREATE OR REPLACE TEMP MACRO limpo(t) AS
    nullif(trim(regexp_replace(coalesce(t, ''), '\\s+', ' ', 'g')), '');
"""


def importar(
    pasta: Path,
    destino: Path | None = None,
    so_ativas: bool = True,
    permitir_parcial: bool = False,
    progresso: Progresso = _nada,
    fonte: str = "receita",
) -> dict:
    if fonte not in ("receita", "demo"):
        raise ValueError(f"fonte invalida: {fonte}")
    destino = destino or base.ARQ_BASE
    inv = inventariar(pasta)
    if not inv.partes("Estabelecimentos"):
        raise ImportacaoIncompleta(
            f"Nenhum Estabelecimentos*.zip valido em {pasta}. "
            f"Corrompidos: {inv.corrompidos or 'nenhum'}.")
    if not inv.completo and not permitir_parcial:
        raise ImportacaoIncompleta(
            "Base incompleta -- importacao abortada para nao gerar base pela metade. "
            f"Faltando: {inv.faltando}. Corrompidos: {inv.corrompidos}. "
            "Baixe o que falta (`base baixar`) ou use --parcial sabendo do recorte.")

    tmp = destino.with_name(destino.stem + ".importando.duckdb")
    for lixo in (tmp, tmp.with_name(tmp.name + ".wal")):
        lixo.unlink(missing_ok=True)

    inicio = datetime.now()
    con = duckdb.connect(str(tmp))
    try:
        with tempfile.TemporaryDirectory(dir=pasta, prefix="_extraindo_") as d:
            stats = _carregar(con, inv, Path(d), so_ativas, progresso)
        _montar(con, progresso, fonte)
        stats.update(_contagens(con))
        meta = {
            "fonte": fonte,
            "versao": inv.versao or "",
            "importada_em": datetime.now().isoformat(timespec="seconds"),
            "so_ativas": str(so_ativas).lower(),
            "parcial": str(not inv.completo).lower(),
            "arquivos": json.dumps(inv.resumo(), ensure_ascii=False),
            "total": str(stats["estabelecimentos"]),
            "linhas_rejeitadas": str(stats.get("rejeitadas", 0)),
        }
        con.execute("CREATE TABLE meta (chave VARCHAR, valor VARCHAR)")
        con.executemany("INSERT INTO meta VALUES (?, ?)", list(meta.items()))
        con.execute("CHECKPOINT")
        con.close()
    except BaseException:
        con.close()
        tmp.unlink(missing_ok=True)
        raise

    base.fechar()                       # Windows: arquivo aberto nao e substituivel
    os.replace(tmp, destino)
    stats["segundos"] = round((datetime.now() - inicio).total_seconds(), 1)
    stats["parcial"] = not inv.completo
    stats["versao"] = inv.versao
    stats["destino"] = str(destino)
    return stats


def _carregar(con: duckdb.DuckDBPyConnection, inv: Inventario, tmp: Path,
              so_ativas: bool, progresso: Progresso) -> dict:
    con.execute(MACROS)
    filtro = "WHERE situacao = '02'" if so_ativas else ""

    est = inv.partes("Estabelecimentos")
    con.execute(f"CREATE TABLE stg_est ({', '.join(f'{c} VARCHAR' for c in COL_EST)})")
    rejeitadas = 0
    for i, z in enumerate(est):
        progresso("estabelecimentos", i, len(est))
        csv = _extrair(z, tmp)
        con.execute(f"INSERT INTO stg_est SELECT * FROM {_ler_csv(csv, COL_EST)} {filtro}")
        rejeitadas += _rejeitadas(con)        # a tabela de rejeitos e por leitura
        csv.unlink()
        progresso("estabelecimentos", i + 1, len(est))

    # So as empresas (CNPJ basico) que tem estabelecimento na base: com
    # so_ativas isso corta metade das linhas de Empresas.
    con.execute("CREATE TABLE basicos AS SELECT DISTINCT cnpj_basico FROM stg_est")
    con.execute("CREATE TABLE stg_emp (cnpj_basico VARCHAR, razao_social VARCHAR, "
                "natureza VARCHAR, capital_social VARCHAR, porte VARCHAR)")
    emp = inv.partes("Empresas")
    for i, z in enumerate(emp):
        progresso("empresas", i, len(emp))
        csv = _extrair(z, tmp)
        con.execute(f"""
            INSERT INTO stg_emp
            SELECT e.cnpj_basico, e.razao_social, e.natureza, e.capital_social, e.porte
              FROM {_ler_csv(csv, COL_EMP)} e SEMI JOIN basicos b USING (cnpj_basico)
        """)
        csv.unlink()
        progresso("empresas", i + 1, len(emp))

    con.execute("CREATE TABLE stg_simples (cnpj_basico VARCHAR, opcao_simples VARCHAR, "
                "opcao_mei VARCHAR)")
    if "Simples.zip" in inv.presentes:
        progresso("simples", 0, 1)
        csv = _extrair(inv.presentes["Simples.zip"], tmp)
        con.execute(f"""
            INSERT INTO stg_simples
            SELECT s.cnpj_basico, s.opcao_simples, s.opcao_mei
              FROM {_ler_csv(csv, COL_SIMPLES)} s SEMI JOIN basicos b USING (cnpj_basico)
        """)
        csv.unlink()
        progresso("simples", 1, 1)

    for tabela in TABELAS_AUX:
        nome_sql = {"Cnaes": "stg_cnae", "Municipios": "stg_mun",
                    "Naturezas": "stg_nat", "Motivos": "stg_mot"}[tabela]
        con.execute(f"CREATE TABLE {nome_sql} (codigo VARCHAR, descricao VARCHAR)")
        if f"{tabela}.zip" in inv.presentes:
            csv = _extrair(inv.presentes[f"{tabela}.zip"], tmp)
            con.execute(f"INSERT INTO {nome_sql} SELECT trim(codigo), limpo(descricao) "
                        f"FROM {_ler_csv(csv, COL_AUX)}")
            csv.unlink()

    return {"rejeitadas": rejeitadas}


def _rejeitadas(con: duckdb.DuckDBPyConnection) -> int:
    try:
        return int(con.execute("SELECT count(*) FROM reject_errors").fetchone()[0])
    except duckdb.Error:
        return 0


def _montar(con: duckdb.DuckDBPyConnection, progresso: Progresso,
            fonte: str = "receita") -> None:
    progresso("montando a base", 0, 1)
    con.execute("""
        CREATE TABLE estabelecimento AS
        WITH emp AS (
            SELECT cnpj_basico,
                   -- I7: CPF (11 digitos, com ou sem pontuacao) sai da razao
                   -- social de MEI. O prefixo '12.345.678 ' e a raiz do
                   -- proprio CNPJ e so polui a exibicao.
                   limpo(regexp_replace(regexp_replace(any_value(razao_social),
                         '\\d{3}\\.?\\d{3}\\.?\\d{3}-?\\d{2}', ' ', 'g'),
                         '^\\d{2}\\.\\d{3}\\.\\d{3}\\s+', '')) AS razao_social,
                   any_value(natureza) AS natureza,
                   TRY_CAST(replace(any_value(capital_social), ',', '.') AS DOUBLE)
                       AS capital_social,
                   any_value(porte) AS porte
              FROM stg_emp GROUP BY cnpj_basico
        ),
        sim AS (
            SELECT cnpj_basico,
                   any_value(opcao_simples) = 'S' AS simples,
                   any_value(opcao_mei) = 'S' AS mei
              FROM stg_simples GROUP BY cnpj_basico
        ),
        base AS (
            SELECT s.cnpj_basico || s.cnpj_ordem || s.cnpj_dv AS cnpj,
                   s.cnpj_basico,
                   s.matriz_filial = '1' AS matriz,
                   limpo(s.nome_fantasia) AS nome_fantasia,
                   e.razao_social,
                   s.situacao,
                   data_rfb(s.data_situacao) AS data_situacao,
                   nullif(trim(s.motivo), '') AS motivo_situacao,
                   data_rfb(s.data_inicio) AS data_abertura,
                   s.cnae_principal AS cnae,
                   CASE WHEN limpo(s.cnae_sec) IS NOT NULL
                        THEN ',' || replace(s.cnae_sec, ' ', '') || ',' END AS cnaes_sec,
                   e.natureza, coalesce(e.porte, '00') AS porte, e.capital_social,
                   sim.simples, sim.mei,
                   limpo(s.tipo_logradouro) AS tipo_logradouro,
                   limpo(s.logradouro) AS logradouro,
                   limpo(s.numero) AS numero,
                   limpo(s.complemento) AS complemento,
                   limpo(s.bairro) AS bairro,
                   nullif(trim(s.cep), '') AS cep,
                   s.uf,
                   s.municipio AS municipio_codigo,
                   m.descricao AS municipio,
                   tel_e164(s.ddd1, s.tel1) AS t1,
                   tel_e164(s.ddd2, s.tel2) AS t2,
                   CASE WHEN regexp_full_match(lower(trim(s.email)),
                            '[a-z0-9._%+-]+@[a-z0-9-]+(\\.[a-z0-9-]+)*\\.[a-z]{2,24}')
                        THEN lower(trim(s.email)) END AS email
              FROM stg_est s
         LEFT JOIN emp e   USING (cnpj_basico)
         LEFT JOIN sim     USING (cnpj_basico)
         LEFT JOIN stg_mun m ON m.codigo = s.municipio
        )
        SELECT cnpj, cnpj_basico, matriz,
               coalesce(nome_fantasia, razao_social, 'CNPJ ' || cnpj) AS nome,
               nome_fantasia, razao_social,
               situacao, data_situacao, motivo_situacao, data_abertura,
               cnae, cnaes_sec, natureza, porte, capital_social, simples, mei,
               tipo_logradouro, logradouro, numero, complemento, bairro, cep,
               uf, municipio_codigo, municipio,
               coalesce(t1, t2) AS telefone1,
               CASE WHEN t1 IS NOT NULL AND t2 <> t1 THEN t2 END AS telefone2,
               CASE WHEN length(t1) = 14 THEN t1 WHEN length(t2) = 14 THEN t2 END AS celular,
               email,
               CASE WHEN email IS NOT NULL
                    THEN count(*) OVER (PARTITION BY email) END AS email_qtd,
               CASE WHEN coalesce(t1, t2) IS NOT NULL
                    THEN count(*) OVER (PARTITION BY coalesce(t1, t2)) END AS telefone_qtd,
               lower(strip_accents(coalesce(nome_fantasia, '') || ' '
                                   || coalesce(razao_social, ''))) AS busca,
               '{fonte}' AS fonte
          FROM base
      ORDER BY uf, municipio_codigo, cnae
    """.replace("{fonte}", fonte))

    con.execute("CREATE TABLE cnae AS SELECT codigo, descricao FROM stg_cnae")
    con.execute("CREATE TABLE natureza AS SELECT codigo, descricao FROM stg_nat")
    con.execute("CREATE TABLE motivo AS SELECT codigo, descricao FROM stg_mot")
    con.execute("""
        CREATE TABLE municipio AS
        SELECT u.municipio_codigo AS codigo,
               coalesce(m.descricao, u.municipio_codigo) AS nome, u.uf,
               u.n AS estabelecimentos
          FROM (SELECT municipio_codigo, any_value(uf) AS uf, count(*) AS n
                  FROM estabelecimento GROUP BY 1) u
     LEFT JOIN stg_mun m ON m.codigo = u.municipio_codigo
    """)
    for t in ("stg_est", "stg_emp", "stg_simples", "stg_cnae", "stg_mun", "stg_nat",
              "stg_mot", "basicos"):
        con.execute(f"DROP TABLE IF EXISTS {t}")
    progresso("montando a base", 1, 1)


def _contagens(con: duckdb.DuckDBPyConnection) -> dict:
    total, cel, mail, razao = con.execute("""
        SELECT count(*), count(celular), count(email), count(razao_social)
          FROM estabelecimento
    """).fetchone()
    return {"estabelecimentos": int(total), "com_celular": int(cel),
            "com_email": int(mail), "com_razao_social": int(razao)}
