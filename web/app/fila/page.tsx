"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { get, post, type Fila } from "@/lib/api";

export default function FilaEnvio() {
  const [f, setF] = useState<Fila | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);

  const carregar = useCallback(
    () => get<Fila>("/api/fila?limite=40").then(setF).catch((e) => setErro(e.message)),
    []
  );
  useEffect(() => { carregar(); }, [carregar]);

  async function abrirEEnviar(item: { id: number; link: string | null }) {
    // Estrada manual: abre o WhatsApp (ou o e-mail) com o texto pronto e só
    // registra depois que você mandou de fato. Risco de banimento zero.
    if (!item.link) return;
    window.open(item.link, "_blank", "noopener");
    try {
      await post(`/api/mensagens/${item.id}/enviada`, { forcar: true });
      setAviso("Marcada como enviada. O follow-up já ficou agendado.");
      await carregar();
    } catch (e: any) {
      setAviso(`Não consegui registrar: ${e.message}`);
    }
  }

  if (erro) return <p className="erro">{erro}</p>;
  if (!f) return <p className="legenda">Carregando…</p>;

  return (
    <>
      <h1>Fila de envio</h1>
      <p className="legenda">
        O sistema prepara tudo; você clica e envia. {f.enviados_hoje} de {f.limite_dia}{" "}
        enviadas hoje (limite de aquecimento do número).
      </p>

      {!f.dentro_da_janela && (
        <div className="aviso">
          <b>Fora da janela recomendada</b>
          O horário sugerido é de segunda a sexta, 9h–11h30 e 14h–17h30. Você ainda
          pode enviar manualmente, mas a taxa de resposta cai bastante fora disso.
        </div>
      )}
      {f.circuit_breaker && (
        <div className="aviso alta">
          <b>Freio automático acionado</b>
          {f.circuit_breaker} — vale revisar a mensagem antes de continuar.
        </div>
      )}
      {aviso && <div className="aviso info">{aviso}</div>}

      {f.itens.length === 0 ? (
        <div className="vazio">
          Nada na fila. Inscreva uma <Link href="/listas" style={{ color: "var(--acento)" }}>lista</Link> numa
          cadência, ou faça uma <Link href="/nova" style={{ color: "var(--acento)" }}>busca com laudo</Link>.
          Só entram mensagens aprovadas pelo validador, com contato válido e sem opt-out.
        </div>
      ) : (
        f.itens.map((i) => (
          <div key={i.id} style={{ marginBottom: "1.5rem" }}>
            <div className="linha-topo" style={{ marginBottom: ".4rem" }}>
              <div>
                <Link href={`/leads/${i.empresa_id}`} style={{ fontWeight: 600 }}>{i.nome}</Link>
                {i.visitou_prova && (
                  <span className="etiqueta ok" style={{ marginLeft: ".45rem" }}>
                    já abriu a análise
                  </span>
                )}
                <span className="etiqueta" style={{ marginLeft: ".45rem" }}>toque {i.toque}</span>
                <span className="etiqueta info" style={{ marginLeft: ".35rem" }}>
                  {i.canal === "email" ? "e-mail" : "WhatsApp"}
                </span>
                {i.cadencia && (
                  <span className="etiqueta" style={{ marginLeft: ".35rem" }}>{i.cadencia}</span>
                )}
                <div className="fraco mono" style={{ fontSize: ".8rem" }}>{i.destino ?? i.telefone}</div>
              </div>
              {i.score != null && (
                <div className="mono" style={{ fontSize: "1.1rem", fontWeight: 600 }}>
                  {i.score.toFixed(0)}
                </div>
              )}
            </div>
            {i.assunto && <p className="legenda" style={{ margin: "0 0 .3rem" }}>Assunto: {i.assunto}</p>}
            <div className="msg-caixa">{i.texto}</div>
            {i.bloqueio && <div className="aviso" style={{ marginBottom: ".5rem" }}>{i.bloqueio}</div>}
            <div className="acoes">
              <button className="botao" disabled={!i.link} onClick={() => abrirEEnviar(i)}>
                {i.canal === "email" ? "Abrir no e-mail e marcar enviada" : "Abrir no WhatsApp e marcar enviada"}
              </button>
              <button className="botao secundario"
                      onClick={() => navigator.clipboard.writeText(i.texto)}>
                Copiar texto
              </button>
            </div>
          </div>
        ))
      )}

      <h2>Sobre o envio automático</h2>
      <p className="legenda" style={{ maxWidth: 620 }}>
        A API oficial do WhatsApp não permite iniciar conversa fria: mensagem de
        marketing exige que a pessoa tenha aceitado receber antes. As bibliotecas não
        oficiais conseguem, mas violam os termos e derrubam números novos em poucos
        dias. Por isso o primeiro toque é manual — são poucos cliques por dia — e a
        automação entra depois que o lead responde, quando a janela de 24 horas abre e
        tudo passa a ser permitido.
      </p>
    </>
  );
}
