"""E10 e E12 -- fila de envio, portoes de seguranca e maquina de estados.

O canal e um adaptador. A estrada padrao e a MANUAL: o sistema monta tudo e
gera o link wa.me; voce clica e envia. Risco de banimento zero, e nenhuma outra
parte da cadeia muda se voce trocar de estrada depois.

Os portoes (G1..G6) rodam por mensagem, nao por lote. Kill switch lido do banco
antes de CADA envio (invariante I6).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from urllib.parse import quote

from . import db
from .classify import (AUTOMATICO, INTERESSE, OPT_OUT, Classificacao,
                       classificar, conta_como_resposta)

# Warmup: quantas mensagens por dia, conforme a idade do numero.
WARMUP = ((3, 5), (7, 10), (14, 20), (10**6, 30))

DIAS_UTEIS = {0, 1, 2, 3, 4}                    # segunda a sexta
BLOCOS = ((time(9, 0), time(11, 30)), (time(14, 0), time(17, 30)))

ESPERA_TOQUE = {2: timedelta(days=3), 3: timedelta(days=7)}


@dataclass
class Portao:
    ok: bool
    motivo: str | None = None


def limite_do_dia(con: sqlite3.Connection) -> int:
    dia = int(db.estado_sistema(con, "dia_warmup") or 1)
    for ate, limite in WARMUP:
        if dia <= ate:
            return limite
    return 30


def enviados_hoje(con: sqlite3.Connection) -> int:
    linha = db.um(con, "SELECT COUNT(*) AS n FROM mensagem "
                       "WHERE date(enviada_em) = date('now')")
    return int(linha["n"]) if linha else 0


def dentro_da_janela(agora: datetime | None = None) -> bool:
    agora = agora or datetime.now()
    if agora.weekday() not in DIAS_UTEIS:
        return False
    return any(ini <= agora.time() <= fim for ini, fim in BLOCOS)


def circuit_breakers(con: sqlite3.Connection) -> Portao:
    """CB1 e CB2 da spec, calculados sobre os ultimos envios."""
    ultimos = db.buscar(con, """
        SELECT m.empresa_id,
               (SELECT COUNT(*) FROM resposta r WHERE r.empresa_id = m.empresa_id) AS respostas,
               (SELECT COUNT(*) FROM contato c WHERE c.empresa_id = m.empresa_id
                                                 AND c.opt_out = 1) AS optouts
          FROM mensagem m
         WHERE m.enviada_em IS NOT NULL
      ORDER BY m.enviada_em DESC LIMIT 30
    """)
    if len(ultimos) < 20:
        return Portao(True)                     # amostra pequena demais para julgar

    optouts = sum(1 for l in ultimos[:20] if l["optouts"])
    if optouts >= 3:
        return Portao(False, "CB1: 3+ opt-outs nos ultimos 20 envios")

    respostas = sum(1 for l in ultimos if l["respostas"])
    if len(ultimos) >= 30 and respostas / len(ultimos) < 0.03:
        return Portao(False, "CB2: taxa de resposta abaixo de 3% em 30 envios")

    return Portao(True)


def portoes(con: sqlite3.Connection, mensagem_id: int) -> Portao:
    """G1..G6. Cada envio passa por todos, na hora do envio."""
    if (db.estado_sistema(con, "envio_habilitado") or "false") != "true":
        motivo = db.estado_sistema(con, "pausa_motivo") or "envio desabilitado"
        return Portao(False, f"G1 kill switch: {motivo}")

    if not dentro_da_janela():
        return Portao(False, "G2: fora da janela (seg-sex, 9h-11h30 e 14h-17h30)")

    if enviados_hoje(con) >= limite_do_dia(con):
        return Portao(False, f"G3: limite de warmup atingido ({limite_do_dia(con)}/dia)")

    linha = db.um(con, """
        SELECT m.*, c.opt_out FROM mensagem m
     LEFT JOIN contato c ON c.id = m.contato_id WHERE m.id = ?
    """, (mensagem_id,))
    if linha is None:
        return Portao(False, "mensagem inexistente")
    if linha["opt_out"]:
        return Portao(False, "G4: contato marcou opt-out")       # I2, 2a verificacao
    if linha["estado"] != "AGENDADA":
        return Portao(False, f"estado {linha['estado']}, esperado AGENDADA")
    if not linha["validador_ok"]:
        return Portao(False, "mensagem reprovada pelo validador")

    recente = db.um(con, """
        SELECT 1 FROM mensagem WHERE empresa_id = ? AND enviada_em IS NOT NULL
          AND enviada_em > datetime('now', '-48 hours') LIMIT 1
    """, (linha["empresa_id"],))
    if recente:
        return Portao(False, "G5: ja houve envio para esta empresa nas ultimas 48h")

    return circuit_breakers(con)


def link_whatsapp(telefone: str, texto: str) -> str:
    return f"https://wa.me/{telefone.lstrip('+')}?text={quote(texto)}"


def link_email(email: str, assunto: str | None, texto: str) -> str:
    """Estrada manual tambem no e-mail: abre o seu cliente de e-mail pronto."""
    return f"mailto:{email}?subject={quote(assunto or '')}&body={quote(texto)}"


DEMO = "empresa de demonstração: contato fictício, envio bloqueado"


def fila(con: sqlite3.Connection, limite: int = 30) -> list[dict]:
    """A fila de hoje: o que esta pronto para ser enviado, com o link montado."""
    linhas = db.buscar(con, """
        SELECT m.id, m.toque, m.texto, m.template_id, m.estado, m.agendada_para,
               m.canal, m.assunto, e.id AS empresa_id, e.nome, e.ramo, e.fonte,
               c.valor AS telefone, c.valor AS destino,
               s.total AS score, s.faixa, p.token AS prova_token,
               p.primeira_visita_em, p.num_visitas,
               (SELECT cd.nome FROM inscricao i JOIN cadencia cd ON cd.id = i.cadencia_id
                 WHERE i.id = m.inscricao_id) AS cadencia
          FROM mensagem m
          JOIN empresa e  ON e.id = m.empresa_id
          JOIN contato c  ON c.id = m.contato_id AND c.opt_out = 0
                          AND (m.canal = 'email' OR c.eh_movel = 1)  -- fixo nao tem WhatsApp
     LEFT JOIN score s    ON s.id = (SELECT id FROM score WHERE empresa_id = e.id
                                      ORDER BY id DESC LIMIT 1)
     LEFT JOIN prova p    ON p.empresa_id = e.id
         WHERE m.estado = 'AGENDADA' AND m.validador_ok = 1
           -- toque 1 sem data = pronto agora; os outros so quando agendados
           -- pelo envio do anterior (marcar_enviada).
           AND ((m.toque = 1 AND m.agendada_para IS NULL)
                OR m.agendada_para <= datetime('now'))
      ORDER BY (p.primeira_visita_em IS NOT NULL) DESC, s.total DESC, m.toque
         LIMIT ?
    """, (limite,))

    saida = []
    for l in linhas:
        d = dict(l)
        d["bloqueio"] = DEMO if d["fonte"] == "demo" else None
        if d["bloqueio"]:
            d["link"] = None
        elif d["canal"] == "email":
            d["link"] = link_email(d["destino"], d["assunto"], d["texto"])
        else:
            d["link"] = link_whatsapp(d["destino"], d["texto"])
        d["visitou_prova"] = bool(d.get("primeira_visita_em"))
        saida.append(d)
    return saida


def marcar_enviada(con: sqlite3.Connection, mensagem_id: int,
                   provider_id: str | None = None, forcar: bool = False) -> Portao:
    """Confirma o envio e agenda o proximo toque.

    `forcar=True` e para a estrada manual: voce ja apertou enviar no WhatsApp,
    entao o registro tem que refletir a realidade mesmo fora da janela.
    """
    if not forcar:
        p = portoes(con, mensagem_id)
        if not p.ok:
            return p

    linha = db.um(con, "SELECT m.*, e.fonte FROM mensagem m JOIN empresa e "
                       "ON e.id = m.empresa_id WHERE m.id = ?", (mensagem_id,))
    if linha is None:
        return Portao(False, "mensagem inexistente")
    if linha["fonte"] == "demo":
        return Portao(False, DEMO)

    # Lock otimista: o UPDATE so pega se ainda estiver AGENDADA. Usar o
    # rowcount DESTE cursor -- `con.total_changes` e o acumulado da conexao
    # inteira e nunca seria zero, entao o guarda nao pegaria nada.
    cur = con.execute(
        "UPDATE mensagem SET estado='ENVIADA', enviada_em=datetime('now'), "
        "provider_message_id=? WHERE id=? AND estado='AGENDADA'",
        (provider_id, mensagem_id),
    )
    if cur.rowcount == 0:
        return Portao(False, "outro processo ja enviou esta mensagem")   # I3

    empresa_id, toque = linha["empresa_id"], linha["toque"]
    con.execute(
        "UPDATE lead SET estado=?, estado_desde=datetime('now') WHERE empresa_id=?",
        (f"TOQUE_{toque}", empresa_id),
    )

    # Proximo toque: o de menor numero ainda agendado e aprovado. A espera vem
    # do passo da cadencia; nas mensagens automaticas, de ESPERA_TOQUE.
    # Datas calculadas no SQLite: o formato e o fuso (UTC) sao os mesmos do
    # datetime('now') da fila. Com datetime.now().isoformat() a comparacao
    # misturava hora local com UTC e 'T' com espaco.
    proximo = db.um(con, """
        SELECT id, toque, espera_dias FROM mensagem
         WHERE empresa_id = ? AND estado = 'AGENDADA' AND validador_ok = 1 AND toque > ?
      ORDER BY toque LIMIT 1
    """, (empresa_id, toque))
    if proximo is not None:
        dias = proximo["espera_dias"]
        if dias is None:
            dias = ESPERA_TOQUE.get(proximo["toque"], timedelta(days=7)).days
        quando = f"+{int(dias)} days"
        con.execute("UPDATE mensagem SET agendada_para = datetime('now', ?) WHERE id = ?",
                    (quando, proximo["id"]))
        con.execute("UPDATE lead SET proximo_evento_em = datetime('now', ?) "
                    "WHERE empresa_id = ?", (quando, empresa_id))
    else:
        # Ultimo toque enviado: dorme 90 dias (I4).
        con.execute(
            "UPDATE lead SET estado='DORMENTE', proximo_evento_em=datetime('now', '+90 days') "
            "WHERE empresa_id=?", (empresa_id,))
        if linha["inscricao_id"]:
            con.execute("UPDATE inscricao SET estado='CONCLUIDA' WHERE id=? AND estado='ATIVA'",
                        (linha["inscricao_id"],))
    con.commit()
    return Portao(True)


def aplicar_opt_out(con: sqlite3.Connection, empresa_id: int,
                    motivo: str = "pedido do contato") -> None:
    """I2: terminal. Marca o contato, cancela toques pendentes, encerra o lead."""
    con.execute(
        "UPDATE contato SET opt_out=1, opt_out_em=datetime('now'), opt_out_motivo=? "
        "WHERE empresa_id=?", (motivo, empresa_id),
    )
    con.execute(
        "UPDATE mensagem SET estado='CANCELADA' WHERE empresa_id=? AND estado='AGENDADA'",
        (empresa_id,),
    )
    con.execute(
        "UPDATE lead SET estado='OPT_OUT', estado_desde=datetime('now'), "
        "desfecho='opt_out', proximo_evento_em=NULL WHERE empresa_id=?",
        (empresa_id,),
    )
    con.execute("UPDATE inscricao SET estado='OPT_OUT' WHERE empresa_id=? AND estado='ATIVA'",
                (empresa_id,))
    con.commit()


def registrar_resposta(con: sqlite3.Connection, empresa_id: int, texto: str,
                       mensagem_id: int | None = None) -> Classificacao:
    """E11 + E12. Resposta humana tira o lead da automacao -- sempre."""
    c = classificar(texto)
    db.inserir(con, "resposta", {
        "mensagem_id": mensagem_id, "empresa_id": empresa_id, "texto": texto,
        "classe": c.classe, "confianca": c.confianca, "classificada_por": c.por,
    })

    if c.classe == OPT_OUT:
        aplicar_opt_out(con, empresa_id, "resposta do contato")
        return c

    if not conta_como_resposta(c):
        con.commit()
        return c                                # bot respondeu: follow-up segue

    # Humano respondeu: cancela os toques e passa para voce.
    con.execute(
        "UPDATE mensagem SET estado='CANCELADA' WHERE empresa_id=? AND estado='AGENDADA'",
        (empresa_id,),
    )
    con.execute(
        "UPDATE lead SET estado='EM_CONVERSA', estado_desde=datetime('now'), "
        "proximo_evento_em=NULL WHERE empresa_id=?", (empresa_id,),
    )
    con.execute("UPDATE inscricao SET estado='RESPONDEU' WHERE empresa_id=? AND estado='ATIVA'",
                (empresa_id,))
    con.commit()
    return c


def registrar_visita_prova(con: sqlite3.Connection, token: str,
                           user_agent: str | None) -> dict | None:
    """E8. Primeira visita e o sinal de intencao mais forte do sistema."""
    linha = db.um(con, "SELECT * FROM prova WHERE token = ?", (token,))
    if linha is None:
        return None
    primeira = linha["primeira_visita_em"] is None
    con.execute(
        "UPDATE prova SET num_visitas = num_visitas + 1, ultimo_user_agent = ?, "
        "primeira_visita_em = COALESCE(primeira_visita_em, datetime('now')) "
        "WHERE token = ?", (user_agent, token),
    )
    con.commit()
    return {"empresa_id": linha["empresa_id"], "auditoria_id": linha["auditoria_id"],
            "primeira_visita": primeira}


def alertas(con: sqlite3.Connection) -> list[dict]:
    """O que exige sua acao agora."""
    saida: list[dict] = []

    visitas = db.buscar(con, """
        SELECT e.id, e.nome, p.primeira_visita_em, p.num_visitas
          FROM prova p JOIN empresa e ON e.id = p.empresa_id
         WHERE p.primeira_visita_em IS NOT NULL
           AND p.primeira_visita_em > datetime('now', '-2 days')
      ORDER BY p.primeira_visita_em DESC
    """)
    for v in visitas:
        saida.append({
            "tipo": "prova_aberta", "urgencia": "alta", "empresa_id": v["id"],
            "texto": f"{v['nome']} abriu a analise ({v['num_visitas']}x). Ligar agora.",
            "quando": v["primeira_visita_em"],
        })

    cb = circuit_breakers(con)
    if not cb.ok:
        saida.append({"tipo": "circuit_breaker", "urgencia": "alta",
                      "texto": cb.motivo, "quando": None})

    pendentes = db.um(con, """
        SELECT COUNT(*) AS n FROM mensagem
         WHERE estado='AGENDADA' AND toque > 1 AND agendada_para <= datetime('now')
    """)
    if pendentes and pendentes["n"]:
        saida.append({"tipo": "follow_up", "urgencia": "media",
                      "texto": f"{pendentes['n']} follow-ups vencidos hoje.",
                      "quando": None})
    return saida
