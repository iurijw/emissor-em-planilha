import { useEffect, useRef, useState } from "react";
import { api, baixar, type Emissao, type Lote, type Validacao } from "../api";
import { formatarDocumento, moeda } from "../format";
import { Dialogo, ErroCaixa, MensagensSefin, Spinner, StatusChip, useToast } from "../ui";

type Fase = "validando" | "conferir" | "enviando" | "acompanhando";

export function EmissaoFluxo(props: {
  linhaIds: number[];
  loteExistente?: string;
  nomes: Map<number, string>;
  fechar: () => void;
}) {
  const toast = useToast();
  const [fase, setFase] = useState<Fase>(props.loteExistente ? "acompanhando" : "validando");
  const [val, setVal] = useState<Validacao | null>(null);
  const [erro, setErro] = useState<unknown>(null);
  const [cienteAvisos, setCienteAvisos] = useState(false);
  const [confirmaProd, setConfirmaProd] = useState("");
  const [loteId, setLoteId] = useState<string | undefined>(props.loteExistente);
  const [lote, setLote] = useState<Lote | null>(null);
  const [parando, setParando] = useState(false);

  useEffect(() => {
    if (fase !== "validando") return;
    api
      .post<Validacao>("/api/tabela/validar", { linha_ids: props.linhaIds })
      .then((v) => {
        setVal(v);
        setFase("conferir");
      })
      .catch((e) => {
        setErro(e);
        setFase("conferir");
      });
  }, [fase, props.linhaIds]);

  // Acompanhamento por polling (~1 s) até o lote terminar.
  const ativo = useRef(true);
  useEffect(() => {
    ativo.current = true;
    if (!loteId) return;
    let t: number;
    const passo = async () => {
      try {
        const l = await api.get<Lote>(`/api/lotes/${loteId}`);
        if (!ativo.current) return;
        setLote(l);
        if (l.status === "em_andamento") t = window.setTimeout(passo, 1000);
      } catch (e) {
        if (!ativo.current) return;
        setErro(e);
        t = window.setTimeout(passo, 3000);
      }
    };
    passo();
    return () => {
      ativo.current = false;
      window.clearTimeout(t);
    };
  }, [loteId]);

  async function emitir() {
    setErro(null);
    setFase("enviando");
    try {
      const l = await api.post<Lote>("/api/lotes", { linha_ids: props.linhaIds });
      setLote(l);
      setLoteId(l.id);
      setFase("acompanhando");
    } catch (e) {
      setErro(e);
      setFase("conferir");
    }
  }

  async function parar() {
    if (!loteId) return;
    setParando(true);
    try {
      await api.post(`/api/lotes/${loteId}/cancelar`);
      toast("A nota que está sendo enviada termina; as demais não serão enviadas.");
    } catch (e) {
      setErro(e);
      setParando(false);
    }
  }

  // ---------- conferência ----------
  if (fase === "validando" || fase === "conferir" || fase === "enviando") {
    const prod = val?.ambiente === "1";
    const comErro = val?.linhas.filter((l) => l.erros.length) ?? [];
    const comAviso = val?.linhas.filter((l) => !l.erros.length && l.avisos.length) ?? [];
    const bloqueado = !val || val.problemas.length > 0 || comErro.length > 0;
    const precisaCiencia = comAviso.length > 0 && !cienteAvisos;
    const precisaConfirmacaoProd = prod && confirmaProd.trim().toUpperCase() !== "EMITIR";
    return (
      <Dialogo
        titulo="Conferir antes de emitir"
        fechar={fase === "enviando" ? undefined : props.fechar}
        largo
        rodape={
          <>
            <button className="btn" onClick={props.fechar} disabled={fase === "enviando"}>
              Voltar à tabela
            </button>
            <button
              className={`btn btn--grande ${prod ? "btn--escuro" : "btn--primario"}`}
              disabled={bloqueado || precisaCiencia || precisaConfirmacaoProd || fase !== "conferir"}
              onClick={emitir}
            >
              {fase === "enviando" && <Spinner />} Emitir {val?.quantidade ?? ""} nota(s) em {prod ? "Produção" : "Homologação"}
            </button>
          </>
        }
      >
        {fase === "validando" && (
          <p className="carregando">
            <Spinner /> Conferindo as linhas…
          </p>
        )}
        {val && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div className={`faixa-amb ${prod ? "faixa-amb--prod" : "faixa-amb--homolog"}`} style={{ borderRadius: 6, height: 34 }}>
              <span>{val.ambiente_descricao}</span>
              <span className="faixa-amb__dica">
                {prod ? "as notas terão validade jurídica" : "notas de teste, sem validade jurídica"}
              </span>
            </div>
            <div style={{ display: "flex", gap: 40, alignItems: "baseline" }}>
              <div>
                <div className="contador-grande">{val.quantidade}</div>
                <div style={{ color: "var(--grafite)" }}>nota(s)</div>
              </div>
              <div>
                <div className="contador-grande">{moeda(val.total)}</div>
                <div style={{ color: "var(--grafite)" }}>valor total</div>
              </div>
            </div>
            {val.problemas.length > 0 && (
              <div className="erro-caixa">
                <div>
                  <strong>A emissão está bloqueada</strong>
                  <ul>{val.problemas.map((p) => <li key={p}>{p}</li>)}</ul>
                </div>
              </div>
            )}
            {comErro.length > 0 && (
              <div className="erro-caixa">
                <div>
                  <strong>{comErro.length} linha(s) precisam de correção antes de emitir</strong>
                  <ul>
                    {comErro.map((l) => (
                      <li key={l.linha_id}>
                        <b>{props.nomes.get(l.linha_id) || `Linha ${l.linha_id}`}</b>: {l.erros.join(" ")}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            )}
            {comAviso.length > 0 && (
              <div className="aviso">
                <div>
                  <strong>{comAviso.length} linha(s) com aviso</strong>
                  <ul>
                    {comAviso.map((l) => (
                      <li key={l.linha_id}>
                        <b>{props.nomes.get(l.linha_id) || `Linha ${l.linha_id}`}</b>: {l.avisos.join(" ")}
                      </li>
                    ))}
                  </ul>
                  <label style={{ display: "flex", gap: 8, marginTop: 10 }}>
                    <input type="checkbox" checked={cienteAvisos} onChange={(e) => setCienteAvisos(e.target.checked)} />
                    Li os avisos e quero emitir mesmo assim.
                  </label>
                </div>
              </div>
            )}
            {prod && !bloqueado && (
              <label className="campo" style={{ maxWidth: 380 }}>
                <span>Para confirmar a emissão em produção, digite EMITIR</span>
                <input value={confirmaProd} onChange={(e) => setConfirmaProd(e.target.value)} autoFocus />
              </label>
            )}
          </div>
        )}
        <div style={{ marginTop: 12 }}>
          <ErroCaixa erro={erro} titulo="A emissão não foi iniciada" />
        </div>
      </Dialogo>
    );
  }

  // ---------- acompanhamento ----------
  const emissoes = lote?.emissoes ?? [];
  const c = lote?.contagem ?? {};
  const feitas = emissoes.filter((e) => !["pendente", "processando"].includes(e.status)).length;
  const atual = emissoes.find((e) => e.status === "processando");
  const terminou = lote && lote.status !== "em_andamento";
  const autorizadas = emissoes.filter((e) => e.status === "autorizada");
  const problemas = emissoes.filter((e) => ["rejeitada", "erro"].includes(e.status));

  return (
    <Dialogo
      titulo={terminou ? tituloFinal(lote!) : "Emitindo notas"}
      fechar={props.fechar}
      largo
      rodape={
        terminou ? (
          <>
            {autorizadas.length > 0 && (
              <button
                className="btn"
                onClick={() =>
                  baixar("/api/emissoes/zip", { ids: autorizadas.map((e) => e.id), conteudo: "ambos" }).catch((e) => toast(e.message, "erro"))
                }
              >
                Baixar XML e PDF ({autorizadas.length})
              </button>
            )}
            <a className="btn" href={`#/emissoes?lote=${lote!.id}`} onClick={props.fechar}>
              Ver na aba Emissões
            </a>
            <button className="btn btn--primario" onClick={props.fechar}>
              Fechar
            </button>
          </>
        ) : (
          <>
            <span style={{ color: "var(--grafite)", fontSize: 13, marginRight: "auto" }}>
              Pode fechar esta janela: a emissão continua no servidor.
            </span>
            <button className="btn btn--perigo" onClick={parar} disabled={parando}>
              {parando ? "Parando…" : "Parar após a nota atual"}
            </button>
          </>
        )
      }
    >
      {!lote ? (
        <p className="carregando">
          <Spinner /> Iniciando…
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 14 }}>
            <span className="contador-grande">
              {feitas}
              <small>/{lote.total}</small>
            </span>
            <span style={{ color: "var(--grafite)" }}>
              {atual ? (
                <>
                  Enviando <b style={{ color: "var(--tinta)" }}>{atual.tomador_nome}</b>…
                </>
              ) : terminou ? (
                "Lote encerrado."
              ) : (
                "Aguardando…"
              )}
            </span>
          </div>
          <div className="fita" aria-hidden="true">
            {emissoes.map((e) => (
              <div key={e.id} className={`fita__seg fita__seg--${e.status}`} title={`${e.tomador_nome}: ${e.status}`} />
            ))}
          </div>
          <div className="fita-legenda">
            <span><b>{c.autorizada ?? 0}</b>autorizadas</span>
            <span><b>{(c.rejeitada ?? 0) + (c.erro ?? 0)}</b>com problema</span>
            {c.nao_enviada ? <span><b>{c.nao_enviada}</b>não enviadas</span> : null}
            <span><b>{(c.pendente ?? 0) + (c.processando ?? 0)}</b>na fila</span>
          </div>
          {lote.motivo_interrupcao && (
            <div className={lote.status === "cancelado" ? "aviso" : "erro-caixa"}>
              <div>
                <strong>{lote.status === "cancelado" ? "Emissão parada" : "Emissão interrompida"}</strong>
                {lote.motivo_interrupcao}
                {lote.status === "interrompido" && (
                  <div style={{ marginTop: 6 }}>
                    As notas não enviadas continuam na tabela; corrija o problema e emita de novo só elas.
                  </div>
                )}
              </div>
            </div>
          )}
          {problemas.length > 0 && (
            <div>
              <h3 className="secao-titulo" style={{ marginTop: 8 }}>Notas com problema</h3>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {problemas.map((e) => (
                  <ProblemaEmissao key={e.id} e={e} />
                ))}
              </div>
            </div>
          )}
          <ListaResumo emissoes={emissoes} />
          <ErroCaixa erro={erro} titulo="Falha ao consultar o andamento (tentando de novo)" />
        </div>
      )}
    </Dialogo>
  );
}

function tituloFinal(l: Lote): string {
  const ok = l.contagem?.autorizada ?? 0;
  if (l.status === "concluido" && ok === l.total) return `${ok} nota(s) autorizada(s)`;
  if (l.status === "concluido") return "Emissão concluída com pendências";
  if (l.status === "cancelado") return "Emissão parada";
  return "Emissão interrompida";
}

function ProblemaEmissao({ e }: { e: Emissao }) {
  return (
    <div className="erro-caixa">
      <div style={{ width: "100%" }}>
        <div style={{ display: "flex", gap: 10, alignItems: "baseline", marginBottom: 8 }}>
          <b>{e.tomador_nome}</b>
          <span className="num">{formatarDocumento(e.tomador_documento)}</span>
          <span className="num" style={{ marginLeft: "auto" }}>{moeda(e.valor)}</span>
        </div>
        {e.erro && <MensagensSefin erro={e.erro} />}
      </div>
    </div>
  );
}

function ListaResumo({ emissoes }: { emissoes: Emissao[] }) {
  if (!emissoes.length) return null;
  return (
    <details>
      <summary style={{ cursor: "pointer", color: "var(--grafite)" }}>Todas as notas deste lote</summary>
      <div className="tabela-caixa" style={{ marginTop: 8, maxHeight: 280 }}>
        <table className="tabela">
          <thead>
            <tr>
              <th>Tomador</th>
              <th className="dir">Valor</th>
              <th>Situação</th>
              <th>NFS-e</th>
            </tr>
          </thead>
          <tbody>
            {emissoes.map((e) => (
              <tr key={e.id}>
                <td>{e.tomador_nome}</td>
                <td className="dir num">{moeda(e.valor)}</td>
                <td><StatusChip status={e.status} /></td>
                <td className="num">{e.numero_nfse ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
