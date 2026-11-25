import { moeda, parseValor } from "../format";
import { CampoCep } from "./CampoCep";
import { CampoMunicipio } from "./FiscalForm";

/**
 * Locação, cessão onerosa e arrendamento de imóvel (subitem 99.03, leiaute da NT 009): a nota leva o imóvel, o valor
 * total do aluguel, a parte do locador (copropriedade) e as parcelas que não entram na base do IBS/CBS. As chaves são
 * as mesmas das colunas da planilha (emissor/nfse/locacao.py, CAMPOS).
 */
export interface DadosLocacao {
  modo: "end" | "c_cib";
  cep: string;
  logradouro: string;
  numero: string;
  complemento: string;
  bairro: string;
  c_mun: string | null;
  insc_imob_fisc: string;
  c_cib: string;
  p_copropriedade: string;
  v_tot_oper: string;
  venc: string;
  iptu: string;
  condominio: string;
  emolumentos: string;
  redutor_social: string;
  outras: string;
  outras_descricao: string;
}

export const locacaoVazia: DadosLocacao = {
  modo: "end",
  cep: "",
  logradouro: "",
  numero: "",
  complemento: "",
  bairro: "",
  c_mun: null,
  insc_imob_fisc: "",
  c_cib: "",
  p_copropriedade: "",
  v_tot_oper: "",
  venc: "",
  iptu: "",
  condominio: "",
  emolumentos: "",
  redutor_social: "",
  outras: "",
  outras_descricao: "",
};

export const ehLocacao = (c: string | null | undefined) => (c ?? "").startsWith("9903");
/** NBS do imóvel residencial: só ele aceita o redutor social (E0944). */
const NBS_RESIDENCIAL = "110021000";

const PARCELAS: { chave: "iptu" | "condominio" | "emolumentos" | "redutor_social" | "outras"; rotulo: string }[] = [
  { chave: "iptu", rotulo: "IPTU" },
  { chave: "condominio", rotulo: "Condomínio" },
  { chave: "emolumentos", rotulo: "Emolumentos" },
  { chave: "redutor_social", rotulo: "Redutor social" },
  { chave: "outras", rotulo: "Outras parcelas" },
];

const numero = (t: string) => {
  const v = parseValor(t.replace("%", ""));
  return v === null ? null : Number(v);
};

/** Locação de dados da cópia de uma nota (as chaves da planilha). */
export function locacaoDe(d: Record<string, string> | null | undefined): DadosLocacao {
  if (!d) return locacaoVazia;
  const r: DadosLocacao = { ...locacaoVazia };
  for (const k of Object.keys(locacaoVazia) as (keyof DadosLocacao)[]) {
    if (k === "modo" || k === "c_mun") continue;
    if (typeof d[k] === "string") r[k] = d[k];
  }
  r.c_mun = d.c_mun || null;
  r.modo = d.c_cib ? "c_cib" : "end";
  return r;
}

/** O que falta para a Sefin aceitar (E0938, E0933, E0940, E0949); o servidor confere tudo de novo. */
export function faltaNaLocacao(l: DadosLocacao): string[] {
  const falta: string[] = [];
  if (l.modo === "c_cib") {
    if (l.c_cib.replace(/[\s.-]/g, "").length !== 8) falta.push("CIB do imóvel");
  } else {
    if (l.cep.replace(/\D/g, "").length !== 8) falta.push("CEP do imóvel");
    if (!l.logradouro.trim()) falta.push("endereço do imóvel");
  }
  const p = l.p_copropriedade.trim() ? numero(l.p_copropriedade) : 100;
  if (p === null || p <= 0 || p > 100) falta.push("copropriedade entre 0,01% e 100%");
  if (l.outras.trim() && !l.outras_descricao.trim()) falta.push("descrição das outras parcelas");
  return falta;
}

/** Corpo para o servidor: só o identificador escolhido (CIB ou endereço) vai. */
export function corpoLocacao(l: DadosLocacao): Record<string, string> {
  const { modo, c_mun, ...resto } = l;
  const corpo: Record<string, string> = {};
  for (const [k, v] of Object.entries(resto)) if (v.trim()) corpo[k] = v.trim();
  if (c_mun) corpo.c_mun = c_mun;
  if (modo === "c_cib") for (const k of ["cep", "logradouro", "numero", "complemento", "bairro"]) delete corpo[k];
  else delete corpo.c_cib;
  return corpo;
}

export function LocacaoNota({
  valor,
  onChange,
  valorNota,
  c_nbs,
}: {
  valor: DadosLocacao;
  onChange: (l: DadosLocacao) => void;
  /** Valor da nota (a parte do locador), para mostrar o valor total quando há copropriedade. */
  valorNota: string | null;
  c_nbs: string | null | undefined;
}) {
  const set = (k: keyof DadosLocacao, v: string) => onChange({ ...valor, [k]: v });
  const residencial = (c_nbs ?? "") === NBS_RESIDENCIAL;
  const p = valor.p_copropriedade.trim() ? numero(valor.p_copropriedade) : 100;
  const pValida = p !== null && p > 0 && p <= 100;
  const total = valor.v_tot_oper.trim() ? numero(valor.v_tot_oper) : null;
  const parte = valorNota !== null ? Number(valorNota) : null;
  // Sem o valor total digitado, ele é o valor da nota dividido pela copropriedade (como o servidor calcula).
  const totalCalculado = total ?? (parte && pValida ? Math.round((parte * 100) / p! * 100) / 100 : null);
  const confere = total === null || parte === null || !pValida || Math.abs(Math.round(total * p!) / 100 - parte) <= 0.01;
  const campo = (k: keyof DadosLocacao, rotulo: string, max: number, opts: { largo?: boolean; obrigatorio?: boolean; num?: boolean; ajuda?: string } = {}) => (
    <label className={`campo${opts.largo ? " largo" : ""}${opts.obrigatorio ? " campo--obrigatorio" : ""}`}>
      <span>{rotulo}</span>
      <input className={opts.num ? "num" : undefined} value={valor[k] as string} maxLength={max} onChange={(e) => set(k, e.target.value)} />
      {opts.ajuda && <small>{opts.ajuda}</small>}
    </label>
  );
  return (
    <section>
      <h3 className="secao-titulo">Imóvel e locação</h3>
      <div className="grade-form">
        <fieldset className="pergunta largo">
          <legend>Como identificar o imóvel</legend>
          <div className="pergunta__opcoes">
            <label>
              <input type="radio" name="modo-imovel" checked={valor.modo === "end"} onChange={() => onChange({ ...valor, modo: "end" })} /> Endereço
            </label>
            <label>
              <input type="radio" name="modo-imovel" checked={valor.modo === "c_cib"} onChange={() => onChange({ ...valor, modo: "c_cib" })} /> CIB do imóvel
            </label>
          </div>
          <small>A nota de aluguel leva o imóvel locado. Sem número, o endereço sai como S/N.</small>
        </fieldset>
        {valor.modo === "c_cib" ? (
          <label className="campo campo--obrigatorio">
            <span>CIB</span>
            <input className="num" value={valor.c_cib} maxLength={9} onChange={(e) => set("c_cib", e.target.value.toUpperCase())} />
            <small>Cadastro Imobiliário Brasileiro: 8 caracteres.</small>
          </label>
        ) : (
          <>
            <CampoCep
              valor={valor.cep || null}
              obrigatorio
              onChange={(c) => set("cep", c ?? "")}
              aoEncontrar={(r) =>
                onChange({
                  ...valor,
                  cep: r.cep,
                  logradouro: r.logradouro || valor.logradouro,
                  bairro: r.bairro || valor.bairro,
                  c_mun: r.c_mun || valor.c_mun,
                })
              }
            />
            {campo("logradouro", "Endereço do imóvel", 255, { largo: true, obrigatorio: true })}
            {campo("numero", "Número", 60)}
            {campo("complemento", "Complemento", 156)}
            {campo("bairro", "Bairro", 60)}
          </>
        )}
        <CampoMunicipio
          rotulo="Município do imóvel"
          codigo={valor.c_mun}
          onChange={(c) => onChange({ ...valor, c_mun: c })}
          ajuda="Em branco, vale o local da prestação da tributação."
        />
        {campo("insc_imob_fisc", "Inscrição imobiliária", 30, { num: true, ajuda: "Opcional: a inscrição do imóvel na prefeitura (a do IPTU)." })}
        <label className={`campo${pValida ? "" : " campo--erro"}`}>
          <span>% copropriedade</span>
          <input className="num" value={valor.p_copropriedade} placeholder="100" maxLength={7} onChange={(e) => set("p_copropriedade", e.target.value)} />
          {pValida ? (
            <small>A parte do imóvel que é deste locador. Em branco, 100%.</small>
          ) : (
            <small className="campo__erro">De 0,01% a 100%.</small>
          )}
        </label>
        <label className={`campo${confere ? "" : " campo--erro"}`}>
          <span>Valor total do aluguel</span>
          <input
            className="num"
            value={valor.v_tot_oper}
            inputMode="decimal"
            placeholder={totalCalculado !== null && p !== 100 ? moeda(totalCalculado) : "Igual ao valor da nota"}
            onChange={(e) => set("v_tot_oper", e.target.value)}
          />
          {confere ? (
            <small>O valor da nota é a parte do locador: o total vezes a copropriedade.</small>
          ) : (
            <small className="campo__erro">
              O valor da nota tem de ser o total vezes {String(p).replace(".", ",")}% (E0433).
            </small>
          )}
        </label>
        <label className="campo">
          <span>Vencimento</span>
          <input type="date" value={/^\d{4}-\d{2}-\d{2}$/.test(valor.venc) ? valor.venc : ""} onChange={(e) => set("venc", e.target.value)} />
          <small>Opcional: o vencimento original do aluguel.</small>
        </label>
      </div>

      <h3 className="secao-titulo" style={{ marginTop: 22 }}>Parcelas fora da base do IBS/CBS</h3>
      <p style={{ color: "var(--grafite)", fontSize: "0.8125rem", margin: "0 0 12px" }}>
        Valores do aluguel inteiro (não só da parte do locador) que entram na cobrança mas não são aluguel. A Sefin tira essa parte da base.
      </p>
      <div className="grade-form">
        {PARCELAS.filter((x) => x.chave !== "redutor_social" || residencial || valor.redutor_social).map(({ chave, rotulo }) => {
          const v = valor[chave];
          const invalido = v.trim() !== "" && (numero(v) === null || numero(v)! < 0);
          return (
            <label key={chave} className={`campo${invalido ? " campo--erro" : ""}`}>
              <span>{rotulo}</span>
              <input className="num" inputMode="decimal" value={v} placeholder="0,00" onChange={(e) => set(chave, e.target.value)} />
              {invalido && <small className="campo__erro">Valor inválido.</small>}
              {chave === "redutor_social" && !residencial && <small className="campo__erro">Só para imóvel residencial (E0944).</small>}
            </label>
          );
        })}
        {valor.outras.trim() !== "" && (
          <label className="campo largo campo--obrigatorio">
            <span>Descrição das outras parcelas</span>
            <input value={valor.outras_descricao} maxLength={150} onChange={(e) => set("outras_descricao", e.target.value)} />
          </label>
        )}
      </div>
    </section>
  );
}
