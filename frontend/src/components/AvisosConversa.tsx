// Avisos e conversa com o suporte (só na hospedagem). Os dados ficam no painel da hospedagem: o navegador busca direto
// lá com um passe curto que o servidor do emissor assina (GET /api/suporte/passe), então uma mensagem do suporte não
// acorda nem mantém ligada a instância. Sem a variável EMISSOR_SUPORTE_URL (instalação própria), nada aparece.
// Cada usuário tem a própria conversa; o administrador vê e responde as de todos.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
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

function dataHora(iso: string): string {
  const d = new Date(iso);
  const hoje = d.toDateString() === new Date().toDateString();
  return d.toLocaleString("pt-BR", hoje ? { hour: "2-digit", minute: "2-digit" } : { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

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

function Bolha({ m, eu }: { m: Mensagem; eu: number }) {
  const suporte = m.autor !== "usuario";
  const minha = !suporte && m.autor_usuario === eu;
  const nome = suporte ? AUTOR[m.autor] ?? "Suporte" : minha ? "Você" : `${m.nome || "Usuário"}${m.papel === "admin" ? " (administrador)" : ""}`;
  return (
    <li className={`conversa__msg ${suporte ? "conversa__msg--suporte" : "conversa__msg--cliente"}`}>
      <span className="conversa__quem">
        {nome}
        {m.autor === "claude" && <span className="conversa__ia">assistente IA</span>}
        <time dateTime={m.em}> · {dataHora(m.em)}</time>
      </span>
      <p>
        <ComLinks texto={m.texto} />
      </p>
    </li>
  );
}

function Conversa({ fechar, eu, admin }: { fechar: () => void; eu: number; admin: boolean }) {
  const qc = useQueryClient();
  const painel = usePainel();
  const [alvo, setAlvo] = useState(eu);
  const [texto, setTexto] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const fio = useRef<HTMLOListElement>(null);
  const propria = alvo === eu;

  const msgs = useQuery({
    queryKey: ["suporte-mensagens", alvo],
    queryFn: () => painel.pedir<{ mensagens: Mensagem[] }>(`/mensagens?usuario=${alvo}`),
    enabled: painel.pronto,
    refetchInterval: 8000,
  });
  const outras = useQuery({
    queryKey: ["suporte-conversas"],
    queryFn: () => painel.pedir<{ conversas: ConversaResumo[] }>("/conversas"),
    enabled: painel.pronto && admin,
    refetchInterval: 60_000,
  });

  const lista = msgs.data?.mensagens ?? [];
  const ultima = lista.at(-1)?.id ?? 0;

  // Mensagem nova: rola para o fim e, na própria conversa, marca como lida.
  useEffect(() => {
    const el = fio.current;
    if (el) el.scrollTop = el.scrollHeight;
    if (!ultima || !propria) return;
    painel
      .pedir("/lido", { ate: ultima })
      .then(() => qc.invalidateQueries({ queryKey: ["suporte-estado"] }))
      .catch(() => undefined);
  }, [ultima, propria]);

  // Resposta do suporte chegando com a conversa aberta: leitores de tela ouvem.
  const anunciada = useRef(0);
  useEffect(() => {
    const nova = lista.at(-1);
    if (!nova) return;
    if (anunciada.current && nova.id > anunciada.current && nova.autor !== "usuario" && "ariaNotify" in document)
      (document as Document & { ariaNotify(t: string): void }).ariaNotify(`Nova mensagem do suporte: ${nova.texto.slice(0, 200)}`);
    anunciada.current = nova.id;
  }, [lista]);

  async function enviar(e?: FormEvent) {
    e?.preventDefault();
    const t = texto.trim();
    if (!t || enviando) return;
    setEnviando(true);
    setErro(null);
    try {
      await painel.pedir("/mensagens", { texto: t, usuario: alvo });
      setTexto("");
      await msgs.refetch();
    } catch (err) {
      setErro(err instanceof Error ? err.message : String(err));
    } finally {
      setEnviando(false);
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
        data-autofocus
        rows={3}
        maxLength={4000}
        placeholder={propria ? "Escreva sua mensagem…" : "Responder nesta conversa…"}
        value={texto}
        onChange={(e) => setTexto(e.target.value)}
        onKeyDown={(e) => {
          if (ehEnter(e) && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            enviar();
          }
        }}
      />
      <div className="conversa__acoes">
        <span className="conversa__dica">Ctrl+Enter envia</span>
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

  return (
    <Gaveta titulo="Conversa com o suporte" fechar={fechar} rodape={rodape}>
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
      {msgs.isLoading ? (
        <p className="conversa__vazia">
          <Spinner /> Carregando…
        </p>
      ) : msgs.error ? (
        <p className="conversa__erro" role="alert">
          {msgs.error.message}
        </p>
      ) : lista.length === 0 ? (
        <div className="conversa__vazia">
          <p>
            <strong>Fale com a gente por aqui.</strong>
          </p>
          <p>
            Dúvidas, erros na emissão ou sugestões: escreva quando quiser. A conversa fica guardada e a resposta aparece
            neste ícone, mesmo que você feche o sistema.
          </p>
        </div>
      ) : (
        <ol className="conversa__fio" ref={fio} aria-live="polite">
          {lista.map((m) => (
            <Bolha key={m.id} m={m} eu={eu} />
          ))}
        </ol>
      )}
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
