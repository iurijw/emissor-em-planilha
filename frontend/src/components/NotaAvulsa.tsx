import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, dadosTributacao, type Cliente, type DadosTributacao } from "../api";
import { documentoValido, formatarDocumento, moeda, parseValor, soDocumento } from "../format";
import { BotaoVariaveis, PreviaDescricao } from "./Variaveis";
import { EmissaoFluxo, type CorpoAvulsa } from "../pages/EmissaoFluxo";
import { ErroCaixa, Gaveta, Spinner } from "../ui";
import { useTributacoes } from "./Abas";
import { buscarClientes, normalizarBusca } from "./EditorNomeCliente";
import { CamposTributacao, ValoresDaNota } from "./FiscalForm";
import { EnderecoTomador, enderecoDe, enderecosIguais, enderecoVazio, type Endereco } from "./EnderecoTomador";

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

/** Consulta na Receita de um CNPJ digitado sem cadastro (o servidor cadastra o cliente com o endereço). */
type Consulta = { documento: string; estado: "buscando" | "falhou"; erro?: string };

/** Uma nota só, fora da tabela: cliente, valor, descrição e a tributação (padrão, predefinida ou só desta nota). */
export function NotaAvulsa({ fechar, admin }: { fechar: () => void; admin: boolean }) {
  const qc = useQueryClient();
  const trib = useTributacoes();
  const clientesQ = useQuery({ queryKey: ["clientes"], queryFn: () => api.get<Cliente[]>("/api/clientes") });
  const [nome, setNome] = useState("");
  // Texto do campo CNPJ/CPF como digitado; o documento sai dele quando não é um nome.
  const [docTexto, setDocTexto] = useState("");
  const [focoDoc, setFocoDoc] = useState(false);
  const docEhNome = pareceNome(docTexto);
  const documento = docEhNome ? "" : soDocumento(docTexto);
  const setDocumento = (d: string) => setDocTexto(formatarDocumento(d));
  const [valor, setValor] = useState("");
  const [descricao, setDescricao] = useState("");
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
  const pronto = consultaAtual?.estado !== "buscando" && !!documento && !docInvalido && valorNum !== null && Number(valorNum) > 0 && descricao.trim() !== "";

  function escolherTributacao(e: Escolha) {
    setEscolha(e);
    if (e === "propria" && !propria && trib.data?.padrao) setPropria(dadosTributacao(trib.data.padrao));
  }

  async function abrirConferencia() {
    // Endereço mexido aqui: grava no cadastro antes, porque a nota usa o endereço do cadastro. Tomador novo com nome
    // (o CPF, que a Receita não traz) também entra no cadastro, para aparecer nas próximas notas.
    if (enderecoAlterado || (!cadastro && nome.trim())) {
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
      nome: nome.trim(),
      documento,
      valor: valorNum ?? valor,
      descricao,
      tributacao_id: typeof escolha === "number" ? escolha : null,
      tributacao: escolha === "propria" ? propria : null,
    });
  }

  const opcoes: { id: Escolha; titulo: string; detalhe: string | null }[] = [
    { id: "padrao", titulo: "Tributação padrão", detalhe: trib.data?.padrao_resumo ?? null },
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
          <p style={{ margin: 0, color: "var(--grafite)" }}>
            Emite uma nota sem mexer na tabela. Ela usa a mesma numeração e aparece na aba Emissões.
          </p>
          <section>
            <h3 className="secao-titulo">Tomador</h3>
            <div className="grade-form">
              <div className="largo tomador-ou">
                <label className={`campo ${mostrarInvalido ? "campo--erro" : ""}`} style={{ position: "relative" }}>
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
                  />
                  {erroEndereco != null && <ErroCaixa erro={erroEndereco} titulo="Não foi possível salvar o endereço" />}
                </div>
              )}
              <label className="campo">
                <span>Valor</span>
                <input className="num" value={valor} onChange={(e) => setValor(e.target.value)} placeholder="0,00" inputMode="decimal" />
                <small>{valorNum ? moeda(valorNum) : " "}</small>
              </label>
              <label className="campo largo">
                <span>Descrição do serviço</span>
                <textarea rows={4} value={descricao} maxLength={2000} onChange={(e) => setDescricao(e.target.value)} />
                <PreviaDescricao texto={descricao} />
                <small>
                  Aceita variáveis como <span className="num">{"{mes}"}</span> e <span className="num">{"{ano}"}</span>.{" "}
                  <BotaoVariaveis className="btn-texto" />
                </small>
              </label>
            </div>
          </section>

          <section>
            <h3 className="secao-titulo">Tributação</h3>
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
              <p style={{ color: "var(--grafite)", fontSize: 13 }}>
                Nenhuma tributação cadastrada. Um administrador pode cadastrá-las em Configurações.
              </p>
            )}
            {escolha === "propria" && propria && (
              <div style={{ display: "flex", flexDirection: "column", gap: 22, marginTop: 18 }}>
                <CamposTributacao valor={propria} onChange={setPropria} regime={trib.data?.regime} semPrevia />
              </div>
            )}
          </section>

          {tributacaoDaNota && <ValoresDaNota tributacao={tributacaoDaNota} valor={Number(valorNum) || 0} regime={trib.data?.regime} />}
        </div>
      </Gaveta>
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
