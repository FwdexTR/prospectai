"use client";

import { useEffect, useState } from "react";
import { dataFmt, fmt, get, type StatusBase } from "@/lib/api";

/** Diz de onde vêm os dados: demonstração, base parcial ou a base completa. */
export default function AvisoBase() {
  const [s, setS] = useState<StatusBase | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  useEffect(() => {
    get<StatusBase>("/api/base/status").then(setS).catch((e) => setErro(e.message));
  }, []);

  if (erro) return <div className="aviso alta"><b>API fora do ar</b>{erro}</div>;
  if (!s) return null;
  if (!s.disponivel)
    return (
      <div className="aviso alta">
        <b>Nenhuma base de empresas</b>
        Gere a de demonstração com <code>py -m prospectai.cli base demo</code>, ou baixe e
        importe a da Receita com <code>base baixar</code> e <code>base importar</code>.
      </div>
    );
  const m = s.meta!;
  if (m.fonte === "demo")
    return (
      <div className="aviso">
        <b>Base de demonstração: {fmt(Number(m.total))} empresas fictícias</b>
        Serve para conhecer o sistema. Nenhum contato daqui é real, e a fila bloqueia o envio
        para elas. Para a base real (~7,8 GB da Receita): <code>base baixar</code> e depois{" "}
        <code>base importar</code>.
      </div>
    );
  if (m.parcial === "true")
    return (
      <div className="aviso">
        <b>Base parcial ({fmt(Number(m.total))} empresas, versão {dataFmt(m.versao)})</b>
        Estabelecimentos {m.arquivos.estabelecimentos}, Empresas {m.arquivos.empresas}
        {m.arquivos.simples ? "" : ", sem Simples/MEI"}. Contagens e análises cobrem só esse recorte.
      </div>
    );
  return (
    <p className="fraco" style={{ fontSize: ".78rem", marginTop: "-1rem", marginBottom: "1.25rem" }}>
      Receita Federal, versão {dataFmt(m.versao)} · {fmt(Number(m.total))} empresas
      {m.so_ativas === "true" ? " ativas" : ""}
    </p>
  );
}
