"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useRef, useState } from "react";
import {
  dataFmt, del, get, post, put, type CadenciaDetalhe, type LeadFunil, type Passo,
} from "@/lib/api";

// Modelo inicial que já passa no validador: identifica quem fala, tem saída
// no primeiro toque, não promete resultado e não usa jargão.
const MODELO: Passo[] = [
  {
    canal: "whatsapp", espera_dias: 0, assunto: null,
    corpo: "Oi, {{nome}}! Aqui é o {{remetente}}, de {{remetente_cidade}}. Faço sites e " +
      "atendimento automático no WhatsApp para empresas da região. Posso te mandar uma " +
      "ideia rápida do que daria para fazer aí? Se não quiser receber, é só falar.",
  },
  {
    canal: "email", espera_dias: 3, assunto: "Uma ideia para a {{nome}}",
    corpo: "Olá! Aqui é o {{remetente}}, de {{remetente_cidade}}.\n\nTe mandei uma mensagem " +
      "no WhatsApp há alguns dias. Resumindo: monto o site e o atendimento automático para que " +
      "o cliente da {{nome}} consiga orçamento e horário sem depender de alguém no telefone.\n\n" +
      "Posso te mostrar em 10 minutos, sem compromisso? Se não fizer sentido, é só responder " +
      "“não” que eu não mando mais.\n\n{{remetente}}\n{{remetente_whatsapp}}",
  },
  {
    canal: "whatsapp", espera_dias: 7, assunto: null,
    corpo: "Oi, {{nome}}, aqui é o {{remetente}} de novo. É a última mensagem: se em algum " +
      "momento quiser conversar sobre isso, é só me chamar por aqui. Obrigado!",
  },
];

type Previa = { ordem: number; canal: string; assunto: string | null; texto: string; erros: string[] };

export default function EditorCadencia({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const nova = id === "nova";
  const router = useRouter();
  const [nome, setNome] = useState(nova ? "Primeiro contato: site + WhatsApp" : "");
  const [descricao, setDescricao] = useState("");
  const [ativa, setAtiva] = useState(true);
  const [passos, setPassos] = useState<Passo[]>(nova ? MODELO : []);
  const [det, setDet] = useState<CadenciaDetalhe | null>(null);
  const [variaveis, setVariaveis] = useState<Record<string, string>>({});
  const [aviso, setAviso] = useState<string | null>(null);
  const [leads, setLeads] = useState<LeadFunil[]>([]);
  const [empresa, setEmpresa] = useState("");
  const [previa, setPrevia] = useState<Previa[] | null>(null);
  const areas = useRef<(HTMLTextAreaElement | null)[]>([]);

  useEffect(() => {
    get<Record<string, string>>("/api/cadencias/variaveis").then(setVariaveis).catch(() => {});
    get<{ leads: LeadFunil[] }>("/api/crm/funil").then((r) => setLeads(r.leads.slice(0, 100))).catch(() => {});
    if (!nova) {
      get<CadenciaDetalhe>(`/api/cadencias/${id}`).then((c) => {
        setDet(c); setNome(c.nome); setDescricao(c.descricao ?? ""); setAtiva(!!c.ativa);
        setPassos(c.passos.map(({ canal, espera_dias, assunto, corpo }) => ({ canal, espera_dias, assunto, corpo })));
      }).catch((e) => setAviso(`Erro: ${e.message}`));
    }
  }, [id, nova]);

  const mudar = (i: number, p: Partial<Passo>) =>
    setPassos((ps) => ps.map((x, k) => (k === i ? { ...x, ...p } : x)));

  function inserir(i: number, v: string) {
    const el = areas.current[i];
    const corpo = passos[i].corpo;
    const pos = el?.selectionStart ?? corpo.length;
    mudar(i, { corpo: corpo.slice(0, pos) + `{{${v}}}` + corpo.slice(pos) });
  }

  async function salvar() {
    const corpo = { nome, descricao: descricao || null, ativa, passos };
    try {
      if (nova) {
        const r = await post<{ id: number }>("/api/cadencias", corpo);
        router.replace(`/cadencias/${r.id}`);
      } else {
        await put(`/api/cadencias/${id}`, corpo);
        setAviso("Salva. Mensagens já geradas não mudam; o novo texto vale para as próximas inscrições.");
      }
    } catch (e: any) { setAviso(`Erro: ${e.message}`); }
  }

  async function verPrevia() {
    if (nova) { setAviso("Salve a cadência para ver a prévia."); return; }
    try { setPrevia(await get<Previa[]>(`/api/cadencias/${id}/previa?empresa_id=${empresa}`)); }
    catch (e: any) { setAviso(`Erro: ${e.message}`); }
  }

  return (
    <>
      <p className="fraco" style={{ fontSize: ".82rem", margin: "0 0 .5rem" }}><Link href="/cadencias">← cadências</Link></p>
      <h1>{nova ? "Nova cadência" : nome}</h1>
      <p className="legenda">
        No máximo 3 passos (regra I4). Variável sem valor para a empresa bloqueia a mensagem em vez
        de sair com buraco (regra I1).
      </p>
      {aviso && <div className="aviso info">{aviso}</div>}

      <div className="linha-campos" style={{ gridTemplateColumns: "2fr 2fr 1fr", maxWidth: 900 }}>
        <div className="campo">
          <label htmlFor="c-nome">Nome</label>
          <input id="c-nome" value={nome} onChange={(e) => setNome(e.target.value)} />
        </div>
        <div className="campo">
          <label htmlFor="c-desc">Descrição</label>
          <input id="c-desc" value={descricao} onChange={(e) => setDescricao(e.target.value)} />
        </div>
        <label className="marcar" style={{ alignSelf: "end", marginBottom: "1.4rem" }}>
          <input type="checkbox" checked={ativa} onChange={(e) => setAtiva(e.target.checked)} /> ativa
        </label>
      </div>

      <div style={{ maxWidth: 900 }}>
        {passos.map((p, i) => (
          <div className="passo" key={i}>
            <div className="passo-topo">
              <b style={{ alignSelf: "center" }}>Passo {i + 1}</b>
              <div className="campo">
                <label htmlFor={`canal-${i}`}>Canal</label>
                <select id={`canal-${i}`} value={p.canal} style={{ width: "auto" }}
                        onChange={(e) => mudar(i, { canal: e.target.value as Passo["canal"] })}>
                  <option value="whatsapp">WhatsApp</option>
                  <option value="email">E-mail</option>
                </select>
              </div>
              <div className="campo">
                <label htmlFor={`espera-${i}`}>{i === 0 ? "Esperar antes (dias)" : "Dias após o anterior"}</label>
                <input id={`espera-${i}`} type="number" min={0} max={60} value={p.espera_dias}
                       style={{ width: 90 }} onChange={(e) => mudar(i, { espera_dias: Number(e.target.value) })} />
              </div>
              <button className="botao secundario pequeno" style={{ marginLeft: "auto" }}
                      disabled={passos.length <= 1}
                      onClick={() => setPassos((ps) => ps.filter((_, k) => k !== i))}>remover passo</button>
            </div>
            {p.canal === "email" && (
              <div className="campo">
                <label htmlFor={`assunto-${i}`}>Assunto</label>
                <input id={`assunto-${i}`} value={p.assunto ?? ""} onChange={(e) => mudar(i, { assunto: e.target.value })} />
              </div>
            )}
            <label htmlFor={`corpo-${i}`}>Mensagem ({p.corpo.length} caracteres; limite {p.canal === "email" ? 3000 : 700})</label>
            <textarea id={`corpo-${i}`} rows={p.canal === "email" ? 9 : 5} value={p.corpo}
                      ref={(el) => { areas.current[i] = el; }}
                      onChange={(e) => mudar(i, { corpo: e.target.value })} />
            <div className="variaveis" aria-label="Inserir variável">
              {Object.entries(variaveis).map(([v, d]) => (
                <button key={v} type="button" title={d} onClick={() => inserir(i, v)}>{`{{${v}}}`}</button>
              ))}
            </div>
          </div>
        ))}
        <div className="acoes">
          {passos.length < 3 && (
            <button className="botao secundario"
                    onClick={() => setPassos((ps) => [...ps, { canal: "whatsapp", espera_dias: 3, assunto: null, corpo: "" }])}>
              + passo
            </button>
          )}
          <button className="botao" disabled={!nome.trim() || passos.some((p) => !p.corpo.trim())} onClick={salvar}>
            Salvar
          </button>
          {!nova && !det?.inscritos.length && (
            <button className="botao secundario" style={{ color: "var(--ruim)" }}
                    onClick={async () => { if (confirm("Excluir esta cadência?")) { await del(`/api/cadencias/${id}`); router.push("/cadencias"); } }}>
              Excluir
            </button>
          )}
        </div>
      </div>

      {!nova && (
        <>
          <h2>Prévia com uma empresa do CRM</h2>
          <div className="acoes" style={{ alignItems: "center", maxWidth: 900 }}>
            <select value={empresa} onChange={(e) => setEmpresa(e.target.value)} style={{ maxWidth: 360 }}
                    aria-label="Empresa para a prévia">
              <option value="">escolher empresa…</option>
              {leads.map((l) => <option key={l.empresa_id} value={l.empresa_id}>{l.nome}</option>)}
            </select>
            <button className="botao secundario" disabled={!empresa} onClick={verPrevia}>Ver prévia</button>
          </div>
          {previa?.map((m) => (
            <div key={m.ordem} style={{ maxWidth: 900, marginTop: ".75rem" }}>
              <p className="legenda" style={{ margin: "0 0 .3rem" }}>
                Passo {m.ordem} · {m.canal}{m.assunto ? ` · assunto: ${m.assunto}` : ""}
                {m.erros.length ? <span className="erro"> · bloqueada: {m.erros.join("; ")}</span>
                                : <span style={{ color: "var(--bom)" }}> · aprovada</span>}
              </p>
              <div className="msg-caixa">{m.texto}</div>
            </div>
          ))}

          <h2>Empresas inscritas</h2>
          {!det?.inscritos.length ? (
            <p className="legenda">
              Nenhuma ainda. Inscreva pela página de uma <Link href="/listas" style={{ color: "var(--acento)" }}>lista</Link>.
            </p>
          ) : (
            <table className="tabela" style={{ maxWidth: 900 }}>
              <thead><tr><th>Empresa</th><th>Situação</th><th className="num-col">Enviadas</th><th>Desde</th></tr></thead>
              <tbody>
                {det.inscritos.map((x) => (
                  <tr key={x.id}>
                    <td>
                      <Link href={`/leads/${x.empresa_id}`} style={{ color: "var(--acento)" }}>{x.nome}</Link>
                      {x.fonte === "demo" && <span className="etiqueta demo" style={{ marginLeft: ".35rem" }}>demo</span>}
                    </td>
                    <td>{x.estado.toLowerCase().replace("_", " ")}</td>
                    <td className="num-col mono">{x.enviadas}/{x.mensagens}</td>
                    <td className="fraco">{dataFmt(x.inscrita_em)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </>
  );
}
