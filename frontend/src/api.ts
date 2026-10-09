// Cliente da API do Emissor. Todo erro vira ApiError com a mensagem do servidor,
// os detalhes (lista ou objeto por linha) e o request_id que aparece no log.

export type Ambiente = "1" | "2";

export interface MensagemSefin {
  codigo: string | null;
  descricao: string;
  complemento: string | null;
  dica: string | null;
}

export interface ErroEmissao {
  tipo: string;
  resumo: string;
  http_status?: number | null;
  mensagens: MensagemSefin[];
}

export type StatusEmissao =
  | "pendente"
  | "processando"
  | "autorizada"
  | "rejeitada"
  | "erro"
  | "nao_enviada"
  | "cancelada"
  | "substituida";

/** Situações em que a nota existe na Sefin (tem XML/PDF para baixar). */
export const COM_ARQUIVOS: StatusEmissao[] = ["autorizada", "cancelada", "substituida"];

export interface EventoNFSe {
  id: string;
  tipo: string;
  descricao: string;
  motivo: string | null;
  autor: string | null;
  dh_evento: string | null;
  origem: "sistema" | "ambiente_nacional";
  tem_xml: boolean;
}

export interface EstadoSincronizacao {
  em_andamento: boolean;
  iniciada_em: string | null;
  concluida_em: string | null;
  erro: string | null;
  documentos: number;
  eventos_novos: number;
  notas_atualizadas: number;
  motivo?: "iniciada" | "em_andamento" | "recente" | "sem_notas";
}

export interface Emissao {
  id: number;
  lote_id: string | null;
  linha_id: number | null;
  ambiente: Ambiente;
  status: StatusEmissao;
  tomador_documento: string;
  tomador_nome: string;
  valor: string;
  descricao: string;
  numero_nfse: string | null;
  chave_acesso: string | null;
  serie: number | null;
  n_dps: number | null;
  dh_emissao: string | null;
  tem_xml: boolean;
  tem_pdf: boolean;
  erro: ErroEmissao | null;
  alertas: MensagemSefin[];
  recuperada: boolean;
  pode_cancelar: boolean;
  atualizado_em: string | null;
  eventos?: EventoNFSe[];
  aviso?: string | null;
  eventos_novos?: number;
  id_dps?: string | null;
  dh_processamento?: string | null;
  tentativas?: number;
  criado_em?: string | null;
}

export interface Linha {
  id?: number;
  nome: string;
  documento: string;
  valor: string;
  descricao: string;
  ultima_emissao?: Emissao | null;
}

export interface CertificadoInfo {
  titular: string;
  cnpj: string | null;
  cpf: string | null;
  emissor: string;
  valido_de: string;
  valido_ate: string;
  dias_para_vencer: number;
  vencido: boolean;
  aviso?: string;
  erro?: string;
}

export interface Status {
  versao: string;
  onboarding_concluido: boolean;
  ambiente: Ambiente;
  ambiente_descricao: string;
  certificado: CertificadoInfo | null;
  prestador: { cnpj: string | null; municipio: string | null };
  pronto_para_emitir: boolean;
  problemas: string[];
  avisos: string[];
  lote_em_andamento: string | null;
}

export interface Fiscal {
  cnpj: string;
  c_mun_emissor: string;
  fone: string | null;
  email: string | null;
  inscricao_municipal: string | null;
  op_simp_nac: string;
  reg_ap_trib_sn: string | null;
  reg_esp_trib: string;
  c_loc_prestacao: string | null;
  c_trib_nac: string;
  c_trib_mun: string | null;
  c_nbs: string | null;
  trib_issqn: string;
  tp_ret_issqn: string;
  cst_pis_cofins: string | null;
  tp_ret_pis_cofins: string | null;
  p_tot_trib_sn: string | null;
  tp_emit: string;
}

export interface Config {
  ambiente: Ambiente;
  serie: number;
  proximo_ndps_homologacao: number;
  proximo_ndps_producao: number;
  fiscal: Fiscal | null;
  onboarding_concluido: boolean;
}

export interface Opcao {
  valor: string;
  descricao: string;
}
export type Opcoes = Record<string, Opcao[]>;

export interface Cliente {
  documento: string;
  nome: string;
  c_mun: string | null;
  municipio: string | null;
  cep: string | null;
  logradouro: string | null;
  numero: string | null;
  complemento: string | null;
  bairro: string | null;
  fone: string | null;
  email: string | null;
  inscricao_municipal: string | null;
  situacao_cadastral: string | null;
  origem: string;
  endereco_completo: boolean;
  atualizado_em: string | null;
}

export interface Municipio {
  codigo: string;
  nome: string;
  uf: string;
}

export interface Validacao {
  ambiente: Ambiente;
  ambiente_descricao: string;
  problemas: string[];
  linhas: { linha_id: number; erros: string[]; avisos: string[] }[];
  quantidade: number;
  total: string;
}

export interface Lote {
  id: string;
  ambiente: Ambiente;
  status: "em_andamento" | "concluido" | "interrompido" | "cancelado";
  total: number;
  motivo_interrupcao: string | null;
  criado_em: string;
  finalizado_em: string | null;
  contagem?: Partial<Record<StatusEmissao, number>>;
  emissoes?: Emissao[];
  em_processamento?: boolean;
}

export interface ListaEmissoes {
  itens: Emissao[];
  total: number;
  pagina: number;
  por_pagina: number;
  valor_autorizado: string;
}

export class ApiError extends Error {
  status: number;
  detalhes: unknown;
  requestId: string | null;
  /** Erro da Sefin/ADN com códigos e dicas (quando a operação falou com o Ambiente Nacional). */
  sefin: ErroEmissao | null;
  constructor(status: number, mensagem: string, detalhes: unknown, requestId: string | null, sefin: ErroEmissao | null = null) {
    super(mensagem);
    this.status = status;
    this.detalhes = detalhes;
    this.requestId = requestId;
    this.sefin = sefin;
  }
}

async function tratar<T>(resp: Response): Promise<T> {
  const rid = resp.headers.get("X-Request-Id");
  const tipo = resp.headers.get("content-type") || "";
  if (!resp.ok) {
    let mensagem = `Erro ${resp.status} ao falar com o servidor.`;
    let detalhes: unknown = null;
    let sefin: ErroEmissao | null = null;
    if (tipo.includes("application/json")) {
      const corpo = await resp.json().catch(() => null);
      if (corpo?.mensagem) mensagem = corpo.mensagem;
      detalhes = corpo?.detalhes ?? null;
      sefin = corpo?.sefin ?? null;
    } else {
      const texto = await resp.text().catch(() => "");
      if (texto) mensagem = `${mensagem} ${texto.slice(0, 300)}`;
    }
    throw new ApiError(resp.status, mensagem, detalhes, rid, sefin);
  }
  if (tipo.includes("application/json")) return (await resp.json()) as T;
  return (await resp.blob()) as unknown as T;
}

async function req<T>(metodo: string, url: string, corpo?: unknown): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(url, {
      method: metodo,
      headers: corpo instanceof FormData || corpo === undefined ? undefined : { "Content-Type": "application/json" },
      body: corpo instanceof FormData ? corpo : corpo === undefined ? undefined : JSON.stringify(corpo),
    });
  } catch {
    throw new ApiError(0, "Sem conexão com o servidor do Emissor. Verifique se ele está ligado e se a rede está ok.", null, null);
  }
  return tratar<T>(resp);
}

export const api = {
  get: <T>(url: string) => req<T>("GET", url),
  post: <T>(url: string, corpo?: unknown) => req<T>("POST", url, corpo),
  put: <T>(url: string, corpo?: unknown) => req<T>("PUT", url, corpo),
  del: <T>(url: string) => req<T>("DELETE", url),
};

/** Baixa um arquivo (GET ou POST) respeitando o nome enviado pelo servidor. */
export async function baixar(url: string, corpo?: unknown): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(url, {
      method: corpo ? "POST" : "GET",
      headers: corpo ? { "Content-Type": "application/json" } : undefined,
      body: corpo ? JSON.stringify(corpo) : undefined,
    });
  } catch {
    throw new ApiError(0, "Sem conexão com o servidor do Emissor.", null, null);
  }
  if (!resp.ok) await tratar(resp);
  const blob = await resp.blob();
  const disp = resp.headers.get("content-disposition") || "";
  const nome = /filename="([^"]+)"/.exec(disp)?.[1] || "arquivo";
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = nome;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

/** Envia erros do navegador para o log do servidor (para diagnóstico). */
export function logCliente(nivel: "error" | "warn" | "info", mensagem: string, contexto?: Record<string, unknown>) {
  fetch("/api/logs/cliente", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nivel, mensagem, contexto: { ...contexto, url: location.href } }),
  }).catch(() => undefined);
}
