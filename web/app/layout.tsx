import type { Metadata } from "next";
import "./globals.css";
import Nav from "./nav";

export const metadata: Metadata = {
  title: "ProspectAI",
  description: "Prospecção de leads locais com auditoria técnica",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR">
      <body>
        <div className="casca">
          <aside className="lateral">
            <div className="marca">Prospect<span>AI</span></div>
            <div className="marca-sub">leads com laudo, não com lista</div>
            <Nav />
          </aside>
          <main className="conteudo">{children}</main>
        </div>
      </body>
    </html>
  );
}
