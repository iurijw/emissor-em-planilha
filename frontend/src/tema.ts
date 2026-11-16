// Modo claro/escuro. Por padrão segue o sistema; "Minha conta → Aparência" pode fixar claro ou escuro neste
// navegador. A escolha fica no <meta name="color-scheme"> (o styles.css copia para a propriedade color-scheme, e as
// cores com light-dark() seguem). O script no index.html aplica a escolha salva antes da página desenhar.
import { useSyncExternalStore } from "react";

export type Esquema = "auto" | "claro" | "escuro";

const CHAVE = "esquema-de-cor";
const META: Record<Esquema, string> = { auto: "light dark", claro: "light", escuro: "dark" };
const EVENTO = "emissor:esquema";
const midia = () => window.matchMedia("(prefers-color-scheme: dark)");

export function esquemaSalvo(): Esquema {
  try {
    const v = localStorage.getItem(CHAVE);
    return v === "light" ? "claro" : v === "dark" ? "escuro" : "auto";
  } catch {
    return "auto";
  }
}

function aplicar(e: Esquema) {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="color-scheme"]');
  if (meta) meta.content = META[e];
  // Cor da barra do navegador no celular, igual ao fundo da página.
  const cor = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (cor) cor.content = getComputedStyle(document.body).backgroundColor;
  window.dispatchEvent(new Event(EVENTO));
}

export function escolherEsquema(e: Esquema) {
  try {
    if (e === "auto") localStorage.removeItem(CHAVE);
    else localStorage.setItem(CHAVE, META[e]);
  } catch {
    // Sem armazenamento (navegação privada): vale só nesta aba.
  }
  aplicar(e);
}

/** Mantém a página em dia com outra aba e com a troca do sistema (que muda a cor da barra do navegador). */
export function instalarEsquema() {
  aplicar(esquemaSalvo());
  window.addEventListener("storage", (ev) => {
    if (ev.key === CHAVE || ev.key === null) aplicar(esquemaSalvo());
  });
  midia().addEventListener("change", () => aplicar(esquemaSalvo()));
}

function assinar(avisar: () => void) {
  const m = midia();
  window.addEventListener(EVENTO, avisar);
  m.addEventListener("change", avisar);
  return () => {
    window.removeEventListener(EVENTO, avisar);
    m.removeEventListener("change", avisar);
  };
}

/** A escolha guardada neste navegador. */
export function useEsquema(): Esquema {
  return useSyncExternalStore(assinar, esquemaSalvo);
}

/** Se a página está escura agora (para o que o CSS não alcança, como a grade desenhada em canvas). */
export function useEscuro(): boolean {
  return useSyncExternalStore(assinar, () => {
    const e = esquemaSalvo();
    return e === "escuro" || (e === "auto" && midia().matches);
  });
}
