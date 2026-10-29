"""Fonte Overture: classificacao de site, categorias, ramos e o painel.

Os casos vem do recorte real do Norte Fluminense (release 2026-09-23.1):
557 "sites" eram placeweb.site, 641 eram so Instagram, e Burger King e postos
Ipiranga apareciam como se fossem negocio local.
"""

from __future__ import annotations

import pytest

from prospectai.discover import eh_agregador
from prospectai.ramos import resolver
from prospectai.sources import overture as ov


def linha(**kw):
    base = {"id": "08f-x", "nome": "Oficina do Zé", "categoria": "automotive_service",
            "taxonomia": "automotive_repair", "status": None, "confidence": 0.9,
            "websites": [], "socials": [], "phones": ["+5522998887766"], "emails": [],
            "endereco": "Rua X, 10", "cidade": "Campos dos Goytacazes", "cep": "28000-000",
            "uf": "RJ", "lat": -21.75, "lng": -41.32, "origem": "meta"}
    return base | kw


class TestSite:
    @pytest.mark.parametrize("links,tipo", [
        (["http://www.oficinadoze.com.br/"], "proprio"),
        (["https://oficinadoze.placeweb.site/"], "gratuito"),
        (["https://zeoficina.negocio.site"], "gratuito"),
        (["https://instagram.com/oficinadoze"], "perfil"),
        (["https://instabio.cc/oficinadoze"], "perfil"),
        (["https://wa.me/5522998887766"], "perfil"),
        ([], "nenhum"),
        (None, "nenhum"),
    ])
    def test_classificacao(self, links, tipo):
        assert ov.classificar_site(links)[0] == tipo

    def test_site_proprio_vence_instagram(self):
        tipo, url = ov.classificar_site(["https://instagram.com/x", "https://x.com.br"])
        assert tipo == "proprio" and url == "https://x.com.br"

    def test_paginas_de_link_viram_agregador_no_e4(self):
        # Sem isso a auditoria trataria o linktree como site da empresa.
        assert eh_agregador("https://instabio.cc/ilhagastro") == "instabio.cc"
        assert eh_agregador("https://bit.ly/abc") == "bit.ly"


class TestEstacionado:
    def test_placeweb_virou_pagina_de_venda(self):
        # Texto real de *.placeweb.site em 14/10/2026.
        html = ("<title>placeweb.site</title><h1>placeweb.site</h1><p>Find information, "
                "resources and relevant links for placeweb.site. This domain may be for sale.</p>")
        assert ov.eh_estacionado(html)

    def test_site_normal_nao_e_estacionado(self):
        assert not ov.eh_estacionado("<h1>Oficina do Zé</h1><p>Venda de peças e serviços</p>")


class TestCategorias:
    def test_igreja_e_orgao_publico_nao_sao_lead(self):
        assert not ov.eh_negocio("christian_place_of_worship", None)
        assert not ov.eh_negocio("government_office", None)
        assert not ov.eh_negocio("place_of_learning", "public_school")
        assert not ov.eh_negocio(None, None)
        assert ov.eh_negocio("dental_clinic", "dental_clinic")

    def test_grupos(self):
        assert ov.grupo("dental_clinic") == "Odontologia"
        assert ov.grupo("personal_or_beauty_service") == "Beleza e estética"
        assert ov.grupo("algo_novo") == ov.OUTROS

    def test_pizza_vai_para_pizzaria_quando_pedida(self):
        ramos = [resolver("restaurante"), resolver("pizzaria")]
        assert ov.ramo_de("restaurant", "pizza_restaurant", ramos) == "pizzaria"
        assert ov.ramo_de("restaurant", "brazilian_restaurant", ramos) == "restaurante"
        assert ov.ramo_de("restaurant", "pizza_restaurant", [resolver("restaurante")]) == "restaurante"

    def test_otica_nao_vira_loja_de_roupa(self):
        ramos = [resolver("loja_roupas"), resolver("otica")]
        assert ov.ramo_de("fashion_and_apparel_store", "eyewear_store", ramos) == "otica"
        assert ov.ramo_de("fashion_and_apparel_store", "eyewear_store",
                          [resolver("loja_roupas")]) is None


class TestEmpresa:
    def test_formato_igual_ao_das_outras_fontes(self):
        e = ov.para_empresa(linha(websites=["https://instagram.com/oficinadoze"]),
                            "Campos dos Goytacazes", "RJ", "oficina")
        assert e["fonte"] == "overture" and e["fonte_id"] == "08f-x"
        assert e["telefone"] == "+5522998887766" and e["telefone_origem"] == "overture"
        assert e["instagram"] == "https://instagram.com/oficinadoze"
        assert e["fonte_payload"]["site_tipo"] == "perfil"
        assert e["ramo"] == "oficina"

    def test_celular_tem_preferencia(self):
        e = ov.para_empresa(linha(phones=["+552227331234", "+5522998887766"]), "C", "RJ")
        assert e["telefone"] == "+5522998887766"

    def test_telefone_antigo_ganha_nono_digito(self):
        e = ov.para_empresa(linha(phones=["(22) 9887-6543"]), "C", "RJ")
        assert e["telefone"] == "+5522998876543"

    def test_rede_nacional_e_igreja_ficam_de_fora(self):
        assert ov.para_empresa(linha(nome="Burger King", categoria="restaurant",
                                     websites=["http://www.burgerking.com.br/"]), "C", "RJ") is None
        assert ov.para_empresa(linha(nome="Posto Central",
                                     websites=["https://www.ipiranga.com.br/posto"]), "C", "RJ") is None
        assert ov.para_empresa(linha(categoria="christian_place_of_worship"), "C", "RJ") is None

    def test_coletar_filtra_cidade_e_ramo(self, monkeypatch):
        from prospectai.sources import overpass

        monkeypatch.setattr(overpass, "geocodificar",
                            lambda c, u: overpass.Local("Campos", -21.75, -41.32, None))
        monkeypatch.setattr(ov, "buscar_caixa", lambda *a, **k: [
            linha(id="1"),
            linha(id="2", cidade="Macaé"),
            linha(id="3", categoria="dental_clinic", taxonomia="dental_clinic"),
            linha(id="4", cidade="CAMPOS DOS GOYTACAZES"),
        ])
        r = ov.coletar("Campos dos Goytacazes", "RJ", [resolver("oficina")])
        assert [e["fonte_id"] for e in r] == ["1", "4"]
        com_raio = ov.coletar("Campos dos Goytacazes", "RJ", [resolver("oficina")], raio_km=30)
        assert {e["fonte_id"] for e in com_raio} == {"1", "2", "4"}


class TestPainel:
    def test_painel_classifica_e_testa_so_sites(self, monkeypatch):
        monkeypatch.setattr(ov, "versao_atual", lambda: "2026-09-23.1")
        monkeypatch.setattr(ov, "buscar_caixa", lambda *a, **k: [
            linha(id="1", websites=["https://zeoficina.com.br"]),
            linha(id="2", nome="Padaria Sol", websites=["https://instagram.com/sol"],
                  categoria="casual_eatery", taxonomia="bakery", cidade="MACAE"),
            linha(id="3", nome="Igreja", categoria="christian_place_of_worship"),
            linha(id="4", nome="Bar Fechado", status="permanently_closed"),
            linha(id="5", nome="Salão Bela", categoria="personal_or_beauty_service",
                  websites=["https://bela.placeweb.site"], cidade="Macaé", phones=[]),
        ])
        testados = []

        def falso(urls, **kw):
            testados.extend(urls)
            return {u: "fora_do_ar" for u in urls}

        monkeypatch.setattr(ov, "verificar_sites", falso)
        p = ov.montar_painel()
        nomes = {i["nome"]: i for i in p["itens"]}
        assert set(nomes) == {"Oficina do Zé", "Padaria Sol", "Salão Bela"}
        assert sorted(testados) == ["https://bela.placeweb.site", "https://zeoficina.com.br"]
        assert nomes["Padaria Sol"]["site_tipo"] == "perfil" and nomes["Padaria Sol"]["site"] is None
        assert nomes["Salão Bela"]["site_status"] == "fora_do_ar"
        assert nomes["Salão Bela"]["telefone"] is None and not nomes["Salão Bela"]["celular"]
        # "MACAE" e "Macaé" viram a mesma cidade, com a grafia acentuada
        assert nomes["Padaria Sol"]["cidade"] == nomes["Salão Bela"]["cidade"] == "Macaé"
        assert "CDLA-Permissive-2.0" in p["atribuicao"]
