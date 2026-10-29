"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { dataFmt, fmt, get, post, type Lista } from "@/lib/api";

export default function Listas() {
  const [listas, setListas] = useState<Lista[] | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [nome, setNome] = useState("");
  const [texto, setTexto] = useState("");
  const [aviso, setAviso] = useState<string | null>(null);

  const carregar = () => get<Lista[]>("/api/listas").then(setListas).catch((e) => setErro(e.message));
  useEffect(() => { carregar(); }, []);

  async function criar(e: React.FormEvent) {
    e.preventDefault();
    try {
      const r = await post<{ id: number; adicionados: number; invalidos: string[] }>(
        "/api/listas", { nome, texto: texto || null });
      setAviso(`Lista criada com ${fmt(r.adicionados)} empresas` +
               (r.invalidos.length ? `; ${r.invalidos.length} CNPJs inválidos ignorados.` : "."));
      setNome(""); setTexto("");
      carregar();
    } catch (x: any) { setAviso(`Erro: ${x.message}`); }
  }

  if (erro) return <p className="erro">{erro}</p>;

  return (
    <>
      <h1>Listas</h1>
      <p className="legenda">
        Grupos de empresas da base para trabalhar juntos: revelar, enriquecer, colocar em
        cadência, exportar. Crie pela busca ou colando CNPJs (por exemplo, a sua carteira).
      </p>
      {aviso && <div className="aviso info">{aviso}</div>}

      {listas && listas.length === 0 ? (
        <div className="vazio">
          Nenhuma lista ainda. <Link href="/base" style={{ color: "var(--acento)" }}>Busque empresas</Link>{" "}
          e salve o resultado, ou cole CNPJs abaixo.
        </div>
      ) : listas && (
        <div className="rolagem">
          <table className="tabela">
            <thead>
              <tr><th>Lista</th><th className="num-col">Empresas</th>
                  <th className="num-col">No CRM</th><th>Criada</th></tr>
            </thead>
            <tbody>
              {listas.map((l) => (
                <tr key={l.id}>
                  <td>
                    <Link href={`/listas/${l.id}`} style={{ color: "var(--acento)", fontWeight: 550 }}>{l.nome}</Link>
                    {l.descricao && <div className="fraco" style={{ fontSize: ".78rem" }}>{l.descricao}</div>}
                  </td>
                  <td className="num-col mono">{fmt(l.itens)}</td>
                  <td className="num-col mono">{fmt(l.reveladas)}</td>
                  <td className="fraco">{dataFmt(l.criada_em)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Nova lista colando CNPJs</h2>
      <form onSubmit={criar} style={{ maxWidth: 620 }}>
        <div className="campo">
          <label htmlFor="nome">Nome</label>
          <input id="nome" value={nome} onChange={(e) => setNome(e.target.value)} required
                 placeholder="ex.: Meus clientes atuais" />
        </div>
        <div className="campo">
          <label htmlFor="cnpjs">CNPJs (qualquer formato, um por linha ou separados por vírgula)</label>
          <textarea id="cnpjs" rows={6} value={texto} onChange={(e) => setTexto(e.target.value)}
                    placeholder={"11.222.333/0001-81\n12345678000195"} />
        </div>
        <button className="botao" disabled={!nome.trim()}>Criar lista</button>
        <p className="legenda" style={{ marginTop: ".75rem" }}>
          Dica: cole os CNPJs de quem já é seu cliente e abra “Ver perfil” na lista. O sistema
          mostra o que eles têm em comum e quantas empresas parecidas existem na base.
        </p>
      </form>
    </>
  );
}
