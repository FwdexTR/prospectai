"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const GRUPOS = [
  {
    titulo: null,
    itens: [{ href: "/", rotulo: "Painel" }],
  },
  {
    titulo: "Base de empresas",
    itens: [
      { href: "/base", rotulo: "Buscar empresas" },
      { href: "/listas", rotulo: "Listas" },
      { href: "/mercado", rotulo: "Mercado e ICP" },
    ],
  },
  {
    titulo: "Vendas",
    itens: [
      { href: "/crm", rotulo: "Funil" },
      { href: "/tarefas", rotulo: "Tarefas" },
      { href: "/cadencias", rotulo: "Cadências" },
      { href: "/fila", rotulo: "Fila de envio" },
    ],
  },
  {
    titulo: "Sites locais",
    itens: [{ href: "/nova", rotulo: "Nova busca com laudo" }],
  },
];

export default function Nav() {
  const caminho = usePathname();
  const ativo = (href: string) =>
    href === "/" ? caminho === "/" : caminho === href || caminho.startsWith(href + "/");
  return (
    <nav className="nav" aria-label="Seções">
      {GRUPOS.map((g, i) => (
        <div key={i} className="nav-grupo">
          {g.titulo && <div className="nav-titulo">{g.titulo}</div>}
          {g.itens.map((item) => (
            <Link key={item.href} href={item.href} className={ativo(item.href) ? "ativo" : ""}
                  aria-current={ativo(item.href) ? "page" : undefined}>
              {item.rotulo}
            </Link>
          ))}
        </div>
      ))}
    </nav>
  );
}
