"""Linha de comando do ProspectAI.

    py -m prospectai.cli rodar --cidade "Campos dos Goytacazes" --uf RJ --ramo restaurante
    py -m prospectai.cli leads --campanha 1
    py -m prospectai.cli mensagens --empresa 42
    py -m prospectai.cli ramos
    py -m prospectai.cli verificar
    py -m prospectai.cli base demo | baixar | importar | status
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import db
from .config import carregar
from .pipeline import executar
from .ramos import listar


def _barra(etapa: str, feito: int, total: int) -> None:
    if total <= 0:
        return
    if not sys.stderr.isatty():
        return
    largura = 28
    cheio = int(largura * feito / total)
    fim = "\n" if feito >= total else ""
    sys.stderr.write(
        f"\r  {etapa:<10} [{'#' * cheio}{'.' * (largura - cheio)}] {feito}/{total}{fim}"
    )
    sys.stderr.flush()


def cmd_rodar(args: argparse.Namespace) -> int:
    cfg = carregar()
    con = db.conectar()
    db.inicializar(con)

    if args.fonte == "receita":
        print("Fonte: Receita Federal (dados abertos de CNPJ).")
        print("       Primeira execucao baixa ~6,5 GB; os ZIPs sao apagados")
        print("       conforme processados. Sem contagem de avaliacoes, mas com")
        print("       telefone, porte, capital social e data de abertura.\n")
    elif not cfg.tem_places and args.fonte == "auto":
        print("Fonte: Overture Maps (gratis, sem chave). Telefone para ~86% das")
        print("       empresas medido em Campos, mas sem numero de avaliacoes: o eixo")
        print("       dinheiro do score fica ausente e a faixa nao passa de B.\n")

    resumo = executar(
        cidade=args.cidade, uf=args.uf, chaves_ramos=args.ramo,
        raio_km=args.raio, cfg=cfg, con=con,
        limite_fila=args.limite, progresso=_barra,
        fonte=args.fonte, partes_receita=args.partes,
    )

    c, a, s, f = resumo["coleta"], resumo["auditoria"], resumo["score"], resumo["fila"]
    print(f"\n=== {resumo['cidade']} | {', '.join(resumo['ramos'])} "
          f"| {resumo['segundos']}s | campanha #{resumo['campanha_id']}")
    print(f"  coleta ({c['fonte']}): {c['brutas']} brutas, "
          f"{c['redes_filtradas']} redes nacionais filtradas, {c['inseridas']} inseridas")
    print(f"  auditoria: {a.get('auditadas', 0)} auditadas | "
          f"site confirmado {a.get('aceitos', 0)} | suspeitos {a.get('suspeitos', 0)} | "
          f"sem site {a.get('sem_site', 0)} | usam perfil {a.get('perfil', 0)}")
    fx = s["faixas"]
    print(f"  score: A={fx['A']} B={fx['B']} C={fx['C']} D={fx['D']}")
    print(f"  fila: {f['preparados']} leads preparados "
          f"({f['elegiveis']} elegiveis, {f['sem_contato']} sem telefone)")
    print(f"\n  Ver: py -m prospectai.cli leads --campanha {resumo['campanha_id']}")
    return 0


def cmd_leads(args: argparse.Namespace) -> int:
    con = db.conectar()
    linhas = db.buscar(con, """
        SELECT e.id, e.nome, e.ramo, e.telefone, s.total, s.faixa, s.dor, s.dinheiro,
               a.site_url, a.site_encontrado_como,
               (SELECT COUNT(*) FROM mensagem m WHERE m.empresa_id = e.id) AS msgs,
               (SELECT GROUP_CONCAT(tipo, ', ') FROM sinal
                 WHERE empresa_id = e.id AND eixo = 'dor') AS dores
          FROM empresa e
          JOIN score s ON s.id = (SELECT id FROM score WHERE empresa_id = e.id
                                   ORDER BY id DESC LIMIT 1)
          JOIN auditoria a ON a.id = s.auditoria_id
     LEFT JOIN campanha_empresa ce ON ce.empresa_id = e.id
         WHERE (? IS NULL OR ce.campanha_id = ?)
      GROUP BY e.id
      ORDER BY s.total DESC LIMIT ?
    """, (args.campanha, args.campanha, args.limite))

    if not linhas:
        print("Nenhum lead. Rode `rodar` primeiro.")
        return 1

    print(f"{'#':>4} {'score':>6} {'f':>2} {'empresa':<32} {'telefone':<15} {'site':<28} dores")
    print("-" * 130)
    for l in linhas:
        site = (l["site_url"] or "SEM SITE")[:27]
        if l["site_encontrado_como"] == "suspeito":
            site = "? " + site[:25]
        print(f"{l['id']:>4} {l['total']:>6.1f} {l['faixa']:>2} {l['nome'][:31]:<32} "
              f"{(l['telefone'] or '-'):<15} {site:<28} {(l['dores'] or '')[:40]}")
    return 0


def cmd_mensagens(args: argparse.Namespace) -> int:
    con = db.conectar()
    linhas = db.buscar(con, """
        SELECT m.*, e.nome FROM mensagem m JOIN empresa e ON e.id = m.empresa_id
         WHERE m.empresa_id = ? ORDER BY m.toque
    """, (args.empresa,))
    if not linhas:
        print(f"Sem mensagens para a empresa {args.empresa}.")
        return 1
    for m in linhas:
        marca = "OK " if m["validador_ok"] else "REPROVADA"
        print(f"\n--- toque {m['toque']} [{m['template_id']}] {marca} "
              f"({len(m['texto'])} chars) ---")
        print(m["texto"])
        if not m["validador_ok"]:
            print(f"  erros: {m['validador_erros']}")
        print(f"  fatos: {m['fatos_usados']}")
    return 0


def cmd_verificar(args: argparse.Namespace) -> int:
    """Confere a configuracao e testa as chaves com UMA chamada barata cada.

    Existe para voce descobrir que a chave esta errada agora, e nao depois de
    uma busca inteira ter falhado no meio.
    """
    import asyncio

    import httpx

    cfg = carregar()
    problemas = 0

    print("Remetente")
    for rotulo, valor, obrigatorio in (
        ("nome", cfg.remetente_nome, True),
        ("cidade", cfg.remetente_cidade, True),
        ("whatsapp", cfg.remetente_whatsapp, False),
    ):
        falta = valor is None or "configure" in str(valor).lower()
        marca = "FALTA" if (falta and obrigatorio) else ("-" if falta else "ok")
        problemas += 1 if (falta and obrigatorio) else 0
        print(f"  {rotulo:10} {marca:6} {valor if not falta else ''}")

    print("\nGoogle Places")
    if not cfg.tem_places:
        print("  chave     AUSENTE")
        print("            Sem ela a fonte e o Overture: tem telefone e site, mas nao")
        print("            numero de avaliacoes, entao o score fica so na dor.")
    else:
        try:
            resp = httpx.post(
                "https://places.googleapis.com/v1/places:searchText",
                headers={"X-Goog-Api-Key": cfg.places_key,
                         "X-Goog-FieldMask": "places.id,places.displayName,places.userRatingCount",
                         "Content-Type": "application/json"},
                json={"textQuery": f"padaria em {cfg.remetente_cidade}",
                      "languageCode": "pt-BR", "regionCode": "BR"},
                timeout=30.0,
            )
            if resp.status_code == 200:
                achados = resp.json().get("places", [])
                com_reviews = sum(1 for p in achados if p.get("userRatingCount"))
                print(f"  chave     ok     {len(achados)} resultados de teste, "
                      f"{com_reviews} com contagem de avaliacoes")
            else:
                detalhe = (resp.json().get("error", {}).get("message", "")
                           if resp.headers.get("content-type", "").startswith("application/json")
                           else resp.text[:120])
                print(f"  chave     ERRO   HTTP {resp.status_code}: {detalhe[:150]}")
                if resp.status_code == 403:
                    print("            Causas comuns: Places API (New) nao ativada no projeto,")
                    print("            faturamento nao vinculado, ou restricao de IP bloqueando.")
                problemas += 1
        except httpx.HTTPError as exc:
            print(f"  chave     ERRO   {type(exc).__name__}")
            problemas += 1

    print("\nPageSpeed Insights")
    try:
        r = asyncio.run(_testar_psi())
        print(f"  {'com chave' if os.environ.get('PAGESPEED_API_KEY') else 'sem chave':10} "
              f"{'ok' if r else 'indisponivel'}"
              f"{'' if r else '   (cota anonima estourada; a chave e gratuita)'}")
    except Exception:
        print("  status    indisponivel")

    print(f"\n{'Tudo pronto.' if problemas == 0 else f'{problemas} pendencia(s) acima.'}")
    return 0 if problemas == 0 else 1


async def _testar_psi() -> bool:
    import httpx

    from .psi import medir
    async with httpx.AsyncClient() as cli:
        return await medir("https://example.com", cli) is not None


def cmd_ramos(_: argparse.Namespace) -> int:
    for r in listar():
        print(f"  {r.chave:<22} {r.rotulo:<36} R$ {r.ticket[0]}-{r.ticket[1]}")
        print(f"  {'':22} -> {r.oferta}")
    return 0


def _barra_simples(etapa: str, feito: int, total: int) -> None:
    """Progresso que tambem aparece fora de terminal interativo (logs, CI)."""
    if total and (feito == total or feito == 0):
        print(f"  {etapa}: {feito}/{total}", flush=True)


def cmd_base(args: argparse.Namespace) -> int:
    from pathlib import Path

    from . import base, base_demo, base_importar

    if args.acao == "status":
        caminho = base.caminho_base()
        if caminho is None:
            print("Nenhuma base. Rode `base demo` ou `base importar`.")
            return 1
        meta = base.meta()
        print(f"Arquivo: {caminho}")
        for k, v in meta.items():
            print(f"  {k}: {v}")
        return 0

    if args.acao == "demo":
        print("Gerando base de DEMONSTRACAO (empresas ficticias, contato bloqueado)...")
        r = base_demo.gerar(quantidade=args.quantidade, progresso=_barra_simples)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    if args.acao == "baixar":
        from . import base_importar as bi
        versao = args.versao or bi.versoes_disponiveis()[-1]
        arquivos = bi.arquivos_da_versao(versao)
        total = sum(t for _, t in arquivos) / 1e9
        print(f"Versao {versao}: {len(arquivos)} arquivos, {total:.2f} GB "
              f"(sem Socios -- I7). Destino: {bi.PASTA_DOWNLOADS / versao}")
        pasta = bi.baixar_versao(versao, progresso=_barra)
        print(f"Pronto. Agora: py -m prospectai.cli base importar --pasta \"{pasta}\"")
        return 0

    if args.acao == "importar":
        pasta = Path(args.pasta) if args.pasta else None
        if pasta is None:
            versoes = sorted(p for p in base_importar.PASTA_DOWNLOADS.glob("*") if p.is_dir())
            if not versoes:
                print("Nenhuma pasta baixada. Use --pasta ou rode `base baixar`.")
                return 1
            pasta = versoes[-1]
        print(f"Importando de {pasta} (so ativas: {not args.todas_situacoes})...")
        try:
            r = base_importar.importar(pasta, so_ativas=not args.todas_situacoes,
                                       permitir_parcial=args.parcial, progresso=_barra_simples)
        except base_importar.ImportacaoIncompleta as exc:
            print(f"ABORTADO: {exc}")
            return 2
        except PermissionError:
            print("A base atual esta aberta pela API. Pare o uvicorn e rode de novo.")
            return 3
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    return 1


def cmd_overture(args: argparse.Namespace) -> int:
    """Gera o JSON do painel online (telefone + situacao do site, por regiao)."""
    from pathlib import Path

    from .sources import overture

    print(f"Overture {overture.versao_atual()} -- regiao {args.regiao}...")
    p = overture.montar_painel(args.regiao, verificar=not args.sem_verificar,
                               progresso=_barra_simples)
    itens = p["itens"]
    destino = Path(args.saida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(p, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
    com_site = [i for i in itens if i["site"]]
    print(f"{len(itens)} negocios | {sum(i['celular'] for i in itens)} com celular | "
          f"{len(com_site)} com site | "
          f"{sum(i.get('site_status') == 'fora_do_ar' for i in com_site)} sites fora do ar")
    print(f"Gravado em {destino} ({destino.stat().st_size / 1e6:.1f} MB)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="prospectai", description="Prospeccao de leads locais")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("base", help="base de empresas (CNPJ): demo, baixar, importar, status")
    b.add_argument("acao", choices=("status", "demo", "baixar", "importar"))
    b.add_argument("--quantidade", type=int, default=60_000, help="demo: quantas empresas")
    b.add_argument("--versao", help="baixar: AAAA-MM (padrao: a mais recente)")
    b.add_argument("--pasta", help="importar: pasta com os ZIPs da Receita")
    b.add_argument("--parcial", action="store_true",
                   help="importar mesmo faltando arquivos (fica marcado na base)")
    b.add_argument("--todas-situacoes", action="store_true",
                   help="importar tambem baixadas/inaptas (base ~2x maior)")
    b.set_defaults(func=cmd_base)

    o = sub.add_parser("overture", help="painel de telefone + site por regiao (gratis)")
    o.add_argument("acao", choices=("painel",))
    o.add_argument("--regiao", default="norte-fluminense")
    o.add_argument("--saida", default=str(db.RAIZ / "dados" / "painel_overture.json"))
    o.add_argument("--sem-verificar", action="store_true",
                   help="nao testar se os sites estao no ar (mais rapido)")
    o.set_defaults(func=cmd_overture)

    r = sub.add_parser("rodar", help="executa a cadeia inteira para uma cidade+ramo")
    r.add_argument("--cidade", required=True)
    r.add_argument("--uf", required=True)
    r.add_argument("--ramo", required=True, nargs="+", help="uma ou mais chaves de ramo")
    r.add_argument("--raio", type=float, default=0, help="km; 0 = so o municipio")
    r.add_argument("--limite", type=int, default=30, help="tamanho da fila de abordagem")
    r.add_argument("--fonte", choices=("auto", "overture", "osm", "places", "receita", "base"),
                   default="auto",
                   help="auto usa Places se houver chave, senao Overture (gratis); "
                        "base usa a base CNPJ importada (instantaneo); "
                        "receita varre os ZIPs a cada busca (~6,5 GB na 1a vez)")
    r.add_argument("--partes", type=int, default=10,
                   help="so para --fonte receita: quantas das 10 partes processar")
    r.set_defaults(func=cmd_rodar)

    l = sub.add_parser("leads", help="lista os leads pontuados")
    l.add_argument("--campanha", type=int, default=None)
    l.add_argument("--limite", type=int, default=40)
    l.set_defaults(func=cmd_leads)

    m = sub.add_parser("mensagens", help="mostra os 3 toques de uma empresa")
    m.add_argument("--empresa", type=int, required=True)
    m.set_defaults(func=cmd_mensagens)

    rr = sub.add_parser("ramos", help="lista os ramos do catalogo")
    rr.set_defaults(func=cmd_ramos)

    v = sub.add_parser("verificar", help="confere .env e testa as chaves de API")
    v.set_defaults(func=cmd_verificar)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
