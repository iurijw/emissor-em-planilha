import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api, baixar, type Emissao, type ListaEmissoes } from "../api";
import { dataHora, formatarDocumento, moeda } from "../format";
import { AmbChip, ErroCaixa, Gaveta, MensagensSefin, Spinner, StatusChip, useToast } from "../ui";

function hoje(offsetDias = 0) {
  const d = new Date();
  d.setDate(d.getDate() + offsetDias);
  return d.toISOString().slice(0, 10);
}
function inicioMes() {
  const d = new Date();
  return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10);
}
function loteDoHash(): string {
  return new URLSearchParams(location.hash.split("?")[1] || "").get("lote") || "";
}

export function Emissoes() {
  const toast = useToast();
  const [filtro, setFiltro] = useState({ de: inicioMes(), ate: hoje(), status: "", ambiente: "", q: "", lote: loteDoHash() });
  const [pagina, setPagina] = useState(1);
  const [marcadas, setMarcadas] = useState<Set<number>>(new Set());
  const [aberta, setAberta] = useState<number | null>(null);
  const [baixando, setBaixando] = useState<string | null>(null);

  useEffect(() => setPagina(1), [filtro]);
  const params = useMemo(() => {
    const p = new URLSearchParams({ pagina: String(pagina), por_pagina: "50" });
    if (filtro.lote) p.set("lote_id", filtro.lote);
    else {
      if (filtro.de) p.set("de", filtro.de);
      if (filtro.ate) p.set("ate", filtro.ate);
    }
    if (filtro.status) p.set("status", filtro.status);
    if (filtro.ambiente) p.set("ambiente", filtro.ambiente);
    if (filtro.q.trim()) p.set("q", filtro.q.trim());
    return p.toString();
  }, [filtro, pagina]);

  const lista = useQuery({
    queryKey: ["emissoes", params],
    queryFn: () => api.get<ListaEmissoes>(`/api/emissoes?${params}`),
    refetchInterval: (q) => (q.state.data?.itens.some((e) => ["pendente", "processando"].includes(e.status)) ? 2000 : false),
  });
  const itens = lista.data?.itens ?? [];
  const autorizadasPagina = itens.filter((e) => e.status === "autorizada");
  const todasMarcadas = autorizadasPagina.length > 0 && autorizadasPagina.every((e) => marcadas.has(e.id));

  function alternar(id: number) {
    setMarcadas((m) => {
      const n = new Set(m);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  }

  async function baixarZip(conteudo: "xml" | "pdf" | "ambos") {
    setBaixando(conteudo);
    try {
      await baixar("/api/emissoes/zip", { ids: [...marcadas], conteudo });
    } catch (e) {
      toast((e as Error).message, "erro");
    } finally {
      setBaixando(null);
    }
  }

  return (
    <div className="pagina">
      <div className="cabecalho-pagina">
        <div>
          <h1 className="titulo">Emissões</h1>
          <p className="subtitulo">Todas as notas enviadas, com o XML e o PDF (DANFSe) de cada uma.</p>
        </div>
        {lista.data && (
          <div className="cabecalho-pagina__acoes" style={{ color: "var(--grafite)" }}>
            <span>
              <b className="num" style={{ color: "var(--tinta)" }}>{lista.data.total}</b> emissões ·{" "}
              <b className="num" style={{ color: "var(--tinta)" }}>{moeda(lista.data.valor_autorizado)}</b> autorizados
            </span>
          </div>
        )}
      </div>

      <div className="filtros">
        {filtro.lote ? (
          <div className="info-caixa" style={{ alignItems: "center" }}>
            Mostrando o lote <span className="num">{filtro.lote}</span>
            <button className="btn btn--pequeno" onClick={() => setFiltro({ ...filtro, lote: "" })}>
              Ver todas
            </button>
          </div>
        ) : (
          <>
            <label className="campo">
              <span>De</span>
              <input type="date" value={filtro.de} onChange={(e) => setFiltro({ ...filtro, de: e.target.value })} />
            </label>
            <label className="campo">
              <span>Até</span>
              <input type="date" value={filtro.ate} onChange={(e) => setFiltro({ ...filtro, ate: e.target.value })} />
            </label>
          </>
        )}
        <label className="campo">
          <span>Situação</span>
          <select value={filtro.status} onChange={(e) => setFiltro({ ...filtro, status: e.target.value })}>
            <option value="">Todas</option>
            <option value="autorizada">Autorizadas</option>
            <option value="rejeitada,erro">Com problema</option>
            <option value="nao_enviada">Não enviadas</option>
            <option value="pendente,processando">Em andamento</option>
          </select>
        </label>
        <label className="campo">
          <span>Ambiente</span>
          <select value={filtro.ambiente} onChange={(e) => setFiltro({ ...filtro, ambiente: e.target.value })}>
            <option value="">Todos</option>
            <option value="1">Produção</option>
            <option value="2">Homologação</option>
          </select>
        </label>
        <label className="campo" style={{ flex: 1, minWidth: 220 }}>
          <span>Buscar</span>
          <input
            placeholder="Cliente, CNPJ, nº da nota ou descrição"
            value={filtro.q}
            onChange={(e) => setFiltro({ ...filtro, q: e.target.value })}
          />
        </label>
      </div>

      <div className="barra" style={{ paddingTop: 0 }}>
        <span style={{ color: "var(--grafite)" }}>
          {marcadas.size ? <><b className="num" style={{ color: "var(--tinta)" }}>{marcadas.size}</b> selecionada(s)</> : "Selecione notas autorizadas para baixar em lote"}
        </span>
        <button className="btn btn--pequeno" disabled={!marcadas.size || !!baixando} onClick={() => baixarZip("ambos")}>
          {baixando === "ambos" && <Spinner />} Baixar XML + PDF
        </button>
        <button className="btn btn--pequeno" disabled={!marcadas.size || !!baixando} onClick={() => baixarZip("xml")}>
          {baixando === "xml" && <Spinner />} Só XML
        </button>
        <button className="btn btn--pequeno" disabled={!marcadas.size || !!baixando} onClick={() => baixarZip("pdf")}>
          {baixando === "pdf" && <Spinner />} Só PDF
        </button>
        {marcadas.size > 0 && (
          <button className="btn btn--pequeno btn--fantasma" onClick={() => setMarcadas(new Set())}>
            Limpar seleção
          </button>
        )}
      </div>

      <ErroCaixa erro={lista.error} titulo="Não foi possível carregar as emissões" />
      <div className="tabela-caixa">
        <table className="tabela">
          <thead>
            <tr>
              <th style={{ width: 36 }}>
                <input
                  type="checkbox"
                  aria-label="Selecionar autorizadas desta página"
                  checked={todasMarcadas}
                  onChange={() =>
                    setMarcadas((m) => {
                      const n = new Set(m);
                      autorizadasPagina.forEach((e) => (todasMarcadas ? n.delete(e.id) : n.add(e.id)));
                      return n;
                    })
                  }
                />
              </th>
              <th>NFS-e</th>
              <th>Emissão</th>
              <th>Tomador</th>
              <th className="dir">Valor</th>
              <th>Situação</th>
              <th>Ambiente</th>
              <th>Arquivos</th>
            </tr>
          </thead>
          <tbody>
            {lista.isLoading && (
              <tr><td colSpan={8} className="carregando">Carregando…</td></tr>
            )}
            {!lista.isLoading && itens.length === 0 && (
              <tr>
                <td colSpan={8}>
                  <div className="vazio">
                    <strong>Nenhuma emissão neste filtro</strong>
                    Ajuste o período ou emita notas na aba <a href="#/emitir">Emitir notas</a>.
                  </div>
                </td>
              </tr>
            )}
            {itens.map((e) => (
              <tr key={e.id} className={marcadas.has(e.id) ? "selecionada" : undefined}>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`Selecionar nota de ${e.tomador_nome}`}
                    disabled={e.status !== "autorizada"}
                    checked={marcadas.has(e.id)}
                    onChange={() => alternar(e.id)}
                  />
                </td>
                <td className="num clicavel" onClick={() => setAberta(e.id)}>{e.numero_nfse ?? "—"}</td>
                <td className="clicavel" onClick={() => setAberta(e.id)}>{dataHora(e.dh_emissao || e.atualizado_em)}</td>
                <td className="clicavel" onClick={() => setAberta(e.id)}>
                  <div style={{ fontWeight: 550 }}>{e.tomador_nome}</div>
                  <div className="num" style={{ color: "var(--grafite)" }}>{formatarDocumento(e.tomador_documento)}</div>
                </td>
                <td className="dir num">{moeda(e.valor)}</td>
                <td>
                  <button className="btn btn--fantasma btn--pequeno" style={{ padding: 0 }} onClick={() => setAberta(e.id)}>
                    <StatusChip status={e.status} />
                  </button>
                  {e.erro?.mensagens?.[0]?.codigo && <span className="num" style={{ marginLeft: 6, color: "var(--erro)" }}>{e.erro.mensagens[0].codigo}</span>}
                </td>
                <td><AmbChip ambiente={e.ambiente} /></td>
                <td>
                  {e.status === "autorizada" && (
                    <div style={{ display: "flex", gap: 4 }}>
                      <button className="btn btn--pequeno" onClick={() => baixar(`/api/emissoes/${e.id}/xml`).catch((x) => toast(x.message, "erro"))}>XML</button>
                      <button className="btn btn--pequeno" onClick={() => baixar(`/api/emissoes/${e.id}/pdf`).catch((x) => toast(x.message, "erro"))}>PDF</button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {lista.data && lista.data.total > lista.data.por_pagina && (
        <div className="paginacao">
          <button className="btn btn--pequeno" disabled={pagina <= 1} onClick={() => setPagina((p) => p - 1)}>Anterior</button>
          <span>Página {pagina} de {Math.ceil(lista.data.total / lista.data.por_pagina)}</span>
          <button className="btn btn--pequeno" disabled={pagina * lista.data.por_pagina >= lista.data.total} onClick={() => setPagina((p) => p + 1)}>Próxima</button>
        </div>
      )}
      {aberta !== null && <DetalheEmissao id={aberta} fechar={() => setAberta(null)} />}
    </div>
  );
}

function DetalheEmissao({ id, fechar }: { id: number; fechar: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["emissao", id], queryFn: () => api.get<Emissao>(`/api/emissoes/${id}`) });
  const [verificando, setVerificando] = useState(false);
  const [erro, setErro] = useState<unknown>(null);
  const e = q.data;

  async function verificar() {
    setVerificando(true);
    setErro(null);
    try {
      const r = await api.post<Emissao>(`/api/emissoes/${id}/verificar`);
      toast(r.status === "autorizada" ? `NFS-e nº ${r.numero_nfse} encontrada e registrada.` : "A Sefin confirmou que esta nota não foi gerada.");
      qc.invalidateQueries();
    } catch (x) {
      setErro(x);
    } finally {
      setVerificando(false);
    }
  }

  return (
    <Gaveta
      titulo={e ? (e.numero_nfse ? `NFS-e nº ${e.numero_nfse}` : e.tomador_nome) : "Emissão"}
      fechar={fechar}
      rodape={
        e && (
          <>
            {e.status === "erro" && e.id_dps && (
              <button className="btn" onClick={verificar} disabled={verificando}>
                {verificando && <Spinner />} Verificar na Sefin
              </button>
            )}
            {e.status === "autorizada" && (
              <>
                <a className="btn" href={`/api/emissoes/${e.id}/pdf?inline=true`} target="_blank" rel="noreferrer">Abrir PDF</a>
                <button className="btn" onClick={() => baixar(`/api/emissoes/${e.id}/xml`).catch((x) => toast(x.message, "erro"))}>Baixar XML</button>
              </>
            )}
          </>
        )
      }
    >
      {q.isLoading && <p className="carregando">Carregando…</p>}
      <ErroCaixa erro={q.error} />
      {e && (
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <StatusChip status={e.status} />
            <AmbChip ambiente={e.ambiente} />
            {e.recuperada && <span className="chip">Recuperada após falha de comunicação</span>}
          </div>
          {e.erro && (
            <div className={e.status === "nao_enviada" ? "aviso" : "erro-caixa"}>
              <MensagensSefin erro={e.erro} />
            </div>
          )}
          {e.status === "erro" && e.id_dps && (
            <div className="info-caixa">
              Se a comunicação caiu durante o envio, a nota pode ter sido gerada mesmo assim. Use “Verificar na Sefin” antes de emitir
              de novo para este cliente.
            </div>
          )}
          {e.alertas.length > 0 && (
            <div className="aviso">
              <MensagensSefin erro={{ tipo: "alerta", resumo: "Alertas da Sefin", mensagens: e.alertas }} />
            </div>
          )}
          <ErroCaixa erro={erro} titulo="Não foi possível verificar" />
          <dl className="dl">
            <dt>Tomador</dt>
            <dd>{e.tomador_nome}<br /><span className="num">{formatarDocumento(e.tomador_documento)}</span></dd>
            <dt>Valor</dt>
            <dd className="num">{moeda(e.valor)}</dd>
            <dt>Descrição</dt>
            <dd style={{ whiteSpace: "pre-wrap" }}>{e.descricao}</dd>
            <dt>Emissão</dt>
            <dd>{dataHora(e.dh_emissao)}</dd>
            {e.chave_acesso && (<><dt>Chave de acesso</dt><dd className="num" style={{ fontSize: 11 }}>{e.chave_acesso}</dd></>)}
            {e.n_dps && (<><dt>DPS</dt><dd className="num">série {e.serie} · nº {e.n_dps}</dd></>)}
            {e.id_dps && (<><dt>Id da DPS</dt><dd className="num" style={{ fontSize: 11 }}>{e.id_dps}</dd></>)}
            <dt>Tentativas</dt>
            <dd className="num">{e.tentativas}</dd>
            {e.lote_id && (<><dt>Lote</dt><dd className="num">{e.lote_id}</dd></>)}
          </dl>
          {e.chave_acesso && (
            <a href={`https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave=${e.chave_acesso}`} target="_blank" rel="noreferrer">
              Consultar no portal nacional da NFS-e
            </a>
          )}
        </div>
      )}
    </Gaveta>
  );
}
