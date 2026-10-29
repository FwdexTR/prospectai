"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { del, get, post, type Atividade } from "@/lib/api";

const TIPO: Record<string, string> = {
  tarefa: "Tarefa", ligacao: "Ligar", reuniao: "Reunião", email: "E-mail",
  whatsapp: "WhatsApp", nota: "Nota",
};

function quando(v: string | null) {
  if (!v) return "—";
  const d = new Date(v.replace(" ", "T") + (v.length > 10 ? "Z" : "T12:00:00"));
  return d.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: v.length > 10 ? "short" : undefined });
}

export default function Tarefas() {
  const [itens, setItens] = useState<Atividade[] | null>(null);
  const [todas, setTodas] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  const carregar = useCallback(
    () => get<Atividade[]>(`/api/tarefas?todas=${todas}`).then(setItens).catch((e) => setErro(e.message)),
    [todas]);
  useEffect(() => { carregar(); }, [carregar]);

  if (erro) return <p className="erro">{erro}</p>;
  const atrasadas = itens?.filter((t) => t.atrasada).length ?? 0;

  return (
    <>
      <div className="linha-topo">
        <div>
          <h1>Tarefas</h1>
          <p className="legenda">
            {atrasadas ? <span className="erro">{atrasadas} atrasada{atrasadas > 1 ? "s" : ""}. </span> : null}
            Crie tarefas na página de cada lead.
          </p>
        </div>
        <label className="marcar">
          <input type="checkbox" checked={todas} onChange={(e) => setTodas(e.target.checked)} />
          mostrar concluídas
        </label>
      </div>
      {!itens ? <p className="legenda">Carregando…</p> : itens.length === 0 ? (
        <div className="vazio">Nenhuma tarefa pendente.</div>
      ) : (
        <div className="rolagem">
          <table className="tabela">
            <thead><tr><th></th><th>Tarefa</th><th>Empresa</th><th>Prazo</th><th></th></tr></thead>
            <tbody>
              {itens.map((t) => (
                <tr key={t.id} style={t.concluida_em ? { opacity: .55 } : undefined}>
                  <td className="check">
                    <input type="checkbox" checked={!!t.concluida_em} aria-label={`Concluir ${t.titulo}`}
                           onChange={() => post(`/api/atividades/${t.id}/concluir`, { desfazer: !!t.concluida_em }).then(carregar)} />
                  </td>
                  <td>
                    <span className="etiqueta" style={{ marginRight: ".4rem" }}>{TIPO[t.tipo] ?? t.tipo}</span>
                    {t.titulo}
                    {t.texto && <div className="fraco" style={{ fontSize: ".78rem" }}>{t.texto}</div>}
                  </td>
                  <td><Link href={`/leads/${t.empresa_id}`} style={{ color: "var(--acento)" }}>{t.empresa_nome}</Link></td>
                  <td className={t.atrasada ? "erro" : "fraco"} style={{ whiteSpace: "nowrap" }}>{quando(t.vence_em)}</td>
                  <td>
                    <button className="botao secundario pequeno" aria-label={`Excluir ${t.titulo}`}
                            onClick={() => confirm("Excluir esta tarefa?") && del(`/api/atividades/${t.id}`).then(carregar)}>
                      excluir
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
