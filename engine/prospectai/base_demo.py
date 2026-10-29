"""Base de demonstracao: empresas FICTICIAS no layout exato da Receita.

Serve para usar a interface antes de baixar os 7,8 GB da base real. Os ZIPs
sao gerados no mesmo formato da RFB e passam pelo importador de verdade -- a
demonstracao exercita o mesmo caminho que a base real vai usar.

O que torna isto seguro:
  - fonte='demo' em toda linha; a interface marca e a fila de envio BLOQUEIA
    qualquer contato com empresa de demonstracao (telefone pode coincidir com
    o de alguem de verdade);
  - e-mails no dominio reservado .example (RFC 2606), que nao entrega nada;
  - razao social de MEI leva um CPF falso, para provar que o importador o
    remove (I7).

Codigos de municipio sao os reais da RFB (conferidos no Municipios.zip de
08/08/2026); descricoes de CNAE sao as subclasses oficiais mais comuns.
"""

from __future__ import annotations

import random
import tempfile
import zipfile
from datetime import date, timedelta
from pathlib import Path

from . import base, base_importar

# (codigo RFB, nome, UF, peso)
MUNICIPIOS = [
    ("5819", "CAMPOS DOS GOYTACAZES", "RJ", 30), ("5847", "MACAE", "RJ", 14),
    ("5899", "SAO JOAO DA BARRA", "RJ", 3), ("5895", "SAO FIDELIS", "RJ", 2),
    ("6007", "QUISSAMA", "RJ", 1.5), ("0772", "CARAPEBUS", "RJ", 1),
    ("5827", "CONCEICAO DE MACABU", "RJ", 1), ("0782", "SAO FRANCISCO DE ITABAPOANA", "RJ", 1.5),
    ("2915", "CARDOSO MOREIRA", "RJ", 0.7), ("5843", "ITAPERUNA", "RJ", 3),
    ("6001", "RIO DE JANEIRO", "RJ", 9), ("5865", "NITEROI", "RJ", 2.5),
    ("2921", "RIO DAS OSTRAS", "RJ", 2), ("5813", "CABO FRIO", "RJ", 2),
    ("7107", "SAO PAULO", "SP", 7), ("6291", "CAMPINAS", "SP", 1.5),
    ("4123", "BELO HORIZONTE", "MG", 3), ("4733", "JUIZ DE FORA", "MG", 1.2),
    ("5705", "VITORIA", "ES", 1.5), ("5623", "CACHOEIRO DE ITAPEMIRIM", "ES", 1),
    ("7535", "CURITIBA", "PR", 2), ("8801", "PORTO ALEGRE", "RS", 1.8),
    ("3849", "SALVADOR", "BA", 2), ("2531", "RECIFE", "PE", 1.5),
    ("1389", "FORTALEZA", "CE", 1.5), ("9701", "BRASILIA", "DF", 2),
    ("9373", "GOIANIA", "GO", 1.2), ("8105", "FLORIANOPOLIS", "SC", 1),
    ("0255", "MANAUS", "AM", 1), ("0427", "BELEM", "PA", 1),
]
DDD = {"5819": "22", "5847": "22", "5899": "22", "5895": "22", "6007": "22",
       "0772": "22", "5827": "22", "0782": "22", "2915": "22", "5843": "22",
       "6001": "21", "5865": "21", "2921": "22", "5813": "22", "7107": "11",
       "6291": "19", "4123": "31", "4733": "32", "5705": "27", "5623": "28",
       "7535": "41", "8801": "51", "3849": "71", "2531": "81", "1389": "85",
       "9701": "61", "9373": "62", "8105": "48", "0255": "92", "0427": "91"}

# (subclasse, descricao, peso, familia de nome)
CNAES = [
    ("4520001", "Serviços de manutenção e reparação mecânica de veículos automotores", 6, "oficina"),
    ("4520005", "Serviços de lavagem, lubrificação e polimento de veículos automotores", 2, "oficina"),
    ("4530703", "Comércio a varejo de peças e acessórios novos para veículos automotores", 3, "pecas"),
    ("4543900", "Manutenção e reparação de motocicletas e motonetas", 2, "oficina"),
    ("5611201", "Restaurantes e similares", 6, "restaurante"),
    ("5611203", "Lanchonetes, casas de chá, de sucos e similares", 5, "restaurante"),
    ("5611204", "Bares e outros estabelecimentos especializados em servir bebidas, sem entretenimento", 3, "bar"),
    ("5620104", "Fornecimento de alimentos preparados preponderantemente para consumo domiciliar", 2, "restaurante"),
    ("4721102", "Padaria e confeitaria com predominância de revenda", 2, "padaria"),
    ("1091102", "Fabricação de produtos de padaria e confeitaria com predominância de produção própria", 2, "padaria"),
    ("4711302", "Comércio varejista de mercadorias em geral, com predominância de produtos alimentícios - supermercados", 2, "mercado"),
    ("4712100", "Comércio varejista de mercadorias em geral, com predominância de produtos alimentícios - minimercados, mercearias e armazéns", 4, "mercado"),
    ("9602501", "Cabeleireiros, manicure e pedicure", 7, "beleza"),
    ("9602502", "Atividades de estética e outros serviços de cuidados com a beleza", 3, "beleza"),
    ("8630504", "Atividade odontológica", 3, "odonto"),
    ("8630503", "Atividade médica ambulatorial restrita a consultas", 2, "clinica"),
    ("8650004", "Atividades de fisioterapia", 1.5, "clinica"),
    ("8650003", "Atividades de psicologia e psicanálise", 1.5, "clinica"),
    ("7500100", "Atividades veterinárias", 1.5, "pet"),
    ("4789004", "Comércio varejista de animais vivos e de artigos e alimentos para animais de estimação", 2, "pet"),
    ("9313100", "Atividades de condicionamento físico", 2, "academia"),
    ("4781400", "Comércio varejista de artigos do vestuário e acessórios", 6, "loja"),
    ("4774100", "Comércio varejista de artigos de óptica", 1, "loja"),
    ("4771701", "Comércio varejista de produtos farmacêuticos, sem manipulação de fórmulas", 2, "farmacia"),
    ("4744099", "Comércio varejista de materiais de construção em geral", 2, "construcao"),
    ("4120400", "Construção de edifícios", 2, "construcao"),
    ("4321500", "Instalação e manutenção elétrica", 2, "servico"),
    ("4322301", "Instalações hidráulicas, sanitárias e de gás", 1, "servico"),
    ("8121400", "Limpeza em prédios e em domicílios", 1.5, "servico"),
    ("6821801", "Corretagem na compra e venda e avaliação de imóveis", 2, "imobiliaria"),
    ("6911701", "Serviços advocatícios", 2.5, "advocacia"),
    ("6920601", "Atividades de contabilidade", 2, "contabilidade"),
    ("7020400", "Atividades de consultoria em gestão empresarial, exceto consultoria técnica específica", 2, "consultoria"),
    ("7112000", "Serviços de engenharia", 1.5, "consultoria"),
    ("6201501", "Desenvolvimento de programas de computador sob encomenda", 1, "ti"),
    ("9511800", "Reparação e manutenção de computadores e de equipamentos periféricos", 1, "ti"),
    ("7319002", "Promoção de vendas", 1, "consultoria"),
    ("8599604", "Treinamento em desenvolvimento profissional e gerencial", 1, "ensino"),
    ("8513900", "Ensino fundamental", 0.8, "ensino"),
    ("5510801", "Hotéis", 0.8, "hotel"),
    ("4930202", "Transporte rodoviário de carga, exceto produtos perigosos e mudanças, intermunicipal, interestadual e internacional", 1.5, "transporte"),
    ("0910600", "Atividades de apoio à extração de petróleo e gás natural", 0.8, "industria"),
    ("3314710", "Manutenção e reparação de máquinas e equipamentos para uso geral não especificados anteriormente", 1, "industria"),
]
FAMILIA_PALAVRA = {
    "oficina": ["Auto Center", "Oficina", "Mecânica", "Auto Peças e Serviços", "Moto Center"],
    "pecas": ["Auto Peças", "Peças", "Distribuidora de Peças"],
    "restaurante": ["Restaurante", "Cantina", "Lanchonete", "Sabor", "Cozinha", "Grill"],
    "bar": ["Bar", "Choperia", "Boteco"],
    "padaria": ["Padaria", "Panificadora", "Confeitaria"],
    "mercado": ["Mercado", "Mercearia", "Supermercado", "Hortifruti"],
    "beleza": ["Studio", "Salão", "Espaço", "Barbearia", "Estética"],
    "odonto": ["Odontologia", "Clínica Odontológica", "Sorriso"],
    "clinica": ["Clínica", "Consultório", "Centro de Saúde"],
    "pet": ["Pet Shop", "Clínica Veterinária", "Agropet"],
    "academia": ["Academia", "Studio Fitness", "CrossFit"],
    "loja": ["Boutique", "Moda", "Ótica", "Store"],
    "farmacia": ["Farmácia", "Drogaria"],
    "construcao": ["Construtora", "Materiais de Construção", "Depósito"],
    "servico": ["Serviços", "Manutenção", "Instalações"],
    "imobiliaria": ["Imóveis", "Imobiliária"],
    "advocacia": ["Advocacia", "Advogados Associados"],
    "contabilidade": ["Contabilidade", "Escritório Contábil"],
    "consultoria": ["Consultoria", "Engenharia", "Gestão"],
    "ti": ["Tecnologia", "Informática", "Sistemas"],
    "ensino": ["Escola", "Instituto", "Centro de Ensino"],
    "hotel": ["Hotel", "Pousada"],
    "transporte": ["Transportes", "Logística"],
    "industria": ["Offshore", "Serviços Industriais", "Equipamentos"],
}
SOBRENOMES = ["Silva", "Santos", "Oliveira", "Souza", "Rodrigues", "Ferreira", "Alves",
              "Pereira", "Lima", "Gomes", "Costa", "Ribeiro", "Martins", "Carvalho",
              "Almeida", "Lopes", "Soares", "Fernandes", "Vieira", "Barbosa", "Rocha",
              "Dias", "Nascimento", "Andrade", "Moreira", "Nunes", "Marques", "Machado",
              "Mendes", "Freitas", "Cardoso", "Ramos", "Teixeira", "Correia", "Pinto"]
PRENOMES = ["Ana", "Maria", "João", "José", "Carlos", "Paulo", "Lucas", "Marcos", "Rafael",
            "Juliana", "Fernanda", "Patrícia", "Bruno", "Thiago", "Camila", "Aline",
            "Rodrigo", "Diego", "Larissa", "Gabriel", "Renata", "Eduardo", "Vanessa"]
LUGARES = ["Centro", "Planície", "Goitacá", "Litoral", "Imperial", "Norte", "Real",
           "Parque", "Jardim", "Pelinca", "Guarus", "Lagoa", "Atlântico", "Serrano",
           "Boa Vista", "Primavera", "Horizonte", "Esperança", "Aliança", "União"]
BAIRROS = ["CENTRO", "PARQUE CALIFORNIA", "PELINCA", "GUARUS", "PARQUE TAMANDARE",
           "JARDIM CARIOCA", "PARQUE LEOPOLDINA", "TURF CLUB", "CAJUEIROS", "IMBETIBA",
           "GLORIA", "CAVALEIROS", "PRAIA CAMPISTA", "VILA DA RAINHA", "PARQUE AURORA"]
RUAS = ["RUA", "AVENIDA", "TRAVESSA", "ESTRADA"]
NOMES_RUA = ["DOS GOYTACAZES", "TREZE DE MAIO", "SETE DE SETEMBRO", "BARAO DA LAGOA DOURADA",
             "ALBERTO TORRES", "PELINCA", "JOSE ALVES DE AZEVEDO", "RUI BARBOSA",
             "TENENTE CORONEL CARDOSO", "QUINZE DE NOVEMBRO", "BENTA PEREIRA",
             "VINTE E OITO DE MARCO", "SALVADOR CORREA", "DO OUVIDOR", "BEIRA RIO"]
NATUREZAS = [("2062", "Sociedade Empresária Limitada"), ("2135", "Empresário (Individual)"),
             ("2240", "Sociedade Simples Limitada"), ("2054", "Sociedade Anônima Fechada"),
             ("2305", "Empresa Individual de Responsabilidade Limitada (de Natureza Empresária)")]
MOTIVOS = [("00", "SEM MOTIVO"), ("01", "EXTINCAO POR ENCERRAMENTO LIQUIDACAO VOLUNTARIA")]
# E-mail de "escritorio contabil" repetido em varias empresas: o problema real
# que o filtro de e-mail proprio resolve.
EMAILS_CONTADOR = ["cadastro@contabilidade-planicie.example", "fiscal@escritorio-norte.example",
                   "societario@contadores-associados.example"]


def _dv_cnpj(doze: str) -> str:
    def dv(digitos: str, pesos: list[int]) -> str:
        r = sum(int(d) * p for d, p in zip(digitos, pesos)) % 11
        return "0" if r < 2 else str(11 - r)
    d1 = dv(doze, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = dv(doze + d1, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return d1 + d2


def _slug(texto: str) -> str:
    from .normalize import sem_acento
    return "".join(ch for ch in sem_acento(texto).lower() if ch.isalnum())[:24]


def _q(v: str) -> str:
    return '"' + v.replace('"', "") + '"'


def gerar(quantidade: int = 60_000, semente: int = 42,
          destino: Path | None = None, progresso=base_importar._nada) -> dict:
    rnd = random.Random(semente)
    destino = destino or base.ARQ_DEMO
    pesos_mun = [m[3] for m in MUNICIPIOS]
    pesos_cnae = [c[2] for c in CNAES]
    hoje = date(2026, 10, 1)               # data fixa: demo reproduzivel

    estab: list[list[str]] = []
    empresas: dict[str, list[str]] = {}
    simples: list[list[str]] = []
    usados: set[str] = set()
    com_filial: list[str] = []

    for i in range(quantidade):
        mun = rnd.choices(MUNICIPIOS, pesos_mun)[0]
        cnae, _, _, familia = rnd.choices(CNAES, pesos_cnae)[0]
        r = rnd.random()
        porte_tipo = "MEI" if r < 0.36 else "ME" if r < 0.76 else "EPP" if r < 0.92 else "DEMAIS"

        # 8% sao filiais de uma empresa ja gerada (mesma raiz de CNPJ).
        if com_filial and rnd.random() < 0.08 and porte_tipo != "MEI":
            basico = rnd.choice(com_filial)         # MEI nao tem filial
            ordem = f"{rnd.randint(2, 40):04d}"
            matriz = "2"
        else:
            basico = f"{rnd.randint(10_000_000, 69_999_999):08d}"
            while basico in usados:
                basico = f"{rnd.randint(10_000_000, 69_999_999):08d}"
            ordem, matriz = "0001", "1"
        if basico + ordem in usados:
            continue
        usados.add(basico)
        usados.add(basico + ordem)
        dv = _dv_cnpj(basico + ordem)

        sobrenome = rnd.choice(SOBRENOMES)
        palavra = rnd.choice(FAMILIA_PALAVRA[familia])
        estilo = rnd.random()
        if estilo < 0.45:
            fantasia = f"{palavra} {sobrenome}"
        elif estilo < 0.8:
            fantasia = f"{palavra} {rnd.choice(LUGARES)}"
        elif estilo < 0.92:
            fantasia = f"{rnd.choice(PRENOMES)} {palavra}"
        else:
            fantasia = ""                       # muita empresa real nao tem fantasia

        if basico not in empresas:
            if porte_tipo == "MEI":
                pessoa = f"{rnd.choice(PRENOMES)} {rnd.choice(SOBRENOMES)} {sobrenome}".upper()
                cpf_falso = "".join(str(rnd.randint(0, 9)) for _ in range(11))
                razao = f"{pessoa} {cpf_falso}"           # o importador tem de remover
                natureza, porte, capital = "2135", "01", rnd.randint(1, 10) * 1000
            else:
                razao = f"{(fantasia or palavra + ' ' + sobrenome).upper()} LTDA"
                natureza = rnd.choice(["2062", "2062", "2062", "2240", "2305", "2054"])
                porte = {"ME": "01", "EPP": "03", "DEMAIS": "05"}[porte_tipo]
                capital = {"ME": rnd.randint(10, 100) * 1000,
                           "EPP": rnd.randint(50, 500) * 1000,
                           "DEMAIS": rnd.randint(200, 5000) * 1000}[porte_tipo]
            empresas[basico] = [basico, razao, natureza, "49", f"{capital},00", porte, ""]
            if porte_tipo != "MEI":
                com_filial.append(basico)
            simples_sn = "S" if porte_tipo in ("MEI", "ME") or (porte_tipo == "EPP" and rnd.random() < .6) else "N"
            simples.append([basico, simples_sn, "20200101", "", "S" if porte_tipo == "MEI" else "N",
                            "20200101" if porte_tipo == "MEI" else "", ""])

        # Abertura: mais empresas recentes, como na base real.
        dias = int(rnd.expovariate(1 / 2600))
        abertura = max(hoje - timedelta(days=dias), date(1975, 1, 1))

        ddd = DDD[mun[0]]
        tel1 = ddd2 = tel2 = ""
        if rnd.random() < 0.82:
            if rnd.random() < 0.55:
                tel1 = f"90000{rnd.randint(0, 9999):04d}"       # celular ficticio
            else:
                tel1 = f"{rnd.choice('2345')}{rnd.randint(0, 9999999):07d}"
        if tel1 and rnd.random() < 0.25:
            ddd2, tel2 = ddd, f"90001{rnd.randint(0, 9999):04d}"

        email = ""
        r = rnd.random()
        if r < 0.15:
            email = rnd.choice(EMAILS_CONTADOR)
        elif r < 0.72:
            email = f"contato@{_slug(fantasia or sobrenome + palavra)}.example"

        parentes = [c[0] for c in CNAES if c[3] == familia and c[0] != cnae]
        secundarios = ",".join(rnd.sample(parentes, k=min(len(parentes), rnd.randint(0, 2))))
        estab.append([
            basico, ordem, dv, matriz, fantasia.upper(), "02", abertura.strftime("%Y%m%d"),
            "00", "", "", abertura.strftime("%Y%m%d"), cnae, secundarios,
            rnd.choice(RUAS), rnd.choice(NOMES_RUA), str(rnd.randint(1, 3000)),
            rnd.choice(["", "", "", "LOJA 1", "SALA 201", "FUNDOS"]),
            rnd.choice(BAIRROS) if mun[2] == "RJ" else "CENTRO",
            f"{rnd.randint(20000, 29999)}{rnd.randint(0, 999):03d}", mun[2], mun[0],
            ddd if tel1 else "", tel1, ddd2, tel2, "", "", email.upper(), "", "",
        ])
        if i % 10_000 == 0:
            progresso("gerando demonstracao", i, quantidade)

    with tempfile.TemporaryDirectory(prefix="prospectai_demo_") as d:
        pasta = Path(d)
        sufixo = "D61001"                       # versao ficticia: 2026-10-01

        def gravar(nome: str, interno: str, linhas: list[list[str]]) -> None:
            texto = "".join(";".join(_q(c) for c in l) + "\n" for l in linhas)
            with zipfile.ZipFile(pasta / nome, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr(f"K3241.K03200Y0.{sufixo}.{interno}", texto.encode("latin-1"))

        partes = base_importar.PARTES
        for p in range(partes):
            gravar(f"Estabelecimentos{p}.zip", "ESTABELE", estab[p::partes])
            gravar(f"Empresas{p}.zip", "EMPRECSV", list(empresas.values())[p::partes])
        gravar("Simples.zip", "SIMPLES.CSV", simples)
        gravar("Cnaes.zip", "CNAECSV", [[c[0], c[1]] for c in CNAES])
        gravar("Municipios.zip", "MUNICCSV", [[m[0], m[1]] for m in MUNICIPIOS])
        gravar("Naturezas.zip", "NATJUCSV", [list(n) for n in NATUREZAS])
        gravar("Motivos.zip", "MOTICSV", [list(m) for m in MOTIVOS])
        return base_importar.importar(pasta, destino, fonte="demo", progresso=progresso)
