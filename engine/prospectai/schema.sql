-- ProspectAI -- esquema (SQLite; portavel para Postgres trocando os tipos)
-- Implementa a Parte 2 da spec. As invariantes I2/I3/I4 estao no banco de
-- proposito: regra que vive so no codigo e regra que uma hora alguem burla.

PRAGMA foreign_keys = ON;

-- --------------------------------------------------------------------------
-- Campanha: o que voce pediu (cidade + ramo). E0 da cadeia.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS campanha (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nome            TEXT    NOT NULL,
    cidade          TEXT    NOT NULL,
    uf              TEXT    NOT NULL,
    raio_km         REAL    NOT NULL DEFAULT 0,
    ramos           TEXT    NOT NULL,          -- JSON: chaves do catalogo de ramos
    quota_diaria    INTEGER NOT NULL DEFAULT 15,
    criada_em       TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- --------------------------------------------------------------------------
-- Empresa: a entidade central. Vem do OSM/Places (E2) e, quando disponivel,
-- enriquecida com a Receita Federal (E1).
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS empresa (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    campanha_id         INTEGER REFERENCES campanha(id),

    -- identidade
    nome                TEXT    NOT NULL,
    nome_norm           TEXT    NOT NULL,
    ramo                TEXT,                  -- chave do catalogo (restaurante, clinica...)
    categoria_fonte     TEXT,                  -- o que a fonte disse (amenity=restaurant)

    -- localizacao
    endereco            TEXT,
    bairro              TEXT,
    cidade              TEXT,
    uf                  TEXT,
    cep                 TEXT,
    lat                 REAL,
    lng                 REAL,

    -- contato bruto vindo das fontes
    telefone            TEXT,                  -- E.164
    telefone_origem     TEXT,
    email               TEXT,
    website_declarado   TEXT,                  -- o que a fonte disse ser o site
    instagram           TEXT,
    facebook            TEXT,

    -- origem
    fonte               TEXT    NOT NULL,      -- osm | places | manual
    fonte_id            TEXT,                  -- node/123456 ou place_id
    fonte_payload       TEXT,                  -- JSON cru, para auditoria/debug

    -- Receita Federal (E1) -- nulo enquanto nao houver import do dump
    cnpj                TEXT,
    razao_social        TEXT,
    cnae                TEXT,
    porte               TEXT,
    capital_social      REAL,
    data_abertura       TEXT,
    situacao_cadastral  TEXT,
    match_confianca     REAL,
    match_revisado      INTEGER NOT NULL DEFAULT 0,

    criado_em           TEXT    NOT NULL DEFAULT (datetime('now')),
    atualizado_em       TEXT    NOT NULL DEFAULT (datetime('now')),

    UNIQUE (fonte, fonte_id)
);

CREATE INDEX IF NOT EXISTS idx_empresa_campanha ON empresa(campanha_id);

-- Uma empresa pode aparecer em varias buscas (mesma cidade, ramos diferentes;
-- ou a mesma busca refeita depois). Sem esta tabela, a coleta idempotente
-- devolvia a empresa existente e a busca nova saia quase vazia.
CREATE TABLE IF NOT EXISTS campanha_empresa (
    campanha_id INTEGER NOT NULL REFERENCES campanha(id) ON DELETE CASCADE,
    empresa_id  INTEGER NOT NULL REFERENCES empresa(id)  ON DELETE CASCADE,
    PRIMARY KEY (campanha_id, empresa_id)
);
CREATE INDEX IF NOT EXISTS idx_ce_empresa ON campanha_empresa(empresa_id);
CREATE INDEX IF NOT EXISTS idx_empresa_ramo     ON empresa(ramo);
CREATE INDEX IF NOT EXISTS idx_empresa_geo      ON empresa(lat, lng);

-- --------------------------------------------------------------------------
-- Auditoria: E5. APPEND ONLY -- nunca atualiza, sempre insere nova linha.
-- E a comparacao entre snapshots que gera o gatilho de segundo toque.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS auditoria (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id          INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    coletado_em         TEXT    NOT NULL DEFAULT (datetime('now')),

    -- E4 descoberta
    site_url            TEXT,
    site_encontrado_como TEXT,                 -- declarado | busca | chute | nao_encontrado
    site_confianca      INTEGER NOT NULL DEFAULT 0,
    site_provas         TEXT,                  -- JSON: quais provas de posse bateram

    -- rede
    http_status         INTEGER,
    tempo_resposta_ms   INTEGER,
    cadeia_redirect     TEXT,
    tls_valido          INTEGER,
    tls_expira_em       TEXT,
    tls_dias_restantes  INTEGER,

    -- html
    tem_viewport        INTEGER,
    tem_whatsapp        INTEGER,
    tem_analytics       INTEGER,
    tem_pixel           INTEGER,
    plataforma          TEXT,
    plano_gratuito      INTEGER,
    ano_copyright       INTEGER,
    titulo              TEXT,
    titulo_generico     INTEGER,
    cardapio_em_pdf     INTEGER,
    html_bytes          INTEGER,
    imagens_sem_lazy    INTEGER,
    tem_link_social     TEXT,                  -- JSON: instagram/facebook achados no site

    -- pagespeed (assincrono, pode ficar nulo)
    psi_mobile_score    INTEGER,
    psi_lcp_ms          INTEGER,
    psi_cls             REAL,
    psi_coletado_em     TEXT,

    -- I1: auditoria com erro NAO pode virar mensagem.
    erro                TEXT
);

CREATE INDEX IF NOT EXISTS idx_auditoria_empresa ON auditoria(empresa_id, coletado_em DESC);

-- --------------------------------------------------------------------------
-- Sinal: E6. Uma linha por regra que disparou. Append only.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sinal (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id    INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    auditoria_id  INTEGER REFERENCES auditoria(id) ON DELETE CASCADE,
    tipo          TEXT    NOT NULL,
    eixo          TEXT    NOT NULL CHECK (eixo IN ('dor','dinheiro','timing')),
    valor         TEXT,
    evidencia     TEXT,                        -- JSON: o dado bruto que provou o sinal
    peso          REAL    NOT NULL,
    detectado_em  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sinal_empresa ON sinal(empresa_id);

-- --------------------------------------------------------------------------
-- Score: E6. Versionado -- recalibrar nunca apaga historico.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS score (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id     INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    auditoria_id   INTEGER REFERENCES auditoria(id) ON DELETE CASCADE,
    dor            REAL    NOT NULL,
    dinheiro       REAL    NOT NULL,
    timing         REAL    NOT NULL,
    total          REAL    NOT NULL,
    faixa          TEXT    NOT NULL CHECK (faixa IN ('A','B','C','D')),
    versao_modelo  TEXT    NOT NULL,
    calculado_em   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_score_empresa ON score(empresa_id, calculado_em DESC);
CREATE INDEX IF NOT EXISTS idx_score_total   ON score(total DESC);

-- --------------------------------------------------------------------------
-- Contato + opt-out. I2: opt_out e terminal e checado em tres lugares.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contato (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id    INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    canal         TEXT    NOT NULL,            -- whatsapp | email | instagram | telefone
    valor         TEXT    NOT NULL,            -- E.164 para whatsapp/telefone
    origem        TEXT,
    eh_movel      INTEGER NOT NULL DEFAULT 0,
    tem_whatsapp  INTEGER,
    verificado_em TEXT,
    opt_out       INTEGER NOT NULL DEFAULT 0,
    opt_out_em    TEXT,
    opt_out_motivo TEXT,
    UNIQUE (empresa_id, canal, valor)
);

CREATE INDEX IF NOT EXISTS idx_contato_optout ON contato(opt_out);

-- --------------------------------------------------------------------------
-- Prova: E8. A auditoria hospedada. primeira_visita_em e o sinal de intencao
-- mais forte do sistema inteiro.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prova (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id          INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    auditoria_id        INTEGER NOT NULL REFERENCES auditoria(id) ON DELETE CASCADE,
    token               TEXT    NOT NULL UNIQUE,
    gerada_em           TEXT    NOT NULL DEFAULT (datetime('now')),
    primeira_visita_em  TEXT,
    num_visitas         INTEGER NOT NULL DEFAULT 0,
    ultimo_user_agent   TEXT
);

-- --------------------------------------------------------------------------
-- Mensagem: E9/E10. I3 -- o UNIQUE torna envio duplicado impossivel.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mensagem (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id     INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    contato_id     INTEGER REFERENCES contato(id),
    template_id    TEXT    NOT NULL,
    toque          INTEGER NOT NULL CHECK (toque BETWEEN 1 AND 3),   -- I4
    texto          TEXT    NOT NULL,
    fatos_usados   TEXT,                        -- JSON: I1, o que a mensagem pode citar
    validador_ok   INTEGER NOT NULL DEFAULT 0,
    validador_erros TEXT,
    gerada_por     TEXT,                        -- llm | fallback
    estado         TEXT    NOT NULL DEFAULT 'AGENDADA',
    agendada_para  TEXT,
    enviada_em     TEXT,
    entregue_em    TEXT,
    lida_em        TEXT,
    provider_message_id TEXT,
    erro           TEXT,
    criada_em      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (empresa_id, template_id)            -- I3: idempotencia de envio
);

CREATE INDEX IF NOT EXISTS idx_mensagem_estado ON mensagem(estado, agendada_para);

-- --------------------------------------------------------------------------
-- Resposta: E11.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS resposta (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    mensagem_id   INTEGER REFERENCES mensagem(id),
    empresa_id    INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    texto         TEXT    NOT NULL,
    recebida_em   TEXT    NOT NULL DEFAULT (datetime('now')),
    classe        TEXT,
    confianca     REAL,
    classificada_por TEXT                       -- regex | llm
);

-- --------------------------------------------------------------------------
-- Lead: a maquina de estados da Parte 4.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lead (
    empresa_id       INTEGER PRIMARY KEY REFERENCES empresa(id) ON DELETE CASCADE,
    estado           TEXT    NOT NULL DEFAULT 'NOVO',
    estado_desde     TEXT    NOT NULL DEFAULT (datetime('now')),
    proximo_evento_em TEXT,
    desfecho         TEXT,
    valor_fechado    REAL,
    motivo_perda     TEXT
);

CREATE INDEX IF NOT EXISTS idx_lead_estado ON lead(estado, proximo_evento_em);

-- ==========================================================================
-- Modulo de prospeccao B2B (base CNPJ, listas, CRM, cadencias).
-- A base em si fica no DuckDB (base.py); aqui so o que VOCE faz com ela.
-- Colunas novas em tabelas antigas entram por db._migrar, nao por aqui.
-- ==========================================================================

-- Lista: conjunto de CNPJs da base. Guarda a busca que a gerou, para dar
-- para refazer ("lista dinamica") quando a base for atualizada.
CREATE TABLE IF NOT EXISTS lista (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nome        TEXT    NOT NULL,
    descricao   TEXT,
    filtros     TEXT,                          -- JSON da busca de origem
    criada_em   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lista_item (
    lista_id      INTEGER NOT NULL REFERENCES lista(id) ON DELETE CASCADE,
    cnpj          TEXT    NOT NULL,
    adicionado_em TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (lista_id, cnpj)
);
CREATE INDEX IF NOT EXISTS idx_lista_item_cnpj ON lista_item(cnpj);

-- Atividade: linha do tempo do lead no CRM (nota, ligacao, reuniao) e tarefa
-- com prazo. Tarefa = atividade com vence_em; concluida quando concluida_em.
CREATE TABLE IF NOT EXISTS atividade (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id    INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    tipo          TEXT    NOT NULL CHECK (tipo IN
                    ('nota','tarefa','ligacao','reuniao','email','whatsapp')),
    titulo        TEXT    NOT NULL,
    texto         TEXT,
    vence_em      TEXT,
    concluida_em  TEXT,
    criada_em     TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_atividade_empresa ON atividade(empresa_id, criada_em DESC);
CREATE INDEX IF NOT EXISTS idx_atividade_prazo   ON atividade(concluida_em, vence_em);

-- Cadencia: sequencia escrita por voce (equivale as "sequences" da Apollo).
-- I4 no banco: no maximo 3 passos de mensagem por cadencia.
CREATE TABLE IF NOT EXISTS cadencia (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nome        TEXT    NOT NULL,
    descricao   TEXT,
    ativa       INTEGER NOT NULL DEFAULT 1,
    criada_em   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cadencia_passo (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cadencia_id  INTEGER NOT NULL REFERENCES cadencia(id) ON DELETE CASCADE,
    ordem        INTEGER NOT NULL CHECK (ordem BETWEEN 1 AND 3),          -- I4
    canal        TEXT    NOT NULL CHECK (canal IN ('whatsapp','email')),
    espera_dias  INTEGER NOT NULL DEFAULT 0 CHECK (espera_dias BETWEEN 0 AND 60),
    assunto      TEXT,
    corpo        TEXT    NOT NULL,
    UNIQUE (cadencia_id, ordem)
);

CREATE TABLE IF NOT EXISTS inscricao (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cadencia_id  INTEGER NOT NULL REFERENCES cadencia(id) ON DELETE CASCADE,
    empresa_id   INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    estado       TEXT    NOT NULL DEFAULT 'ATIVA' CHECK (estado IN
                   ('ATIVA','CONCLUIDA','RESPONDEU','INTERROMPIDA','OPT_OUT')),
    inscrita_em  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (cadencia_id, empresa_id)
);
CREATE INDEX IF NOT EXISTS idx_inscricao_empresa ON inscricao(empresa_id, estado);

-- --------------------------------------------------------------------------
-- Estado do sistema: kill switch (I6) e circuit breakers.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sistema_estado (
    chave         TEXT PRIMARY KEY,
    valor         TEXT NOT NULL,
    atualizado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO sistema_estado (chave, valor) VALUES
    ('envio_habilitado', 'false'),   -- comeca DESLIGADO de proposito
    ('pausa_motivo',     'sistema recem-instalado; ligue manualmente'),
    ('dia_warmup',       '1');
