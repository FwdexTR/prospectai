"use client";

import Link from "next/link";
import { useState } from "react";
import { cnpjFmt, del, ETAPA, moeda, post, type DetalheLead } from "@/lib/api";

const TIPOS: Record<string, string> = {
  nota: "Nota", tarefa: "Tarefa", ligacao: "Ligação", reuniao: "Reunião",
  email: "E-mail", whatsapp: "WhatsApp",
};
const ETAPAS = ["NOVO", "EM_CONVERSA", "REUNIAO", "PROPOSTA", "FECHADO", "PERDIDO", "DORMENTE"];

function quando(v: string | null) {
  if (!v) return "";
  const d = new Date(v.replace(" ", "T") + (v.length > 10 ? "Z" : "T12:00:00"));
  return d.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: v.length > 10 ? "short" : undefined });
}

/** O bloco de CRM da página do lead: etapa, valor, linha do tempo e tarefas. */
export default function PainelCRM({ d, recarregar, avisar }: {
  d: DetalheLead; recarregar: () => void; avisar: (t: string) => void;
}) {
  const id = d.empresa.id;
  const lead = d.lead ?? {};
  const [valor, setValor] = useState<string>(lead.valor_estimado != null ? String(lead.valor_estimado) : "");
  const [tipo, setTipo] = useState("nota");
  const [titulo, setTitulo] = useState("");
  const [texto, setTexto] = useState("");
  const [vence, setVence] = useState("");
  const optOut = lead.estado === "OPT_OUT";

  async function acao(fn: () => Promise<unknown>, ok: string) {
    try { await fn(); avisar(ok); recarregar(); }
    catch (e: any) { avisar(`Erro: ${e.message}`); }
  }

  async function registrar(e: React.FormEvent) {
    e.preventDefault();
    // datetime-local é hora local; o banco guarda UTC, como o datetime('now') do SQLite.
    const venceUtc = vence ? new Date(vence).toISOString().slice(0, 19).replace("T", " ") : null;
    await acao(() => post(`/api/leads/${id}/atividades`, {
      tipo, titulo: titulo.trim() || TIPOS[tipo], texto: texto || null, vence_em: venceUtc,
    }), vence ? "Tarefa criada." : "Registrado.");
    setTitulo(""); setTexto(""); setVence("");
  }

  return (
    <>
      <h2>CRM</h2>
      <div className="ficha-grade">
        <div className="cartao">
          <dl className="dl">
            <dt>Etapa</dt>
            <dd>
              {optOut ? <span className="etiqueta dor">opt-out</span> : (
                <select value={ETAPAS.includes(lead.estado) ? lead.estado : ""} aria-label="Etapa do funil"
                        onChange={(e) => {
                          const estado = e.target.value;
                          if (!estado) return;
                          let valorFechado: number | null = null;
                          if (estado === "FECHADO") {
                            const v = prompt("Valor fechado (R$)?", valor);
                            if (v === null) return;
                            valorFechado = Number(v.replace(/\./g, "").replace(",", ".")) || null;
                          }
                          acao(() => post(`/api/leads/${id}/estado`, { estado, valor: valorFechado }),
                               `Etapa: ${ETAPA[estado]}.`);
                        }}>
                  {!ETAPAS.includes(lead.estado) && <option value="">{ETAPA[lead.estado] ?? "—"}</option>}
                  {ETAPAS.map((e) => <option key={e} value={e}>{ETAPA[e]}</option>)}
                </select>
              )}
            </dd>
            <dt>Valor estimado</dt>
            <dd style={{ display: "flex", gap: ".4rem" }}>
              <input type="number" min={0} step={100} value={valor} onChange={(e) => setValor(e.target.value)}
                     aria-label="Valor estimado em reais" style={{ maxWidth: 140 }} />
              <button className="botao secundario pequeno"
                      onClick={() => acao(() => post(`/api/leads/${id}/valor`, { valor: valor === "" ? null : Number(valor) }),
                                          "Valor salvo.")}>salvar</button>
            </dd>
            {lead.valor_fechado != null && <><dt>Fechado por</dt><dd>{moeda(lead.valor_fechado)}</dd></>}
            {d.empresa.cnpj && (
              <><dt>CNPJ</dt><dd><Link href={`/base/${d.empresa.cnpj}`} style={{ color: "var(--acento)" }}>{cnpjFmt(d.empresa.cnpj)}</Link></dd></>
            )}
            {d.inscricoes.length > 0 && (
              <><dt>Cadências</dt>
                <dd>{d.inscricoes.map((i) => (
                  <div key={i.id}>
                    <Link href={`/cadencias/${i.cadencia_id}`} style={{ color: "var(--acento)" }}>{i.cadencia}</Link>{" "}
                    <span className="fraco">({i.estado.toLowerCase().replace("_", " ")})</span>
                  </div>
                ))}</dd></>
            )}
          </dl>
        </div>

        <form className="cartao" onSubmit={registrar}>
          <div className="duas">
            <div className="campo">
              <label htmlFor="at-tipo">Registrar</label>
              <select id="at-tipo" value={tipo} onChange={(e) => setTipo(e.target.value)}>
                {Object.entries(TIPOS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            <div className="campo">
              <label htmlFor="at-vence">Prazo (vira tarefa)</label>
              <input id="at-vence" type="datetime-local" value={vence} onChange={(e) => setVence(e.target.value)} />
            </div>
          </div>
          <div className="campo">
            <label htmlFor="at-titulo">Título</label>
            <input id="at-titulo" value={titulo} onChange={(e) => setTitulo(e.target.value)}
                   placeholder={tipo === "ligacao" ? "ex.: liguei, pediu retorno sexta" : "ex.: mandar proposta"} />
          </div>
          <div className="campo">
            <label htmlFor="at-texto">Detalhes</label>
            <textarea id="at-texto" rows={2} value={texto} onChange={(e) => setTexto(e.target.value)} />
          </div>
          <button className="botao pequeno">Registrar</button>
        </form>
      </div>

      {d.atividades.length > 0 && (
        <>
          <h2>Linha do tempo</h2>
          <ul className="tempo">
            {d.atividades.map((a) => (
              <li key={a.id} className={a.vence_em ? (a.concluida_em ? "feita" : "tarefa") : ""}>
                <div>
                  {a.vence_em && (
                    <input type="checkbox" checked={!!a.concluida_em} style={{ width: "auto", marginRight: ".4rem" }}
                           aria-label={`Concluir ${a.titulo}`}
                           onChange={() => acao(() => post(`/api/atividades/${a.id}/concluir`, { desfazer: !!a.concluida_em }),
                                                a.concluida_em ? "Reaberta." : "Concluída.")} />
                  )}
                  <span className="etiqueta" style={{ marginRight: ".35rem" }}>{TIPOS[a.tipo] ?? a.tipo}</span>
                  <span style={a.concluida_em ? { textDecoration: "line-through" } : undefined}>{a.titulo}</span>
                  <button className="botao secundario pequeno" style={{ marginLeft: ".5rem", padding: ".1rem .4rem" }}
                          aria-label={`Excluir ${a.titulo}`}
                          onClick={() => confirm("Excluir?") && acao(() => del(`/api/atividades/${a.id}`), "Excluída.")}>×</button>
                </div>
                {a.texto && <div className="fraco" style={{ whiteSpace: "pre-wrap" }}>{a.texto}</div>}
                <div className="quando">
                  {quando(a.criada_em)}
                  {a.vence_em && ` · prazo ${quando(a.vence_em)}`}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
