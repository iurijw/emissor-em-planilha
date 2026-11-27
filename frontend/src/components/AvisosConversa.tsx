// Avisos e conversa com o suporte (só na hospedagem). Os dados ficam no painel da hospedagem: o navegador busca direto
// lá com um passe curto que o servidor do emissor assina (GET /api/suporte/passe), então uma mensagem do suporte não
// acorda nem mantém ligada a instância. Sem a variável EMISSOR_SUPORTE_URL (instalação própria), nada aparece.
// Cada usuário tem a própria conversa; o administrador vê e responde as de todos.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, type Sessao } from "../api";
import { ehEnter, Gaveta, Spinner, usePopover } from "../ui";

interface Passe {
  url: string;
  passe: string;
  expira: number;
}

interface Aviso {
  id: number;
  titulo: string;
  texto: string;
  tipo: "novidade" | "importante" | "urgente";
  inicio: string;
  lido: boolean;
}

interface Estado {
  avisos: Aviso[];
  conversa: { nao_lidas: number; ultima: number };
  admin: boolean;
}

interface Mensagem {
  id: number;
  usuario: number;
  autor: "usuario" | "iuri" | "claude";
  autor_usuario: number | null;
  nome: string | null;
  papel: string | null;
  texto: string;
  em: string;
}

interface ConversaResumo {
  usuario: number;
  nome: string | null;
  email: string | null;
  papel: string | null;
  ultima_texto: string;
  ultima_em: string;
}

class ErroPainel extends Error {
  constructor(
    mensagem: string,
    readonly status: number,
  ) {
    super(mensagem);
  }
}

const TIPO: Record<Aviso["tipo"], string> = { novidade: "Novidade", importante: "Importante", urgente: "Urgente" };
const AUTOR: Record<string, string> = { iuri: "Iuri, do suporte", claude: "Claude" };

function useLigado(): boolean {
  const q = useQuery({ queryKey: ["sessao"], queryFn: () => api.get<Sessao>("/api/auth/sessao"), staleTime: 60_000 });
  return Boolean(q.data?.suporte);
}

function usePasse(ligado: boolean) {
  return useQuery({
    queryKey: ["suporte-passe"],
    queryFn: () => api.get<Passe>("/api/suporte/passe"),
    enabled: ligado,
    staleTime: 20 * 60_000,
    refetchInterval: 20 * 60_000,
    retry: false,
  });
}

/** Pedido ao painel da hospedagem com o passe. Passe recusado (401) → pede outro na próxima vez. */
function usePainel() {
  const qc = useQueryClient();
  const passe = usePasse(useLigado());
  const pedir = async <T,>(caminho: string, corpo?: unknown): Promise<T> => {
    const p = passe.data;
    if (!p) throw new ErroPainel("Sem conexão com o suporte.", 0);
    let r: Response;
    try {
      r = await fetch(p.url + caminho, {
        method: corpo === undefined ? "GET" : "POST",
        headers: { Authorization: `Bearer ${p.passe}`, ...(corpo === undefined ? {} : { "Content-Type": "application/json" }) },
        body: corpo === undefined ? undefined : JSON.stringify(corpo),
        credentials: "omit",
      });
    } catch {
      throw new ErroPainel("Não foi possível falar com o suporte agora. Confira a internet e tente de novo.", 0);
    }
    const dados = await r.json().catch(() => ({}));
    if (r.status === 401) qc.invalidateQueries({ queryKey: ["suporte-passe"] });
    if (!r.ok) throw new ErroPainel(dados.erro || `O suporte respondeu com erro (HTTP ${r.status}).`, r.status);
    return dados as T;
  };
  return { pronto: Boolean(passe.data), pedir, chave: passe.data?.url ?? "" };
}

function useEstado() {
  const painel = usePainel();
  const q = useQuery({
    queryKey: ["suporte-estado", painel.chave],
    queryFn: () => painel.pedir<Estado>("/estado"),
    enabled: painel.pronto,
    // A cada 2 min com a aba à vista; parado com a aba escondida (padrão do TanStack Query).
    refetchInterval: 120_000,
    staleTime: 60_000,
    retry: 1,
  });
  return { ...q, painel };
}

/** Texto puro com os endereços https clicáveis (nada de HTML vindo do painel). */
function ComLinks({ texto }: { texto: string }) {
  const partes = texto.split(/(https:\/\/[^\s<>"]+[^\s<>".,;:!?)])/g);
  return (
    <>
      {partes.map((p, i) =>
        i % 2 ? (
          <a key={i} href={p} target="_blank" rel="noreferrer noopener">
            {p}
          </a>
        ) : (
          p
        ),
      )}
    </>
  );
}

function hora(iso: string): string {
  return new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

/** Dia da mensagem no fuso do navegador (AAAA-MM-DD), para agrupar. */
function diaDe(ms: number): string {
  return new Date(ms).toLocaleDateString("en-CA");
}

/** Divisor de dia como no WhatsApp: Hoje, Ontem, o dia da semana nesta semana e a data nas outras. */
function rotuloDia(iso: string): string {
  const d = new Date(iso);
  const chave = diaDe(d.getTime());
  const hoje = diaDe(Date.now());
  const dias = Math.round((Date.parse(hoje) - Date.parse(chave)) / 864e5);
  if (dias === 0) return "Hoje";
  if (dias === 1) return "Ontem";
  if (dias > 1 && dias < 7) {
    const s = d.toLocaleDateString("pt-BR", { weekday: "long" });
    return s[0].toUpperCase() + s.slice(1);
  }
  return d.toLocaleDateString("pt-BR", { day: "numeric", month: "long", ...(chave.slice(0, 4) === hoje.slice(0, 4) ? {} : { year: "numeric" }) });
}

/** Mensagens agrupadas por dia, na ordem da conversa. */
function porDia(lista: Mensagem[]): { chave: string; rotulo: string; mensagens: Mensagem[] }[] {
  const dias: { chave: string; rotulo: string; mensagens: Mensagem[] }[] = [];
  for (const m of lista) {
    const chave = diaDe(Date.parse(m.em));
    const ultimo = dias.at(-1);
    if (ultimo?.chave === chave) ultimo.mensagens.push(m);
    else dias.push({ chave, rotulo: rotuloDia(m.em), mensagens: [m] });
  }
  return dias;
}

/** Junta mensagens sem repetir (a mesma pode vir no envio e na busca seguinte), em ordem. */
function juntar(a: Mensagem[], b: Mensagem[]): Mensagem[] {
  const ids = new Set(a.map((m) => m.id));
  const novas = b.filter((m) => !ids.has(m.id));
  return novas.length ? [...a, ...novas].sort((x, y) => x.id - y.id) : a;
}

const POR_PAGINA = 40;
/** Distância do fim (px) que ainda conta como "no fim" da conversa. */
const PERTO_DO_FIM = 48;

function Sino() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
      <g fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M5 13.4V9a5 5 0 0 1 10 0v4.4l1.3 1.6H3.7z" />
        <path d="M8.3 17.2a1.8 1.8 0 0 0 3.4 0" />
      </g>
    </svg>
  );
}

function Balao() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
      <path
        d="M3.5 5.6a2.1 2.1 0 0 1 2.1-2.1h8.8a2.1 2.1 0 0 1 2.1 2.1v5.8a2.1 2.1 0 0 1-2.1 2.1H9.2l-3.7 3v-3a2 2 0 0 1-2-2.1z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Sino no topo: os avisos do suporte numa lista como a das atualizações, os novos em cima. */
export function Avisos() {
  const pop = usePopover();
  const qc = useQueryClient();
  const { data, painel } = useEstado();
  const marcados = useRef(new Set<number>());
  const novos = data?.avisos.filter((a) => !a.lido) ?? [];

  // Abriu a lista: os avisos à vista contam como lidos (continuam na lista, apagados).
  useEffect(() => {
    if (!pop.aberto || !data) return;
    const ids = data.avisos.filter((a) => !a.lido && !marcados.current.has(a.id)).map((a) => a.id);
    if (!ids.length) return;
    for (const id of ids) marcados.current.add(id);
    painel
      .pedir("/avisos/lidos", { ids })
      .catch(() => ids.forEach((id) => marcados.current.delete(id)));
  }, [pop.aberto, data, painel]);

  // Fechou: atualiza o ponto do ícone.
  useEffect(() => {
    if (!pop.aberto && marcados.current.size) qc.invalidateQueries({ queryKey: ["suporte-estado"] });
  }, [pop.aberto, qc]);

  if (!data) return null;
  const titulo = novos.length ? `${novos.length === 1 ? "1 aviso novo" : `${novos.length} avisos novos`} do suporte` : "Avisos do suporte";
  return (
    <span className="att sup-avisos">
      <button className={`ic-att ic-avisos ${novos.length ? "ic-att--nova" : ""}`} title={titulo} aria-label={titulo} popovertarget="avisos-pop">
        <Sino />
        {novos.length > 0 && <span className="ic-att__ponto" />}
      </button>
      <dialog className="att-pop avisos-pop" id="avisos-pop" popover="auto" aria-label="Avisos" ref={pop.ref}>
        {pop.aberto && (
          <>
            <div className="att-pop__cab">
              <h2>Avisos</h2>
              <p>{data.avisos.length ? "Recados do suporte do Emissor em Planilha." : "Nenhum aviso por enquanto."}</p>
            </div>
            {data.avisos.length > 0 && (
              <ol className="lt">
                {data.avisos.map((a) => {
                  const novo = !a.lido && !marcados.current.has(a.id);
                  return (
                    <li key={a.id} className={`lt__item aviso-item ${a.lido ? "lt__item--antiga" : "lt__item--nova"} aviso-item--${a.tipo}`}>
                      <div className="lt__cab">
                        {a.tipo !== "novidade" && <span className={`aviso-selo aviso-selo--${a.tipo}`}>{TIPO[a.tipo]}</span>}
                        <strong className="aviso-item__titulo">{a.titulo}</strong>
                        {novo && <span className="lt__selo">Novo</span>}
                        <span className="lt__d">{new Date(a.inicio).toLocaleDateString("pt-BR")}</span>
                      </div>
                      <p className="aviso-item__texto">
                        <ComLinks texto={a.texto} />
                      </p>
                    </li>
                  );
                })}
              </ol>
            )}
          </>
        )}
      </dialog>
    </span>
  );
}

/** Aviso urgente ainda não lido: faixa abaixo da do ambiente, como a da assinatura. */
export function FaixaAvisoUrgente() {
  const qc = useQueryClient();
  const { data, painel } = useEstado();
  const [fechando, setFechando] = useState<number | null>(null);
  const a = data?.avisos.find((x) => x.tipo === "urgente" && !x.lido && x.id !== fechando);
  if (!a) return null;
  return (
    <div className="faixa-assinatura faixa-assinatura--encerrado faixa-aviso" role="alert">
      <p>
        <strong>{a.titulo}.</strong> <ComLinks texto={a.texto} />
      </p>
      <button
        className="btn btn--pequeno"
        onClick={() => {
          setFechando(a.id);
          painel.pedir("/avisos/lidos", { ids: [a.id] }).finally(() => qc.invalidateQueries({ queryKey: ["suporte-estado"] }));
        }}
      >
        Entendi
      </button>
    </div>
  );
}

function SetaBaixo() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
      <path d="M5 8l5 5 5-5" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Bolha({ m, eu }: { m: Mensagem; eu: number }) {
  const suporte = m.autor !== "usuario";
  const minha = !suporte && m.autor_usuario === eu;
  const nome = suporte ? AUTOR[m.autor] ?? "Suporte" : minha ? "Você" : `${m.nome || "Usuário"}${m.papel === "admin" ? " (administrador)" : ""}`;
  return (
    <li className={`conversa__msg ${suporte ? "conversa__msg--suporte" : "conversa__msg--cliente"}`}>
      <span className="conversa__quem">
        {nome}
        {m.autor === "claude" && <span className="conversa__ia">assistente IA</span>}
        <time dateTime={m.em} title={new Date(m.em).toLocaleString("pt-BR")}>
          {" "}
          · {hora(m.em)}
        </time>
      </span>
      <p>
        <ComLinks texto={m.texto} />
      </p>
    </li>
  );
}

type Rolagem = { modo: "fim" | "antigas" | "novas"; altura: number; topo: number; noFim: boolean };

function Conversa({ fechar, eu, admin }: { fechar: () => void; eu: number; admin: boolean }) {
  const qc = useQueryClient();
  const painel = usePainel();
  const painelRef = useRef(painel);
  painelRef.current = painel;
  const [alvo, setAlvo] = useState(eu);
  const [texto, setTexto] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [lista, setLista] = useState<Mensagem[]>([]);
  const [mais, setMais] = useState(false);
  const [carga, setCarga] = useState<"carregando" | "pronta" | "erro">("carregando");
  const [erroCarga, setErroCarga] = useState("");
  const [antigas, setAntigas] = useState(false);
  const [noFim, setNoFim] = useState(true);
  const [novas, setNovas] = useState(0);
  const [anuncio, setAnuncio] = useState("");
  const fio = useRef<HTMLDivElement>(null);
  const topo = useRef<HTMLParagraphElement>(null);
  const campo = useRef<HTMLTextAreaElement>(null);
  const rolagem = useRef<Rolagem | null>(null);
  const antigasRef = useRef(false);
  const listaRef = useRef(lista);
  listaRef.current = lista;
  const alvoRef = useRef(alvo);
  alvoRef.current = alvo;
  const propria = alvo === eu;

  const outras = useQuery({
    queryKey: ["suporte-conversas"],
    queryFn: () => painel.pedir<{ conversas: ConversaResumo[] }>("/conversas"),
    enabled: painel.pronto && admin,
    refetchInterval: 60_000,
  });

  /** Guarda onde a rolagem estava antes de a lista mudar; o efeito de layout abaixo ajusta depois de desenhar. */
  const prepararRolagem = (modo: Rolagem["modo"]) => {
    const el = fio.current;
    rolagem.current = {
      modo,
      altura: el?.scrollHeight ?? 0,
      topo: el?.scrollTop ?? 0,
      noFim: !el || el.scrollTop + el.clientHeight >= el.scrollHeight - PERTO_DO_FIM,
    };
  };

  useLayoutEffect(() => {
    const el = fio.current;
    const r = rolagem.current;
    if (!el || !r) return;
    rolagem.current = null;
    // Mensagens anteriores entraram em cima: o que estava à vista fica no mesmo lugar.
    if (r.modo === "antigas") el.scrollTop = el.scrollHeight - r.altura + r.topo;
    else if (r.modo === "fim" || r.noFim) {
      el.scrollTop = el.scrollHeight;
      setNovas(0);
    }
  }, [lista, carga]);

  // Abriu a conversa (ou trocou de pessoa): as mensagens mais novas, já rolado para o fim.
  useEffect(() => {
    if (!painel.pronto) return;
    let vivo = true;
    setCarga("carregando");
    setLista([]);
    setMais(false);
    setNovas(0);
    painelRef.current
      .pedir<{ mensagens: Mensagem[]; mais?: boolean }>(`/mensagens?usuario=${alvo}&limite=${POR_PAGINA}`)
      .then((d) => {
        if (!vivo) return;
        prepararRolagem("fim");
        setLista(d.mensagens);
        setMais(Boolean(d.mais));
        setCarga("pronta");
      })
      .catch((err: unknown) => {
        if (!vivo) return;
        setErroCarga(err instanceof Error ? err.message : String(err));
        setCarga("erro");
      });
    return () => {
      vivo = false;
    };
  }, [alvo, painel.pronto]);

  // Mensagens novas a cada 8 s, só com a aba à vista: só as que vieram depois da última.
  useEffect(() => {
    if (carga !== "pronta") return;
    const buscar = async () => {
      if (document.visibilityState !== "visible") return;
      const quem = alvoRef.current;
      const ultima = listaRef.current.at(-1)?.id ?? 0;
      try {
        const d = await painelRef.current.pedir<{ mensagens: Mensagem[] }>(`/mensagens?usuario=${quem}&depois=${ultima}`);
        if (quem !== alvoRef.current || !d.mensagens.length) return;
        const chegando = d.mensagens.filter((m) => m.id > ultima);
        if (!chegando.length) return;
        prepararRolagem("novas");
        if (!rolagem.current?.noFim) setNovas((n) => n + chegando.length);
        setLista((l) => juntar(l, chegando));
      } catch {
        // Fica para a próxima volta.
      }
    };
    const t = setInterval(buscar, 8000);
    document.addEventListener("visibilitychange", buscar);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", buscar);
    };
  }, [carga]);

  // Rolou até perto do início: busca a página anterior.
  const carregarAntigas = useCallback(async () => {
    const primeira = listaRef.current[0]?.id;
    if (!primeira || antigasRef.current) return;
    const quem = alvoRef.current;
    antigasRef.current = true;
    setAntigas(true);
    try {
      const d = await painelRef.current.pedir<{ mensagens: Mensagem[]; mais?: boolean }>(
        `/mensagens?usuario=${quem}&limite=${POR_PAGINA}&antes=${primeira}`,
      );
      if (quem !== alvoRef.current) return;
      prepararRolagem("antigas");
      setLista((l) => juntar(l, d.mensagens));
      setMais(Boolean(d.mais) && d.mensagens.length > 0);
    } catch {
      // Tenta de novo quando a pessoa rolar outra vez.
    } finally {
      antigasRef.current = false;
      setAntigas(false);
    }
  }, []);

  useEffect(() => {
    const raiz = fio.current;
    const alvoTopo = topo.current;
    if (!raiz || !alvoTopo || !mais || antigas) return;
    const obs = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && carregarAntigas(), {
      root: raiz,
      rootMargin: "320px 0px 0px 0px",
    });
    obs.observe(alvoTopo);
    return () => obs.disconnect();
  }, [mais, antigas, carga, carregarAntigas]);

  const ultima = lista.at(-1)?.id ?? 0;

  // Mensagem nova na própria conversa: marca como lida.
  useEffect(() => {
    if (!ultima || !propria) return;
    painelRef.current
      .pedir("/lido", { ate: ultima })
      .then(() => qc.invalidateQueries({ queryKey: ["suporte-estado"] }))
      .catch(() => undefined);
  }, [ultima, propria, qc]);

  // Resposta do suporte chegando com a conversa aberta: leitores de tela ouvem (só as novas, não as antigas que
  // entram em cima ao rolar).
  const anunciada = useRef(0);
  useEffect(() => {
    const nova = lista.at(-1);
    if (!nova) return;
    if (anunciada.current && nova.id > anunciada.current && nova.autor !== "usuario") {
      const t = `Nova mensagem do suporte: ${nova.texto.slice(0, 200)}`;
      if ("ariaNotify" in document) (document as Document & { ariaNotify(t: string): void }).ariaNotify(t);
      else setAnuncio(t);
    }
    anunciada.current = Math.max(anunciada.current, nova.id);
  }, [lista]);

  function aoRolar() {
    const el = fio.current;
    if (!el) return;
    const perto = el.scrollTop + el.clientHeight >= el.scrollHeight - PERTO_DO_FIM;
    setNoFim(perto);
    if (perto) setNovas(0);
  }

  function irAoFim() {
    const el = fio.current;
    if (!el) return;
    const suave = !matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollTo({ top: el.scrollHeight, behavior: suave ? "smooth" : "auto" });
    setNovas(0);
  }

  async function enviar(e?: FormEvent) {
    e?.preventDefault();
    const t = texto.trim();
    if (!t || enviando) return;
    setEnviando(true);
    setErro(null);
    try {
      const r = await painel.pedir<{ mensagem?: Mensagem }>("/mensagens", { texto: t, usuario: alvo });
      setTexto("");
      prepararRolagem("fim");
      if (r.mensagem) setLista((l) => juntar(l, [r.mensagem as Mensagem]));
      else setLista((l) => [...l]);
    } catch (err) {
      setErro(err instanceof Error ? err.message : String(err));
    } finally {
      setEnviando(false);
      campo.current?.focus();
    }
  }

  const outrasConversas = (outras.data?.conversas ?? []).filter((c) => c.usuario !== eu);
  const rodape: ReactNode = (
    <form className="conversa__escrever" onSubmit={enviar}>
      <label className="sr-only" htmlFor="conversa-texto">
        Mensagem
      </label>
      <textarea
        id="conversa-texto"
        ref={campo}
        data-autofocus
        rows={2}
        maxLength={4000}
        enterKeyHint="send"
        aria-describedby="conversa-dica"
        placeholder={propria ? "Escreva sua mensagem…" : "Responder nesta conversa…"}
        value={texto}
        onChange={(e) => setTexto(e.target.value)}
        onKeyDown={(e) => {
          // Enter envia; Shift+Enter quebra a linha. Enter que confirma um caractere do IME não envia.
          if (e.key !== "Enter" || e.shiftKey) return;
          e.preventDefault();
          if (ehEnter(e)) enviar();
        }}
      />
      <div className="conversa__acoes">
        <span className="conversa__dica" id="conversa-dica">
          Enter envia. Shift+Enter pula a linha.
        </span>
        <button className="btn btn--primario" type="submit" disabled={enviando || !texto.trim()}>
          {enviando && <Spinner />} Enviar
        </button>
      </div>
      {erro && (
        <p className="conversa__erro" role="alert">
          {erro}
        </p>
      )}
    </form>
  );

  const vazia = carga === "pronta" && lista.length === 0;
  return (
    <Gaveta titulo="Conversa com o suporte" fechar={fechar} rodape={rodape}>
      <div className="conversa">
        {admin && outrasConversas.length > 0 && (
          <label className="campo conversa__de">
            <span>Conversa de</span>
            <select value={alvo} onChange={(e) => setAlvo(Number(e.target.value))}>
              <option value={eu}>Você</option>
              {outrasConversas.map((c) => (
                <option key={c.usuario} value={c.usuario}>
                  {c.nome || c.email || `Usuário ${c.usuario}`}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="conversa__caixa">
          <div className="conversa__fio" ref={fio} onScroll={aoRolar} tabIndex={0} aria-label="Mensagens">
            {carga === "carregando" ? (
              <p className="conversa__vazia">
                <Spinner /> Carregando…
              </p>
            ) : carga === "erro" ? (
              <p className="conversa__erro" role="alert">
                {erroCarga}
              </p>
            ) : vazia ? (
              <div className="conversa__vazia">
                <p>
                  <strong>Fale com a gente por aqui.</strong>
                </p>
                <p>
                  Dúvidas, erros na emissão ou sugestões: escreva quando quiser. A conversa fica guardada e a resposta
                  aparece neste ícone, mesmo que você feche o sistema.
                </p>
              </div>
            ) : (
              <>
                <p className="conversa__topo" ref={topo}>
                  {antigas ? (
                    <>
                      <Spinner /> Carregando mensagens anteriores…
                    </>
                  ) : mais ? (
                    ""
                  ) : (
                    "Início da conversa"
                  )}
                </p>
                {porDia(lista).map((dia) => (
                  <section key={dia.chave} className="conversa__dia" aria-label={dia.rotulo}>
                    <h3 className="conversa__data">
                      <span>{dia.rotulo}</span>
                    </h3>
                    <ol className="conversa__msgs">
                      {dia.mensagens.map((m) => (
                        <Bolha key={m.id} m={m} eu={eu} />
                      ))}
                    </ol>
                  </section>
                ))}
              </>
            )}
          </div>
          {(!noFim || novas > 0) && carga === "pronta" && !vazia && (
            <button
              type="button"
              className="conversa__ir-fim"
              onClick={irAoFim}
              aria-label={novas ? `${novas === 1 ? "1 mensagem nova" : `${novas} mensagens novas`}: ir para o fim` : "Ir para as últimas mensagens"}
              title="Ir para as últimas mensagens"
            >
              <SetaBaixo />
              {novas > 0 && <span className="conversa__ir-fim-n num">{novas > 9 ? "9+" : novas}</span>}
            </button>
          )}
        </div>
        <p className="sr-only" aria-live="polite">
          {anuncio}
        </p>
      </div>
    </Gaveta>
  );
}

/** Ícone da conversa no topo, com o número de respostas não lidas. */
export function BotaoConversa({ usuarioId }: { usuarioId: number }) {
  const [aberta, setAberta] = useState(false);
  const { data } = useEstado();
  if (!data) return null;
  const n = data.conversa.nao_lidas;
  const titulo = n ? `Conversa com o suporte: ${n === 1 ? "1 resposta nova" : `${n} respostas novas`}` : "Conversa com o suporte";
  return (
    <>
      <button className={`ic-att ic-conversa ${n ? "ic-att--nova" : ""}`} title={titulo} aria-label={titulo} onClick={() => setAberta(true)}>
        <Balao />
        {n > 0 && <span className="ic-conversa__n num">{n > 9 ? "9+" : n}</span>}
      </button>
      {aberta && <Conversa fechar={() => setAberta(false)} eu={usuarioId} admin={data.admin} />}
    </>
  );
}
