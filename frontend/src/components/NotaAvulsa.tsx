import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, dadosTributacao, type Cliente, type CopiaNota, type DadosTributacao, type EscolhaTributacaoHistorico } from "../api";
import { documentoValido, formatarDocumento, moeda, parseValor, soDocumento } from "../format";
import { BotaoVariaveis, PreviaDescricao } from "./Variaveis";
import { EmissaoFluxo, type CorpoAvulsa } from "../pages/EmissaoFluxo";
import { ErroCaixa, Gaveta, Spinner } from "../ui";
import { useTributacoes } from "./Abas";
import { buscarClientes, normalizarBusca } from "./EditorNomeCliente";
import { CamposTributacao, LegendaObrigatorio, Pergunta, ValoresDaNota } from "./FiscalForm";
import { corpoPessoa, OutraPessoa, pessoaPronta, pessoaVazia, type DadosPessoa } from "./OutraPessoa";
import { exteriorVazio, faltaNoExterior, TomadorExterior, type DadosExterior } from "./TomadorExterior";
import { ajusteEmReais, corpoDadosNota, DadosDaNota, dadosNotaVazios, type DadosNota } from "./DadosDaNota";
import {
  corpoEvento,
  corpoObra,
  eventoPronto,
  eventoVazio,
  EventoNota,
  exigeEvento,
  exigeObra,
  obraPronta,
  obraVazia,
  ObraNota,
  type DadosEvento,
  type DadosObra,
} from "./ObraEvento";
import { EnderecoTomador, enderecoCompleto, enderecoDe, enderecosIguais, enderecoVazio, type Endereco } from "./EnderecoTomador";
import { BotaoHistorico, HistoricoAvulsa } from "./HistoricoAvulsa";
import { corpoLocacao, ehLocacao, faltaNaLocacao, locacaoDe, LocacaoNota, type DadosLocacao } from "./LocacaoImovel";

type Escolha = "padrao" | "propria" | number;

/**
 * O campo CNPJ/CPF também aceita o nome: texto com letra e espaço, letra acentuada ou letra sem nenhum número é
 * nome (um CNPJ alfanumérico sempre tem números, e é digitado sem espaço).
 */
export function pareceNome(texto: string): boolean {
  const t = texto.trim();
  if (!/[A-Za-zÀ-ÿ]/.test(t)) return false;
  return /\s/.test(t) || /[À-ÿ]/.test(t) || !/\d/.test(t);
}

/** Valor gravado ("10.00") como a tela mostra ("10,00"). */
const valorTela = (v: unknown) => (v === null || v === undefined || v === "" ? "" : String(v).replace(".", ","));
const texto = (v: unknown) => (typeof v === "string" ? v : "");

/** Dados da nota copiada (descontos, pedido, informações complementares) no formato do formulário. */
function dadosNotaDaCopia(n: Record<string, unknown>): DadosNota {
  const emReais = n.ajuste_bc_v != null && n.ajuste_bc_v !== "";
  return {
    v_desc_incond: valorTela(n.v_desc_incond),
    v_desc_cond: valorTela(n.v_desc_cond),
    x_ped: texto(n.x_ped),
    itens_pedido: Array.isArray(n.itens_pedido) ? n.itens_pedido.join(", ") : "",
    x_inf_comp: texto(n.x_inf_comp),
    ajuste_bc: valorTela(emReais ? n.ajuste_bc_v : n.ajuste_bc_p),
    ajuste_em_reais: emReais,
  };
}

/** Intermediário/destinatário gravado na nota → formulário (endereço nacional). */
function pessoaDaCopia(d: unknown): DadosPessoa {
  if (!d || typeof d !== "object") return pessoaVazia;
  const p = d as Record<string, string | undefined>;
  return {
    documento: p.documento ?? "",
    nome: p.nome ?? "",
    im: p.im ?? "",
    email: p.email ?? "",
    endereco: {
      logradouro: p.logradouro ?? null,
      numero: p.numero ?? null,
      complemento: p.complemento ?? null,
      bairro: p.bairro ?? null,
      cep: p.cep ?? null,
      c_mun: p.c_mun ?? null,
    },
  };
}

/** Consulta na Receita de um CNPJ digitado sem cadastro (o servidor cadastra o cliente com o endereço). */
type Consulta = { documento: string; estado: "buscando" | "falhou"; erro?: string };

/** Uma nota só, fora da tabela: cliente, valor, descrição e a tributação (padrão, predefinida ou só desta nota). */
export function NotaAvulsa({ fechar, admin, copia }: { fechar: () => void; admin: boolean; copia?: CopiaNota }) {
  const qc = useQueryClient();
  const trib = useTributacoes();
  const clientesQ = useQuery({ queryKey: ["clientes"], queryFn: () => api.get<Cliente[]>("/api/clientes") });
  const n = copia?.nota ?? {};
  const [nome, setNome] = useState(copia && !n.tomador_exterior ? copia.nome : "");
  // Texto do campo CNPJ/CPF como digitado; o documento sai dele quando não é um nome.
  const [docTexto, setDocTexto] = useState(copia?.documento ? formatarDocumento(copia.documento) : "");
  const [focoDoc, setFocoDoc] = useState(false);
  const docEhNome = pareceNome(docTexto);
  const documento = docEhNome ? "" : soDocumento(docTexto);
  const setDocumento = (d: string) => setDocTexto(formatarDocumento(d));
  const [valor, setValor] = useState(copia ? valorTela(copia.valor) : "");
  const [descricao, setDescricao] = useState(copia?.descricao ?? "");
  const [dadosNota, setDadosNota] = useState<DadosNota>(copia ? dadosNotaDaCopia(n) : dadosNotaVazios);
  const [noExterior, setNoExterior] = useState(!!n.tomador_exterior);
  const [exterior, setExterior] = useState<DadosExterior>(
    n.tomador_exterior ? { ...exteriorVazio, ...(n.tomador_exterior as Partial<DadosExterior>) } : exteriorVazio,
  );
  const [comex, setComex] = useState({ v_serv_moeda: valorTela(n.v_serv_moeda), n_di: texto(n.n_di), n_re: texto(n.n_re) });
  const [obra, setObra] = useState<DadosObra>(obraVazia);
  const [evento, setEvento] = useState<DadosEvento>(eventoVazio);
  // Aluguel se repete todo mês: o imóvel da nota copiada vem junto.
  const [locacao, setLocacao] = useState<DadosLocacao>(locacaoDe(n.locacao as Record<string, string> | undefined));
  const [interm, setInterm] = useState<DadosPessoa>(pessoaDaCopia(n.intermediario));
  const [outroDest, setOutroDest] = useState(!!n.destinatario);
  const [dest, setDest] = useState<DadosPessoa>(pessoaDaCopia(n.destinatario));
  const [historico, setHistorico] = useState<"descricoes" | "tributacoes" | null>(null);
  const [avisoTributacao, setAvisoTributacao] = useState<string | null>(null);
  const [refs, setRefs] = useState("");
  const [escolha, setEscolha] = useState<Escolha>("padrao");
  const [propria, setPropria] = useState<DadosTributacao | null>(null);
  const [focoNome, setFocoNome] = useState(false);
  const [conferir, setConferir] = useState<CorpoAvulsa | null>(null);
  const [emitida, setEmitida] = useState(false);
  const [consulta, setConsulta] = useState<Consulta | null>(null);
  const consultados = useRef(new Set<string>());
  const [endereco, setEndereco] = useState<Endereco>(enderecoDe(undefined));
  const [salvando, setSalvando] = useState(false);
  const [erroEndereco, setErroEndereco] = useState<unknown>(null);

  const sugestoes = useMemo(() => (focoNome ? buscarClientes(clientesQ.data ?? [], nome, 6) : []), [clientesQ.data, nome, focoNome]);
  const cadastro = clientesQ.data?.find((c) => c.documento === documento);
  // Sugestões no campo CNPJ/CPF: pelo nome digitado ou pelo começo/trecho do número (o documento já completo, não).
  const sugestoesDoc = useMemo(
    () => (focoDoc && !cadastro ? buscarClientes(clientesQ.data ?? [], docTexto, 6) : []),
    [clientesQ.data, docTexto, focoDoc, cadastro],
  );
  const valorNum = parseValor(valor);
  const tributacaoDaNota =
    escolha === "propria" ? propria : typeof escolha === "number" ? trib.data?.itens.find((t) => t.id === escolha)?.dados ?? null : trib.data?.padrao ?? null;
  const docInvalido = documento !== "" && !documentoValido(documento);
  // Enquanto digita no campo, só acusa quando já tem o tamanho de um CNPJ.
  const mostrarInvalido = docInvalido && (!focoDoc || documento.length >= 14);
  // Só o nome: procura um cadastro com esse nome exato; sem CNPJ/CPF a nota não pode sair.
  const porNome = useMemo(() => {
    const t = normalizarBusca(nome);
    return t && !documento ? (clientesQ.data ?? []).filter((c) => normalizarBusca(c.nome) === t) : [];
  }, [clientesQ.data, nome, documento]);
  const faltaDocumento = !documento && nome.trim() !== "" && !focoNome && clientesQ.isSuccess && porNome.length !== 1;

  // Ao sair do campo do nome (não a cada redigitação do CNPJ, senão não daria para apagá-lo).
  useEffect(() => {
    if (!focoNome && porNome.length === 1) setDocumento(porNome[0].documento);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focoNome]);

  // CNPJ do cadastro com o nome em branco: mostra o nome que vai na nota.
  useEffect(() => {
    if (cadastro && !nome.trim()) setNome(cadastro.nome);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cadastro]);

  // CNPJ novo: busca na Receita e cadastra o cliente (com endereço), como na tabela.
  useEffect(() => {
    if (documento.length !== 14 || !documentoValido(documento) || !clientesQ.isSuccess || cadastro) return;
    if (consultados.current.has(documento)) return;
    consultados.current.add(documento);
    const doc = documento;
    setConsulta({ documento: doc, estado: "buscando" });
    api
      .post<{ resultados: { documento: string; ok: boolean; nome?: string; erro?: string }[] }>("/api/clientes/consultar-pendentes", {
        documentos: [doc],
      })
      .then(async (r) => {
        const res = r.resultados.find((x) => x.documento === doc);
        await qc.invalidateQueries({ queryKey: ["clientes"] });
        if (res?.ok) {
          setNome((n) => (n.trim() ? n : res.nome ?? ""));
          setConsulta(null);
        } else {
          setConsulta({ documento: doc, estado: "falhou", erro: res?.erro });
        }
      })
      .catch((e) => {
        // Outra consulta em andamento no servidor: deixa tentar de novo ao redigitar.
        if (e instanceof ApiError && e.status === 429) consultados.current.delete(doc);
        setConsulta({ documento: doc, estado: "falhou", erro: e instanceof Error ? e.message : undefined });
      });
  }, [documento, cadastro, clientesQ.isSuccess, qc]);

  // O endereço mostrado acompanha o cadastro (inclusive quando a Receita acaba de preencher).
  useEffect(() => {
    setEndereco(enderecoDe(cadastro));
    setErroEndereco(null);
  }, [documento, cadastro?.atualizado_em]); // eslint-disable-line react-hooks/exhaustive-deps

  const consultaAtual = consulta?.documento === documento ? consulta : null;
  const enderecoAlterado = !enderecosIguais(endereco, enderecoDe(cadastro)) && !(enderecoVazio(endereco) && !cadastro);
  // Exportação, serviço no exterior ou tomador no exterior: a nota leva o valor na moeda estrangeira (comExt).
  const comExterior = noExterior || tributacaoDaNota?.trib_issqn === "3" || (tributacaoDaNota?.c_pais_prestacao ?? null) != null;
  const moedaNum = parseValor(comex.v_serv_moeda);
  // Construção civil e item 12: a nota leva os dados da obra ou do evento (E0370/E0390).
  const precisaObra = exigeObra(tributacaoDaNota?.c_trib_nac);
  const precisaEvento = exigeEvento(tributacaoDaNota?.c_trib_nac);
  const prestadoFora = (tributacaoDaNota?.c_pais_prestacao ?? null) != null;
  // Locação de imóvel (99.03): a nota leva o imóvel e as parcelas fora da base do IBS/CBS (NT 009).
  const precisaLocacao = ehLocacao(tributacaoDaNota?.c_trib_nac);
  // ISS retido pelo intermediário, destinatário do IBS/CBS e NFS-e referenciada (tpOper 2/3) vêm de cada nota.
  const precisaInterm = tributacaoDaNota?.tp_ret_issqn === "3";
  const comIbsCbs = !!tributacaoDaNota?.ibscbs;
  const precisaRefs = comIbsCbs && ["2", "3"].includes(tributacaoDaNota?.ibscbs_tp_oper ?? "");
  const chavesRef = refs.split(/[\s,;]+/).map((c) => c.trim().toUpperCase()).filter(Boolean);
  // ISS retido pelo tomador: a Sefin exige o endereço dele (E0237).
  const exigeEnderecoTomador = tributacaoDaNota?.tp_ret_issqn === "2";
  // O que falta para a nota sair (os campos com * na tela); o servidor confere tudo de novo.
  const faltando = [
    ...(noExterior
      ? faltaNoExterior(exterior).length
        ? [`tomador (${faltaNoExterior(exterior).join(", ")})`]
        : []
      : !documento
        ? ["CNPJ/CPF do tomador"]
        : docInvalido
          ? ["CNPJ/CPF válido do tomador"]
          : exigeEnderecoTomador && !enderecoCompleto(endereco)
            ? ["endereço do tomador"]
            : []),
    ...(valorNum === null || Number(valorNum) <= 0 ? ["valor"] : []),
    ...(comExterior && (moedaNum === null || Number(moedaNum) <= 0) ? ["valor na moeda estrangeira"] : []),
    ...(!descricao.trim() ? ["descrição do serviço"] : []),
    ...(precisaObra && !obraPronta(obra, prestadoFora) ? ["dados da obra"] : []),
    ...(precisaEvento && !eventoPronto(evento, prestadoFora) ? ["dados do evento"] : []),
    ...(precisaLocacao ? faltaNaLocacao(locacao) : []),
    ...(precisaInterm && !pessoaPronta(interm, true) ? ["dados do intermediário"] : []),
    ...(comIbsCbs && outroDest && !pessoaPronta(dest, false) ? ["dados do destinatário"] : []),
    ...(precisaRefs && !(chavesRef.length > 0 && chavesRef.every((c) => /^[0-9A-Z]{50}$/.test(c)))
      ? ["chave da NFS-e referenciada"]
      : []),
  ];
  const pronto = faltando.length === 0 && (noExterior || consultaAtual?.estado !== "buscando");

  function escolherTributacao(e: Escolha) {
    setEscolha(e);
    setAvisoTributacao(null);
    if (e === "propria" && !propria && trib.data?.padrao) setPropria(dadosTributacao(trib.data.padrao));
  }

  // Tributação de uma nota anterior (cópia ou histórico): a padrão, a predefinida que ainda existe igual, ou os campos
  // como estavam na nota em "Só para esta nota".
  function usarTributacao(t: EscolhaTributacaoHistorico) {
    const existe = t.tipo === "predefinida" && trib.data?.itens.some((x) => x.id === t.tributacao_id);
    if (t.tipo === "padrao") escolherTributacao("padrao");
    else if (existe) escolherTributacao(t.tributacao_id!);
    else if (t.dados && trib.data?.padrao) {
      setPropria(dadosTributacao({ ...trib.data.padrao, ...t.dados }));
      setEscolha("propria");
      setAvisoTributacao(
        t.nome === "Só desta nota"
          ? "A tributação veio como estava na nota anterior. Confira antes de emitir."
          : `A tributação “${t.nome}” mudou ou foi excluída depois daquela nota: veio como estava na nota. Confira antes de emitir.`,
      );
    }
  }

  // Nota copiada: aplica a tributação quando a lista de tributações chega.
  const tributacaoCopiada = useRef(false);
  useEffect(() => {
    if (!copia || tributacaoCopiada.current || !trib.data) return;
    tributacaoCopiada.current = true;
    usarTributacao(copia.tributacao);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trib.data]);

  async function abrirConferencia() {
    // Endereço mexido aqui: grava no cadastro antes, porque a nota usa o endereço do cadastro. Tomador novo com nome
    // (o CPF, que a Receita não traz) também entra no cadastro, para aparecer nas próximas notas.
    if (!noExterior && (enderecoAlterado || (!cadastro && nome.trim()))) {
      setErroEndereco(null);
      setSalvando(true);
      try {
        await api.put(`/api/clientes/${documento}`, {
          nome: cadastro?.nome || nome.trim(),
          c_mun: endereco.c_mun,
          cep: endereco.cep,
          logradouro: endereco.logradouro,
          numero: endereco.numero,
          complemento: endereco.complemento,
          bairro: endereco.bairro,
          fone: cadastro?.fone ?? null,
          email: cadastro?.email ?? null,
          inscricao_municipal: cadastro?.inscricao_municipal ?? null,
        });
        await qc.invalidateQueries({ queryKey: ["clientes"] });
      } catch (e) {
        setErroEndereco(e);
        return;
      } finally {
        setSalvando(false);
      }
    }
    setConferir({
      nome: noExterior ? exterior.nome.trim() : nome.trim(),
      documento: noExterior ? "" : documento,
      valor: valorNum ?? valor,
      descricao,
      tributacao_id: typeof escolha === "number" ? escolha : null,
      tributacao: escolha === "propria" ? propria : null,
      ...corpoDadosNota(dadosNota),
      tomador_exterior: noExterior ? { ...exterior } : null,
      obra: precisaObra ? corpoObra(obra) : null,
      evento: precisaEvento ? corpoEvento(evento) : null,
      locacao: precisaLocacao ? corpoLocacao(locacao) : null,
      intermediario: precisaInterm ? corpoPessoa(interm, true) : null,
      destinatario: comIbsCbs && outroDest ? corpoPessoa(dest, false) : null,
      ref_nfse: precisaRefs ? chavesRef : [],
      ...(comExterior
        ? { v_serv_moeda: moedaNum ?? comex.v_serv_moeda, n_di: comex.n_di.trim() || null, n_re: comex.n_re.trim() || null }
        : {}),
    });
  }

  const opcoes: { id: Escolha; titulo: string; detalhe: string | null }[] = [
    { id: "padrao", titulo: trib.data?.padrao_nome || "Tributação padrão", detalhe: trib.data?.padrao_resumo ?? null },
    ...(trib.data?.itens ?? []).map((t) => ({ id: t.id as Escolha, titulo: t.nome, detalhe: t.resumo })),
    { id: "propria", titulo: "Só para esta nota", detalhe: "Parte da tributação padrão; ajuste o que mudar." },
  ];

  return (
    <>
      <Gaveta
        titulo="Nota avulsa"
        fechar={fechar}
        rodape={
          <>
            {faltando.length > 0 && (
              <span className="falta-preencher" aria-live="polite">
                <b>Falta:</b> {faltando.join(", ")}.
              </span>
            )}
            <button className="btn" onClick={fechar}>
              Cancelar
            </button>
            <button
              className="btn btn--primario"
              disabled={!pronto || salvando || (escolha === "propria" && !propria)}
              onClick={abrirConferencia}
            >
              {salvando && <Spinner />}
              Conferir e emitir{valorNum ? <span className="num">· {moeda(valorNum)}</span> : null}
            </button>
          </>
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
          {copia ? (
            <div className="info-caixa copia-nota">
              <strong>Cópia da {copia.numero_nfse ? `NFS-e nº ${copia.numero_nfse}` : "nota"}, para emitir uma nota nova.</strong>
              <span>
                Tomador, valor, descrição e tributação vieram dela. Confira e mude o que precisar; a nota anterior continua como está.
                {!!(n.obra || n.evento) && " Os dados da obra ou do evento não são copiados: preencha de novo."}
              </span>
            </div>
          ) : (
            <p style={{ margin: 0, color: "var(--grafite)" }}>
              Emite uma nota sem mexer na tabela. Ela usa a mesma numeração e aparece na aba Emissões.
            </p>
          )}
          <LegendaObrigatorio />
          <section>
            <h3 className="secao-titulo">Tomador</h3>
            <div className="grade-form">
              <Pergunta
                texto="O tomador está no exterior?"
                sim={noExterior}
                onChange={setNoExterior}
                ajuda={
                  noExterior
                    ? "Sem CNPJ/CPF: a nota leva o NIF e o endereço no país do tomador. Ele não entra no cadastro de clientes."
                    : undefined
                }
              />
              {noExterior ? (
                <TomadorExterior valor={exterior} onChange={setExterior} />
              ) : (
                <>
                  <div className="largo tomador-ou">
                    <label className={`campo campo--obrigatorio${mostrarInvalido ? " campo--erro" : ""}`} style={{ position: "relative" }}>
                      <span>CNPJ / CPF</span>
                      <input
                        className={docEhNome || !docTexto ? undefined : "num"}
                        autoFocus
                        value={docEhNome ? docTexto : formatarDocumento(documento)}
                        onChange={(e) => setDocTexto(e.target.value)}
                        onFocus={() => setFocoDoc(true)}
                        onBlur={() =>
                          window.setTimeout(() => {
                            setFocoDoc(false);
                            // Nome digitado aqui sem escolher da lista: passa para o campo do nome.
                            setDocTexto((t) => {
                              if (!pareceNome(t)) return t;
                              setNome((n) => (n.trim() ? n : t.trim()));
                              return "";
                            });
                          }, 150)
                        }
                        placeholder="Número ou nome do cliente"
                      />
                      {sugestoesDoc.length > 0 && (
                        <Sugestoes
                          clientes={sugestoesDoc}
                          escolher={(c) => {
                            setNome(c.nome);
                            setDocumento(c.documento);
                            setFocoDoc(false);
                          }}
                        />
                      )}
                      {mostrarInvalido ? (
                        <small className="campo__erro">CNPJ/CPF inválido.</small>
                      ) : (
                        <small>
                          {cadastro
                            ? "Cliente cadastrado."
                            : consultaAtual?.estado === "buscando"
                              ? "Buscando a empresa na Receita…"
                              : consultaAtual?.estado === "falhou"
                                ? `Não achei na Receita${consultaAtual.erro ? ` (${consultaAtual.erro.replace(/\.$/, "")})` : ""}.${nome.trim() ? "" : " Digite o nome da empresa."}`
                                : documento.length === 11
                                  ? "CPF sem cadastro: com o nome, entra nos clientes ao conferir a nota."
                                  : " "}
                        </small>
                      )}
                    </label>
                    <span className="tomador-ou__ou" aria-hidden="true">
                      e
                    </span>
                    <label className="campo" style={{ position: "relative" }}>
                      <span>Nome da empresa/pessoa</span>
                      <input
                        value={nome}
                        onChange={(e) => setNome(e.target.value)}
                        onFocus={() => setFocoNome(true)}
                        onBlur={() => window.setTimeout(() => setFocoNome(false), 150)}
                        placeholder="Digite para buscar no cadastro"
                      />
                      {sugestoes.length > 0 && (
                        <Sugestoes
                          clientes={sugestoes}
                          escolher={(c) => {
                            setNome(c.nome);
                            setDocumento(c.documento);
                            setFocoNome(false);
                          }}
                        />
                      )}
                      {faltaDocumento ? (
                        <small className="campo__erro">
                          {porNome.length > 1
                            ? "Há mais de um cliente com esse nome. Escolha na lista ou informe o CNPJ/CPF."
                            : "Nenhum cliente cadastrado com esse nome. Informe o CNPJ/CPF para emitir."}
                        </small>
                      ) : (
                        <small>Em branco, vem do cadastro ou da Receita.</small>
                      )}
                    </label>
                  </div>
                  {documento && !docInvalido && (
                    <div className="largo">
                      <EnderecoTomador
                        key={documento}
                        valor={endereco}
                        onChange={setEndereco}
                        cpf={documento.length === 11}
                        buscando={consultaAtual?.estado === "buscando"}
                        municipio={endereco.c_mun && endereco.c_mun === cadastro?.c_mun ? cadastro.municipio : null}
                        obrigatorio={exigeEnderecoTomador}
                      />
                      {erroEndereco != null && <ErroCaixa erro={erroEndereco} titulo="Não foi possível salvar o endereço" />}
                    </div>
                  )}
                </>
              )}
              <label className="campo campo--obrigatorio">
                <span>Valor</span>
                <input className="num" value={valor} onChange={(e) => setValor(e.target.value)} placeholder="0,00" inputMode="decimal" />
                <small>{valorNum ? moeda(valorNum) : " "}</small>
              </label>
              {comExterior && (
                <>
                  <label className="campo campo--obrigatorio">
                    <span>Valor na moeda estrangeira</span>
                    <input
                      className="num"
                      value={comex.v_serv_moeda}
                      onChange={(e) => setComex({ ...comex, v_serv_moeda: e.target.value })}
                      placeholder="0,00"
                      inputMode="decimal"
                    />
                    <small>Na moeda escolhida na tributação. O valor ao lado continua em reais.</small>
                  </label>
                  {tributacaoDaNota?.comex_mov_temp_bens === "2" && (
                    <label className="campo">
                      <span>Número da DI</span>
                      <input className="num" maxLength={12} value={comex.n_di} onChange={(e) => setComex({ ...comex, n_di: e.target.value })} />
                      <small>Declaração de importação ligada à movimentação temporária de bens.</small>
                    </label>
                  )}
                  {tributacaoDaNota?.comex_mov_temp_bens === "3" && (
                    <label className="campo">
                      <span>Número do RE</span>
                      <input className="num" maxLength={12} value={comex.n_re} onChange={(e) => setComex({ ...comex, n_re: e.target.value })} />
                      <small>Registro de exportação ligado à movimentação temporária de bens.</small>
                    </label>
                  )}
                </>
              )}
              <div className="campo largo campo--obrigatorio">
                <span className="rotulo-com-acao">
                  <label htmlFor="avulsa-descricao">Descrição do serviço</label>
                  <BotaoHistorico titulo="Descrições usadas antes" onClick={() => setHistorico("descricoes")} />
                </span>
                <textarea id="avulsa-descricao" rows={4} value={descricao} maxLength={2000} onChange={(e) => setDescricao(e.target.value)} />
                <PreviaDescricao texto={descricao} />
                <small>
                  Aceita variáveis como <span className="num">{"{mes}"}</span> e <span className="num">{"{ano}"}</span>.{" "}
                  <BotaoVariaveis className="btn-texto" />
                </small>
              </div>
              <div className="largo">
                <DadosDaNota valor={dadosNota} onChange={setDadosNota} />
              </div>
            </div>
          </section>

          <section>
            <h3 className="secao-titulo">
              Tributação
              <BotaoHistorico titulo="Tributações usadas antes" onClick={() => setHistorico("tributacoes")} />
            </h3>
            {avisoTributacao && <p className="aviso" style={{ margin: "0 0 12px" }}>{avisoTributacao}</p>}
            <div className="escolha-tributacao" role="radiogroup" aria-label="Tributação da nota">
              {opcoes.map((o) => (
                <button
                  key={String(o.id)}
                  type="button"
                  role="radio"
                  aria-checked={escolha === o.id}
                  className="escolha-tributacao__op"
                  onClick={() => escolherTributacao(o.id)}
                >
                  <strong>{o.titulo}</strong>
                  {o.detalhe && <span>{o.detalhe}</span>}
                </button>
              ))}
            </div>
            {!admin && trib.data && !trib.data.itens.length && (
              <p style={{ color: "var(--grafite)", fontSize: "0.8125rem" }}>
                Nenhuma tributação cadastrada. Um administrador pode cadastrá-las em Configurações.
              </p>
            )}
            {escolha === "propria" && propria && (
              <div style={{ display: "flex", flexDirection: "column", gap: 22, marginTop: 18 }}>
                <CamposTributacao valor={propria} onChange={setPropria} regime={trib.data?.regime} semPrevia semLegenda />
              </div>
            )}
          </section>

          {precisaObra && <ObraNota valor={obra} onChange={setObra} exterior={prestadoFora} />}
          {precisaEvento && <EventoNota valor={evento} onChange={setEvento} exterior={prestadoFora} />}
          {precisaLocacao && (
            <LocacaoNota valor={locacao} onChange={setLocacao} valorNota={valorNum} c_nbs={tributacaoDaNota?.c_nbs} />
          )}
          {precisaInterm && (
            <OutraPessoa
              titulo="Intermediário"
              ajuda="A tributação diz que o ISSQN é retido pelo intermediário: informe quem intermediou o serviço."
              valor={interm}
              onChange={setInterm}
              comIm
              exigeEnderecoCnpj
            />
          )}
          {comIbsCbs && (
            <section>
              <div className="grade-form">
                <Pergunta
                  texto="O destinatário do serviço (IBS/CBS) é outra pessoa, diferente do tomador?"
                  sim={outroDest}
                  onChange={setOutroDest}
                  ajuda={outroDest ? "Quem recebe o serviço, quando não é quem paga (o tomador)." : undefined}
                />
              </div>
            </section>
          )}
          {comIbsCbs && outroDest && (
            <OutraPessoa titulo="Destinatário" ajuda="Vai no grupo do IBS/CBS da nota." valor={dest} onChange={setDest} comIm={false} />
          )}
          {precisaRefs && (
            <section>
              <h3 className="secao-titulo">NFS-e referenciada</h3>
              <div className="grade-form">
                <label className="campo largo campo--obrigatorio">
                  <span>Chave da NFS-e anterior</span>
                  <input className="num" value={refs} onChange={(e) => setRefs(e.target.value)} placeholder="50 caracteres" />
                  <small>
                    O tipo de operação da tributação diz que o pagamento ou o fornecimento já foi feito: informe a nota daquela parte.
                    Mais de uma, separadas por vírgula.
                  </small>
                </label>
              </div>
            </section>
          )}

          {tributacaoDaNota && (
            <ValoresDaNota
              tributacao={tributacaoDaNota}
              valor={Number(valorNum) || 0}
              regime={trib.data?.regime}
              descIncond={Number(parseValor(dadosNota.v_desc_incond)) || 0}
              descCond={Number(parseValor(dadosNota.v_desc_cond)) || 0}
              ajuste={ajusteEmReais(dadosNota, Number(valorNum) || 0)}
            />
          )}
        </div>
      </Gaveta>
      {historico && (
        <HistoricoAvulsa
          aba={historico}
          documento={noExterior ? "" : documento}
          nomeTomador={nome || cadastro?.nome || ""}
          usarDescricao={setDescricao}
          usarTributacao={usarTributacao}
          fechar={() => setHistorico(null)}
        />
      )}
      {conferir && (
        <EmissaoFluxo
          linhaIds={[]}
          avulsa={conferir}
          nomes={new Map([[0, conferir.nome || cadastro?.nome || formatarDocumento(conferir.documento)]])}
          aoCriarLote={() => setEmitida(true)}
          fechar={() => {
            setConferir(null);
            qc.invalidateQueries();
            if (emitida) fechar(); // depois de emitir, a gaveta fecha; se só conferiu, volta para ela
          }}
        />
      )}
    </>
  );
}

function Sugestoes({ clientes, escolher }: { clientes: Cliente[]; escolher: (c: Cliente) => void }) {
  return (
    <ul className="lista-clientes lista-clientes--sugestoes lista-clientes--flutuante">
      {clientes.map((c) => (
        <li key={c.documento}>
          <button type="button" className="btn btn--fantasma" title={c.nome} onMouseDown={(e) => e.preventDefault()} onClick={() => escolher(c)}>
            <span>{c.nome}</span>
            <span className="num">{formatarDocumento(c.documento)}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
