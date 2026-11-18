import { useQuery } from "@tanstack/react-query";
import { useId, useMemo, useState } from "react";
import { api, type EscolhaTributacaoHistorico, type HistoricoAvulsa as Historico } from "../api";
import { formatarDocumento } from "../format";
import { Dialogo, ErroCaixa } from "../ui";
import { normalizarBusca } from "./EditorNomeCliente";

type Aba = "descricoes" | "tributacoes";

/** Relógio pequeno: abre o histórico. Fica ao lado do rótulo, sem ocupar espaço do formulário. */
export function BotaoHistorico(props: { titulo: string; onClick: () => void }) {
  return (
    <button type="button" className="botao-historico" aria-label={props.titulo} title={props.titulo} onClick={props.onClick}>
      <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <path d="M2.6 5.2A6 6 0 1 1 2 8" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        <path d="M2.2 2.4v3h3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M8 4.8V8l2.2 1.4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </button>
  );
}

const quando = (iso: string) => new Date(iso).toLocaleDateString("pt-BR");
const vezes = (n: number) => (n === 1 ? "1 nota" : `${n} notas`);

/**
 * Descrições e tributações das notas já emitidas, para reaproveitar na nota avulsa. Com um tomador digitado, dá para
 * ver só o que já foi usado para ele. Escolher só preenche o campo: nada é emitido daqui.
 */
export function HistoricoAvulsa(props: {
  documento: string;
  nomeTomador: string;
  aba: Aba;
  usarDescricao: (texto: string) => void;
  usarTributacao: (t: EscolhaTributacaoHistorico) => void;
  fechar: () => void;
}) {
  const id = useId();
  const [aba, setAba] = useState<Aba>(props.aba);
  const [busca, setBusca] = useState("");
  const temTomador = props.documento.length >= 11;
  const q = useQuery({
    queryKey: ["avulsa-historico", temTomador ? props.documento : ""],
    queryFn: () => api.get<Historico>(`/api/avulsa/historico${temTomador ? `?documento=${props.documento}` : ""}`),
  });
  const doTomador = q.data ? [...q.data.descricoes, ...q.data.tributacoes].some((x) => x.do_tomador) : false;
  // Começa filtrado pelo tomador quando ele já tem notas; o usuário desmarca para ver todas.
  const [soTomador, setSoTomador] = useState<boolean | null>(null);
  const filtrar = temTomador && (soTomador ?? doTomador);
  const tomador = props.nomeTomador.trim() || formatarDocumento(props.documento);

  const termo = normalizarBusca(busca);
  const descricoes = useMemo(
    () => (q.data?.descricoes ?? []).filter((d) => (!filtrar || d.do_tomador) && (!termo || normalizarBusca(d.texto).includes(termo))),
    [q.data, filtrar, termo],
  );
  const tributacoes = useMemo(
    () =>
      (q.data?.tributacoes ?? []).filter(
        (t) => (!filtrar || t.do_tomador) && (!termo || normalizarBusca(`${t.nome} ${t.resumo ?? ""}`).includes(termo)),
      ),
    [q.data, filtrar, termo],
  );
  const lista = aba === "descricoes" ? descricoes : tributacoes;

  return (
    <Dialogo titulo="Usadas em notas anteriores" fechar={props.fechar} className="dialogo--historico">
      <div className="historico">
        <div className="historico__topo">
          <div className="historico__abas" role="tablist" aria-label="O que reaproveitar">
            {(
              [
                ["descricoes", "Descrições"],
                ["tributacoes", "Tributações"],
              ] as const
            ).map(([valor, rotulo]) => (
              <button
                key={valor}
                type="button"
                role="tab"
                id={`${id}-${valor}`}
                aria-selected={aba === valor}
                aria-controls={`${id}-lista`}
                className="historico__aba"
                onClick={() => setAba(valor)}
              >
                {rotulo}
              </button>
            ))}
          </div>
          <input
            className="input historico__busca"
            type="search"
            value={busca}
            onChange={(e) => setBusca(e.target.value)}
            placeholder={aba === "descricoes" ? "Buscar no texto" : "Buscar pelo nome"}
            aria-label="Buscar"
            data-autofocus
          />
          {temTomador && (
            <label className="historico__filtro">
              <input type="checkbox" checked={filtrar} onChange={(e) => setSoTomador(e.target.checked)} />
              <span>
                Só as usadas para <b>{tomador}</b>
              </span>
            </label>
          )}
        </div>

        <ErroCaixa erro={q.error} titulo="Não foi possível carregar o histórico" />
        {q.isLoading && <p className="carregando">Carregando…</p>}
        <ul className="historico__lista" id={`${id}-lista`} role="tabpanel" aria-labelledby={`${id}-${aba}`}>
          {aba === "descricoes"
            ? descricoes.map((d) => (
                <li key={d.texto}>
                  <button
                    type="button"
                    className="historico__item"
                    onClick={() => {
                      props.usarDescricao(d.texto);
                      props.fechar();
                    }}
                  >
                    <span className="historico__texto">{d.texto}</span>
                    <span className="historico__meta">
                      {vezes(d.vezes)}, a última em {quando(d.ultima_em)}
                      {!filtrar && d.ultimo_tomador ? ` para ${d.ultimo_tomador}` : ""}
                    </span>
                  </button>
                </li>
              ))
            : tributacoes.map((t, i) => (
                <li key={`${t.tipo}-${t.tributacao_id ?? i}`}>
                  <button
                    type="button"
                    className="historico__item"
                    onClick={() => {
                      props.usarTributacao(t);
                      props.fechar();
                    }}
                  >
                    <span className="historico__texto">
                      <b>{t.nome}</b>
                      {t.tipo === "propria" && <span className="historico__marca">como estava na nota</span>}
                    </span>
                    {t.resumo && <span className="historico__resumo">{t.resumo}</span>}
                    <span className="historico__meta">
                      {vezes(t.vezes)}, a última em {quando(t.ultima_em)}
                    </span>
                  </button>
                </li>
              ))}
        </ul>
        {q.data && lista.length === 0 && (
          <p className="historico__vazio">
            {termo
              ? "Nada com esse texto."
              : filtrar
                ? `Nenhuma nota emitida para ${tomador} ainda.`
                : "Nenhuma nota emitida ainda. Depois da primeira, as descrições e tributações aparecem aqui."}
          </p>
        )}
        {aba === "tributacoes" && tributacoes.some((t) => t.tipo === "propria") && (
          <p className="historico__nota">
            “Como estava na nota”: a tributação mudou depois ou era só daquela nota. Ela entra em “Só para esta nota”, com os
            campos daquela vez.
          </p>
        )}
      </div>
    </Dialogo>
  );
}
