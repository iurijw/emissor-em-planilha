import { useState } from "react";
import { api, ApiError, type Emissao } from "../api";
import { formatarDocumento, moeda } from "../format";
import { AmbChip, Dialogo, ErroCaixa, MensagensSefin, Spinner } from "../ui";

const MOTIVOS = [
  { valor: "1", rotulo: "Erro na emissão" },
  { valor: "2", rotulo: "Serviço não prestado" },
  { valor: "9", rotulo: "Outros" },
];
const MIN = 15;
const MAX = 255;

type Resultado = { estado: "ok"; aviso?: string | null } | { estado: "erro"; erro: unknown } | { estado: "nao_enviada" };

/** Cancela uma ou várias NFS-e (evento 101101), uma de cada vez, mostrando o resultado de cada nota. */
export function CancelarNotas(props: { emissoes: Emissao[]; fechar: () => void; concluido: () => void }) {
  const notas = props.emissoes.filter((e) => e.pode_cancelar);
  const [motivo, setMotivo] = useState("1");
  const [texto, setTexto] = useState("");
  const [confirma, setConfirma] = useState("");
  const [rodando, setRodando] = useState(false);
  const [resultados, setResultados] = useState<Map<number, Resultado>>(new Map());
  const prod = notas.some((e) => e.ambiente === "1");
  const ambientes = [...new Set(notas.map((e) => e.ambiente))];
  const total = notas.reduce((s, e) => s + Number(e.valor), 0);
  const tamanho = texto.trim().length;
  const terminou = resultados.size > 0 && !rodando;
  const pronto = tamanho >= MIN && tamanho <= MAX && (!prod || confirma.trim().toUpperCase() === "CANCELAR");

  async function cancelar() {
    setRodando(true);
    const res = new Map<number, Resultado>();
    let parar = false;
    for (const e of notas) {
      if (parar) {
        res.set(e.id, { estado: "nao_enviada" });
        continue;
      }
      try {
        const r = await api.post<Emissao>(`/api/emissoes/${e.id}/cancelar`, { c_motivo: motivo, x_motivo: texto.trim() });
        res.set(e.id, { estado: "ok", aviso: r.aviso });
      } catch (x) {
        res.set(e.id, { estado: "erro", erro: x });
        // Rejeição de uma nota (prazo, valor...) não impede as outras; certificado recusado ou
        // Sefin fora do ar valem para todas: para em vez de repetir o mesmo erro.
        parar = !(x instanceof ApiError && x.status === 400);
      }
      setResultados(new Map(res));
    }
    setRodando(false);
    props.concluido();
  }

  const ok = [...resultados.values()].filter((r) => r.estado === "ok").length;
  const titulo = notas.length === 1 ? `Cancelar NFS-e nº ${notas[0].numero_nfse}` : `Cancelar ${notas.length} NFS-e`;

  return (
    <Dialogo
      titulo={
        !terminou
          ? titulo
          : notas.length === 1
            ? `NFS-e nº ${notas[0].numero_nfse} ${ok ? "cancelada" : "não foi cancelada"}`
            : `${ok} de ${notas.length} nota(s) cancelada(s)`
      }
      fechar={rodando ? undefined : props.fechar}
      rodape={
        terminou ? (
          <button key="fechar" className="btn btn--primario" onClick={props.fechar}>Fechar</button>
        ) : (
          <>
            <button className="btn" onClick={props.fechar} disabled={rodando}>Voltar</button>
            <button className={`btn ${prod ? "btn--escuro" : "btn--primario"}`} disabled={!pronto || rodando || !notas.length} onClick={cancelar}>
              {rodando && <Spinner />}
              {rodando ? `Cancelando ${resultados.size + 1} de ${notas.length}…` : notas.length === 1 ? "Cancelar a nota" : `Cancelar ${notas.length} notas`}
            </button>
          </>
        )
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {!terminou && (
          <div className="aviso">
            <div>
              <strong>O cancelamento é definitivo</strong>
              Depois de registrado na Sefin ele não pode ser desfeito. O município pode limitar o prazo e o valor para
              cancelar direto; fora disso a Sefin recusa e mostra o motivo.
            </div>
          </div>
        )}

        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", color: "var(--grafite)" }}>
          {ambientes.map((a) => <AmbChip key={a} ambiente={a} />)}
          <span>
            <b className="num" style={{ color: "var(--tinta)" }}>{notas.length}</b> nota(s) ·{" "}
            <b className="num" style={{ color: "var(--tinta)" }}>{moeda(total)}</b>
          </span>
        </div>

        <div className="tabela-caixa" style={{ maxHeight: 260 }}>
          <table className="tabela">
            <thead>
              <tr><th>NFS-e</th><th>Tomador</th><th className="dir">Valor</th>{resultados.size > 0 && <th>Resultado</th>}</tr>
            </thead>
            <tbody>
              {notas.map((e) => {
                const r = resultados.get(e.id);
                return (
                  <tr key={e.id}>
                    <td className="num">{e.numero_nfse}</td>
                    <td>
                      <div style={{ fontWeight: 550 }}>{e.tomador_nome}</div>
                      <div className="num" style={{ color: "var(--grafite)" }}>{formatarDocumento(e.tomador_documento)}</div>
                    </td>
                    <td className="dir num">{moeda(e.valor)}</td>
                    {resultados.size > 0 && (
                      <td style={{ minWidth: 200 }}>
                        {!r && rodando && <span style={{ color: "var(--grafite)" }}>aguardando…</span>}
                        {r?.estado === "ok" && <span className="chip chip--cancelada">Cancelada</span>}
                        {r?.estado === "ok" && r.aviso && <div style={{ fontSize: 12, marginTop: 4 }}>{r.aviso}</div>}
                        {r?.estado === "nao_enviada" && <span className="chip chip--nao_enviada">Não enviada</span>}
                        {r?.estado === "erro" && <ResultadoErro erro={r.erro} />}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {!terminou && (
          <>
            <label className="campo" style={{ maxWidth: 320 }}>
              <span>Motivo</span>
              <select value={motivo} onChange={(e) => setMotivo(e.target.value)} disabled={rodando}>
                {MOTIVOS.map((m) => <option key={m.valor} value={m.valor}>{m.rotulo}</option>)}
              </select>
            </label>
            <label className={`campo ${tamanho > 0 && (tamanho < MIN || tamanho > MAX) ? "campo--erro" : ""}`}>
              <span>Descrição do motivo (vai no evento de cancelamento)</span>
              <textarea
                rows={3}
                maxLength={MAX}
                value={texto}
                disabled={rodando}
                placeholder="Ex.: Valor digitado errado; a nota correta será emitida em seguida."
                onChange={(e) => setTexto(e.target.value)}
              />
              <small className="num">
                {tamanho}/{MAX} {tamanho < MIN && `· mínimo de ${MIN} caracteres`}
              </small>
            </label>
            {prod && (
              <label className="campo" style={{ maxWidth: 380 }}>
                <span>Nota de produção: para confirmar, digite CANCELAR</span>
                <input value={confirma} onChange={(e) => setConfirma(e.target.value)} disabled={rodando} />
              </label>
            )}
          </>
        )}
        {notas.length === 0 && <ErroCaixa erro={new Error("Nenhuma das notas selecionadas pode ser cancelada (só notas autorizadas).")} />}
      </div>
    </Dialogo>
  );
}

function ResultadoErro({ erro }: { erro: unknown }) {
  if (erro instanceof ApiError && erro.sefin) {
    return (
      <div className="erro-caixa" style={{ padding: "8px 10px", fontSize: 12.5 }}>
        <MensagensSefin erro={erro.sefin} />
      </div>
    );
  }
  return <ErroCaixa erro={erro} />;
}
