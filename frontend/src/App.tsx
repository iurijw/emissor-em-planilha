import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type Status } from "./api";
import { formatarDocumento } from "./format";
import { Clientes } from "./pages/Clientes";
import { Configuracoes } from "./pages/Configuracoes";
import { Emissoes } from "./pages/Emissoes";
import { Emitir } from "./pages/Emitir";
import { Onboarding } from "./pages/Onboarding";
import { ErroCaixa } from "./ui";

const ROTAS = [
  { id: "emitir", rotulo: "Emitir notas" },
  { id: "emissoes", rotulo: "Emissões" },
  { id: "clientes", rotulo: "Clientes" },
  { id: "configuracoes", rotulo: "Configurações" },
] as const;
type Rota = (typeof ROTAS)[number]["id"];

function rotaAtual(): Rota {
  const h = location.hash.replace(/^#\/?/, "").split("?")[0] as Rota;
  return ROTAS.some((r) => r.id === h) ? h : "emitir";
}

export function useStatus() {
  return useQuery({ queryKey: ["status"], queryFn: () => api.get<Status>("/api/status"), refetchInterval: 30_000 });
}

export function FaixaAmbiente({ status }: { status: Status }) {
  const prod = status.ambiente === "1";
  return (
    <div className={`faixa-amb ${prod ? "faixa-amb--prod" : "faixa-amb--homolog"}`} role="note">
      <span>{prod ? "Produção" : "Homologação"}</span>
      <span className="faixa-amb__dica">
        {prod
          ? "as notas emitidas têm validade jurídica"
          : "ambiente de testes da Sefin — notas sem validade jurídica"}
      </span>
      <button onClick={() => (location.hash = "#/configuracoes?secao=ambiente")}>Alterar ambiente</button>
    </div>
  );
}

export function App() {
  const [rota, setRota] = useState<Rota>(rotaAtual());
  const status = useStatus();

  useEffect(() => {
    const h = () => setRota(rotaAtual());
    window.addEventListener("hashchange", h);
    return () => window.removeEventListener("hashchange", h);
  }, []);

  if (status.isLoading) return <div className="carregando">Carregando…</div>;
  if (status.error || !status.data)
    return (
      <div className="pagina">
        <ErroCaixa erro={status.error} titulo="Não foi possível abrir o Emissor" />
      </div>
    );
  const s = status.data;
  if (!s.onboarding_concluido) return <Onboarding status={s} />;

  const cert = s.certificado;
  return (
    <>
      <FaixaAmbiente status={s} />
      <header className="topo">
        <a className="marca" href="#/emitir">
          <img src="/logo.svg" alt="" />
          <span>Emissor em Planilha</span>
        </a>
        <nav className="nav" aria-label="Seções">
          {ROTAS.map((r) => (
            <a key={r.id} href={`#/${r.id}`} aria-current={rota === r.id ? "page" : undefined}>
              {r.rotulo}
            </a>
          ))}
        </nav>
        <div className="topo__info">
          {s.prestador.cnpj && (
            <span>
              Prestador <strong className="num">{formatarDocumento(s.prestador.cnpj)}</strong>
            </span>
          )}
          {cert && !cert.erro && (
            <span title={`Certificado de ${cert.titular}`}>
              Certificado até <strong>{new Date(cert.valido_ate).toLocaleDateString("pt-BR")}</strong>
            </span>
          )}
        </div>
      </header>
      {(s.avisos.length > 0 || s.problemas.length > 0) && rota !== "configuracoes" && (
        <div style={{ padding: "12px 24px 0", maxWidth: 1480, margin: "0 auto" }}>
          {s.problemas.length > 0 && (
            <div className="erro-caixa" role="alert">
              <div>
                <strong>Emissão bloqueada</strong>
                <ul>
                  {s.problemas.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
                <a href="#/configuracoes">Abrir configurações</a>
              </div>
            </div>
          )}
          {s.avisos.map((a) => (
            <div key={a} className="aviso">
              {a}
            </div>
          ))}
        </div>
      )}
      <main>
        {rota === "emitir" && <Emitir status={s} />}
        {rota === "emissoes" && <Emissoes />}
        {rota === "clientes" && <Clientes />}
        {rota === "configuracoes" && <Configuracoes status={s} />}
      </main>
    </>
  );
}
