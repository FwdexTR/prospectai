"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { fmt, get, type Cadencia } from "@/lib/api";

export default function Cadencias() {
  const [itens, setItens] = useState<Cadencia[] | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  useEffect(() => { get<Cadencia[]>("/api/cadencias").then(setItens).catch((e) => setErro(e.message)); }, []);

  if (erro) return <p className="erro">{erro}</p>;
  return (
    <>
      <div className="linha-topo">
        <div>
          <h1>Cadências</h1>
          <p className="legenda">
            Sequências de até 3 mensagens (WhatsApp ou e-mail) com intervalo em dias. O texto é
            montado por empresa e passa pelo validador; o envio continua manual, pela fila.
          </p>
        </div>
        <Link href="/cadencias/nova" className="botao">Nova cadência</Link>
      </div>
      {!itens ? <p className="legenda">Carregando…</p> : itens.length === 0 ? (
        <div className="vazio">
          Nenhuma cadência. <Link href="/cadencias/nova" style={{ color: "var(--acento)" }}>Crie a primeira</Link>{" "}
          a partir do modelo pronto.
        </div>
      ) : (
        <div className="rolagem">
          <table className="tabela">
            <thead>
              <tr><th>Cadência</th><th className="num-col">Passos</th><th className="num-col">Ativas</th>
                  <th className="num-col">Inscritas</th><th className="num-col">Responderam</th><th></th></tr>
            </thead>
            <tbody>
              {itens.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link href={`/cadencias/${c.id}`} style={{ color: "var(--acento)", fontWeight: 550 }}>{c.nome}</Link>
                    {c.descricao && <div className="fraco" style={{ fontSize: ".78rem" }}>{c.descricao}</div>}
                  </td>
                  <td className="num-col mono">{c.passos}</td>
                  <td className="num-col mono">{fmt(c.ativas)}</td>
                  <td className="num-col mono">{fmt(c.inscritas)}</td>
                  <td className="num-col mono">
                    {fmt(c.responderam)}
                    {c.inscritas > 0 && <span className="fraco"> ({Math.round((100 * c.responderam) / c.inscritas)}%)</span>}
                  </td>
                  <td>{!c.ativa && <span className="etiqueta">pausada</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
