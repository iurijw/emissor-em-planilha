import { useId, useState } from "react";
import { moeda, parseValor } from "../format";

/** O que vale só para esta nota: descontos, pedido e informações complementares (nota avulsa). */
export interface DadosNota {
  v_desc_incond: string;
  v_desc_cond: string;
  x_ped: string;
  itens_pedido: string;
  x_inf_comp: string;
}

export const dadosNotaVazios: DadosNota = { v_desc_incond: "", v_desc_cond: "", x_ped: "", itens_pedido: "", x_inf_comp: "" };

/** Itens do pedido digitados separados por vírgula ou ponto e vírgula. */
export const itensDoPedido = (t: string) =>
  t
    .split(/[;,]/)
    .map((i) => i.trim())
    .filter(Boolean);

/** Corpo para o servidor (descontos no formato do valor; vazio = não informar). */
export function corpoDadosNota(d: DadosNota) {
  return {
    v_desc_incond: parseValor(d.v_desc_incond) ?? (d.v_desc_incond.trim() || null),
    v_desc_cond: parseValor(d.v_desc_cond) ?? (d.v_desc_cond.trim() || null),
    x_ped: d.x_ped.trim() || null,
    itens_pedido: itensDoPedido(d.itens_pedido),
    x_inf_comp: d.x_inf_comp.trim() || null,
  };
}

export function DadosDaNota({ valor, onChange }: { valor: DadosNota; onChange: (d: DadosNota) => void }) {
  const id = useId();
  const [aberto, setAberto] = useState(false);
  const set = <K extends keyof DadosNota>(k: K, v: string) => onChange({ ...valor, [k]: v });
  const incond = parseValor(valor.v_desc_incond);
  const cond = parseValor(valor.v_desc_cond);
  const itens = itensDoPedido(valor.itens_pedido);
  const partes = [
    incond && `desconto incondicionado ${moeda(incond)}`,
    cond && `desconto condicionado ${moeda(cond)}`,
    valor.x_ped.trim() && `pedido ${valor.x_ped.trim()}`,
    itens.length > 0 && `${itens.length} ${itens.length === 1 ? "item" : "itens"} do pedido`,
    valor.x_inf_comp.trim() && "informações complementares",
  ].filter(Boolean) as string[];
  const resumo = partes.length ? partes.join(", ") : "Nenhum";
  const tamanho = valor.x_inf_comp.length;
  return (
    <div className={`endereco-tomador ${aberto ? "endereco-tomador--aberto" : ""}`}>
      <button type="button" className="endereco-tomador__cabeca" aria-expanded={aberto} aria-controls={id} onClick={() => setAberto((a) => !a)}>
        <span className="endereco-tomador__titulo">Descontos e informações complementares</span>
        <span className="endereco-tomador__resumo" title={resumo}>
          {resumo.charAt(0).toUpperCase() + resumo.slice(1)}
        </span>
        <svg className="endereco-tomador__seta" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M3 4.5 6 7.5 9 4.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      <div className="endereco-tomador__corpo" id={id} hidden={!aberto}>
        <div className="grade-form">
          <label className="campo">
            <span>Desconto incondicionado</span>
            <input className="num" inputMode="decimal" placeholder="0,00" value={valor.v_desc_incond} onChange={(e) => set("v_desc_incond", e.target.value)} />
            <small>Dado na hora, sem condição: reduz a base do ISSQN.</small>
          </label>
          <label className="campo">
            <span>Desconto condicionado</span>
            <input className="num" inputMode="decimal" placeholder="0,00" value={valor.v_desc_cond} onChange={(e) => set("v_desc_cond", e.target.value)} />
            <small>Depende de algo depois (pagar até o vencimento): só sai do valor líquido.</small>
          </label>
          <label className="campo">
            <span>Número do pedido</span>
            <input value={valor.x_ped} maxLength={60} onChange={(e) => set("x_ped", e.target.value)} />
            <small>Pedido ou ordem de compra do tomador.</small>
          </label>
          <label className="campo">
            <span>Itens do pedido</span>
            <input value={valor.itens_pedido} onChange={(e) => set("itens_pedido", e.target.value)} placeholder="Ex.: 10, 20" />
            <small>Separados por vírgula, até 99.</small>
          </label>
          <label className={`campo largo ${tamanho > 2000 ? "campo--erro" : ""}`}>
            <span>Informações complementares</span>
            <textarea rows={3} value={valor.x_inf_comp} onChange={(e) => set("x_inf_comp", e.target.value)} />
            <small className={tamanho > 2000 ? "campo__erro" : undefined}>
              Saem no campo próprio da NFS-e, fora da descrição. Até 2000 caracteres ({tamanho}); quebras de linha viram espaço.
            </small>
          </label>
        </div>
      </div>
    </div>
  );
}
