import { useOpcoes } from "./FiscalForm";

/** Tomador sem CNPJ/CPF, com endereço fora do Brasil (só na nota avulsa; não entra no cadastro de clientes). */
export interface DadosExterior {
  nif: string;
  /** Sem NIF: 1 dispensado, 2 não exigido. */
  c_nao_nif: string;
  nome: string;
  c_pais: string;
  c_end_post: string;
  cidade: string;
  estado: string;
  logradouro: string;
  numero: string;
  complemento: string;
  bairro: string;
  email: string;
  fone: string;
}

export const exteriorVazio: DadosExterior = {
  nif: "",
  c_nao_nif: "",
  nome: "",
  c_pais: "",
  c_end_post: "",
  cidade: "",
  estado: "",
  logradouro: "",
  numero: "",
  complemento: "",
  bairro: "",
  email: "",
  fone: "",
};

/** O que falta para a nota sair (o servidor confere de novo). */
export function faltaNoExterior(d: DadosExterior): string[] {
  const falta: string[] = [];
  if (!d.nif.trim() && !d.c_nao_nif) falta.push("NIF ou o motivo de não ter");
  if (!d.nome.trim()) falta.push("nome");
  if (!d.c_pais) falta.push("país");
  for (const [k, r] of [
    ["c_end_post", "código postal"],
    ["cidade", "cidade"],
    ["estado", "estado/província"],
    ["logradouro", "logradouro"],
    ["bairro", "bairro"],
  ] as const)
    if (!d[k].trim()) falta.push(r);
  return falta;
}

export function TomadorExterior({
  valor,
  onChange,
}: {
  valor: DadosExterior;
  onChange: (d: DadosExterior) => void;
}) {
  const op = useOpcoes().data;
  const set = (k: keyof DadosExterior, v: string) =>
    onChange({ ...valor, [k]: v });
  const campo = (
    k: keyof DadosExterior,
    rotulo: string,
    extra?: { ajuda?: string; max?: number; largo?: boolean; tipo?: string },
  ) => (
    <label className={extra?.largo ? "campo largo" : "campo"}>
      <span>{rotulo}</span>
      <input
        type={extra?.tipo ?? "text"}
        value={valor[k]}
        maxLength={extra?.max}
        onChange={(e) => set(k, e.target.value)}
      />
      {extra?.ajuda && <small>{extra.ajuda}</small>}
    </label>
  );
  const semNif = !valor.nif.trim();
  return (
    <>
      <label className="campo">
        <span>NIF</span>
        <input
          className="num"
          value={valor.nif}
          maxLength={40}
          onChange={(e) =>
            onChange({ ...valor, nif: e.target.value, c_nao_nif: "" })
          }
        />
        <small>Número de identificação fiscal do tomador no país dele.</small>
      </label>
      {semNif ? (
        <label className="campo">
          <span>Sem NIF, por quê?</span>
          <select
            value={valor.c_nao_nif}
            onChange={(e) => set("c_nao_nif", e.target.value)}
          >
            <option value="">Escolha</option>
            {op?.c_nao_nif?.map((o) => (
              <option key={o.valor} value={o.valor}>
                {o.descricao}
              </option>
            ))}
          </select>
          <small>Só quando o tomador não tem NIF.</small>
        </label>
      ) : (
        <span className="campo" aria-hidden="true" />
      )}
      {campo("nome", "Nome da empresa/pessoa", { max: 300, largo: true })}
      <label className="campo">
        <span>País</span>
        <select
          value={valor.c_pais}
          onChange={(e) => set("c_pais", e.target.value)}
        >
          <option value="">Escolha</option>
          {op?.paises?.map((o) => (
            <option key={o.valor} value={o.valor}>
              {o.descricao}
            </option>
          ))}
        </select>
      </label>
      {campo("c_end_post", "Código postal", { max: 11 })}
      {campo("cidade", "Cidade", { max: 60 })}
      {campo("estado", "Estado, província ou região", { max: 60 })}
      {campo("logradouro", "Logradouro", { max: 255, largo: true })}
      {campo("numero", "Número", { max: 60 })}
      {campo("complemento", "Complemento", { max: 156 })}
      {campo("bairro", "Bairro ou distrito", {
        max: 60,
        ajuda: "Obrigatório no leiaute; sem bairro, repita a cidade.",
      })}
      {campo("email", "E-mail", { max: 80, tipo: "email" })}
    </>
  );
}
