import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import {
  api,
  ApiError,
  type ConferenciaServico,
  type DadosTributacao,
  type Fiscal,
  issExigeAliquota,
  type Municipio,
  type Opcao,
  type Opcoes,
  type Regime,
  type Servico,
  type ServicoDetalhe,
} from "../api";
import { formatarDocumento, moeda } from "../format";
import { Spinner } from "../ui";

export function useOpcoes() {
  return useQuery({ queryKey: ["opcoes"], queryFn: () => api.get<Opcoes>("/api/config/opcoes"), staleTime: Infinity });
}

const semAcento = (t: string) =>
  t.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").trim().toLowerCase();

/** Escolhe o município do texto digitado sem clique na lista: único resultado ou único nome idêntico. */
function municipioDoTexto(termo: string, lista: Municipio[]): Municipio | null {
  if (lista.length === 1) return lista[0];
  const t = semAcento(termo).replace(/\s*[/-]\s*([a-z]{2})$/, " / $1");
  const iguais = lista.filter((m) => semAcento(m.nome) === t || semAcento(`${m.nome} / ${m.uf}`) === t);
  return iguais.length === 1 ? iguais[0] : null;
}

/** Campo de município: busca por nome, guarda o código IBGE. */
export function CampoMunicipio(props: {
  rotulo: string;
  codigo: string | null;
  onChange: (codigo: string | null) => void;
  ajuda?: string;
  obrigatorio?: boolean;
}) {
  const id = useId();
  const [termo, setTermo] = useState("");
  const [aberto, setAberto] = useState(false);
  const [destaque, setDestaque] = useState(0);
  // Texto digitado que não virou município (nenhuma cidade escolhida na lista).
  const [pendente, setPendente] = useState("");
  const [rotulo, setRotulo] = useState<string>("");
  const buscar = (q: string) => api.get<Municipio[]>(`/api/municipios?q=${encodeURIComponent(q)}`);
  const busca = useQuery({
    queryKey: ["municipios", termo],
    queryFn: () => buscar(termo),
    enabled: termo.trim().length >= 2,
  });
  useEffect(() => {
    if (!props.codigo) {
      setRotulo("");
      return;
    }
    api
      .get<Municipio[]>(`/api/municipios?q=${props.codigo}`)
      .then((r) => setRotulo(r[0] ? `${r[0].nome} / ${r[0].uf}` : "Código não encontrado na tabela do IBGE"))
      .catch(() => undefined);
  }, [props.codigo]);

  const escolher = (m: Municipio) => {
    props.onChange(m.codigo);
    setRotulo(`${m.nome} / ${m.uf}`);
    setPendente("");
    setTermo("");
    setAberto(false);
  };

  // Saiu do campo sem clicar numa cidade: aproveita o texto se ele indicar uma só.
  const sair = async () => {
    setAberto(false);
    const t = termo.trim();
    if (t.length < 2) {
      setPendente("");
      return;
    }
    let lista = busca.data;
    if (!lista || busca.isFetching) lista = await buscar(t).catch(() => []);
    const m = municipioDoTexto(t, lista);
    if (m) escolher(m);
    else setPendente(t);
  };

  const opcoes = busca.data ?? [];
  const mostrarLista = aberto && opcoes.length > 0;

  return (
    <label className={`campo${pendente ? " campo--erro" : ""}${props.obrigatorio ? " campo--obrigatorio" : ""}`} style={{ position: "relative" }}>
      <span>{props.rotulo}</span>
      <input
        aria-describedby={`${id}-ajuda`}
        aria-expanded={mostrarLista}
        aria-autocomplete="list"
        role="combobox"
        value={aberto ? termo : pendente || rotulo || props.codigo || ""}
        placeholder="Digite o nome da cidade"
        onFocus={() => {
          setTermo(pendente);
          setDestaque(0);
          setAberto(true);
        }}
        onBlur={() => void sair()}
        onChange={(e) => {
          setTermo(e.target.value);
          setDestaque(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" && opcoes.length) {
            e.preventDefault();
            setDestaque((i) => (i + 1) % opcoes.length);
          } else if (e.key === "ArrowUp" && opcoes.length) {
            e.preventDefault();
            setDestaque((i) => (i - 1 + opcoes.length) % opcoes.length);
          } else if (e.key === "Enter" && mostrarLista) {
            e.preventDefault();
            escolher(opcoes[Math.min(destaque, opcoes.length - 1)]);
          } else if (e.key === "Escape") {
            setAberto(false);
          }
        }}
      />
      {mostrarLista && (
        <ul role="listbox" className="lista-sugestoes">
          {opcoes.map((m, i) => (
            <li key={m.codigo} role="option" aria-selected={i === destaque}>
              <button
                type="button"
                tabIndex={-1}
                className="btn btn--fantasma"
                style={{
                  width: "100%", justifyContent: "space-between", fontWeight: 400,
                  background: i === destaque ? "var(--linha)" : undefined,
                }}
                onMouseEnter={() => setDestaque(i)}
                onMouseDown={(e) => {
                  // Evita o blur do campo antes do clique (senão o texto digitado seria avaliado primeiro).
                  e.preventDefault();
                  escolher(m);
                }}
              >
                <span>
                  {m.nome} / {m.uf}
                </span>
                <span className="num" style={{ color: "var(--grafite)" }}>
                  {m.codigo}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {pendente ? (
        <small id={`${id}-ajuda`} className="campo__erro">
          Escolha a cidade na lista: "{pendente}" não indica um município só.
        </small>
      ) : (
        <small id={`${id}-ajuda`}>
          {props.codigo ? <>IBGE <span className="num">{props.codigo}</span>. </> : null}
          {props.ajuda ?? "Digite o nome e escolha na lista."}
        </small>
      )}
    </label>
  );
}

/** Exportação ou serviço no exterior pedem o grupo de comércio exterior (E0330): entra com os valores mais comuns e
 * sai inteiro quando deixa de valer. */
function comComex(t: DadosTributacao): DadosTributacao {
  if (t.trib_issqn !== "3" && t.c_pais_prestacao == null)
    return {
      ...t,
      comex_md_prestacao: null,
      comex_vinc_prest: null,
      comex_tp_moeda: null,
      comex_mec_af_p: null,
      comex_mec_af_t: null,
      comex_mov_temp_bens: null,
      comex_mdic: null,
    };
  return {
    ...t,
    comex_vinc_prest: t.comex_vinc_prest ?? "0",
    comex_tp_moeda: t.comex_tp_moeda ?? "220",
    comex_mec_af_p: t.comex_mec_af_p ?? "01",
    comex_mec_af_t: t.comex_mec_af_t ?? "01",
    comex_mov_temp_bens: t.comex_mov_temp_bens ?? "1",
    comex_mdic: t.comex_mdic ?? "0",
  };
}

function Selecao(props: {
  rotulo: string;
  valor: string | null;
  opcoes?: { valor: string; descricao: string }[];
  onChange: (v: string | null) => void;
  vazio?: string;
  ajuda?: string;
  largo?: boolean;
  /** Sem ele a Sefin recusa a nota: o rótulo ganha o asterisco. */
  obrigatorio?: boolean;
}) {
  return (
    <label className={`campo${props.largo ? " largo" : ""}${props.obrigatorio ? " campo--obrigatorio" : ""}`}>
      <span>{props.rotulo}</span>
      <select
        value={props.valor ?? ""}
        aria-required={props.obrigatorio || undefined}
        onChange={(e) => props.onChange(e.target.value || null)}
      >
        {props.vazio !== undefined && <option value="">{props.vazio}</option>}
        {props.opcoes?.map((o) => (
          <option key={o.valor} value={o.valor}>
            {o.valor} — {o.descricao}
          </option>
        ))}
      </select>
      {props.ajuda && <small>{props.ajuda}</small>}
    </label>
  );
}

function Texto(props: {
  rotulo: string;
  valor: string | null;
  onChange: (v: string) => void;
  ajuda?: string;
  /** Mostrado no lugar da ajuda, com o campo em vermelho. */
  erro?: string;
  num?: boolean;
  largo?: boolean;
  placeholder?: string;
  obrigatorio?: boolean;
}) {
  return (
    <label className={`campo${props.largo ? " largo" : ""}${props.erro ? " campo--erro" : ""}${props.obrigatorio ? " campo--obrigatorio" : ""}`}>
      <span>{props.rotulo}</span>
      <input
        className={props.num ? "num" : undefined}
        aria-required={props.obrigatorio || undefined}
        value={props.valor ?? ""}
        placeholder={props.placeholder}
        aria-invalid={props.erro ? true : undefined}
        onChange={(e) => props.onChange(e.target.value)}
      />
      {props.erro ? <small className="campo__erro">{props.erro}</small> : props.ajuda && <small>{props.ajuda}</small>}
    </label>
  );
}

// Códigos como a lista nacional os mostra: 171901 → 17.19.01; 113022100 → 1.1302.21.00.
export const codigoServicoTela = (c: string | null | undefined) =>
  c && /^\d{6}$/.test(c) ? `${c.slice(0, 2)}.${c.slice(2, 4)}.${c.slice(4)}` : c ?? "";
const nbsTela = (c: string | null | undefined) =>
  c && /^\d{9}$/.test(c) ? `${c[0]}.${c.slice(1, 5)}.${c.slice(5, 7)}.${c.slice(7)}` : c ?? "";

/** Serviço da lista nacional pelo código (6 dígitos) com NBS e cClassTrib sugeridos; null = fora da lista. */
function useServico(codigo: string) {
  return useQuery({
    queryKey: ["servico", codigo],
    queryFn: async () => {
      try {
        return await api.get<ServicoDetalhe>(`/api/servicos/${codigo}`);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    enabled: /^\d{6}$/.test(codigo),
    staleTime: Infinity,
  });
}

/** Código de tributação nacional: busca na lista nacional por código ou descrição, guarda os 6 dígitos. */
function CampoServico(props: { codigo: string; onChange: (codigo: string) => void; servico: ServicoDetalhe | null | undefined }) {
  const id = useId();
  const [termo, setTermo] = useState("");
  const [aberto, setAberto] = useState(false);
  const [destaque, setDestaque] = useState(0);
  const [pendente, setPendente] = useState("");
  const buscar = (q: string) => api.get<Servico[]>(`/api/servicos?q=${encodeURIComponent(q)}`);
  const busca = useQuery({
    queryKey: ["servicos", termo],
    queryFn: () => buscar(termo),
    enabled: termo.trim().length >= 2,
    staleTime: Infinity,
  });

  const escolher = (codigo: string) => {
    props.onChange(codigo);
    setPendente("");
    setTermo("");
    setAberto(false);
  };

  // Saiu do campo sem clicar: um código de 6 dígitos vale como digitado; senão, só se a busca indicar um serviço.
  const sair = async () => {
    setAberto(false);
    const t = termo.trim();
    const digitos = t.replace(/[\s./-]/g, "");
    if (!t) {
      setPendente("");
      return;
    }
    if (/^\d{6}$/.test(digitos)) return escolher(digitos);
    let lista = busca.data;
    if (!lista || busca.isFetching) lista = await buscar(t).catch(() => []);
    if (lista.length === 1) escolher(lista[0].codigo);
    else setPendente(t);
  };

  const opcoes = busca.data ?? [];
  const mostrarLista = aberto && opcoes.length > 0;
  const rotulo = props.servico ? `${codigoServicoTela(props.codigo)} — ${props.servico.descricao}` : codigoServicoTela(props.codigo);
  const foraDaLista = props.servico === null && /^\d{6}$/.test(props.codigo);
  const erro = pendente
    ? `Escolha o serviço na lista: "${pendente}" não indica um serviço só.`
    : foraDaLista
      ? "Este código não está na lista nacional de serviços: a Sefin deve recusar a nota. Busque o serviço pela descrição."
      : "";

  return (
    <label className={`campo largo campo--obrigatorio${erro ? " campo--erro" : ""}`} style={{ position: "relative" }}>
      <span>Serviço (código de tributação nacional)</span>
      <input
        aria-required
        aria-describedby={`${id}-ajuda`}
        aria-expanded={mostrarLista}
        aria-autocomplete="list"
        aria-invalid={erro ? true : undefined}
        role="combobox"
        value={aberto ? termo : pendente || rotulo}
        placeholder="Digite o código ou parte da descrição, ex.: contabilidade"
        onFocus={() => {
          setTermo(pendente);
          setDestaque(0);
          setAberto(true);
        }}
        onBlur={() => void sair()}
        onChange={(e) => {
          setTermo(e.target.value);
          setDestaque(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" && opcoes.length) {
            e.preventDefault();
            setDestaque((i) => (i + 1) % opcoes.length);
          } else if (e.key === "ArrowUp" && opcoes.length) {
            e.preventDefault();
            setDestaque((i) => (i - 1 + opcoes.length) % opcoes.length);
          } else if (e.key === "Enter" && mostrarLista) {
            e.preventDefault();
            escolher(opcoes[Math.min(destaque, opcoes.length - 1)].codigo);
          } else if (e.key === "Escape") {
            setAberto(false);
          }
        }}
      />
      {mostrarLista && (
        <ul role="listbox" className="lista-sugestoes">
          {opcoes.map((o, i) => (
            <li key={o.codigo} role="option" aria-selected={i === destaque}>
              <button
                type="button"
                tabIndex={-1}
                className="lista-sugestoes__item"
                style={{ background: i === destaque ? "var(--linha)" : undefined }}
                onMouseEnter={() => setDestaque(i)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  escolher(o.codigo);
                }}
              >
                <span className="num">{codigoServicoTela(o.codigo)}</span>
                <span>
                  {o.descricao}
                  {o.subitem_descricao && o.subitem_descricao !== o.descricao && (
                    <small>Subitem {o.subitem}: {o.subitem_descricao}</small>
                  )}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {aberto && termo.trim().length >= 2 && busca.isSuccess && !opcoes.length ? (
        <small id={`${id}-ajuda`}>Nenhum serviço com esse código ou essas palavras. Tente outra palavra.</small>
      ) : erro ? (
        <small id={`${id}-ajuda`} className="campo__erro">
          {erro}
        </small>
      ) : (
        <small id={`${id}-ajuda`}>
          Busque pelo código ou pela descrição da lista nacional (LC 116). O código vai na nota com 6 dígitos
          {props.codigo ? <> (<span className="num">{props.codigo}</span>)</> : null}.
        </small>
      )}
    </label>
  );
}

/** NBS: os que o Anexo VIII liga ao serviço escolhido, ou outro código digitado. */
function CampoNbs(props: {
  valor: string | null;
  onChange: (v: string | null) => void;
  servico: ServicoDetalhe | null | undefined;
  obrigatorio: boolean;
}) {
  const lista = props.servico?.nbs ?? [];
  const naLista = !props.valor || lista.some((n) => n.codigo === props.valor);
  const [livre, setLivre] = useState(false);
  const ajudaBase = props.obrigatorio ? "Obrigatório com IBS/CBS." : "Vazio = não informar.";
  if (!lista.length || livre) {
    const erro = props.valor && !/^\d{9}$/.test(props.valor) ? "O código NBS tem 9 dígitos, ex.: 1.1302.21.00." : undefined;
    return (
      <label className={`campo largo${erro ? " campo--erro" : ""}${props.obrigatorio ? " campo--obrigatorio" : ""}`}>
        <span>Código NBS</span>
        <input
          className="num"
          value={props.valor ?? ""}
          placeholder="9 dígitos"
          aria-invalid={erro ? true : undefined}
          onChange={(e) => props.onChange(e.target.value.replace(/\D/g, "").slice(0, 9) || null)}
        />
        {erro ? (
          <small className="campo__erro">{erro}</small>
        ) : (
          <small>
            {ajudaBase}{" "}
            {lista.length > 0 && (
              <button type="button" className="btn-texto" onClick={() => setLivre(false)}>
                Escolher entre os NBS do serviço
              </button>
            )}
          </small>
        )}
      </label>
    );
  }
  return (
    <label className={`campo largo${props.obrigatorio ? " campo--obrigatorio" : ""}`}>
      <span>Código NBS</span>
      <select
        value={props.valor ?? ""}
        onChange={(e) => {
          if (e.target.value === "__outro") setLivre(true);
          else props.onChange(e.target.value || null);
        }}
      >
        <option value="">{props.obrigatorio ? "Escolha o NBS do serviço" : "Não informar"}</option>
        {lista.map((n) => (
          <option key={n.codigo} value={n.codigo}>
            {nbsTela(n.codigo)} — {n.descricao}
          </option>
        ))}
        {!naLista && <option value={props.valor ?? ""}>{nbsTela(props.valor)} — fora da lista deste serviço</option>}
        <option value="__outro">Outro código NBS…</option>
      </select>
      <small>
        {ajudaBase} A lista vem do Anexo VIII (correlação entre o serviço e a NBS).
        {!naLista && " O código atual não é um dos que o Anexo VIII liga a este serviço: confira com a contabilidade."}
      </small>
    </label>
  );
}

/**
 * Pergunta ao Sistema Nacional, com o certificado, o que a prefeitura cadastrou para o serviço (alíquota). Sem o
 * cadastro a Sefin recusa a nota (E0312). Só leitura; o servidor guarda a resposta por algumas horas.
 */
function useConferencia(codigo: string, local: string | null, municipioPrestador?: string | null) {
  const [forcar, setForcar] = useState(false);
  const q = useQuery({
    queryKey: ["conferir-servico", codigo, local ?? "", municipioPrestador ?? "", forcar],
    queryFn: () =>
      api.post<ConferenciaServico>("/api/config/conferir-servico", {
        c_trib_nac: codigo,
        c_loc_prestacao: local,
        c_mun_emissor: municipioPrestador || null,
        forcar,
      }),
    enabled: /^\d{6}$/.test(codigo),
    staleTime: 10 * 60_000,
    retry: false,
  });
  return { ...q, consultarDeNovo: () => (forcar ? q.refetch() : setForcar(true)) };
}

/** Pergunta de Sim/Não como no Emissor Nacional (Não primeiro). */
/** Explica o asterisco dos campos obrigatórios (classe ``campo--obrigatorio``). */
export function LegendaObrigatorio() {
  return (
    <p className="legenda-obrigatorio">
      <b aria-hidden="true">*</b> Obrigatório: sem ele a nota é recusada.
    </p>
  );
}

export function Pergunta(props: { texto: string; sim: boolean; onChange: (sim: boolean) => void; ajuda?: string }) {
  const nome = useId();
  return (
    <fieldset className="pergunta largo">
      <legend>{props.texto}</legend>
      <div className="pergunta__opcoes">
        <label>
          <input type="radio" name={nome} checked={!props.sim} onChange={() => props.onChange(false)} /> Não
        </label>
        <label>
          <input type="radio" name={nome} checked={props.sim} onChange={() => props.onChange(true)} /> Sim
        </label>
      </div>
      {props.ajuda && <small>{props.ajuda}</small>}
    </fieldset>
  );
}

/** Valor que o sistema mostra e não se edita aqui (como os campos cinza do portal). */
function Mostrado(props: { rotulo: string; valor: ReactNode; ajuda?: ReactNode; num?: boolean }) {
  return (
    <div className="campo">
      <span>{props.rotulo}</span>
      <output className={`campo__mostrado${props.num ? " num" : ""}`}>{props.valor}</output>
      {props.ajuda && <small>{props.ajuda}</small>}
    </div>
  );
}

/**
 * Dados da empresa (prestador) e regime tributário: valem para todas as tributações. Com `pedirTotTribSN`, quem
 * passa a ME/EPP informa aqui o % do Simples da tributação padrão (lá o campo só aparece depois de salvar o regime).
 */
export function CamposEmpresa({
  valor,
  onChange,
  pedirTotTribSN = false,
}: {
  valor: Fiscal;
  onChange: (f: Fiscal) => void;
  pedirTotTribSN?: boolean;
}) {
  const op = useOpcoes().data;
  const set = <K extends keyof Fiscal>(k: K, v: Fiscal[K]) => onChange({ ...valor, [k]: v });
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 26 }}>
      <LegendaObrigatorio />
      <section>
        <h3 className="secao-titulo">Prestador</h3>
        <div className="grade-form">
          <Texto
            rotulo="CNPJ"
            valor={formatarDocumento(valor.cnpj)}
            onChange={(v) => set("cnpj", v.replace(/[^0-9A-Za-z]/g, ""))}
            obrigatorio
            num
          />
          <Texto
            rotulo="Inscrição municipal"
            valor={valor.inscricao_municipal}
            onChange={(v) => set("inscricao_municipal", v || null)}
            ajuda="Deixe vazio se as notas atuais não informam."
          />
          <CampoMunicipio
            rotulo="Município do prestador"
            codigo={valor.c_mun_emissor || null}
            onChange={(c) => set("c_mun_emissor", c ?? "")}
            obrigatorio
          />
          <Texto rotulo="Telefone" valor={valor.fone} onChange={(v) => set("fone", v || null)} num />
          <Texto rotulo="E-mail" valor={valor.email} onChange={(v) => set("email", v || null)} largo />
        </div>
      </section>

      <section>
        <h3 className="secao-titulo">Regime tributário</h3>
        <div className="grade-form">
          <Selecao
            rotulo="Situação no Simples Nacional"
            valor={valor.op_simp_nac}
            opcoes={op?.op_simp_nac}
            onChange={(v) => set("op_simp_nac", v ?? "1")}
          />
          {valor.op_simp_nac === "3" && (
            <Selecao
              rotulo="Regime de apuração pelo Simples"
              valor={valor.reg_ap_trib_sn}
              opcoes={op?.reg_ap_trib_sn}
              vazio="Não informar"
              onChange={(v) => set("reg_ap_trib_sn", v)}
            />
          )}
          {valor.op_simp_nac === "3" && pedirTotTribSN && (
            <Texto
              rotulo="% aproximado de tributos (Simples)"
              valor={pctTela(valor.p_tot_trib_sn)}
              onChange={(v) => set("p_tot_trib_sn", pctDado(v))}
              ajuda="Obrigatório para ME/EPP (E0712). Vai para a tributação padrão; as outras tributações têm o próprio campo."
              obrigatorio
              num
            />
          )}
          <Selecao
            rotulo="Regime especial de tributação"
            valor={valor.reg_esp_trib}
            opcoes={op?.reg_esp_trib}
            onChange={(v) => set("reg_esp_trib", v ?? "0")}
          />
        </div>
        <p className="dica-grade">O regime vale para todas as tributações e decide quais alíquotas cada uma pede.</p>
      </section>
    </div>
  );
}

type ClasseIbsCbs = Opcao & { cst: string; reducao_ibs: number; reducao_cbs: number; trib_regular?: boolean; diferimento?: boolean };

/**
 * Serviço, tributos e IBS/CBS: o que muda de uma tributação para outra, na ordem e com os nomes do Emissor Nacional
 * (Serviço → Valores → prévia da emissão). Usado na tributação padrão e nas outras tributações (Configurações e
 * onboarding) e na nota avulsa com tributação própria.
 */
export function CamposTributacao({
  valor,
  onChange,
  regime,
  municipioPrestador,
  semPrevia,
  semLegenda,
}: {
  valor: DadosTributacao;
  onChange: (t: DadosTributacao) => void;
  /** Sem a explicação do asterisco (quem usa já a mostra, como a nota avulsa). */
  semLegenda?: boolean;
  /** Sem a prévia numa nota de exemplo (a nota avulsa mostra os valores da própria nota no fim). */
  semPrevia?: boolean;
  /** Regime do prestador (Configurações): decide quais alíquotas a nota aceita. */
  regime: Regime | null | undefined;
  /** Município do prestador ainda não salvo (onboarding); sem ele, a consulta à prefeitura usa o das configurações. */
  municipioPrestador?: string | null;
}) {
  const op = useOpcoes().data;
  const servico = useServico(valor.c_trib_nac).data;
  const prefeitura = useConferencia(valor.c_trib_nac, valor.c_loc_prestacao, municipioPrestador);
  const conferencia = prefeitura.data;
  const set = <K extends keyof DadosTributacao>(k: K, v: DadosTributacao[K]) => onChange({ ...valor, [k]: v });
  // Imunidade, exportação e não incidência não têm suspensão nem benefício (E0585/E0533); o tipo de imunidade só
  // existe com imunidade (E0592).
  const mudarTribIssqn = (trib: string) =>
    onChange(
      comComex({
        ...valor,
        trib_issqn: trib,
        tp_imunidade: trib === "2" ? valor.tp_imunidade : null,
        c_pais_result: trib === "3" ? valor.c_pais_result : null,
        ...(trib === "1" ? {} : { tp_susp: null, n_processo: null, n_bm: null, p_red_bcbm: null, v_red_bcbm: null }),
        ...(trib === "1" ? {} : { tp_ret_issqn: "1" }),
      }),
    );
  const noExterior = valor.c_pais_prestacao != null;
  const exportacao = valor.trib_issqn === "3" || noExterior;
  const [beneficioEmReais, setBeneficioEmReais] = useState(!!valor.v_red_bcbm);
  // Ao trocar o serviço, um NBS que não é dele dá lugar ao único NBS do serviço novo (ou fica para escolher).
  const servicoTrocado = useRef<string | null>(null);
  useEffect(() => {
    if (!servico || servico.codigo !== servicoTrocado.current) return;
    servicoTrocado.current = null;
    const codigos = servico.nbs.map((n) => n.codigo);
    if (valor.c_nbs && !codigos.includes(valor.c_nbs)) onChange({ ...valor, c_nbs: codigos.length === 1 ? codigos[0] : null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [servico]);
  const retido = ["2", "3"].includes(valor.tp_ret_issqn);
  const exigeAliquota = issExigeAliquota(regime, valor);
  const incide = valor.trib_issqn === "1";
  const nbsEscolhido = servico?.nbs.find((n) => n.codigo === valor.c_nbs);
  // Indicador que o sistema não emite (ex.: 020301, imóveis, vindo de uma nota antiga): o <select> mostraria a
  // primeira opção, mas o valor salvo seria o antigo e o servidor recusaria. Troca pelo do NBS (se o sistema o
  // emite) ou pelo dos serviços em geral.
  useEffect(() => {
    const suportados = op?.ibscbs_c_ind_op?.map((o) => o.valor);
    if (!valor.ibscbs || !suportados?.length || suportados.includes(valor.ibscbs_c_ind_op)) return;
    const doNbs = nbsEscolhido?.c_ind_op.find((c) => suportados.includes(c));
    onChange({ ...valor, ibscbs_c_ind_op: doNbs ?? "100301" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [valor.ibscbs, valor.ibscbs_c_ind_op, op, nbsEscolhido?.codigo]);
  // 10.05, 15.09, 17.12 e 25.05 exigem o tipo de operação com o IBS/CBS (E0903); os demais não podem tê-lo (E0904).
  const exigeTpOperServico = /^(1005|1509|1712|2505)/.test(valor.c_trib_nac);
  const exigeTpOper = exigeTpOperServico || !!valor.ibscbs_tp_ente_gov;
  const retencao = RETENCOES[valor.tp_ret_pis_cofins ?? ""];
  const retemCsll = !!valor.cst_pis_cofins && !!retencao?.[2];
  const erroTribMun =
    valor.c_trib_mun && !/^\d{3}$/.test(valor.c_trib_mun)
      ? "O código municipal tem só 3 dígitos (ex.: 001). O código nacional vai no campo acima."
      : undefined;
  const codigoCompleto = /^\d{6}$/.test(valor.c_trib_nac)
    ? `${codigoServicoTela(valor.c_trib_nac)}.${/^\d{3}$/.test(valor.c_trib_mun ?? "") ? valor.c_trib_mun : "000"}`
    : null;
  // Alíquota do ISS: a do Simples (digitada) quando a Sefin exige; senão a que a prefeitura cadastrou.
  const aliquotaPrefeitura = conferencia?.situacao === "encontrado" ? conferencia.aliquota : null;
  const aliquotaIss = !incide ? null : exigeAliquota ? valor.p_aliq_iss : aliquotaPrefeitura;

  return (
    <>
      {!semLegenda && <LegendaObrigatorio />}
      <section>
        <h3 className="secao-titulo">Informações gerais</h3>
        <div className="grade-form">
          <Pergunta
            texto="Preencher as informações IBS/CBS?"
            sim={valor.ibscbs}
            onChange={(v) => set("ibscbs", v)}
            ajuda="A Sefin calcula os valores do IBS e da CBS a partir da classificação. Sem elas, a nota é aceita até 31/12/2026; confirme com a contabilidade a partir de quando são exigidas para você."
          />
        </div>
      </section>

      <section>
        <h3 className="secao-titulo">Serviço prestado</h3>
        <div className="grade-form">
          <Pergunta
            texto="O serviço foi prestado no exterior?"
            sim={noExterior}
            onChange={(sim) =>
              onChange(comComex({ ...valor, c_pais_prestacao: sim ? "" : null, c_loc_prestacao: null, c_pais_result: null }))
            }
            ajuda={noExterior ? "A nota sai com o país no lugar do município e com os dados de comércio exterior." : undefined}
          />
          {noExterior ? (
            <Selecao
              rotulo="País da prestação do serviço"
              valor={valor.c_pais_prestacao || null}
              opcoes={op?.paises}
              vazio="Escolha"
              obrigatorio
              onChange={(v) => set("c_pais_prestacao", v ?? "")}
            />
          ) : (
            <CampoMunicipio
              rotulo="Local da prestação do serviço"
              codigo={valor.c_loc_prestacao}
              onChange={(c) => set("c_loc_prestacao", c)}
              ajuda="Vazio = município do prestador."
            />
          )}
          <CampoServico
            codigo={valor.c_trib_nac}
            onChange={(c) => {
              servicoTrocado.current = c;
              set("c_trib_nac", c);
            }}
            servico={servico}
          />
          <Texto
            rotulo="Código de Tributação Municipal"
            valor={valor.c_trib_mun}
            onChange={(v) => {
              const d = v.replace(/\D/g, "").slice(0, 9);
              // "080101003" = nacional + municipal: fica só o municipal (o servidor faz o mesmo ao salvar).
              set("c_trib_mun", (d.length === 9 && d.startsWith(valor.c_trib_nac) ? d.slice(6) : d) || null);
            }}
            ajuda={
              "Opcional: os 3 dígitos que a prefeitura criou para o serviço. Na dúvida, deixe vazio." +
              (codigoCompleto ? ` Código completo do serviço: ${codigoCompleto}.` : "")
            }
            erro={erroTribMun}
            num
          />
          <Mostrado
            rotulo="Município de incidência do ISSQN"
            valor={conferencia?.municipio_nome ?? "—"}
            ajuda={!conferencia?.municipio && conferencia?.situacao === "nao_conferido" ? conferencia.mensagem : undefined}
          />
          <Pergunta
            texto="O serviço prestado é um caso de imunidade, exportação de serviço ou não incidência do ISSQN?"
            sim={!incide}
            onChange={(sim) => mudarTribIssqn(sim ? "2" : "1")}
          />
          {!incide && (
            <Selecao
              rotulo="Tributação do ISSQN sobre o serviço prestado"
              valor={valor.trib_issqn}
              opcoes={op?.trib_issqn?.filter((o) => o.valor !== "1")}
              onChange={(v) => mudarTribIssqn(v ?? "2")}
              ajuda={
                valor.trib_issqn === "3"
                  ? "A exportação sai só pela nota avulsa, que pede o valor na moeda estrangeira."
                  : undefined
              }
            />
          )}
          {valor.trib_issqn === "3" && !noExterior && (
            <Selecao
              rotulo="País onde se deu o resultado do serviço"
              valor={valor.c_pais_result}
              opcoes={op?.paises}
              vazio="Escolha"
              obrigatorio
              onChange={(v) => set("c_pais_result", v)}
              ajuda="Onde o serviço produz efeito (normalmente o país do tomador)."
            />
          )}
          {valor.trib_issqn === "2" && (
            <Selecao
              rotulo="Tipo de imunidade"
              largo
              valor={valor.tp_imunidade}
              opcoes={op?.tp_imunidade}
              vazio="Escolha"
              obrigatorio
              onChange={(v) => set("tp_imunidade", v)}
              ajuda="Obrigatório com imunidade (art. 150, VI, da Constituição)."
            />
          )}
          <CampoNbs valor={valor.c_nbs} onChange={(v) => set("c_nbs", v)} servico={servico} obrigatorio={valor.ibscbs} />
          {valor.ibscbs && (
            <Selecao
              rotulo="Código indicador da operação"
              largo
              valor={valor.ibscbs_c_ind_op}
              opcoes={op?.ibscbs_c_ind_op}
              onChange={(v) => set("ibscbs_c_ind_op", v ?? "100301")}
              ajuda={
                "Onde o IBS/CBS é devido. Serviços em geral: 100301." +
                (nbsEscolhido?.c_ind_op.length
                  ? ` Para o NBS escolhido, o Anexo VIII indica ${nbsEscolhido.c_ind_op.join(" ou ")}.`
                  : "")
              }
            />
          )}
          {valor.ibscbs && exigeTpOper && (
            <Selecao
              rotulo="Tipo de operação"
              largo
              valor={valor.ibscbs_tp_oper ?? null}
              opcoes={op?.ibscbs_tp_oper}
              vazio="Escolha"
              obrigatorio
              onChange={(v) => set("ibscbs_tp_oper", v)}
              ajuda={
                ["2", "3"].includes(valor.ibscbs_tp_oper ?? "")
                  ? "Com uma das partes já feita, a nota sai só pela nota avulsa, que pede a chave da NFS-e anterior."
                  : "Quando o serviço é prestado e quando é pago. No comum (presta e recebe no mês), 5."
              }
            />
          )}
          {valor.ibscbs && (
            <Selecao
              rotulo="Compra governamental"
              largo
              valor={valor.ibscbs_tp_ente_gov ?? null}
              opcoes={op?.ibscbs_tp_ente_gov}
              vazio="Não"
              onChange={(v) =>
                onChange({ ...valor, ibscbs_tp_ente_gov: v, ibscbs_tp_oper: v || exigeTpOperServico ? valor.ibscbs_tp_oper : null })
              }
              ajuda="Tomador da administração pública direta, autarquia ou fundação: a Sefin aplica o redutor das compras governamentais."
            />
          )}
        </div>
        <SituacaoPrefeitura consulta={prefeitura} />
      </section>

      {exportacao && (
        <section>
          <h3 className="secao-titulo">Comércio exterior</h3>
          <div className="grade-form">
            <Selecao
              rotulo="Modo de prestação"
              largo
              valor={valor.comex_md_prestacao}
              opcoes={op?.comex_md_prestacao}
              vazio="Escolha"
              obrigatorio
              onChange={(v) => set("comex_md_prestacao", v)}
              ajuda="Transfronteiriço: o serviço é feito daqui para o exterior (ex.: consultoria a distância)."
            />
            <Selecao
              rotulo="Vínculo entre as partes"
              largo
              valor={valor.comex_vinc_prest}
              opcoes={op?.comex_vinc_prest}
              onChange={(v) => set("comex_vinc_prest", v)}
            />
            <Selecao
              rotulo="Moeda"
              largo
              valor={valor.comex_tp_moeda}
              opcoes={op?.comex_tp_moeda}
              onChange={(v) => set("comex_tp_moeda", v)}
              ajuda="Moeda do contrato. O valor nela é informado em cada nota avulsa."
            />
            <Selecao
              rotulo="Mecanismo de apoio ao prestador"
              largo
              valor={valor.comex_mec_af_p}
              opcoes={op?.comex_mec_af_p}
              onChange={(v) => set("comex_mec_af_p", v)}
            />
            <Selecao
              rotulo="Mecanismo de apoio ao tomador"
              largo
              valor={valor.comex_mec_af_t}
              opcoes={op?.comex_mec_af_t}
              onChange={(v) => set("comex_mec_af_t", v)}
            />
            <Selecao
              rotulo="Movimentação temporária de bens"
              largo
              valor={valor.comex_mov_temp_bens}
              opcoes={op?.comex_mov_temp_bens}
              onChange={(v) => set("comex_mov_temp_bens", v)}
              ajuda="Vinculada a uma declaração: o número da DI ou do RE vai na nota avulsa."
            />
            <Selecao
              rotulo="Compartilhar com o MDIC"
              largo
              valor={valor.comex_mdic}
              opcoes={op?.comex_mdic}
              onChange={(v) => set("comex_mdic", v)}
            />
          </div>
        </section>
      )}

      <section>
        <h3 className="secao-titulo">Tributação municipal</h3>
        <div className="grade-form">
          <Pergunta
            texto="Há retenção do ISSQN pelo Tomador?"
            sim={retido}
            onChange={(sim) => onChange({ ...valor, tp_ret_issqn: sim ? "2" : "1" })}
          />
          {retido && (
            <Selecao
              rotulo="Quem retém o ISSQN"
              valor={valor.tp_ret_issqn}
              opcoes={[
                { valor: "2", descricao: "Tomador" },
                { valor: "3", descricao: "Intermediário" },
              ]}
              onChange={(v) => set("tp_ret_issqn", v ?? "2")}
              ajuda={valor.tp_ret_issqn === "3" ? "Com intermediário a nota sai só pela nota avulsa, que pede os dados dele." : undefined}
            />
          )}
          {exigeAliquota ? (
            <Texto
              rotulo="Alíquota (%)"
              valor={pctTela(valor.p_aliq_iss)}
              onChange={(v) => onChange({ ...valor, p_aliq_iss: pctDado(v), p_aliq_iss_atualizada_em: hoje() })}
              obrigatorio
              ajuda={
                "Alíquota efetiva do Simples no mês, de 1,80% a 5,00%. A cada emissão o sistema pergunta se ela continua valendo." +
                (valor.p_aliq_iss_atualizada_em ? ` Atualizada em ${dataTela(valor.p_aliq_iss_atualizada_em)}.` : "") +
                (aliquotaPrefeitura ? ` A prefeitura cadastrou ${pctTela(aliquotaPrefeitura)}% para o serviço.` : "")
              }
              num
            />
          ) : (
            <Mostrado
              rotulo="Alíquota"
              num={incide && !!aliquotaPrefeitura}
              valor={!incide ? "—" : aliquotaPrefeitura ? `${pctTela(aliquotaPrefeitura)} %` : "Definida pela prefeitura"}
              ajuda={
                !incide
                  ? "Sem ISSQN nesta operação."
                  : aliquotaPrefeitura
                    ? `Cadastrada pela prefeitura no Sistema Nacional para ${conferencia?.competencia}. A nota não informa alíquota: a Sefin aplica esta.`
                    : "A nota não informa alíquota: a Sefin aplica a que a prefeitura cadastrou."
              }
            />
          )}
          {incide && (
            <>
              <Pergunta
                texto="A exigibilidade do ISSQN está suspensa?"
                sim={!!valor.tp_susp}
                onChange={(sim) => onChange({ ...valor, tp_susp: sim ? "1" : null, n_processo: sim ? valor.n_processo : null })}
                ajuda="Por decisão judicial ou processo administrativo que suspende a cobrança do ISSQN."
              />
              {valor.tp_susp && (
                <>
                  <Selecao
                    rotulo="Motivo da suspensão"
                    valor={valor.tp_susp}
                    opcoes={op?.tp_susp}
                    onChange={(v) => set("tp_susp", v ?? "1")}
                  />
                  <Texto
                    rotulo="Número do processo"
                    valor={valor.n_processo}
                    onChange={(v) => set("n_processo", v.replace(/\D/g, "").slice(0, 30) || null)}
                    obrigatorio
                    ajuda="30 dígitos, sem pontos. Se o número tiver menos, complete com zeros à esquerda."
                    erro={valor.n_processo && valor.n_processo.length !== 30 ? `Tem ${valor.n_processo.length} de 30 dígitos.` : undefined}
                    num
                  />
                </>
              )}
              {regime?.op_simp_nac !== "2" && (
                <Pergunta
                  texto="Há benefício municipal?"
                  sim={valor.n_bm !== null && valor.n_bm !== undefined}
                  onChange={(sim) =>
                    onChange({ ...valor, n_bm: sim ? valor.n_bm ?? "" : null, p_red_bcbm: null, v_red_bcbm: null })
                  }
                  ajuda="Isenção, redução da base ou alíquota diferenciada que a prefeitura cadastrou no Sistema Nacional, com número próprio."
                />
              )}
              {valor.n_bm !== null && valor.n_bm !== undefined && (
                <>
                  <Texto
                    rotulo="Número do benefício municipal"
                    valor={valor.n_bm}
                    onChange={(v) => set("n_bm", v.replace(/\D/g, "").slice(0, 14))}
                    obrigatorio
                    ajuda="14 dígitos, como a prefeitura divulga."
                    erro={valor.n_bm && valor.n_bm.length !== 14 ? `Tem ${valor.n_bm.length} de 14 dígitos.` : undefined}
                    num
                  />
                  <label className="campo">
                    <span>Redução da base de cálculo</span>
                    <select
                      value={beneficioEmReais ? "reais" : "pct"}
                      onChange={(e) => {
                        setBeneficioEmReais(e.target.value === "reais");
                        onChange({ ...valor, p_red_bcbm: null, v_red_bcbm: null });
                      }}
                    >
                      <option value="pct">Em percentual</option>
                      <option value="reais">Em reais</option>
                    </select>
                    <small>Só se o benefício for de redução da base. Isenção e alíquota diferenciada não pedem valor.</small>
                  </label>
                  {beneficioEmReais ? (
                    <Texto
                      rotulo="Redução (R$)"
                      valor={(valor.v_red_bcbm ?? "").replace(".", ",")}
                      onChange={(v) => set("v_red_bcbm", pctDado(v))}
                      ajuda="O mesmo valor em todas as notas com esta tributação. Vazio = sem redução."
                      num
                    />
                  ) : (
                    <Texto
                      rotulo="Redução (%)"
                      valor={pctTela(valor.p_red_bcbm)}
                      onChange={(v) => set("p_red_bcbm", pctDado(v))}
                      ajuda="Vazio = sem redução."
                      num
                    />
                  )}
                </>
              )}
            </>
          )}
        </div>
      </section>

      <section>
        <h3 className="secao-titulo">Tributação federal</h3>
        <div className="grade-form grade-form--rotulos-longos">
          <Selecao
            rotulo="Situação Tributária do PIS/COFINS"
            valor={valor.cst_pis_cofins}
            opcoes={op?.cst_pis_cofins}
            vazio="Não informar"
            onChange={(v) =>
              // Sem CST não há PIS/COFINS na nota, nem retenção deles.
              onChange(
                v
                  ? { ...valor, cst_pis_cofins: v }
                  : { ...valor, cst_pis_cofins: null, p_aliq_pis: null, p_aliq_cofins: null, tp_ret_pis_cofins: null, p_ret_csll: null },
              )
            }
          />
          {valor.cst_pis_cofins && (
            <>
              <Selecao
                rotulo="Tipo de retenção do PIS/COFINS/CSLL"
                valor={valor.tp_ret_pis_cofins}
                opcoes={op?.tp_ret_pis_cofins}
                vazio="Não informar"
                onChange={(v) => onChange({ ...valor, tp_ret_pis_cofins: v, p_ret_csll: v && RETENCOES[v]?.[2] ? valor.p_ret_csll : null })}
              />
              <Texto
                rotulo="Alíquota do PIS (%)"
                valor={pctTela(valor.p_aliq_pis)}
                onChange={(v) => set("p_aliq_pis", pctDado(v))}
                obrigatorio={!!retencao?.[0]}
                ajuda={retencao?.[0] ? "Retido: sai do valor líquido." : "Sobre o valor do serviço. Vazio = não informar."}
                num
              />
              <Texto
                rotulo="Alíquota da COFINS (%)"
                valor={pctTela(valor.p_aliq_cofins)}
                onChange={(v) => set("p_aliq_cofins", pctDado(v))}
                obrigatorio={!!retencao?.[1]}
                ajuda={retencao?.[1] ? "Retida: sai do valor líquido." : "Sobre o valor do serviço. Vazio = não informar."}
                num
              />
            </>
          )}
          <Texto
            rotulo="IRRF (%)"
            valor={pctTela(valor.p_ret_irrf)}
            onChange={(v) => set("p_ret_irrf", pctDado(v))}
            ajuda="Imposto de renda retido pelo tomador. Vazio = sem retenção."
            num
          />
          {(retemCsll || valor.p_ret_csll) && (
            <Texto
              rotulo="Contribuições Sociais - Retidas (%)"
              valor={pctTela(valor.p_ret_csll)}
              onChange={(v) => set("p_ret_csll", pctDado(v))}
              obrigatorio={retemCsll}
              ajuda="CSLL retida, como pede o tipo de retenção escolhido."
              num
            />
          )}
          <Texto
            rotulo="Contribuição Previdenciária - Retida (%)"
            valor={pctTela(valor.p_ret_cp)}
            onChange={(v) => set("p_ret_cp", pctDado(v))}
            ajuda="INSS retido pelo tomador. Vazio = sem retenção."
            num
          />
        </div>
        <p className="dica-grade">
          Em percentual, porque a mesma tributação vale para notas de valores diferentes: o sistema calcula o valor em
          reais de cada nota.{!valor.cst_pis_cofins && " Para reter PIS, COFINS ou CSLL, escolha antes a situação tributária do PIS/COFINS."}
        </p>
      </section>

      {valor.ibscbs && (
        <section>
          <h3 className="secao-titulo">IBS/CBS</h3>
          <CamposIbsCbs valor={valor} onChange={onChange} servico={servico} />
        </section>
      )}

      <section>
        <h3 className="secao-titulo">Valor aproximado dos tributos</h3>
        <div className="grade-form">
          {regime?.op_simp_nac === "1" ? (
            <>
              <Texto rotulo="Federais (%)" valor={pctTela(valor.p_tot_trib_fed)} onChange={(v) => set("p_tot_trib_fed", pctDado(v))} num />
              <Texto rotulo="Estaduais (%)" valor={pctTela(valor.p_tot_trib_est)} onChange={(v) => set("p_tot_trib_est", pctDado(v))} num />
              <Texto rotulo="Municipais (%)" valor={pctTela(valor.p_tot_trib_mun)} onChange={(v) => set("p_tot_trib_mun", pctDado(v))} num />
            </>
          ) : regime?.op_simp_nac === "2" ? (
            <p className="dica-grade largo">MEI não informa o valor aproximado dos tributos.</p>
          ) : (
            <Texto
              rotulo="Alíquota no Simples Nacional (%)"
              valor={pctTela(valor.p_tot_trib_sn)}
              onChange={(v) => set("p_tot_trib_sn", pctDado(v))}
              ajuda="Obrigatória para ME/EPP optante do Simples (Lei 12.741/2012)."
              obrigatorio
              num
            />
          )}
        </div>
      </section>

      {!semPrevia && <PreviaValores valor={valor} aliquotaIss={aliquotaIss} incide={incide} />}
    </>
  );
}

/** Resultado da consulta à prefeitura, embaixo do serviço: só aparece quando há algo a fazer. */
function SituacaoPrefeitura({ consulta }: { consulta: ReturnType<typeof useConferencia> }) {
  const c = consulta.data;
  if (consulta.isFetching)
    return (
      <p className="dica-grade" aria-live="polite">
        <Spinner /> Consultando a prefeitura no Sistema Nacional…
      </p>
    );
  if (consulta.error)
    return (
      <p className="dica-grade campo__erro" aria-live="polite">
        {consulta.error instanceof Error ? consulta.error.message : "Não foi possível consultar a prefeitura."}
      </p>
    );
  if (!c || c.situacao === "encontrado") return null;
  if (c.situacao === "nao_encontrado")
    return (
      <div className="aviso" style={{ marginTop: 12 }} aria-live="polite">
        <div>
          <strong>A prefeitura não tem este serviço cadastrado</strong>
          {c.mensagem}
          {c.detalhe && <small style={{ display: "block", marginTop: 4 }}>Resposta do Sistema Nacional: {c.detalhe}</small>}
        </div>
      </div>
    );
  if (!c.municipio) return null; // sem um município só para consultar (a ajuda do município de incidência explica)
  return (
    <p className="dica-grade" aria-live="polite">
      {c.mensagem}{" "}
      <button type="button" className="btn-texto" onClick={consulta.consultarDeNovo}>
        Consultar de novo
      </button>
    </p>
  );
}

/** CST e classificação do IBS/CBS em listas, como no portal; as do Anexo VIII para o serviço vêm primeiro. */
function CamposIbsCbs({
  valor,
  onChange,
  servico,
}: {
  valor: DadosTributacao;
  onChange: (t: DadosTributacao) => void;
  servico: ServicoDetalhe | null | undefined;
}) {
  const op = useOpcoes().data;
  const [livre, setLivre] = useState(false);
  const set = <K extends keyof DadosTributacao>(k: K, v: DadosTributacao[K]) => onChange({ ...valor, [k]: v });
  const classes = (op?.ibscbs_classes ?? []) as ClasseIbsCbs[];
  const cst = (valor.ibscbs_c_class_trib || "").slice(0, 3);
  const sugeridas = new Set(servico?.c_class_trib.map((c) => c.codigo));
  const doCst = classes.filter((c) => c.cst === cst);
  const atual = classes.find((c) => c.valor === valor.ibscbs_c_class_trib);
  // Classificação nova: os grupos que ela não pede saem (E0964/E0971); os que pede ficam para preencher.
  const trocarClasse = (codigo: string) => {
    const c = classes.find((x) => x.valor === codigo);
    onChange({
      ...valor,
      ibscbs_c_class_trib: codigo,
      ibscbs_c_class_trib_reg: c?.trib_regular ? valor.ibscbs_c_class_trib_reg : null,
      ...(c?.diferimento ? {} : { ibscbs_p_dif_uf: null, ibscbs_p_dif_mun: null, ibscbs_p_dif_cbs: null }),
    });
  };
  const regulares = classes.filter((c) => !c.trib_regular && !c.diferimento);
  const opcao = (c: ClasseIbsCbs) => (
    <option key={c.valor} value={c.valor}>
      {c.valor} - {c.descricao}
      {reducao(c)}
    </option>
  );
  const escolherCst = (novo: string) => {
    // Ao trocar o CST, fica a classificação indicada para o serviço (se houver nesse CST) ou a primeira dele.
    const lista = classes.filter((c) => c.cst === novo);
    const escolhida = lista.find((c) => sugeridas.has(c.valor)) ?? lista[0];
    if (escolhida) trocarClasse(escolhida.valor);
  };
  return (
    <>
      <div className="grade-form">
        <Selecao
          rotulo="Código de Situação Tributária"
          valor={cst || null}
          opcoes={op?.ibscbs_cst}
          onChange={(v) => v && escolherCst(v)}
          ajuda="Os 3 primeiros dígitos da classificação."
        />
        {livre ? (
          <Texto
            rotulo="Código de Classificação Tributária"
            valor={valor.ibscbs_c_class_trib}
            onChange={(v) => trocarClasse(v.replace(/\D/g, "").slice(0, 6))}
            obrigatorio
            ajuda="6 dígitos, da tabela oficial de classificação tributária."
            num
          />
        ) : (
          <label className="campo largo">
            <span>Código de Classificação Tributária</span>
            <select
              value={valor.ibscbs_c_class_trib}
              onChange={(e) => (e.target.value === "__outro" ? setLivre(true) : trocarClasse(e.target.value))}
            >
              {!atual && valor.ibscbs_c_class_trib && (
                <option value={valor.ibscbs_c_class_trib}>{valor.ibscbs_c_class_trib} - fora da tabela para NFS-e</option>
              )}
              {servico && doCst.some((c) => sugeridas.has(c.valor)) && (
                <optgroup label={`Indicadas para ${codigoServicoTela(servico.codigo)} (Anexo VIII)`}>
                  {doCst.filter((c) => sugeridas.has(c.valor)).map(opcao)}
                </optgroup>
              )}
              <optgroup label={`CST ${cst || "—"}`}>{doCst.filter((c) => !sugeridas.has(c.valor)).map(opcao)}</optgroup>
              <option value="__outro">Outro código…</option>
            </select>
            <small>Algumas classificações reduzem ou isentam o IBS/CBS: confirme a do seu serviço com a contabilidade.</small>
          </label>
        )}
        {atual?.trib_regular && (
          <label className="campo largo campo--obrigatorio">
            <span>Classificação na tributação regular</span>
            <select value={valor.ibscbs_c_class_trib_reg ?? ""} onChange={(e) => set("ibscbs_c_class_trib_reg", e.target.value || null)}>
              <option value="">Escolha</option>
              {regulares.map(opcao)}
            </select>
            <small>Esta classificação é de um regime especial: a Sefin pede também a que valeria sem ele (em geral 000001).</small>
          </label>
        )}
        {atual?.diferimento && (
          <>
            <Texto
              rotulo="Diferimento do IBS estadual (%)"
              valor={pctTela(valor.ibscbs_p_dif_uf)}
              onChange={(v) => set("ibscbs_p_dif_uf", pctDado(v))}
              obrigatorio
              num
            />
            <Texto
              rotulo="Diferimento do IBS municipal (%)"
              valor={pctTela(valor.ibscbs_p_dif_mun)}
              onChange={(v) => set("ibscbs_p_dif_mun", pctDado(v))}
              obrigatorio
              num
            />
            <Texto
              rotulo="Diferimento da CBS (%)"
              valor={pctTela(valor.ibscbs_p_dif_cbs)}
              onChange={(v) => set("ibscbs_p_dif_cbs", pctDado(v))}
              obrigatorio
              ajuda="Classificação de diferimento: a Sefin exige os três percentuais (0 vale)."
              num
            />
          </>
        )}
        <Selecao
          rotulo="Uso ou consumo pessoal"
          valor={valor.ibscbs_ind_final}
          opcoes={op?.ibscbs_ind_final}
          vazio="Não informar"
          onChange={(v) => set("ibscbs_ind_final", v)}
        />
      </div>
      {servico && servico.c_class_trib.some((c) => c.cst !== cst) && (
        <p className="dica-grade">
          O Anexo VIII também indica para {codigoServicoTela(servico.codigo)}:{" "}
          {servico.c_class_trib
            .filter((c) => c.cst !== cst)
            .map((c, i) => (
              <span key={c.codigo}>
                {i > 0 && "; "}
                <button type="button" className="btn-texto num" onClick={() => trocarClasse(c.codigo)}>
                  {c.codigo}
                </button>{" "}
                {c.nome}
                {reducao(c)}
              </span>
            ))}
          .
        </p>
      )}
    </>
  );
}

const reducao = (c: { reducao_ibs: number; reducao_cbs: number }) =>
  c.reducao_ibs || c.reducao_cbs
    ? c.reducao_ibs === c.reducao_cbs
      ? `, redução de ${c.reducao_ibs}%`
      : `, redução de ${c.reducao_ibs}% no IBS e ${c.reducao_cbs}% na CBS`
    : "";

// Percentuais: a tela usa vírgula, os dados guardam ponto ("2.50"); vazio = não informar.
const pctTela = (v: string | null) => (v ?? "").replace(".", ",");
const pctDado = (v: string) => v.replace(/[^\d,.]/g, "").replace(",", ".") || null;
const hoje = () => new Date().toLocaleDateString("sv-SE"); // AAAA-MM-DD no fuso do navegador
const dataTela = (iso: string) => iso.split("-").reverse().join("/");

function de(base: number, p: string | null): number {
  const n = Number(p ?? "");
  return p && Number.isFinite(n) ? Math.round(base * n) / 100 : 0;
}

// tpRetPisCofins → [PIS, COFINS, CSLL] retidos (igual a RETENCOES_PIS_COFINS no backend).
const RETENCOES: Record<string, [boolean, boolean, boolean]> = {
  "0": [false, false, false],
  "1": [true, true, true],
  "2": [false, false, false],
  "3": [true, true, true],
  "4": [true, true, false],
  "5": [true, false, false],
  "6": [false, true, false],
  "7": [false, true, true],
  "8": [false, false, true],
  "9": [true, false, true],
};

const reais = (v: number) => moeda(v.toFixed(2));

/**
 * Prévia dos valores da NFS-e, no formato da revisão do Emissor Nacional, numa nota de exemplo. O ISSQN usa a
 * alíquota do Simples (quando a Sefin exige) ou a que a prefeitura cadastrou; sem ela, fica para a Sefin calcular.
 */
/**
 * Valores de uma nota de verdade com a tributação escolhida (nota avulsa): ISSQN, tributos federais, retenções e o
 * líquido, como na prévia da tela de tributação. A alíquota do ISS vem da mesma regra (a do Simples quando a Sefin
 * exige, senão a cadastrada na prefeitura).
 */
export function ValoresDaNota({
  tributacao,
  valor,
  regime,
  descIncond = 0,
  descCond = 0,
  ajuste = 0,
}: {
  tributacao: DadosTributacao;
  valor: number;
  regime: Regime | null | undefined;
  descIncond?: number;
  descCond?: number;
  /** Dedução/redução da base do ISSQN, em R$. */
  ajuste?: number;
}) {
  const conferencia = useConferencia(tributacao.c_trib_nac, tributacao.c_loc_prestacao).data;
  const incide = tributacao.trib_issqn === "1";
  const aliquotaPrefeitura = conferencia?.situacao === "encontrado" ? conferencia.aliquota : null;
  const aliquotaIss = !incide ? null : issExigeAliquota(regime, tributacao) ? tributacao.p_aliq_iss : aliquotaPrefeitura;
  return (
    <PreviaValores valor={tributacao} aliquotaIss={aliquotaIss} incide={incide} valorNota={valor} descIncond={descIncond} descCond={descCond} ajuste={ajuste} />
  );
}

function PreviaValores({
  valor,
  aliquotaIss,
  incide,
  valorNota,
  descIncond = 0,
  descCond = 0,
  ajuste = 0,
}: {
  valor: DadosTributacao;
  aliquotaIss: string | null;
  incide: boolean;
  /** Valor de uma nota de verdade (nota avulsa): troca a nota de exemplo por ele. */
  valorNota?: number;
  descIncond?: number;
  descCond?: number;
  ajuste?: number;
}) {
  const [exemplo, setExemplo] = useState("1.000,00");
  const servico = valorNota ?? (Number(exemplo.replace(/\./g, "").replace(",", ".")) || 0);
  // Desconto incondicionado e benefício municipal reduzem a base (E1295); o condicionado só sai do líquido (E1508).
  const base = Math.max(servico - descIncond, 0);
  // Dedução/redução da base (só do ISSQN) sai antes do benefício municipal.
  const deducao = incide ? Math.min(ajuste, base) : 0;
  const beneficio =
    incide && valor.n_bm ? (valor.v_red_bcbm ? Number(valor.v_red_bcbm) : de(base - deducao, valor.p_red_bcbm)) : 0;
  const baseIss = Math.max(base - deducao - beneficio, 0);
  const [pisRet, cofinsRet, csllRet] = (valor.cst_pis_cofins && RETENCOES[valor.tp_ret_pis_cofins ?? ""]) || [false, false, false];
  const issRetido = incide && ["2", "3"].includes(valor.tp_ret_issqn);
  const iss = aliquotaIss ? de(baseIss, aliquotaIss) : null;
  const pis = valor.cst_pis_cofins ? de(base, valor.p_aliq_pis) : 0;
  const cofins = valor.cst_pis_cofins ? de(base, valor.p_aliq_cofins) : 0;
  const irrf = de(base, valor.p_ret_irrf);
  const csll = csllRet ? de(base, valor.p_ret_csll) : 0;
  const cp = de(base, valor.p_ret_cp);
  const retidos = (issRetido ? iss ?? 0 : 0) + (pisRet ? pis : 0) + (cofinsRet ? cofins : 0) + irrf + csll + cp;
  const linha = (rotulo: string, v: ReactNode, destaque = false) => (
    <div className={`previa-nfse__linha${destaque ? " previa-nfse__linha--total" : ""}`}>
      <dt>{rotulo}</dt>
      <dd className={typeof v === "string" && /\d/.test(v) ? "num" : undefined}>{v}</dd>
    </div>
  );
  return (
    <section className="previa-nfse">
      <div className="previa-nfse__cabeca">
        <h3 className="secao-titulo">{valorNota === undefined ? "Prévia dos valores da NFS-e" : "Valores desta nota"}</h3>
        {valorNota === undefined && (
          <label className="previa-nfse__exemplo">
            <span>Numa nota de R$</span>
            <input className="num" inputMode="decimal" value={exemplo} onChange={(e) => setExemplo(e.target.value.replace(/[^\d.,]/g, ""))} />
          </label>
        )}
      </div>
      <h4>ISSQN calculado</h4>
      <dl>
        {linha("Serviço prestado", reais(servico))}
        {descIncond > 0 && linha("Desconto incondicionado", reais(descIncond))}
        {deducao > 0 && linha("Dedução/redução da base", reais(deducao))}
        {beneficio > 0 && linha("Redução do benefício municipal", reais(beneficio))}
        {linha("Base de cálculo", incide ? reais(baseIss) : "—")}
        {linha("Alíquota aplicada", !incide ? "—" : aliquotaIss ? `${pctTela(aliquotaIss)}%` : "a da prefeitura")}
        {linha(issRetido ? "ISSQN retido" : "ISSQN", !incide ? reais(0) : iss !== null ? reais(iss) : "calculado pela Sefin")}
      </dl>
      <h4>Tributação federal</h4>
      <dl>
        {linha(pisRet ? "PIS - Retido" : "PIS - Débito Apuração Própria", reais(pis))}
        {linha(cofinsRet ? "COFINS - Retida" : "COFINS - Débito Apuração Própria", reais(cofins))}
        {linha("Imposto de Renda Retido na Fonte (IRRF)", reais(irrf))}
        {linha("Contribuições Sociais - Retidas", reais(csll))}
        {linha("Contribuição Previdenciária - Retida", reais(cp))}
      </dl>
      <h4>Valor líquido da NFS-e</h4>
      <dl>
        {linha("Serviço prestado", reais(servico))}
        {descIncond + descCond > 0 && linha("Descontos", reais(descIncond + descCond))}
        {linha("Valor total de tributos retidos", reais(retidos))}
        {linha("Valor líquido da NFS-e", reais(servico - descIncond - descCond - retidos), true)}
      </dl>
      {issRetido && iss === null && (
        <p className="dica-grade">O ISSQN retido também sai do líquido; a Sefin calcula com a alíquota da prefeitura.</p>
      )}
    </section>
  );
}
