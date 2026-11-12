import { CampoCep } from "./CampoCep";

/** Endereço da obra ou do evento: CEP (no Brasil, do município da prestação) ou código postal no exterior. */
export interface EnderecoLocal {
  cep: string;
  c_end_post: string;
  cidade: string;
  estado: string;
  logradouro: string;
  numero: string;
  complemento: string;
  bairro: string;
}

const enderecoVazio: EnderecoLocal = { cep: "", c_end_post: "", cidade: "", estado: "", logradouro: "", numero: "", complemento: "", bairro: "" };

export type ModoObra = "c_obra" | "c_cib" | "end";
export interface DadosObra extends EnderecoLocal {
  modo: ModoObra;
  insc_imob_fisc: string;
  c_obra: string;
  c_cib: string;
}
export const obraVazia: DadosObra = { ...enderecoVazio, modo: "c_obra", insc_imob_fisc: "", c_obra: "", c_cib: "" };

export interface DadosEvento extends EnderecoLocal {
  nome: string;
  dt_ini: string;
  dt_fim: string;
  /** Com o código do evento dado pela prefeitura, o endereço não vai. */
  id_atv_evt: string;
  comCodigo: boolean;
}
export const eventoVazio: DadosEvento = { ...enderecoVazio, nome: "", dt_ini: "", dt_fim: "", id_atv_evt: "", comCodigo: false };

/** Serviços de construção que exigem os dados da obra (E0370), como no servidor. */
const SERVICOS_OBRA = new Set("070201 070202 070401 070501 070502 070601 070602 070701 070801 071701 071901 141403 141404".split(" "));
export const exigeObra = (c: string | null | undefined) => SERVICOS_OBRA.has(c ?? "");
/** Item 12 (espetáculos, shows, feiras, congressos...) exige os dados do evento (E0390). */
export const exigeEvento = (c: string | null | undefined) => /^12\d{4}$/.test(c ?? "");

function enderecoCompleto(e: EnderecoLocal, exterior: boolean): boolean {
  const local = exterior ? !!(e.c_end_post.trim() && e.cidade.trim() && e.estado.trim()) : e.cep.replace(/\D/g, "").length === 8;
  return local && !!e.logradouro.trim() && !!e.bairro.trim();
}

export function obraPronta(o: DadosObra, exterior: boolean): boolean {
  if (o.modo === "c_obra") return !!o.c_obra.trim();
  if (o.modo === "c_cib") return o.c_cib.replace(/[\s.-]/g, "").length === 8;
  return enderecoCompleto(o, exterior);
}

export function eventoPronto(e: DadosEvento, exterior: boolean): boolean {
  if (!e.nome.trim() || !e.dt_ini || !e.dt_fim || e.dt_fim < e.dt_ini) return false;
  return e.comCodigo ? !!e.id_atv_evt.trim() : enderecoCompleto(e, exterior);
}

const enderecoCorpo = (e: EnderecoLocal) => ({
  cep: e.cep,
  c_end_post: e.c_end_post,
  cidade: e.cidade,
  estado: e.estado,
  logradouro: e.logradouro,
  numero: e.numero,
  complemento: e.complemento,
  bairro: e.bairro,
});

/** Corpo para o servidor: só o identificador escolhido vai (o servidor usa o primeiro que encontrar). */
export function corpoObra(o: DadosObra): Record<string, string> {
  const base = { insc_imob_fisc: o.insc_imob_fisc };
  if (o.modo === "c_obra") return { ...base, c_obra: o.c_obra };
  if (o.modo === "c_cib") return { ...base, c_cib: o.c_cib };
  return { ...base, ...enderecoCorpo(o) };
}

export function corpoEvento(e: DadosEvento): Record<string, string> {
  const base = { nome: e.nome, dt_ini: e.dt_ini, dt_fim: e.dt_fim };
  return e.comCodigo ? { ...base, id_atv_evt: e.id_atv_evt } : { ...base, ...enderecoCorpo(e) };
}

function Endereco<T extends EnderecoLocal>({ valor, onChange, exterior }: { valor: T; onChange: (v: T) => void; exterior: boolean }) {
  const set = (k: keyof EnderecoLocal, v: string) => onChange({ ...valor, [k]: v });
  const campo = (k: keyof EnderecoLocal, rotulo: string, max: number, largo = false, obrigatorio = false) => (
    <label className={`campo${largo ? " largo" : ""}${obrigatorio ? " campo--obrigatorio" : ""}`}>
      <span>{rotulo}</span>
      <input value={valor[k]} maxLength={max} onChange={(e) => set(k, e.target.value)} />
    </label>
  );
  return (
    <>
      {exterior ? (
        <>
          {campo("c_end_post", "Código postal", 11, false, true)}
          {campo("cidade", "Cidade", 60, false, true)}
          {campo("estado", "Estado, província ou região", 60, false, true)}
        </>
      ) : (
        <CampoCep
          valor={valor.cep || null}
          obrigatorio
          onChange={(c) => set("cep", c ?? "")}
          aoEncontrar={(r) =>
            onChange({ ...valor, cep: r.cep, logradouro: r.logradouro ?? valor.logradouro, bairro: r.bairro ?? valor.bairro })
          }
        />
      )}
      {campo("logradouro", "Logradouro", 255, true, true)}
      {campo("numero", "Número", 60)}
      {campo("complemento", "Complemento", 156)}
      {campo("bairro", "Bairro", 60, false, true)}
    </>
  );
}

const MODOS: { id: ModoObra; rotulo: string }[] = [
  { id: "c_obra", rotulo: "Código da obra (CNO)" },
  { id: "c_cib", rotulo: "CIB do imóvel" },
  { id: "end", rotulo: "Endereço da obra" },
];

export function ObraNota({ valor, onChange, exterior }: { valor: DadosObra; onChange: (o: DadosObra) => void; exterior: boolean }) {
  const set = (k: keyof DadosObra, v: string) => onChange({ ...valor, [k]: v });
  return (
    <section>
      <h3 className="secao-titulo">Obra</h3>
      <div className="grade-form">
        <fieldset className="pergunta largo">
          <legend>Como identificar a obra</legend>
          <div className="pergunta__opcoes">
            {MODOS.map((m) => (
              <label key={m.id}>
                <input type="radio" name="modo-obra" checked={valor.modo === m.id} onChange={() => onChange({ ...valor, modo: m.id })} /> {m.rotulo}
              </label>
            ))}
          </div>
          <small>Este serviço de construção exige os dados da obra em cada nota.</small>
        </fieldset>
        {valor.modo === "c_obra" && (
          <label className="campo campo--obrigatorio">
            <span>Código da obra</span>
            <input className="num" value={valor.c_obra} maxLength={30} onChange={(e) => set("c_obra", e.target.value)} />
            <small>CNO (Cadastro Nacional de Obras) ou o código que a prefeitura deu.</small>
          </label>
        )}
        {valor.modo === "c_cib" && (
          <label className="campo campo--obrigatorio">
            <span>CIB</span>
            <input className="num" value={valor.c_cib} maxLength={9} onChange={(e) => set("c_cib", e.target.value.toUpperCase())} />
            <small>Cadastro Imobiliário Brasileiro: 8 caracteres.</small>
          </label>
        )}
        <label className="campo">
          <span>Inscrição imobiliária fiscal</span>
          <input className="num" value={valor.insc_imob_fisc} maxLength={30} onChange={(e) => set("insc_imob_fisc", e.target.value)} />
          <small>Opcional: a inscrição do imóvel na prefeitura.</small>
        </label>
        {valor.modo === "end" && <Endereco valor={valor} onChange={onChange} exterior={exterior} />}
      </div>
    </section>
  );
}

export function EventoNota({ valor, onChange, exterior }: { valor: DadosEvento; onChange: (e: DadosEvento) => void; exterior: boolean }) {
  const set = (k: keyof DadosEvento, v: string) => onChange({ ...valor, [k]: v });
  const datasErradas = !!valor.dt_ini && !!valor.dt_fim && valor.dt_fim < valor.dt_ini;
  return (
    <section>
      <h3 className="secao-titulo">Atividade ou evento</h3>
      <div className="grade-form">
        <label className="campo largo campo--obrigatorio">
          <span>Nome do evento</span>
          <input value={valor.nome} maxLength={255} onChange={(e) => set("nome", e.target.value)} />
          <small>Serviços do item 12 (espetáculos, shows, feiras, congressos) exigem os dados do evento.</small>
        </label>
        <label className="campo campo--obrigatorio">
          <span>Início</span>
          <input type="date" value={valor.dt_ini} onChange={(e) => set("dt_ini", e.target.value)} />
        </label>
        <label className={`campo campo--obrigatorio${datasErradas ? " campo--erro" : ""}`}>
          <span>Fim</span>
          <input type="date" value={valor.dt_fim} onChange={(e) => set("dt_fim", e.target.value)} />
          {datasErradas && <small className="campo__erro">O fim não pode ser antes do início.</small>}
        </label>
        <fieldset className="pergunta largo">
          <legend>A prefeitura deu um código para o evento?</legend>
          <div className="pergunta__opcoes">
            <label>
              <input type="radio" name="evento-codigo" checked={!valor.comCodigo} onChange={() => onChange({ ...valor, comCodigo: false })} /> Não, informar o endereço
            </label>
            <label>
              <input type="radio" name="evento-codigo" checked={valor.comCodigo} onChange={() => onChange({ ...valor, comCodigo: true })} /> Sim
            </label>
          </div>
        </fieldset>
        {valor.comCodigo ? (
          <label className="campo campo--obrigatorio">
            <span>Código do evento</span>
            <input className="num" value={valor.id_atv_evt} maxLength={30} onChange={(e) => set("id_atv_evt", e.target.value)} />
          </label>
        ) : (
          <Endereco valor={valor} onChange={onChange} exterior={exterior} />
        )}
      </div>
    </section>
  );
}
