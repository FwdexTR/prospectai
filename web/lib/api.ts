export const API = process.env.NEXT_PUBLIC_API ?? "http://127.0.0.1:8000";

async function req<T>(caminho: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API}${caminho}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!r.ok) {
    const corpo = await r.text().catch(() => "");
    let detalhe = corpo;
    try {
      detalhe = JSON.parse(corpo).detail ?? corpo;
    } catch {}
    throw new Error(detalhe || `${r.status} ${r.statusText}`);
  }
  return r.json() as Promise<T>;
}

export const get = <T,>(c: string) => req<T>(c);
export const post = <T,>(c: string, corpo?: unknown) =>
  req<T>(c, { method: "POST", body: JSON.stringify(corpo ?? {}) });
export const put = <T,>(c: string, corpo?: unknown) =>
  req<T>(c, { method: "PUT", body: JSON.stringify(corpo ?? {}) });
export const del = <T,>(c: string) => req<T>(c, { method: "DELETE" });

/** Baixa um CSV gerado pela API (GET ou POST) como arquivo. */
export async function baixar(caminho: string, nome: string, corpo?: unknown) {
  const r = await fetch(`${API}${caminho}`, corpo === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(corpo),
  });
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  const url = URL.createObjectURL(await r.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: nome });
  a.click();
  URL.revokeObjectURL(url);
}

export const fmt = (n: number | null | undefined) =>
  n == null ? "—" : n.toLocaleString("pt-BR");
export const moeda = (n: number | null | undefined) =>
  n == null ? "—" : n.toLocaleString("pt-BR", { style: "currency", currency: "BRL",
                                                maximumFractionDigits: 0 });
export const cnpjFmt = (c: string) =>
  c.length === 14 ? `${c.slice(0, 2)}.${c.slice(2, 5)}.${c.slice(5, 8)}/${c.slice(8, 12)}-${c.slice(12)}` : c;
export const telFmt = (t: string | null | undefined) => {
  if (!t) return "—";
  const d = t.replace(/\D/g, "").replace(/^55/, "");
  if (d.length === 11) return `(${d.slice(0, 2)}) ${d.slice(2, 7)}-${d.slice(7)}`;
  if (d.length === 10) return `(${d.slice(0, 2)}) ${d.slice(2, 6)}-${d.slice(6)}`;
  return t;
};
export const dataFmt = (d: string | null | undefined) =>
  d ? new Date(d.length === 10 ? `${d}T12:00:00` : d.replace(" ", "T") + "Z")
        .toLocaleDateString("pt-BR") : "—";

// --------------------------------------------------------------------------
// Prospecção B2B: base, listas, CRM, cadências
// --------------------------------------------------------------------------
export type Filtros = {
  q?: string; ufs?: string[]; municipios?: string[]; cnaes?: string[];
  cnaes_excluir?: string[]; secundarios?: boolean; portes?: string[];
  mei?: "todos" | "so_mei" | "sem_mei"; simples?: "todos" | "sim" | "nao";
  capital_min?: number | null; capital_max?: number | null;
  abertura_de?: string | null; abertura_ate?: string | null;
  situacoes?: string[]; so_matriz?: boolean; com_telefone?: boolean;
  com_celular?: boolean; com_email?: boolean; email_proprio?: boolean; bairro?: string;
};

export type NoCrm = { empresa_id: number; estado: string | null } | null;

export type ItemBase = {
  cnpj: string; nome: string; razao_social: string | null; nome_fantasia: string | null;
  cnae: string; cnae_descricao: string | null; municipio: string | null; uf: string;
  bairro: string | null; porte: string; porte_rotulo: string | null; mei: boolean | null;
  simples: boolean | null; capital_social: number | null; data_abertura: string | null;
  situacao: string; situacao_rotulo: string; matriz: boolean; telefone: string | null;
  tem_celular: boolean; tem_email: boolean; email_qtd: number | null; fonte: string;
  crm?: NoCrm; fora_da_base?: boolean;
};

export type ResultadoBusca = {
  total: number; pagina: number; por_pagina: number; itens: ItemBase[];
};

export type StatusBase = {
  disponivel: boolean; arquivo?: string;
  meta?: {
    fonte: "receita" | "demo"; versao: string; importada_em: string; total: string;
    parcial: string; so_ativas: string;
    arquivos: { estabelecimentos: string; empresas: string; simples: boolean;
                faltando: string[]; corrompidos: string[] };
  };
};

export type Ficha = ItemBase & {
  cnpj_basico: string; tipo_logradouro: string | null; logradouro: string | null;
  numero: string | null; complemento: string | null; cep: string | null;
  telefone1: string | null; telefone2: string | null; celular: string | null;
  email: string | null; telefone_qtd: number | null; natureza: string | null;
  natureza_descricao: string | null; data_situacao: string | null;
  motivo_descricao: string | null; municipio_codigo: string;
  cnaes_secundarios: { codigo: string; descricao: string | null }[];
  outras_unidades: ItemBase[]; listas: { id: number; nome: string }[];
};

export type Opcao = { codigo: string; descricao?: string; nome?: string; uf?: string;
                      estabelecimentos?: number };
export type Atalho = { chave: string; rotulo: string; cnaes: string[] };

export type Analise = {
  resumo: { total: number; com_telefone: number; com_celular: number; com_email: number;
            com_email_proprio: number; mei: number; simples: number; abertas_90d: number;
            capital_mediano: number | null };
  por_uf: { uf: string; n: number }[];
  por_municipio: { codigo: string; nome: string; uf: string; n: number }[];
  por_cnae: { codigo: string; descricao: string | null; n: number }[];
  por_porte: { porte: string; porte_rotulo: string | null; n: number }[];
  por_ano: { ano: number; n: number }[];
  por_capital: { faixa: string; n: number }[];
};

export type Perfil = {
  encontradas: number; informadas: number; invalidos?: string[];
  cnaes?: { codigo: string; descricao: string | null; n: number }[];
  ufs?: { uf: string; n: number }[];
  municipios?: { codigo: string; nome: string; n: number }[];
  portes?: { porte: string; n: number }[];
  filtros: Filtros | null; parecidos_na_base?: number;
};

export type Lista = {
  id: number; nome: string; descricao: string | null; criada_em: string;
  itens: number; reveladas: number;
};

export type ListaDetalhe = Omit<Lista, "itens"> & {
  filtros: Filtros | null; total: number; pagina: number; por_pagina: number;
  itens: ItemBase[];
};

export type LeadFunil = {
  empresa_id: number; nome: string; cidade: string | null; uf: string | null;
  ramo: string | null; cnae: string | null; fonte: string; telefone: string | null;
  cnpj: string | null; estado: string; estado_desde: string;
  proximo_evento_em: string | null; valor_estimado: number | null;
  valor_fechado: number | null; score: number | null; faixa: string | null;
  tarefas: number; proxima_tarefa: string | null; cadencia: string | null;
};

export type Atividade = {
  id: number; empresa_id: number; tipo: string; titulo: string; texto: string | null;
  vence_em: string | null; concluida_em: string | null; criada_em: string;
  empresa_nome?: string; cidade?: string | null; estado?: string | null; atrasada?: number;
};

export type Passo = {
  canal: "whatsapp" | "email"; espera_dias: number; assunto: string | null; corpo: string;
};

export type Cadencia = {
  id: number; nome: string; descricao: string | null; ativa: number; criada_em: string;
  passos: number; ativas: number; inscritas: number; responderam: number;
};

export type CadenciaDetalhe = Omit<Cadencia, "passos"> & {
  passos: (Passo & { id: number; ordem: number })[];
  inscritos: { id: number; empresa_id: number; nome: string; cidade: string | null;
               fonte: string; estado: string; inscrita_em: string; enviadas: number;
               mensagens: number }[];
};

export type ResultadoInscricao = {
  inscritas: { empresa_id: number; nome: string; toques: number;
               bloqueadas_pelo_validador: number }[];
  recusadas: { empresa_id: number; nome: string | null; motivo: string }[];
};

export const ETAPA: Record<string, string> = {
  NOVO: "Novo", SELECIONADO: "Em cadência", TOQUE_1: "Em cadência", TOQUE_2: "Em cadência",
  TOQUE_3: "Em cadência", EM_CONVERSA: "Em conversa", REUNIAO: "Reunião",
  PROPOSTA: "Proposta", FECHADO: "Fechado", PERDIDO: "Perdido", DORMENTE: "Dormente",
  OPT_OUT: "Opt-out",
};

export const UFS = ["AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
  "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"];

export const PORTES: Record<string, string> = {
  "01": "Microempresa", "03": "Pequeno porte", "05": "Demais", "00": "Não informado",
  MEI: "MEI",
};

// --------------------------------------------------------------------------
export type Ramo = {
  chave: string; rotulo: string; oferta: string; gancho: string; ticket: number[];
};

export type Campanha = {
  id: number; nome: string; cidade: string; uf: string; raio_km: number;
  ramos: string[]; criada_em: string; empresas: number; com_mensagem: number;
};

export type Lead = {
  id: number; nome: string; ramo: string | null; endereco: string | null;
  bairro: string | null; telefone: string | null;
  total: number; faixa: string; dor: number; dinheiro: number; timing: number;
  site_url: string | null; site_encontrado_como: string; site_confianca: number;
  psi_mobile_score: number | null; auditoria_erro: string | null;
  lead_estado: string | null; dores: string[]; msgs: number;
  prova_aberta: string | null; opt_out: number | null;
};

export type ItemFila = {
  id: number; toque: number; texto: string; template_id: string;
  empresa_id: number; nome: string; ramo: string | null; telefone: string;
  score: number | null; faixa: string | null; link: string | null;
  prova_token: string | null; visitou_prova: boolean;
  canal: "whatsapp" | "email"; assunto: string | null; destino: string;
  cadencia: string | null; bloqueio: string | null; fonte: string;
};

export type Fila = {
  itens: ItemFila[]; envio_habilitado: boolean; pausa_motivo: string | null;
  limite_dia: number; enviados_hoje: number; dentro_da_janela: boolean;
  circuit_breaker: string | null;
};

export type Alerta = {
  tipo: string; urgencia: string; texto: string;
  empresa_id?: number; quando: string | null;
};

export type Painel = {
  empresas: number; sem_site: number; faixa_a: number; faixa_b: number;
  enviadas: number; respostas: number; provas_abertas: number; opt_outs: number;
  em_conversa: number; fechados: number; alertas: Alerta[];
  reveladas: number; tarefas_vencidas: number; em_cadencia: number;
  config: { tem_places: boolean };
};

export type Sinal = {
  tipo: string; eixo: string; valor: string | null; peso: number;
  evidencia: Record<string, unknown> | null;
};

export type Mensagem = {
  id: number; toque: number; texto: string; template_id: string; estado: string;
  validador_ok: number; validador_erros: string | null;
  enviada_em: string | null; agendada_para: string | null;
};

export type DetalheLead = {
  empresa: Record<string, any>;
  auditoria: Record<string, any> | null;
  score: Record<string, any> | null;
  sinais: Sinal[];
  mensagens: (Mensagem & { canal?: string; assunto?: string | null })[];
  contatos: Record<string, any>[];
  respostas: Record<string, any>[];
  prova: { url: string; num_visitas: number; primeira_visita_em: string | null } | null;
  lead: Record<string, any> | null;
  atividades: Atividade[];
  inscricoes: { id: number; cadencia_id: number; cadencia: string; estado: string;
                inscrita_em: string }[];
};

export type Execucao = {
  estado: "rodando" | "concluida" | "erro";
  etapa: string; feito: number; total: number;
  resumo?: any; erro?: string;
};

// Rótulos legíveis para os códigos de sinal.
export const NOME_SINAL: Record<string, string> = {
  SEM_SITE: "sem site", SITE_E_PERFIL: "usa perfil como site",
  SITE_MORTO: "site fora do ar", SITE_QUEBRADO: "site com erro",
  SEM_HTTPS: "sem HTTPS", NAO_RESPONSIVO: "não responsivo",
  LENTO: "lento no celular", LCP_RUIM: "carregamento lento",
  RESPOSTA_LENTA: "servidor lento", SITE_ABANDONADO: "site abandonado",
  CONSTRUTOR_GRATUITO: "construtor grátis", CARDAPIO_EM_PDF: "cardápio em PDF",
  PAGINA_PESADA: "página pesada", IMAGENS_SEM_LAZY: "imagens sem otimização",
  TITULO_GENERICO: "título genérico", SEM_WHATSAPP: "sem WhatsApp",
  SEM_MEDICAO: "sem medição",
  VOLUME_REVIEWS: "avaliações no Google", REVIEWS_RECENTES: "avaliações recentes",
  NAO_MEI: "não é MEI", CAPITAL_RELEVANTE: "capital relevante",
  RAMO_TICKET_ALTO: "ramo de ticket alto",
  PERFIL_COMPLETO_GOOGLE: "perfil completo no Google",
  RECEM_ABERTA: "empresa recém-aberta", CERTIFICADO_VENCENDO: "certificado vencendo",
};
