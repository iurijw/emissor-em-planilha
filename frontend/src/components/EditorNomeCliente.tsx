import { GridCellKind, type ProvideEditorComponent, type TextCell } from "@glideapps/glide-data-grid";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Cliente } from "../api";
import { formatarDocumento } from "../format";

/** Minúsculas e sem acento, para comparar nomes. */
export function normalizarBusca(s: string): string {
  return (s || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

/** Clientes que combinam com o texto: todas as palavras no nome, ou os dígitos no CNPJ/CPF. */
export function buscarClientes(clientes: Cliente[], texto: string, limite = 8): Cliente[] {
  const t = normalizarBusca(texto);
  if (t.length < 2) return [];
  const palavras = t.split(" ");
  const digitos = texto.replace(/\D/g, "");
  const achados: { c: Cliente; peso: number }[] = [];
  for (const c of clientes) {
    const nome = normalizarBusca(c.nome);
    let peso = -1;
    if (nome.startsWith(t)) peso = 0;
    else if (palavras.every((p) => nome.includes(p))) peso = 1;
    else if (digitos.length >= 3 && c.documento.includes(digitos)) peso = 2;
    if (peso >= 0) achados.push({ c, peso });
  }
  achados.sort((a, b) => a.peso - b.peso || a.c.nome.localeCompare(b.c.nome, "pt-BR"));
  return achados.slice(0, limite).map((x) => x.c);
}

type Props = Parameters<ProvideEditorComponent<TextCell>>[0] & {
  clientes: Cliente[];
  onEscolher: (c: Cliente) => void;
};

/**
 * Editor da coluna "Nome da empresa": texto livre com sugestões do cadastro de clientes.
 * A lista fica dentro da caixa do editor (a grade a faz crescer), então clicar numa
 * sugestão não conta como "clique fora".
 */
export function EditorNomeCliente(p: Props) {
  const celula = p.value;
  const [texto, setTexto] = useState(p.initialValue ?? celula.data);
  const [ativo, setAtivo] = useState(-1);
  const input = useRef<HTMLInputElement>(null);
  const sugestoes = useMemo(() => buscarClientes(p.clientes, texto), [p.clientes, texto]);

  const publicar = (t: string) => p.onChange({ ...celula, kind: GridCellKind.Text, data: t, displayData: t });

  useEffect(() => {
    // Quando a edição começa digitando, a grade só conhece o 1º caractere; registra-o.
    if (p.initialValue !== undefined) publicar(p.initialValue);
    const el = input.current;
    if (el) {
      el.focus();
      const fim = el.value.length;
      el.setSelectionRange(fim, fim);
    }
  }, []);

  function escolher(c: Cliente) {
    p.onEscolher(c); // aplica nome + CNPJ e posiciona o cursor na coluna Valor
    p.onFinishedEditing(undefined, [0, 0]);
  }

  function aoTeclar(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown" && sugestoes.length) {
      e.preventDefault();
      e.stopPropagation();
      setAtivo((a) => Math.min(a + 1, sugestoes.length - 1));
    } else if (e.key === "ArrowUp" && sugestoes.length) {
      e.preventDefault();
      e.stopPropagation();
      setAtivo((a) => Math.max(a - 1, -1));
    } else if ((e.key === "Enter" || e.key === "Tab") && !e.shiftKey) {
      const exato = sugestoes.find((c) => normalizarBusca(c.nome) === normalizarBusca(texto));
      const alvo = ativo >= 0 ? sugestoes[ativo] : exato;
      if (alvo) {
        e.preventDefault();
        e.stopPropagation();
        escolher(alvo);
      }
      // sem sugestão escolhida: a grade salva o texto digitado normalmente
    }
  }

  return (
    <div className="editor-nome">
      <input
        ref={input}
        className="editor-nome__input"
        value={texto}
        aria-autocomplete="list"
        aria-expanded={sugestoes.length > 0}
        aria-activedescendant={ativo >= 0 ? `sug-${sugestoes[ativo]?.documento}` : undefined}
        onChange={(e) => {
          setTexto(e.target.value);
          setAtivo(-1);
          publicar(e.target.value);
        }}
        onKeyDown={aoTeclar}
      />
      {sugestoes.length > 0 && (
        <ul className="editor-nome__lista" role="listbox">
          {sugestoes.map((c, i) => (
            <li
              key={c.documento}
              id={`sug-${c.documento}`}
              role="option"
              aria-selected={i === ativo}
              className={i === ativo ? "ativo" : undefined}
              onMouseEnter={() => setAtivo(i)}
              onMouseDown={(e) => {
                e.preventDefault();
                escolher(c);
              }}
            >
              <span className="editor-nome__nome">{c.nome}</span>
              <span className="editor-nome__doc num">{formatarDocumento(c.documento)}</span>
              {c.municipio && <span className="editor-nome__mun">{c.municipio}</span>}
            </li>
          ))}
          <li className="editor-nome__dica" aria-hidden="true">
            ↑↓ escolher · Enter preenche nome e CNPJ e vai para o valor · Esc cancela
          </li>
        </ul>
      )}
    </div>
  );
}
