"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { get, type Campanha, type Painel } from "@/lib/api";

export default function PainelPage() {
  const [p, setP] = useState<Painel | null>(null);
  const [campanhas, setCampanhas] = useState<Campanha[]>([]);
  const [erro, setErro] = useState<string | null>(null);

  useEffect(() => {
    const carregar = () =>
      Promise.all([get<Painel>("/api/painel"), get<Campanha[]>("/api/campanhas")])
        .then(([a, b]) => { setP(a); setCampanhas(b); setErro(null); })
        .catch((e) => setErro(e.message));
    carregar();
    // A visita à página de prova é o sinal mais urgente do sistema: vale a
    // pena reconsultar sozinho enquanto o painel estiver aberto.
    const t = setInterval(carregar, 30_000);
    return () => clearInterval(t);
  }, []);

  if (erro)
    return (
      <>
        <h1>Painel</h1>
        <p className="erro">Não consegui falar com a API: {erro}</p>
        <p className="legenda">
          Suba o motor com <code>uvicorn prospectai.api:app --port 8000</code> na
          pasta <code>engine/</code>.
        </p>
      </>
    );

  if (!p) return <p className="legenda">Carregando…</p>;

  const cartoes = [
    { n: p.reveladas, r: "reveladas da base" },
    { n: p.em_cadencia, r: "em cadência" },
    { n: p.tarefas_vencidas, r: "tarefas atrasadas" },
    { n: p.empresas, r: "empresas no CRM" },
    { n: p.sem_site, r: "sem site" },
    { n: p.faixa_a + p.faixa_b, r: "leads A + B" },
    { n: p.enviadas, r: "mensagens enviadas" },
    { n: p.respostas, r: "respostas" },
    { n: p.provas_abertas, r: "análises abertas" },
    { n: p.em_conversa, r: "em conversa" },
    { n: p.fechados, r: "fechados" },
  ];

  return (
    <>
      <h1>Painel</h1>
      <p className="legenda">
        Tudo aqui foi medido — nenhum número é estimado.
      </p>

      {!p.config.tem_places && (
        <div className="aviso info">
          <b>Rodando sem a chave da Google Places</b>
          A fonte é o Overture Maps: grátis, com telefone para cerca de 86% das empresas
          (medido em Campos), mas sem contagem de avaliações. Sem isso o eixo “dinheiro”
          do score fica ausente, os leads não passam da faixa B e tendem a empatar. Configure{" "}
          <code>GOOGLE_PLACES_API_KEY</code> no arquivo <code>.env</code> para ligar
          a priorização de verdade.
        </div>
      )}

      {p.alertas.map((a, i) => (
        <div key={i} className={`aviso ${a.urgencia === "alta" ? "alta" : ""}`}>
          <b>
            {a.tipo === "prova_aberta"
              ? "Alguém abriu a análise"
              : a.tipo === "circuit_breaker"
              ? "Envio pausado automaticamente"
              : "Follow-up pendente"}
          </b>
          {a.texto}
          {a.empresa_id ? (
            <> — <Link href={`/leads/${a.empresa_id}`} style={{ color: "var(--acento)" }}>ver lead</Link></>
          ) : null}
        </div>
      ))}

      <div className="grade">
        {cartoes.map((c) => (
          <div className="cartao" key={c.r}>
            <div className="num mono">{c.n}</div>
            <div className="rot">{c.r}</div>
          </div>
        ))}
      </div>

      {p.tarefas_vencidas > 0 && (
        <div className="aviso alta" style={{ marginTop: "1rem" }}>
          <b>{p.tarefas_vencidas} tarefa{p.tarefas_vencidas > 1 ? "s" : ""} atrasada{p.tarefas_vencidas > 1 ? "s" : ""}</b>
          <Link href="/tarefas" style={{ color: "var(--acento)" }}>Ver tarefas</Link>
        </div>
      )}

      <h2>Prospecção B2B</h2>
      <div className="acoes">
        <Link className="botao" href="/base">Buscar empresas</Link>
        <Link className="botao secundario" href="/listas">Listas</Link>
        <Link className="botao secundario" href="/mercado">Mercado e ICP</Link>
        <Link className="botao secundario" href="/crm">Funil</Link>
        <Link className="botao secundario" href="/cadencias">Cadências</Link>
      </div>

      <h2>Buscas com laudo de site</h2>
      {campanhas.length === 0 ? (
        <div className="vazio">
          Nenhuma busca ainda. <Link href="/nova" style={{ color: "var(--acento)" }}>Criar a primeira</Link>.
        </div>
      ) : (
        <div className="rolagem">
          <table className="tabela">
            <thead>
              <tr>
                <th>Cidade</th><th>Ramos</th>
                <th className="num-col">Empresas</th>
                <th className="num-col">Com mensagem</th>
                <th>Quando</th>
              </tr>
            </thead>
            <tbody>
              {campanhas.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link href={`/campanhas/${c.id}`} style={{ color: "var(--acento)", fontWeight: 550 }}>
                      {c.cidade}/{c.uf}
                    </Link>
                  </td>
                  <td className="fraco">{c.ramos.join(", ")}</td>
                  <td className="num-col mono">{c.empresas}</td>
                  <td className="num-col mono">{c.com_mensagem}</td>
                  <td className="fraco">{c.criada_em}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
