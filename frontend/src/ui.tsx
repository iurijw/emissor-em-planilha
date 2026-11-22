import { createContext, useCallback, useContext, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, logCliente, type ErroEmissao, type MensagemSefin } from "./api";
import { STATUS_ROTULO } from "./format";

// ---------------- Toasts ----------------
/** Avisa que um diálogo entrou na camada superior, para os avisos (toasts) voltarem para cima dele. */
const EVENTO_CAMADA = "emissor:camada-superior";

interface Toast {
  id: number;
  texto: string;
  tipo: "ok" | "erro";
}
const ToastCtx = createContext<(texto: string, tipo?: "ok" | "erro") => void>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);
  const caixa = useRef<HTMLDivElement>(null);
  const anunciar = typeof document !== "undefined" && "ariaNotify" in document;
  const mostrar = useCallback(
    (texto: string, tipo: "ok" | "erro" = "ok") => {
      const id = ++seq.current;
      setToasts((t) => [...t, { id, texto, tipo }]);
      // Leitores de tela: ariaNotify onde existe; nos outros navegadores, a região aria-live abaixo.
      if (anunciar) (document as Document & { ariaNotify(t: string): void }).ariaNotify(texto);
      setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tipo === "erro" ? 9000 : 4000);
    },
    [anunciar],
  );
  // Os avisos ficam num popover manual (camada superior). Cada diálogo que abre entra por cima; reabrir o
  // popover o põe de novo no topo, para o aviso não ficar escondido atrás do fundo do diálogo.
  useEffect(() => {
    const el = caixa.current;
    if (!el || !("showPopover" in el)) return;
    const subir = () => {
      if (el.matches(":popover-open")) el.hidePopover();
      el.showPopover();
    };
    subir();
    window.addEventListener(EVENTO_CAMADA, subir);
    return () => window.removeEventListener(EVENTO_CAMADA, subir);
  }, []);
  return (
    <ToastCtx.Provider value={mostrar}>
      {children}
      <div
        ref={caixa}
        className="toasts"
        popover="manual"
        role="region"
        aria-label="Avisos"
        aria-live={anunciar ? undefined : "polite"}
      >
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.tipo === "erro" ? "toast--erro" : ""}`}>
            <span>{t.texto}</span>
            <button type="button" aria-label="Fechar aviso" onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))}>
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

// ---------------- Erro detalhado ----------------
function detalhesComoLista(d: unknown): string[] {
  if (!d) return [];
  if (Array.isArray(d)) {
    return d.flatMap((x) => {
      if (typeof x === "string") return [x];
      if (x && typeof x === "object" && "erros" in x) {
        const o = x as { linha_id?: number; erros: string[] };
        return o.erros.map((e) => `Linha ${o.linha_id ?? "?"}: ${e}`);
      }
      if (x && typeof x === "object" && "arquivo" in x) {
        const o = x as { arquivo: string; erro: string };
        return [`${o.arquivo}: ${o.erro}`];
      }
      return [JSON.stringify(x)];
    });
  }
  return [typeof d === "string" ? d : JSON.stringify(d)];
}

export function ErroCaixa({ erro, titulo }: { erro: unknown; titulo?: string }) {
  if (!erro) return null;
  const e = erro instanceof ApiError ? erro : null;
  const msg = e?.message ?? (erro instanceof Error ? erro.message : String(erro));
  if (e?.sefin) {
    // Erro da Sefin/ADN: código, mensagem original e "Como resolver".
    return (
      <div className="erro-caixa" role="alert">
        <div>
          {titulo && <strong style={{ marginBottom: 6 }}>{titulo}</strong>}
          <MensagensSefin erro={e.sefin} />
          {e.requestId && <span className="rid">Código para suporte: {e.requestId}</span>}
        </div>
      </div>
    );
  }
  const itens = e ? detalhesComoLista(e.detalhes) : [];
  return (
    <div className="erro-caixa" role="alert">
      <div>
        <strong>{titulo ?? msg}</strong>
        {titulo && <span>{msg}</span>}
        {itens.length > 0 && (
          <ul>
            {itens.slice(0, 30).map((i, n) => (
              <li key={n}>{i}</li>
            ))}
            {itens.length > 30 && <li>… e mais {itens.length - 30}.</li>}
          </ul>
        )}
        {e?.requestId && <span className="rid">Código para suporte: {e.requestId}</span>}
      </div>
    </div>
  );
}

/** Mensagens da Sefin com código, complemento e dica de correção. */
export function MensagensSefin({ erro }: { erro: ErroEmissao }) {
  return (
    <div>
      <strong>{erro.resumo}</strong>
      {erro.http_status && !erro.resumo.includes("HTTP") ? <span className="num"> (HTTP {erro.http_status})</span> : null}
      <div style={{ marginTop: erro.mensagens.length ? 10 : 0 }}>
        {erro.mensagens.map((m: MensagemSefin, i) => (
          <div key={i} className="mensagem-sefin">
            {m.codigo && <code>{m.codigo}</code>} {m.descricao}
            {m.complemento && <div>{m.complemento}</div>}
            {m.dica && <span className="dica">{m.dica}</span>}
          </div>
        ))}
      </div>
      {erro.aviso && <p className="aviso-homologacao">{erro.aviso}</p>}
    </div>
  );
}

// ---------------- Chips ----------------
export function StatusChip({ status }: { status: string }) {
  return <span className={`chip chip--${status}`}>{STATUS_ROTULO[status] ?? status}</span>;
}
export function AmbChip({ ambiente }: { ambiente: string }) {
  return ambiente === "1" ? (
    <span className="chip chip--prod">Produção</span>
  ) : (
    <span className="chip chip--homolog">Homologação</span>
  );
}

// ---------------- Diálogo e gaveta ----------------
// <dialog> nativo aberto com showModal(): camada superior, página inerte, foco preso no diálogo, Esc e gesto de
// voltar fecham, e o foco volta para quem abriu. Quem controla se o diálogo existe continua sendo o React: o
// diálogo abre ao montar e, quando o navegador o fecha (Esc, clique fora), chama `fechar`. Se `fechar` não
// desmontar o componente (ex.: envio em andamento), o diálogo reabre.

/** Clique fora do retângulo do diálogo (no fundo). Pela posição: o alvo é o próprio <dialog> também no padding. */
function foraDoDialogo(e: PointerEvent, d: HTMLElement) {
  const r = d.getBoundingClientRect();
  return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
}

/** Foco inicial: o campo marcado com data-autofocus, senão o primeiro campo de texto do corpo. */
function focarInicio(d: HTMLDialogElement) {
  const alvo =
    d.querySelector<HTMLElement>("[data-autofocus]") ??
    d.querySelector<HTMLElement>(
      ".dialogo__corpo :is(input:not([type=hidden], [type=checkbox], [type=radio], [type=file], [disabled], [readonly]), select:not([disabled]), textarea:not([disabled], [readonly]))",
    );
  alvo?.focus();
}

export function useDialogoModal(fechar?: () => void) {
  const ref = useRef<HTMLDialogElement>(null);
  const fecharRef = useRef(fechar);
  fecharRef.current = fechar;
  useLayoutEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (!d.open) d.showModal();
    focarInicio(d);
    window.dispatchEvent(new Event(EVENTO_CAMADA));

    const aoCancelar = (e: Event) => {
      // Diálogo sem "fechar" (ex.: pagamento aprovado): Esc não fecha.
      if (!fecharRef.current) e.preventDefault();
    };
    let reabrir = 0;
    const aoFechar = () => {
      fecharRef.current?.();
      // Se continuou montado (fechar recusou ou não existe), volta a abrir.
      reabrir = requestAnimationFrame(() => {
        if (d.isConnected && !d.open) d.showModal();
      });
    };
    // Fechar clicando fora, onde o navegador ainda não tem closedby="any".
    let pressionouFora = false;
    const semClosedBy = !("closedBy" in HTMLDialogElement.prototype);
    const aoPressionar = (e: PointerEvent) => {
      pressionouFora = semClosedBy && !!fecharRef.current && e.target === d && foraDoDialogo(e, d);
    };
    const aoSoltar = (e: PointerEvent) => {
      if (pressionouFora && e.target === d && foraDoDialogo(e, d)) d.close();
      pressionouFora = false;
    };
    d.addEventListener("cancel", aoCancelar);
    d.addEventListener("close", aoFechar);
    d.addEventListener("pointerdown", aoPressionar);
    d.addEventListener("pointerup", aoSoltar);
    return () => {
      cancelAnimationFrame(reabrir);
      d.removeEventListener("cancel", aoCancelar);
      d.removeEventListener("close", aoFechar);
      d.removeEventListener("pointerdown", aoPressionar);
      d.removeEventListener("pointerup", aoSoltar);
      if (d.open) d.close();
    };
  }, []);
  return ref;
}

export function Dialogo(props: {
  titulo: ReactNode;
  children: ReactNode;
  rodape?: ReactNode;
  fechar?: () => void;
  largo?: boolean;
  /** "alertdialog" para avisos que pedem resposta ou bloqueiam (ex.: atualizando o sistema). */
  papel?: "alertdialog";
  className?: string;
}) {
  const ref = useDialogoModal(props.fechar);
  const id = useId();
  return (
    <dialog
      ref={ref}
      className={`dialogo ${props.largo ? "dialogo--largo" : ""} ${props.className ?? ""}`}
      aria-labelledby={`${id}-titulo`}
      role={props.papel}
      closedby={props.fechar ? "any" : "none"}
    >
      <div className="dialogo__topo">
        <h2 id={`${id}-titulo`}>{props.titulo}</h2>
        {props.fechar && (
          <button type="button" className="fechar" aria-label="Fechar" onClick={props.fechar}>
            ×
          </button>
        )}
      </div>
      <div className="dialogo__corpo">{props.children}</div>
      {props.rodape && <div className="dialogo__rodape">{props.rodape}</div>}
    </dialog>
  );
}

export function Gaveta(props: { titulo: ReactNode; children: ReactNode; rodape?: ReactNode; fechar: () => void }) {
  const ref = useDialogoModal(props.fechar);
  const id = useId();
  return (
    <dialog
      ref={ref}
      className="gaveta"
      aria-labelledby={`${id}-titulo`}
      closedby="any"
    >
      <div className="dialogo__topo" style={{ paddingBottom: 12, borderBottom: "1px solid var(--linha)" }}>
        <h2 id={`${id}-titulo`}>{props.titulo}</h2>
        <button type="button" className="fechar" aria-label="Fechar" onClick={props.fechar}>
          ×
        </button>
      </div>
      <div className="dialogo__corpo">{props.children}</div>
      {props.rodape && <div className="dialogo__rodape">{props.rodape}</div>}
    </dialog>
  );
}

// ---------------- Popover ancorado (menus do topo) ----------------
// popover="auto" aberto pelo botão com popovertarget: camada superior, fecha com Esc e clicando fora, e o
// navegador mantém o aria-expanded do botão. A posição vem do CSS anchor positioning; onde ele não existe,
// as variáveis --ancora-baixo e --ancora-direita (medidas do botão) fazem o mesmo papel.
const TEM_ANCORA = typeof CSS !== "undefined" && CSS.supports?.("anchor-name: --a");

export function usePopover() {
  // Ref por estado: o popover pode aparecer só depois (ex.: quando os dados chegam).
  const [el, ref] = useState<HTMLElement | null>(null);
  const [aberto, setAberto] = useState(false);
  useEffect(() => {
    if (!el) return;
    const antes = (e: Event) => {
      if ((e as ToggleEvent).newState !== "open" || TEM_ANCORA) return;
      const botao = document.querySelector<HTMLElement>(`[popovertarget="${el.id}"]`);
      if (!botao) return;
      const r = botao.getBoundingClientRect();
      el.style.setProperty("--ancora-baixo", `${Math.round(r.bottom)}px`);
      el.style.setProperty("--ancora-direita", `${Math.round(window.innerWidth - r.right)}px`);
      el.style.setProperty("--ancora-esquerda", `${Math.round(r.left)}px`);
    };
    const depois = (e: Event) => setAberto((e as ToggleEvent).newState === "open");
    el.addEventListener("beforetoggle", antes);
    el.addEventListener("toggle", depois);
    return () => {
      el.removeEventListener("beforetoggle", antes);
      el.removeEventListener("toggle", depois);
    };
  }, [el]);
  const fechar = useCallback(() => {
    if (el?.matches(":popover-open")) el.hidePopover();
  }, [el]);
  return { ref, aberto, fechar };
}

// ---------------- Captura global de erros do navegador ----------------
export function instalarCapturaDeErros() {
  window.addEventListener("error", (e) =>
    logCliente("error", `Erro no navegador: ${e.message}`, { arquivo: e.filename, linha: e.lineno }),
  );
  window.addEventListener("unhandledrejection", (e) => {
    const r = e.reason;
    if (r instanceof ApiError) return; // já tratado/exibido e registrado no servidor
    logCliente("error", `Promise rejeitada: ${r?.message ?? String(r)}`, { stack: r?.stack });
  });
}

// ---------------- Teclado ----------------
/** Enter de verdade: não o Enter que confirma a composição de um IME (japonês, chinês...). keyCode 229 cobre o Safari. */
export function ehEnter(e: { key: string; keyCode: number; nativeEvent: KeyboardEvent }) {
  return e.key === "Enter" && !e.nativeEvent.isComposing && e.keyCode !== 229;
}

// ---------------- Validação acessível ----------------
// O visual de erro usa :user-invalid (só depois de o usuário mexer no campo ou tentar enviar). Os leitores de tela
// não enxergam esse estado, então o aria-invalid acompanha o :user-invalid. Fica "false" até lá, porque Firefox e
// Safari anunciam um campo obrigatório vazio como inválido desde o carregamento.
type Campo = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
function ehCampo(el: EventTarget | null): el is Campo {
  return el instanceof HTMLInputElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement;
}
function sincronizarInvalido(el: Campo) {
  if (!el.willValidate || el.hasAttribute("data-aria-invalid-proprio")) return;
  let invalido: boolean;
  try {
    invalido = el.matches(":user-invalid");
  } catch {
    return; // navegador sem :user-invalid
  }
  el.setAttribute("aria-invalid", String(invalido));
}
export function instalarValidacaoAcessivel() {
  const aoEvento = (e: Event) => ehCampo(e.target) && sincronizarInvalido(e.target);
  for (const tipo of ["focusin", "focusout", "input", "change"]) document.addEventListener(tipo, aoEvento, true);
  // "invalid" vem antes de o navegador marcar o campo como mexido: confere no quadro seguinte.
  document.addEventListener(
    "invalid",
    (e) => {
      const el = e.target;
      if (ehCampo(el)) requestAnimationFrame(() => sincronizarInvalido(el));
    },
    true,
  );
}

export function Spinner() {
  return <span className="spinner" aria-hidden="true" />;
}

/** Copia texto; funciona também em HTTP na rede local, onde navigator.clipboard não existe. */
export async function copiar(texto: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(texto);
    return;
  }
  const area = document.createElement("textarea");
  area.value = texto;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  document.execCommand("copy");
  area.remove();
}

// ---------------- Logo ----------------
/** A logo (public/logo.svg) desenhada na página, para o canto dobrado acompanhar o tema (preto no claro, claro no
 *  escuro, onde o preto sumiria no fundo). O favicon continua sendo o arquivo. */
export function Logo({ tamanho = 30 }: { tamanho?: number }) {
  // Três folhas em lote com a grade da planilha vazada (mesmo desenho de public/logo.svg).
  const mascara = `logo-grade-${useId().replace(/:/g, "")}`;
  return (
    <svg className="logo" viewBox="0 0 128 128" width={tamanho} height={tamanho} aria-hidden="true">
      <defs>
        <mask id={mascara}>
          <rect width="128" height="128" fill="#fff" />
          {[72, 86, 100].map((y) => (
            <g key={y}>
              <rect x="60" y={y} width="14" height="10" rx="2.5" fill="#000" />
              <rect x="78" y={y} width="22" height="10" rx="2.5" fill="#000" />
            </g>
          ))}
        </mask>
      </defs>
      <g mask={`url(#${mascara})`}>
        <path d="M26 6H60L78 24V76Q78 84 70 84H26Q18 84 18 76V14Q18 6 26 6Z" fill="#B3E0F3" />
        <path d="M42 22H76L94 40V92Q94 100 86 100H42Q34 100 34 92V30Q34 22 42 22Z" fill="#66C1E7" />
        <path d="M58 38H92L110 56V108Q110 116 102 116H58Q50 116 50 108V46Q50 38 58 38Z" fill="#0098D7" />
      </g>
      <path d="M92 38L110 56H96Q92 56 92 52Z" style={{ fill: "var(--logo-canto)" }} />
    </svg>
  );
}
