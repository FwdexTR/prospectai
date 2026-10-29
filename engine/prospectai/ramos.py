"""Catalogo de ramos.

Une tres coisas que sempre andam juntas na cadeia:
  - como achar o ramo na fonte de dados (tags de OSM / tipos do Places)
  - qual oferta faz sentido para ele            (E9, tabela CNAE -> oferta)
  - qual gancho de conversa funciona            (E9, contexto da mensagem)

Tabela fixa de proposito: isso e conhecimento de negocio, nao trabalho de LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Ramo:
    chave: str
    rotulo: str
    osm: tuple[str, ...]              # filtros "chave=valor" do OpenStreetMap
    places: tuple[str, ...]           # tipos da Places API (quando houver chave)
    oferta: str                       # o que voce vende para esse ramo
    gancho: str                       # a dor tipica, em uma frase
    ticket: tuple[int, int]           # faixa sugerida em R$
    peso_cnae: float = 0.0            # bonus no eixo dinheiro (ticket alto)
    extras: tuple[str, ...] = field(default_factory=tuple)
    # Prefixos de CNAE -- a chave para usar a Receita Federal como fonte.
    # Prefixo e nao codigo exato: "5611" cobre restaurante, lanchonete e bar.
    cnaes: tuple[str, ...] = ()


CATALOGO: dict[str, Ramo] = {
    "restaurante": Ramo(
        "restaurante", "Restaurantes e lanchonetes",
        ("amenity=restaurant", "amenity=fast_food", "amenity=cafe", "amenity=ice_cream"),
        ("restaurant", "meal_takeaway", "cafe"),
        "Site + cardápio online + pedido pelo WhatsApp",
        "paga 12% a 27% de comissão para o aplicativo e não tem canal próprio",
        (1500, 4000), 0.0, ("cardapio", "reservas", "delivery"),
        cnaes=("5611", "5612"),
    ),
    "pizzaria": Ramo(
        "pizzaria", "Pizzarias",
        ("cuisine=pizza",), ("restaurant",),
        "Site + cardápio + pedido direto",
        "depende de aplicativo para receber pedido",
        (1500, 4000), 0.0, ("cardapio", "delivery"),
        cnaes=("5611",),
    ),
    "clinica_odonto": Ramo(
        "clinica_odonto", "Clínicas odontológicas",
        ("amenity=dentist", "healthcare=dentist"), ("dentist",),
        "Site + agendamento online + área do paciente",
        "agenda por telefone e perde paciente que liga fora do horário",
        (2500, 6000), 10.0, ("agendamento",),
        cnaes=("8630",),
    ),
    "clinica_medica": Ramo(
        "clinica_medica", "Clínicas e consultórios médicos",
        ("amenity=clinic", "amenity=doctors", "healthcare=centre"), ("doctor",),
        "Site + agendamento online + convênios",
        "telefone ocupado e recepção sobrecarregada",
        (3000, 8000), 10.0, ("agendamento",),
        cnaes=("8610", "8630", "8640", "8650"),
    ),
    "veterinaria": Ramo(
        "veterinaria", "Clínicas veterinárias e pet shops",
        ("amenity=veterinary", "shop=pet", "shop=pet_grooming"), ("veterinary_care",),
        "Site + agendamento de banho e tosa + catálogo",
        "agendamento de banho e tosa todo no WhatsApp manual",
        (2000, 5000), 10.0, ("agendamento",),
        cnaes=("7500", "4789004"),
    ),
    "salao": Ramo(
        "salao", "Salões, barbearias e estética",
        ("shop=hairdresser", "shop=beauty", "shop=massage", "leisure=spa"),
        ("hair_care", "beauty_salon"),
        "Site + agenda online + galeria de trabalhos",
        "agenda no caderno e cliente marca pelo Direct",
        (1500, 4000), 5.0, ("agendamento", "galeria"),
        cnaes=("9602",),
    ),
    "academia": Ramo(
        "academia", "Academias e estúdios",
        ("leisure=fitness_centre", "leisure=sports_centre"), ("gym",),
        "Site + planos + matrícula online",
        "matrícula só presencial",
        (2000, 5000), 5.0, ("planos",),
        cnaes=("9313",),
    ),
    "oficina": Ramo(
        "oficina", "Oficinas mecânicas e auto centers",
        ("shop=car_repair", "shop=tyres", "shop=car_parts"), ("car_repair",),
        "Site + catálogo de serviços + orçamento por WhatsApp",
        "orçamento só por telefone, sem histórico",
        (1500, 4000), 0.0, ("orcamento",),
        cnaes=("4520", "4530", "4543"),
    ),
    "imobiliaria": Ramo(
        "imobiliaria", "Imobiliárias",
        ("office=estate_agent",), ("real_estate_agency",),
        "Site + catálogo de imóveis com filtro + captação de lead",
        "imóveis divulgados só no Instagram, sem filtro nem busca",
        (4000, 12000), 10.0, ("catalogo", "filtros"),
        cnaes=("6821", "6822"),
    ),
    "hotel": Ramo(
        "hotel", "Hotéis e pousadas",
        ("tourism=hotel", "tourism=guest_house", "tourism=motel", "tourism=hostel"),
        ("lodging",),
        "Site + reserva direta + galeria",
        "paga comissão de OTA por reserva que poderia ser direta",
        (3000, 9000), 10.0, ("reservas",),
        cnaes=("5510", "5590"),
    ),
    "advocacia": Ramo(
        "advocacia", "Escritórios de advocacia",
        ("office=lawyer",), ("lawyer",),
        "Site institucional + áreas de atuação + SEO local",
        "credibilidade: cliente pesquisa o nome e não acha nada",
        (2500, 7000), 10.0, ("seo",),
        cnaes=("6911",),
    ),
    "contabilidade": Ramo(
        "contabilidade", "Escritórios de contabilidade",
        ("office=accountant", "office=tax_advisor"), ("accounting",),
        "Site + captação de lead + área do cliente",
        "prospecção 100% por indicação",
        (2500, 7000), 10.0, ("area_cliente",),
        cnaes=("6920",),
    ),
    "otica": Ramo(
        "otica", "Óticas",
        ("shop=optician",), ("store",),
        "Site + catálogo de armações + agendamento de exame",
        "catálogo só na vitrine física",
        (2000, 5000), 5.0, ("catalogo",),
        cnaes=("4774",),
    ),
    "farmacia": Ramo(
        "farmacia", "Farmácias e drogarias",
        ("amenity=pharmacy", "healthcare=pharmacy"), ("pharmacy",),
        "Site + pedido por WhatsApp + delivery",
        "pedido por telefone, sem catálogo",
        (2000, 5000), 0.0, ("catalogo", "delivery"),
        cnaes=("4771", "4772"),
    ),
    "padaria": Ramo(
        "padaria", "Padarias e confeitarias",
        ("shop=bakery", "shop=confectionery", "shop=pastry"), ("bakery",),
        "Site + encomendas online",
        "encomenda de bolo anotada no papel",
        (1200, 3000), 0.0, ("encomendas",),
        cnaes=("4721", "1091"),
    ),
    "loja_roupas": Ramo(
        "loja_roupas", "Lojas de roupas e calçados",
        ("shop=clothes", "shop=shoes", "shop=boutique", "shop=fashion_accessories"),
        ("clothing_store",),
        "Site + catálogo + venda por WhatsApp",
        "vende por Direct e perde pedido no volume",
        (2000, 6000), 0.0, ("catalogo",),
        cnaes=("4781", "4782", "4783"),
    ),
    "materiais_construcao": Ramo(
        "materiais_construcao", "Materiais de construção",
        ("shop=doityourself", "shop=hardware", "shop=trade", "shop=paint"),
        ("hardware_store",),
        "Site + catálogo + orçamento",
        "orçamento por telefone e lista no papel",
        (2500, 7000), 5.0, ("catalogo", "orcamento"),
        cnaes=("4744", "4741", "4742", "4743"),
    ),
    "construtora": Ramo(
        "construtora", "Construtoras e arquitetura",
        ("office=architect", "craft=builder", "office=engineer"), ("general_contractor",),
        "Portfólio visual + captação de lead",
        "portfólio espalhado no Instagram, sem página própria",
        (3000, 9000), 10.0, ("portfolio",),
        cnaes=("4120", "7111", "7112"),
    ),
    "escola": Ramo(
        "escola", "Escolas e cursos livres",
        ("amenity=language_school", "amenity=driving_school", "amenity=college",
         "office=educational_institution"),
        ("school",),
        "Site + matrícula online + página de cursos",
        "matrícula presencial e divulgação só por panfleto",
        (2500, 7000), 10.0, ("matricula",),
        cnaes=("8513", "8520", "8531", "8599"),
    ),
}


def resolver(entrada: str) -> Ramo | None:
    """Aceita a chave exata ou um pedaco do rotulo: 'restaurante', 'odonto', 'otica'.

    Compara sem acento: os rotulos sao acentuados (vao para o cliente), mas as
    chaves nao -- 'otica' precisa casar com 'Óticas'.
    """
    from .normalize import sem_acento

    alvo = sem_acento(entrada.strip().lower()).replace(" ", "_")
    if alvo in CATALOGO:
        return CATALOGO[alvo]
    for ramo in CATALOGO.values():
        if alvo in ramo.chave or alvo in sem_acento(ramo.rotulo.lower()):
            return ramo
    return None


def listar() -> list[Ramo]:
    return list(CATALOGO.values())
