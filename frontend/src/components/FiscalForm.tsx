import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import {
  api,
  type DadosTributacao,
  type Fiscal,
  issExigeAliquota,
  type Municipio,
  type Opcoes,
  type Regime,
} from "../api";
import { formatarDocumento, moeda } from "../format";

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

      <CamposTributacao
        valor={valor}
        // Quase sempre é o mesmo município: o local da prestação preenche o do prestador se ainda estiver vazio.
        onChange={(t) => onChange({ ...valor, ...t, c_mun_emissor: valor.c_mun_emissor || (t.c_loc_prestacao ?? "") })}
        regime={valor}
      />
    </div>
  );
}

/**
 * Serviço, tributos e IBS/CBS: o que muda de uma tributação para outra. Usado nos padrões da nota
 * (Configurações), nas tributações predefinidas e na nota avulsa com tributação própria.
 */
export function CamposTributacao({
  valor,
  onChange,
  regime,
}: {
  valor: DadosTributacao;
  onChange: (t: DadosTributacao) => void;
  /** Regime do prestador (Configurações): decide quais alíquotas a nota aceita. */
  regime: Regime | null | undefined;
}) {
  const op = useOpcoes().data;
  const retido = ["2", "3"].includes(valor.tp_ret_issqn);
  const exigeAliquota = issExigeAliquota(regime, valor);
  const set = <K extends keyof DadosTributacao>(k: K, v: DadosTributacao[K]) => onChange({ ...valor, [k]: v });
  const classeConhecida = op?.ibscbs_c_class_trib?.some((o) => o.valor === valor.ibscbs_c_class_trib);
  return (
    <>
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
            ajuda={valor.ibscbs ? "9 dígitos. Obrigatório com IBS/CBS." : "9 dígitos."}
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
        <h3 className="secao-titulo">ISS</h3>
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
            // Retenção pelo intermediário exige os dados dele na nota, que o sistema não envia.
            opcoes={op?.tp_ret_issqn?.filter((o) => o.valor !== "3" || valor.tp_ret_issqn === "3")}
            onChange={(v) => set("tp_ret_issqn", v ?? "1")}
          />
          {exigeAliquota && (
            <Texto
              rotulo="Alíquota do ISS retido (%)"
              valor={pctTela(valor.p_aliq_iss)}
              onChange={(v) => onChange({ ...valor, p_aliq_iss: pctDado(v), p_aliq_iss_atualizada_em: hoje() })}
              ajuda={
                "Alíquota efetiva do Simples no mês, de 1,80% a 5,00%. A cada emissão o sistema pergunta se ela continua valendo." +
                (valor.p_aliq_iss_atualizada_em ? ` Atualizada em ${dataTela(valor.p_aliq_iss_atualizada_em)}.` : "")
              }
              num
            />
          )}
        </div>
        {retido && !exigeAliquota && regime && (
          <p className="dica-grade">
            Com este regime a alíquota do ISS retido é a que a prefeitura cadastrou no Sistema Nacional: a nota não pode
            informar outra. Ela só é pedida para optante do Simples com o ISS apurado pelo Simples.
          </p>
        )}
      </section>

      <section>
        <h3 className="secao-titulo">PIS e COFINS</h3>
        <div className="grade-form">
          <Selecao
            rotulo="CST do PIS/COFINS"
            valor={valor.cst_pis_cofins}
            opcoes={op?.cst_pis_cofins}
            vazio="Não informar"
            onChange={(v) => set("cst_pis_cofins", v)}
          />
          {valor.cst_pis_cofins && (
            <>
              <Texto
                rotulo="Alíquota do PIS (%)"
                valor={pctTela(valor.p_aliq_pis)}
                onChange={(v) => set("p_aliq_pis", pctDado(v))}
                ajuda="Sobre o valor do serviço. Vazio = não informar."
                num
              />
              <Texto
                rotulo="Alíquota da COFINS (%)"
                valor={pctTela(valor.p_aliq_cofins)}
                onChange={(v) => set("p_aliq_cofins", pctDado(v))}
                ajuda="Sobre o valor do serviço. Vazio = não informar."
                num
              />
              <Selecao
                rotulo="Retenção de PIS/COFINS/CSLL"
                valor={valor.tp_ret_pis_cofins}
                opcoes={op?.tp_ret_pis_cofins}
                vazio="Não informar"
                onChange={(v) => set("tp_ret_pis_cofins", v)}
                ajuda="Informar as alíquotas não obriga a reter: só os retidos saem do valor líquido."
              />
            </>
          )}
        </div>
      </section>

      <section>
        <h3 className="secao-titulo">Retenções federais</h3>
        <div className="grade-form">
          <Texto
            rotulo="IRRF retido (%)"
            valor={pctTela(valor.p_ret_irrf)}
            onChange={(v) => set("p_ret_irrf", pctDado(v))}
            ajuda="Vazio = sem retenção."
            num
          />
          <Texto
            rotulo="CSLL retida (%)"
            valor={pctTela(valor.p_ret_csll)}
            onChange={(v) => set("p_ret_csll", pctDado(v))}
            ajuda="Combine com a retenção de PIS/COFINS/CSLL acima."
            num
          />
          <Texto
            rotulo="INSS retido (%)"
            valor={pctTela(valor.p_ret_cp)}
            onChange={(v) => set("p_ret_cp", pctDado(v))}
            ajuda="Contribuição previdenciária (CP). Vazio = sem retenção."
            num
          />
        </div>
        <p className="dica-grade">
          A nota só tem campo de valor para IRRF, CSLL e INSS, e eles são sempre descontados do valor líquido; o sistema
          calcula o valor de cada nota com estes percentuais.
        </p>
      </section>

      <section>
        <h3 className="secao-titulo">Tributos aproximados (Lei 12.741/2012)</h3>
        <div className="grade-form">
          {regime?.op_simp_nac === "1" ? (
            <>
              <Texto rotulo="Federais (%)" valor={pctTela(valor.p_tot_trib_fed)} onChange={(v) => set("p_tot_trib_fed", pctDado(v))} num />
              <Texto rotulo="Estaduais (%)" valor={pctTela(valor.p_tot_trib_est)} onChange={(v) => set("p_tot_trib_est", pctDado(v))} num />
              <Texto rotulo="Municipais (%)" valor={pctTela(valor.p_tot_trib_mun)} onChange={(v) => set("p_tot_trib_mun", pctDado(v))} num />
            </>
          ) : regime?.op_simp_nac === "2" ? (
            <p className="dica-grade">MEI não informa o percentual aproximado de tributos.</p>
          ) : (
            <Texto
              rotulo="% aproximado de tributos (Simples)"
              valor={pctTela(valor.p_tot_trib_sn)}
              onChange={(v) => set("p_tot_trib_sn", pctDado(v))}
              ajuda="Obrigatório para ME/EPP optante do Simples."
              num
            />
          )}
        </div>
      </section>

      <PreviaTributos valor={valor} exigeAliquota={exigeAliquota} />

      <section>
        <h3 className="secao-titulo">IBS e CBS</h3>
        <label className="interruptor">
          <input type="checkbox" checked={valor.ibscbs} onChange={(e) => set("ibscbs", e.target.checked)} />
          <span>
            <strong>Informar IBS e CBS na nota</strong>
            <small>
              A Sefin calcula os valores a partir da classificação abaixo. Sem o grupo, a nota é aceita até 31/12/2026;
              confirme com a contabilidade a partir de quando ele é exigido para você.
            </small>
          </span>
        </label>
        {valor.ibscbs && (
          <div className="grade-form" style={{ marginTop: 14 }}>
            <Selecao
              rotulo="Indicador da operação"
              valor={valor.ibscbs_c_ind_op}
              opcoes={op?.ibscbs_c_ind_op}
              onChange={(v) => set("ibscbs_c_ind_op", v ?? "100301")}
              ajuda="Onde o IBS/CBS é devido. Serviços em geral: 100301."
            />
            <Texto
              rotulo="Classificação tributária (cClassTrib)"
              valor={valor.ibscbs_c_class_trib}
              onChange={(v) => set("ibscbs_c_class_trib", v.replace(/\D/g, "").slice(0, 6))}
              ajuda={
                (classeConhecida
                  ? `${op?.ibscbs_c_class_trib?.find((o) => o.valor === valor.ibscbs_c_class_trib)?.descricao}. `
                  : `CST ${(valor.ibscbs_c_class_trib || "").slice(0, 3) || "—"} (3 primeiros dígitos). `) +
                "Use o código da tabela oficial para o seu serviço: alguns têm redução ou isenção."
              }
              num
            />
            <Selecao
              rotulo="Uso ou consumo pessoal"
              valor={valor.ibscbs_ind_final}
              opcoes={op?.ibscbs_ind_final}
              vazio="Não informar"
              onChange={(v) => set("ibscbs_ind_final", v)}
            />
          </div>
        )}
      </section>
    </>
  );
}

// Percentuais: a tela usa vírgula, os dados guardam ponto ("2.50"); vazio = não informar.
const pctTela = (v: string | null) => (v ?? "").replace(".", ",");
const pctDado = (v: string) => v.replace(/[^\d,.]/g, "").replace(",", ".") || null;
const hoje = () => new Date().toLocaleDateString("sv-SE"); // AAAA-MM-DD no fuso do navegador
const dataTela = (iso: string) => iso.split("-").reverse().join("/");

function de(base: number, p: string | null): number {
  const n = Number(p ?? "");
  return p && Number.isFinite(n) ? Math.round(base * n) / 100 : 0;
}

// tpRetPisCofins → [PIS retido, COFINS retido] (igual a RETENCOES_PIS_COFINS no backend).
const PIS_COFINS_RETIDOS: Record<string, [boolean, boolean]> = {
  "1": [true, true],
  "3": [true, true],
  "4": [true, true],
  "5": [true, false],
  "6": [false, true],
  "7": [false, true],
  "9": [true, false],
};

/** Exemplo numa nota de R$ 1.000,00 com os percentuais da tributação. */
function PreviaTributos({ valor, exigeAliquota }: { valor: DadosTributacao; exigeAliquota: boolean }) {
  const base = 1000;
  const [pisRet, cofinsRet] = (valor.cst_pis_cofins && PIS_COFINS_RETIDOS[valor.tp_ret_pis_cofins ?? ""]) || [false, false];
  const pis = valor.cst_pis_cofins ? de(base, valor.p_aliq_pis) : 0;
  const cofins = valor.cst_pis_cofins ? de(base, valor.p_aliq_cofins) : 0;
  const itens: [string, number, boolean][] = [
    ["ISS retido", exigeAliquota ? de(base, valor.p_aliq_iss) : 0, true],
    ["IRRF", de(base, valor.p_ret_irrf), true],
    ["CSLL", de(base, valor.p_ret_csll), true],
    ["INSS", de(base, valor.p_ret_cp), true],
    [pisRet ? "PIS retido" : "PIS", pis, pisRet],
    [cofinsRet ? "COFINS retida" : "COFINS", cofins, cofinsRet],
  ];
  const visiveis = itens.filter(([, v]) => v > 0);
  if (!visiveis.length) return null;
  const liquido = base - visiveis.filter(([, , r]) => r).reduce((t, [, v]) => t + v, 0);
  return (
    <div className="info-caixa" style={{ flexDirection: "column", gap: 4 }}>
      <strong>Numa nota de {moeda("1000")}</strong>
      <span>
        {visiveis.map(([n, v, r]) => `${n} ${moeda(v.toFixed(2))}${r ? "" : " (informado, não retido)"}`).join(" · ")}
      </span>
      <span>
        Valor líquido: <b className="num">{moeda(liquido.toFixed(2))}</b>
        {!exigeAliquota && ["2", "3"].includes(valor.tp_ret_issqn) ? " menos o ISS retido, calculado pela Sefin com a alíquota do município" : ""}
      </span>
    </div>
  );
}
