"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import AvisoBase from "@/components/aviso-base";
import PainelFiltros, { aplicarFiltros, useFiltros } from "@/components/filtros";
import { BarList, Colunas, MapaUF } from "@/components/graficos";
import {
  fmt, get, moeda, PORTES, post, type Analise, type Lista, type Perfil,
} from "@/lib/api";

export default function Mercado() {
  const [aba, setAba] = useState<"mercado" | "icp">("mercado");
  const [listaInicial, setListaInicial] = useState<string>("");

  useEffect(() => {
    const p = new URLSearchParams(window.location.search).get("lista");
    if (p) { setAba("icp"); setListaInicial(p); }
  }, []);

  return (
    <>
      <h1>Mercado e ICP</h1>
      <p className="legenda">
        Onde estão as empresas do seu nicho, e como achar mais empresas parecidas com quem já compra de você.
      </p>
      <AvisoBase />
      <div className="abas" role="tablist">
        <button role="tab" aria-selected={aba === "mercado"} className={aba === "mercado" ? "ativa" : ""}
                onClick={() => setAba("mercado")}>Análise de mercado</button>
        <button role="tab" aria-selected={aba === "icp"} className={aba === "icp" ? "ativa" : ""}
                onClick={() => setAba("icp")}>Perfil da sua carteira (ICP)</button>
      </div>
      {aba === "mercado" ? <AnaliseMercado />
        : <PerfilCarteira listaInicial={listaInicial} irParaMercado={() => setAba("mercado")} />}
    </>
  );
}

function pct(n: number, total: number) {
  return total ? `${Math.round((100 * n) / total)}%` : "—";
}

function AnaliseMercado() {
  const router = useRouter();
  const { filtros, setFiltros, pronto } = useFiltros();
  const [a, setA] = useState<Analise | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [carregando, setCarregando] = useState(false);
  const t = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    if (!pronto) return;
    clearTimeout(t.current);
    t.current = setTimeout(() => {
      setCarregando(true);
      post<Analise>("/api/base/analise", { filtros })
        .then((x) => { setA(x); setErro(null); })
        .catch((e) => setErro(e.message))
        .finally(() => setCarregando(false));
    }, 350);
    return () => clearTimeout(t.current);
  }, [filtros, pronto]);

  const add = (campo: "ufs" | "municipios" | "cnaes", v: string) => {
    const atual = filtros[campo] ?? [];
    setFiltros({ ...filtros, [campo]: atual.includes(v) ? atual.filter((x) => x !== v) : [...atual, v] });
  };

  const r = a?.resumo;
  const tiles = r ? [
    { n: fmt(r.total), rot: "empresas no recorte" },
    { n: pct(r.com_celular, r.total), rot: `com celular (${fmt(r.com_celular)})` },
    { n: pct(r.com_email_proprio, r.total), rot: `com e-mail próprio (${fmt(r.com_email_proprio)})` },
    { n: pct(r.mei, r.total), rot: `são MEI (${fmt(r.mei)})` },
    { n: fmt(r.abertas_90d), rot: "abertas nos 90 dias da base" },
    { n: r.capital_mediano ? moeda(r.capital_mediano) : "—", rot: "capital social mediano" },
  ] : [];

  return (
    <div className="busca">
      <PainelFiltros valor={filtros} onChange={setFiltros} />
      <section aria-live="polite" aria-busy={carregando}>
        {erro && <p className="erro">{erro}</p>}
        {a && r && (
          <>
            <div className="grade" style={{ marginBottom: "1rem" }}>
              {tiles.map((x) => (
                <div className="cartao" key={x.rot}>
                  <div className="num mono" style={{ fontSize: "1.4rem" }}>{x.n}</div>
                  <div className="rot">{x.rot}</div>
                </div>
              ))}
            </div>
            <div className="acoes" style={{ marginBottom: "1rem" }}>
              <button className="botao" onClick={() => router.push("/base")}>
                Ver as {fmt(r.total)} empresas
              </button>
              <span className="fraco" style={{ fontSize: ".8rem", alignSelf: "center" }}>
                Clique numa barra ou num estado para filtrar.
              </span>
            </div>

            <div className="graficos">
              <div className="cartao grafico">
                <h3>Empresas por estado</h3>
                <p className="sub">quanto mais escuro, mais empresas</p>
                <MapaUF dados={a.por_uf} selecionadas={filtros.ufs ?? []} onClick={(uf) => add("ufs", uf)} />
              </div>
              <div className="cartao grafico">
                <h3>Cidades com mais empresas</h3>
                <p className="sub">top 15 do recorte</p>
                <BarList total={r.total} onClick={(c) => add("municipios", c)}
                         itens={a.por_municipio.map((m) => ({ chave: m.codigo, rotulo: `${m.nome}/${m.uf}`, valor: m.n }))} />
              </div>
              <div className="cartao grafico">
                <h3>Atividades mais comuns</h3>
                <p className="sub">CNAE principal, top 15</p>
                <BarList total={r.total} onClick={(c) => add("cnaes", c)}
                         itens={a.por_cnae.map((c) => ({ chave: c.codigo, rotulo: `${c.codigo} · ${c.descricao ?? ""}`, valor: c.n }))} />
              </div>
              <div className="cartao grafico">
                <h3>Porte</h3>
                <p className="sub">MEI separado da microempresa</p>
                <BarList total={r.total}
                         itens={a.por_porte.map((p) => ({ chave: p.porte, rotulo: PORTES[p.porte] ?? p.porte, valor: p.n }))} />
              </div>
              <div className="cartao grafico">
                <h3>Aberturas por ano</h3>
                <p className="sub">últimos 15 anos da base</p>
                <Colunas total={r.total}
                         itens={a.por_ano.map((y) => ({ chave: String(y.ano), rotulo: String(y.ano), valor: y.n }))} />
              </div>
              <div className="cartao grafico">
                <h3>Capital social</h3>
                <p className="sub">proxy de tamanho; MEI costuma declarar pouco</p>
                <BarList total={r.total}
                         itens={a.por_capital.map((c) => ({ chave: c.faixa, rotulo: c.faixa, valor: c.n }))} />
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  );
}

function PerfilCarteira({ listaInicial, irParaMercado }: {
  listaInicial: string; irParaMercado: () => void;
}) {
  const router = useRouter();
  const [listas, setListas] = useState<Lista[]>([]);
  const [lista, setLista] = useState(listaInicial);
  const [texto, setTexto] = useState("");
  const [p, setP] = useState<Perfil | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  useEffect(() => { get<Lista[]>("/api/listas").then(setListas).catch(() => {}); }, []);
  useEffect(() => { setLista(listaInicial); }, [listaInicial]);
  useEffect(() => {
    if (lista) analisar({ lista_id: Number(lista) });
  }, [lista]); // eslint-disable-line react-hooks/exhaustive-deps

  function analisar(corpo: object) {
    setErro(null);
    post<Perfil>("/api/base/perfil", corpo).then(setP).catch((e) => { setP(null); setErro(e.message); });
  }

  return (
    <>
      <p className="legenda" style={{ maxWidth: 680 }}>
        Cole os CNPJs de quem já é seu cliente (ou escolha uma lista). O sistema mostra o que essas
        empresas têm em comum e monta a busca que acha as parecidas na base.
      </p>
      <div className="ficha-grade" style={{ marginBottom: "1rem" }}>
        <div>
          <label htmlFor="icp-lista">Uma lista</label>
          <select id="icp-lista" value={lista} onChange={(e) => setLista(e.target.value)}>
            <option value="">escolher…</option>
            {listas.map((l) => <option key={l.id} value={l.id}>{l.nome} ({fmt(l.itens)})</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="icp-texto">Ou cole CNPJs</label>
          <textarea id="icp-texto" rows={3} value={texto} onChange={(e) => setTexto(e.target.value)} />
          <button className="botao secundario pequeno" style={{ marginTop: ".4rem" }} disabled={!texto.trim()}
                  onClick={() => { setLista(""); analisar({ texto }); }}>Analisar</button>
        </div>
      </div>
      {erro && <p className="erro">{erro}</p>}

      {p && (p.encontradas === 0 ? (
        <div className="aviso">Nenhum desses CNPJs está na base ({p.informadas} informados).</div>
      ) : (
        <>
          <div className="aviso info">
            <b>{fmt(p.parecidos_na_base)} empresas parecidas na base</b>
            Perfil montado com {p.encontradas} de {p.informadas} CNPJs encontrados
            {p.invalidos?.length ? ` (${p.invalidos.length} inválidos ignorados)` : ""}.
            <div className="acoes" style={{ marginTop: ".6rem" }}>
              <button className="botao pequeno" onClick={() => { aplicarFiltros(p.filtros!); router.push("/base"); }}>
                Buscar as parecidas
              </button>
              <button className="botao secundario pequeno" onClick={() => { aplicarFiltros(p.filtros!); irParaMercado(); }}>
                Analisar o mercado delas
              </button>
            </div>
          </div>
          <div className="graficos">
            <div className="cartao grafico">
              <h3>Atividades da carteira</h3>
              <p className="sub">classe CNAE (4 dígitos)</p>
              <BarList total={p.encontradas}
                       itens={(p.cnaes ?? []).map((c) => ({ chave: c.codigo, rotulo: `${c.codigo} · ${c.descricao ?? ""}`, valor: c.n }))} />
            </div>
            <div className="cartao grafico">
              <h3>Onde estão</h3>
              <p className="sub">cidades da carteira</p>
              <BarList total={p.encontradas}
                       itens={(p.municipios ?? []).map((m) => ({ chave: m.codigo, rotulo: m.nome, valor: m.n }))} />
            </div>
            <div className="cartao grafico">
              <h3>Porte</h3>
              <p className="sub">da carteira</p>
              <BarList total={p.encontradas}
                       itens={(p.portes ?? []).map((x) => ({ chave: x.porte, rotulo: PORTES[x.porte] ?? x.porte, valor: x.n }))} />
            </div>
          </div>
        </>
      ))}
    </>
  );
}
