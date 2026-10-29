"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { get, NOME_SINAL, type Lead } from "@/lib/api";

export default function LeadsDaCampanha({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [filtro, setFiltro] = useState<"todos" | "A" | "B" | "com_telefone" | "sem_site">("todos");
  const [erro, setErro] = useState<string | null>(null);
  const [carregando, setCarregando] = useState(true);

  useEffect(() => {
    get<Lead[]>(`/api/campanhas/${id}/leads?limite=400`)
      .then(setLeads).catch((e) => setErro(e.message)).finally(() => setCarregando(false));
  }, [id]);

  const visiveis = leads.filter((l) => {
    if (filtro === "A" || filtro === "B") return l.faixa === filtro;
    if (filtro === "com_telefone") return !!l.telefone;
    if (filtro === "sem_site") return l.dores.includes("SEM_SITE");
    return true;
  });

  const comTelefone = leads.filter((l) => l.telefone).length;

  if (erro) return <p className="erro">{erro}</p>;
  if (carregando) return <p className="legenda">Carregando…</p>;

  return (
    <>
      <div className="linha-topo">
        <div>
          <h1>Leads</h1>
          <p className="legenda">
            {leads.length} empresas auditadas · {comTelefone} com telefone ·
            ordenadas por score
          </p>
        </div>
      </div>

      <div className="chips" style={{ marginBottom: "1.25rem" }}>
        {([
          ["todos", `Todos (${leads.length})`],
          ["A", `Faixa A (${leads.filter((l) => l.faixa === "A").length})`],
          ["B", `Faixa B (${leads.filter((l) => l.faixa === "B").length})`],
          ["com_telefone", `Com telefone (${comTelefone})`],
          ["sem_site", `Sem site (${leads.filter((l) => l.dores.includes("SEM_SITE")).length})`],
        ] as const).map(([chave, rotulo]) => (
          <button key={chave} className={`chip ${filtro === chave ? "marcado" : ""}`}
                  onClick={() => setFiltro(chave as typeof filtro)}>
            {rotulo}
          </button>
        ))}
      </div>

      {visiveis.length === 0 ? (
        <div className="vazio">Nenhum lead com esse filtro.</div>
      ) : (
        <div className="rolagem">
          <table className="tabela">
            <thead>
              <tr>
                <th className="num-col">Score</th>
                <th>Empresa</th>
                <th>Telefone</th>
                <th>Site</th>
                <th>Problemas encontrados</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {visiveis.map((l) => (
                <tr key={l.id}>
                  <td className="num-col">
                    <span className={`etiqueta ${l.faixa.toLowerCase()}`}>{l.faixa}</span>{" "}
                    <span className="mono">{l.total.toFixed(0)}</span>
                  </td>
                  <td>
                    <Link href={`/leads/${l.id}`} style={{ fontWeight: 550 }}>{l.nome}</Link>
                    {l.prova_aberta && (
                      <span className="etiqueta ok" style={{ marginLeft: ".4rem" }}>abriu a análise</span>
                    )}
                    {l.opt_out ? (
                      <span className="etiqueta dor" style={{ marginLeft: ".4rem" }}>opt-out</span>
                    ) : null}
                    <div className="fraco" style={{ fontSize: ".78rem" }}>
                      {l.bairro || l.endereco || "—"}
                    </div>
                  </td>
                  <td className="mono fraco">{l.telefone ?? "—"}</td>
                  <td style={{ maxWidth: 210 }}>
                    {l.site_confianca >= 2 ? (
                      <a href={l.site_url!} target="_blank" rel="noreferrer"
                         style={{ color: "var(--acento)", fontSize: ".82rem" }}>
                        {l.site_url!.replace(/^https?:\/\/(www\.)?/, "").slice(0, 30)}
                      </a>
                    ) : l.site_encontrado_como === "suspeito" ? (
                      <span className="fraco" style={{ fontSize: ".8rem" }} title={l.site_url ?? ""}>
                        não confirmado
                      </span>
                    ) : (
                      <span className="etiqueta dor">sem site</span>
                    )}
                  </td>
                  <td>
                    <div className="chips">
                      {l.dores.slice(0, 4).map((d) => (
                        <span key={d} className="etiqueta dor">{NOME_SINAL[d] ?? d}</span>
                      ))}
                      {l.dores.length > 4 && (
                        <span className="etiqueta">+{l.dores.length - 4}</span>
                      )}
                    </div>
                  </td>
                  <td className="fraco" style={{ fontSize: ".78rem", whiteSpace: "nowrap" }}>
                    {l.msgs > 0 ? `${l.msgs} msgs` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="legenda" style={{ marginTop: "1.5rem" }}>
        “Não confirmado” quer dizer que existe um domínio parecido, mas nada prova que
        seja da empresa — então nada dele é usado no laudo nem na mensagem.
      </p>
    </>
  );
}
