import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Cliente } from "../api";
import { CampoMunicipio } from "../components/FiscalForm";
import { cep, documentoValido, formatarDocumento, soDocumento } from "../format";
import { ImportarClientesXml } from "../components/ImportarClientesXml";
import { Dialogo, ErroCaixa, Gaveta, Spinner, useToast } from "../ui";

const ORIGEM: Record<string, string> = {
  xml: "Notas importadas",
  brasilapi: "Receita (BrasilAPI)",
  cnpja: "Receita (CNPJá)",
  manual: "Editado à mão",
};

export function Clientes() {
  const [q, setQ] = useState("");
  const [editando, setEditando] = useState<Partial<Cliente> | null>(null);
  const [importando, setImportando] = useState(false);
  const lista = useQuery({ queryKey: ["clientes", q], queryFn: () => api.get<Cliente[]>(`/api/clientes?q=${encodeURIComponent(q)}`) });
  const itens = lista.data ?? [];

  return (
    <div className="pagina">
      <div className="cabecalho-pagina">
        <div>
          <h1 className="titulo">Clientes</h1>
          <p className="subtitulo">
            Dados do tomador usados nas notas. CNPJs novos da tabela são buscados na Receita automaticamente; corrija aqui o que
            estiver desatualizado.
          </p>
        </div>
        <div className="cabecalho-pagina__acoes">
          <button className="btn" onClick={() => setImportando(true)}>
            Importar de XMLs
          </button>
          <button className="btn btn--primario" onClick={() => setEditando({})}>
            Cadastrar cliente
          </button>
        </div>
      </div>
      <div className="filtros">
        <label className="campo" style={{ flex: 1, maxWidth: 420 }}>
          <span>Buscar</span>
          <input placeholder="Nome ou CNPJ/CPF" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
      </div>
      <ErroCaixa erro={lista.error} titulo="Não foi possível carregar os clientes" />
      <div className="tabela-caixa">
        <table className="tabela">
          <thead>
            <tr>
              <th>Nome</th>
              <th>CNPJ / CPF</th>
              <th>Endereço</th>
              <th>Situação na Receita</th>
              <th>Origem dos dados</th>
            </tr>
          </thead>
          <tbody>
            {!lista.isLoading && itens.length === 0 && (
              <tr><td colSpan={5}><div className="vazio"><strong>Nenhum cliente {q ? "encontrado" : "cadastrado"}</strong>Os clientes aparecem aqui quando entram na tabela de emissão ou vêm dos XMLs importados.</div></td></tr>
            )}
            {itens.map((c) => (
              <tr key={c.documento} className="clicavel" onClick={() => setEditando(c)}>
                <td style={{ fontWeight: 550 }}>{c.nome}</td>
                <td className="num">{formatarDocumento(c.documento)}</td>
                <td>
                  {c.endereco_completo ? (
                    <>
                      {c.logradouro}, {c.numero} · {c.bairro}
                      <div style={{ color: "var(--grafite)", fontSize: 12.5 }}>{c.municipio} · {cep(c.cep)}</div>
                    </>
                  ) : (
                    <span style={{ color: "var(--aviso)" }}>Endereço incompleto — a nota sai sem endereço</span>
                  )}
                </td>
                <td style={{ color: c.situacao_cadastral && c.situacao_cadastral.toUpperCase() !== "ATIVA" ? "var(--erro)" : undefined }}>
                  {c.situacao_cadastral ?? "—"}
                </td>
                <td style={{ color: "var(--grafite)" }}>{ORIGEM[c.origem] ?? c.origem}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editando && <EditarCliente inicial={editando} fechar={() => setEditando(null)} />}
      {importando && (
        <Dialogo titulo="Cadastrar clientes a partir de XMLs" fechar={() => setImportando(false)}>
          <ImportarClientesXml />
        </Dialogo>
      )}
    </div>
  );
}

function EditarCliente({ inicial, fechar }: { inicial: Partial<Cliente>; fechar: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const novo = !inicial.documento;
  const [c, setC] = useState<Partial<Cliente>>(inicial);
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState<"" | "salvar" | "consultar" | "excluir">("");
  const set = (k: keyof Cliente, v: string | null) => setC((x) => ({ ...x, [k]: v }));
  const doc = soDocumento(c.documento || "");

  async function acao(tipo: "salvar" | "consultar" | "excluir") {
    setErro(null);
    setOcupado(tipo);
    try {
      if (tipo === "consultar") {
        const r = await api.post<{ fonte: string; cliente: Cliente }>(`/api/clientes/${doc}/consultar`);
        setC(r.cliente);
        toast(`Dados atualizados pela Receita (${ORIGEM[r.cliente.origem] ?? r.fonte}).`);
      } else if (tipo === "salvar") {
        await api.put(`/api/clientes/${doc}`, {
          nome: c.nome ?? "",
          c_mun: c.c_mun,
          cep: c.cep,
          logradouro: c.logradouro,
          numero: c.numero,
          complemento: c.complemento,
          bairro: c.bairro,
          fone: c.fone,
          email: c.email,
          inscricao_municipal: c.inscricao_municipal,
        });
        toast("Cliente salvo.");
        fechar();
      } else {
        await api.del(`/api/clientes/${doc}`);
        toast("Cliente excluído do cadastro.");
        fechar();
      }
      qc.invalidateQueries({ queryKey: ["clientes"] });
    } catch (e) {
      setErro(e);
    } finally {
      setOcupado("");
    }
  }

  const campo = (k: keyof Cliente, rotulo: string, extra?: { largo?: boolean; num?: boolean }) => (
    <label className={`campo ${extra?.largo ? "largo" : ""}`}>
      <span>{rotulo}</span>
      <input className={extra?.num ? "num" : undefined} value={(c[k] as string) ?? ""} onChange={(e) => set(k, e.target.value || null)} />
    </label>
  );

  return (
    <Gaveta
      titulo={novo ? "Novo cliente" : c.nome || formatarDocumento(doc)}
      fechar={fechar}
      rodape={
        <>
          {!novo && (
            <button className="btn btn--perigo" style={{ marginRight: "auto" }} disabled={!!ocupado} onClick={() => acao("excluir")}>
              Excluir
            </button>
          )}
          <button className="btn" onClick={fechar}>Cancelar</button>
          <button className="btn btn--primario" disabled={!!ocupado || !documentoValido(doc) || !c.nome} onClick={() => acao("salvar")}>
            {ocupado === "salvar" && <Spinner />} Salvar cliente
          </button>
        </>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
        <div style={{ display: "flex", gap: 10, alignItems: "flex-end" }}>
          <label className={`campo ${doc && !documentoValido(doc) ? "campo--erro" : ""}`} style={{ flex: 1 }}>
            <span>CNPJ / CPF</span>
            <input className="num" value={novo ? c.documento ?? "" : formatarDocumento(doc)} readOnly={!novo} onChange={(e) => set("documento", e.target.value)} />
            {doc && !documentoValido(doc) && <span className="campo__erro">Dígitos verificadores não conferem.</span>}
          </label>
          {doc.length === 14 && documentoValido(doc) && (
            <button className="btn" disabled={!!ocupado} onClick={() => acao("consultar")}>
              {ocupado === "consultar" && <Spinner />} Buscar na Receita
            </button>
          )}
        </div>
        <div className="grade-form">
          {campo("nome", "Nome / razão social", { largo: true })}
          {campo("logradouro", "Logradouro", { largo: true })}
          {campo("numero", "Número")}
          {campo("complemento", "Complemento")}
          {campo("bairro", "Bairro")}
          {campo("cep", "CEP", { num: true })}
          <div className="largo">
            <CampoMunicipio rotulo="Município" codigo={c.c_mun ?? null} onChange={(v) => set("c_mun", v)} />
          </div>
          {campo("email", "E-mail", { largo: true })}
          {campo("fone", "Telefone", { num: true })}
          {campo("inscricao_municipal", "Inscrição municipal")}
        </div>
        {c.origem && <p style={{ color: "var(--grafite)", fontSize: 12.5, margin: 0 }}>Dados de: {ORIGEM[c.origem] ?? c.origem}{c.situacao_cadastral ? ` · situação na Receita: ${c.situacao_cadastral}` : ""}</p>}
        <ErroCaixa erro={erro} titulo="Não foi possível concluir" />
      </div>
    </Gaveta>
  );
}
