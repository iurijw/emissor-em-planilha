import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, logCliente, type ErroEmissao, type MensagemSefin } from "./api";
import { STATUS_ROTULO } from "./format";

// ---------------- Toasts ----------------
interface Toast {
  id: number;
  texto: string;
  tipo: "ok" | "erro";
}
const ToastCtx = createContext<(texto: string, tipo?: "ok" | "erro") => void>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);
  const mostrar = useCallback((texto: string, tipo: "ok" | "erro" = "ok") => {
    const id = ++seq.current;
    setToasts((t) => [...t, { id, texto, tipo }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tipo === "erro" ? 9000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={mostrar}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.tipo === "erro" ? "toast--erro" : ""}`}>
            <span>{t.texto}</span>
            <button aria-label="Fechar" onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))}>
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
function useEsc(fechar?: () => void) {
  useEffect(() => {
    if (!fechar) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && fechar();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [fechar]);
}

export function Dialogo(props: {
  titulo: ReactNode;
  children: ReactNode;
  rodape?: ReactNode;
  fechar?: () => void;
  largo?: boolean;
}) {
  useEsc(props.fechar);
  return (
    <div className="veu" onMouseDown={(e) => e.target === e.currentTarget && props.fechar?.()}>
      <div className={`dialogo ${props.largo ? "dialogo--largo" : ""}`} role="dialog" aria-modal="true">
        <div className="dialogo__topo">
          <h2>{props.titulo}</h2>
          {props.fechar && (
            <button className="fechar" aria-label="Fechar" onClick={props.fechar}>
              ×
            </button>
          )}
        </div>
        <div className="dialogo__corpo">{props.children}</div>
        {props.rodape && <div className="dialogo__rodape">{props.rodape}</div>}
      </div>
    </div>
  );
}

export function Gaveta(props: { titulo: ReactNode; children: ReactNode; rodape?: ReactNode; fechar: () => void }) {
  useEsc(props.fechar);
  return (
    <>
      <div className="gaveta-veu" onClick={props.fechar} />
      <aside className="gaveta" role="dialog" aria-modal="true">
        <div className="dialogo__topo" style={{ paddingBottom: 12, borderBottom: "1px solid var(--linha)" }}>
          <h2>{props.titulo}</h2>
          <button className="fechar" aria-label="Fechar" onClick={props.fechar}>
            ×
          </button>
        </div>
        <div className="dialogo__corpo">{props.children}</div>
        {props.rodape && <div className="dialogo__rodape">{props.rodape}</div>}
      </aside>
    </>
  );
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

export function Spinner() {
  return <span className="spinner" aria-hidden="true" />;
}
