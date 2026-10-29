"use client";

import { useEffect, useRef, useState } from "react";
import { get, PORTES, UFS, type Atalho, type Filtros, type Opcao } from "@/lib/api";

// Filtros e rótulos (nome do município, descrição do CNAE) ficam na sessão:
// a busca e a análise de mercado trabalham sobre o mesmo recorte.
const CHAVE = "prospectai.filtros";
const CHAVE_ROT = "prospectai.rotulos";

export const FILTROS_PADRAO: Filtros = { situacoes: ["02"] };

function ler<T>(chave: string, padrao: T): T {
  try {
    const v = sessionStorage.getItem(chave);
    return v ? (JSON.parse(v) as T) : padrao;
  } catch {
    return padrao;
  }
}
function gravar(chave: string, v: unknown) {
  try { sessionStorage.setItem(chave, JSON.stringify(v)); } catch {}
}

export function useFiltros() {
  const [filtros, setFiltros] = useState<Filtros>(FILTROS_PADRAO);
  const [pronto, setPronto] = useState(false);
  useEffect(() => { setFiltros(ler(CHAVE, FILTROS_PADRAO)); setPronto(true); }, []);
  const mudar = (f: Filtros) => { setFiltros(f); gravar(CHAVE, f); };
  return { filtros, setFiltros: mudar, pronto };
}

export function aplicarFiltros(f: Filtros) {
  gravar(CHAVE, f);
}

const rotulos: Record<string, string> = {};
function lembrar(chave: string, rotulo: string) {
  rotulos[chave] = rotulo;
  gravar(CHAVE_ROT, { ...ler<Record<string, string>>(CHAVE_ROT, {}), [chave]: rotulo });
}
function rotulo(chave: string) {
  return rotulos[chave] ?? ler<Record<string, string>>(CHAVE_ROT, {})[chave] ?? chave;
}

/** Quantos filtros estão ativos, para mostrar na tela. */
export function contarFiltros(f: Filtros): number {
  let n = 0;
  if (f.q?.trim()) n++;
  n += (f.ufs?.length ?? 0) + (f.municipios?.length ?? 0) + (f.cnaes?.length ?? 0);
  n += f.portes?.length ?? 0;
  if (f.mei && f.mei !== "todos") n++;
  if (f.simples && f.simples !== "todos") n++;
  if (f.capital_min != null) n++;
  if (f.capital_max != null) n++;
  if (f.abertura_de) n++;
  if (f.abertura_ate) n++;
  for (const k of ["com_telefone", "com_celular", "com_email", "email_proprio", "so_matriz"] as const)
    if (f[k]) n++;
  if (f.bairro?.trim()) n++;
  return n;
}

function Autocompletar({ placeholder, buscar, onEscolher, rotuloDe, id }: {
  placeholder: string; id: string;
  buscar: (q: string) => Promise<Opcao[]>;
  onEscolher: (o: Opcao) => void;
  rotuloDe: (o: Opcao) => React.ReactNode;
}) {
  const [q, setQ] = useState("");
  const [opcoes, setOpcoes] = useState<Opcao[]>([]);
  const [aberto, setAberto] = useState(false);
  const t = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    clearTimeout(t.current);
    if (q.trim().length < 2) { setOpcoes([]); return; }
    t.current = setTimeout(() => {
      buscar(q).then((o) => { setOpcoes(o); setAberto(true); }).catch(() => setOpcoes([]));
    }, 220);
    return () => clearTimeout(t.current);
  }, [q, buscar]);

  return (
    <div className="auto">
      <input id={id} value={q} placeholder={placeholder} autoComplete="off"
             onChange={(e) => setQ(e.target.value)}
             onFocus={() => opcoes.length && setAberto(true)}
             onBlur={() => setTimeout(() => setAberto(false), 150)}
             onKeyDown={(e) => {
               if (e.key === "Enter" && opcoes[0]) {
                 e.preventDefault(); onEscolher(opcoes[0]); setQ(""); setOpcoes([]);
               }
             }} />
      {aberto && opcoes.length > 0 && (
        <div className="auto-lista" role="listbox">
          {opcoes.map((o) => (
            <button type="button" key={o.codigo} role="option" aria-selected={false}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => { onEscolher(o); setQ(""); setOpcoes([]); setAberto(false); }}>
              {rotuloDe(o)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function PainelFiltros({ valor, onChange }: {
  valor: Filtros; onChange: (f: Filtros) => void;
}) {
  const [atalhos, setAtalhos] = useState<Atalho[]>([]);
  useEffect(() => { get<Atalho[]>("/api/base/atalhos").then(setAtalhos).catch(() => {}); }, []);

  const f = valor;
  const set = (parcial: Partial<Filtros>) => onChange({ ...f, ...parcial });
  const alternar = (campo: "ufs" | "portes", v: string) => {
    const atual = f[campo] ?? [];
    set({ [campo]: atual.includes(v) ? atual.filter((x) => x !== v) : [...atual, v] });
  };
  const addLista = (campo: "municipios" | "cnaes", v: string) => {
    const atual = f[campo] ?? [];
    if (!atual.includes(v)) set({ [campo]: [...atual, v] });
  };
  const tirar = (campo: "municipios" | "cnaes", v: string) =>
    set({ [campo]: (f[campo] ?? []).filter((x) => x !== v) });

  const buscarMun = (q: string) =>
    get<Opcao[]>(`/api/base/municipios?q=${encodeURIComponent(q)}&uf=${(f.ufs ?? []).join(",")}`);
  const buscarCnae = (q: string) => get<Opcao[]>(`/api/base/cnaes?q=${encodeURIComponent(q)}`);

  const hoje = new Date();
  const diasAtras = (d: number) =>
    new Date(hoje.getTime() - d * 86400000).toISOString().slice(0, 10);

  return (
    <aside className="painel-filtros" aria-label="Filtros">
      <div className="linha-topo" style={{ marginBottom: ".6rem" }}>
        <b style={{ fontSize: ".9rem" }}>Filtros</b>
        {contarFiltros(f) > 0 && (
          <button className="botao secundario pequeno" onClick={() => onChange(FILTROS_PADRAO)}>
            Limpar ({contarFiltros(f)})
          </button>
        )}
      </div>

      <div className="campo">
        <label htmlFor="f-q">Nome ou CNPJ</label>
        <input id="f-q" value={f.q ?? ""} placeholder="ex.: auto center, 11.222.333"
               onChange={(e) => set({ q: e.target.value })} />
      </div>

      <h3>Onde</h3>
      <div className="chips ufs" role="group" aria-label="Estados">
        {UFS.map((uf) => (
          <button key={uf} type="button" aria-pressed={f.ufs?.includes(uf) ?? false}
                  className={`chip ${f.ufs?.includes(uf) ? "marcado" : ""}`}
                  onClick={() => alternar("ufs", uf)}>{uf}</button>
        ))}
      </div>
      <div className="campo" style={{ marginTop: ".6rem" }}>
        <label htmlFor="f-mun">Município</label>
        <Autocompletar id="f-mun" placeholder="digite a cidade" buscar={buscarMun}
          rotuloDe={(o) => <>{o.nome}/{o.uf} <span className="fraco">· {o.estabelecimentos?.toLocaleString("pt-BR")}</span></>}
          onEscolher={(o) => { lembrar(`m:${o.codigo}`, `${o.nome}/${o.uf}`); addLista("municipios", o.codigo); }} />
        <div className="chips" style={{ marginTop: ".4rem" }}>
          {(f.municipios ?? []).map((m) => (
            <button key={m} type="button" className="chip mini marcado" onClick={() => tirar("municipios", m)}
                    aria-label={`Remover ${rotulo(`m:${m}`)}`}>
              {rotulo(`m:${m}`)}<span className="x" aria-hidden>×</span>
            </button>
          ))}
        </div>
      </div>
      <div className="campo">
        <label htmlFor="f-bairro">Bairro</label>
        <input id="f-bairro" value={f.bairro ?? ""} onChange={(e) => set({ bairro: e.target.value })} />
      </div>

      <h3>Atividade</h3>
      {atalhos.length > 0 && (
        <div className="campo">
          <label htmlFor="f-atalho">Ramo pronto</label>
          <select id="f-atalho" value="" onChange={(e) => {
            const a = atalhos.find((x) => x.chave === e.target.value);
            if (!a) return;
            for (const c of a.cnaes) lembrar(`c:${c}`, `${c} · ${a.rotulo}`);
            set({ cnaes: Array.from(new Set([...(f.cnaes ?? []), ...a.cnaes])) });
          }}>
            <option value="">escolher…</option>
            {atalhos.map((a) => <option key={a.chave} value={a.chave}>{a.rotulo}</option>)}
          </select>
        </div>
      )}
      <div className="campo">
        <label htmlFor="f-cnae">CNAE (código ou palavra)</label>
        <Autocompletar id="f-cnae" placeholder="ex.: 4520 ou odontológica" buscar={buscarCnae}
          rotuloDe={(o) => <><span className="cod">{o.codigo}</span>{o.descricao}</>}
          onEscolher={(o) => { lembrar(`c:${o.codigo}`, `${o.codigo} · ${o.descricao ?? ""}`); addLista("cnaes", o.codigo); }} />
        <div className="chips" style={{ marginTop: ".4rem" }}>
          {(f.cnaes ?? []).map((c) => (
            <button key={c} type="button" className="chip mini marcado" title={rotulo(`c:${c}`)}
                    onClick={() => tirar("cnaes", c)} aria-label={`Remover CNAE ${c}`}>
              {rotulo(`c:${c}`).slice(0, 34)}<span className="x" aria-hidden>×</span>
            </button>
          ))}
        </div>
        {(f.cnaes?.length ?? 0) > 0 && (
          <label className="marcar" style={{ marginTop: ".4rem" }}>
            <input type="checkbox" checked={!!f.secundarios}
                   onChange={(e) => set({ secundarios: e.target.checked })} />
            incluir atividade secundária
          </label>
        )}
      </div>

      <h3>Porte</h3>
      <div className="chips" role="group" aria-label="Porte">
        {["01", "03", "05", "00"].map((p) => (
          <button key={p} type="button" aria-pressed={f.portes?.includes(p) ?? false}
                  className={`chip mini ${f.portes?.includes(p) ? "marcado" : ""}`}
                  onClick={() => alternar("portes", p)}>{PORTES[p]}</button>
        ))}
      </div>
      <div className="duas" style={{ marginTop: ".6rem" }}>
        <div className="campo">
          <label htmlFor="f-mei">MEI</label>
          <select id="f-mei" value={f.mei ?? "todos"}
                  onChange={(e) => set({ mei: e.target.value as Filtros["mei"] })}>
            <option value="todos">todos</option>
            <option value="sem_mei">sem MEI</option>
            <option value="so_mei">só MEI</option>
          </select>
        </div>
        <div className="campo">
          <label htmlFor="f-simples">Simples</label>
          <select id="f-simples" value={f.simples ?? "todos"}
                  onChange={(e) => set({ simples: e.target.value as Filtros["simples"] })}>
            <option value="todos">todos</option>
            <option value="sim">optante</option>
            <option value="nao">não optante</option>
          </select>
        </div>
      </div>
      <div className="duas">
        <div className="campo">
          <label htmlFor="f-cmin">Capital mín. (R$)</label>
          <input id="f-cmin" type="number" min={0} step={1000} value={f.capital_min ?? ""}
                 onChange={(e) => set({ capital_min: e.target.value === "" ? null : Number(e.target.value) })} />
        </div>
        <div className="campo">
          <label htmlFor="f-cmax">Capital máx. (R$)</label>
          <input id="f-cmax" type="number" min={0} step={1000} value={f.capital_max ?? ""}
                 onChange={(e) => set({ capital_max: e.target.value === "" ? null : Number(e.target.value) })} />
        </div>
      </div>

      <h3>Abertura</h3>
      <div className="duas">
        <div className="campo">
          <label htmlFor="f-de">De</label>
          <input id="f-de" type="date" value={f.abertura_de ?? ""}
                 onChange={(e) => set({ abertura_de: e.target.value || null })} />
        </div>
        <div className="campo">
          <label htmlFor="f-ate">Até</label>
          <input id="f-ate" type="date" value={f.abertura_ate ?? ""}
                 onChange={(e) => set({ abertura_ate: e.target.value || null })} />
        </div>
      </div>
      <div className="chips" style={{ marginBottom: ".4rem" }}>
        <button type="button" className="chip mini" onClick={() => set({ abertura_de: diasAtras(90), abertura_ate: null })}>últimos 90 dias</button>
        <button type="button" className="chip mini" onClick={() => set({ abertura_de: diasAtras(365), abertura_ate: null })}>último ano</button>
        <button type="button" className="chip mini" onClick={() => set({ abertura_de: null, abertura_ate: diasAtras(365 * 5) })}>+5 anos</button>
      </div>

      <h3>Contato</h3>
      {([
        ["com_celular", "tem celular (WhatsApp)"],
        ["com_telefone", "tem algum telefone"],
        ["com_email", "tem e-mail"],
        ["email_proprio", "e-mail próprio (sem o do contador)"],
        ["so_matriz", "só matriz"],
      ] as const).map(([k, r]) => (
        <label key={k} className="marcar">
          <input type="checkbox" checked={!!f[k]} onChange={(e) => set({ [k]: e.target.checked })} />
          {r}
        </label>
      ))}
    </aside>
  );
}
