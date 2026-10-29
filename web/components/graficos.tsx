"use client";

import { useState } from "react";

// Gráficos de série única: uma cor (--serie), sem legenda (o título nomeia),
// valor ao lado da barra e dica com a fatia do total. Cores validadas com o
// script do dataviz nos modos claro e escuro (ver globals.css).

type Item = { chave: string; rotulo: string; valor: number };

function pct(v: number, total: number) {
  return total ? `${((100 * v) / total).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%` : "";
}

function useDica() {
  const [dica, setDica] = useState<{ x: number; y: number; texto: string } | null>(null);
  const mostrar = (e: React.MouseEvent, texto: string) => setDica({ x: e.clientX, y: e.clientY, texto });
  const el = dica ? <div className="dica" style={{ left: dica.x, top: dica.y }}>{dica.texto}</div> : null;
  return { mostrar, esconder: () => setDica(null), el };
}

export function BarList({ itens, total, onClick }: {
  itens: Item[]; total: number; onClick?: (chave: string) => void;
}) {
  const max = Math.max(1, ...itens.map((i) => i.valor));
  const d = useDica();
  if (!itens.length) return <p className="fraco" style={{ fontSize: ".85rem" }}>Sem dados.</p>;
  return (
    <div className="barlist">
      {itens.map((i) => {
        const Tag = onClick ? "button" : "div";
        return (
          <Tag key={i.chave} className={`barlist-linha ${onClick ? "clicavel" : ""}`}
               style={onClick ? { border: 0, background: "transparent", font: "inherit",
                                   color: "inherit", textAlign: "left", width: "100%" } : undefined}
               onClick={onClick ? () => onClick(i.chave) : undefined}
               onMouseMove={(e) => d.mostrar(e, `${i.rotulo}: ${i.valor.toLocaleString("pt-BR")} (${pct(i.valor, total)})`)}
               onMouseLeave={d.esconder}
               title={onClick ? `Filtrar por ${i.rotulo}` : undefined}>
            <span className="barlist-rotulo">{i.rotulo}</span>
            <span className="barlist-valor">{i.valor.toLocaleString("pt-BR")}</span>
            <span className="barlist-trilho" aria-hidden>
              <span className="barlist-barra" style={{ display: "block", width: `${(100 * i.valor) / max}%` }} />
            </span>
          </Tag>
        );
      })}
      {d.el}
    </div>
  );
}

export function Colunas({ itens, total }: { itens: Item[]; total: number }) {
  const max = Math.max(1, ...itens.map((i) => i.valor));
  const d = useDica();
  if (!itens.length) return <p className="fraco" style={{ fontSize: ".85rem" }}>Sem dados.</p>;
  const pico = itens.reduce((a, b) => (b.valor > a.valor ? b : a), itens[0]);
  return (
    <>
      <div className="colunas" role="img"
           aria-label={`Maior ano: ${pico.rotulo}, ${pico.valor.toLocaleString("pt-BR")} aberturas`}>
        {itens.map((i) => (
          <div key={i.chave} className="coluna"
               onMouseMove={(e) => d.mostrar(e, `${i.rotulo}: ${i.valor.toLocaleString("pt-BR")} (${pct(i.valor, total)})`)}
               onMouseLeave={d.esconder}>
            <div style={{ height: `${(100 * i.valor) / max}%` }} />
          </div>
        ))}
      </div>
      <div className="colunas-eixo">
        <span>{itens[0].rotulo}</span>
        <span>pico: {pico.rotulo} ({pico.valor.toLocaleString("pt-BR")})</span>
        <span>{itens[itens.length - 1].rotulo}</span>
      </div>
      {d.el}
    </>
  );
}

// Grade aproximada do mapa do Brasil: um quadrado por UF.
const GRADE: (string | null)[][] = [
  [null, "RR", "AP", null, null, null],
  ["AM", "PA", "MA", "CE", "RN", null],
  ["AC", "RO", "TO", "PI", "PB", "PE"],
  [null, "MT", "GO", "BA", "SE", "AL"],
  [null, "MS", "DF", "MG", "ES", null],
  [null, "PR", "SP", "RJ", null, null],
  [null, "SC", null, null, null, null],
  [null, "RS", null, null, null, null],
];

const NOMES: Record<string, string> = {
  AC: "Acre", AL: "Alagoas", AP: "Amapá", AM: "Amazonas", BA: "Bahia", CE: "Ceará",
  DF: "Distrito Federal", ES: "Espírito Santo", GO: "Goiás", MA: "Maranhão", MT: "Mato Grosso",
  MS: "Mato Grosso do Sul", MG: "Minas Gerais", PA: "Pará", PB: "Paraíba", PR: "Paraná",
  PE: "Pernambuco", PI: "Piauí", RJ: "Rio de Janeiro", RN: "Rio Grande do Norte",
  RS: "Rio Grande do Sul", RO: "Rondônia", RR: "Roraima", SC: "Santa Catarina",
  SP: "São Paulo", SE: "Sergipe", TO: "Tocantins",
};

/** Classe 1..5 em escala log: a base é muito concentrada (SP sozinho pesa muito). */
function classe(n: number, max: number) {
  if (n <= 0) return 0;
  if (max <= 1) return 5;
  return Math.min(5, Math.max(1, Math.ceil((5 * Math.log(n + 1)) / Math.log(max + 1))));
}

export function MapaUF({ dados, selecionadas = [], onClick }: {
  dados: { uf: string; n: number }[]; selecionadas?: string[]; onClick?: (uf: string) => void;
}) {
  const por = Object.fromEntries(dados.map((d) => [d.uf, d.n]));
  const max = Math.max(0, ...dados.map((d) => d.n));
  const total = dados.reduce((a, b) => a + b.n, 0);
  const d = useDica();
  return (
    <>
      <div className="mapa-uf" style={{ gridTemplateColumns: "repeat(6, 1fr)" }}>
        {GRADE.flatMap((linha, r) =>
          linha.map((uf, c) => {
            if (!uf) return <div key={`${r}-${c}`} aria-hidden />;
            const n = por[uf] ?? 0;
            const k = classe(n, max);
            return (
              <button key={uf} type="button"
                      className={`uf ${k === 0 ? "zero" : ""} ${selecionadas.includes(uf) ? "sel" : ""}`}
                      style={k ? { background: `var(--mapa-${k})`, color: `var(--mapa-tinta-${k})` } : undefined}
                      aria-label={`${NOMES[uf]}: ${n.toLocaleString("pt-BR")} empresas`}
                      aria-pressed={selecionadas.includes(uf)}
                      onClick={onClick ? () => onClick(uf) : undefined}
                      onMouseMove={(e) => d.mostrar(e, `${NOMES[uf]}: ${n.toLocaleString("pt-BR")} (${pct(n, total)})`)}
                      onMouseLeave={d.esconder}>
                {uf}
                {n > 0 && <span>{n >= 1000 ? `${(n / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} mil` : n}</span>}
              </button>
            );
          })
        )}
      </div>
      <div className="mapa-legenda" aria-hidden>
        menos {[1, 2, 3, 4, 5].map((k) => <i key={k} style={{ background: `var(--mapa-${k})` }} />)} mais
        <span style={{ marginLeft: ".5rem" }}>(escala logarítmica)</span>
      </div>
      {d.el}
    </>
  );
}
