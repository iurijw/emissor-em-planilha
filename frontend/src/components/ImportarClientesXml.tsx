import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { formatarDocumento } from "../format";
import { ZonaArquivo } from "../pages/Onboarding";
import { ErroCaixa, Spinner, useToast } from "../ui";

interface Resultado {
  notas_lidas: number;
  novos: { documento: string; nome: string }[];
  completados: { documento: string; nome: string }[];
  sem_alteracao: number;
  ignorados: { arquivo: string; motivo: string }[];
}

/** Cadastro automático de clientes a partir de XMLs de NFS-e (soltos ou em .zip). */
export function ImportarClientesXml() {
  const qc = useQueryClient();
  const toast = useToast();
  const [ocupado, setOcupado] = useState(false);
  const [erro, setErro] = useState<unknown>(null);
  const [res, setRes] = useState<Resultado | null>(null);

  async function enviar(arquivos: File[]) {
    if (!arquivos.length) return;
    setErro(null);
    setRes(null);
    setOcupado(true);
    try {
      const fd = new FormData();
      arquivos.forEach((a) => fd.append("arquivos", a));
      const r = await api.post<Resultado>("/api/clientes/importar-xmls", fd);
      setRes(r);
      toast(`${r.novos.length} cliente(s) cadastrado(s) a partir de ${r.notas_lidas} nota(s).`);
      qc.invalidateQueries({ queryKey: ["clientes"] });
    } catch (e) {
      setErro(e);
    } finally {
      setOcupado(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <p style={{ margin: 0, color: "var(--grafite)" }}>
        Envie XMLs de NFS-e já emitidas (pode ser um .zip com todos). Cada tomador vira um cliente com nome, CNPJ e endereço.
        Clientes já cadastrados só têm os campos vazios completados: nada que você editou é alterado.
      </p>
      <ZonaArquivo
        aceitar=".xml,.zip"
        multiplo
        titulo={ocupado ? "Lendo as notas…" : "Escolha ou arraste os XMLs ou um .zip"}
        dica="Pode selecionar vários arquivos de uma vez"
        onArquivos={enviar}
      />
      {ocupado && (
        <p className="carregando" style={{ padding: 0 }}>
          <Spinner /> Cadastrando clientes…
        </p>
      )}
      <ErroCaixa erro={erro} titulo="Não foi possível importar" />
      {res && (
        <div className="info-caixa" style={{ display: "block" }}>
          <p style={{ margin: 0 }}>
            <b className="num">{res.notas_lidas}</b> nota(s) lida(s) · <b className="num">{res.novos.length}</b> cliente(s) novo(s) ·{" "}
            <b className="num">{res.completados.length}</b> completado(s) · <b className="num">{res.sem_alteracao}</b> já estava(m)
            completo(s)
          </p>
          {res.novos.length > 0 && (
            <details style={{ marginTop: 8 }}>
              <summary style={{ cursor: "pointer" }}>Ver clientes novos</summary>
              <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                {res.novos.map((c) => (
                  <li key={c.documento}>
                    {c.nome} <span className="num">{formatarDocumento(c.documento)}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
          {res.completados.length > 0 && (
            <details style={{ marginTop: 6 }}>
              <summary style={{ cursor: "pointer" }}>Ver clientes completados</summary>
              <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                {res.completados.map((c) => (
                  <li key={c.documento}>
                    {c.nome} <span className="num">{formatarDocumento(c.documento)}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
      {res && res.ignorados.length > 0 && (
        <div className="aviso">
          <div>
            <strong>{res.ignorados.length} arquivo(s) ou nota(s) ignorado(s)</strong>
            <ul>
              {res.ignorados.slice(0, 20).map((i, n) => (
                <li key={n}>
                  {i.arquivo}: {i.motivo}
                </li>
              ))}
              {res.ignorados.length > 20 && <li>… e mais {res.ignorados.length - 20}.</li>}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
