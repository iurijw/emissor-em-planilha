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
import { api, baixar, type Cliente, type Linha, type Status } from "../api";
import { documentoValido, formatarDocumento, moeda, parseTSV, parseValor, soDocumento, STATUS_ROTULO } from "../format";
import { EditorNomeCliente } from "../components/EditorNomeCliente";
import { AmbChip, Dialogo, ErroCaixa, Gaveta, MensagensSefin, Spinner, StatusChip, useToast } from "../ui";
import { EmissaoFluxo } from "./EmissaoFluxo";
import { ZonaArquivo } from "./Onboarding";

interface LinhaLocal extends Linha {
  chave: string; // identidade estável no cliente, inclusive antes de ter id no servidor
}

let seqChave = 0;
const novaChave = () => `l${++seqChave}`;
const vazia = (): LinhaLocal => ({ chave: novaChave(), nome: "", documento: "", valor: "", descricao: "" });

const tema: Partial<Theme> = {
  accentColor: "#0098D7",
  accentFg: "#FFFFFF",
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
  fontFamily: "'Archivo Variable', 'Segoe UI', sans-serif",
  baseFontStyle: "13px",
  headerFontStyle: "600 12px",
  editorFontSize: "13px",
  cellHorizontalPadding: 10,
  lineHeight: 1.4,
};
const MONO = "'Martian Mono Variable', Consolas, monospace";
const ERRO: Partial<Theme> = { bgCell: "#FDECEA", textDark: "#C62F24" };

type ColId = "nome" | "documento" | "valor" | "descricao" | "situacao";
const COLUNAS: (GridColumn & { id: ColId })[] = [
  { id: "nome", title: "Nome da empresa", width: 300 },
  { id: "documento", title: "CNPJ / CPF", width: 190 },
  { id: "valor", title: "Valor", width: 140 },
  { id: "descricao", title: "Descrição do serviço", width: 360, grow: 1 },
  { id: "situacao", title: "Última emissão", width: 230 },
];
const EDITAVEIS: ColId[] = ["nome", "documento", "valor", "descricao"];

function aplicarValor(ln: LinhaLocal, col: ColId, texto: string): LinhaLocal {
  if (col === "documento") return { ...ln, documento: soDocumento(texto) };
  if (col === "valor") {
    const v = parseValor(texto);
    return { ...ln, valor: v === null ? texto.trim() : v };
  }
  if (col === "nome") return { ...ln, nome: texto.trim() };
  if (col === "descricao") return { ...ln, descricao: texto.trim() };
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

function situacaoTexto(ln: LinhaLocal, cli: Cliente | undefined): { texto: string; tema?: Partial<Theme> } {
  const e = ln.ultima_emissao;
  if (e) {
    const amb = e.ambiente === "2" ? " (homolog.)" : "";
    if (e.status === "autorizada")
      return { texto: `✓ Nº ${e.numero_nfse} · ${new Date(e.dh_emissao || "").toLocaleDateString("pt-BR")}${amb}`, tema: { textDark: "#0079AD" } };
    if (e.status === "cancelada" || e.status === "substituida")
      return { texto: `Nº ${e.numero_nfse} ${STATUS_ROTULO[e.status].toLowerCase()}${amb} — ver detalhe`, tema: { textDark: "#56656F" } };
    const cod = e.erro?.mensagens?.[0]?.codigo;
    return {
      texto: `${STATUS_ROTULO[e.status]}${cod ? ` · ${cod}` : ""}${amb} — ver detalhe`,
      tema: ["rejeitada", "erro"].includes(e.status) ? { textDark: "#C62F24" } : undefined,
    };
  }
  if (ln.documento && !documentoValido(ln.documento)) return { texto: "CNPJ/CPF inválido", tema: { textDark: "#C62F24" } };
  if (ln.documento.length === 14 && !cli) return { texto: "Sem cadastro", tema: { textDark: "#8795A0" } };
  if (cli && !cli.endereco_completo) return { texto: "Cadastro sem endereço", tema: { textDark: "#8A5A00" } };
  return { texto: "" };
}

export function Emitir({ status }: { status: Status }) {
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
  const consultados = useRef(new Set<string>());

  const deServidor = (ls: Linha[]): LinhaLocal[] => ls.map((l) => ({ ...l, chave: novaChave() }));

  const carregar = useCallback(async () => {
    try {
      const r = await api.get<{ linhas: Linha[] }>("/api/tabela");
      setLinhas(deServidor(r.linhas));
      setCarregado(true);
    } catch (e) {
      setErroCarga(e);
    }
  }, []);
  useEffect(() => {
    carregar();
    document.fonts?.ready.then(() => setFontes((f) => f + 1));
  }, [carregar]);

  const salvar = useCallback(
    (estado: LinhaLocal[]) => {
      const minha = ++versao.current;
      setSalvando("salvando");
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => {
        fila.current = fila.current.then(async () => {
          try {
            const r = await api.put<{ linhas: Linha[] }>("/api/tabela", {
              linhas: estado.map(({ id, nome, documento, valor, descricao }) => ({ id, nome, documento, valor, descricao })),
            });
            setErroSalvar(null);
            if (minha === versao.current) {
              setLinhas(deServidor(r.linhas));
              setSalvando("ok");
            }
          } catch (e) {
            setErroSalvar(e);
            setSalvando("erro");
          }
        });
      }, 500);
    },
    [],
  );

  const alterar = useCallback(
    (fn: (atual: LinhaLocal[]) => LinhaLocal[]) => {
      setLinhas((atual) => {
        const novo = fn(atual);
        salvar(novo);
        return novo;
      });
    },
    [salvar],
  );

  // ---- consulta automática de CNPJs sem cadastro ----
  useEffect(() => {
    if (!carregado || !clientesQ.data || salvando !== "ok") return;
    const faltando = [
      ...new Set(
        linhas
          .map((l) => l.documento)
          .filter((d) => d.length === 14 && documentoValido(d) && !clientes.has(d) && !consultados.current.has(d)),
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
        if (nomes.size) alterar((ls) => ls.map((l) => (!l.nome && nomes.has(l.documento) ? { ...l, nome: nomes.get(l.documento)! } : l)));
        if (falhas.length)
          toast(`${falhas.length} CNPJ(s) sem dados na Receita: ${falhas[0].erro} Complete o cadastro em Clientes.`, "erro");
        qc.invalidateQueries({ queryKey: ["clientes"] });
      })
      .catch((e) => toast(`Consulta de CNPJ falhou: ${e.message}`, "erro"))
      .finally(() => setConsultando(null));
  }, [linhas, carregado, clientes, clientesQ.data, salvando, alterar, qc, toast]);

  // ---- conteúdo da grade ----
  const getCellContent = useCallback(
    ([col, row]: Item): GridCell => {
      const ln = linhas[row];
      const id = COLUNAS[col].id;
      if (!ln) return { kind: GridCellKind.Text, data: "", displayData: "", allowOverlay: false };
      if (id === "situacao") {
        const s = situacaoTexto(ln, clientes.get(ln.documento));
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
          themeOverride: { fontFamily: MONO, baseFontStyle: "12px", ...(invalido ? ERRO : {}) },
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
          themeOverride: { fontFamily: MONO, baseFontStyle: "12px", ...(ln.valor !== "" && !numero ? ERRO : {}) },
        };
      }
      const texto = id === "nome" ? ln.nome : ln.descricao;
      return { kind: GridCellKind.Text, data: texto, displayData: texto, allowOverlay: true };
    },
    [linhas, clientes],
  );

  // Seleção atual sempre fresca para os callbacks da grade.
  const selecaoRef = useRef(selecao);
  selecaoRef.current = selecao;

  const aplicarEdicoes = useCallback(
    (novos: readonly { location: Item; value: EditableGridCell }[]) => {
      alterar((atual) => {
        const r = [...atual];
        for (const { location: [c, row], value } of novos) {
          const id = COLUNAS[c]?.id;
          if (!id || !EDITAVEIS.includes(id)) continue;
          while (r.length <= row) r.push(vazia());
          r[row] = aplicarValor(r[row], id, value.kind === GridCellKind.Text ? value.data : "");
          // CNPJ já cadastrado em linha sem nome: completa o nome pelo cadastro.
          if (id === "documento" && !r[row].nome) {
            const cli = clientesRef.current.get(r[row].documento);
            if (cli) r[row] = { ...r[row], nome: cli.nome };
          }
        }
        return r;
      });
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
      });
      // Próximo dado a digitar é o valor: leva o cursor para lá (depois que o editor fechar).
      const colValor = COLUNAS.findIndex((col) => col.id === "valor");
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
      if (!alvo || COLUNAS[alvo[0]]?.id !== "nome" || cell.kind !== GridCellKind.Text) return undefined;
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
          aplicarEdicoes(alvos);
          return true;
        }
      }
      aplicarEdicoes(novos);
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
          if (x0 + dx < COLUNAS.length)
            edits.push({ location: [x0 + dx, y0 + dy], value: { kind: GridCellKind.Text, data: v, displayData: v, allowOverlay: true } });
        }),
      );
      aplicarEdicoes(edits);
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

  const onDelete = useCallback(
    (sel: GridSelection) => {
      if (sel.rows.length > 0) {
        const remover = new Set(sel.rows.toArray());
        alterar((atual) => atual.filter((_, i) => !remover.has(i)));
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
      const col = COLUNAS[c]?.id;
      if (!col) return;
      const nova: Ordem = { col, dir: ordem?.col === col && ordem.dir === 1 ? -1 : 1 };
      setOrdem(nova);
      alterar((atual) => ordenar(atual, nova, clientesRef.current));
      // Seleção por índice apontaria para outras linhas depois de reordenar.
      setSelecao({ columns: CompactSelection.empty(), rows: CompactSelection.empty() });
    },
    [ordem, alterar],
  );

  // A seta no título indica a última ordenação aplicada (linhas novas/editadas não se reordenam sozinhas).
  const colunas = useMemo(
    () => COLUNAS.map((c) => (ordem?.col === c.id ? { ...c, title: `${c.title} ${ordem.dir === 1 ? "▲" : "▼"}` } : c)),
    [ordem],
  );

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
        <button className="btn btn--pequeno" onClick={() => alterar((a) => [...a, vazia()])}>
          + Linha
        </button>
        <button className="btn btn--pequeno" disabled={!linhasMarcadas.length} onClick={() => onDelete(selecao)}>
          Excluir {linhasMarcadas.length || ""} linha(s)
        </button>
        <span className="barra__sep" />
        <button className="btn btn--pequeno" onClick={() => setImportar(true)}>
          Importar planilha
        </button>
        <button className="btn btn--pequeno" onClick={() => baixar("/api/tabela/exportar?formato=xlsx").catch((e) => toast(e.message, "erro"))}>
          Exportar Excel
        </button>
        <button className="btn btn--pequeno btn--fantasma" onClick={() => baixar("/api/tabela/exportar?formato=csv").catch((e) => toast(e.message, "erro"))}>
          CSV
        </button>
        <div className="barra__resumo">
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
            theme={tema}
            columns={colunas}
            onHeaderClicked={onHeaderClicked}
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
            onRowAppended={() => alterar((a) => [...a, vazia()])}
            onCellClicked={([c, r]) => {
              if (COLUNAS[c]?.id === "situacao" && linhas[r]?.ultima_emissao) setDetalhe(linhas[r]);
            }}
          />
        )}
      </div>
      <p className="dica-grade">
        Selecione várias células arrastando ou com <kbd>Shift</kbd>, digite e tecle <kbd>Enter</kbd> para aplicar o valor a todas.
        Cole direto do Excel com <kbd>Ctrl</kbd>+<kbd>V</kbd>, arraste o canto da seleção para repetir valores e use
        <kbd>Delete</kbd> para limpar. Marque linhas à esquerda para emitir só elas. Clique no título de uma coluna para
        ordenar (clique de novo para inverter).
      </p>

      {importar && (
        <Importar
          fechar={() => setImportar(false)}
          concluido={(ls) => {
            versao.current++;
            setLinhas(deServidor(ls));
            setOrdem(null);
            setImportar(false);
          }}
        />
      )}
      {detalhe?.ultima_emissao && (
        <Gaveta titulo={`${detalhe.nome || formatarDocumento(detalhe.documento)}`} fechar={() => setDetalhe(null)}
          rodape={<a className="btn" href={`#/emissoes`}>Abrir emissões</a>}>
          <DetalheUltima linha={detalhe} />
        </Gaveta>
      )}
      {emitindo && (
        <EmissaoFluxo
          linhaIds={emitindo.ids}
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

function Importar({ fechar, concluido }: { fechar: () => void; concluido: (l: Linha[]) => void }) {
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
      const r = await api.post<{ linhas: Linha[]; importadas: number; avisos: string[] }>(`/api/tabela/importar?modo=${modo}`, fd);
      toast(`${r.importadas} linha(s) importada(s).${r.avisos.length ? ` ${r.avisos.length} aviso(s).` : ""}`);
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
      <p style={{ marginTop: 0, color: "var(--grafite)" }}>
        Excel (.xlsx) ou CSV com as colunas <b>Nome</b>, <b>CNPJ/CPF</b>, <b>Valor</b> e <b>Descrição</b>. A primeira linha pode
        ter esses títulos; sem títulos, vale essa ordem.
      </p>
      <div className="amb-escolha" style={{ marginBottom: 16 }}>
        <button type="button" className="amb-op" aria-pressed={modo === "substituir"} onClick={() => setModo("substituir")}>
          <strong>Substituir a tabela</strong>
          <span>Apaga as linhas atuais e usa só a planilha.</span>
        </button>
        <button type="button" className="amb-op" aria-pressed={modo === "acrescentar"} onClick={() => setModo("acrescentar")}>
          <strong>Acrescentar no fim</strong>
          <span>Mantém as linhas atuais.</span>
        </button>
      </div>
      <ZonaArquivo aceitar=".xlsx,.xlsm,.csv,.txt" titulo={ocupado ? "Importando…" : "Escolha ou arraste a planilha"} dica=".xlsx ou .csv" onArquivos={enviar} />
      <div style={{ marginTop: 12 }}>
        <ErroCaixa erro={erro} titulo="Não foi possível importar" />
      </div>
    </Dialogo>
  );
}
