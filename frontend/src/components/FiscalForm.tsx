import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { api, type Fiscal, type Municipio, type Opcoes } from "../api";
import { formatarDocumento } from "../format";

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
    <label className={`campo${pendente ? " campo--erro" : ""}`} style={{ position: "relative" }}>
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
        <ul
          role="listbox"
          style={{
            position: "absolute", top: "100%", left: 0, right: 0, zIndex: 5, margin: "4px 0 0", padding: 4,
            listStyle: "none", background: "var(--folha)", border: "1px solid var(--linha-forte)", borderRadius: 6,
            boxShadow: "var(--sombra)", maxHeight: 240, overflow: "auto",
          }}
        >
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

function Selecao(props: {
  rotulo: string;
  valor: string | null;
  opcoes?: { valor: string; descricao: string }[];
  onChange: (v: string | null) => void;
  vazio?: string;
  ajuda?: string;
}) {
  return (
    <label className="campo">
      <span>{props.rotulo}</span>
      <select value={props.valor ?? ""} onChange={(e) => props.onChange(e.target.value || null)}>
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
  num?: boolean;
  largo?: boolean;
  placeholder?: string;
}) {
  return (
    <label className={`campo ${props.largo ? "largo" : ""}`}>
      <span>{props.rotulo}</span>
      <input
        className={props.num ? "num" : undefined}
        value={props.valor ?? ""}
        placeholder={props.placeholder}
        onChange={(e) => props.onChange(e.target.value)}
      />
      {props.ajuda && <small>{props.ajuda}</small>}
    </label>
  );
}

export function FiscalForm({ valor, onChange }: { valor: Fiscal; onChange: (f: Fiscal) => void }) {
  const op = useOpcoes().data;
  const set = <K extends keyof Fiscal>(k: K, v: Fiscal[K]) => onChange({ ...valor, [k]: v });
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 26 }}>
      <section>
        <h3 className="secao-titulo">Prestador</h3>
        <div className="grade-form">
          <Texto
            rotulo="CNPJ"
            valor={formatarDocumento(valor.cnpj)}
            onChange={(v) => set("cnpj", v.replace(/[^0-9A-Za-z]/g, ""))}
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
          <Selecao
            rotulo="Regime especial de tributação"
            valor={valor.reg_esp_trib}
            opcoes={op?.reg_esp_trib}
            onChange={(v) => set("reg_esp_trib", v ?? "0")}
          />
        </div>
      </section>

      <section>
        <h3 className="secao-titulo">Serviço</h3>
        <div className="grade-form">
          <Texto
            rotulo="Código de tributação nacional"
            valor={valor.c_trib_nac}
            onChange={(v) => set("c_trib_nac", v.replace(/\D/g, ""))}
            ajuda="6 dígitos. 171901 = contabilidade."
            num
          />
          <Texto
            rotulo="Código NBS"
            valor={valor.c_nbs}
            onChange={(v) => set("c_nbs", v.replace(/\D/g, "") || null)}
            ajuda="9 dígitos."
            num
          />
          <Texto
            rotulo="Código de tributação municipal"
            valor={valor.c_trib_mun}
            onChange={(v) => set("c_trib_mun", v || null)}
            ajuda="Opcional."
            num
          />
          <CampoMunicipio
            rotulo="Local da prestação"
            codigo={valor.c_loc_prestacao}
            onChange={(c) =>
              // Quase sempre é o mesmo município: preenche o do prestador se ainda estiver vazio.
              onChange({ ...valor, c_loc_prestacao: c, c_mun_emissor: valor.c_mun_emissor || (c ?? "") })
            }
            ajuda="Normalmente o mesmo município do prestador."
          />
        </div>
      </section>

      <section>
        <h3 className="secao-titulo">Tributos</h3>
        <div className="grade-form">
          <Selecao
            rotulo="Tributação do ISSQN"
            valor={valor.trib_issqn}
            opcoes={op?.trib_issqn}
            onChange={(v) => set("trib_issqn", v ?? "1")}
          />
          <Selecao
            rotulo="Retenção do ISSQN"
            valor={valor.tp_ret_issqn}
            opcoes={op?.tp_ret_issqn}
            onChange={(v) => set("tp_ret_issqn", v ?? "1")}
          />
          <Selecao
            rotulo="CST do PIS/COFINS"
            valor={valor.cst_pis_cofins}
            opcoes={op?.cst_pis_cofins}
            vazio="Não informar"
            onChange={(v) => set("cst_pis_cofins", v)}
          />
          <Selecao
            rotulo="Retenção de PIS/COFINS/CSLL"
            valor={valor.tp_ret_pis_cofins}
            opcoes={op?.tp_ret_pis_cofins}
            vazio="Não informar"
            onChange={(v) => set("tp_ret_pis_cofins", v)}
          />
          <Texto
            rotulo="% aproximado de tributos (Simples)"
            valor={valor.p_tot_trib_sn}
            onChange={(v) => set("p_tot_trib_sn", v.replace(",", ".") || null)}
            ajuda="Lei 12.741/2012. Vazio = não informar."
            num
          />
        </div>
      </section>
    </div>
  );
}
