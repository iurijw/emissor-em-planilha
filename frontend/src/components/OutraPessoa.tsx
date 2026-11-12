import { documentoValido, formatarDocumento, soDocumento } from "../format";
import { EnderecoTomador, enderecoCompleto, type Endereco } from "./EnderecoTomador";

/** Intermediário ou destinatário (IBS/CBS) da nota avulsa: CNPJ/CPF, nome e endereço; não entram no cadastro. */
export interface DadosPessoa {
  documento: string;
  nome: string;
  im: string;
  email: string;
  endereco: Endereco;
}

export const pessoaVazia: DadosPessoa = {
  documento: "",
  nome: "",
  im: "",
  email: "",
  endereco: { logradouro: null, numero: null, complemento: null, bairro: null, cep: null, c_mun: null },
};

/** Pronta para a nota: documento válido, nome e, para o intermediário com CNPJ, o endereço completo (E1388). */
export function pessoaPronta(p: DadosPessoa, exigeEnderecoCnpj: boolean): boolean {
  const doc = soDocumento(p.documento);
  if (!documentoValido(doc) || ![11, 14].includes(doc.length) || !p.nome.trim()) return false;
  return !(exigeEnderecoCnpj && doc.length === 14 && !enderecoCompleto(p.endereco));
}

export function corpoPessoa(p: DadosPessoa, comIm: boolean): Record<string, string> {
  const e = p.endereco;
  return {
    documento: soDocumento(p.documento),
    nome: p.nome.trim(),
    ...(comIm && p.im.trim() ? { im: p.im.trim() } : {}),
    email: p.email.trim(),
    c_mun: e.c_mun ?? "",
    cep: (e.cep ?? "").replace(/\D/g, ""),
    logradouro: e.logradouro ?? "",
    numero: e.numero ?? "",
    complemento: e.complemento ?? "",
    bairro: e.bairro ?? "",
  };
}

export function OutraPessoa({
  titulo,
  ajuda,
  valor,
  onChange,
  comIm,
}: {
  titulo: string;
  ajuda: string;
  valor: DadosPessoa;
  onChange: (p: DadosPessoa) => void;
  comIm: boolean;
}) {
  const doc = soDocumento(valor.documento);
  const invalido = doc.length >= 11 && !documentoValido(doc);
  return (
    <section>
      <h3 className="secao-titulo">{titulo}</h3>
      <p style={{ margin: "0 0 12px", color: "var(--grafite)", fontSize: 13 }}>{ajuda}</p>
      <div className="grade-form">
        <label className={`campo ${invalido ? "campo--erro" : ""}`}>
          <span>CNPJ / CPF</span>
          <input className="num" value={formatarDocumento(doc)} onChange={(e) => onChange({ ...valor, documento: e.target.value })} />
          {invalido && <small className="campo__erro">CNPJ/CPF inválido.</small>}
        </label>
        <label className="campo">
          <span>Nome</span>
          <input value={valor.nome} maxLength={300} onChange={(e) => onChange({ ...valor, nome: e.target.value })} />
        </label>
        {comIm && (
          <label className="campo">
            <span>Inscrição municipal</span>
            <input className="num" value={valor.im} maxLength={15} onChange={(e) => onChange({ ...valor, im: e.target.value })} />
            <small>Opcional.</small>
          </label>
        )}
        <label className="campo">
          <span>E-mail</span>
          <input type="email" value={valor.email} maxLength={80} onChange={(e) => onChange({ ...valor, email: e.target.value })} />
          <small>Opcional.</small>
        </label>
        {doc.length >= 11 && !invalido && (
          <div className="largo">
            <EnderecoTomador
              valor={valor.endereco}
              onChange={(e) => onChange({ ...valor, endereco: e })}
              cpf={doc.length === 11}
              buscando={false}
              municipio={null}
              titulo="Endereço"
              semCadastro
            />
          </div>
        )}
      </div>
    </section>
  );
}
