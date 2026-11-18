import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, dadosTributacao, type Aba, type DadosTributacao } from "../api";
import { Spinner, usePopover, useToast } from "../ui";
import { nomeDeCopia, useTributacoes } from "./Abas";
import { EditorTributacao, type EdicaoTributacao } from "./EditorTributacao";

/**
 * Tributação da aba aberta, em cima da tabela: mostra com que tributação as notas desta aba saem, troca por outra
 * e (administrador) abre a tributação para editar, sem ir a Configurações. Trocar aqui é o mesmo que trocar no
 * diálogo da aba.
 */
export function TributacaoDaAba({ aba, admin }: { aba: Aba; admin: boolean }) {
  const trib = useTributacoes();
  const qc = useQueryClient();
  const toast = useToast();
  const pop = usePopover();
  const [trocando, setTrocando] = useState<number | "padrao" | null>(null);
  const [editando, setEditando] = useState<EdicaoTributacao | null>(null);
  const itens = trib.data?.itens ?? [];
  const atual = aba.tributacao_id ? itens.find((t) => t.id === aba.tributacao_id) : null;
  const nomePadrao = trib.data?.padrao_nome || "Tributação padrão";
  const nome = aba.tributacao_excluida ? "Tributação excluída" : aba.tributacao_nome ?? nomePadrao;
  const resumo = atual?.resumo ?? (aba.tributacao_id ? null : trib.data?.padrao_resumo) ?? null;

  async function trocar(id: number | null) {
    if (id === aba.tributacao_id && !aba.tributacao_excluida) return pop.fechar();
    setTrocando(id ?? "padrao");
    try {
      await api.put<Aba>(`/api/abas/${aba.id}`, { nome: aba.nome, tributacao_id: id, clientes: aba.clientes.map((c) => c.documento) });
      await qc.invalidateQueries({ queryKey: ["abas"] });
      await qc.invalidateQueries({ queryKey: ["tributacoes"] });
      const novo = id ? itens.find((t) => t.id === id)?.nome : nomePadrao;
      toast(`As notas da aba “${aba.nome}” saem agora com “${novo}”.`);
      pop.fechar();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Não foi possível trocar a tributação.", "erro");
    } finally {
      setTrocando(null);
    }
  }

  function editar(id: number | "padrao" | null) {
    pop.fechar();
    const base = trib.data?.padrao;
    if (!base) return;
    if (id === "padrao") setEditando({ id: "padrao", nome: nomePadrao, dados: dadosTributacao(base) });
    else if (id === null) setEditando({ id: null, nome: "", dados: dadosTributacao(base) });
    else {
      const t = itens.find((x) => x.id === id);
      if (t) setEditando({ id: t.id, nome: t.nome, dados: dadosTributacao(t.dados) });
    }
  }

  function copiar(id: number | null) {
    pop.fechar();
    const origem = id === null ? { nome: nomePadrao, dados: trib.data?.padrao } : itens.find((t) => t.id === id);
    if (!origem?.dados) return;
    const dados: DadosTributacao = dadosTributacao(origem.dados);
    setEditando({ id: null, nome: nomeDeCopia(origem.nome, [nomePadrao, ...itens.map((t) => t.nome)]), dados, copiaDe: origem.nome });
  }

  const opcoes: { id: number | null; nome: string; resumo: string | null }[] = [
    { id: null, nome: nomePadrao, resumo: trib.data?.padrao_resumo ?? null },
    ...itens.map((t) => ({ id: t.id, nome: t.nome, resumo: t.resumo })),
  ];
  const escolhida = (id: number | null) => !aba.tributacao_excluida && (aba.tributacao_id ?? null) === id;

  return (
    <div className="trib-aba">
      <button
        type="button"
        className={`trib-aba__botao${aba.tributacao_excluida ? " trib-aba__botao--erro" : ""}`}
        popovertarget="trib-aba-lista"
        aria-haspopup="listbox"
        title={resumo ? `${nome}: ${resumo}` : nome}
      >
        <span className="trib-aba__rotulo">Tributação</span>
        <span className="trib-aba__nome">{nome}</span>
        <svg className="trib-aba__seta" width="10" height="10" viewBox="0 0 10 10" aria-hidden>
          <path d="M2 3.5 5 6.5 8 3.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      <div className="trib-aba__lista" id="trib-aba-lista" popover="auto" ref={pop.ref}>
        {pop.aberto && (
          <>
            <p className="trib-aba__titulo">Notas da aba “{aba.nome}” saem com</p>
            <div role="listbox" aria-label="Tributação da aba" className="trib-aba__opcoes">
              {opcoes.map((o) => (
                <div key={o.id ?? "padrao"} className="trib-aba__linha">
                  <button
                    type="button"
                    role="option"
                    aria-selected={escolhida(o.id)}
                    className="trib-aba__op"
                    disabled={trocando !== null}
                    onClick={() => trocar(o.id)}
                  >
                    <span className="trib-aba__marca" aria-hidden>
                      {trocando === (o.id ?? "padrao") ? <Spinner /> : escolhida(o.id) ? "●" : ""}
                    </span>
                    <span className="trib-aba__texto">
                      <strong>{o.nome}</strong>
                      {o.resumo && <small>{o.resumo}</small>}
                    </span>
                  </button>
                  {admin && (
                    <span className="trib-aba__acoes">
                      <button
                        type="button"
                        className="btn btn--pequeno btn--fantasma"
                        aria-label={`Copiar ${o.nome} para uma tributação nova`}
                        title="Fazer uma tributação nova a partir desta"
                        onClick={() => copiar(o.id)}
                      >
                        Copiar
                      </button>
                      <button
                        type="button"
                        className="btn btn--pequeno btn--fantasma"
                        aria-label={`Editar ${o.nome}`}
                        title={`Editar ${o.nome}`}
                        onClick={() => editar(o.id ?? "padrao")}
                      >
                        Editar
                      </button>
                    </span>
                  )}
                </div>
              ))}
            </div>
            {admin ? (
              <button type="button" className="trib-aba__nova" onClick={() => editar(null)}>
                + Nova tributação
              </button>
            ) : (
              <p className="trib-aba__nota">Um administrador cadastra e edita as tributações.</p>
            )}
          </>
        )}
      </div>
      {editando && (
        <EditorTributacao
          edicao={editando}
          fechar={() => setEditando(null)}
          // Tributação nova: já passa a valer para esta aba (foi para isso que foi criada daqui).
          salvo={(id) => editando.id === null && id && trocar(id)}
        />
      )}
    </div>
  );
}
