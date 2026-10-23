import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, dadosTributacao, type Cliente, type DadosTributacao } from "../api";
import { documentoValido, formatarDocumento, moeda, parseValor, soDocumento } from "../format";
import { BotaoVariaveis, PreviaDescricao } from "./Variaveis";
import { EmissaoFluxo, type CorpoAvulsa } from "../pages/EmissaoFluxo";
import { Gaveta } from "../ui";
import { useTributacoes } from "./Abas";
import { buscarClientes, normalizarBusca } from "./EditorNomeCliente";
import { CamposTributacao } from "./FiscalForm";

type Escolha = "padrao" | "propria" | number;

/** Consulta na Receita de um CNPJ digitado sem cadastro (o servidor cadastra o cliente com o endereço). */
type Consulta = { documento: string; estado: "buscando" | "falhou"; erro?: string };

/** Uma nota só, fora da tabela: cliente, valor, descrição e a tributação (padrão, predefinida ou só desta nota). */
export function NotaAvulsa({ fechar, admin }: { fechar: () => void; admin: boolean }) {
  const qc = useQueryClient();
  const trib = useTributacoes();
  const clientesQ = useQuery({ queryKey: ["clientes"], queryFn: () => api.get<Cliente[]>("/api/clientes") });
  const [nome, setNome] = useState("");
  const [documento, setDocumento] = useState("");
  const [valor, setValor] = useState("");
  const [descricao, setDescricao] = useState("");
  const [escolha, setEscolha] = useState<Escolha>("padrao");
  const [propria, setPropria] = useState<DadosTributacao | null>(null);
  const [focoNome, setFocoNome] = useState(false);
  const [conferir, setConferir] = useState<CorpoAvulsa | null>(null);
  const [emitida, setEmitida] = useState(false);
  const [consulta, setConsulta] = useState<Consulta | null>(null);
  const consultados = useRef(new Set<string>());

  const sugestoes = useMemo(() => (focoNome ? buscarClientes(clientesQ.data ?? [], nome, 6) : []), [clientesQ.data, nome, focoNome]);
  const cadastro = clientesQ.data?.find((c) => c.documento === documento);
  const valorNum = parseValor(valor);
  const docInvalido = documento !== "" && !documentoValido(documento);
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

  const consultaAtual = consulta?.documento === documento ? consulta : null;
  const pronto = consultaAtual?.estado !== "buscando" && !!documento && !docInvalido && valorNum !== null && Number(valorNum) > 0 && descricao.trim() !== "";

  function escolherTributacao(e: Escolha) {
    setEscolha(e);
    if (e === "propria" && !propria && trib.data?.padrao) setPropria(dadosTributacao(trib.data.padrao));
  }

  function abrirConferencia() {
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
            <button className="btn btn--primario" disabled={!pronto || (escolha === "propria" && !propria)} onClick={abrirConferencia}>
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
                <label className={`campo ${docInvalido ? "campo--erro" : ""}`}>
                  <span>CNPJ / CPF</span>
                  <input className="num" autoFocus value={formatarDocumento(documento)} onChange={(e) => setDocumento(soDocumento(e.target.value))} />
                  {docInvalido ? (
                    <small className="campo__erro">CNPJ/CPF inválido.</small>
                  ) : (
                    <small>
                      {cadastro
                        ? cadastro.endereco_completo
                          ? "Cliente cadastrado, com endereço."
                          : "Cadastro sem endereço: a nota sai sem o endereço."
                        : consultaAtual?.estado === "buscando"
                          ? "Buscando a empresa na Receita…"
                          : consultaAtual?.estado === "falhou"
                            ? `Não achei na Receita${consultaAtual.erro ? ` (${consultaAtual.erro.replace(/\.$/, "")})` : ""}.${nome.trim() ? "" : " Digite o nome da empresa."} A nota sai sem o endereço; complete em Clientes.`
                            : documento.length === 11
                              ? "CPF sem cadastro: a nota sai sem o endereço. Complete em Clientes."
                              : documento
                                ? "Sem cadastro: a nota sai sem o endereço."
                                : " "}
                    </small>
                  )}
                </label>
                <span className="tomador-ou__ou" aria-hidden="true">
                  ou
                </span>
                <label className="campo" style={{ position: "relative" }}>
                  <span>Nome da empresa</span>
                  <input
                    value={nome}
                    onChange={(e) => setNome(e.target.value)}
                    onFocus={() => setFocoNome(true)}
                    onBlur={() => window.setTimeout(() => setFocoNome(false), 150)}
                    placeholder="Digite para buscar no cadastro"
                  />
                  {sugestoes.length > 0 && (
                    <ul className="lista-clientes lista-clientes--sugestoes lista-clientes--flutuante">
                      {sugestoes.map((c) => (
                        <li key={c.documento}>
                          <button
                            type="button"
                            className="btn btn--fantasma"
                            onMouseDown={(e) => e.preventDefault()}
                            onClick={() => {
                              setNome(c.nome);
                              setDocumento(c.documento);
                              setFocoNome(false);
                            }}
                          >
                            <span>{c.nome}</span>
                            <span className="num">{formatarDocumento(c.documento)}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
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
                <CamposTributacao valor={propria} onChange={setPropria} regime={trib.data?.regime} />
              </div>
            )}
          </section>
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
