"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { dataFmt, get, moeda, post, type LeadFunil } from "@/lib/api";

// "Em cadência" agrupa SELECIONADO e TOQUE_1..3: essas etapas só mudam pelo
// envio (spec, Parte 4), então a coluna recebe cartão nenhum por arrasto.
const COLUNAS: { chave: string; rotulo: string; estados: string[]; travada?: boolean }[] = [
  { chave: "NOVO", rotulo: "Novo", estados: ["NOVO"] },
  { chave: "CADENCIA", rotulo: "Em cadência", estados: ["SELECIONADO", "TOQUE_1", "TOQUE_2", "TOQUE_3"], travada: true },
  { chave: "EM_CONVERSA", rotulo: "Em conversa", estados: ["EM_CONVERSA"] },
  { chave: "REUNIAO", rotulo: "Reunião", estados: ["REUNIAO"] },
  { chave: "PROPOSTA", rotulo: "Proposta", estados: ["PROPOSTA"] },
  { chave: "FECHADO", rotulo: "Fechado", estados: ["FECHADO"] },
  { chave: "PERDIDO", rotulo: "Perdido", estados: ["PERDIDO"] },
  { chave: "DORMENTE", rotulo: "Dormente", estados: ["DORMENTE"] },
];
const DESTINOS = COLUNAS.filter((c) => !c.travada);

type Contagens = Record<string, { n: number; valor_estimado: number | null; valor_fechado: number | null }>;

export default function Funil() {
  const [leads, setLeads] = useState<LeadFunil[] | null>(null);
  const [contagens, setContagens] = useState<Contagens | null>(null);
  const [busca, setBusca] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [alvo, setAlvo] = useState<string | null>(null);
  const arrastando = useRef<LeadFunil | null>(null);

  const carregar = useCallback(() => {
    get<{ leads: LeadFunil[]; contagens: Contagens | null }>(
      `/api/crm/funil${busca ? `?busca=${encodeURIComponent(busca)}` : ""}`)
      .then((r) => { setLeads(r.leads); setContagens(r.contagens); setErro(null); })
      .catch((e) => setErro(e.message));
  }, [busca]);
  useEffect(() => { const t = setTimeout(carregar, 250); return () => clearTimeout(t); }, [carregar]);

  async function mover(l: LeadFunil, destino: string) {
    if (l.estado === destino || l.estado === "OPT_OUT") return;
    const emCadencia = COLUNAS[1].estados.includes(l.estado);
    if (emCadencia && !confirm(`Mover ${l.nome} cancela os toques agendados da cadência. Continuar?`)) return;
    let valor: number | null = null;
    let motivo: string | null = null;
    if (destino === "FECHADO") {
      const v = prompt("Valor fechado (R$)?", String(l.valor_estimado ?? ""));
      if (v === null) return;
      valor = Number(v.replace(/\./g, "").replace(",", ".")) || null;
    }
    if (destino === "PERDIDO") {
      motivo = prompt("Motivo da perda (opcional)?") ?? null;
    }
    try {
      await post(`/api/leads/${l.empresa_id}/estado`, { estado: destino, valor, motivo_perda: motivo });
      setAviso(`${l.nome} → ${COLUNAS.find((c) => c.estados.includes(destino))?.rotulo}.`);
      carregar();
    } catch (e: any) { setAviso(`Erro: ${e.message}`); }
  }

  if (erro) return <p className="erro">{erro}</p>;
  const optOut = contagens?.OPT_OUT?.n ?? leads?.filter((l) => l.estado === "OPT_OUT").length ?? 0;

  return (
    <>
      <div className="linha-topo" style={{ flexWrap: "wrap" }}>
        <div>
          <h1>Funil</h1>
          <p className="legenda">
            Arraste o cartão ou use o seletor. “Em cadência” só muda pelo envio das mensagens.
          </p>
        </div>
        <input value={busca} onChange={(e) => setBusca(e.target.value)} placeholder="buscar empresa ou cidade"
               style={{ maxWidth: 260 }} aria-label="Buscar no funil" />
      </div>
      {aviso && <div className="aviso info">{aviso}</div>}
      {!leads ? <p className="legenda">Carregando…</p> : leads.length === 0 ? (
        <div className="vazio">
          O funil está vazio. Revele empresas a partir da{" "}
          <Link href="/base" style={{ color: "var(--acento)" }}>busca</Link> ou de uma{" "}
          <Link href="/listas" style={{ color: "var(--acento)" }}>lista</Link>.
        </div>
      ) : (
        <div className="kanban">
          {COLUNAS.map((col) => {
            const itens = leads.filter((l) => col.estados.includes(l.estado));
            // Total vem do banco (a lista de cartões tem teto); com busca, conta o que veio.
            const total = contagens
              ? col.estados.reduce((a, e) => a + (contagens[e]?.n ?? 0), 0) : itens.length;
            const soma = contagens
              ? col.estados.reduce((a, e) => a + ((col.chave === "FECHADO"
                  ? contagens[e]?.valor_fechado : contagens[e]?.valor_estimado) ?? 0), 0)
              : itens.reduce((a, l) => a + ((col.chave === "FECHADO" ? l.valor_fechado : l.valor_estimado) ?? 0), 0);
            return (
              <section key={col.chave} aria-label={col.rotulo}
                       className={`coluna-kanban ${alvo === col.chave && !col.travada ? "alvo" : ""}`}
                       onDragOver={(e) => { if (!col.travada) { e.preventDefault(); setAlvo(col.chave); } }}
                       onDragLeave={() => setAlvo(null)}
                       onDrop={(e) => {
                         e.preventDefault(); setAlvo(null);
                         if (arrastando.current && !col.travada) mover(arrastando.current, col.estados[0]);
                       }}>
                <header>
                  {col.rotulo}
                  <span>{total.toLocaleString("pt-BR")}{soma ? ` · ${moeda(soma)}` : ""}</span>
                </header>
                {itens.slice(0, 200).map((l) => (
                  <article key={l.empresa_id} className={`cartao-lead ${col.travada ? "travado" : ""}`}
                           draggable onDragStart={() => { arrastando.current = l; }}
                           onDragEnd={() => { arrastando.current = null; }}>
                    <Link href={`/leads/${l.empresa_id}`} className="nome">{l.nome}</Link>
                    <div className="meta">
                      {[l.cidade, l.uf].filter(Boolean).join("/")}
                      {l.faixa && <> · faixa {l.faixa}</>}
                      {l.fonte === "demo" && <> · <span style={{ color: "var(--atencao)" }}>demo</span></>}
                    </div>
                    {l.cadencia && <div className="meta">cadência: {l.cadencia} ({l.estado.replace("_", " ").toLowerCase()})</div>}
                    {l.tarefas > 0 && (
                      <div className="meta" style={{ color: l.proxima_tarefa && new Date(l.proxima_tarefa.replace(" ", "T") + "Z") < new Date() ? "var(--ruim)" : undefined }}>
                        {l.tarefas} tarefa{l.tarefas > 1 ? "s" : ""} · próxima {dataFmt(l.proxima_tarefa)}
                      </div>
                    )}
                    {(l.valor_estimado || l.valor_fechado) ? (
                      <div className="meta">{moeda(l.valor_fechado ?? l.valor_estimado)}</div>
                    ) : null}
                    <select aria-label={`Mover ${l.nome}`} value="" onChange={(e) => e.target.value && mover(l, e.target.value)}>
                      <option value="">mover para…</option>
                      {DESTINOS.filter((d) => !d.estados.includes(l.estado)).map((d) => (
                        <option key={d.chave} value={d.estados[0]}>{d.rotulo}</option>
                      ))}
                    </select>
                  </article>
                ))}
                {total > Math.min(itens.length, 200) && (
                  <p className="fraco" style={{ fontSize: ".75rem" }}>
                    +{(total - Math.min(itens.length, 200)).toLocaleString("pt-BR")} (use a busca)
                  </p>
                )}
              </section>
            );
          })}
        </div>
      )}
      {optOut > 0 && (
        <p className="legenda">{optOut} empresa{optOut > 1 ? "s pediram" : " pediu"} para não receber contato (opt-out) e não aparece{optOut > 1 ? "m" : ""} no funil.</p>
      )}
    </>
  );
}
