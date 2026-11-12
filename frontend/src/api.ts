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
  /** Recusa na homologação: o cadastro da Produção Restrita costuma estar incompleto. */
  aviso?: string | null;
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
  tributacao_nome: string | null;
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
  aba_id?: number;
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
  empresa: { id: number; nome: string | null; cnpj: string | null; acesso: AcessoEmpresa };
  varias_empresas: boolean;
}

/** certificado = A1 da própria empresa; procuracao = certificado de outro CNPJ/CPF com procuração eletrônica. */
export type AcessoEmpresa = "certificado" | "procuracao";

export interface Empresa {
  id: number;
  nome: string;
  nome_definido: string | null;
  cnpj: string | null;
  acesso: AcessoEmpresa;
  ambiente: Ambiente;
  onboarding_concluido: boolean;
  papel: Papel | null;
  certificado: {
    id: number;
    titular: string;
    cnpj: string | null;
    cpf: string | null;
    valido_ate: string;
    dias_para_vencer: number;
    vencido: boolean;
  } | null;
}

export interface ListaEmpresas {
  empresas: Empresa[];
  atual: number | null;
  limite: number | null;
  disponivel: boolean;
  pode_adicionar: boolean;
  compartilhar_clientes: boolean;
  desativadas: { id: number; nome: string; cnpj: string | null }[];
}

export interface CertificadoGuardado {
  id: number;
  titular: string;
  cnpj: string | null;
  cpf: string | null;
  emissor: string;
  valido_ate: string;
  vencido: boolean;
  empresas: number[];
}

export type Papel = "admin" | "operador";

export interface UsuarioSessao {
  id: number;
  email: string;
  nome: string;
  papel: Papel;
  totp_ativo: boolean;
  empresa_id: number | null;
  /** Papel na empresa desta aba (o administrador da instalação é admin em todas). */
  papel_empresa: Papel | null;
}

export interface Sessao {
  autenticado: boolean;
  usuario: UsuarioSessao | null;
  primeiro_acesso: boolean;
  primeiro_acesso_sem_codigo: boolean;
  /** Hospedagem: o link do primeiro acesso foi enviado ao e-mail de quem assinou. */
  primeiro_acesso_por_email?: boolean;
  suspenso: boolean;
  url_assinatura: string | null;
  recuperacao_email: boolean;
}

export interface Usuario {
  id: number;
  email: string;
  nome: string;
  papel: Papel;
  ativo: boolean;
  totp_ativo: boolean;
  convite_pendente: boolean;
  bloqueado: boolean;
  criado_em: string | null;
  ultimo_login: string | null;
  empresas: { empresa_id: number; papel: Papel }[];
}

export interface RegistroAuditoria {
  quando: string;
  usuario: string | null;
  acao: string;
  detalhe: string | null;
  ip: string | null;
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
  p_aliq_iss: string | null;
  p_aliq_iss_atualizada_em: string | null;
  p_aliq_pis: string | null;
  p_aliq_cofins: string | null;
  p_ret_irrf: string | null;
  p_ret_csll: string | null;
  p_ret_cp: string | null;
  p_tot_trib_fed: string | null;
  p_tot_trib_est: string | null;
  p_tot_trib_mun: string | null;
  ibscbs: boolean;
  ibscbs_c_ind_op: string;
  ibscbs_c_class_trib: string;
  ibscbs_ind_final: string | null;
  /** Tipo de operação (tpOper): só nos serviços 10.05, 15.09, 17.12 e 25.05. */
  ibscbs_tp_oper: string | null;
  tp_emit: string;
}

/** Campos que mudam de uma tributação para outra (o resto vem das configurações). */
export const CAMPOS_TRIBUTACAO = [
  "c_loc_prestacao",
  "c_trib_nac",
  "c_trib_mun",
  "c_nbs",
  "trib_issqn",
  "tp_ret_issqn",
  "cst_pis_cofins",
  "tp_ret_pis_cofins",
  "p_tot_trib_sn",
  "p_aliq_iss",
  "p_aliq_iss_atualizada_em",
  "p_aliq_pis",
  "p_aliq_cofins",
  "p_ret_irrf",
  "p_ret_csll",
  "p_ret_cp",
  "p_tot_trib_fed",
  "p_tot_trib_est",
  "p_tot_trib_mun",
  "ibscbs",
  "ibscbs_c_ind_op",
  "ibscbs_c_class_trib",
  "ibscbs_ind_final",
  "ibscbs_tp_oper",
] as const;
export type DadosTributacao = Pick<Fiscal, (typeof CAMPOS_TRIBUTACAO)[number]>;

export function dadosTributacao(f: DadosTributacao): DadosTributacao {
  return Object.fromEntries(CAMPOS_TRIBUTACAO.map((k) => [k, f[k]])) as unknown as DadosTributacao;
}

export interface Tributacao {
  id: number;
  nome: string;
  dados: DadosTributacao;
  resumo: string;
  abas: string[];
}

export interface ListaTributacoes {
  padrao: DadosTributacao | null;
  padrao_resumo: string | null;
  regime: Regime | null;
  itens: Tributacao[];
}

export type Regime = Pick<Fiscal, "op_simp_nac" | "reg_ap_trib_sn">;

/** A Sefin exige a alíquota do ISS (E0621): ME/EPP com ISS pelo Simples e ISS retido. Nos demais, vale a do município. */
export function issExigeAliquota(regime: Regime | null | undefined, t: Pick<Fiscal, "tp_ret_issqn">): boolean {
  return regime?.op_simp_nac === "3" && regime.reg_ap_trib_sn === "1" && ["2", "3"].includes(t.tp_ret_issqn);
}

/** Tributação que pede a alíquota do ISS retido na emissão (manter ou alterar). */
export interface AliquotaIss {
  origem: "tributacao" | "padrao" | "nota";
  tributacao_id: number | null;
  nome: string;
  p_aliq_iss: string | null;
  atualizada_em: string | null;
  linhas: number;
}

export interface Aba {
  id: number;
  nome: string;
  ordem: number;
  tributacao_id: number | null;
  tributacao_nome: string | null;
  tributacao_excluida: boolean;
  clientes: { documento: string; nome: string }[];
  linhas: number;
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
  exemplo?: string; // variaveis_descricao: como a variável sai hoje
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

/** Serviço da lista nacional (cTribNac, Anexo B). */
export interface Servico {
  codigo: string;
  descricao: string;
  subitem: string;
  subitem_descricao: string;
}

/** Serviço com os NBS e as sugestões de cClassTrib que o Anexo VIII correlaciona a ele. */
export interface ServicoDetalhe extends Servico {
  grupo_descricao: string;
  nbs: { codigo: string; descricao: string; c_ind_op: string[] }[];
  c_class_trib: { codigo: string; cst: string; nome: string; reducao_ibs: number; reducao_cbs: number }[];
  fontes: string[];
}

/** Consulta do serviço na prefeitura (parametrização do município no Sistema Nacional). */
export interface ConferenciaServico {
  situacao: "encontrado" | "nao_encontrado" | "nao_conferido";
  mensagem: string;
  c_trib_nac: string;
  municipio: string | null;
  /** "Florianópolis / SC" */
  municipio_nome: string | null;
  competencia: string | null;
  aliquota: string | null;
  detalhe: string | null;
  ambiente: string;
}

export interface Validacao {
  ambiente: Ambiente;
  ambiente_descricao: string;
  problemas: string[];
  /** Avisos que valem para várias linhas (ex.: serviço que a prefeitura não tem cadastrado). */
  avisos: string[];
  linhas: { linha_id: number; erros: string[]; avisos: string[] }[];
  quantidade: number;
  total: string;
  tributacao_nome?: string | null;
  aliquotas_iss: AliquotaIss[];
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
  /** Corpo completo da resposta de erro (ex.: precisa_codigo no login). */
  corpo: Record<string, unknown> | null = null;
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
    let corpo: Record<string, unknown> | null = null;
    if (tipo.includes("application/json")) {
      corpo = await resp.json().catch(() => null);
      if (corpo?.mensagem) mensagem = String(corpo.mensagem);
      detalhes = corpo?.detalhes ?? null;
      sefin = (corpo?.sefin as ErroEmissao | undefined) ?? null;
    } else {
      const texto = await resp.text().catch(() => "");
      if (texto) mensagem = `${mensagem} ${texto.slice(0, 300)}`;
    }
    // Sessão expirada ou encerrada em outro lugar: o App volta para a tela de login.
    if (resp.status === 401 && corpo?.login) window.dispatchEvent(new Event("emissor:sessao-expirada"));
    // Empresa guardada nesta aba que o usuário não acessa mais (desativada ou acesso retirado): volta à padrão.
    if (empresaAtual() && resp.status === 404 && String(corpo?.mensagem || "").startsWith("Empresa não encontrada")) {
      try {
        sessionStorage.removeItem(CHAVE_EMPRESA);
        localStorage.removeItem(CHAVE_EMPRESA);
        location.reload();
      } catch {
        /* nada */
      }
    }
    const erro = new ApiError(resp.status, mensagem, detalhes, rid, sefin);
    erro.corpo = corpo;
    throw erro;
  }
  if (tipo.includes("application/json")) return (await resp.json()) as T;
  return (await resp.blob()) as unknown as T;
}

// --- empresa da aba do navegador ---------------------------------------------------------------------------
// Cada aba guarda a sua empresa (sessionStorage); a última escolhida vale para as abas novas (localStorage).
const CHAVE_EMPRESA = "emissor:empresa";

export function empresaAtual(): string | null {
  try {
    return sessionStorage.getItem(CHAVE_EMPRESA) || localStorage.getItem(CHAVE_EMPRESA);
  } catch {
    return null;
  }
}

/** Troca a empresa desta aba e recarrega a página (tabela, notas e configurações passam a ser as dela). */
export function trocarEmpresa(id: number) {
  try {
    sessionStorage.setItem(CHAVE_EMPRESA, String(id));
    localStorage.setItem(CHAVE_EMPRESA, String(id));
  } catch {
    /* sem armazenamento: fica a empresa padrão do servidor */
  }
  location.hash = "#/emitir";
  location.reload();
}

/** Cabeçalhos de toda chamada: a empresa desta aba (o servidor confere se o usuário tem acesso). */
function cabecalhos(json: boolean): Record<string, string> {
  const h: Record<string, string> = {};
  const emp = empresaAtual();
  if (emp) h["X-Empresa"] = emp;
  if (json) h["Content-Type"] = "application/json";
  return h;
}

/** Link direto (abrir PDF, ZIP baixado pelo navegador) com a empresa desta aba. */
export function comEmpresa(url: string): string {
  const emp = empresaAtual();
  if (!emp) return url;
  return `${url}${url.includes("?") ? "&" : "?"}empresa=${encodeURIComponent(emp)}`;
}

async function req<T>(metodo: string, url: string, corpo?: unknown): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(url, {
      method: metodo,
      headers: cabecalhos(!(corpo instanceof FormData || corpo === undefined)),
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
      headers: cabecalhos(Boolean(corpo)),
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
