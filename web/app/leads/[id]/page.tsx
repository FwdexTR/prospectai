"use client";

import { use, useCallback, useEffect, useState } from "react";
import PainelCRM from "@/components/painel-crm";
import { get, NOME_SINAL, post, type DetalheLead } from "@/lib/api";

export default function LeadDetalhe({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [d, setD] = useState<DetalheLead | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [resposta, setResposta] = useState("");
  const [aviso, setAviso] = useState<string | null>(null);

  const carregar = useCallback(
    () => get<DetalheLead>(`/api/leads/${id}`).then(setD).catch((e) => setErro(e.message)),
    [id]
  );
  useEffect(() => { carregar(); }, [carregar]);

  if (erro) return <p className="erro">{erro}</p>;
  if (!d) return <p className="legenda">Carregando…</p>;

  const { empresa, auditoria, score, sinais, mensagens, prova, respostas } = d;
  const dores = sinais.filter((s) => s.eixo === "dor");
  const dinheiro = sinais.filter((s) => s.eixo === "dinheiro");
  const optOut = d.contatos.some((c) => c.opt_out);

  async function acao(fn: () => Promise<unknown>, ok: string) {
    try { await fn(); setAviso(ok); await carregar(); }
    catch (e: any) { setAviso(`Erro: ${e.message}`); }
  }

  return (
    <>
      <div className="linha-topo">
        <div>
          <h1>{empresa.nome}</h1>
          <p className="legenda">
            {[empresa.ramo, empresa.bairro, empresa.endereco].filter(Boolean).join(" · ") || "—"}
          </p>
        </div>
        {score && (
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: "2rem", fontWeight: 650 }} className="mono">
              {Number(score.total).toFixed(0)}
            </div>
            <span className={`etiqueta ${String(score.faixa).toLowerCase()}`}>
              faixa {score.faixa}
            </span>
          </div>
        )}
      </div>

      {optOut && (
        <div className="aviso alta">
          <b>Este contato pediu para não receber mensagens</b>
          Opt-out é definitivo. Nenhuma mensagem sai para esta empresa, em nenhum canal.
        </div>
      )}
      {aviso && <div className="aviso info">{aviso}</div>}

      <PainelCRM d={d} recarregar={carregar} avisar={setAviso} />

      {score && (
        <p className="legenda">
          dor {Number(score.dor).toFixed(0)} × dinheiro {Number(score.dinheiro).toFixed(0)}
          {Number(score.dinheiro) === 25 && " (não medido — sem chave do Places)"}
          {Number(score.timing) > 1 && ` × timing ${score.timing}`}
        </p>
      )}

      <h2>Auditoria</h2>
      {auditoria && auditoria.site_confianca >= 2 ? (
        <div className="rolagem">
          <table className="tabela">
            <tbody>
              <Linha r="Site confirmado" v={auditoria.site_url}
                     extra={`provas: ${JSON.parse(auditoria.site_provas || "[]").join(", ")}`} />
              <Linha r="Status HTTP" v={auditoria.http_status} />
              <Linha r="Tempo de resposta" v={auditoria.tempo_resposta_ms ? `${auditoria.tempo_resposta_ms} ms` : null} />
              <Linha r="PageSpeed (celular)" v={auditoria.psi_mobile_score != null ? `${auditoria.psi_mobile_score}/100` : "não medido"} />
              <Linha r="Certificado" v={auditoria.tls_valido ? `válido (${auditoria.tls_dias_restantes} dias)` : "inválido"} />
              <Linha r="Adaptado ao celular" v={sim(auditoria.tem_viewport)} />
              <Linha r="Botão de WhatsApp" v={sim(auditoria.tem_whatsapp)} />
              <Linha r="Medição instalada" v={sim(auditoria.tem_analytics)} />
              <Linha r="Plataforma" v={auditoria.plataforma} />
              <Linha r="Ano no rodapé" v={auditoria.ano_copyright} />
              <Linha r="Peso do HTML" v={auditoria.html_bytes ? `${Math.round(auditoria.html_bytes / 1024)} KB` : null} />
            </tbody>
          </table>
        </div>
      ) : !auditoria ? (
        <p className="legenda">
          Ainda não auditada: nada foi medido sobre o site desta empresa. Use “Enriquecer” na
          página da lista para procurar o site, e-mails e WhatsApp.
        </p>
      ) : (
        <p className="legenda">
          Nenhum site confirmado como sendo desta empresa
          {auditoria?.site_encontrado_como === "suspeito" && (
            <> — existe um domínio parecido ({auditoria.site_url}), mas sem prova de que
            seja dela, então nada dele entra no laudo</>
          )}.
        </p>
      )}

      <h2>Sinais</h2>
      <div className="chips">
        {dores.map((s, i) => (
          <span key={i} className="etiqueta dor" title={JSON.stringify(s.evidencia)}>
            {NOME_SINAL[s.tipo] ?? s.tipo}{s.valor ? `: ${s.valor}` : ""}
          </span>
        ))}
        {dinheiro.map((s, i) => (
          <span key={`d${i}`} className="etiqueta ok">
            {NOME_SINAL[s.tipo] ?? s.tipo}{s.valor ? `: ${s.valor}` : ""}
          </span>
        ))}
        {sinais.length === 0 && <span className="fraco">nenhum sinal detectado</span>}
      </div>

      {prova && (
        <>
          <h2>Análise hospedada</h2>
          <p>
            <a href={prova.url} target="_blank" rel="noreferrer" style={{ color: "var(--acento)" }}>
              {prova.url}
            </a>
          </p>
          <p className="legenda">
            {prova.primeira_visita_em
              ? `Aberta ${prova.num_visitas}× — primeira vez em ${prova.primeira_visita_em}. Ligue agora: é o sinal de interesse mais forte que existe.`
              : "Ainda não foi aberta."}
          </p>
        </>
      )}

      <h2>Mensagens</h2>
      {mensagens.length === 0 ? (
        <p className="legenda">Nenhuma mensagem preparada.</p>
      ) : (
        mensagens.map((m) => (
          <div key={m.id}>
            <p className="legenda" style={{ margin: ".75rem 0 .35rem" }}>
              Toque {m.toque} · {m.canal === "email" ? "e-mail" : "WhatsApp"} · {m.estado.toLowerCase()}
              {m.assunto && ` · assunto: ${m.assunto}`}
              {m.enviada_em && ` · enviada em ${m.enviada_em}`}
              {!m.validador_ok && (
                <span className="erro"> · reprovada: {m.validador_erros}</span>
              )}
            </p>
            <div className="msg-caixa">{m.texto}</div>
          </div>
        ))
      )}

      <h2>Registrar resposta</h2>
      <p className="legenda">
        Cole o que a pessoa respondeu. O sistema classifica e, se for pedido para
        parar, aplica o opt-out na hora e cancela os toques seguintes.
      </p>
      <textarea rows={3} value={resposta} onChange={(e) => setResposta(e.target.value)}
                placeholder="Ex.: quanto fica um site desses?" />
      <div className="acoes" style={{ marginTop: ".6rem" }}>
        <button className="botao" disabled={!resposta.trim()}
                onClick={() => acao(
                  async () => {
                    const r: any = await post(`/api/leads/${empresa.id}/resposta`, { texto: resposta });
                    setResposta("");
                    return r;
                  }, "Resposta registrada.")}>
          Registrar
        </button>
        {!optOut && (
          <button className="botao perigo"
                  onClick={() => acao(() => post(`/api/leads/${empresa.id}/opt-out`),
                                      "Opt-out aplicado.")}>
            marcar opt-out
          </button>
        )}
      </div>

      {respostas.length > 0 && (
        <>
          <h2>Histórico de respostas</h2>
          {respostas.map((r: any) => (
            <div key={r.id} className="msg-caixa">
              <span className="etiqueta">{r.classe}</span> {r.texto}
              <div className="fraco" style={{ fontSize: ".78rem", marginTop: ".3rem" }}>
                {r.recebida_em}
              </div>
            </div>
          ))}
        </>
      )}
    </>
  );
}

function sim(v: unknown) {
  if (v === null || v === undefined) return "não medido";
  return v ? "sim" : "não";
}

function Linha({ r, v, extra }: { r: string; v: unknown; extra?: string }) {
  if (v === null || v === undefined || v === "") return null;
  return (
    <tr>
      <td className="fraco" style={{ width: 200 }}>{r}</td>
      <td>
        {String(v)}
        {extra && <div className="fraco" style={{ fontSize: ".78rem" }}>{extra}</div>}
      </td>
    </tr>
  );
}
