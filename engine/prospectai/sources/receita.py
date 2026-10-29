"""E1 -- Dados Abertos de CNPJ da Receita Federal.

A alternativa gratuita a Places API. Nao da contagem de avaliacoes, mas da o
que falta hoje para o sistema funcionar sem pagar nada:

    telefone, e-mail, CNAE, porte, capital social, data de abertura, flag de MEI

Ou seja: resolve a contatabilidade (o maior buraco do modo OSM) e liga os eixos
DINHEIRO e TIMING do score, que hoje ficam desligados.

Nao baixamos os arquivos de Socios. Eles contem CPF e nome de pessoa fisica --
invariante I7. O que nao entra no disco nao vaza.

Volume: ~6,5 GB uma vez (Estabelecimentos 5,0 + Empresas 1,3 + Simples 0,3).
Os ZIPs sao processados um por vez e apagados em seguida, entao o pico de disco
e o tamanho de um arquivo, nao do conjunto.
"""

from __future__ import annotations

import csv
import io
import re
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import httpx

from ..normalize import eh_movel, normalizar_nome, normalizar_telefone, sem_acento
from ..ramos import Ramo

# Espelho em CDN. O host oficial (dadosabertos.rfb.gov.br) e instavel e em
# muitas redes brasileiras nem resolve no DNS -- medido nesta maquina.
ESPELHO = "https://dados-abertos-rf-cnpj.casadosdados.com.br/arquivos"
OFICIAL = "https://dadosabertos.rfb.gov.br/CNPJ/dados_abertos_cnpj"

# Layout dos CSV (';' separado, latin-1, sem cabecalho).
EST = {
    "cnpj_basico": 0, "cnpj_ordem": 1, "cnpj_dv": 2, "matriz_filial": 3,
    "nome_fantasia": 4, "situacao": 5, "data_situacao": 6,
    "data_inicio": 10, "cnae_principal": 11, "cnae_secundario": 12,
    "tipo_logradouro": 13, "logradouro": 14, "numero": 15, "complemento": 16,
    "bairro": 17, "cep": 18, "uf": 19, "municipio": 20,
    "ddd1": 21, "tel1": 22, "ddd2": 23, "tel2": 24, "email": 27,
}
EMP = {"cnpj_basico": 0, "razao_social": 1, "natureza": 2,
       "capital_social": 4, "porte": 5}
SIMPLES = {"cnpj_basico": 0, "opcao_mei": 4}

SITUACAO_ATIVA = "02"
PORTES = {"01": "MICRO EMPRESA", "03": "EMPRESA DE PEQUENO PORTE",
          "05": "DEMAIS", "00": "NAO INFORMADO"}

Progresso = Callable[[str, int, int], None]


def _nada(etapa: str, feito: int, total: int) -> None:
    pass


@dataclass
class Estabelecimento:
    cnpj: str
    cnpj_basico: str
    nome_fantasia: str
    razao_social: str | None
    cnae: str
    endereco: str
    bairro: str
    cep: str
    municipio_codigo: str
    telefones: list[str]
    email: str | None
    data_inicio: str | None


# ==========================================================================
# Download
# ==========================================================================
def versoes_disponiveis(timeout: float = 60.0) -> list[str]:
    """Datas disponiveis no espelho, mais recente por ultimo."""
    with httpx.Client(timeout=timeout, follow_redirects=True) as cli:
        html = cli.get(f"{ESPELHO}/").text
    return sorted(set(re.findall(r'href="(\d{4}-\d{2}-\d{2})/"', html)))


def baixar(url: str, destino: Path, progresso: Progresso = _nada,
           rotulo: str = "download", timeout: float = 120.0) -> Path:
    """Baixa com retomada: arquivo parcial continua de onde parou."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    ja = destino.stat().st_size if destino.exists() else 0

    with httpx.Client(timeout=timeout, follow_redirects=True) as cli:
        cabecalho = cli.head(url)
        total = int(cabecalho.headers.get("content-length", 0))
        if ja and total and ja >= total:
            progresso(rotulo, total, total)
            return destino

        headers = {"Range": f"bytes={ja}-"} if ja else {}
        with cli.stream("GET", url, headers=headers) as r:
            if r.status_code not in (200, 206):
                raise RuntimeError(f"{url} devolveu HTTP {r.status_code}")
            modo = "ab" if r.status_code == 206 else "wb"
            if modo == "wb":
                ja = 0
            with open(destino, modo) as f:
                for bloco in r.iter_bytes(1024 * 512):
                    f.write(bloco)
                    ja += len(bloco)
                    progresso(rotulo, ja, total or ja)
    return destino


# ==========================================================================
# Leitura dos ZIPs
# ==========================================================================
def _linhas_do_zip(caminho: Path) -> Iterator[list[str]]:
    """Le o CSV de dentro do ZIP em streaming, sem extrair para disco."""
    with zipfile.ZipFile(caminho) as z:
        for nome in z.namelist():
            with z.open(nome) as bruto:
                texto = io.TextIOWrapper(bruto, encoding="latin-1", errors="replace")
                for linha in csv.reader(texto, delimiter=";", quotechar='"'):
                    if linha:
                        yield linha


def codigo_municipio(cache: Path, cidade: str, uf: str,
                     versao: str, progresso: Progresso = _nada) -> str | None:
    """Codigo do municipio na tabela da RFB (nao e o codigo do IBGE)."""
    arq = baixar(f"{ESPELHO}/{versao}/Municipios.zip", cache / "Municipios.zip",
                 progresso, "municipios")
    alvo = normalizar_nome(cidade)
    for linha in _linhas_do_zip(arq):
        if len(linha) >= 2 and normalizar_nome(linha[1]) == alvo:
            return linha[0]
    # Segunda passada, mais frouxa: alguns nomes vem abreviados.
    tokens_alvo = set(alvo.split())
    for linha in _linhas_do_zip(arq):
        if len(linha) >= 2 and tokens_alvo and tokens_alvo <= set(
            normalizar_nome(linha[1]).split()
        ):
            return linha[0]
    return None


def _cnae_casa(cnae: str, prefixos: tuple[str, ...]) -> bool:
    return any(cnae.startswith(p) for p in prefixos)


def coletar_estabelecimentos(
    cache: Path, versao: str, municipio: str, prefixos_cnae: tuple[str, ...],
    progresso: Progresso = _nada, manter_zips: bool = False,
    partes: int = 10,
) -> dict[str, Estabelecimento]:
    """Varre as 10 partes de Estabelecimentos e devolve o que casa.

    Cada parte e baixada, filtrada e apagada antes da proxima -- o pico de
    disco e ~2 GB (a maior parte), nao os 5 GB do conjunto.
    """
    achados: dict[str, Estabelecimento] = {}

    for i in range(partes):
        nome = f"Estabelecimentos{i}.zip"
        arq = baixar(f"{ESPELHO}/{versao}/{nome}", cache / nome,
                     progresso, f"baixando {i+1}/10")

        lidas = 0
        for linha in _linhas_do_zip(arq):
            lidas += 1
            if lidas % 200_000 == 0:
                progresso(f"filtrando {i+1}/10", lidas, 0)
            if len(linha) <= EST["email"]:
                continue
            if linha[EST["municipio"]] != municipio:
                continue
            if linha[EST["situacao"]] != SITUACAO_ATIVA:
                continue
            cnae = linha[EST["cnae_principal"]]
            if prefixos_cnae and not _cnae_casa(cnae, prefixos_cnae):
                continue

            basico = linha[EST["cnpj_basico"]]
            cnpj = basico + linha[EST["cnpj_ordem"]] + linha[EST["cnpj_dv"]]

            telefones = []
            for ddd, tel in ((EST["ddd1"], EST["tel1"]), (EST["ddd2"], EST["tel2"])):
                d, t = linha[ddd].strip(), linha[tel].strip()
                if d and t:
                    e164 = normalizar_telefone(d + t)
                    if e164:
                        telefones.append(e164)

            logradouro = " ".join(
                x for x in (linha[EST["tipo_logradouro"]], linha[EST["logradouro"]])
                if x
            ).strip()
            numero = linha[EST["numero"]].strip()

            achados[cnpj] = Estabelecimento(
                cnpj=cnpj, cnpj_basico=basico,
                nome_fantasia=linha[EST["nome_fantasia"]].strip(),
                razao_social=None,
                cnae=cnae,
                endereco=f"{logradouro}, {numero}" if numero else logradouro,
                bairro=linha[EST["bairro"]].strip(),
                cep=linha[EST["cep"]].strip(),
                municipio_codigo=municipio,
                telefones=telefones,
                email=(linha[EST["email"]].strip() or None),
                data_inicio=linha[EST["data_inicio"]].strip() or None,
            )

        if not manter_zips:
            arq.unlink(missing_ok=True)
        progresso("estabelecimentos", i + 1, partes)

    return achados


def enriquecer_com_empresas(
    cache: Path, versao: str, achados: dict[str, Estabelecimento],
    progresso: Progresso = _nada, manter_zips: bool = False,
) -> dict[str, dict]:
    """Junta razao social, porte e capital social (tabela Empresas)."""
    basicos = {e.cnpj_basico for e in achados.values()}
    extra: dict[str, dict] = {}

    for i in range(10):
        nome = f"Empresas{i}.zip"
        arq = baixar(f"{ESPELHO}/{versao}/{nome}", cache / nome,
                     progresso, f"empresas {i+1}/10")
        for linha in _linhas_do_zip(arq):
            if len(linha) <= EMP["porte"]:
                continue
            b = linha[EMP["cnpj_basico"]]
            if b not in basicos:
                continue
            try:
                capital = float((linha[EMP["capital_social"]] or "0").replace(",", "."))
            except ValueError:
                capital = 0.0
            extra[b] = {
                "razao_social": linha[EMP["razao_social"]].strip(),
                "capital_social": capital,
                "porte": PORTES.get(linha[EMP["porte"]], linha[EMP["porte"]]),
            }
        if not manter_zips:
            arq.unlink(missing_ok=True)
        progresso("empresas", i + 1, 10)

    return extra


def marcar_mei(cache: Path, versao: str, basicos: set[str],
               progresso: Progresso = _nada, manter_zips: bool = False) -> set[str]:
    """Quem e MEI. Sinal de dinheiro relevante: MEI raramente paga R$ 3 mil."""
    arq = baixar(f"{ESPELHO}/{versao}/Simples.zip", cache / "Simples.zip",
                 progresso, "simples")
    meis: set[str] = set()
    for linha in _linhas_do_zip(arq):
        if len(linha) > SIMPLES["opcao_mei"]:
            b = linha[SIMPLES["cnpj_basico"]]
            if b in basicos and linha[SIMPLES["opcao_mei"]].strip().upper() == "S":
                meis.add(b)
    if not manter_zips:
        arq.unlink(missing_ok=True)
    return meis


# ==========================================================================
# Entrada principal
# ==========================================================================
def coletar(
    cidade: str, uf: str, ramos: list[Ramo], cache: Path,
    versao: str | None = None, progresso: Progresso = _nada,
    manter_zips: bool = False, partes: int = 10,
) -> list[dict]:
    """Devolve empresas prontas para o banco, no mesmo formato das outras fontes."""
    versao = versao or versoes_disponiveis()[-1]
    progresso("versao", 1, 1)

    municipio = codigo_municipio(cache, cidade, uf, versao, progresso)
    if municipio is None:
        raise RuntimeError(
            f"Nao achei '{cidade}' na tabela de municipios da RFB (versao {versao})."
        )

    prefixos = tuple(p for r in ramos for p in r.cnaes)
    if not prefixos:
        raise RuntimeError("Nenhum dos ramos escolhidos tem CNAE mapeado.")

    achados = coletar_estabelecimentos(cache, versao, municipio, prefixos,
                                       progresso, manter_zips, partes)
    if not achados:
        return []

    extra = enriquecer_com_empresas(cache, versao, achados, progresso, manter_zips)
    meis = marcar_mei(cache, versao, {e.cnpj_basico for e in achados.values()},
                      progresso, manter_zips)

    # CNAE -> ramo do catalogo
    def ramo_de(cnae: str) -> str | None:
        for r in ramos:
            if _cnae_casa(cnae, r.cnaes):
                return r.chave
        return None

    saida: list[dict] = []
    for cnpj, e in achados.items():
        emp = extra.get(e.cnpj_basico, {})
        nome = e.nome_fantasia or emp.get("razao_social") or ""
        if not nome:
            continue

        # Preferimos celular: fixo nao tem WhatsApp.
        celular = next((t for t in e.telefones if eh_movel(t)), None)
        telefone = celular or (e.telefones[0] if e.telefones else None)

        porte = "MEI" if e.cnpj_basico in meis else emp.get("porte")
        data_abertura = None
        if e.data_inicio and len(e.data_inicio) == 8:
            data_abertura = f"{e.data_inicio[:4]}-{e.data_inicio[4:6]}-{e.data_inicio[6:]}"

        saida.append({
            "nome": nome,
            "nome_norm": normalizar_nome(nome),
            "ramo": ramo_de(e.cnae),
            "categoria_fonte": f"cnae={e.cnae}",
            "endereco": e.endereco or None,
            "bairro": e.bairro or None,
            "cidade": cidade,
            "uf": uf,
            "cep": e.cep or None,
            "telefone": telefone,
            "telefone_origem": "receita" if telefone else None,
            "email": e.email,
            "website_declarado": None,          # a RFB nao guarda site
            "fonte": "receita",
            "fonte_id": cnpj,
            "cnpj": cnpj,
            "razao_social": emp.get("razao_social"),
            "cnae": e.cnae,
            "porte": porte,
            "capital_social": emp.get("capital_social"),
            "data_abertura": data_abertura,
            "situacao_cadastral": "ATIVA",
            "fonte_payload": {"cnae": e.cnae, "telefones": e.telefones},
        })
    return saida


def limpar_cache(cache: Path) -> None:
    if cache.exists():
        shutil.rmtree(cache)
