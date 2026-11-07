import { useEffect, useId, useState } from "react";
import type { Cliente } from "../api";
import { CampoCep } from "./CampoCep";
import { CampoMunicipio } from "./FiscalForm";

/** Campos do endereço que vão na nota (o número em branco sai como "S/N"). */
export type Endereco = Pick<Cliente, "logradouro" | "numero" | "complemento" | "bairro" | "cep" | "c_mun">;

const VAZIO: Endereco = { logradouro: null, numero: null, complemento: null, bairro: null, cep: null, c_mun: null };

export function enderecoDe(c: Cliente | undefined): Endereco {
  if (!c) return VAZIO;
  return { logradouro: c.logradouro, numero: c.numero, complemento: c.complemento, bairro: c.bairro, cep: c.cep, c_mun: c.c_mun };
}

const limpo = (v: string | null) => (v ?? "").trim();
const soDigitos = (v: string | null) => (v ?? "").replace(/\D/g, "");

/** Mesma regra do servidor (``Cliente.endereco_completo``): município, CEP, logradouro e bairro. */
export function enderecoCompleto(e: Endereco): boolean {
  return !!(e.c_mun && soDigitos(e.cep).length === 8 && limpo(e.logradouro) && limpo(e.bairro));
}

export function enderecoVazio(e: Endereco): boolean {
  return !Object.values(e).some((v) => limpo(v));
}

export function enderecosIguais(a: Endereco, b: Endereco): boolean {
  return (Object.keys(VAZIO) as (keyof Endereco)[]).every((k) =>
    k === "cep" ? soDigitos(a[k]) === soDigitos(b[k]) : limpo(a[k]) === limpo(b[k]),
  );
}

function formatarCep(v: string | null): string {
  const d = soDigitos(v);
  return d.length === 8 ? `${d.slice(0, 5)}-${d.slice(5)}` : d;
}

type Situacao = "buscando" | "completo" | "incompleto" | "sem";

/**
 * Endereço do tomador na nota avulsa: uma linha com o resumo e a situação, que abre para editar.
 * O que for alterado aqui é gravado no cadastro do cliente ao conferir a nota.
 */
export function EnderecoTomador({
  valor,
  onChange,
  cpf,
  buscando,
  municipio,
}: {
  valor: Endereco;
  onChange: (e: Endereco) => void;
  cpf: boolean;
  buscando: boolean;
  /** "Cidade / UF" do cadastro, para o resumo fechado. */
  municipio: string | null;
}) {
  const id = useId();
  const situacao: Situacao = buscando ? "buscando" : enderecoCompleto(valor) ? "completo" : enderecoVazio(valor) ? "sem" : "incompleto";
  // Fechado quando já há endereço; aberto quando falta algo de um CNPJ (para completar na hora).
  const [aberto, setAberto] = useState(false);
  const [mexeu, setMexeu] = useState(false);
  useEffect(() => {
    if (!mexeu && situacao === "incompleto") setAberto(true);
  }, [situacao, mexeu]);

  const set = (k: keyof Endereco, v: string) => onChange({ ...valor, [k]: v || null });

  const resumo =
    situacao === "buscando"
      ? "Buscando o endereço na Receita…"
      : situacao === "sem"
        ? "Não informado."
        : [
            [limpo(valor.logradouro), limpo(valor.numero) || (limpo(valor.logradouro) ? "S/N" : "")].filter(Boolean).join(", "),
            limpo(valor.bairro),
            municipio,
          ]
            .filter(Boolean)
            .join(" · ");

  const tag =
    situacao === "buscando" ? null : situacao === "completo" ? null : situacao === "incompleto" ? (
      <span className="tag-endereco tag-endereco--aviso">Endereço incompleto</span>
    ) : cpf ? (
      <span className="tag-endereco">Sem endereço · opcional para CPF</span>
    ) : (
      <span className="tag-endereco tag-endereco--aviso">Sem endereço</span>
    );

  return (
    <div className={`endereco-tomador ${aberto ? "endereco-tomador--aberto" : ""}`}>
      <button
        type="button"
        className="endereco-tomador__cabeca"
        aria-expanded={aberto}
        aria-controls={id}
        onClick={() => {
          setMexeu(true);
          setAberto((a) => !a);
        }}
      >
        <span className="endereco-tomador__titulo">Endereço do tomador</span>
        <span className="endereco-tomador__resumo" title={resumo}>
          {resumo}
          {situacao !== "buscando" && soDigitos(valor.cep).length === 8 && (
            <span className="num"> · {formatarCep(valor.cep)}</span>
          )}
        </span>
        {tag}
        <svg className="endereco-tomador__seta" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M3 4.5 6 7.5 9 4.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      <div className="endereco-tomador__corpo" id={id} hidden={!aberto}>
        <div className="grade-form">
          <CampoCep
            valor={valor.cep}
            onChange={(v) => set("cep", v ?? "")}
            disabled={buscando}
            aoEncontrar={(e) => {
              onChange({
                ...valor,
                cep: e.cep,
                logradouro: e.logradouro || valor.logradouro,
                bairro: e.bairro || valor.bairro,
                c_mun: e.c_mun || valor.c_mun,
              });
              if (e.logradouro) document.getElementById(`${id}-numero`)?.focus();
            }}
          />
          <label className="campo largo">
            <span>Logradouro</span>
            <input value={valor.logradouro ?? ""} onChange={(e) => set("logradouro", e.target.value)} disabled={buscando} />
          </label>
          <label className="campo">
            <span>Número</span>
            <input id={`${id}-numero`} value={valor.numero ?? ""} onChange={(e) => set("numero", e.target.value)} placeholder="S/N" disabled={buscando} />
          </label>
          <label className="campo">
            <span>Complemento</span>
            <input value={valor.complemento ?? ""} onChange={(e) => set("complemento", e.target.value)} disabled={buscando} />
          </label>
          <label className="campo">
            <span>Bairro</span>
            <input value={valor.bairro ?? ""} onChange={(e) => set("bairro", e.target.value)} disabled={buscando} />
          </label>
          <div>
            <CampoMunicipio rotulo="Município" codigo={valor.c_mun} onChange={(v) => onChange({ ...valor, c_mun: v })} />
          </div>
        </div>
        <small className="endereco-tomador__nota">
          {situacao === "incompleto"
            ? "Para ir na nota, o endereço precisa de logradouro, bairro, CEP e município. "
            : ""}
          O que você mudar aqui fica salvo no cadastro do cliente.
        </small>
      </div>
    </div>
  );
}
