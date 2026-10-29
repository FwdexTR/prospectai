"""Testes de aceitacao -- T1..T6 da spec, mais as invariantes.

Cada teste aqui existe porque o bug correspondente ACONTECEU durante a
construcao. Nenhum e hipotetico:

  - Alaska (bar em Campos) casou com alaska.org, site do estado americano
  - Mister Motel casou com um site holandes
  - lead marcado SEM_SITE recebeu sinais medidos num dominio de terceiro
  - telefone de outra empresa entrou como contato do lead
  - DDD 30, que nao existe, passou na validacao
  - `con.total_changes` nunca zerava e o guarda de duplicidade nao pegava nada
"""

from __future__ import annotations

import pytest

from prospectai import db, outreach, score
from prospectai.audit import Auditoria
from prospectai.classify import INTERESSE, NEGATIVO, OPT_OUT, classificar
from prospectai.discover import (Descoberta, eh_agregador, eh_rede_nacional,
                                 provar_posse, url_plausivel)
from prospectai.messages import Remetente, gerar, validar
from prospectai.normalize import (eh_movel, normalizar_nome, normalizar_telefone,
                                  token_set_ratio)
from prospectai.signals import Sinal, sinais_dor


# ==========================================================================
# T-normalize
# ==========================================================================
class TestNormalizacao:
    def test_remove_sufixo_societario(self):
        assert normalizar_nome("RESTAURANTE SABOR & CIA LTDA - ME") == "restaurante sabor"

    def test_filial_nao_quebra_similaridade(self):
        assert token_set_ratio("Ótica São Jorge", "Ótica São Jorge Centro") == 1.0

    def test_nomes_diferentes_nao_casam(self):
        assert token_set_ratio("Bar do Zé", "Padaria Aliança") == 0.0

    @pytest.mark.parametrize("bruto,esperado", [
        ("(22) 99876-5432", "+5522998765432"),
        ("+55 22 99876-5432", "+5522998765432"),
        ("2233334444", "+552233334444"),
    ])
    def test_telefones_validos(self, bruto, esperado):
        assert normalizar_telefone(bruto) == esperado

    def test_ddd_inexistente_e_rejeitado(self):
        # DDD 30 nao existe. Passava na faixa "11 a 99" e virava lead
        # inalcancavel na fila.
        assert normalizar_telefone("+5530976793116") is None
        assert normalizar_telefone("(20) 99999-9999") is None

    def test_celular_precisa_do_nono_digito(self):
        assert normalizar_telefone("22888765432") is None      # 11 digitos sem o 9
        assert eh_movel("+5522998765432") is True
        assert eh_movel("+552233334444") is False              # fixo


# ==========================================================================
# T3 -- descoberta de site. O teste mais importante do sistema.
# ==========================================================================
class TestProvaDePosse:
    def _aud(self, **kw) -> Auditoria:
        base = dict(site_url="https://exemplo.com.br", http_status=200,
                    titulo="", texto_norm="", digitos_pagina="", telefones_no_site=[])
        base.update(kw)
        return Auditoria(**base)

    def test_alaska_nao_casa_com_site_do_estado_americano(self):
        """O caso real: bar 'Alaska' em Campos x alaska.org.

        Nome no titulo + nome no dominio pareciam duas provas, mas sao a mesma
        coincidencia. Sem prova independente, tem que ser recusado.
        """
        aud = self._aud(site_url="https://www.alaska.org/", titulo="Alaska",
                        texto_norm="travel alaska tourism official state guide")
        provas, independente = provar_posse(aud, "Alaska", cidade="Campos dos Goytacazes")
        assert independente is False
        assert len(provas) <= 1

    def test_site_holandes_nao_casa_com_motel_de_campos(self):
        aud = self._aud(site_url="https://mistermotel.nl/", titulo="Mister Motel",
                        texto_norm="welkom bij mister motel amsterdam")
        _, independente = provar_posse(aud, "Mister Motel", cidade="Campos dos Goytacazes")
        assert independente is False

    def test_telefone_no_site_e_prova_independente(self):
        aud = self._aud(titulo="Academia Slim Fit", telefones_no_site=["22999990001"])
        provas, independente = provar_posse(
            aud, "Academia Slim Fit", telefone="+5522999990001"
        )
        assert independente is True
        assert "telefone" in provas

    def test_cidade_no_texto_e_prova_independente(self):
        # A normalizacao de cidade removia stopwords ("campos dos goytacazes"
        # virava "campos goytacazes") e a comparacao por substring nunca casava.
        aud = self._aud(titulo="Empadiça",
                        texto_norm="empadica salgados em campos dos goytacazes rj")
        provas, independente = provar_posse(
            aud, "Empadiça", cidade="Campos dos Goytacazes"
        )
        assert independente is True
        assert "cidade" in provas

    def test_cidade_parcial_nao_conta(self):
        """So 'campos' nao basta -- casaria com qualquer pagina sobre campo."""
        aud = self._aud(titulo="Alaska", texto_norm="campos de futebol e lazer")
        provas, _ = provar_posse(aud, "Alaska", cidade="Campos dos Goytacazes")
        assert "cidade" not in provas

    def test_cnpj_no_texto_e_prova(self):
        aud = self._aud(titulo="Padaria X", digitos_pagina="12345678000199")
        provas, independente = provar_posse(aud, "Padaria X", cnpj="12.345.678/0001-99")
        assert "cnpj" in provas and independente


class TestClassificacaoDeDominio:
    @pytest.mark.parametrize("url,esperado", [
        ("https://instagram.com/pizzaria", "instagram.com"),
        ("https://linktr.ee/salao", "linktr.ee"),
        ("https://www.ifood.com.br/loja/x", "ifood.com.br"),
        ("https://padariaalianca.com.br", None),
    ])
    def test_agregadores(self, url, esperado):
        assert eh_agregador(url) == esperado

    def test_redes_nacionais_sao_filtradas(self):
        assert eh_rede_nacional("McDonald's") is True
        assert eh_rede_nacional("Spoleto Campos") is True
        assert eh_rede_nacional("Restaurante Tremendão") is False

    def test_url_sem_tld_e_descartada(self):
        # O OSM traz coisas como "spetaopelinca", que nunca resolvem.
        assert url_plausivel("spetaopelinca") is None
        assert url_plausivel("https://ospetao.com.br") == "https://ospetao.com.br"


# ==========================================================================
# I1 -- o portao: dominio nao aceito nao gera sinal derivado
# ==========================================================================
class TestPortaoDaInvarianteI1:
    def test_dominio_suspeito_nao_produz_sinais_do_site(self):
        """Bug real: lead saia como 'SEM_SITE + NAO_RESPONSIVO + TITULO_GENERICO',
        com os dois ultimos medidos no site de outra empresa."""
        aud = Auditoria(site_url="https://peixe.com", http_status=200,
                        tem_viewport=False, titulo_generico=True, tem_whatsapp=False)
        desc = Descoberta(url="https://peixe.com", como="suspeito", confianca=1)

        tipos = {s.tipo for s in sinais_dor(aud, desc)}
        assert tipos == {"SEM_SITE"}

    def test_dominio_aceito_produz_sinais_normalmente(self):
        aud = Auditoria(site_url="https://real.com.br", http_status=200,
                        tem_viewport=False, tem_whatsapp=False, tem_analytics=False,
                        tem_pixel=False)
        desc = Descoberta(url="https://real.com.br", como="declarado", confianca=3)

        tipos = {s.tipo for s in sinais_dor(aud, desc)}
        assert "NAO_RESPONSIVO" in tipos
        assert "SEM_SITE" not in tipos

    def test_perfil_social_vira_sinal_proprio(self):
        desc = Descoberta(como="nao_encontrado", confianca=0, agregador="instagram.com")
        tipos = {s.tipo for s in sinais_dor(None, desc)}
        assert tipos == {"SITE_E_PERFIL"}

    def test_campo_nao_medido_nao_vira_sinal(self):
        """None significa 'nao consegui verificar', nunca 'nao tem'."""
        aud = Auditoria(site_url="https://x.com.br", http_status=200,
                        tem_whatsapp=None, tem_viewport=None)
        desc = Descoberta(url="https://x.com.br", como="declarado", confianca=2)
        tipos = {s.tipo for s in sinais_dor(aud, desc)}
        assert "SEM_WHATSAPP" not in tipos
        assert "NAO_RESPONSIVO" not in tipos


# ==========================================================================
# T4 -- score
# ==========================================================================
class TestScore:
    def test_dinheiro_zero_zera_o_total(self):
        """O ponto do produto: dor maxima sem capacidade de pagar = 0.
        Numa soma ponderada este lead lideraria a lista."""
        sinais = [Sinal("SEM_SITE", "dor", 40), Sinal("LENTO", "dor", 25)]
        r = score.calcular(sinais, dinheiro_medido=True)
        assert r.total == 0
        assert r.faixa == "D"

    def test_restaurante_com_muitas_avaliacoes_supera_mei(self):
        forte = score.calcular([
            Sinal("SEM_SITE", "dor", 40),
            Sinal("VOLUME_REVIEWS", "dinheiro", 40), Sinal("NAO_MEI", "dinheiro", 15),
            Sinal("REVIEWS_RECENTES", "dinheiro", 15),
        ], dinheiro_medido=True)
        fraco = score.calcular([
            Sinal("SEM_SITE", "dor", 40),
            Sinal("VOLUME_REVIEWS", "dinheiro", 6),
        ], dinheiro_medido=True)
        assert forte.total > fraco.total * 2

    def test_sem_eixo_dinheiro_nao_ha_faixa_a(self):
        """Faixa A dispara protótipo gratis (4h de trabalho). Nao entra nela
        sem saber se a empresa pode pagar."""
        r = score.calcular([Sinal("SEM_SITE", "dor", 40),
                            Sinal("SITE_MORTO", "dor", 40)], dinheiro_medido=False)
        assert r.faixa != "A"
        assert r.provisorio is True

    def test_timing_multiplica_e_tem_teto(self):
        base = [Sinal("SEM_SITE", "dor", 40), Sinal("VOLUME_REVIEWS", "dinheiro", 40)]
        sem = score.calcular(base, True)
        com = score.calcular(base + [Sinal("RECEM_ABERTA", "timing", 1.4)], True)
        estourado = score.calcular(base + [Sinal("X", "timing", 9.0)], True)
        assert com.total > sem.total
        assert estourado.timing == score.TETO_TIMING


# ==========================================================================
# T5 -- mensagens
# ==========================================================================
class TestValidadorDeMensagem:
    def test_jargao_de_vendedor_reprova(self):
        erros = validar("Oi, aqui e o Joao. Gostaria de apresentar uma parceria. Topa?",
                        {"x": 1}, 1, [])
        assert any(e.startswith("R3") for e in erros)

    def test_promessa_de_resultado_reprova(self):
        erros = validar("Aqui e o Joao, seu site vai dobrar as vendas. Posso mandar? "
                        "so falar", {"psi": 30}, 1, [])
        assert any(e.startswith("R4") for e in erros)

    def test_sem_identificacao_reprova(self):
        erros = validar("Seu site está lento. Quer ver?", {"psi": 30}, 1, [])
        assert any(e.startswith("R7") for e in erros)

    def test_incoerencia_com_sem_site_reprova(self):
        erros = validar("Aqui e o Joao, o site de voces esta lento. Posso mandar? "
                        "so falar", {"a": 1}, 1, ["SEM_SITE"])
        assert any(e.startswith("R9") for e in erros)

    def test_toque_1_exige_opt_out(self):
        erros = validar("Oi, aqui e o Joao, achei um problema. Posso mandar?",
                        {"a": 1}, 1, [])
        assert any(e.startswith("R10") for e in erros)

    def test_texto_acentuado_passa_nas_regras(self):
        """As regras sao escritas sem acento; o texto enviado E acentuado."""
        erros = validar(
            "Oi! Aqui é o Fulano. O site levou 8 segundos para abrir. "
            "Posso te mandar a análise? Se não quiser, é só falar que eu não mando mais.",
            {"tempo": 8}, 1, ["RESPOSTA_LENTA"],
        )
        assert erros == []


class TestGeracao:
    def _remetente(self):
        return Remetente(nome="Fulano", cidade="Campos dos Goytacazes")

    def test_sequencia_gera_tres_toques_validos(self):
        sinais = [Sinal("SEM_SITE", "dor", 40, evidencia={"candidatos_testados": ["a", "b"]})]
        for toque in (1, 2, 3):
            m = gerar("Barbearia do Zé", "salao", sinais, self._remetente(),
                      "https://x/a/tok", toque)
            assert m.validador_ok, (toque, m.validador_erros)
            assert len(m.texto) <= 480

    def test_toque_2_muda_de_angulo_quando_ha_um_sintoma_so(self):
        """Repetir a mesma frase no follow-up soa como cobranca."""
        sinais = [Sinal("SEM_SITE", "dor", 40, evidencia={})]
        r = self._remetente()
        t1 = gerar("Salão X", "salao", sinais, r, None, 1).texto
        t2 = gerar("Salão X", "salao", sinais, r, None, 2).texto
        assert t1 != t2
        assert "agenda no caderno" in t2          # veio do gancho do ramo

    def test_mensagem_cita_o_numero_medido(self):
        sinais = [Sinal("LENTO", "dor", 25, evidencia={"psi_mobile": 34})]
        m = gerar("Padaria Y", "padaria", sinais, self._remetente(), None, 1)
        assert "34" in m.texto and m.validador_ok

    def test_sem_sintoma_nao_gera_mensagem(self):
        m = gerar("Empresa Z", None, [], self._remetente(), None, 1)
        assert m.validador_ok is False
        assert m.texto == ""


# ==========================================================================
# E11 -- classificacao de resposta
# ==========================================================================
class TestClassificacao:
    @pytest.mark.parametrize("texto", [
        "não tenho interesse", "para de mandar mensagem", "me remove daí",
        "NÃO QUERO", "vou bloquear",
    ])
    def test_opt_out_tem_precedencia(self, texto):
        assert classificar(texto).classe == OPT_OUT

    def test_interesse(self):
        assert classificar("quanto custa um site desses?").classe == INTERESSE
        assert classificar("manda aí que eu vejo").classe == INTERESSE

    def test_negativo_nao_e_opt_out(self):
        """'Já tenho' e recusa, nao pedido para parar: pode receber follow-up."""
        assert classificar("obrigado, já temos agência").classe == NEGATIVO

    def test_resposta_automatica_nao_conta(self):
        c = classificar("Mensagem automática: nosso horário de atendimento é 8h às 18h")
        assert c.classe == "AUTOMATICO"
        from prospectai.classify import conta_como_resposta
        assert conta_como_resposta(c) is False


# ==========================================================================
# T6 -- opt-out ponta a ponta. O unico erro com consequencia fora do PC.
# ==========================================================================
@pytest.fixture
def banco(tmp_path):
    con = db.conectar(tmp_path / "teste.db")
    db.inicializar(con)
    eid = db.inserir(con, "empresa", {
        "nome": "Padaria Teste", "nome_norm": "padaria teste",
        "ramo": "padaria", "cidade": "Campos dos Goytacazes", "uf": "RJ",
        "fonte": "manual", "fonte_id": "t1",
    })
    cid = db.inserir(con, "contato", {
        "empresa_id": eid, "canal": "whatsapp", "valor": "+5522999998888",
        "eh_movel": 1,
    })
    con.execute("INSERT INTO lead (empresa_id, estado) VALUES (?, 'SELECIONADO')", (eid,))
    for toque in (1, 2, 3):
        db.inserir(con, "mensagem", {
            "empresa_id": eid, "contato_id": cid, "template_id": f"t{toque}_x",
            "toque": toque, "texto": f"mensagem {toque}", "validador_ok": 1,
            "estado": "AGENDADA",
        })
    con.commit()
    yield con, eid
    con.close()

class TestOptOutPontaAPonta:
    def test_opt_out_cancela_tudo_e_e_terminal(self, banco):
        con, eid = banco
        # So o toque 1 aparece: os toques 2 e 3 so entram na fila depois de
        # agendados, o que acontece quando o anterior e enviado.
        assert len(outreach.fila(con)) == 1

        cl = outreach.registrar_resposta(con, eid, "não tenho interesse, obrigado")
        assert cl.classe == OPT_OUT

        # some da fila, contato marcado, toques cancelados, lead encerrado
        assert outreach.fila(con) == []
        assert db.um(con, "SELECT opt_out FROM contato WHERE empresa_id=?", (eid,))["opt_out"] == 1
        pendentes = db.um(con, "SELECT COUNT(*) AS n FROM mensagem "
                               "WHERE empresa_id=? AND estado='AGENDADA'", (eid,))
        assert pendentes["n"] == 0
        assert db.um(con, "SELECT estado FROM lead WHERE empresa_id=?", (eid,))["estado"] == "OPT_OUT"

    def test_resposta_humana_tira_da_automacao(self, banco):
        con, eid = banco
        outreach.registrar_resposta(con, eid, "quanto custa?")
        assert db.um(con, "SELECT estado FROM lead WHERE empresa_id=?", (eid,))["estado"] == "EM_CONVERSA"
        assert outreach.fila(con) == []

    def test_envio_duplicado_e_impossivel(self, banco):
        """I3. `con.total_changes` e acumulado da conexao e nunca zerava --
        o guarda so funciona com o rowcount do cursor."""
        con, eid = banco
        mid = db.um(con, "SELECT id FROM mensagem WHERE toque=1 AND empresa_id=?",
                    (eid,))["id"]
        assert outreach.marcar_enviada(con, mid, forcar=True).ok is True
        segunda = outreach.marcar_enviada(con, mid, forcar=True)
        assert segunda.ok is False

    def test_kill_switch_bloqueia(self, banco):
        con, eid = banco
        mid = db.um(con, "SELECT id FROM mensagem WHERE toque=1 AND empresa_id=?",
                    (eid,))["id"]
        db.definir_estado(con, "envio_habilitado", "false")
        p = outreach.marcar_enviada(con, mid, forcar=False)
        assert p.ok is False and "kill switch" in (p.motivo or "")

    def test_envio_agenda_o_proximo_toque(self, banco):
        con, eid = banco
        mid = db.um(con, "SELECT id FROM mensagem WHERE toque=1 AND empresa_id=?",
                    (eid,))["id"]
        outreach.marcar_enviada(con, mid, forcar=True)
        t2 = db.um(con, "SELECT agendada_para FROM mensagem WHERE toque=2 AND empresa_id=?",
                   (eid,))
        assert t2["agendada_para"] is not None

    def test_telefone_fixo_nao_entra_na_fila(self, banco):
        con, _ = banco
        eid = db.inserir(con, "empresa", {
            "nome": "Fixo Ltda", "nome_norm": "fixo", "fonte": "manual", "fonte_id": "t2",
        })
        cid = db.inserir(con, "contato", {
            "empresa_id": eid, "canal": "whatsapp", "valor": "+552233334444",
            "eh_movel": 0,
        })
        db.inserir(con, "mensagem", {
            "empresa_id": eid, "contato_id": cid, "template_id": "t1_x", "toque": 1,
            "texto": "oi", "validador_ok": 1, "estado": "AGENDADA",
        })
        con.commit()
        assert all(i["empresa_id"] != eid for i in outreach.fila(con))
