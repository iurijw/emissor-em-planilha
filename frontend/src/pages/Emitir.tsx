// CSS da grade junto com ela: só baixado quando a tela de emissão abre.
import "@glideapps/glide-data-grid/dist/index.css";
import {
  CompactSelection,
  DataEditor,
  GridCellKind,
  type DataEditorRef,
  type EditableGridCell,
  type GridCell,
  type GridColumn,
  type GridSelection,
  type Item,
  type ProvideEditorCallbackResult,
  type ProvideEditorComponent,
  type TextCell,
  type Theme,
} from "@glideapps/glide-data-grid";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, baixar, type Aba, type Cliente, type ColunaTabela, type Linha, type Status } from "../api";
import { documentoValido, formatarDocumento, moeda, parseTSV, parseValor, soDocumento, STATUS_ROTULO } from "../format";
import { AbasPlanilha, EditorAba, useNomePadrao, useTributacoes } from "../components/Abas";
import { EditorNomeCliente } from "../components/EditorNomeCliente";
import { NotaAvulsa } from "../components/NotaAvulsa";
import { BotaoVariaveis } from "../components/Variaveis";
import { TributacaoDaAba } from "../components/TributacaoDaAba";
import { usePreferencias } from "../preferencias";
import { AmbChip, Dialogo, ErroCaixa, Gaveta, MensagensSefin, Spinner, StatusChip, useToast } from "../ui";
import { useEscuro } from "../tema";
import { EmissaoFluxo } from "./EmissaoFluxo";
import { ZonaArquivo } from "./Onboarding";

interface LinhaLocal extends Linha {
  chave: string; // identidade estável no cliente, inclusive antes de ter id no servidor
}

let seqChave = 0;
const novaChave = () => `l${++seqChave}`;
const vazia = (): LinhaLocal => ({ chave: novaChave(), nome: "", documento: "", valor: "", descricao: "" });

// A grade é desenhada em canvas: as cores não vêm do CSS, então há uma paleta para cada tema (as mesmas de styles.css).
const CORES_CLARO = {
  azulTexto: "#0079AD", grafite: "#56656F", grafiteClaro: "#8795A0", erro: "#C62F24", erroFundo: "#FDECEA", aviso: "#8A5A00",
};
const CORES_ESCURO = {
  azulTexto: "#5CC6F0", grafite: "#A0AFB8", grafiteClaro: "#74848E", erro: "#FF8A7E", erroFundo: "#34140F", aviso: "#F0BB55",
};
type Cores = typeof CORES_CLARO;
const TEMA_BASE: Partial<Theme> = {
  accentColor: "#0098D7",
  accentFg: "#FFFFFF",
  fontFamily: "'Archivo Variable', 'Segoe UI', sans-serif",
  baseFontStyle: "13px",
  headerFontStyle: "600 12px",
  editorFontSize: "13px",
  cellHorizontalPadding: 10,
  lineHeight: 1.4,
};
const TEMA_CLARO: Partial<Theme> = {
  ...TEMA_BASE,
  accentLight: "rgba(0, 152, 215, 0.13)",
  textDark: "#0B1014",
  textMedium: "#56656F",
  textLight: "#8795A0",
  textHeader: "#56656F",
  bgHeader: "#F4F8FA",
  bgHeaderHasFocus: "#E7F5FC",
  bgHeaderHovered: "#EDF4F7",
  borderColor: "#E1EBF0",
  horizontalBorderColor: "#E8F0F4",
  bgCell: "#FFFFFF",
  bgBubble: "#EEF3F6",
};
const TEMA_ESCURO: Partial<Theme> = {
  ...TEMA_BASE,
  accentLight: "rgba(0, 152, 215, 0.24)",
  textDark: "#E3EBEF",
  textMedium: "#A0AFB8",
  textLight: "#74848E",
  textHeader: "#A0AFB8",
  textBubble: "#E3EBEF",
  bgIconHeader: "#A0AFB8",
  fgIconHeader: "#0A1115",
  bgHeader: "#0D151A",
  bgHeaderHasFocus: "#0D2633",
  bgHeaderHovered: "#18232A",
  borderColor: "#233139",
  horizontalBorderColor: "#1B262D",
  bgCell: "#111A20",
  bgCellMedium: "#0D151A",
  bgBubble: "#1C2830",
  bgBubbleSelected: "#26343C",
  bgSearchResult: "#2D230C",
  drilldownBorder: "rgba(0, 0, 0, 0)",
  linkColor: "#5CC6F0",
};
const MONO = "'Martian Mono Variable', Consolas, monospace";

// Colunas do imóvel (só na aba com tributação de locação, 99.03): "loc:<chave>", depois das de sempre.
type LocId = `loc:${string}`;
type ColId = ColunaTabela | LocId;
type Coluna = GridColumn & { id: ColId };
const ehLoc = (id: ColId): id is LocId => id.startsWith("loc:");
// Ordem de fábrica; cada usuário pode arrastar os títulos para outra (fica nas preferências dele).
const COLUNAS_PADRAO: Coluna[] = [
  { id: "nome", title: "Nome da empresa", width: 300 },
  { id: "documento", title: "CNPJ / CPF", width: 190 },
  { id: "valor", title: "Valor", width: 140 },
  { id: "descricao", title: "Descrição do serviço", width: 360, grow: 1 },
  { id: "situacao", title: "Última emissão", width: 230 },
];
const EDITAVEIS: ColId[] = ["nome", "documento", "valor", "descricao"];
const editavel = (id: ColId) => ehLoc(id) || EDITAVEIS.includes(id);
const POR_ID = new Map(COLUNAS_PADRAO.map((c) => [c.id, c]));

// Mesmas chaves e títulos das colunas da planilha (emissor/nfse/locacao.py, CAMPOS).
const COLUNAS_LOCACAO: { chave: string; title: string; width: number; numero?: boolean }[] = [
  { chave: "cep", title: "CEP do imóvel", width: 120 },
  { chave: "logradouro", title: "Endereço do imóvel", width: 240 },
  { chave: "numero", title: "Nº do imóvel", width: 100 },
  { chave: "complemento", title: "Complemento do imóvel", width: 160 },
  { chave: "bairro", title: "Bairro do imóvel", width: 160 },
  { chave: "insc_imob_fisc", title: "Inscrição imobiliária", width: 170 },
  { chave: "c_cib", title: "CIB", width: 110 },
  { chave: "p_copropriedade", title: "% copropriedade", width: 130, numero: true },
  { chave: "v_tot_oper", title: "Valor total do aluguel", width: 170, numero: true },
  { chave: "venc", title: "Vencimento", width: 120 },
  { chave: "iptu", title: "IPTU", width: 110, numero: true },
  { chave: "condominio", title: "Condomínio", width: 120, numero: true },
  { chave: "emolumentos", title: "Emolumentos", width: 120, numero: true },
  { chave: "redutor_social", title: "Redutor social", width: 130, numero: true },
  { chave: "outras", title: "Outras parcelas", width: 130, numero: true },
  { chave: "outras_descricao", title: "Descrição das outras parcelas", width: 220 },
];
const COLUNAS_IMOVEL: Coluna[] = COLUNAS_LOCACAO.map((c) => ({
  id: `loc:${c.chave}` as LocId,
  title: c.title,
  width: c.width,
}));
const NUMERICAS_LOC = new Set(COLUNAS_LOCACAO.filter((c) => c.numero).map((c) => c.chave));
const SUBITEM_LOCACAO = "9903";

const textoLoc = (ln: Linha, id: LocId) => ln.locacao?.[id.slice(4)] ?? "";
const mesmaLocacao = (a: Linha, b: Linha) => {
  const x = a.locacao ?? {};
  const y = b.locacao ?? {};
  const chaves = new Set([...Object.keys(x), ...Object.keys(y)]);
  return [...chaves].every((k) => (x[k] ?? "") === (y[k] ?? ""));
};

/** Colunas na ordem salva pelo usuário (lista incompleta ou com coluna desconhecida = ordem de fábrica). */
function colunasNaOrdem(ordem: ColunaTabela[] | undefined): Coluna[] {
  if (!ordem || ordem.length !== COLUNAS_PADRAO.length || !ordem.every((id) => POR_ID.has(id))) return COLUNAS_PADRAO;
  return ordem.map((id) => POR_ID.get(id)!);
}

function aplicarValor(ln: LinhaLocal, col: ColId, texto: string): LinhaLocal {
  if (col === "documento") return { ...ln, documento: soDocumento(texto) };
  if (col === "valor") {
    const v = parseValor(texto);
    return { ...ln, valor: v === null ? texto.trim() : v };
  }
  if (col === "nome") return { ...ln, nome: texto.trim() };
  if (col === "descricao") return { ...ln, descricao: texto.trim() };
  if (ehLoc(col)) {
    const chave = col.slice(4);
    const locacao = { ...(ln.locacao ?? {}) };
    const t = texto.trim();
    if (t) locacao[chave] = t;
    else delete locacao[chave];
    return { ...ln, locacao: Object.keys(locacao).length ? locacao : null };
  }
  return ln;
}

// ---- ordenação (clique no título da coluna) ----
// Reordena as próprias linhas, como o "Classificar" do Excel: a ordem é salva no servidor
// e vale também para exportação. Células vazias ficam sempre no fim.
type Ordem = { col: ColId; dir: 1 | -1 };
const comparadorTexto = new Intl.Collator("pt-BR", { sensitivity: "base", numeric: true });

function chaveOrdem(ln: LinhaLocal, col: ColId, cli: Cliente | undefined): string | number | null {
  if (col === "valor") {
    if (ln.valor === "") return null;
    const n = Number(ln.valor);
    return Number.isFinite(n) ? n : ln.valor;
  }
  if (col === "situacao") {
    const e = ln.ultima_emissao;
    if (e?.status === "autorizada") return `0 ${e.dh_emissao ?? ""}`; // autorizadas juntas, por data
    return situacaoTexto(ln, cli).texto || null;
  }
  if (ehLoc(col)) {
    const t = textoLoc(ln, col);
    if (t === "") return null;
    const n = NUMERICAS_LOC.has(col.slice(4)) ? parseValor(t) : null;
    return n !== null ? Number(n) : t;
  }
  const t = col === "nome" ? ln.nome : col === "documento" ? ln.documento : ln.descricao;
  return t === "" ? null : t;
}

function ordenar(ls: LinhaLocal[], { col, dir }: Ordem, clientes: Map<string, Cliente>): LinhaLocal[] {
  const chaves = new Map(ls.map((l) => [l.chave, chaveOrdem(l, col, clientes.get(l.documento))]));
  return [...ls].sort((a, b) => {
    const ka = chaves.get(a.chave) ?? null;
    const kb = chaves.get(b.chave) ?? null;
    if (ka === null || kb === null) return ka === kb ? 0 : ka === null ? 1 : -1;
    if (typeof ka === "number" && typeof kb === "number") return (ka - kb) * dir;
    if (typeof ka === "number") return -1; // número antes de texto inválido na coluna Valor
    if (typeof kb === "number") return 1;
    return comparadorTexto.compare(ka, kb) * dir;
  });
}

// ---- desfazer / refazer (Ctrl+Z / Ctrl+Y) ----
// Cada passo guarda a tabela inteira de antes (as linhas são imutáveis, então é só uma referência).
// Desfazer manda esse estado ao servidor como uma edição qualquer; a linha excluída volta com o
// mesmo id e a última emissão (o servidor confere que a emissão é dela).
type Passo = { linhas: LinhaLocal[]; ordem: Ordem | null; rotulo: string };
const LIMITE_PASSOS = 100;
const CAMPOS: (keyof Linha)[] = ["id", "nome", "documento", "valor", "descricao"];
const mesmaLinha = (a: LinhaLocal | undefined, b: LinhaLocal | undefined) =>
  !!a && !!b && CAMPOS.every((k) => a[k] === b[k]) && mesmaLocacao(a, b);
const mesmoConteudo = (a: LinhaLocal[], b: LinhaLocal[]) => a.length === b.length && a.every((l, i) => mesmaLinha(l, b[i]));
const plural = (n: number, um: string, varios: string) => (n === 1 ? um : `${n} ${varios}`);

function situacaoTexto(ln: LinhaLocal, cli: Cliente | undefined, cor: Cores = CORES_CLARO): { texto: string; tema?: Partial<Theme> } {
  const e = ln.ultima_emissao;
  if (e) {
    const amb = e.ambiente === "2" ? " (homolog.)" : "";
    if (e.status === "autorizada")
      return { texto: `✓ Nº ${e.numero_nfse} · ${new Date(e.dh_emissao || "").toLocaleDateString("pt-BR")}${amb}`, tema: { textDark: cor.azulTexto } };
    if (e.status === "cancelada" || e.status === "substituida")
      return { texto: `Nº ${e.numero_nfse} ${STATUS_ROTULO[e.status].toLowerCase()}${amb} — ver detalhe`, tema: { textDark: cor.grafite } };
    const cod = e.erro?.mensagens?.[0]?.codigo;
    return {
      texto: `${STATUS_ROTULO[e.status]}${cod ? ` · ${cod}` : ""}${amb} — ver detalhe`,
      tema: ["rejeitada", "erro"].includes(e.status) ? { textDark: cor.erro } : undefined,
    };
  }
  if (ln.documento && !documentoValido(ln.documento)) return { texto: "CNPJ/CPF inválido", tema: { textDark: cor.erro } };
  if (ln.documento.length === 14 && !cli) return { texto: "Sem cadastro", tema: { textDark: cor.grafiteClaro } };
  if (cli && !cli.endereco_completo) return { texto: "Cadastro sem endereço", tema: { textDark: cor.aviso } };
  return { texto: "" };
}

export function Emitir({ status, admin }: { status: Status; admin: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const grade = useRef<DataEditorRef>(null);
  const [linhas, setLinhas] = useState<LinhaLocal[]>([]);
  const [carregado, setCarregado] = useState(false);
  const [erroCarga, setErroCarga] = useState<unknown>(null);
  const [selecao, setSelecao] = useState<GridSelection>({ columns: CompactSelection.empty(), rows: CompactSelection.empty() });
  const [salvando, setSalvando] = useState<"ok" | "salvando" | "erro">("ok");
  const [erroSalvar, setErroSalvar] = useState<unknown>(null);
  const [fontes, setFontes] = useState(0);
  const [detalhe, setDetalhe] = useState<LinhaLocal | null>(null);
  const [importar, setImportar] = useState(false);
  const [emitindo, setEmitindo] = useState<{ ids: number[]; loteId?: string } | null>(null);
  const [consultando, setConsultando] = useState<string | null>(null);
  const [ordem, setOrdem] = useState<Ordem | null>(null);
  const [abaId, setAbaId] = useState<number | null>(null);
  const [editarAba, setEditarAba] = useState<Aba | "nova" | null>(null);
  const [avulsa, setAvulsa] = useState(false);
  const [preferencias, salvarPreferencias] = usePreferencias();
  const nomePadrao = useNomePadrao();
  // Callbacks da grade (criados uma vez) leem a ordem atual por aqui.
  const colunasRef = useRef<Coluna[]>(COLUNAS_PADRAO);
  const linhasRef = useRef(linhas);
  linhasRef.current = linhas;
  const ordemRef = useRef(ordem);
  ordemRef.current = ordem;
  const definirLinhas = (ls: LinhaLocal[]) => {
    linhasRef.current = ls;
    setLinhas(ls);
  };
  const pilhaDesfazer = useRef<Passo[]>([]);
  const pilhaRefazer = useRef<Passo[]>([]);
  const [, setPassos] = useState(0); // só para redesenhar os botões quando as pilhas mudam
  const [avisoPasso, setAvisoPasso] = useState<string | null>(null);
  const limparPassos = () => {
    pilhaDesfazer.current = [];
    pilhaRefazer.current = [];
    setPassos((n) => n + 1);
  };

  const abasQ = useQuery({ queryKey: ["abas"], queryFn: () => api.get<Aba[]>("/api/abas") });
  const abas = abasQ.data ?? [];
  const aba = abas.find((a) => a.id === abaId) ?? null;
  const abaRef = useRef<number | null>(null);

  // Aba com tributação de locação de imóvel (99.03): a grade ganha as colunas do imóvel.
  const tributacoesQ = useTributacoes();
  const servicoDaAba = aba?.tributacao_id
    ? tributacoesQ.data?.itens.find((t) => t.id === aba.tributacao_id)?.dados.c_trib_nac
    : tributacoesQ.data?.padrao?.c_trib_nac;
  const abaDeLocacao = !!servicoDaAba?.startsWith(SUBITEM_LOCACAO) || (!!aba && linhas.some((l) => l.locacao));
  const BASE = useMemo(() => colunasNaOrdem(preferencias.colunas_tabela), [preferencias.colunas_tabela]);
  const COLUNAS = useMemo(() => (abaDeLocacao ? [...BASE, ...COLUNAS_IMOVEL] : BASE), [BASE, abaDeLocacao]);
  colunasRef.current = COLUNAS;

  const clientesQ = useQuery({ queryKey: ["clientes"], queryFn: () => api.get<Cliente[]>("/api/clientes") });
  const clientes = useMemo(() => new Map((clientesQ.data ?? []).map((c) => [c.documento, c])), [clientesQ.data]);
  // Referências sempre atualizadas para callbacks/editores da grade (que não são recriados a cada render).
  const clientesRef = useRef(clientes);
  clientesRef.current = clientes;
  const listaClientesRef = useRef<Cliente[]>([]);
  listaClientesRef.current = clientesQ.data ?? [];

  // ---- carga e salvamento ----
  const versao = useRef(0);
  const fila = useRef<Promise<void>>(Promise.resolve());
  const timer = useRef<number>();
  const pendente = useRef<(() => void) | null>(null);
  const consultados = useRef(new Set<string>());

  const deServidor = (ls: Linha[]): LinhaLocal[] => ls.map((l) => ({ ...l, chave: novaChave() }));

  // Carrega uma aba (null = primeira). Edições ainda no debounce são salvas antes, na aba em que foram feitas.
  const carregar = useCallback(async (id: number | null = abaRef.current) => {
    if (pendente.current) {
      window.clearTimeout(timer.current);
      pendente.current();
    }
    versao.current++; // respostas de salvamentos de outra aba não sobrescrevem esta
    await fila.current;
    try {
      const r = await api.get<{ aba_id: number; linhas: Linha[] }>(`/api/tabela${id ? `?aba_id=${id}` : ""}`);
      if (r.aba_id !== abaRef.current) limparPassos(); // o histórico é de uma aba só
      abaRef.current = r.aba_id;
      setAbaId(r.aba_id);
      definirLinhas(deServidor(r.linhas));
      setSalvando("ok");
      setCarregado(true);
      setErroCarga(null);
    } catch (e) {
      setErroCarga(e);
    }
  }, []);
  useEffect(() => {
    carregar();
    document.fonts?.ready.then(() => setFontes((f) => f + 1));
  }, [carregar]);

  function trocarAba(id: number) {
    if (id === abaRef.current) return;
    setSelecao({ columns: CompactSelection.empty(), rows: CompactSelection.empty() });
    setOrdem(null);
    setCarregado(false);
    carregar(id).then(() => qc.invalidateQueries({ queryKey: ["abas"] }));
  }

  const salvar = useCallback(
    (estado: LinhaLocal[]) => {
      const minha = ++versao.current;
      const destino = abaRef.current;
      setSalvando("salvando");
      window.clearTimeout(timer.current);
      const enviar = () => {
        pendente.current = null;
        fila.current = fila.current.then(async () => {
          try {
            const r = await api.put<{ linhas: Linha[] }>(`/api/tabela${destino ? `?aba_id=${destino}` : ""}`, {
              linhas: estado.map(({ id, nome, documento, valor, descricao, locacao, ultima_emissao }) => ({
                id,
                nome,
                documento,
                valor,
                descricao,
                locacao: locacao ?? null,
                ultima_emissao_id: ultima_emissao?.id ?? null,
              })),
            });
            setErroSalvar(null);
            if (minha === versao.current) {
              definirLinhas(deServidor(r.linhas));
              setSalvando("ok");
            }
          } catch (e) {
            setErroSalvar(e);
            setSalvando("erro");
          }
        });
      };
      pendente.current = enviar;
      timer.current = window.setTimeout(enviar, 500);
    },
    [],
  );

  // rotulo = nome do passo no "Desfazer"; null = mudança automática (nome vindo da Receita), fora do histórico.
  const alterar = useCallback(
    (fn: (atual: LinhaLocal[]) => LinhaLocal[], rotulo: string | null, ordemNova?: Ordem | null) => {
      const atual = linhasRef.current;
      const novo = fn(atual);
      const mudouOrdem = ordemNova !== undefined && ordemNova !== ordemRef.current;
      if (mesmoConteudo(atual, novo) && !mudouOrdem) return; // ex.: Enter sem mudar o texto
      if (rotulo) {
        pilhaDesfazer.current = [...pilhaDesfazer.current.slice(-(LIMITE_PASSOS - 1)), { linhas: atual, ordem: ordemRef.current, rotulo }];
        pilhaRefazer.current = [];
        setPassos((n) => n + 1);
      }
      if (ordemNova !== undefined) {
        ordemRef.current = ordemNova;
        setOrdem(ordemNova);
      }
      definirLinhas(novo);
      salvar(novo);
    },
    [salvar],
  );

  // Volta (ou refaz) um passo e seleciona as células que mudaram, como no Excel.
  const andarPasso = useCallback(
    (direcao: "desfazer" | "refazer") => {
      const de = direcao === "desfazer" ? pilhaDesfazer : pilhaRefazer;
      const para = direcao === "desfazer" ? pilhaRefazer : pilhaDesfazer;
      const passo = de.current.at(-1);
      if (!passo) return;
      const atual = linhasRef.current;
      de.current = de.current.slice(0, -1);
      para.current = [...para.current, { linhas: atual, ordem: ordemRef.current, rotulo: passo.rotulo }];
      setPassos((n) => n + 1);
      ordemRef.current = passo.ordem;
      setOrdem(passo.ordem);
      definirLinhas(passo.linhas);
      salvar(passo.linhas);
      setAvisoPasso(`${direcao === "desfazer" ? "Desfeito" : "Refeito"}: ${passo.rotulo}`);

      const novo = passo.linhas;
      const vaziaSel = { columns: CompactSelection.empty(), rows: CompactSelection.empty() };
      let x0 = Infinity, x1 = -1, y0 = Infinity, y1 = -1;
      if (atual.length === novo.length) {
        novo.forEach((l, y) =>
          colunasRef.current.forEach(({ id }, x) => {
            if (!editavel(id)) return;
            const a = atual[y];
            const valorDe = (ln: LinhaLocal) => (ehLoc(id) ? textoLoc(ln, id) : ln[id as Exclude<ColunaTabela, "situacao">]);
            if (a && valorDe(l) === valorDe(a)) return;
            x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
          }),
        );
      } else {
        // Linhas entraram ou saíram: marca a primeira posição diferente.
        const y = novo.findIndex((l, i) => !mesmaLinha(l, atual[i]));
        const alvoY = y < 0 ? Math.max(novo.length - 1, 0) : y;
        if (novo.length) [x0, x1, y0, y1] = [0, 0, alvoY, alvoY];
      }
      if (y1 < 0) setSelecao(vaziaSel);
      else {
        setSelecao({
          ...vaziaSel,
          current: { cell: [x0, y0], range: { x: x0, y: y0, width: x1 - x0 + 1, height: y1 - y0 + 1 }, rangeStack: [] },
        });
        grade.current?.scrollTo(x0, y0);
      }
    },
    [salvar],
  );
  const podeDesfazer = pilhaDesfazer.current.length > 0;
  const podeRefazer = pilhaRefazer.current.length > 0;
  const proximoDesfazer = pilhaDesfazer.current.at(-1)?.rotulo;
  const proximoRefazer = pilhaRefazer.current.at(-1)?.rotulo;

  useEffect(() => {
    if (!avisoPasso) return;
    const t = window.setTimeout(() => setAvisoPasso(null), 2500);
    return () => window.clearTimeout(t);
  }, [avisoPasso]);

  // ---- consulta automática de CNPJs sem cadastro ----
  useEffect(() => {
    if (!carregado || !clientesQ.data || salvando !== "ok") return;
    const faltando = [
      ...new Set(
        linhas
          .map((l) => l.documento)
          .filter((d) => d.length === 14 && documentoValido(d) && pendenteReceita(clientes.get(d)) && !consultados.current.has(d)),
      ),
    ];
    if (!faltando.length) return;
    faltando.forEach((d) => consultados.current.add(d));
    setConsultando(`Buscando dados de ${faltando.length} CNPJ(s) na Receita…`);
    api
      .post<{ resultados: { documento: string; ok: boolean; nome?: string; erro?: string }[] }>("/api/clientes/consultar-pendentes", {
        documentos: faltando,
      })
      .then((r) => {
        const nomes = new Map(r.resultados.filter((x) => x.ok).map((x) => [x.documento, x.nome!]));
        const falhas = r.resultados.filter((x) => !x.ok);
        if (nomes.size)
          alterar((ls) => ls.map((l) => (!l.nome && nomes.has(l.documento) ? { ...l, nome: nomes.get(l.documento)! } : l)), null);
        if (falhas.length)
          toast(`${falhas.length} CNPJ(s) sem dados na Receita: ${falhas[0].erro} Complete o cadastro em Clientes.`, "erro");
        qc.invalidateQueries({ queryKey: ["clientes"] });
      })
      .catch((e) => {
        // Outra consulta em andamento no servidor: tenta de novo na próxima alteração da tabela.
        if (e instanceof ApiError && e.status === 429) faltando.forEach((d) => consultados.current.delete(d));
        else toast(`Consulta de CNPJ falhou: ${e.message}`, "erro");
      })
      .finally(() => setConsultando(null));
  }, [linhas, carregado, clientes, clientesQ.data, salvando, alterar, qc, toast]);

  // ---- conteúdo da grade ----
  const escuro = useEscuro();
  const cores = escuro ? CORES_ESCURO : CORES_CLARO;
  const erroCelula = useMemo<Partial<Theme>>(() => ({ bgCell: cores.erroFundo, textDark: cores.erro }), [cores]);
  const getCellContent = useCallback(
    ([col, row]: Item): GridCell => {
      const ln = linhas[row];
      const id = COLUNAS[col].id;
      if (!ln) return { kind: GridCellKind.Text, data: "", displayData: "", allowOverlay: false };
      if (id === "situacao") {
        const s = situacaoTexto(ln, clientes.get(ln.documento), cores);
        return { kind: GridCellKind.Text, data: s.texto, displayData: s.texto, allowOverlay: false, readonly: true, themeOverride: s.tema, cursor: ln.ultima_emissao ? "pointer" : undefined };
      }
      if (id === "documento") {
        const invalido = ln.documento !== "" && !documentoValido(ln.documento);
        return {
          kind: GridCellKind.Text,
          data: ln.documento,
          displayData: formatarDocumento(ln.documento),
          copyData: formatarDocumento(ln.documento),
          allowOverlay: true,
          themeOverride: { fontFamily: MONO, baseFontStyle: "12px", ...(invalido ? erroCelula : {}) },
        };
      }
      if (id === "valor") {
        const numero = ln.valor !== "" && parseValor(ln.valor) !== null && Number(ln.valor) > 0;
        return {
          kind: GridCellKind.Text,
          data: ln.valor ? ln.valor.replace(".", ",") : "",
          displayData: numero ? moeda(ln.valor) : ln.valor,
          copyData: ln.valor ? ln.valor.replace(".", ",") : "",
          allowOverlay: true,
          contentAlign: "right",
          themeOverride: { fontFamily: MONO, baseFontStyle: "12px", ...(ln.valor !== "" && !numero ? erroCelula : {}) },
        };
      }
      if (ehLoc(id)) {
        const t = textoLoc(ln, id);
        const numero = NUMERICAS_LOC.has(id.slice(4));
        const invalido = numero && t !== "" && parseValor(t.replace("%", "")) === null;
        return {
          kind: GridCellKind.Text,
          data: t,
          displayData: t,
          allowOverlay: true,
          contentAlign: numero ? "right" : undefined,
          themeOverride: numero || invalido ? { ...(numero ? { fontFamily: MONO, baseFontStyle: "12px" } : {}), ...(invalido ? erroCelula : {}) } : undefined,
        };
      }
      const texto = id === "nome" ? ln.nome : ln.descricao;
      return { kind: GridCellKind.Text, data: texto, displayData: texto, allowOverlay: true };
    },
    [linhas, clientes, cores, erroCelula, COLUNAS],
  );

  // Seleção atual sempre fresca para os callbacks da grade.
  const selecaoRef = useRef(selecao);
  selecaoRef.current = selecao;

  const aplicarEdicoes = useCallback(
    (novos: readonly { location: Item; value: EditableGridCell }[], rotulo: string) => {
      alterar((atual) => {
        const r = [...atual];
        for (const { location: [c, row], value } of novos) {
          const id = colunasRef.current[c]?.id;
          if (!id || !editavel(id)) continue;
          while (r.length <= row) r.push(vazia());
          r[row] = aplicarValor(r[row], id, value.kind === GridCellKind.Text ? value.data : "");
          // CNPJ já cadastrado em linha sem nome: completa o nome pelo cadastro.
          if (id === "documento" && !r[row].nome) {
            const cli = clientesRef.current.get(r[row].documento);
            if (cli) r[row] = { ...r[row], nome: cli.nome };
          }
        }
        return r;
      }, rotulo);
    },
    [alterar],
  );

  // Autocompletar da coluna Nome: escolher um cliente preenche nome e CNPJ da linha.
  const escolherCliente = useCallback(
    (row: number, c: Cliente) => {
      alterar((atual) => {
        const r = [...atual];
        while (r.length <= row) r.push(vazia());
        r[row] = { ...r[row], nome: c.nome, documento: c.documento };
        return r;
      }, "Escolher cliente");
      // Próximo dado a digitar é o valor: leva o cursor para lá (depois que o editor fechar).
      const colValor = colunasRef.current.findIndex((col) => col.id === "valor");
      window.setTimeout(() => {
        setSelecao({
          columns: CompactSelection.empty(),
          rows: CompactSelection.empty(),
          current: { cell: [colValor, row], range: { x: colValor, y: row, width: 1, height: 1 }, rangeStack: [] },
        });
        grade.current?.focus();
      }, 0);
    },
    [alterar],
  );

  const provideEditor = useCallback(
    (cell: GridCell) => {
      const alvo = selecaoRef.current.current?.cell;
      if (!alvo || colunasRef.current[alvo[0]]?.id !== "nome" || cell.kind !== GridCellKind.Text) return undefined;
      const row = alvo[1];
      return {
        disablePadding: true,
        editor: (p: Parameters<ProvideEditorComponent<TextCell>>[0]) => (
          <EditorNomeCliente {...p} clientes={listaClientesRef.current} onEscolher={(c) => escolherCliente(row, c)} />
        ),
      } as ProvideEditorCallbackResult<GridCell>;
    },
    [escolherCliente],
  );

  // A grade chama isto para edição de célula, preenchimento (alça) e Delete. Uma edição
  // única dentro de uma faixa com várias células = usuário digitou com a faixa
  // selecionada: o valor vale para todas as células da faixa (como Ctrl+Enter no Excel).
  const onCellsEdited = useCallback(
    (novos: readonly { location: Item; value: EditableGridCell }[]) => {
      const faixa = selecaoRef.current.current?.range;
      if (novos.length === 1 && faixa && faixa.width * faixa.height > 1) {
        const [[col, row], value] = [novos[0].location, novos[0].value];
        const dentro = col >= faixa.x && col < faixa.x + faixa.width && row >= faixa.y && row < faixa.y + faixa.height;
        if (dentro) {
          const alvos: { location: Item; value: EditableGridCell }[] = [];
          for (let y = faixa.y; y < faixa.y + faixa.height; y++)
            for (let x = faixa.x; x < faixa.x + faixa.width; x++) alvos.push({ location: [x, y], value });
          aplicarEdicoes(alvos, `Preencher ${alvos.length} células`);
          return true;
        }
      }
      aplicarEdicoes(novos, novos.length === 1 ? "Editar célula" : `Alterar ${novos.length} células`);
      return true;
    },
    [aplicarEdicoes],
  );

  // Colar do Excel: cria linhas novas se o bloco colado passar do fim da tabela.
  const colar = useCallback(
    (alvo: Item, valores: readonly (readonly string[])[]) => {
      const [x0, y0] = alvo;
      const edits: { location: Item; value: EditableGridCell }[] = [];
      valores.forEach((linha, dy) =>
        linha.forEach((v, dx) => {
          if (x0 + dx < colunasRef.current.length)
            edits.push({ location: [x0 + dx, y0 + dy], value: { kind: GridCellKind.Text, data: v, displayData: v, allowOverlay: true } });
        }),
      );
      aplicarEdicoes(edits, edits.length === 1 ? "Colar" : `Colar ${edits.length} células`);
    },
    [aplicarEdicoes],
  );

  // A colagem nativa da grade usa navigator.clipboard, que o navegador só libera em HTTPS
  // ou localhost — na rede local (http://servidor:8000) ela não funcionaria. Tratamos o
  // evento "paste" direto, lendo e.clipboardData (funciona em qualquer contexto, sem pedir
  // permissão). Se o editor de célula estiver aberto, o foco está no #portal e a colagem
  // segue normal dentro do campo.
  const caixaGrade = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const aoColar = (e: ClipboardEvent) => {
      const ativo = document.activeElement;
      if (!caixaGrade.current || !ativo || !caixaGrade.current.contains(ativo)) return;
      const faixa = selecaoRef.current.current?.range;
      const texto = e.clipboardData?.getData("text/plain");
      if (!faixa || !texto) return;
      e.preventDefault();
      const valores = parseTSV(texto);
      // Um único valor colado sobre uma faixa preenche a faixa inteira (como no Excel).
      if (valores.length === 1 && valores[0].length === 1 && faixa.width * faixa.height > 1) {
        const bloco = Array.from({ length: faixa.height }, () => Array(faixa.width).fill(valores[0][0]));
        colar([faixa.x, faixa.y], bloco);
      } else colar([faixa.x, faixa.y], valores);
    };
    window.addEventListener("paste", aoColar, true);
    return () => window.removeEventListener("paste", aoColar, true);
  }, [colar]);

  // Ctrl+Z desfaz; Ctrl+Y ou Ctrl+Shift+Z refaz. Só com o foco na grade (ou em nada): num campo
  // de texto, inclusive o editor de célula aberto, vale o desfazer do próprio campo.
  useEffect(() => {
    const aoTeclar = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
      const tecla = e.key.toLowerCase();
      const direcao = tecla === "z" ? (e.shiftKey ? "refazer" : "desfazer") : tecla === "y" && !e.shiftKey ? "refazer" : null;
      if (!direcao) return;
      const ativo = document.activeElement;
      const naGrade = !!ativo && !!caixaGrade.current?.contains(ativo) && !ativo.closest("input, textarea, [contenteditable]");
      const semFoco = !ativo || ativo === document.body;
      if (!naGrade && !(semFoco && !document.querySelector("dialog[open]"))) return;
      e.preventDefault();
      andarPasso(direcao);
    };
    window.addEventListener("keydown", aoTeclar);
    return () => window.removeEventListener("keydown", aoTeclar);
  }, [andarPasso]);

  const onDelete = useCallback(
    (sel: GridSelection) => {
      if (sel.rows.length > 0) {
        const remover = new Set(sel.rows.toArray());
        alterar((atual) => atual.filter((_, i) => !remover.has(i)), `Excluir ${plural(remover.size, "linha", "linhas")}`);
        setSelecao({ columns: CompactSelection.empty(), rows: CompactSelection.empty() });
        return false;
      }
      return true; // limpa as células selecionadas (vem por onCellsEdited)
    },
    [alterar],
  );

  // 1º clique: crescente (A→Z, 0→9); 2º clique na mesma coluna: decrescente.
  const onHeaderClicked = useCallback(
    (c: number) => {
      const col = colunasRef.current[c]?.id;
      if (!col) return;
      const nova: Ordem = { col, dir: ordem?.col === col && ordem.dir === 1 ? -1 : 1 };
      alterar((atual) => ordenar(atual, nova, clientesRef.current), `Ordenar por ${colunasRef.current[c].title}`, nova);
      // Seleção por índice apontaria para outras linhas depois de reordenar.
      setSelecao({ columns: CompactSelection.empty(), rows: CompactSelection.empty() });
    },
    [ordem, alterar],
  );

  // A seta no título indica a última ordenação aplicada (linhas novas/editadas não se reordenam sozinhas).
  const colunas = useMemo(
    () => COLUNAS.map((c) => (ordem?.col === c.id ? { ...c, title: `${c.title} ${ordem.dir === 1 ? "▲" : "▼"}` } : c)),
    [ordem, COLUNAS],
  );

  // Arrastar o título de uma coluna muda a ordem (só a da tela: a planilha exportada e a importação continuam
  // Nome, CNPJ/CPF, Valor, Descrição). A ordem fica nas preferências do usuário, em qualquer navegador.
  const onColumnMoved = useCallback(
    (de: number, para: number) => {
      if (de === para) return;
      // As colunas do imóvel ficam sempre depois das de sempre, na ordem da planilha.
      const base = colunasRef.current.filter((c) => !ehLoc(c.id)).length;
      if (de >= base || para >= base) return;
      const ids = colunasRef.current.slice(0, base).map((c) => c.id as ColunaTabela);
      const [movida] = ids.splice(de, 1);
      ids.splice(para, 0, movida);
      setSelecao({ columns: CompactSelection.empty(), rows: CompactSelection.empty() });
      salvarPreferencias({ colunas_tabela: ids }).catch((e) =>
        toast(`A nova ordem das colunas não foi salva: ${e instanceof Error ? e.message : e}`, "erro"),
      );
    },
    [salvarPreferencias, toast],
  );
  const ordemDeFabrica = BASE.every((c, i) => c.id === COLUNAS_PADRAO[i].id);

  // ---- seleção e totais ----
  const linhasMarcadas = selecao.rows.toArray().filter((i) => i < linhas.length);
  const alvo = linhasMarcadas.length ? linhasMarcadas.map((i) => linhas[i]) : linhas;
  const somar = (ls: LinhaLocal[]) => ls.reduce((a, l) => a + (Number(l.valor) > 0 ? Number(l.valor) : 0), 0);
  const total = somar(linhas);
  const totalAlvo = somar(alvo);
  const prod = status.ambiente === "1";

  function iniciarEmissao() {
    const ids = alvo.map((l) => l.id).filter((x): x is number => typeof x === "number");
    if (salvando !== "ok" || ids.length !== alvo.length) {
      toast("Aguarde a tabela terminar de salvar e tente de novo.", "erro");
      return;
    }
    if (!ids.length) {
      toast("A tabela está vazia. Adicione ou importe linhas.", "erro");
      return;
    }
    setEmitindo({ ids });
  }

  if (erroCarga) return <div className="pagina"><ErroCaixa erro={erroCarga} titulo="Não foi possível carregar a tabela" /></div>;

  return (
    <div className="pagina pagina--cheia">
      <div className="cabecalho-pagina" style={{ marginBottom: 4 }}>
        <div>
          <h1 className="titulo">Notas a emitir</h1>
          <p className="subtitulo">
            A tabela fica salva entre as emissões. A competência de cada nota é a data do envio.
          </p>
        </div>
        <div className="cabecalho-pagina__acoes">
          <button
            className="btn"
            disabled={!status.pronto_para_emitir}
            onClick={() => setAvulsa(true)}
            title={!status.pronto_para_emitir ? status.problemas.join(" ") : "Emitir uma nota só, fora da tabela"}
          >
            Nota avulsa
          </button>
          {status.lote_em_andamento && !emitindo && (
            <button className="btn" onClick={() => setEmitindo({ ids: [], loteId: status.lote_em_andamento! })}>
              <Spinner /> Emissão em andamento, ver progresso
            </button>
          )}
          <button
            className={`btn btn--grande ${prod ? "btn--escuro" : "btn--primario"}`}
            disabled={!carregado || !linhas.length || !status.pronto_para_emitir || !!emitindo}
            onClick={iniciarEmissao}
            title={!status.pronto_para_emitir ? status.problemas.join(" ") : undefined}
          >
            {linhasMarcadas.length ? `Emitir ${linhasMarcadas.length} selecionada(s)` : `Emitir todas (${linhas.length})`}
            <span className="num">· {moeda(totalAlvo)}</span>
          </button>
        </div>
      </div>

      <div className="barra">
        {aba && (
          <>
            <TributacaoDaAba aba={aba} admin={admin} />
            <span className="barra__sep" />
          </>
        )}
        <div className="barra__historico" role="group" aria-label="Histórico de alterações">
          <button
            className="btn btn--pequeno btn--icone"
            disabled={!podeDesfazer}
            onClick={() => andarPasso("desfazer")}
            aria-label={proximoDesfazer ? `Desfazer: ${proximoDesfazer}` : "Desfazer"}
            title={proximoDesfazer ? `Desfazer: ${proximoDesfazer} (Ctrl+Z)` : "Nada para desfazer"}
          >
            <IconeHistorico />
          </button>
          <button
            className="btn btn--pequeno btn--icone"
            disabled={!podeRefazer}
            onClick={() => andarPasso("refazer")}
            aria-label={proximoRefazer ? `Refazer: ${proximoRefazer}` : "Refazer"}
            title={proximoRefazer ? `Refazer: ${proximoRefazer} (Ctrl+Y)` : "Nada para refazer"}
          >
            <IconeHistorico refazer />
          </button>
        </div>
        <button className="btn btn--pequeno" onClick={() => alterar((a) => [...a, vazia()], "Nova linha")}>
          + Linha
        </button>
        <button className="btn btn--pequeno" disabled={!linhasMarcadas.length} onClick={() => onDelete(selecao)}>
          Excluir {linhasMarcadas.length || ""} linha(s)
        </button>
        <span className="barra__sep" />
        <button className="btn btn--pequeno" onClick={() => setImportar(true)}>
          Importar planilha
        </button>
        <button className="btn btn--pequeno" onClick={() => baixar(`/api/tabela/exportar?formato=xlsx&aba_id=${abaId ?? ""}`).catch((e) => toast(e.message, "erro"))}>
          Exportar Excel
        </button>
        <button className="btn btn--pequeno btn--fantasma" onClick={() => baixar(`/api/tabela/exportar?formato=csv&aba_id=${abaId ?? ""}`).catch((e) => toast(e.message, "erro"))}>
          CSV
        </button>
        <span className="barra__sep" />
        <BotaoVariaveis />
        {aba && aba.clientes.length > 0 && (
          <button
            className="btn btn--pequeno btn--fantasma"
            title="Acrescenta uma linha para cada cliente da aba que ainda não está na tabela"
            onClick={() => {
              const presentes = new Set(linhas.map((l) => l.documento));
              const novas = aba.clientes.filter((c) => !presentes.has(c.documento));
              if (!novas.length) return toast("Todos os clientes da aba já estão na tabela.");
              alterar((a) => [...a, ...novas.map((c) => ({ ...vazia(), nome: c.nome, documento: c.documento }))], "Clientes da aba");
            }}
          >
            + Clientes da aba
          </button>
        )}
        <div className="barra__resumo">
          {avisoPasso && <span className="barra__passo">{avisoPasso}</span>}
          {consultando && (
            <span>
              <Spinner /> {consultando}
            </span>
          )}
          <span aria-live="polite">
            {salvando === "salvando" ? "Salvando…" : salvando === "erro" ? <span style={{ color: "var(--erro)" }}>Não salvo</span> : "Salvo"}
          </span>
          <span>
            <b className="num">{linhas.length}</b> linhas · <b className="num">{moeda(total)}</b>
          </span>
          <AmbChip ambiente={status.ambiente} />
        </div>
      </div>
      {erroSalvar != null && (
        <div style={{ marginBottom: 10 }}>
          <ErroCaixa erro={erroSalvar} titulo="A última alteração da tabela não foi salva" />
        </div>
      )}

      <div className="grade-caixa" ref={caixaGrade}>
        {!carregado ? (
          <p className="carregando">Carregando tabela…</p>
        ) : (
          <DataEditor
            key={fontes}
            ref={grade}
            theme={escuro ? TEMA_ESCURO : TEMA_CLARO}
            columns={colunas}
            onHeaderClicked={onHeaderClicked}
            onColumnMoved={onColumnMoved}
            rows={linhas.length}
            getCellContent={getCellContent}
            onCellsEdited={onCellsEdited}
            keybindings={{ paste: false }}
            provideEditor={provideEditor}
            onDelete={onDelete}
            gridSelection={selecao}
            onGridSelectionChange={setSelecao}
            rowMarkers="checkbox-visible"
            rangeSelect="multi-rect"
            columnSelect="none"
            fillHandle
            getCellsForSelection
            smoothScrollY
            rowHeight={36}
            headerHeight={36}
            width="100%"
            height="100%"
            trailingRowOptions={{ hint: "Nova linha…", sticky: true, tint: true }}
            onRowAppended={() => alterar((a) => [...a, vazia()], "Nova linha")}
            onCellClicked={([c, r]) => {
              if (colunasRef.current[c]?.id === "situacao" && linhas[r]?.ultima_emissao) setDetalhe(linhas[r]);
            }}
          />
        )}
      </div>
      <AbasPlanilha
        abas={abas}
        atual={abaId}
        trocar={trocarAba}
        criar={() => setEditarAba("nova")}
        editar={(a) => setEditarAba(a)}
      />
      <p className="dica-grade">
        Selecione várias células arrastando ou com <kbd>Shift</kbd>, digite e tecle <kbd>Enter</kbd> para aplicar o valor a todas.
        Cole direto do Excel com <kbd>Ctrl</kbd>+<kbd>V</kbd>, desfaça com <kbd>Ctrl</kbd>+<kbd>Z</kbd> (refaça com
        <kbd>Ctrl</kbd>+<kbd>Y</kbd>), arraste o canto da seleção para repetir valores e use
        <kbd>Delete</kbd> para limpar. Marque linhas à esquerda para emitir só elas. Clique no título de uma coluna para
        ordenar (clique de novo para inverter) e arraste o título para mudar a coluna de lugar.
        {!ordemDeFabrica && (
          <>
            {" "}
            <button
              type="button"
              className="btn-texto"
              onClick={() => salvarPreferencias({ colunas_tabela: COLUNAS_PADRAO.map((c) => c.id as ColunaTabela) }).catch(() => undefined)}
            >
              Voltar as colunas para a ordem original
            </button>
          </>
        )}
      </p>

      {importar && (
        <Importar
          abaId={abaId}
          fechar={() => setImportar(false)}
          concluido={(ls) => {
            // Já está salva no servidor; entra no histórico para poder voltar à tabela de antes.
            versao.current++;
            pilhaDesfazer.current = [...pilhaDesfazer.current.slice(-(LIMITE_PASSOS - 1)), { linhas: linhasRef.current, ordem: ordemRef.current, rotulo: "Importar planilha" }];
            pilhaRefazer.current = [];
            setPassos((n) => n + 1);
            definirLinhas(deServidor(ls));
            ordemRef.current = null;
            setOrdem(null);
            setImportar(false);
            qc.invalidateQueries({ queryKey: ["abas"] });
          }}
        />
      )}
      {editarAba && (
        <EditorAba
          aba={editarAba === "nova" ? null : editarAba}
          ultima={abas.length <= 1}
          admin={admin}
          fechar={() => setEditarAba(null)}
          salvo={(a, excluida) => {
            setEditarAba(null);
            qc.invalidateQueries({ queryKey: ["abas"] });
            if (excluida) {
              setCarregado(false);
              carregar(abas.find((x) => x.id !== excluida)?.id ?? null);
            } else if (a && a.id !== abaRef.current) trocarAba(a.id);
          }}
        />
      )}
      {avulsa && <NotaAvulsa fechar={() => setAvulsa(false)} admin={admin} />}
      {detalhe?.ultima_emissao && (
        <Gaveta titulo={`${detalhe.nome || formatarDocumento(detalhe.documento)}`} fechar={() => setDetalhe(null)}
          rodape={<a className="btn" href={`#/emissoes`}>Abrir emissões</a>}>
          <DetalheUltima linha={detalhe} />
        </Gaveta>
      )}
      {emitindo && (
        <EmissaoFluxo
          linhaIds={emitindo.ids}
          tributacao={aba ? aba.tributacao_nome ?? nomePadrao : undefined}
          loteExistente={emitindo.loteId}
          nomes={new Map(linhas.filter((l) => l.id).map((l) => [l.id!, l.nome || formatarDocumento(l.documento)]))}
          fechar={() => {
            setEmitindo(null);
            carregar();
            qc.invalidateQueries();
          }}
        />
      )}
    </div>
  );
}

// Seta curva de "desfazer"; espelhada para "refazer".
function IconeHistorico({ refazer = false }: { refazer?: boolean }) {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true" style={refazer ? { transform: "scaleX(-1)" } : undefined}>
      <path d="M7.5 4.5 3.5 8.5l4 4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 8.5h7.5a5 5 0 0 1 0 10H9" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function DetalheUltima({ linha }: { linha: LinhaLocal }) {
  const e = linha.ultima_emissao!;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", gap: 8 }}>
        <StatusChip status={e.status} />
        <AmbChip ambiente={e.ambiente} />
      </div>
      {e.erro && (
        <div className={e.status === "nao_enviada" ? "aviso" : "erro-caixa"}>
          <MensagensSefin erro={e.erro} />
        </div>
      )}
      <dl className="dl">
        <dt>Valor</dt>
        <dd className="num">{moeda(e.valor)}</dd>
        <dt>Descrição</dt>
        <dd>{e.descricao}</dd>
        {e.numero_nfse && (
          <>
            <dt>NFS-e nº</dt>
            <dd className="num">{e.numero_nfse}</dd>
          </>
        )}
        {e.n_dps && (
          <>
            <dt>DPS</dt>
            <dd className="num">
              série {e.serie} nº {e.n_dps}
            </dd>
          </>
        )}
      </dl>
    </div>
  );
}

/** CNPJ sem cadastro, ou cadastrado só por uma planilha importada e ainda sem endereço: vale buscar na Receita. */
function pendenteReceita(c: Cliente | undefined): boolean {
  return !c || (c.origem === "planilha" && !c.endereco_completo);
}

const EXEMPLO_IMPORTAR: [string, string, string, string][] = [
  ["Padaria Pão Dourado Ltda", "11.222.333/0001-81", "1.250,00", "Assessoria contábil de {mes}/{ano}"],
  ["Maria da Silva", "529.982.247-25", "380,00", "Consultoria tributária"],
];

/** Modelo em CSV (separado por ";", com BOM para o Excel abrir os acentos). */
function baixarModelo() {
  const linhas = [["Nome", "CNPJ/CPF", "Valor", "Descrição"], ...EXEMPLO_IMPORTAR];
  const csv = "\ufeff" + linhas.map((l) => l.map((c) => (/[;"\n]/.test(c) ? `"${c.replace(/"/g, '""')}"` : c)).join(";")).join("\r\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  a.download = "modelo-notas.csv";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function Importar({ abaId, fechar, concluido }: { abaId: number | null; fechar: () => void; concluido: (l: Linha[]) => void }) {
  const [modo, setModo] = useState<"substituir" | "acrescentar">("substituir");
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);
  const toast = useToast();
  async function enviar(files: File[]) {
    if (!files[0]) return;
    setErro(null);
    setOcupado(true);
    try {
      const fd = new FormData();
      fd.append("arquivo", files[0]);
      const r = await api.post<{ linhas: Linha[]; importadas: number; avisos: string[]; informacoes?: string[] }>(
        `/api/tabela/importar?modo=${modo}${abaId ? `&aba_id=${abaId}` : ""}`,
        fd,
      );
      toast(`${r.importadas} linha(s) importada(s).${r.avisos.length ? ` ${r.avisos.length} aviso(s).` : ""}`);
      (r.informacoes ?? []).forEach((a) => toast(a));
      r.avisos.slice(0, 3).forEach((a) => toast(a, "erro"));
      concluido(r.linhas);
    } catch (e) {
      setErro(e);
    } finally {
      setOcupado(false);
    }
  }
  return (
    <Dialogo titulo="Importar planilha" fechar={fechar}>
      <p className="importar__intro">Excel (.xlsx) ou CSV com uma nota por linha, assim:</p>
      <div className="importar__exemplo" role="region" aria-label="Exemplo de planilha">
        <table>
          <thead>
            <tr>
              <th className="importar__col">A</th>
              <th className="importar__col">B</th>
              <th className="importar__col">C</th>
              <th className="importar__col">D</th>
            </tr>
          </thead>
          <tbody>
            <tr className="importar__titulos">
              <td>Nome</td>
              <td>CNPJ/CPF</td>
              <td>Valor</td>
              <td>Descrição</td>
            </tr>
            {EXEMPLO_IMPORTAR.map((l) => (
              <tr key={l[1]}>
                <td>{l[0]}</td>
                <td className="num">{l[1]}</td>
                <td className="num importar__valor">{l[2]}</td>
                <td>{l[3]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="importar__dica">
        A linha de títulos é opcional (sem ela, vale a ordem acima). O nome pode ficar vazio: o sistema busca pelo CNPJ.{" "}
        <button type="button" className="btn-texto" onClick={baixarModelo}>
          Baixar modelo
        </button>
      </p>
      <div className="importar__modo" role="radiogroup" aria-label="O que fazer com as linhas atuais">
        <button type="button" role="radio" aria-checked={modo === "substituir"} onClick={() => setModo("substituir")}>
          Substituir a tabela
        </button>
        <button type="button" role="radio" aria-checked={modo === "acrescentar"} onClick={() => setModo("acrescentar")}>
          Acrescentar no fim
        </button>
      </div>
      <ZonaArquivo aceitar=".xlsx,.xlsm,.csv,.txt" titulo={ocupado ? "Importando…" : "Escolha ou arraste a planilha"} dica={modo === "substituir" ? "As linhas atuais desta aba serão trocadas pela planilha" : "As linhas da planilha entram depois das atuais"} onArquivos={enviar} />
      <div style={{ marginTop: 12 }}>
        <ErroCaixa erro={erro} titulo="Não foi possível importar" />
      </div>
      <details className="importar__shopee">
        <summary>Afiliado da Shopee?</summary>
        <p>
          Envie o <b>relatório de comissões</b> ou o <b>NF/RPA</b> da Central do Afiliado do jeito que veio: sai uma linha por loja com a comissão paga
          por ela. O relatório de comissões não traz o CNPJ: preencha na primeira vez, e nos meses seguintes a loja é reconhecida pelo nome.
        </p>
      </details>
    </Dialogo>
  );
}
