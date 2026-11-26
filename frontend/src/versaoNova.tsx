import { Component, type ReactNode } from "react";

// Depois de uma atualização, os arquivos de /assets têm outro nome (hash) e os da versão anterior deixam de existir.
// Uma aba aberta antes da atualização ainda tem a página antiga e, ao abrir a tela de emissão (carregada à parte),
// pede um arquivo que sumiu ("Failed to fetch dynamically imported module"). A página (index.html, sem cache) já
// aponta para os arquivos novos: basta recarregar uma vez. A guarda evita recarregar sem fim se o arquivo faltar
// de verdade (aí aparece o aviso com o botão).

const CHAVE = "emissor-recarregou-versao-nova";
const INTERVALO_MINIMO = 60_000;

/** Erro de módulo que não pôde ser baixado (Chrome, Firefox e Safari usam mensagens diferentes). */
export function ehFalhaDeModulo(erro: unknown): boolean {
  const msg = erro instanceof Error ? erro.message : String(erro ?? "");
  return /dynamically imported module|Importing a module script failed|error loading dynamically imported module|Unable to preload|Failed to load module script/i.test(
    msg,
  );
}

/** Recarrega a página uma vez por minuto no máximo. Devolve false quando a guarda impediu. */
export function recarregarParaVersaoNova(): boolean {
  try {
    const ultimo = Number(sessionStorage.getItem(CHAVE) ?? 0);
    if (Date.now() - ultimo < INTERVALO_MINIMO) return false;
    sessionStorage.setItem(CHAVE, String(Date.now()));
  } catch {
    return false; // sem sessionStorage não há como garantir que não entra em loop
  }
  window.location.reload();
  return true;
}

/** Envolve um import() dinâmico: se o arquivo sumiu, recarrega a página em vez de falhar. */
export function importarComRecarga<T>(
  importar: () => Promise<T>,
): () => Promise<T> {
  return () =>
    importar().catch((erro: unknown) => {
      if (ehFalhaDeModulo(erro) && recarregarParaVersaoNova())
        return new Promise<T>(() => {});
      throw erro;
    });
}

/** O Vite avisa quando um arquivo pré-carregado (JS ou CSS de um trecho à parte) não pôde ser baixado. */
export function instalarRecargaDeVersaoNova() {
  window.addEventListener("vite:preloadError", (e) => {
    if (recarregarParaVersaoNova()) e.preventDefault();
  });
}

/** Se mesmo assim a tela não carregar, mostra um aviso no lugar dela (sem isso a página inteira ficaria em branco). */
export class LimiteFalhaDeModulo extends Component<
  { children: ReactNode },
  { erro: unknown }
> {
  state = { erro: null as unknown };
  static getDerivedStateFromError(erro: unknown) {
    return { erro };
  }
  render() {
    if (!this.state.erro) return this.props.children;
    const versao = ehFalhaDeModulo(this.state.erro);
    return (
      <div className="erro-caixa" role="alert">
        <div>
          <strong>
            {versao
              ? "O sistema foi atualizado"
              : "Não foi possível abrir esta tela"}
          </strong>
          <p>
            {versao
              ? "Esta aba ainda está com a versão anterior. Recarregue a página para abrir a versão nova."
              : "Recarregue a página. Se continuar, baixe o arquivo de diagnóstico em Configurações."}
          </p>
          <button className="btn" onClick={() => window.location.reload()}>
            Recarregar a página
          </button>
        </div>
      </div>
    );
  }
}
