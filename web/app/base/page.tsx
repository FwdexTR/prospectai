"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import PainelFiltros, { useFiltros } from "@/components/filtros";
import AvisoBase from "@/components/aviso-base";
import {
  baixar, dataFmt, ETAPA, fmt, get, moeda, post,
  type ItemBase, type Lista, type ResultadoBusca,
} from "@/lib/api";

const ORDENS: Record<string, string> = {
  padrao: "CNPJ", nome: "Nome (A–Z)", recentes: "Abertas há menos tempo",
  antigas: "Mais antigas", capital: "Maior capital social",
};

export default function BuscarEmpresas() {
  const router = useRouter();
  const { filtros, setFiltros, pronto } = useFiltros();
  const [ordem, setOrdem] = useState("padrao");
  const [pagina, setPagina] = useState(1);
  const [r, setR] = useState<ResultadoBusca | null>(null);
  const [carregando, setCarregando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [aviso, setAviso] = useState<string | null>(null);
  const [listas, setListas] = useState<Lista[]>([]);
  const [destino, setDestino] = useState<string>("");
  const [novaLista, setNovaLista] = useState("");
  const t = useRef<ReturnType<typeof setTimeout>>(undefined);

  const buscar = useCallback(() => {
    setCarregando(true);
    post<ResultadoBusca>("/api/base/busca", { filtros, pagina, por_pagina: 50, ordem })
      .then((x) => { setR(x); setErro(null); })
      .catch((e) => setErro(e.message))
      .finally(() => setCarregando(false));
  }, [filtros, pagina, ordem]);

  useEffect(() => {
    if (!pronto) return;
    clearTimeout(t.current);
    t.current = setTimeout(buscar, 300);
    return () => clearTimeout(t.current);
  }, [buscar, pronto]);

  useEffect(() => { get<Lista[]>("/api/listas").then(setListas).catch(() => {}); }, []);

  const mudarFiltros = (f: typeof filtros) => { setFiltros(f); setPagina(1); setSel(new Set()); };
  const alternar = (c: string) =>
    setSel((s) => { const n = new Set(s); n.has(c) ? n.delete(c) : n.add(c); return n; });
  const todosDaPagina = r ? r.itens.every((i) => sel.has(i.cnpj)) && r.itens.length > 0 : false;

  async function acao<T>(fn: () => Promise<T>, msg: (x: T) => string) {
    try { const x = await fn(); setAviso(msg(x)); buscar(); }
    catch (e: any) { setAviso(`Erro: ${e.message}`); }
  }

  async function paraLista(origem: "selecao" | "busca") {
    const corpo = origem === "selecao" ? { cnpjs: [...sel] } : { filtros, limite: 5000 };
    if (destino === "nova" || !destino) {
      const nome = novaLista.trim() || `Busca de ${new Date().toLocaleDateString("pt-BR")}`;
      await acao(() => post<{ id: number; adicionados: number }>("/api/listas", { nome, ...corpo }),
        (x) => `Lista “${nome}” criada com ${fmt(x.adicionados)} empresas.`);
    } else {
      await acao(() => post<{ adicionados: number }>(`/api/listas/${destino}/itens`, corpo),
        (x) => `${fmt(x.adicionados)} empresas adicionadas à lista.`);
    }
    setNovaLista("");
    get<Lista[]>("/api/listas").then(setListas).catch(() => {});
  }

  const paginas = r ? Math.max(1, Math.ceil(r.total / r.por_pagina)) : 1;

  return (
    <>
      <h1>Buscar empresas</h1>
      <p className="legenda">
        Base aberta do CNPJ (Receita Federal). Filtre, salve em lista, revele no CRM.
      </p>
      <AvisoBase />

      <div className="busca">
        <PainelFiltros valor={filtros} onChange={mudarFiltros} />

        <section aria-live="polite">
          <div className="linha-topo" style={{ marginBottom: ".75rem", flexWrap: "wrap" }}>
            <div>
              <div className="contador mono">{r ? fmt(r.total) : "…"}</div>
              <div className="fraco" style={{ fontSize: ".8rem" }}>
                empresas {carregando && "· buscando…"}
              </div>
            </div>
            <div className="acoes" style={{ alignItems: "center" }}>
              <label htmlFor="ordem" style={{ margin: 0 }}>Ordem</label>
              <select id="ordem" value={ordem} style={{ width: "auto" }}
                      onChange={(e) => { setOrdem(e.target.value); setPagina(1); }}>
                {Object.entries(ORDENS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
              <button className="botao secundario pequeno"
                      onClick={() => router.push("/mercado")}>Analisar esta busca</button>
              <button className="botao secundario pequeno" disabled={!r?.total}
                      onClick={() => baixar("/api/base/exportar", "empresas.csv", { filtros })
                                       .catch((e) => setAviso(`Erro: ${e.message}`))}>
                Exportar CSV
              </button>
            </div>
          </div>

          <div className="barra-acoes">
            <span>{sel.size > 0 ? <b>{sel.size} selecionadas</b> : "Nenhuma selecionada"}</span>
            <select value={destino} onChange={(e) => setDestino(e.target.value)}
                    style={{ width: "auto" }} aria-label="Lista de destino">
              <option value="">nova lista…</option>
              {listas.map((l) => <option key={l.id} value={l.id}>{l.nome} ({fmt(l.itens)})</option>)}
            </select>
            {!destino && (
              <input value={novaLista} onChange={(e) => setNovaLista(e.target.value)}
                     placeholder="nome da lista" style={{ width: 170 }} aria-label="Nome da nova lista" />
            )}
            <button className="botao pequeno" disabled={!sel.size} onClick={() => paraLista("selecao")}>
              Selecionadas → lista
            </button>
            <button className="botao secundario pequeno" disabled={!r?.total}
                    onClick={() => paraLista("busca")}
                    title="Até 5.000 empresas desta busca">
              Busca inteira → lista
            </button>
            <button className="botao secundario pequeno" disabled={!sel.size}
                    onClick={() => acao(() => post<{ reveladas: unknown[]; ja_existiam: unknown[] }>(
                      "/api/revelar", { cnpjs: [...sel] }),
                      (x) => `${x.reveladas.length} reveladas no CRM` +
                             (x.ja_existiam.length ? ` (${x.ja_existiam.length} já estavam)` : "") + ".")}>
              Revelar no CRM
            </button>
          </div>
          {aviso && <div className="aviso info">{aviso}</div>}
          {erro && <p className="erro">{erro}</p>}

          {r && r.itens.length === 0 ? (
            <div className="vazio">Nenhuma empresa com esses filtros.</div>
          ) : r && (
            <div className="rolagem">
              <table className="tabela">
                <thead>
                  <tr>
                    <th className="check">
                      <input type="checkbox" aria-label="Selecionar a página" checked={todosDaPagina}
                             onChange={() => setSel((s) => {
                               const n = new Set(s);
                               r.itens.forEach((i) => todosDaPagina ? n.delete(i.cnpj) : n.add(i.cnpj));
                               return n;
                             })} />
                    </th>
                    <th>Empresa</th><th>Atividade</th><th>Local</th><th>Porte</th>
                    <th>Abertura</th><th>Contato</th>
                  </tr>
                </thead>
                <tbody>
                  {r.itens.map((i) => <Linha key={i.cnpj} i={i} marcado={sel.has(i.cnpj)}
                                             onMarcar={() => alternar(i.cnpj)} />)}
                </tbody>
              </table>
            </div>
          )}

          {r && r.total > r.por_pagina && (
            <div className="paginacao">
              <button className="botao secundario pequeno" disabled={pagina <= 1}
                      onClick={() => setPagina((p) => p - 1)}>← anterior</button>
              <span>página {fmt(pagina)} de {fmt(paginas)}</span>
              <button className="botao secundario pequeno" disabled={pagina >= paginas}
                      onClick={() => setPagina((p) => p + 1)}>próxima →</button>
            </div>
          )}
        </section>
      </div>
    </>
  );
}

function Linha({ i, marcado, onMarcar }: { i: ItemBase; marcado: boolean; onMarcar: () => void }) {
  const contador = i.tem_email && (i.email_qtd ?? 0) > 3;
  return (
    <tr>
      <td className="check">
        <input type="checkbox" checked={marcado} onChange={onMarcar} aria-label={`Selecionar ${i.nome}`} />
      </td>
      <td style={{ maxWidth: 260 }}>
        <Link href={`/base/${i.cnpj}`} style={{ fontWeight: 550 }}>{i.nome}</Link>
        {i.fonte === "demo" && <span className="etiqueta demo" style={{ marginLeft: ".35rem" }}>demo</span>}
        {i.crm && (
          <Link href={`/leads/${i.crm.empresa_id}`} className="etiqueta info" style={{ marginLeft: ".35rem" }}>
            {ETAPA[i.crm.estado ?? "NOVO"] ?? "no CRM"}
          </Link>
        )}
        {!i.matriz && <span className="etiqueta" style={{ marginLeft: ".35rem" }}>filial</span>}
        {i.razao_social && i.razao_social !== i.nome && (
          <div className="fraco" style={{ fontSize: ".75rem" }}>{i.razao_social}</div>
        )}
      </td>
      <td style={{ maxWidth: 240, fontSize: ".8rem" }}>
        <span className="mono fraco">{i.cnae}</span> {i.cnae_descricao ?? ""}
      </td>
      <td style={{ fontSize: ".82rem" }}>
        {i.municipio}/{i.uf}
        {i.bairro && <div className="fraco" style={{ fontSize: ".75rem" }}>{i.bairro}</div>}
      </td>
      <td style={{ fontSize: ".8rem", whiteSpace: "nowrap" }}>
        {i.porte_rotulo ?? "—"}
        {i.capital_social ? <div className="fraco" style={{ fontSize: ".74rem" }}>{moeda(i.capital_social)}</div> : null}
      </td>
      <td className="mono fraco" style={{ fontSize: ".8rem" }}>{dataFmt(i.data_abertura)}</td>
      <td style={{ fontSize: ".78rem", whiteSpace: "nowrap" }}>
        <span className={i.tem_celular ? "icone-ok" : "icone-nao"}
              title={i.tem_celular ? "tem celular" : "sem celular"}>
          {i.tem_celular ? "✓" : "–"} WhatsApp
        </span>
        <br />
        <span className={i.tem_email && !contador ? "icone-ok" : "icone-nao"}
              title={contador ? `e-mail usado por ${i.email_qtd} empresas (provável contador)` : undefined}>
          {i.tem_email ? (contador ? "≈" : "✓") : "–"} e-mail{contador ? " do contador" : ""}
        </span>
      </td>
    </tr>
  );
}
