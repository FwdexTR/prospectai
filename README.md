# ProspectAI

Encontra empresas por cidade + ramo, **audita tecnicamente o site de cada uma**,
pontua, gera uma página de análise hospedada e escreve as mensagens de abordagem.

O que separa isto de um raspador de listas: nenhuma afirmação sai daqui sem ter
sido medida. Se o sistema diz "seu site leva 8,4s para abrir", ele abriu o site,
cronometrou e guardou a evidência.

```
Empresas da cidade → descoberta do site → auditoria técnica → sinais → score
                  → página de prova → mensagem validada → fila de envio → CRM
```

---

## Instalação

Requer **Python 3.11+** e **Node 20+**.

```bash
cd prospectai
py -m venv .venv
./.venv/Scripts/python.exe -m pip install httpx beautifulsoup4 fastapi "uvicorn[standard]" pytest duckdb
cd web && npm install
```

Copie `.env.example` para `.env` e preencha:

```ini
REMETENTE_NOME=SeuNome
REMETENTE_CIDADE=Campos dos Goytacazes
REMETENTE_WHATSAPP=+5522999999999
BASE_URL=http://localhost:8000

# Opcional, mas ver "Sobre as fontes" abaixo
GOOGLE_PLACES_API_KEY=
PAGESPEED_API_KEY=
```

## Uso

Dois processos:

```bash
cd engine && ../.venv/Scripts/python.exe -m uvicorn prospectai.api:app --port 8000
```

```bash
cd web && npm run dev
```

Abra `http://localhost:3000`, vá em **Nova busca**, escolha cidade e ramo.

Também dá para usar só pela linha de comando:

```bash
cd engine
../.venv/Scripts/python.exe -m prospectai.cli rodar --cidade "Campos dos Goytacazes" --uf RJ --ramo restaurante
../.venv/Scripts/python.exe -m prospectai.cli leads --campanha 1
../.venv/Scripts/python.exe -m prospectai.cli mensagens --empresa 42
../.venv/Scripts/python.exe -m prospectai.cli ramos
```

---

## Telefone e site de graça: Overture Maps

Sem chave do Google, a **Nova busca** usa o Overture Maps (base aberta de lugares de
Meta, Microsoft e outros; licença CDLA-Permissive-2.0). Não precisa de conta nem
de download: o DuckDB lê do bucket público só o pedaço da cidade (~20 s).

| Medido em Campos dos Goytacazes (14/10/2026) | OpenStreetMap | Overture |
|---|---|---|
| Empresas encontradas (oficina + dentista) | poucas dezenas | 635 |
| Com telefone | 16% | 93% |

**Radar Norte Fluminense** (página online, privada):
https://claude.ai/artifact/GKtwq3PxCjseZRAz25ZgLL. São 21.588 negócios com telefone
e situação do site (fora do ar, sem site cadastrado, só Instagram, site gratuito,
site no ar), filtros por cidade, ramo e celular. Para atualizar todo mês:

```bash
cd engine
../.venv/Scripts/python.exe -m prospectai.cli overture painel --saida ../painel/dados.json
```

e peça ao Claude para republicar `painel/index.html` na mesma URL. O teste dos sites
leva ~9 min (5 mil sites); `--sem-verificar` pula essa parte.

"Sem site cadastrado" quer dizer que a página da empresa no Facebook não informa
site. Quem afirma que a empresa não tem site é a auditoria (I1).

---

## Prospecção B2B: base CNPJ, listas, funil e cadências

Módulo inspirado em Apollo.io e Leads2B (estudo em
`Claude mind/10 Projetos/Engenharia Reversa - Apollo e Leads2B.md`). O fluxo é o mesmo
dos dois, sem créditos:

```
BUSCA na base → LISTA → REVELAR no CRM → ENRIQUECER → CADÊNCIA → FILA → FUNIL
```

| Tela | O que faz |
|---|---|
| **Buscar empresas** (`/base`) | Filtros por UF, município, bairro, CNAE (com atalho por ramo), porte, MEI, Simples, capital, abertura e contato. Salva em lista, revela no CRM, exporta CSV |
| **Listas** (`/listas`) | Grupos de CNPJs (de uma busca ou colados). Revelar, enriquecer, inscrever em cadência, exportar |
| **Mercado e ICP** (`/mercado`) | Mapa por UF, cidades, atividades, porte, aberturas por ano, capital. Aba ICP: cole os CNPJs dos seus clientes e receba a busca que acha os parecidos |
| **Funil** (`/crm`) | Kanban por etapa (arrastar ou seletor). Mover para "Em conversa" cancela os toques agendados |
| **Tarefas** (`/tarefas`) | Tarefas com prazo criadas na página do lead |
| **Cadências** (`/cadencias`) | Até 3 passos (WhatsApp ou e-mail) com espera em dias e variáveis `{{nome}}`, `{{cidade}}`… |

### A base de empresas

Fica em `dados/base_cnpj.duckdb` (DuckDB, só leitura pela API), separada do SQLite do CRM.

```bash
cd engine
../.venv/Scripts/python.exe -m prospectai.cli base demo       # 60 mil empresas FICTÍCIAS, em segundos
../.venv/Scripts/python.exe -m prospectai.cli base baixar     # ~7,8 GB da Receita (sem Sócios)
../.venv/Scripts/python.exe -m prospectai.cli base importar   # monta a base (pare a API antes)
../.venv/Scripts/python.exe -m prospectai.cli base status
```

- **Fonte oficial:** o antigo `dadosabertos.rfb.gov.br` não responde mais. Os arquivos estão
  no compartilhamento da RFB `arquivos.receitafederal.gov.br/index.php/s/YggdBLfdninEJX9`
  (link publicado no dados.gov.br), lido por WebDAV. Release mensal.
- **Medido com dado real** (1 das 10 partes, 08/08/2026): 1,17 mi de empresas ativas
  importadas em 16 s, 46% com celular, 76% com e-mail. Busca com filtros em 25–50 ms.
- **E-mail do contador:** 15% dos e-mails da Receita se repetem em vários CNPJs (um só
  apareceu em 1.241). Acima de 3 repetições o e-mail é marcado como compartilhado,
  a cadência não manda nada para ele, e o filtro "e-mail próprio" o exclui.
- **Celular antigo:** número de 8 dígitos começando com 6–9 ganha o nono dígito
  (plano da Anatel). Vale para todas as fontes, não só a Receita.
- **I7:** `Socios*.zip` não é baixado nem importado; CPF é removido da razão social de MEI.
- Falha no meio da importação não estraga a base atual: tudo é montado num arquivo
  temporário. Base incompleta só com `--parcial`, e fica marcada na interface.
- A base de demonstração usa o mesmo importador, e-mails `.example` (não entregam) e
  **a fila bloqueia qualquer envio para empresa de demonstração**. Ela também não é
  enriquecida: chutar domínio com nome fictício bateria no site de empresa real.

### Enriquecer

Para empresas reveladas: descobre e audita o site (mesma prova de posse das campanhas),
pega e-mails e celulares publicados **só de site aceito**, valida o MX dos e-mails por
DNS-over-HTTPS (Cloudflare; só o domínio sai do PC) e calcula o score.

### Cadências

- No máximo 3 passos (I4), espera por passo, canal WhatsApp ou e-mail (`mailto:`,
  envio manual como no WhatsApp).
- Variável sem valor bloqueia a mensagem em vez de sair com buraco (I1). Exemplo: sem
  `REMETENTE_WHATSAPP` no `.env`, todo passo que usa `{{remetente_whatsapp}}` fica bloqueado.
- O primeiro passo precisa identificar quem fala e dar saída ("se não quiser, é só falar").
- Recusa inscrição de quem pediu opt-out, já está em outra sequência, já está negociando,
  já passou pela cadência ou recebeu mensagem nos últimos 90 dias.
- Passo sem contato no canal é pulado; os outros sobem de posição.

---

## Sobre as fontes de dados

Sem `GOOGLE_PLACES_API_KEY` o sistema usa OpenStreetMap, que é aberto e gratuito.
**Medido em Campos dos Goytacazes/RJ:** 107 empresas em 10 ramos, das quais apenas
**16% tinham telefone** e 7% tinham site; odontologia devolveu 1 resultado e
advocacia devolveu 0.

Consequência prática:

- sem telefone não há WhatsApp, então a maior parte dos leads não é contatável;
- sem contagem de avaliações não existe o eixo "dinheiro" do score, e aí os leads
  empatam (todo mundo tira 40, porque o único sinal é "não tem site").

O sistema **avisa isso na tela** em vez de fingir que priorizou. Com a chave do
Places (faixa gratuita cobre um município), entram telefone e número de avaliações,
e a priorização passa a funcionar de verdade.

---

## Estrutura

```
engine/prospectai/
  normalize.py   nome, telefone E.164, DDDs válidos, similaridade
  ramos.py       catálogo: tags OSM/Places + oferta + gancho por segmento
  sources/
    overpass.py  coleta via OpenStreetMap (grátis)
    places.py    coleta via Google Places API (precisa de chave)
  discover.py    descoberta do site + PROVA DE POSSE
  audit.py       auditoria técnica (HTTP, TLS, viewport, plataforma, peso…)
  psi.py         PageSpeed Insights
  signals.py     regras determinísticas → sinais
  score.py       score em 3 eixos, multiplicativo
  proof.py       a página de análise hospedada
  messages.py    gerador determinístico + validador de 10 regras
  classify.py    classificação de resposta (regex antes de tudo)
  outreach.py    fila, portões de segurança, máquina de estados
  pipeline.py    orquestra a cadeia
  api.py         FastAPI: /a/{token} público + /api/* privado
  cli.py         linha de comando
  base.py        base CNPJ no DuckDB: busca, ficha, análise, ICP, CSV
  base_importar.py  download (WebDAV da RFB) e importação dos ZIPs
  base_demo.py   base fictícia no layout da Receita
  crm.py         listas, revelar, enriquecer, funil, tarefas, cadências
  base_api.py    rotas /api/base, /api/listas, /api/crm, /api/cadencias…
  execucoes.py   progresso das tarefas em segundo plano
web/             interface Next.js (components/ = filtros, gráficos, painel do CRM)
```

Testes: `cd engine && ../.venv/Scripts/python.exe -m pytest tests/ -q`

---

## As regras que o código não pode quebrar

| | Regra | Onde vive |
|---|---|---|
| **I1** | Nunca afirmar fato não medido | `pipeline._reconstruir` (portão único) + validador |
| **I2** | Opt-out é terminal, para sempre | banco + 3 verificações independentes |
| **I3** | Envio duplicado é impossível | `UNIQUE(empresa_id, template_id)` + lock otimista |
| **I4** | No máximo 3 toques, depois 90 dias | máquina de estados |
| **I5** | Nada fora da janela horária | portão G2, no momento do envio |
| **I6** | Kill switch tem precedência | lido do banco antes de cada envio |
| **I7** | Dado de pessoa física não entra | import da Receita descarta a tabela de sócios |

### A mais importante: prova de posse

Antes de dizer "auditei o site de vocês", o sistema precisa provar que o domínio é
mesmo daquela empresa. E a regra não é ingênua: **nome batendo não conta como duas
provas.**

Na primeira versão, um bar chamado *Alaska* em Campos casou com `alaska.org` — o
site oficial do estado americano — porque o nome aparecia no título **e** no
domínio. *Mister Motel* casou com um site holandês pelo mesmo motivo.

Hoje a categoria "nome" vale no máximo 1 ponto e nunca decide sozinha. É preciso
uma prova independente: telefone, CNPJ, CEP, a cidade no texto da página, ou a
declaração da fonte. Sem isso, o veredito é **sem site** — perder um lead é barato,
mandar mensagem citando o site de outra empresa não é.

---

## Sobre o envio automático no WhatsApp

A API oficial (Cloud API) **não permite iniciar conversa fria**: mensagem de
marketing exige opt-in prévio do destinatário. As bibliotecas não oficiais
(Baileys, Evolution, Z-API) conseguem, mas violam os Termos e derrubam números
novos em poucos dias.

O desenho aqui é híbrido:

- **toque 1 (frio):** o sistema monta tudo e abre o `wa.me` com o texto pronto —
  você clica e envia. Poucos cliques por dia, risco de banimento zero, e converte
  melhor porque a mensagem passou por olho humano;
- **depois que o lead responde:** abre a janela de 24h e a automação oficial pode
  assumir follow-up, proposta e lembretes.

O canal é um adaptador: trocar de estrada não muda nenhuma outra parte da cadeia.

---

## O sinal mais valioso do sistema

`prova.primeira_visita_em`. Quando o lead abre a página de análise, ele está lendo
os problemas do próprio negócio naquele momento. O painel avisa na hora.

Ligue em 10 minutos. Nenhum follow-up agendado converte perto disso.
