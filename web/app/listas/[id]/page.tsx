"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useCallback, useEffect, useState } from "react";
import {
  baixar, del, ETAPA, fmt, get, post,
  type Cadencia, type Execucao, type ListaDetalhe, type ResultadoInscricao,
} from "@/lib/api";

export default function ListaDetalhePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const [l, setL] = useState<ListaDetalhe | null>(null);
  const [pagina, setPagina] = useState(1);
  const [erro, setErro] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [cadencias, setCadencias] = useState<Cadencia[]>([]);
  const [cadencia, setCadencia] = useState("");
  const [inscricao, setInscricao] = useState<ResultadoInscricao | null>(null);
  const [exec, setExec] = useState<Execucao | null>(null);
  const [texto, setTexto] = useState("");

  const carregar = useCallback(
    () => get<ListaDetalhe>(`/api/listas/${id}?pagina=${pagina}`).then(setL).catch((e) => setErro(e.message)),
    [id, pagina]);
  useEffect(() => { carregar(); }, [carregar]);
  useEffect(() => {
    get<Cadencia[]>("/api/cadencias").then((c) => setCadencias(c.filter((x) => x.ativa))).catch(() => {});
  }, []);

  async function acao<T>(fn: () => Promise<T>, msg: (x: T) => string) {
    try { const x = await fn(); setAviso(msg(x)); carregar(); }
    catch (e: any) { setAviso(`Erro: ${e.message}`); }
  }

  async function enriquecer() {
    try {
      const r = await post<{ execucao_id: string; empresas: number }>("/api/enriquecer", { lista_id: Number(id) });
      setAviso(`Enriquecendo ${r.empresas} empresas: procurando site, e-mail e WhatsApp…`);
      const t = setInterval(async () => {
        const e = await get<Execucao>(`/api/execucoes/${r.execucao_id}`);
        setExec(e);
        if (e.estado !== "rodando") {
          clearInterval(t);
          if (e.estado === "concluida") {
            const s = e.resumo;
            setAviso(`Pronto: ${s.enriquecidas} enriquecidas, ${s.sites_aceitos} sites confirmados, ` +
                     `${s.emails_do_site} e-mails e ${s.whatsapp_do_site} WhatsApp achados nos sites` +
                     (s.ignoradas_demo ? ` (${s.ignoradas_demo} de demonstração ignoradas).` : "."));
            carregar();
          } else setAviso(`Erro: ${e.erro}`);
        }
      }, 1500);
    } catch (e: any) { setAviso(`Erro: ${e.message}`); }
  }

  if (erro) return <p className="erro">{erro}</p>;
  if (!l) return <p className="legenda">Carregando…</p>;
  const paginas = Math.max(1, Math.ceil(l.total / l.por_pagina));

  return (
    <>
      <p className="fraco" style={{ fontSize: ".82rem", margin: "0 0 .5rem" }}><Link href="/listas">← listas</Link></p>
      <div className="linha-topo" style={{ alignItems: "flex-start" }}>
        <div>
          <h1>{l.nome}</h1>
          <p className="legenda">{fmt(l.total)} empresas · {fmt(l.reveladas)} no CRM</p>
        </div>
        <div className="acoes">
          <button className="botao secundario pequeno"
                  onClick={() => router.push(`/mercado?lista=${id}`)}>Ver perfil (ICP)</button>
          <button className="botao secundario pequeno"
                  onClick={() => baixar(`/api/listas/${id}/exportar`, `lista-${id}.csv`)
                                   .catch((e) => setAviso(`Erro: ${e.message}`))}>Exportar CSV</button>
          <button className="botao secundario pequeno" style={{ color: "var(--ruim)" }}
                  onClick={async () => {
                    if (!confirm(`Excluir a lista “${l.nome}”? As empresas continuam na base e no CRM.`)) return;
                    await del(`/api/listas/${id}`); router.push("/listas");
                  }}>Excluir</button>
        </div>
      </div>

      <div className="barra-acoes">
        <b>Passo a passo:</b>
        <button className="botao pequeno"
                onClick={() => acao(() => post<Record<string, number>>(`/api/listas/${id}/revelar`),
                  (x) => `${x.reveladas} reveladas no CRM` + (x.ja_existiam ? `, ${x.ja_existiam} já estavam` : "") + ".")}>
          1. Revelar todas
        </button>
        <button className="botao pequeno" disabled={!l.reveladas || exec?.estado === "rodando"} onClick={enriquecer}>
          2. Enriquecer
        </button>
        <select value={cadencia} onChange={(e) => setCadencia(e.target.value)} style={{ width: "auto" }}
                aria-label="Cadência">
          <option value="">3. escolher cadência…</option>
          {cadencias.map((c) => <option key={c.id} value={c.id}>{c.nome}</option>)}
        </select>
        <button className="botao pequeno" disabled={!cadencia}
                onClick={() => acao(() => post<ResultadoInscricao>(`/api/cadencias/${cadencia}/inscrever`,
                  { lista_id: Number(id), revelar_lista: true }),
                  (x) => { setInscricao(x); return `${x.inscritas.length} inscritas, ${x.recusadas.length} recusadas.`; })}>
          Inscrever
        </button>
        {cadencias.length === 0 && (
          <Link href="/cadencias/nova" style={{ color: "var(--acento)" }}>criar uma cadência</Link>
        )}
      </div>
      {exec?.estado === "rodando" && (
        <div style={{ marginBottom: ".75rem" }}>
          <div className="fraco" style={{ fontSize: ".8rem", marginBottom: ".3rem" }}>
            {exec.etapa} {exec.total > 1 ? `${exec.feito}/${exec.total}` : ""}
          </div>
          <div className="trilho"><div style={{ width: `${(100 * exec.feito) / Math.max(1, exec.total)}%` }} /></div>
        </div>
      )}
      {aviso && <div className="aviso info">{aviso}</div>}
      {inscricao && inscricao.recusadas.length > 0 && (
        <details className="aviso" open>
          <summary><b>Por que {inscricao.recusadas.length} não entraram</b></summary>
          <ul style={{ margin: ".5rem 0 0", paddingLeft: "1.1rem" }}>
            {Object.entries(inscricao.recusadas.reduce<Record<string, number>>((a, r) => {
              a[r.motivo] = (a[r.motivo] ?? 0) + 1; return a;
            }, {})).map(([m, n]) => <li key={m}>{m}: {n}</li>)}
          </ul>
        </details>
      )}

      {l.itens.length === 0 ? (
        <div className="vazio">Lista vazia.</div>
      ) : (
        <div className="rolagem">
          <table className="tabela">
            <thead>
              <tr><th>Empresa</th><th>Local</th><th>Contato</th><th>CRM</th><th></th></tr>
            </thead>
            <tbody>
              {l.itens.map((i) => (
                <tr key={i.cnpj}>
                  <td>
                    <Link href={`/base/${i.cnpj}`} style={{ fontWeight: 550 }}>{i.nome ?? i.cnpj}</Link>
                    {i.fonte === "demo" && <span className="etiqueta demo" style={{ marginLeft: ".35rem" }}>demo</span>}
                    {i.fora_da_base && <span className="etiqueta dor" style={{ marginLeft: ".35rem" }}>fora da base</span>}
                    {i.cnae_descricao && <div className="fraco" style={{ fontSize: ".75rem" }}>{i.cnae_descricao}</div>}
                  </td>
                  <td style={{ fontSize: ".82rem" }}>{i.municipio ? `${i.municipio}/${i.uf}` : "—"}</td>
                  <td style={{ fontSize: ".78rem" }}>
                    {i.tem_celular ? "WhatsApp" : ""}{i.tem_celular && i.tem_email ? " · " : ""}
                    {i.tem_email ? ((i.email_qtd ?? 0) > 3 ? "e-mail do contador" : "e-mail") : ""}
                  </td>
                  <td>
                    {i.crm ? (
                      <Link href={`/leads/${i.crm.empresa_id}`} className="etiqueta info">
                        {ETAPA[i.crm.estado ?? "NOVO"]}
                      </Link>
                    ) : <span className="fraco" style={{ fontSize: ".78rem" }}>não revelada</span>}
                  </td>
                  <td>
                    <button className="botao secundario pequeno" aria-label={`Tirar ${i.nome} da lista`}
                            onClick={() => acao(() => post(`/api/listas/${id}/remover`, { cnpjs: [i.cnpj] }),
                                                () => "Removida da lista.")}>tirar</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {l.total > l.por_pagina && (
        <div className="paginacao">
          <button className="botao secundario pequeno" disabled={pagina <= 1} onClick={() => setPagina(pagina - 1)}>← anterior</button>
          <span>página {pagina} de {paginas}</span>
          <button className="botao secundario pequeno" disabled={pagina >= paginas} onClick={() => setPagina(pagina + 1)}>próxima →</button>
        </div>
      )}

      <h2>Adicionar CNPJs</h2>
      <textarea rows={3} value={texto} onChange={(e) => setTexto(e.target.value)}
                placeholder="cole CNPJs aqui" aria-label="CNPJs para adicionar" />
      <div className="acoes" style={{ marginTop: ".5rem" }}>
        <button className="botao secundario" disabled={!texto.trim()}
                onClick={() => acao(() => post<{ adicionados: number; invalidos: string[] }>(
                  `/api/listas/${id}/itens`, { texto }),
                  (x) => { setTexto(""); return `${x.adicionados} adicionados` +
                    (x.invalidos.length ? `, ${x.invalidos.length} inválidos.` : "."); })}>
          Adicionar
        </button>
      </div>
    </>
  );
}
