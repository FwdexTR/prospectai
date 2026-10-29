"""Testes do modulo de prospeccao B2B: base CNPJ, listas, CRM e cadencias.

Os casos de importacao vem do que apareceu no dado real da Receita
(Estabelecimentos1.zip de 08/08/2026, 1,17 mi de linhas):
  - byte 0x80-0x9F fazia o leitor latin-1 do DuckDB recusar o arquivo inteiro;
  - o mesmo e-mail de contabilidade aparecia em 1.241 CNPJs;
  - celular antigo de 8 digitos, sem o nono digito.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import duckdb
import pytest

from prospectai import base, base_demo, base_importar, crm, db, outreach
from prospectai.config import Config
from prospectai.normalize import normalizar_telefone
from prospectai.pipeline import _coletar_da_base
from prospectai.ramos import resolver

CFG = Config(remetente_nome="Fulano", remetente_cidade="Campos dos Goytacazes",
             remetente_whatsapp="+5522999990000", remetente_profissao="desenvolvedor",
             base_url="http://localhost:8000", places_key=None)

TEXTO_OK = ("Oi, {{nome}}! Aqui é o {{remetente}}, de {{remetente_cidade}}. "
            "Vi que vocês atuam com {{atividade}}. Posso te mandar uma ideia? "
            "Se não quiser receber, é só falar.")


# ==========================================================================
# Fixtures
# ==========================================================================
@pytest.fixture(scope="module")
def base_demo_pequena(tmp_path_factory):
    destino = tmp_path_factory.mktemp("base") / "demo.duckdb"
    base_demo.gerar(quantidade=4000, destino=destino)
    anterior = os.environ.get("BASE_CNPJ")
    os.environ["BASE_CNPJ"] = str(destino)
    yield destino
    base.fechar()
    if anterior is None:
        os.environ.pop("BASE_CNPJ", None)
    else:
        os.environ["BASE_CNPJ"] = anterior


@pytest.fixture
def banco(tmp_path):
    con = db.conectar(tmp_path / "crm.db")
    db.inicializar(con)
    yield con
    con.close()


def _zip(pasta: Path, nome: str, linhas: list[str], bruto: bytes | None = None) -> None:
    conteudo = bruto if bruto is not None else "".join(l + "\n" for l in linhas).encode("latin-1")
    with zipfile.ZipFile(pasta / nome, "w") as z:
        z.writestr("K3241.K03200Y0.D60808.TESTE", conteudo)


def _estab(cnpj8: str, ordem: str, dv: str, fantasia: str, cnae: str, ddd: str, tel: str,
           email: str = "", municipio: str = "5819", uf: str = "RJ") -> str:
    campos = [cnpj8, ordem, dv, "1", fantasia, "02", "20200101", "00", "", "",
              "20200101", cnae, "", "RUA", "TESTE", "10", "", "CENTRO", "28000000", uf,
              municipio, ddd, tel, "", "", "", "", email, "", ""]
    return ";".join(f'"{c}"' for c in campos)


def _pasta_receita(pasta: Path, extra_est: bytes | None = None) -> Path:
    """Conjunto minimo e completo no layout da RFB."""
    pasta.mkdir(parents=True, exist_ok=True)
    est = [
        _estab("11111111", "0001", "91", "OFICINA ANTIGA", "4520001", "22", "98765432"),
        _estab("22222222", "0001", "01", "PADARIA FIXO", "4721102", "22", "27331234",
               "CONTATO@CONTADOR.COM.BR"),
        _estab("33333333", "0001", "02", "", "9602501", "22", "998887777",
               "contato@contador.com.br"),
    ]
    for i in range(4):
        est.append(_estab(f"4444444{i}", "0001", "03", f"LOJA {i}", "4781400", "21",
                          "988880000", "contato@contador.com.br", "6001"))
    for i in range(base_importar.PARTES):
        linhas = est if i == 0 else []
        if i == 1 and extra_est is not None:
            _zip(pasta, f"Estabelecimentos{i}.zip", [], extra_est)
        else:
            _zip(pasta, f"Estabelecimentos{i}.zip", linhas)
        emp = []
        if i == 0:
            emp = ['"11111111";"OFICINA ANTIGA LTDA";"2062";"49";"50000,00";"01";""',
                   '"33333333";"MARIA SILVA 12345678909";"2135";"50";"5000,00";"01";""']
        _zip(pasta, f"Empresas{i}.zip", emp)
    _zip(pasta, "Simples.zip", ['"33333333";"S";"20200101";"";"S";"20200101";""'])
    _zip(pasta, "Cnaes.zip", ['"4520001";"Serviços de manutenção e reparação mecânica"'])
    _zip(pasta, "Municipios.zip", ['"5819";"CAMPOS DOS GOYTACAZES"', '"6001";"RIO DE JANEIRO"'])
    _zip(pasta, "Naturezas.zip", ['"2062";"Sociedade Empresária Limitada"'])
    _zip(pasta, "Motivos.zip", ['"00";"SEM MOTIVO"'])
    return pasta


# ==========================================================================
# Importacao
# ==========================================================================
class TestImportacao:
    def test_layout_real_telefone_cpf_e_contador(self, tmp_path):
        pasta = _pasta_receita(tmp_path / "zips")
        destino = tmp_path / "b.duckdb"
        r = base_importar.importar(pasta, destino)
        assert r["estabelecimentos"] == 7 and r["parcial"] is False
        c = duckdb.connect(str(destino), read_only=True)
        linhas = dict(c.execute("SELECT nome_fantasia, celular FROM estabelecimento").fetchall())
        # celular antigo (8 digitos, comeca com 9) ganha o nono digito
        assert linhas["OFICINA ANTIGA"] == "+5522998765432"
        # fixo continua fixo e nao vira celular
        assert linhas["PADARIA FIXO"] is None
        # I7: CPF sai da razao social do MEI
        razao, mei = c.execute("SELECT razao_social, mei FROM estabelecimento "
                               "WHERE cnpj_basico = '33333333'").fetchone()
        assert razao == "MARIA SILVA" and mei is True
        # e-mail do contador: 6 CNPJs, maiusculo ou nao
        assert c.execute("SELECT max(email_qtd) FROM estabelecimento").fetchone()[0] == 6
        # sem nome fantasia nem razao: nome cai para o CNPJ, nunca vazio
        assert c.execute("SELECT count(*) FROM estabelecimento WHERE nome IS NULL").fetchone()[0] == 0
        c.close()

    def test_byte_de_controle_nao_derruba_o_arquivo(self, tmp_path):
        """0x96 (travessao no cp1252) fazia o DuckDB recusar o CSV inteiro."""
        linha = _estab("55555555", "0001", "04", "BAR X", "5611204", "22", "999990000")
        bruto = (linha.replace("BAR X", "BAR \x96 X") + "\n").encode("latin-1")
        pasta = _pasta_receita(tmp_path / "zips", extra_est=bruto)
        r = base_importar.importar(pasta, tmp_path / "b.duckdb")
        assert r["estabelecimentos"] == 8

    def test_incompleta_aborta_e_nao_toca_na_base_atual(self, tmp_path):
        pasta = _pasta_receita(tmp_path / "zips")
        (pasta / "Empresas3.zip").unlink()
        (pasta / "Simples.zip").write_bytes(b"isto nao e zip")
        destino = tmp_path / "b.duckdb"
        destino.write_bytes(b"base antiga")
        with pytest.raises(base_importar.ImportacaoIncompleta) as exc:
            base_importar.importar(pasta, destino)
        assert "Empresas3.zip" in str(exc.value) and "Simples.zip" in str(exc.value)
        assert destino.read_bytes() == b"base antiga"
        assert not list(tmp_path.glob("*.importando.duckdb"))

    def test_parcial_so_com_permissao_e_fica_marcada(self, tmp_path):
        pasta = _pasta_receita(tmp_path / "zips")
        (pasta / "Empresas3.zip").unlink()
        destino = tmp_path / "b.duckdb"
        base_importar.importar(pasta, destino, permitir_parcial=True)
        c = duckdb.connect(str(destino), read_only=True)
        assert dict(c.execute("SELECT * FROM meta").fetchall())["parcial"] == "true"
        c.close()

    def test_versao_vem_do_nome_interno(self):
        assert base_importar._versao_do_nome("K3241.K03200Y1.D60808.ESTABELE") == "2026-08-08"

    @pytest.mark.parametrize("ddd,num", [
        ("22", "98765432"), ("22", "998765432"), ("22", "27331234"), ("021", "988887777"),
        ("30", "999999999"), ("22", "88765432"), ("22", "12345"), ("", "22998765432"),
        ("11", "3333 4444"), ("22", "888765432"),
    ])
    def test_telefone_sql_igual_ao_python(self, ddd, num):
        """O importador normaliza em SQL; a regra e a do normalize.py."""
        c = duckdb.connect()
        c.execute(base_importar.MACROS)
        sql = c.execute("SELECT tel_e164(?, ?)", [ddd, num]).fetchone()[0]
        assert sql == normalizar_telefone(ddd.lstrip("0") + num)


# ==========================================================================
# Busca, analise, perfil e exportacao
# ==========================================================================
class TestBase:
    def test_filtros_combinados(self, base_demo_pequena):
        f = base.Filtros.de_dict({"ufs": ["RJ"], "cnaes": ["4520"], "com_celular": True})
        r = base.buscar(f, por_pagina=200)
        assert r["total"] > 0
        assert all(i["uf"] == "RJ" and i["cnae"].startswith("4520") and i["tem_celular"]
                   for i in r["itens"])

    def test_email_proprio_descarta_contador(self, base_demo_pequena):
        com = base.contar(base.Filtros(com_email=True))
        proprio = base.contar(base.Filtros(email_proprio=True))
        assert 0 < proprio < com
        r = base.buscar(base.Filtros(email_proprio=True), por_pagina=200)
        assert all(i["email_qtd"] <= base.LIMITE_EMAIL_PROPRIO for i in r["itens"])

    def test_busca_por_cnpj_e_texto_hostil(self, base_demo_pequena):
        alvo = base.buscar(base.Filtros(), por_pagina=1)["itens"][0]["cnpj"]
        assert base.buscar(base.Filtros(q=alvo[:8]))["total"] >= 1
        # parametro, nunca concatenado: nao quebra nem injeta
        assert base.buscar(base.Filtros(q="'; DROP TABLE estabelecimento; --"))["total"] == 0

    def test_analise_fecha_com_o_total(self, base_demo_pequena):
        a = base.analisar(base.Filtros(ufs=["RJ"]))
        assert sum(x["n"] for x in a["por_uf"]) == a["resumo"]["total"]
        assert sum(x["n"] for x in a["por_porte"]) == a["resumo"]["total"]

    def test_perfil_da_carteira_acha_o_ramo(self, base_demo_pequena):
        oficinas = base.cnpjs(base.Filtros(cnaes=["4520"]), limite=40)
        p = base.perfil_icp(oficinas)
        assert p["encontradas"] == len(oficinas)
        assert "4520" in p["filtros"]["cnaes"]
        assert p["parecidos_na_base"] >= len(oficinas)

    def test_exportacao_csv(self, base_demo_pequena):
        f = base.Filtros(ufs=["SP"])
        texto = "".join(base.exportar_csv(f))
        linhas = texto.strip("\n").split("\n")
        assert texto.startswith("﻿cnpj;")
        assert len(linhas) - 1 == base.contar(f)


# ==========================================================================
# CRM
# ==========================================================================
class TestCRM:
    def test_revelar_e_idempotente_e_marca_email_de_contador(self, base_demo_pequena, banco):
        alvo = base.cnpjs(base.Filtros(com_email=True), limite=30)
        r1 = crm.revelar(banco, alvo)
        r2 = crm.revelar(banco, alvo)
        assert len(r1["reveladas"]) == 30 and not r2["reveladas"]
        assert len(r2["ja_existiam"]) == 30
        assert db.um(banco, "SELECT COUNT(*) AS n FROM empresa")["n"] == 30
        assert db.um(banco, "SELECT COUNT(*) AS n FROM lead WHERE estado='NOVO'")["n"] == 30
        compartilhados = base.buscar(base.Filtros(com_email=True), por_pagina=200)["itens"]
        esperado = sum(1 for i in compartilhados
                       if i["cnpj"] in alvo and i["email_qtd"] > base.LIMITE_EMAIL_PROPRIO)
        marcados = db.um(banco, "SELECT COUNT(*) AS n FROM contato "
                                "WHERE validacao='compartilhado'")["n"]
        assert marcados == esperado

    def test_extrair_cnpjs_valida_dv(self):
        validos, invalidos = crm.extrair_cnpjs("11.222.333/0001-81, 11222333000182 e lixo")
        assert validos == ["11222333000181"] and invalidos == ["11222333000182"]

    def test_listas(self, banco):
        lid = crm.criar_lista(banco, "Oficinas Campos")
        assert crm.adicionar_itens(banco, lid, ["11222333000181", "11.222.333/0001-81"]) == 1
        assert crm.remover_itens(banco, lid, ["11222333000181"]) == 1
        assert crm.itens_da_lista(banco, lid) == []

    def test_funil_opt_out_terminal_e_toque_so_pelo_envio(self, banco):
        eid = _empresa_manual(banco)
        with pytest.raises(ValueError):
            crm.mudar_estado(banco, eid, "TOQUE_1")
        outreach.aplicar_opt_out(banco, eid, "teste")
        with pytest.raises(PermissionError):
            crm.mudar_estado(banco, eid, "NOVO")

    def test_assumir_conversa_cancela_toques(self, banco):
        eid = _empresa_manual(banco)
        cid = crm.salvar_cadencia(banco, "C", [_passo(), _passo(espera=2)])
        assert crm.inscrever(banco, cid, [eid], CFG)["inscritas"]
        crm.mudar_estado(banco, eid, "EM_CONVERSA")
        assert db.um(banco, "SELECT COUNT(*) AS n FROM mensagem WHERE empresa_id=? "
                            "AND estado='AGENDADA'", (eid,))["n"] == 0
        assert db.um(banco, "SELECT estado FROM inscricao")["estado"] == "INTERROMPIDA"

    def test_tarefas(self, banco):
        eid = _empresa_manual(banco)
        aid = crm.criar_atividade(banco, eid, "tarefa", "Ligar", vence_em="2000-01-01 09:00")
        t = crm.tarefas(banco)
        assert t[0]["id"] == aid and t[0]["atrasada"] == 1
        crm.concluir_atividade(banco, aid)
        assert crm.tarefas(banco) == []


# ==========================================================================
# Cadencias
# ==========================================================================
def _empresa_manual(con, fonte: str = "manual", email: str | None = "dono@oficina.com.br",
                    bairro: str | None = "Centro") -> int:
    eid = db.inserir(con, "empresa", {
        "nome": "OFICINA DO ZE", "nome_norm": "oficina ze", "cidade": "CAMPOS DOS GOYTACAZES",
        "uf": "RJ", "bairro": bairro, "fonte": fonte, "fonte_id": f"x{os.urandom(4).hex()}",
        "fonte_payload": '{"cnae_descricao": "Manutenção mecânica de veículos"}',
    })
    db.inserir(con, "contato", {"empresa_id": eid, "canal": "whatsapp",
                                "valor": "+5522999998888", "eh_movel": 1})
    if email:
        db.inserir(con, "contato", {"empresa_id": eid, "canal": "email", "valor": email,
                                    "validacao": "mx_ok"})
    con.execute("INSERT INTO lead (empresa_id, estado) VALUES (?, 'NOVO')", (eid,))
    con.commit()
    return eid


def _passo(canal="whatsapp", espera=0, corpo=TEXTO_OK, assunto=None):
    return {"canal": canal, "espera_dias": espera, "corpo": corpo,
            "assunto": assunto or ("Ideia para a {{nome}}" if canal == "email" else None)}


class TestCadencias:
    def test_maximo_tres_passos_e_variavel_desconhecida(self, banco):
        with pytest.raises(ValueError):
            crm.salvar_cadencia(banco, "x", [_passo()] * 4)                    # I4
        with pytest.raises(ValueError, match="desconhecida"):
            crm.salvar_cadencia(banco, "x", [_passo(corpo="Oi {{faturamento}}")])

    def test_inscricao_gera_toques_e_ordem_de_envio(self, banco):
        eid = _empresa_manual(banco)
        cid = crm.salvar_cadencia(banco, "Oficinas", [
            _passo(), _passo("email", 3, "Oi {{nome}}, retomando. Aqui é o {{remetente}}."),
            _passo("whatsapp", 5, "Última mensagem, {{nome}}. Aqui é o {{remetente}}."),
        ])
        r = crm.inscrever(banco, cid, [eid], CFG)
        assert r["inscritas"][0]["toques"] == 3 and not r["recusadas"]
        msgs = db.buscar(banco, "SELECT * FROM mensagem WHERE empresa_id=? ORDER BY toque", (eid,))
        assert [m["canal"] for m in msgs] == ["whatsapp", "email", "whatsapp"]
        assert all(m["validador_ok"] for m in msgs), [m["validador_erros"] for m in msgs]
        assert "Oficina do Ze" in msgs[0]["texto"]               # titulo, nao CAIXA ALTA
        assert db.um(banco, "SELECT estado FROM lead")["estado"] == "SELECIONADO"

        fila = outreach.fila(banco)
        assert [i["toque"] for i in fila] == [1] and fila[0]["link"].startswith("https://wa.me/")
        outreach.marcar_enviada(banco, msgs[0]["id"], forcar=True)
        # passo 2 agendado pela espera DO PASSO (3 dias), no relogio do SQLite
        dias = db.um(banco, "SELECT julianday(agendada_para) - julianday('now') AS d "
                            "FROM mensagem WHERE id=?", (msgs[1]["id"],))["d"]
        assert 2.9 < dias < 3.1
        banco.execute("UPDATE mensagem SET agendada_para = datetime('now', '-1 minute') "
                      "WHERE id=?", (msgs[1]["id"],))
        item = outreach.fila(banco)[0]
        assert item["canal"] == "email" and item["link"].startswith("mailto:dono@oficina.com.br")
        outreach.marcar_enviada(banco, msgs[1]["id"], forcar=True)
        banco.execute("UPDATE mensagem SET agendada_para = datetime('now', '-1 minute') "
                      "WHERE id=?", (msgs[2]["id"],))
        outreach.marcar_enviada(banco, msgs[2]["id"], forcar=True)
        assert db.um(banco, "SELECT estado FROM lead")["estado"] == "DORMENTE"
        assert db.um(banco, "SELECT estado FROM inscricao")["estado"] == "CONCLUIDA"

    def test_espera_no_primeiro_passo_segura_a_fila(self, banco):
        eid = _empresa_manual(banco)
        cid = crm.salvar_cadencia(banco, "C", [_passo(espera=2)])
        crm.inscrever(banco, cid, [eid], CFG)
        assert outreach.fila(banco) == []

    def test_variavel_sem_valor_bloqueia_a_mensagem(self, banco):
        """I1: {{bairro}} vazio nao pode virar 'no bairro ,'."""
        eid = _empresa_manual(banco, bairro=None)
        corpo = TEXTO_OK.replace("Vi que", "Vi no bairro {{bairro}} que")
        cid = crm.salvar_cadencia(banco, "C", [_passo(corpo=corpo)])
        r = crm.inscrever(banco, cid, [eid], CFG)
        assert r["inscritas"][0]["bloqueadas_pelo_validador"] == 1
        assert outreach.fila(banco) == []

    def test_primeiro_toque_precisa_de_saida(self, banco):
        eid = _empresa_manual(banco)
        cid = crm.salvar_cadencia(banco, "C", [_passo(corpo="Oi {{nome}}, aqui é o {{remetente}}.")])
        crm.inscrever(banco, cid, [eid], CFG)
        erros = db.um(banco, "SELECT validador_erros FROM mensagem")["validador_erros"]
        assert "saída" in erros

    def test_recusas_opt_out_sequencia_e_90_dias(self, banco):
        cid = crm.salvar_cadencia(banco, "C", [_passo()])
        cid2 = crm.salvar_cadencia(banco, "D", [_passo()])
        sai = _empresa_manual(banco)
        outreach.aplicar_opt_out(banco, sai, "teste")
        ocupada = _empresa_manual(banco)
        crm.inscrever(banco, cid, [ocupada], CFG)
        recente = _empresa_manual(banco)
        crm.inscrever(banco, cid, [recente], CFG)
        mid = db.um(banco, "SELECT id FROM mensagem WHERE empresa_id=?", (recente,))["id"]
        outreach.marcar_enviada(banco, mid, forcar=True)

        r = crm.inscrever(banco, cid2, [sai, ocupada, recente], CFG)
        motivos = {x["empresa_id"]: x["motivo"] for x in r["recusadas"]}
        assert not r["inscritas"]
        assert "opt-out" in motivos[sai]
        assert "outra sequência" in motivos[ocupada]
        assert "90 dias" in motivos[recente]

    def test_email_de_contador_nao_recebe(self, banco):
        eid = _empresa_manual(banco, email=None)
        db.inserir(banco, "contato", {"empresa_id": eid, "canal": "email",
                                      "valor": "fiscal@contador.com.br",
                                      "validacao": "compartilhado"})
        banco.commit()
        cid = crm.salvar_cadencia(banco, "So e-mail", [
            _passo("email", corpo=TEXTO_OK)])
        r = crm.inscrever(banco, cid, [eid], CFG)
        assert not r["inscritas"] and "sem contato" in r["recusadas"][0]["motivo"]

    def test_resposta_humana_marca_respondeu(self, banco):
        eid = _empresa_manual(banco)
        cid = crm.salvar_cadencia(banco, "C", [_passo(), _passo(espera=2)])
        crm.inscrever(banco, cid, [eid], CFG)
        outreach.registrar_resposta(banco, eid, "quanto custa?")
        assert db.um(banco, "SELECT estado FROM inscricao")["estado"] == "RESPONDEU"

    def test_demo_nunca_sai(self, base_demo_pequena, banco):
        alvo = base.cnpjs(base.Filtros(com_celular=True), limite=1)
        eid = crm.revelar(banco, alvo)["reveladas"][0]["empresa_id"]
        cid = crm.salvar_cadencia(banco, "C", [_passo()])
        assert crm.inscrever(banco, cid, [eid], CFG)["inscritas"]
        item = outreach.fila(banco)[0]
        assert item["link"] is None and "demonstração" in item["bloqueio"]
        assert outreach.marcar_enviada(banco, item["id"], forcar=True).ok is False

    def test_enriquecer_empresa_real_sem_rede(self, banco, monkeypatch):
        """Caminho completo do 'robo' com a rede simulada: site aceito vira
        contato (e-mail e WhatsApp), o e-mail e validado e a empresa ganha score."""
        from prospectai import discover
        from prospectai.audit import Auditoria
        from prospectai.discover import Descoberta

        eid = _empresa_manual(banco, email=None)
        banco.execute("DELETE FROM contato WHERE empresa_id = ?", (eid,))
        banco.commit()

        async def falso(empresas, concorrencia=8, progresso=None):
            a = Auditoria(site_url="https://oficinadoze.com.br", http_status=200,
                          tem_viewport=True, tls_valido=True,
                          emails_no_site=["contato@oficinadoze.com.br"],
                          telefones_no_site=["22998887766"])
            d = Descoberta(url=a.site_url, como="chute", confianca=2,
                           provas=["dominio", "telefone"])
            return {0: (d, a)}

        async def mx(dominios):
            return {d: "mx_ok" for d in dominios}

        monkeypatch.setattr(discover, "descobrir_muitos", falso)
        monkeypatch.setattr(crm, "_validar_dominios", mx)
        r = crm.enriquecer(banco, [eid])
        assert r["sites_aceitos"] == 1 and r["emails_do_site"] == 1
        assert r["whatsapp_do_site"] == 1
        email = db.um(banco, "SELECT * FROM contato WHERE canal='email'")
        assert email["origem"] == "site" and email["validacao"] == "mx_ok"
        assert db.um(banco, "SELECT COUNT(*) AS n FROM score WHERE empresa_id=?", (eid,))["n"] == 1

    def test_site_suspeito_nao_vira_contato(self, banco, monkeypatch):
        """Mesma regra do telefone (I1): dominio sem prova e de outra empresa."""
        from prospectai import discover
        from prospectai.audit import Auditoria
        from prospectai.discover import Descoberta

        eid = _empresa_manual(banco, email=None)

        async def falso(empresas, concorrencia=8, progresso=None):
            a = Auditoria(site_url="https://outra.com.br", http_status=200,
                          emails_no_site=["vendas@outra.com.br"])
            return {0: (Descoberta(url=a.site_url, como="suspeito", confianca=1), a)}

        async def mx(dominios):
            return {d: "mx_ok" for d in dominios}

        monkeypatch.setattr(discover, "descobrir_muitos", falso)
        monkeypatch.setattr(crm, "_validar_dominios", mx)
        crm.enriquecer(banco, [eid])
        assert db.um(banco, "SELECT COUNT(*) AS n FROM contato WHERE valor LIKE '%outra%'")["n"] == 0

    def test_demo_nao_e_enriquecida(self, base_demo_pequena, banco):
        alvo = base.cnpjs(base.Filtros(), limite=2)
        ids = [x["empresa_id"] for x in crm.revelar(banco, alvo)["reveladas"]]
        r = crm.enriquecer(banco, ids)
        assert r["ignoradas_demo"] == 2 and r["enriquecidas"] == 0


# ==========================================================================
# Campanha a partir da base
# ==========================================================================
def test_campanha_com_fonte_base(base_demo_pequena):
    brutas = _coletar_da_base("Campos dos Goytacazes", "RJ", [resolver("oficina")])
    assert brutas
    assert all(b["cidade"] == "Campos dos Goytacazes" for b in brutas)
    assert all(b["cnae"].startswith(("4520", "4530", "4543")) for b in brutas)
