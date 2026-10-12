import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, type Aba, type Cliente, type ListaTributacoes } from "../api";
import { formatarDocumento } from "../format";
import { Dialogo, ErroCaixa, Spinner } from "../ui";
import { buscarClientes } from "./EditorNomeCliente";

export function useTributacoes() {
  return useQuery({ queryKey: ["tributacoes"], queryFn: () => api.get<ListaTributacoes>("/api/tributacoes") });
}

/**
 * Abas da tabela, no rodapé da grade como as planilhas do Excel. Cada aba leva o selo da tributação
 * com que as notas dela saem.
 */
export function AbasPlanilha(props: {
  abas: Aba[];
  atual: number | null;
  trocar: (id: number) => void;
  criar: () => void;
  editar: (a: Aba) => void;
}) {
  return (
    <div className="abas" role="tablist" aria-label="Abas da tabela">
      {props.abas.map((a) => {
        const ativa = a.id === props.atual;
        return (
          <div key={a.id} className={`aba ${ativa ? "aba--ativa" : ""}`}>
            <button
              role="tab"
              aria-selected={ativa}
              className="aba__nome"
              onClick={() => (ativa ? props.editar(a) : props.trocar(a.id))}
              onDoubleClick={() => props.editar(a)}
              title={ativa ? "Clique para renomear, trocar a tributação ou escolher os clientes da aba" : undefined}
            >
              <span>{a.nome}</span>
              <span className={`aba__selo ${a.tributacao_excluida ? "aba__selo--erro" : a.tributacao_nome ? "" : "aba__selo--padrao"}`}>
                {a.tributacao_excluida ? "tributação excluída" : a.tributacao_nome ?? "padrão"}
              </span>
              {a.clientes.length > 0 && (
                <span className="aba__clientes" title={a.clientes.map((c) => c.nome || formatarDocumento(c.documento)).join(", ")}>
                  {a.clientes.length === 1 ? "1 cliente" : `${a.clientes.length} clientes`}
                </span>
              )}
            </button>
          </div>
        );
      })}
      <button className="aba__nova" onClick={props.criar} aria-label="Nova aba" title="Nova aba">
        +
      </button>
    </div>
  );
}

export function EditorAba(props: {
  aba: Aba | null;
  ultima: boolean;
  admin: boolean;
  fechar: () => void;
  salvo: (a: Aba | null, excluida?: number) => void;
}) {
  const trib = useTributacoes();
  const clientesQ = useQuery({ queryKey: ["clientes"], queryFn: () => api.get<Cliente[]>("/api/clientes") });
  const [nome, setNome] = useState(props.aba?.nome ?? "");
  const [tributacao, setTributacao] = useState<number | null>(props.aba?.tributacao_id ?? null);
  const [clientes, setClientes] = useState(props.aba?.clientes ?? []);
  const [busca, setBusca] = useState("");
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);
  const [confirmarExclusao, setConfirmarExclusao] = useState(false);

  const escolhidos = new Set(clientes.map((c) => c.documento));
  const sugestoes = useMemo(
    () => buscarClientes(clientesQ.data ?? [], busca, 6).filter((c) => !escolhidos.has(c.documento)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [clientesQ.data, busca, clientes],
  );
  const escolhida = trib.data?.itens.find((t) => t.id === tributacao);

  async function salvar() {
    setErro(null);
    setOcupado(true);
    try {
      const corpo = { nome, tributacao_id: tributacao, clientes: clientes.map((c) => c.documento) };
      const a = props.aba
        ? await api.put<Aba>(`/api/abas/${props.aba.id}`, corpo)
        : await api.post<Aba>("/api/abas", corpo);
      props.salvo(a);
    } catch (e) {
      setErro(e);
    } finally {
      setOcupado(false);
    }
  }

  async function excluir() {
    setErro(null);
    setOcupado(true);
    try {
      await api.del(`/api/abas/${props.aba!.id}`);
      props.salvo(null, props.aba!.id);
    } catch (e) {
      setErro(e);
      setConfirmarExclusao(false);
    } finally {
      setOcupado(false);
    }
  }

  return (
    <Dialogo
      titulo={props.aba ? `Aba “${props.aba.nome}”` : "Nova aba"}
      fechar={props.fechar}
      rodape={
        <>
          {props.aba && !props.ultima && (
            <button
              className="btn btn--perigo"
              style={{ marginRight: "auto" }}
              disabled={ocupado}
              onClick={() => (confirmarExclusao ? excluir() : setConfirmarExclusao(true))}
            >
              {confirmarExclusao ? `Confirmar: excluir com ${props.aba.linhas} linha(s)` : "Excluir aba"}
            </button>
          )}
          <button className="btn" onClick={props.fechar}>
            Cancelar
          </button>
          <button className="btn btn--primario" disabled={ocupado || !nome.trim()} onClick={salvar}>
            {ocupado && <Spinner />} {props.aba ? "Salvar aba" : "Criar aba"}
          </button>
        </>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
        <label className="campo">
          <span>Nome da aba</span>
          <input value={nome} maxLength={40} onChange={(e) => setNome(e.target.value)} autoFocus placeholder="Ex.: Mensalistas" />
        </label>

        <label className="campo">
          <span>Tributação das notas desta aba</span>
          <select value={tributacao ?? ""} onChange={(e) => setTributacao(e.target.value ? Number(e.target.value) : null)}>
            <option value="">Tributação padrão</option>
            {trib.data?.itens.map((t) => (
              <option key={t.id} value={t.id}>
                {t.nome}
              </option>
            ))}
          </select>
          <small>
            {escolhida ? escolhida.resumo : trib.data?.padrao_resumo ? `Padrão: ${trib.data.padrao_resumo}` : null}
            {props.admin ? (
              <>
                {" "}
                <a href="#/configuracoes?secao=tributacoes" onClick={props.fechar}>
                  Cadastrar ou editar tributações
                </a>
              </>
            ) : (
              trib.data && !trib.data.itens.length && " Um administrador cadastra as tributações em Configurações."
            )}
          </small>
        </label>

        <div className="campo">
          <span>Clientes exclusivos (opcional)</span>
          <small style={{ marginTop: -2 }}>
            Com clientes escolhidos, só eles podem ser emitidos nesta aba, e emitir para eles em outra aba gera um aviso.
          </small>
          {clientes.length > 0 && (
            <ul className="lista-clientes">
              {clientes.map((c) => (
                <li key={c.documento}>
                  <span>{c.nome || "Sem cadastro"}</span>
                  <span className="num">{formatarDocumento(c.documento)}</span>
                  <button
                    className="btn btn--fantasma btn--pequeno"
                    aria-label={`Tirar ${c.nome} da aba`}
                    onClick={() => setClientes(clientes.filter((x) => x.documento !== c.documento))}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
          <input
            className="input"
            value={busca}
            onChange={(e) => setBusca(e.target.value)}
            placeholder="Buscar cliente pelo nome ou CNPJ/CPF"
          />
          {sugestoes.length > 0 && (
            <ul className="lista-clientes lista-clientes--sugestoes">
              {sugestoes.map((c) => (
                <li key={c.documento}>
                  <button
                    className="btn btn--fantasma"
                    onClick={() => {
                      setClientes([...clientes, { documento: c.documento, nome: c.nome }]);
                      setBusca("");
                    }}
                  >
                    <span>{c.nome}</span>
                    <span className="num">{formatarDocumento(c.documento)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <ErroCaixa erro={erro} titulo="A aba não foi salva" />
      </div>
    </Dialogo>
  );
}
