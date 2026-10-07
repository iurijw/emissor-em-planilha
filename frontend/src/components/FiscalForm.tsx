import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { api, type Fiscal, type Municipio, type Opcoes } from "../api";
import { formatarDocumento } from "../format";

export function useOpcoes() {
  return useQuery({ queryKey: ["opcoes"], queryFn: () => api.get<Opcoes>("/api/config/opcoes"), staleTime: Infinity });
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
  const [rotulo, setRotulo] = useState<string>("");
  const busca = useQuery({
    queryKey: ["municipios", termo],
    queryFn: () => api.get<Municipio[]>(`/api/municipios?q=${encodeURIComponent(termo)}`),
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

  return (
    <label className="campo" style={{ position: "relative" }}>
      <span>{props.rotulo}</span>
      <input
        aria-describedby={`${id}-ajuda`}
        value={aberto ? termo : rotulo || props.codigo || ""}
        placeholder="Digite o nome da cidade"
        onFocus={() => {
          setTermo("");
          setAberto(true);
        }}
        onBlur={() => setTimeout(() => setAberto(false), 150)}
        onChange={(e) => setTermo(e.target.value)}
      />
      {aberto && (busca.data?.length ?? 0) > 0 && (
        <ul
          role="listbox"
          style={{
            position: "absolute", top: "100%", left: 0, right: 0, zIndex: 5, margin: "4px 0 0", padding: 4,
            listStyle: "none", background: "var(--folha)", border: "1px solid var(--linha-forte)", borderRadius: 6,
            boxShadow: "var(--sombra)", maxHeight: 240, overflow: "auto",
          }}
        >
          {busca.data!.map((m) => (
            <li key={m.codigo}>
              <button
                type="button"
                className="btn btn--fantasma"
                style={{ width: "100%", justifyContent: "space-between", fontWeight: 400 }}
                onMouseDown={(e) => {
                  e.preventDefault();
                  props.onChange(m.codigo);
                  setAberto(false);
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
      <small id={`${id}-ajuda`}>
        {props.codigo ? <>IBGE <span className="num">{props.codigo}</span>. </> : null}
        {props.ajuda ?? "Digite o nome e escolha na lista."}
      </small>
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
            onChange={(c) => set("c_loc_prestacao", c)}
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
