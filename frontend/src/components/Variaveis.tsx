import { useState } from "react";
import type { Opcao } from "../api";
import { Dialogo, copiar, useToast } from "../ui";
import { useOpcoes } from "./FiscalForm";

const semAcento = (t: string) => t.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();

/** Mesma troca do servidor (services/variaveis.py), com os exemplos de hoje: {Mês} → Outubro, {MES} → OUTUBRO. */
export function substituirVariaveis(texto: string, variaveis: Opcao[]): string {
  const mapa = new Map(variaveis.map((v) => [v.valor.slice(1, -1), v.exemplo ?? ""]));
  return texto.replace(/\{([^{}\s]{1,40})\}/g, (todo, nome: string) => {
    const valor = mapa.get(semAcento(nome));
    if (valor === undefined) return todo;
    const letras = [...nome].filter((c) => c.toLowerCase() !== c.toUpperCase());
    if (letras.length && letras.every((c) => c === c.toUpperCase())) return valor.toUpperCase();
    if (letras.length && letras[0] === letras[0].toUpperCase()) return valor.charAt(0).toUpperCase() + valor.slice(1);
    return valor;
  });
}

export function useVariaveis(): Opcao[] {
  return useOpcoes().data?.variaveis_descricao ?? [];
}

/** Prévia da descrição como sairia hoje (só quando o texto tem variáveis). */
export function PreviaDescricao({ texto }: { texto: string }) {
  const variaveis = useVariaveis();
  if (!variaveis.length) return null;
  const previa = substituirVariaveis(texto, variaveis);
  const conhecidas = new Set(variaveis.map((v) => v.valor.slice(1, -1)));
  const erradas = [...new Set([...texto.matchAll(/\{([^{}\s]{1,40})\}/g)].filter((m) => !conhecidas.has(semAcento(m[1]))).map((m) => m[0]))];
  return (
    <>
      {previa !== texto && (
        <small className="previa-descricao">
          Emitida hoje, sai: <span>{previa}</span>
        </small>
      )}
      {erradas.length > 0 && (
        <small className="campo__erro">
          {erradas.join(", ")} não {erradas.length > 1 ? "são variáveis" : "é uma variável"}: confira a lista.
        </small>
      )}
    </>
  );
}

export function BotaoVariaveis({ className = "btn btn--pequeno btn--fantasma" }: { className?: string }) {
  const [aberto, setAberto] = useState(false);
  return (
    <>
      <button type="button" className={className} onClick={() => setAberto(true)}>
        Variáveis na descrição
      </button>
      {aberto && <VariaveisDialogo fechar={() => setAberto(false)} />}
    </>
  );
}

function VariaveisDialogo({ fechar }: { fechar: () => void }) {
  const variaveis = useVariaveis();
  const toast = useToast();
  const exemplo = "Honorários contábeis de {mes_anterior}/{ano_mes_anterior}";
  return (
    <Dialogo titulo="Variáveis na descrição" fechar={fechar} largo>
      <p style={{ marginTop: 0 }}>
        Escreva a descrição uma vez e deixe o mês por conta do sistema. Cada nota sai com a data do dia em que for
        emitida, e a tabela continua com as variáveis para o mês seguinte.
      </p>
      <div className="info-caixa" style={{ display: "block" }}>
        <span className="num">{exemplo}</span>
        <br />
        emitida hoje sai <b>{substituirVariaveis(exemplo, variaveis)}</b>
      </div>
      <table className="tabela variaveis" style={{ marginTop: 14 }}>
        <thead>
          <tr>
            <th>Variável</th>
            <th>O que vira</th>
            <th>Hoje</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {variaveis.map((v) => (
            <tr key={v.valor}>
              <td className="num">{v.valor}</td>
              <td>{v.descricao}</td>
              <td>{v.exemplo}</td>
              <td className="dir">
                <button
                  type="button"
                  className="btn btn--pequeno btn--fantasma"
                  onClick={() => copiar(v.valor).then(() => toast(`${v.valor} copiada.`))}
                >
                  Copiar
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p style={{ color: "var(--grafite)", marginBottom: 0 }}>
        Com a primeira letra maiúscula, o mês sai com maiúscula: <span className="num">{"{Mes}"}</span> vira{" "}
        {substituirVariaveis("{Mes}", variaveis)}, e <span className="num">{"{MES}"}</span> vira{" "}
        {substituirVariaveis("{MES}", variaveis)}. Uma variável escrita errado impede a emissão da linha, para não
        sair na nota.
      </p>
    </Dialogo>
  );
}
