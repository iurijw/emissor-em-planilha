import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type Sessao, type Status, type UsuarioSessao } from "./api";
import { formatarDocumento } from "./format";
import { AvisoSuspenso, TelaConvite, TelaLogin, TelaPrimeiroAcesso } from "./pages/Acesso";
import { Clientes } from "./pages/Clientes";
import { MinhaConta } from "./pages/Conta";
import { Usuarios } from "./pages/Usuarios";
import { Configuracoes } from "./pages/Configuracoes";
import { Emissoes } from "./pages/Emissoes";
import { Emitir } from "./pages/Emitir";
import { Onboarding } from "./pages/Onboarding";
import { ErroCaixa } from "./ui";

const ROTAS = [
  { id: "emitir", rotulo: "Emitir notas", admin: false },
  { id: "emissoes", rotulo: "Emissões", admin: false },
  { id: "clientes", rotulo: "Clientes", admin: false },
  { id: "configuracoes", rotulo: "Configurações", admin: true },
  { id: "usuarios", rotulo: "Usuários", admin: true },
] as const;
type Rota = (typeof ROTAS)[number]["id"] | "conta";

function rotaHash(): string {
  return location.hash.replace(/^#\/?/, "").split("?")[0];
}

function rotaAtual(): Rota {
  const h = rotaHash() as Rota;
  return h === "conta" || ROTAS.some((r) => r.id === h) ? h : "emitir";
}

export function useSessao() {
  return useQuery({ queryKey: ["sessao"], queryFn: () => api.get<Sessao>("/api/auth/sessao"), staleTime: 60_000 });
}

/** Decide entre login, primeiro acesso, convite e o sistema. */
export function Raiz() {
  const qc = useQueryClient();
  const sessao = useSessao();
  const [hash, setHash] = useState(rotaHash());
  useEffect(() => {
    const h = () => setHash(rotaHash());
    const expirou = () => qc.invalidateQueries({ queryKey: ["sessao"] });
    window.addEventListener("hashchange", h);
    window.addEventListener("emissor:sessao-expirada", expirou);
    return () => {
      window.removeEventListener("hashchange", h);
      window.removeEventListener("emissor:sessao-expirada", expirou);
    };
  }, [qc]);

  if (hash === "convite") return <TelaConvite />;
  if (sessao.isLoading) return <div className="carregando">Carregando…</div>;
  if (sessao.error || !sessao.data)
    return (
      <div className="pagina">
        <ErroCaixa erro={sessao.error} titulo="Não foi possível abrir o Emissor" />
      </div>
    );
  const s = sessao.data;
  if (s.primeiro_acesso) return <TelaPrimeiroAcesso sessao={s} />;
  if (!s.autenticado || !s.usuario) return <TelaLogin sessao={s} />;
  return <App sessao={s} usuario={s.usuario} />;
}

export function useStatus() {
  return useQuery({ queryKey: ["status"], queryFn: () => api.get<Status>("/api/status"), refetchInterval: 30_000 });
}

export function FaixaAmbiente({ status, admin = true }: { status: Status; admin?: boolean }) {
  const prod = status.ambiente === "1";
  return (
    <div className={`faixa-amb ${prod ? "faixa-amb--prod" : "faixa-amb--homolog"}`} role="note">
      <span>{prod ? "Produção" : "Homologação"}</span>
      <span className="faixa-amb__dica">
        {prod
          ? "as notas emitidas têm validade jurídica"
          : "ambiente de testes da Sefin — notas sem validade jurídica"}
      </span>
      {admin && <button onClick={() => (location.hash = "#/configuracoes?secao=ambiente")}>Alterar ambiente</button>}
    </div>
  );
}

function MenuUsuario({ usuario }: { usuario: UsuarioSessao }) {
  const qc = useQueryClient();
  async function sair() {
    await api.post("/api/auth/sair").catch(() => undefined);
    location.hash = "#/emitir";
    await qc.resetQueries();
  }
  return (
    <span className="topo__usuario">
      <a href="#/conta" title={usuario.email}>{usuario.nome || usuario.email}</a>
      <button className="btn btn--pequeno btn--fantasma" onClick={sair}>Sair</button>
    </span>
  );
}

export function App({ sessao, usuario }: { sessao: Sessao; usuario: UsuarioSessao }) {
  const [rota, setRota] = useState<Rota>(rotaAtual());
  const status = useStatus();
  const admin = usuario.papel === "admin";

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
  if (!s.onboarding_concluido) {
    if (admin) return <Onboarding status={s} />;
    return (
      <div className="pagina pagina--estreita">
        <div className="info-caixa">
          <span>
            O sistema ainda não foi configurado. Peça a um administrador para concluir a configuração inicial (certificado digital e
            padrões da nota).{" "}
          </span>
        </div>
        <p><MenuUsuario usuario={usuario} /></p>
      </div>
    );
  }
  const rotas = ROTAS.filter((r) => admin || !r.admin);
  const rotaVisivel = rota === "conta" || rotas.some((r) => r.id === rota) ? rota : "emitir";

  const cert = s.certificado;
  return (
    <>
      <FaixaAmbiente status={s} admin={admin} />
      {sessao.suspenso && (
        <div className="aviso faixa-suspenso" role="alert">
          <AvisoSuspenso sessao={sessao} />
        </div>
      )}
      <header className="topo">
        <a className="marca" href="#/emitir">
          <img src="/logo.svg" alt="" />
          <span>Emissor em Planilha</span>
        </a>
        <nav className="nav" aria-label="Seções">
          {rotas.map((r) => (
            <a key={r.id} href={`#/${r.id}`} aria-current={rotaVisivel === r.id ? "page" : undefined}>
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
        <MenuUsuario usuario={usuario} />
      </header>
      {(s.avisos.length > 0 || s.problemas.length > 0) && rotaVisivel !== "configuracoes" && (
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
                {admin ? <a href="#/configuracoes">Abrir configurações</a> : <span>Fale com um administrador.</span>}
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
        {!usuario.totp_ativo && rotaVisivel !== "conta" && (
          <div style={{ padding: "12px 24px 0", maxWidth: 1480, margin: "0 auto" }}>
            <div className="info-caixa">
              <span>
                Proteja o acesso ao certificado: <a href="#/conta">ative a verificação em duas etapas</a> com um aplicativo
                autenticador no celular.
              </span>
            </div>
          </div>
        )}
        {rotaVisivel === "emitir" && <Emitir status={s} />}
        {rotaVisivel === "emissoes" && <Emissoes />}
        {rotaVisivel === "clientes" && <Clientes />}
        {rotaVisivel === "configuracoes" && <Configuracoes status={s} />}
        {rotaVisivel === "usuarios" && <Usuarios meuId={usuario.id} />}
        {rotaVisivel === "conta" && <MinhaConta />}
      </main>
    </>
  );
}
