"""E8 -- a auditoria hospedada.

Uma pagina por lead, em /a/{token}. Sobria de proposito: e um laudo, nao um
panfleto. Sem preco, sem "GARANTA JA", sem formulario. A venda acontece na
conversa; aqui so mostramos o que foi medido.

`primeira_visita_em` e o sinal de intencao mais forte do sistema inteiro: o
sujeito abriu e leu os problemas do proprio negocio. Isso dispara notificacao.
"""

from __future__ import annotations

import html
import secrets
from datetime import date

from .audit import Auditoria
from .ramos import CATALOGO
from .signals import Sinal

# Explicacao em portugues de gente. O laudo tem que ser entendido por quem nao
# e tecnico -- e essa e a parte que o concorrente nao faz.
EXPLICACOES: dict[str, str] = {
    "SEM_SITE": "Quem procura vocês no Google encontra o endereço e o telefone, mas não tem para onde ir depois. Sem site, a decisão de comprar acontece na página de outra empresa.",
    "SITE_E_PERFIL": "O link leva para uma rede social. Funciona para quem já conhece vocês, mas não aparece em busca no Google e não é uma página que vocês controlam.",
    "SITE_MORTO": "O endereço cadastrado não responde. Quem clicar nele vai ver uma página de erro do navegador — e a impressão é de empresa que fechou.",
    "SITE_QUEBRADO": "O servidor está respondendo com erro em vez da página. Para o visitante, é o mesmo que estar fora do ar.",
    "SEM_HTTPS": "Sem certificado de segurança, o navegador mostra o aviso \"Não seguro\" antes da página abrir. A maioria das pessoas volta nesse momento.",
    "NAO_RESPONSIVO": "O site não foi feito para telas de celular. Como a maior parte dos acessos hoje vem do celular, o visitante precisa dar zoom e arrastar para ler.",
    "LENTO": "Nota do próprio teste do Google para a versão de celular. Abaixo de 50 é considerado ruim, e afeta a posição nos resultados de busca.",
    "LCP_RUIM": "Tempo até o conteúdo principal aparecer. Acima de 4 segundos, boa parte das pessoas desiste antes de ver qualquer coisa.",
    "RESPOSTA_LENTA": "Tempo que o servidor levou só para começar a responder, antes mesmo de carregar imagens.",
    "SITE_ABANDONADO": "O ano no rodapé indica que o site não recebe manutenção há um tempo. O visitante repara nisso e conclui que a informação pode estar desatualizada.",
    "CONSTRUTOR_GRATUITO": "O site está hospedado num plano gratuito, o que costuma incluir marca d'água e propaganda de terceiro na página de vocês.",
    "CARDAPIO_EM_PDF": "PDF no celular abre fora do site, exige zoom e não permite clicar para pedir. Cardápio em página normal resolve.",
    "PAGINA_PESADA": "Quantidade de código que o visitante precisa baixar antes de ver a página. Pesa mais em conexão móvel.",
    "IMAGENS_SEM_LAZY": "As imagens são carregadas todas de uma vez, inclusive as que estão no fim da página e ninguém vai ver.",
    "TITULO_GENERICO": "É esse texto que aparece como título nos resultados do Google e na aba do navegador.",
    "SEM_WHATSAPP": "Não há botão de WhatsApp na página. Quem está no celular precisa anotar o número e sair do site para falar com vocês.",
    "SEM_MEDICAO": "Sem ferramenta de medição instalada, não dá para saber quantas pessoas visitam o site nem de onde elas vêm.",
}

ROTULOS: dict[str, str] = {
    "SEM_SITE": "Site oficial", "SITE_E_PERFIL": "Site próprio",
    "SITE_MORTO": "Site no ar", "SITE_QUEBRADO": "Site no ar",
    "SEM_HTTPS": "Certificado de segurança", "NAO_RESPONSIVO": "Adaptado ao celular",
    "LENTO": "Velocidade no celular", "LCP_RUIM": "Tempo de carregamento",
    "RESPOSTA_LENTA": "Tempo de resposta", "SITE_ABANDONADO": "Última atualização",
    "CONSTRUTOR_GRATUITO": "Hospedagem", "CARDAPIO_EM_PDF": "Cardápio",
    "PAGINA_PESADA": "Peso da página", "IMAGENS_SEM_LAZY": "Otimização de imagens",
    "TITULO_GENERICO": "Título no Google", "SEM_WHATSAPP": "Botão de WhatsApp",
    "SEM_MEDICAO": "Medição de visitas",
}


def novo_token() -> str:
    return secrets.token_urlsafe(16)


def _valor_exibido(s: Sinal) -> str:
    ev = s.evidencia or {}
    if s.tipo == "LENTO":
        return f"{ev.get('psi_mobile')}/100"
    if s.tipo == "LCP_RUIM":
        return f"{ev.get('lcp_ms', 0)/1000:.1f}s"
    if s.tipo == "RESPOSTA_LENTA":
        return f"{ev.get('tempo_resposta_ms', 0)/1000:.1f}s"
    if s.tipo == "PAGINA_PESADA":
        return f"{ev.get('html_bytes', 0)/1024:.0f} KB"
    if s.tipo == "IMAGENS_SEM_LAZY":
        return f"{ev.get('imagens_sem_lazy')} imagens"
    if s.tipo == "SITE_ABANDONADO":
        return str(ev.get("ano_copyright"))
    if s.tipo == "CONSTRUTOR_GRATUITO":
        return str(ev.get("plataforma") or "plano gratuito")
    if s.tipo == "SITE_QUEBRADO":
        return f"erro {ev.get('status')}"
    if s.tipo in ("SEM_SITE", "SITE_MORTO"):
        return "não encontrado"
    if s.tipo == "SITE_E_PERFIL":
        return str(ev.get("perfil") or "perfil social")
    if s.tipo == "TITULO_GENERICO":
        return f'"{ev.get("titulo")}"'
    return "não"


def _linhas_ok(a: Auditoria | None) -> list[tuple[str, str]]:
    """O que esta certo. Um laudo so com vermelho parece venda; com verde,
    parece diagnostico -- e e diagnostico."""
    if a is None or a.erro:
        return []
    ok: list[tuple[str, str]] = []
    if a.tls_valido:
        ok.append(("Certificado de segurança", "válido"))
    if a.tem_viewport:
        ok.append(("Adaptado ao celular", "sim"))
    if a.tem_whatsapp:
        ok.append(("Botão de WhatsApp", "presente"))
    if a.tem_analytics:
        ok.append(("Medição de visitas", "instalada"))
    if a.http_status == 200 and (a.tempo_resposta_ms or 9999) < 1500:
        ok.append(("Tempo de resposta", f"{(a.tempo_resposta_ms or 0)/1000:.1f}s"))
    return ok


def renderizar(
    nome_empresa: str,
    ramo: str | None,
    sinais: list[Sinal],
    auditoria: Auditoria | None,
    remetente_nome: str,
    remetente_whatsapp: str | None,
    site_url: str | None,
    token: str,
) -> str:
    e = html.escape
    dores = [s for s in sinais if s.eixo == "dor"]
    r = CATALOGO.get(ramo or "")

    linhas_problema = "".join(
        f"""<tr>
              <td class="rotulo">{e(ROTULOS.get(s.tipo, s.tipo))}</td>
              <td class="valor ruim">{e(_valor_exibido(s))}</td>
            </tr>
            <tr class="explica"><td colspan="2">{e(EXPLICACOES.get(s.tipo, ''))}</td></tr>"""
        for s in dores
    )
    linhas_ok = "".join(
        f'<tr><td class="rotulo">{e(k)}</td><td class="valor bom">{e(v)}</td></tr>'
        for k, v in _linhas_ok(auditoria)
    )

    bloco_ok = (
        f'<h2>O que já está certo</h2><table>{linhas_ok}</table>' if linhas_ok else ""
    )
    bloco_oferta = (
        f'<p class="oferta">Pelo tipo de negócio, o que costuma resolver isso é: '
        f'<strong>{e(r.oferta)}</strong>.</p>' if r else ""
    )
    alvo = e(site_url) if site_url else "presença digital"
    zap = (
        f'<a href="https://wa.me/{e((remetente_whatsapp or "").lstrip("+"))}">'
        f'{e(remetente_whatsapp or "")}</a>' if remetente_whatsapp else ""
    )

    return f"""<!doctype html>
<html lang="pt-BR"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Análise — {e(nome_empresa)}</title>
<style>
  :root {{ --tinta:#1a1a1a; --fraco:#666; --linha:#e5e5e5; --ruim:#c0392b; --bom:#1e8449; --fundo:#fff; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --tinta:#e8e8e8; --fraco:#9a9a9a; --linha:#333; --ruim:#ff6b5a; --bom:#4ade80; --fundo:#141414; }}
  }}
  * {{ box-sizing:border-box }}
  body {{ margin:0; padding:2rem 1rem 4rem; background:var(--fundo); color:var(--tinta);
         font:16px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }}
  main {{ max-width:640px; margin:0 auto }}
  h1 {{ font-size:1.5rem; margin:0 0 .25rem }}
  .sub {{ color:var(--fraco); font-size:.9rem; margin:0 0 2rem }}
  h2 {{ font-size:1.05rem; margin:2.5rem 0 .75rem; padding-bottom:.4rem;
        border-bottom:1px solid var(--linha) }}
  table {{ width:100%; border-collapse:collapse }}
  td {{ padding:.6rem 0; vertical-align:top }}
  .rotulo {{ color:var(--fraco) }}
  .valor {{ text-align:right; font-weight:600; font-variant-numeric:tabular-nums }}
  .ruim {{ color:var(--ruim) }} .bom {{ color:var(--bom) }}
  .explica td {{ padding:0 0 1.1rem; color:var(--fraco); font-size:.88rem;
                 border-bottom:1px solid var(--linha) }}
  .oferta {{ background:rgba(127,127,127,.08); padding:1rem; border-radius:8px;
             font-size:.95rem }}
  footer {{ margin-top:3rem; padding-top:1.25rem; border-top:1px solid var(--linha);
            color:var(--fraco); font-size:.85rem }}
  a {{ color:inherit }}
</style></head>
<body><main>
  <h1>Análise rápida — {e(nome_empresa)}</h1>
  <p class="sub">Feita em {date.today().strftime('%d/%m/%Y')} por {e(remetente_nome)}
     &middot; alvo: {alvo}</p>

  <h2>O que encontrei</h2>
  <table>{linhas_problema or '<tr><td>Nada crítico encontrado.</td></tr>'}</table>

  {bloco_ok}
  {bloco_oferta}

  <footer>
    <p>Esta é uma análise técnica automatizada dos dados públicos do site.
       Não é proposta comercial e não tem custo.</p>
    <p>{e(remetente_nome)} {zap}</p>
    <p><a href="/a/{e(token)}/nao-quero">Não quero receber contato</a></p>
  </footer>
</main></body></html>"""
