"""O lado comercial em cima da base: listas, revelar, enriquecer, funil,
tarefas e cadencias.

Fluxo (o mesmo de Apollo e Leads2B, sem a parte de creditos):

    BUSCA na base (DuckDB) -> LISTA -> REVELAR (vira `empresa` + `lead` no
    SQLite) -> ENRIQUECER (site, e-mail, WhatsApp, score) -> CADENCIA -> FUNIL

As invariantes do ProspectAI continuam valendo aqui:
  - I1: variavel de template sem valor medido bloqueia a mensagem;
  - I2: opt-out recusa inscricao e cancela tudo que estiver agendado;
  - I4: no maximo 3 toques por empresa, e 90 dias de silencio depois.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from typing import Any, Callable

import httpx

from . import base, db
from .config import Config
from .messages import PROIBIDAS, PROMESSAS, RE_EMOJI
from .normalize import eh_movel, normalizar_nome, sem_acento, so_digitos
from .ramos import listar as listar_ramos

Progresso = Callable[[str, int, int], None]


def _nada(etapa: str, feito: int, total: int) -> None:
    pass


# ==========================================================================
# Utilitarios
# ==========================================================================
_MINUSCULAS = {"de", "da", "do", "das", "dos", "e", "em", "a", "o"}


def titulo(texto: str | None) -> str | None:
    """'CAMPOS DOS GOYTACAZES' -> 'Campos dos Goytacazes'. So mexe em CAIXA ALTA."""
    if not texto or texto != texto.upper():
        return texto
    palavras = texto.lower().split()
    return " ".join(p if (i and p in _MINUSCULAS) else p[:1].upper() + p[1:]
                    for i, p in enumerate(palavras))


def cnpj_valido(cnpj: str) -> bool:
    d = so_digitos(cnpj)
    if len(d) != 14 or d == d[0] * 14:
        return False

    def dv(digitos: str, pesos: list[int]) -> str:
        r = sum(int(x) * p for x, p in zip(digitos, pesos)) % 11
        return "0" if r < 2 else str(11 - r)
    d1 = dv(d[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = dv(d[:12] + d1, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return d[12:] == d1 + d2


def extrair_cnpjs(texto: str) -> tuple[list[str], list[str]]:
    """CNPJs de um texto colado (planilha, e-mail, lista). Devolve (validos, invalidos)."""
    brutos = re.findall(r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}", texto or "")
    validos, invalidos, vistos = [], [], set()
    for b in brutos:
        d = so_digitos(b)
        if d in vistos:
            continue
        vistos.add(d)
        (validos if cnpj_valido(d) else invalidos).append(d)
    return validos, invalidos


def _ramo_de(cnae: str | None) -> str | None:
    """CNAE -> ramo do catalogo. Prefixo mais longo vence."""
    if not cnae:
        return None
    melhor, tamanho = None, 0
    for r in listar_ramos():
        for p in r.cnaes:
            if cnae.startswith(p) and len(p) > tamanho:
                melhor, tamanho = r.chave, len(p)
    return melhor


# ==========================================================================
# Listas
# ==========================================================================
def criar_lista(con: sqlite3.Connection, nome: str, descricao: str | None = None,
                filtros: dict | None = None) -> int:
    lid = db.inserir(con, "lista", {"nome": nome.strip(), "descricao": descricao,
                                    "filtros": filtros})
    con.commit()
    return lid


def listas(con: sqlite3.Connection) -> list[dict]:
    return [dict(l) for l in db.buscar(con, """
        SELECT l.*, (SELECT COUNT(*) FROM lista_item i WHERE i.lista_id = l.id) AS itens,
               (SELECT COUNT(*) FROM lista_item i JOIN empresa e ON e.cnpj = i.cnpj
                 WHERE i.lista_id = l.id) AS reveladas
          FROM lista l ORDER BY l.id DESC
    """)]


def lista(con: sqlite3.Connection, lista_id: int) -> dict | None:
    l = db.um(con, "SELECT * FROM lista WHERE id = ?", (lista_id,))
    return dict(l) if l else None


def itens_da_lista(con: sqlite3.Connection, lista_id: int) -> list[str]:
    return [l["cnpj"] for l in db.buscar(
        con, "SELECT cnpj FROM lista_item WHERE lista_id = ? ORDER BY adicionado_em, cnpj",
        (lista_id,))]


def adicionar_itens(con: sqlite3.Connection, lista_id: int, cnpjs: list[str]) -> int:
    novos = 0
    for c in cnpjs:
        d = so_digitos(c)
        if len(d) == 14:
            cur = con.execute("INSERT OR IGNORE INTO lista_item (lista_id, cnpj) VALUES (?, ?)",
                              (lista_id, d))
            novos += cur.rowcount
    con.commit()
    return novos


def remover_itens(con: sqlite3.Connection, lista_id: int, cnpjs: list[str]) -> int:
    removidos = 0
    for c in cnpjs:
        cur = con.execute("DELETE FROM lista_item WHERE lista_id = ? AND cnpj = ?",
                          (lista_id, so_digitos(c)))
        removidos += cur.rowcount
    con.commit()
    return removidos


def excluir_lista(con: sqlite3.Connection, lista_id: int) -> None:
    con.execute("DELETE FROM lista WHERE id = ?", (lista_id,))
    con.commit()


def empresas_reveladas(con: sqlite3.Connection, cnpjs: list[str]) -> dict[str, dict]:
    """cnpj -> {empresa_id, estado do lead}: o que da lista ja esta no CRM."""
    if not cnpjs:
        return {}
    saida: dict[str, dict] = {}
    for i in range(0, len(cnpjs), 500):
        bloco = cnpjs[i:i + 500]
        for l in db.buscar(con, f"""
            SELECT e.cnpj, e.id, l.estado FROM empresa e LEFT JOIN lead l ON l.empresa_id = e.id
             WHERE e.cnpj IN ({', '.join('?' for _ in bloco)})
        """, bloco):
            saida[l["cnpj"]] = {"empresa_id": l["id"], "estado": l["estado"]}
    return saida


# ==========================================================================
# Revelar: da base para o CRM
# ==========================================================================
def _empresa_de_registro(r: dict[str, Any]) -> dict[str, Any]:
    """Linha da base -> formato da tabela `empresa` (o mesmo de sources/receita.py)."""
    nome = r.get("nome_fantasia") or r.get("razao_social") or r["nome"]
    logradouro = " ".join(x for x in (r.get("tipo_logradouro"), r.get("logradouro")) if x)
    endereco = ", ".join(x for x in (logradouro, r.get("numero"), r.get("complemento")) if x)
    porte = "MEI" if r.get("mei") else base.PORTES.get(r.get("porte") or "")
    return {
        "nome": titulo(nome),
        "nome_norm": normalizar_nome(nome),
        "ramo": _ramo_de(r.get("cnae")),
        "categoria_fonte": f"cnae={r.get('cnae')}",
        "endereco": titulo(endereco) or None,
        "bairro": titulo(r.get("bairro")),
        "cidade": titulo(r.get("municipio")),
        "uf": r.get("uf"),
        "cep": r.get("cep"),
        "telefone": r.get("celular") or r.get("telefone1"),
        "telefone_origem": "receita" if (r.get("celular") or r.get("telefone1")) else None,
        "email": r.get("email"),
        "fonte": r.get("fonte") or "receita",
        "fonte_id": r["cnpj"],
        "cnpj": r["cnpj"],
        "razao_social": r.get("razao_social"),
        "cnae": r.get("cnae"),
        "porte": porte,
        "capital_social": r.get("capital_social"),
        "data_abertura": r.get("data_abertura"),
        "situacao_cadastral": base.SITUACOES.get(r.get("situacao") or "", "").upper() or None,
        "fonte_payload": {
            "cnae": r.get("cnae"), "cnae_descricao": r.get("cnae_descricao"),
            "telefones": [t for t in (r.get("telefone1"), r.get("telefone2")) if t],
            "email_compartilhado_por": r.get("email_qtd"),
            "cnaes_secundarios": [c for c in (r.get("cnaes_sec") or "").split(",") if c],
            "natureza": r.get("natureza"), "simples": r.get("simples"),
        },
    }


def _contatos_de_registro(con: sqlite3.Connection, empresa_id: int, r: dict) -> None:
    for tel in {t for t in (r.get("telefone1"), r.get("telefone2")) if t}:
        movel = eh_movel(tel)
        con.execute(
            "INSERT OR IGNORE INTO contato (empresa_id, canal, valor, origem, eh_movel) "
            "VALUES (?, ?, ?, 'receita', ?)",
            (empresa_id, "whatsapp" if movel else "telefone", tel, int(movel)),
        )
    if r.get("email"):
        # E-mail usado por varios CNPJs e do contador: fica registrado, mas
        # marcado, e a cadencia nao manda nada para ele.
        compartilhado = (r.get("email_qtd") or 0) > base.LIMITE_EMAIL_PROPRIO
        con.execute(
            "INSERT OR IGNORE INTO contato (empresa_id, canal, valor, origem, validacao) "
            "VALUES (?, 'email', ?, 'receita', ?)",
            (empresa_id, r["email"], "compartilhado" if compartilhado else None),
        )


def revelar(con: sqlite3.Connection, cnpjs: list[str]) -> dict[str, Any]:
    """Traz empresas da base para o CRM. Idempotente: revelar duas vezes nao duplica."""
    reveladas, ja_existiam, nao_encontradas = [], [], []
    alvo = list(dict.fromkeys(so_digitos(x) for x in cnpjs))
    linhas = base.registros(alvo)
    for c in alvo:
        existente = db.um(con, "SELECT id FROM empresa WHERE cnpj = ?", (c,))
        if existente:
            con.execute("UPDATE empresa SET revelada_em = COALESCE(revelada_em, datetime('now')) "
                        "WHERE id = ?", (existente["id"],))
            ja_existiam.append({"cnpj": c, "empresa_id": existente["id"]})
            continue
        r = linhas.get(c)
        if r is None:
            nao_encontradas.append(c)
            continue
        empresa_id = db.upsert_empresa(con, _empresa_de_registro(r))
        con.execute("UPDATE empresa SET revelada_em = datetime('now') WHERE id = ?", (empresa_id,))
        _contatos_de_registro(con, empresa_id, r)
        con.execute("INSERT OR IGNORE INTO lead (empresa_id, estado) VALUES (?, 'NOVO')",
                    (empresa_id,))
        reveladas.append({"cnpj": c, "empresa_id": empresa_id})
    con.commit()
    return {"reveladas": reveladas, "ja_existiam": ja_existiam,
            "nao_encontradas": nao_encontradas}


# ==========================================================================
# Enriquecer: site, contatos do site, validacao de e-mail e score
# ==========================================================================
DOH = "https://cloudflare-dns.com/dns-query"
PROVEDORES_COMUNS = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "yahoo.com.br",
                     "bol.com.br", "uol.com.br", "terra.com.br", "live.com", "icloud.com",
                     "ig.com.br", "globo.com", "globomail.com", "msn.com"}


async def _validar_dominios(dominios: set[str]) -> dict[str, str]:
    """MX por DNS-over-HTTPS (sem dependencia nova). So o dominio sai daqui."""
    saida: dict[str, str] = {d: "mx_ok" for d in dominios if d in PROVEDORES_COMUNS}
    pendentes = [d for d in dominios if d not in saida]
    if not pendentes:
        return saida
    limite = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=8.0, headers={"accept": "application/dns-json"}) as cli:
        async def um(dominio: str) -> None:
            async with limite:
                try:
                    r = await cli.get(DOH, params={"name": dominio, "type": "MX"})
                    j = r.json()
                    if j.get("Status") == 3:
                        saida[dominio] = "dominio_inexistente"
                    elif any(a.get("type") == 15 for a in j.get("Answer", [])):
                        saida[dominio] = "mx_ok"
                    else:
                        saida[dominio] = "sem_mx"
                except (httpx.HTTPError, ValueError):
                    saida[dominio] = "nao_verificado"
        await asyncio.gather(*(um(d) for d in pendentes))
    return saida


def validar_emails(con: sqlite3.Connection, empresa_ids: list[int]) -> dict[str, int]:
    marcas = ", ".join("?" for _ in empresa_ids)
    contatos = db.buscar(con, f"""
        SELECT id, valor, validacao FROM contato
         WHERE canal = 'email' AND empresa_id IN ({marcas})
           AND (validacao IS NULL OR validacao = 'nao_verificado')
    """, empresa_ids)
    if not contatos:
        return {}
    dominios = {c["valor"].rsplit("@", 1)[-1].lower() for c in contatos if "@" in c["valor"]}
    resultado = asyncio.run(_validar_dominios(dominios))
    contagem: dict[str, int] = {}
    for c in contatos:
        dominio = c["valor"].rsplit("@", 1)[-1].lower() if "@" in c["valor"] else ""
        v = resultado.get(dominio, "sintaxe_invalida")
        con.execute("UPDATE contato SET validacao = ?, verificado_em = datetime('now') "
                    "WHERE id = ?", (v, c["id"]))
        contagem[v] = contagem.get(v, 0) + 1
    con.commit()
    return contagem


def enriquecer(con: sqlite3.Connection, empresa_ids: list[int],
               progresso: Progresso = _nada) -> dict[str, Any]:
    """O 'robo' da Leads2B: acha o site, audita, recupera contato e pontua.

    Empresa de demonstracao nao e enriquecida: o nome e ficticio, e chutar
    dominio com ele so bateria no site de alguma empresa real parecida.
    """
    from .discover import Descoberta, descobrir_muitos
    from .pipeline import _gravar_auditoria, _gravar_telefone_do_site, pontuar_empresas

    if not empresa_ids:
        return {"enriquecidas": 0}
    marcas = ", ".join("?" for _ in empresa_ids)
    empresas = [dict(l) for l in db.buscar(
        con, f"SELECT * FROM empresa WHERE id IN ({marcas}) ORDER BY id", empresa_ids)]
    demo = [e["id"] for e in empresas if e["fonte"] == "demo"]
    empresas = [e for e in empresas if e["fonte"] != "demo"]

    stats: dict[str, Any] = {"enriquecidas": len(empresas), "ignoradas_demo": len(demo),
                             "sites_aceitos": 0, "emails_do_site": 0, "whatsapp_do_site": 0}
    if empresas:
        progresso("procurando sites", 0, len(empresas))
        resultados = asyncio.run(descobrir_muitos(
            empresas, progresso=lambda f, t: progresso("procurando sites", f, t)))
        for i, e in enumerate(empresas):
            d, a = resultados.get(i, (Descoberta(), None))
            _gravar_auditoria(con, e["id"], d, a)
            if d.confianca >= 2 and a:
                stats["sites_aceitos"] += 1
                antes = db.um(con, "SELECT COUNT(*) AS n FROM contato WHERE empresa_id = ? "
                                   "AND canal = 'whatsapp'", (e["id"],))["n"]
                if a.telefones_no_site:
                    _gravar_telefone_do_site(con, e["id"], a.telefones_no_site)
                depois = db.um(con, "SELECT COUNT(*) AS n FROM contato WHERE empresa_id = ? "
                                    "AND canal = 'whatsapp'", (e["id"],))["n"]
                stats["whatsapp_do_site"] += depois - antes
                # So do site ACEITO: e-mail de dominio suspeito e de outra empresa.
                for email in a.emails_no_site:
                    cur = con.execute(
                        "INSERT OR IGNORE INTO contato (empresa_id, canal, valor, origem) "
                        "VALUES (?, 'email', ?, 'site')", (e["id"], email))
                    stats["emails_do_site"] += cur.rowcount
        con.commit()
        progresso("validando e-mails", 0, 1)
        stats["emails"] = validar_emails(con, [e["id"] for e in empresas])
        progresso("validando e-mails", 1, 1)

    progresso("score", 0, 1)
    stats["score"] = pontuar_empresas(con, [e["id"] for e in empresas])
    progresso("score", 1, 1)
    return stats


# ==========================================================================
# Funil (CRM)
# ==========================================================================
# Etapas que voce move na mao. TOQUE_N so muda pelo envio (Parte 4 da spec) e
# OPT_OUT so pelo pedido do contato.
ESTADOS_MANUAIS = ("NOVO", "EM_CONVERSA", "REUNIAO", "PROPOSTA", "FECHADO", "PERDIDO",
                   "DORMENTE")
INTERROMPEM_CADENCIA = {"EM_CONVERSA", "REUNIAO", "PROPOSTA", "FECHADO", "PERDIDO",
                        "DORMENTE"}


def funil(con: sqlite3.Connection, busca: str | None = None) -> list[dict]:
    termo = f"%{sem_acento(busca).lower()}%" if busca else None
    linhas = db.buscar(con, """
        SELECT e.id AS empresa_id, e.nome, e.cidade, e.uf, e.ramo, e.cnae, e.fonte,
               e.telefone, e.cnpj, l.estado, l.estado_desde, l.proximo_evento_em,
               l.valor_estimado, l.valor_fechado, l.motivo_perda,
               s.total AS score, s.faixa,
               (SELECT COUNT(*) FROM atividade a WHERE a.empresa_id = e.id
                   AND a.vence_em IS NOT NULL AND a.concluida_em IS NULL) AS tarefas,
               (SELECT MIN(a.vence_em) FROM atividade a WHERE a.empresa_id = e.id
                   AND a.vence_em IS NOT NULL AND a.concluida_em IS NULL) AS proxima_tarefa,
               (SELECT c.nome FROM inscricao i JOIN cadencia c ON c.id = i.cadencia_id
                 WHERE i.empresa_id = e.id AND i.estado = 'ATIVA' LIMIT 1) AS cadencia
          FROM lead l
          JOIN empresa e ON e.id = l.empresa_id
     LEFT JOIN score s ON s.id = (SELECT id FROM score WHERE empresa_id = e.id
                                   ORDER BY id DESC LIMIT 1)
         WHERE (? IS NULL OR (e.nome_norm || ' ' || lower(e.nome) || ' '
                              || lower(coalesce(e.cidade, ''))) LIKE ?)
      ORDER BY l.estado_desde DESC
         LIMIT 2000
    """, (termo, termo))
    return [dict(l) for l in linhas]


def contagens_funil(con: sqlite3.Connection) -> dict[str, dict]:
    """Totais por etapa no banco inteiro: a lista de cartoes tem teto, o total nao."""
    return {l["estado"]: {"n": l["n"], "valor_estimado": l["ve"], "valor_fechado": l["vf"]}
            for l in db.buscar(con, """
                SELECT estado, COUNT(*) AS n, SUM(valor_estimado) AS ve,
                       SUM(valor_fechado) AS vf FROM lead GROUP BY estado""")}


def mudar_estado(con: sqlite3.Connection, empresa_id: int, estado: str,
                 valor: float | None = None, motivo_perda: str | None = None) -> None:
    if estado not in ESTADOS_MANUAIS:
        raise ValueError(f"etapa {estado} nao pode ser escolhida a mao")
    atual = db.um(con, "SELECT estado FROM lead WHERE empresa_id = ?", (empresa_id,))
    if atual and atual["estado"] == "OPT_OUT":
        raise PermissionError("OPT_OUT e terminal e nao pode ser revertido")    # I2
    if not db.um(con, "SELECT 1 FROM empresa WHERE id = ?", (empresa_id,)):
        raise LookupError("empresa nao encontrada")

    if estado in INTERROMPEM_CADENCIA:
        # Voce assumiu a conversa (ou desistiu): toque automatico agendado
        # viraria mensagem fora de contexto.
        con.execute("UPDATE mensagem SET estado = 'CANCELADA' WHERE empresa_id = ? "
                    "AND estado = 'AGENDADA'", (empresa_id,))
        con.execute("UPDATE inscricao SET estado = 'INTERROMPIDA' WHERE empresa_id = ? "
                    "AND estado = 'ATIVA'", (empresa_id,))

    con.execute("INSERT OR IGNORE INTO lead (empresa_id, estado) VALUES (?, ?)",
                (empresa_id, estado))
    con.execute(
        "UPDATE lead SET estado = ?, estado_desde = datetime('now'), "
        "valor_fechado = CASE WHEN ? = 'FECHADO' THEN COALESCE(?, valor_fechado) "
        "                     ELSE valor_fechado END, "
        "motivo_perda = CASE WHEN ? = 'PERDIDO' THEN ? ELSE motivo_perda END, "
        "desfecho = CASE WHEN ? IN ('FECHADO','PERDIDO') THEN lower(?) ELSE desfecho END, "
        "proximo_evento_em = CASE WHEN ? = 'DORMENTE' THEN datetime('now', '+90 days') "
        "                         ELSE NULL END "
        "WHERE empresa_id = ?",
        (estado, estado, valor, estado, motivo_perda, estado, estado, estado, empresa_id),
    )
    anterior = atual["estado"] if atual else "—"
    if anterior != estado:
        db.inserir(con, "atividade", {"empresa_id": empresa_id, "tipo": "nota",
                                      "titulo": f"Etapa: {anterior} → {estado}"})
    con.commit()


def definir_valor(con: sqlite3.Connection, empresa_id: int, valor: float | None) -> None:
    con.execute("UPDATE lead SET valor_estimado = ? WHERE empresa_id = ?", (valor, empresa_id))
    con.commit()


# ==========================================================================
# Atividades e tarefas
# ==========================================================================
TIPOS_ATIVIDADE = ("nota", "tarefa", "ligacao", "reuniao", "email", "whatsapp")


def atividades(con: sqlite3.Connection, empresa_id: int) -> list[dict]:
    return [dict(a) for a in db.buscar(
        con, "SELECT * FROM atividade WHERE empresa_id = ? ORDER BY criada_em DESC, id DESC",
        (empresa_id,))]


def criar_atividade(con: sqlite3.Connection, empresa_id: int, tipo: str, titulo_: str,
                    texto: str | None = None, vence_em: str | None = None) -> int:
    if tipo not in TIPOS_ATIVIDADE:
        raise ValueError(f"tipo invalido: {tipo}")
    aid = db.inserir(con, "atividade", {"empresa_id": empresa_id, "tipo": tipo,
                                        "titulo": titulo_.strip(), "texto": texto,
                                        "vence_em": vence_em})
    con.commit()
    return aid


def concluir_atividade(con: sqlite3.Connection, atividade_id: int, desfazer: bool = False) -> None:
    con.execute("UPDATE atividade SET concluida_em = "
                + ("NULL" if desfazer else "datetime('now')") + " WHERE id = ?",
                (atividade_id,))
    con.commit()


def excluir_atividade(con: sqlite3.Connection, atividade_id: int) -> None:
    con.execute("DELETE FROM atividade WHERE id = ?", (atividade_id,))
    con.commit()


def tarefas(con: sqlite3.Connection, incluir_concluidas: bool = False) -> list[dict]:
    return [dict(t) for t in db.buscar(con, f"""
        SELECT a.*, e.nome AS empresa_nome, e.cidade, l.estado,
               (a.concluida_em IS NULL AND a.vence_em < datetime('now')) AS atrasada
          FROM atividade a JOIN empresa e ON e.id = a.empresa_id
     LEFT JOIN lead l ON l.empresa_id = e.id
         WHERE a.vence_em IS NOT NULL
               {'' if incluir_concluidas else 'AND a.concluida_em IS NULL'}
      ORDER BY a.concluida_em IS NOT NULL, a.vence_em
         LIMIT 500
    """)]


# ==========================================================================
# Cadencias
# ==========================================================================
VARIAVEIS = {
    "nome": "nome da empresa", "cidade": "cidade", "bairro": "bairro",
    "atividade": "atividade principal (CNAE)", "remetente": "seu nome",
    "remetente_cidade": "sua cidade", "remetente_whatsapp": "seu WhatsApp",
}
RE_VARIAVEL = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
LIMITE = {"whatsapp": 700, "email": 3000}


def contexto(empresa: dict, cfg: Config) -> dict[str, str | None]:
    try:
        payload = json.loads(empresa.get("fonte_payload") or "{}")
    except (TypeError, ValueError):
        payload = {}
    atividade = payload.get("cnae_descricao") if isinstance(payload, dict) else None
    return {
        "nome": titulo(empresa.get("nome")),
        "cidade": titulo(empresa.get("cidade")),
        "bairro": titulo(empresa.get("bairro")),
        "atividade": atividade.lower() if atividade else None,
        "remetente": cfg.remetente_nome if not cfg.remetente_nome.startswith("(") else None,
        "remetente_cidade": cfg.remetente_cidade,
        "remetente_whatsapp": cfg.remetente_whatsapp,
    }


def renderizar(modelo: str, ctx: dict[str, str | None]) -> tuple[str, list[str]]:
    """Troca {{variavel}}. Variavel sem valor e erro, nunca texto vazio (I1)."""
    erros: list[str] = []

    def troca(m: re.Match) -> str:
        nome = m.group(1)
        if nome not in VARIAVEIS:
            erros.append(f"variável desconhecida {{{{{nome}}}}}")
            return m.group(0)
        valor = ctx.get(nome)
        if not valor:
            erros.append(f"{{{{{nome}}}}} sem valor para esta empresa")
            return m.group(0)
        return str(valor)

    return RE_VARIAVEL.sub(troca, modelo or ""), erros


def validar_texto(texto: str, canal: str, primeiro: bool) -> list[str]:
    """As regras da spec que valem para texto escrito por voce."""
    erros: list[str] = []
    baixo = sem_acento(texto).lower()
    if len(texto) > LIMITE[canal]:
        erros.append(f"{len(texto)} caracteres (limite {LIMITE[canal]} para {canal})")
    for termo in PROIBIDAS:
        if termo in baixo:
            erros.append(f"jargão proibido ({termo!r})")
    for termo in PROMESSAS:
        if termo in baixo:
            erros.append(f"promessa de resultado ({termo!r})")
    if len(RE_EMOJI.findall(texto)) > 1:
        erros.append("mais de 1 emoji")
    if primeiro:
        if not re.search(r"\b(aqui e o|aqui e a|meu nome e|sou o|sou a)\b", baixo):
            erros.append("primeiro contato precisa dizer quem é você (\"aqui é o…\", \"sou a…\")")
        if not re.search(r"nao mando mais|nao te incomodo mais|so falar|nao quiser receber",
                         baixo):
            erros.append("primeiro contato precisa de saída (\"se não quiser, é só falar\")")
    return erros


def cadencias(con: sqlite3.Connection) -> list[dict]:
    return [dict(c) for c in db.buscar(con, """
        SELECT c.*,
               (SELECT COUNT(*) FROM cadencia_passo p WHERE p.cadencia_id = c.id) AS passos,
               (SELECT COUNT(*) FROM inscricao i WHERE i.cadencia_id = c.id
                   AND i.estado = 'ATIVA') AS ativas,
               (SELECT COUNT(*) FROM inscricao i WHERE i.cadencia_id = c.id) AS inscritas,
               (SELECT COUNT(*) FROM inscricao i WHERE i.cadencia_id = c.id
                   AND i.estado = 'RESPONDEU') AS responderam
          FROM cadencia c ORDER BY c.id DESC
    """)]


def cadencia(con: sqlite3.Connection, cadencia_id: int) -> dict | None:
    c = db.um(con, "SELECT * FROM cadencia WHERE id = ?", (cadencia_id,))
    if c is None:
        return None
    passos = db.buscar(con, "SELECT * FROM cadencia_passo WHERE cadencia_id = ? ORDER BY ordem",
                       (cadencia_id,))
    inscritos = db.buscar(con, """
        SELECT i.*, e.nome, e.cidade, e.fonte,
               (SELECT COUNT(*) FROM mensagem m WHERE m.inscricao_id = i.id
                   AND m.enviada_em IS NOT NULL) AS enviadas,
               (SELECT COUNT(*) FROM mensagem m WHERE m.inscricao_id = i.id) AS mensagens
          FROM inscricao i JOIN empresa e ON e.id = i.empresa_id
         WHERE i.cadencia_id = ? ORDER BY i.inscrita_em DESC LIMIT 500
    """, (cadencia_id,))
    return dict(c) | {"passos": [dict(p) for p in passos],
                      "inscritos": [dict(i) for i in inscritos]}


def salvar_cadencia(con: sqlite3.Connection, nome: str, passos: list[dict],
                    descricao: str | None = None, ativa: bool = True,
                    cadencia_id: int | None = None) -> int:
    """Cria ou substitui os passos. Mensagens ja geradas nao mudam: o texto e
    congelado na inscricao, para o historico mostrar o que foi enviado."""
    if not 1 <= len(passos) <= 3:
        raise ValueError("a cadência precisa de 1 a 3 passos (I4: no máximo 3 toques)")
    for i, p in enumerate(passos, 1):
        if p.get("canal") not in LIMITE:
            raise ValueError(f"passo {i}: canal deve ser whatsapp ou email")
        if not (p.get("corpo") or "").strip():
            raise ValueError(f"passo {i}: texto vazio")
        if p["canal"] == "email" and not (p.get("assunto") or "").strip():
            raise ValueError(f"passo {i}: e-mail precisa de assunto")
        _, erros = renderizar(p["corpo"] + " " + (p.get("assunto") or ""),
                              {k: "x" for k in VARIAVEIS})
        desconhecidas = [e for e in erros if e.startswith("variável desconhecida")]
        if desconhecidas:
            raise ValueError(f"passo {i}: {desconhecidas[0]}")

    if cadencia_id is None:
        cadencia_id = db.inserir(con, "cadencia", {"nome": nome.strip(), "descricao": descricao,
                                                   "ativa": int(ativa)})
    else:
        con.execute("UPDATE cadencia SET nome = ?, descricao = ?, ativa = ? WHERE id = ?",
                    (nome.strip(), descricao, int(ativa), cadencia_id))
        con.execute("DELETE FROM cadencia_passo WHERE cadencia_id = ?", (cadencia_id,))
    for i, p in enumerate(passos, 1):
        db.inserir(con, "cadencia_passo", {
            "cadencia_id": cadencia_id, "ordem": i, "canal": p["canal"],
            "espera_dias": int(p.get("espera_dias") or 0),
            "assunto": (p.get("assunto") or "").strip() or None, "corpo": p["corpo"].strip(),
        })
    con.commit()
    return cadencia_id


def previa(con: sqlite3.Connection, cadencia_id: int, empresa_id: int,
           cfg: Config) -> list[dict]:
    """Como cada passo ficaria para uma empresa, com os erros do validador."""
    e = db.um(con, "SELECT * FROM empresa WHERE id = ?", (empresa_id,))
    if e is None:
        raise LookupError("empresa nao encontrada")
    ctx = contexto(dict(e), cfg)
    saida = []
    for i, p in enumerate(db.buscar(con, "SELECT * FROM cadencia_passo WHERE cadencia_id = ? "
                                         "ORDER BY ordem", (cadencia_id,)), 1):
        texto, erros = renderizar(p["corpo"], ctx)
        assunto, erros_assunto = renderizar(p["assunto"] or "", ctx)
        erros += erros_assunto + validar_texto(texto, p["canal"], i == 1)
        saida.append({"ordem": p["ordem"], "canal": p["canal"], "assunto": assunto or None,
                      "texto": texto, "erros": erros})
    return saida


def _contato_para(con: sqlite3.Connection, empresa_id: int, canal: str) -> sqlite3.Row | None:
    if canal == "whatsapp":
        return db.um(con, """
            SELECT * FROM contato WHERE empresa_id = ? AND canal = 'whatsapp'
               AND eh_movel = 1 AND opt_out = 0
          ORDER BY origem = 'site' DESC, id LIMIT 1
        """, (empresa_id,))
    # E-mail do contador, dominio inexistente ou sem MX: mandar e queimar o
    # dominio do remetente sem chegar a ninguem.
    return db.um(con, """
        SELECT * FROM contato WHERE empresa_id = ? AND canal = 'email' AND opt_out = 0
           AND coalesce(validacao, '') NOT IN
               ('compartilhado', 'dominio_inexistente', 'sem_mx', 'sintaxe_invalida')
      ORDER BY origem = 'site' DESC, validacao = 'mx_ok' DESC, id LIMIT 1
    """, (empresa_id,))


def _motivo_recusa(con: sqlite3.Connection, empresa_id: int, cadencia_id: int) -> str | None:
    if db.um(con, "SELECT 1 FROM contato WHERE empresa_id = ? AND opt_out = 1", (empresa_id,)) \
            or db.um(con, "SELECT 1 FROM lead WHERE empresa_id = ? AND estado = 'OPT_OUT'",
                     (empresa_id,)):
        return "pediu para não receber mensagens (opt-out)"
    lead = db.um(con, "SELECT estado FROM lead WHERE empresa_id = ?", (empresa_id,))
    if lead and lead["estado"] in ("EM_CONVERSA", "REUNIAO", "PROPOSTA", "FECHADO"):
        return f"já está em negociação ({lead['estado']})"
    if db.um(con, "SELECT 1 FROM inscricao WHERE empresa_id = ? AND cadencia_id = ?",
             (empresa_id, cadencia_id)):
        return "já passou por esta cadência"
    if db.um(con, "SELECT 1 FROM mensagem WHERE empresa_id = ? AND estado = 'AGENDADA'",
             (empresa_id,)):
        return "já está em outra sequência de mensagens"
    if db.um(con, "SELECT 1 FROM mensagem WHERE empresa_id = ? AND enviada_em IS NOT NULL "
                  "AND enviada_em > datetime('now', '-90 days')", (empresa_id,)):
        return "recebeu mensagem há menos de 90 dias (I4)"
    return None


def inscrever(con: sqlite3.Connection, cadencia_id: int, empresa_ids: list[int],
              cfg: Config) -> dict[str, Any]:
    cad = db.um(con, "SELECT * FROM cadencia WHERE id = ?", (cadencia_id,))
    if cad is None:
        raise LookupError("cadência não encontrada")
    if not cad["ativa"]:
        raise ValueError("cadência pausada")
    passos = db.buscar(con, "SELECT * FROM cadencia_passo WHERE cadencia_id = ? ORDER BY ordem",
                       (cadencia_id,))

    inscritas, recusadas = [], []
    for empresa_id in dict.fromkeys(empresa_ids):
        e = db.um(con, "SELECT * FROM empresa WHERE id = ?", (empresa_id,))
        if e is None:
            recusadas.append({"empresa_id": empresa_id, "nome": None,
                              "motivo": "empresa não encontrada"})
            continue
        motivo = _motivo_recusa(con, empresa_id, cadencia_id)
        if motivo:
            recusadas.append({"empresa_id": empresa_id, "nome": e["nome"], "motivo": motivo})
            continue

        # Passo sem contato no canal fica de fora; os outros sobem de posicao.
        plano = [(p, c) for p in passos if (c := _contato_para(con, empresa_id, p["canal"]))]
        if not plano:
            canais = " / ".join(sorted({p["canal"] for p in passos}))
            recusadas.append({"empresa_id": empresa_id, "nome": e["nome"],
                              "motivo": f"sem contato utilizável para {canais}"})
            continue

        ctx = contexto(dict(e), cfg)
        inscricao_id = db.inserir(con, "inscricao", {"cadencia_id": cadencia_id,
                                                     "empresa_id": empresa_id})
        bloqueadas = 0
        for toque, (p, contato) in enumerate(plano, 1):
            texto, erros = renderizar(p["corpo"], ctx)
            assunto, erros_assunto = renderizar(p["assunto"] or "", ctx)
            erros += erros_assunto + validar_texto(texto, p["canal"], toque == 1)
            bloqueadas += bool(erros)
            espera = int(p["espera_dias"] or 0)
            db.inserir(con, "mensagem", {
                "empresa_id": empresa_id, "contato_id": contato["id"],
                "template_id": f"cad{cadencia_id}.{inscricao_id}.{p['ordem']}",
                "toque": toque, "texto": texto, "canal": p["canal"],
                "assunto": assunto or None, "espera_dias": espera,
                "inscricao_id": inscricao_id,
                "fatos_usados": {k: v for k, v in ctx.items()
                                 if v and "{{" + k in (p["corpo"] + (p["assunto"] or ""))},
                "validador_ok": int(not erros),
                "validador_erros": "; ".join(erros) or None,
                "gerada_por": "cadencia", "estado": "AGENDADA",
            })
            if toque == 1 and espera:
                con.execute("UPDATE mensagem SET agendada_para = datetime('now', ?) "
                            "WHERE inscricao_id = ? AND toque = 1",
                            (f"+{espera} days", inscricao_id))
        con.execute("INSERT OR IGNORE INTO lead (empresa_id, estado) VALUES (?, 'NOVO')",
                    (empresa_id,))
        con.execute("UPDATE lead SET estado = 'SELECIONADO', estado_desde = datetime('now') "
                    "WHERE empresa_id = ?", (empresa_id,))
        db.inserir(con, "atividade", {"empresa_id": empresa_id, "tipo": "nota",
                                      "titulo": f"Inscrita na cadência “{cad['nome']}”"})
        inscritas.append({"empresa_id": empresa_id, "nome": e["nome"], "toques": len(plano),
                          "bloqueadas_pelo_validador": bloqueadas})
    con.commit()
    return {"inscritas": inscritas, "recusadas": recusadas}
