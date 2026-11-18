import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Config, type DadosTributacao } from "../api";
import { Dialogo, ErroCaixa, Spinner, useToast } from "../ui";
import { useTributacoes } from "./Abas";
import { CamposTributacao } from "./FiscalForm";

/** Tributação em edição: a padrão (mora nas configurações), uma predefinida (id) ou uma nova (null). */
export type EdicaoTributacao = {
  id: number | "padrao" | null;
  nome: string;
  dados: DadosTributacao;
  /** Tributação nova feita a partir desta (botão "Copiar"). */
  copiaDe?: string;
};

/**
 * Diálogo de uma tributação, o mesmo de Configurações → Tributações. Também abre pela tabela (botão da tributação
 * da aba), para mudar sem sair da emissão. Só o administrador da empresa grava (o servidor confere).
 */
export function EditorTributacao(props: {
  edicao: EdicaoTributacao;
  fechar: () => void;
  /** Depois de gravar; ``id`` da tributação nova ou editada (null para a padrão). */
  salvo?: (id: number | null) => void;
}) {
  const lista = useTributacoes();
  const cfg = useQuery({ queryKey: ["config"], queryFn: () => api.get<Config>("/api/config") });
  const qc = useQueryClient();
  const toast = useToast();
  const [editando, setEditando] = useState(props.edicao);
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);
  const padrao = editando.id === "padrao";

  async function salvar() {
    setErro(null);
    setOcupado(true);
    try {
      let id: number | null = null;
      if (editando.id === "padrao") {
        // A tributação padrão mora nas configurações, junto dos dados da empresa.
        if (!cfg.data?.fiscal) throw new Error("Configurações não carregadas. Recarregue a página.");
        await api.put("/api/config", { fiscal: { ...cfg.data.fiscal, ...editando.dados } });
        const nome = editando.nome.trim();
        if (nome !== (lista.data?.padrao_nome ?? "")) await api.put("/api/tributacoes/padrao", { nome });
        toast(`Tributação “${nome}” salva.`);
        qc.invalidateQueries();
      } else {
        const corpo = { nome: editando.nome, dados: editando.dados };
        const r = editando.id
          ? await api.put<{ id: number }>(`/api/tributacoes/${editando.id}`, corpo)
          : await api.post<{ id: number }>("/api/tributacoes", corpo);
        id = r?.id ?? (typeof editando.id === "number" ? editando.id : null);
        toast(`Tributação “${editando.nome.trim()}” salva.`);
        qc.invalidateQueries({ queryKey: ["tributacoes"] });
        qc.invalidateQueries({ queryKey: ["abas"] });
      }
      props.salvo?.(id);
      props.fechar();
    } catch (e) {
      setErro(e);
    } finally {
      setOcupado(false);
    }
  }

  return (
    <Dialogo
      titulo={
        padrao
          ? props.edicao.nome === "Tributação padrão" ? "Tributação padrão" : `Tributação “${props.edicao.nome}” (padrão)`
          : editando.id ? `Tributação “${props.edicao.nome}”` : "Nova tributação"
      }
      fechar={props.fechar}
      largo
      rodape={
        <>
          <button className="btn" onClick={props.fechar}>Cancelar</button>
          <button className="btn btn--primario" disabled={ocupado || !editando.nome.trim()} onClick={salvar}>
            {ocupado && <Spinner />} Salvar tributação
          </button>
        </>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 26 }}>
        <label className="campo" style={{ maxWidth: 420 }}>
          <span>Nome</span>
          <input
            value={editando.nome}
            maxLength={60}
            autoFocus
            data-autofocus
            placeholder={padrao ? "Tributação padrão" : "Ex.: ISS retido pelo tomador"}
            onChange={(e) => setEditando({ ...editando, nome: e.target.value })}
          />
          {padrao ? (
            <small>A padrão: vale para as abas sem tributação própria e para a nota avulsa quando nenhuma outra é escolhida.</small>
          ) : editando.copiaDe ? (
            <small>Cópia de “{editando.copiaDe}”, com os mesmos campos; mude só o que for diferente.</small>
          ) : (
            !editando.id && <small>Começa igual à tributação padrão; mude só o que for diferente.</small>
          )}
        </label>
        <CamposTributacao valor={editando.dados} onChange={(d) => setEditando({ ...editando, dados: d })} regime={lista.data?.regime} />
        <ErroCaixa erro={erro} titulo="Tributação não salva" />
      </div>
    </Dialogo>
  );
}
