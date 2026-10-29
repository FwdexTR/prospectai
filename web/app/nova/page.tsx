"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { get, post, type Execucao, type Ramo } from "@/lib/api";

const ETAPAS: Record<string, string> = {
  iniciando: "preparando",
  coleta: "procurando empresas",
  auditoria: "auditando os sites",
  score: "calculando o score",
  pagespeed: "medindo velocidade no PageSpeed",
};

export default function NovaBusca() {
  const router = useRouter();
  const [ramos, setRamos] = useState<Ramo[]>([]);
  const [cidade, setCidade] = useState("");
  const [uf, setUf] = useState("RJ");
  const [raio, setRaio] = useState(0);
  const [escolhidos, setEscolhidos] = useState<string[]>([]);
  const [psi, setPsi] = useState(true);
  const [fonte, setFonte] = useState<"auto" | "base">("auto");
  const [execId, setExecId] = useState<string | null>(null);
  const [exec, setExec] = useState<Execucao | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  useEffect(() => {
    get<Ramo[]>("/api/ramos").then(setRamos).catch((e) => setErro(e.message));
  }, []);

  useEffect(() => {
    if (!execId) return;
    const t = setInterval(async () => {
      try {
        const e = await get<Execucao>(`/api/execucoes/${execId}`);
        setExec(e);
        if (e.estado === "concluida") {
          clearInterval(t);
          router.push(`/campanhas/${e.resumo.campanha_id}`);
        }
        if (e.estado === "erro") clearInterval(t);
      } catch (err: any) {
        setErro(err.message);
        clearInterval(t);
      }
    }, 1200);
    return () => clearInterval(t);
  }, [execId, router]);

  const alternar = (chave: string) =>
    setEscolhidos((atual) =>
      atual.includes(chave) ? atual.filter((c) => c !== chave) : [...atual, chave]
    );

  async function enviar(e: React.FormEvent) {
    e.preventDefault();
    setErro(null);
    try {
      const r = await post<{ execucao_id: string }>("/api/campanhas", {
        cidade, uf, ramos: escolhidos, raio_km: raio,
        limite_fila: 30, medir_pagespeed: psi, fonte,
      });
      setExecId(r.execucao_id);
    } catch (err: any) {
      setErro(err.message);
    }
  }

  if (execId) {
    const pct = exec && exec.total ? Math.round((exec.feito / exec.total) * 100) : 0;
    return (
      <>
        <h1>Procurando…</h1>
        <p className="legenda">
          {exec?.estado === "erro"
            ? "A busca falhou."
            : `${ETAPAS[exec?.etapa ?? "iniciando"] ?? exec?.etapa} — ${exec?.feito ?? 0} de ${exec?.total ?? 0}`}
        </p>
        <div className="trilho"><div style={{ width: `${pct}%` }} /></div>
        {exec?.estado === "erro" && (
          <>
            <p className="erro" style={{ marginTop: "1.5rem" }}>{exec.erro}</p>
            <button className="botao secundario" onClick={() => { setExecId(null); setExec(null); }}>
              Tentar de novo
            </button>
          </>
        )}
        <p className="legenda" style={{ marginTop: "2rem" }}>
          A auditoria abre cada site encontrado e mede HTTP, certificado, adaptação a
          celular e peso da página. Leva alguns minutos e é o que gera o laudo.
        </p>
      </>
    );
  }

  return (
    <>
      <h1>Nova busca</h1>
      <p className="legenda">
        Escolha a cidade e o ramo. O sistema procura as empresas, audita o site de
        cada uma, pontua e escreve as mensagens.
      </p>

      <form onSubmit={enviar} style={{ maxWidth: 660 }}>
        <div className="linha-campos">
          <div className="campo">
            <label htmlFor="cidade">Cidade</label>
            <input id="cidade" value={cidade} required placeholder="Campos dos Goytacazes"
                   onChange={(e) => setCidade(e.target.value)} />
          </div>
          <div className="campo">
            <label htmlFor="uf">UF</label>
            <input id="uf" value={uf} required maxLength={2}
                   onChange={(e) => setUf(e.target.value.toUpperCase())} />
          </div>
          <div className="campo">
            <label htmlFor="raio">Raio (km)</label>
            <input id="raio" type="number" min={0} max={60} value={raio}
                   onChange={(e) => setRaio(Number(e.target.value))} />
          </div>
        </div>
        <p className="legenda" style={{ marginTop: "-.5rem" }}>
          Raio 0 usa o limite exato do município. Acima de 0, pega as cidades vizinhas.
        </p>

        <div className="campo">
          <label>Ramos ({escolhidos.length} escolhidos)</label>
          <div className="chips">
            {ramos.map((r) => (
              <button type="button" key={r.chave} title={r.gancho}
                      className={`chip ${escolhidos.includes(r.chave) ? "marcado" : ""}`}
                      onClick={() => alternar(r.chave)}>
                {r.rotulo}
              </button>
            ))}
          </div>
          <p className="legenda" style={{ marginTop: ".6rem" }}>
            Um ou dois ramos por busca funciona melhor: a mensagem fica específica e
            você reaproveita o mesmo argumento.
          </p>
        </div>

        <div className="campo">
          <label htmlFor="fonte">De onde vêm as empresas</label>
          <select id="fonte" value={fonte} onChange={(e) => setFonte(e.target.value as "auto" | "base")}
                  style={{ maxWidth: 420 }}>
            <option value="auto">Google Places (se houver chave) ou Overture Maps, grátis</option>
            <option value="base">Base CNPJ importada: todas as empresas ativas do ramo</option>
          </select>
          <p className="legenda" style={{ marginTop: ".4rem" }}>
            A base CNPJ traz muito mais empresas e telefone de quase todas, mas não traz avaliações
            do Google. A de demonstração tem empresas fictícias: a fila bloqueia o envio para elas.
          </p>
        </div>

        <div className="campo">
          <label style={{ display: "flex", gap: ".5rem", alignItems: "center", cursor: "pointer" }}>
            <input type="checkbox" checked={psi} style={{ width: "auto" }}
                   onChange={(e) => setPsi(e.target.checked)} />
            Medir velocidade no PageSpeed (mais lento, mas rende o melhor argumento)
          </label>
        </div>

        {erro && <p className="erro">{erro}</p>}

        <button className="botao" type="submit" disabled={!cidade || escolhidos.length === 0}>
          Procurar leads
        </button>
      </form>
    </>
  );
}
