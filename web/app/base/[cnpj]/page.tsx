"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import {
  cnpjFmt, dataFmt, ETAPA, fmt, get, moeda, post, telFmt, type Ficha, type Lista,
} from "@/lib/api";

export default function FichaEmpresa({ params }: { params: Promise<{ cnpj: string }> }) {
  const { cnpj } = use(params);
  const [e, setE] = useState<Ficha | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [listas, setListas] = useState<Lista[]>([]);
  const [lista, setLista] = useState("");

  const carregar = useCallback(
    () => get<Ficha>(`/api/base/empresa/${cnpj}`).then(setE).catch((x) => setErro(x.message)),
    [cnpj]);
  useEffect(() => { carregar(); get<Lista[]>("/api/listas").then(setListas).catch(() => {}); }, [carregar]);

  if (erro) return <p className="erro">{erro}</p>;
  if (!e) return <p className="legenda">Carregando…</p>;

  const contador = !!e.email && (e.email_qtd ?? 0) > 3;
  const endereco = [
    [e.tipo_logradouro, e.logradouro].filter(Boolean).join(" "), e.numero, e.complemento,
  ].filter(Boolean).join(", ");

  async function revelar() {
    try {
      const r = await post<{ reveladas: { empresa_id: number }[]; ja_existiam: { empresa_id: number }[] }>(
        "/api/revelar", { cnpjs: [e!.cnpj] });
      const id = (r.reveladas[0] ?? r.ja_existiam[0])?.empresa_id;
      setAviso(id ? "Empresa no CRM." : "Não consegui revelar.");
      carregar();
    } catch (x: any) { setAviso(`Erro: ${x.message}`); }
  }

  async function addLista() {
    if (!lista) return;
    try {
      await post(`/api/listas/${lista}/itens`, { cnpjs: [e!.cnpj] });
      setAviso("Adicionada à lista.");
      carregar();
    } catch (x: any) { setAviso(`Erro: ${x.message}`); }
  }

  return (
    <>
      <p className="fraco" style={{ fontSize: ".82rem", margin: "0 0 .5rem" }}>
        <Link href="/base">← busca</Link>
      </p>
      <div className="linha-topo" style={{ alignItems: "flex-start" }}>
        <div>
          <h1>{e.nome}</h1>
          <p className="legenda">
            {cnpjFmt(e.cnpj)} · {e.matriz ? "matriz" : "filial"} · {e.situacao_rotulo}
            {e.fonte === "demo" && <span className="etiqueta demo" style={{ marginLeft: ".4rem" }}>demonstração</span>}
          </p>
        </div>
        <div className="acoes">
          {e.crm ? (
            <Link className="botao" href={`/leads/${e.crm.empresa_id}`}>
              Abrir no CRM · {ETAPA[e.crm.estado ?? "NOVO"]}
            </Link>
          ) : (
            <button className="botao" onClick={revelar}>Revelar no CRM</button>
          )}
        </div>
      </div>
      {aviso && <div className="aviso info">{aviso}</div>}

      <div className="ficha-grade">
        <div className="cartao">
          <h2 style={{ marginTop: 0 }}>Cadastro</h2>
          <dl className="dl">
            <dt>Razão social</dt><dd>{e.razao_social ?? "—"}</dd>
            <dt>Nome fantasia</dt><dd>{e.nome_fantasia ?? "—"}</dd>
            <dt>Natureza</dt><dd>{e.natureza_descricao ?? e.natureza ?? "—"}</dd>
            <dt>Porte</dt><dd>{e.porte_rotulo ?? "—"}</dd>
            <dt>Capital social</dt><dd>{moeda(e.capital_social)}</dd>
            <dt>Simples</dt><dd>{e.simples == null ? "—" : e.simples ? "optante" : "não optante"}</dd>
            <dt>Abertura</dt><dd>{dataFmt(e.data_abertura)}</dd>
            <dt>Situação</dt>
            <dd>{e.situacao_rotulo} desde {dataFmt(e.data_situacao)}
              {e.motivo_descricao && e.motivo_descricao !== "SEM MOTIVO" ? ` (${e.motivo_descricao})` : ""}</dd>
          </dl>
        </div>

        <div className="cartao">
          <h2 style={{ marginTop: 0 }}>Contato e endereço</h2>
          <dl className="dl">
            <dt>Celular</dt>
            <dd>{e.celular ? telFmt(e.celular) : "—"}
              {e.celular && e.fonte !== "demo" && (
                <> · <a href={`https://wa.me/${e.celular.replace("+", "")}`} target="_blank" rel="noreferrer"
                        style={{ color: "var(--acento)" }}>WhatsApp</a></>
              )}
            </dd>
            <dt>Telefones</dt>
            <dd>{[e.telefone1, e.telefone2].filter(Boolean).map(telFmt).join(" · ") || "—"}
              {(e.telefone_qtd ?? 0) > 3 && (
                <div className="fraco" style={{ fontSize: ".78rem" }}>
                  número usado por {fmt(e.telefone_qtd)} empresas
                </div>
              )}
            </dd>
            <dt>E-mail</dt>
            <dd>{e.email ?? "—"}
              {contador && (
                <div style={{ fontSize: ".78rem", color: "var(--atencao)" }}>
                  usado por {fmt(e.email_qtd)} empresas: provavelmente do contador, não da empresa
                </div>
              )}
            </dd>
            <dt>Endereço</dt><dd>{endereco || "—"}</dd>
            <dt>Bairro</dt><dd>{e.bairro ?? "—"}</dd>
            <dt>Cidade</dt><dd>{e.municipio}/{e.uf} · CEP {e.cep ?? "—"}</dd>
          </dl>
        </div>
      </div>

      <h2>Atividades</h2>
      <p style={{ margin: ".2rem 0" }}>
        <span className="etiqueta">principal</span>{" "}
        <span className="mono fraco">{e.cnae}</span> {e.cnae_descricao}
      </p>
      {e.cnaes_secundarios.map((c) => (
        <p key={c.codigo} style={{ margin: ".2rem 0", fontSize: ".88rem" }}>
          <span className="mono fraco">{c.codigo}</span> {c.descricao ?? ""}
        </p>
      ))}

      <h2>Listas</h2>
      <div className="acoes" style={{ alignItems: "center" }}>
        {e.listas.map((l) => (
          <Link key={l.id} href={`/listas/${l.id}`} className="etiqueta info">{l.nome}</Link>
        ))}
        <select value={lista} onChange={(x) => setLista(x.target.value)} style={{ width: "auto" }}
                aria-label="Adicionar à lista">
          <option value="">adicionar a uma lista…</option>
          {listas.filter((l) => !e.listas.some((x) => x.id === l.id))
                 .map((l) => <option key={l.id} value={l.id}>{l.nome}</option>)}
        </select>
        <button className="botao secundario pequeno" disabled={!lista} onClick={addLista}>Adicionar</button>
      </div>

      {e.outras_unidades.length > 0 && (
        <>
          <h2>Outras unidades da mesma empresa</h2>
          <table className="tabela">
            <tbody>
              {e.outras_unidades.map((u) => (
                <tr key={u.cnpj}>
                  <td><Link href={`/base/${u.cnpj}`} style={{ color: "var(--acento)" }}>{u.nome}</Link></td>
                  <td className="mono fraco">{cnpjFmt(u.cnpj)}</td>
                  <td>{u.municipio}/{u.uf}</td>
                  <td>{u.matriz ? "matriz" : "filial"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <p className="legenda" style={{ marginTop: "1.5rem" }}>
        Fonte: Dados Abertos do CNPJ (Receita Federal). O quadro de sócios não é importado:
        é dado de pessoa física (regra I7).
      </p>
    </>
  );
}
