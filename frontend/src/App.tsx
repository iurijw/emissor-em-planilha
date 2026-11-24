import { useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useLayoutEffect, useState, type ReactNode } from "react";
import { api, type Sessao, type Status, type UsuarioSessao } from "./api";
import { formatarDocumento } from "./format";
import { AvisoSuspenso, TelaConvite, TelaLogin, TelaPrimeiroAcesso } from "./pages/Acesso";
import { Clientes } from "./pages/Clientes";
import { MinhaConta } from "./pages/Conta";
import { Usuarios } from "./pages/Usuarios";
import { Configuracoes } from "./pages/Configuracoes";
import { Emissoes } from "./pages/Emissoes";
import { Onboarding } from "./pages/Onboarding";
import { ErroCaixa, Logo, Spinner } from "./ui";
import { FaixaAssinatura, useAssinatura } from "./components/Assinatura";
import { Atualizacoes } from "./components/Atualizacoes";
import { BotaoSuporte, LinkSuporte } from "./components/Suporte";
import { nomeEmpresa, SeletorEmpresa } from "./components/Empresas";
import { usePreferencias } from "./preferencias";

// A tela de emissão traz a grade (Glide Data Grid, a maior parte do código): fica num arquivo à parte, baixado
// só depois do login (a tela de entrada fica leve). Logo depois do login começa a baixar, sem esperar a tela abrir.
const carregarEmitir = () => import("./pages/Emitir").then((m) => ({ default: m.Emitir }));
const Emitir = lazy(carregarEmitir);

const ROTAS = [
  { id: "emitir", rotulo: "Emitir notas", admin: false },
  { id: "emissoes", rotulo: "Emissões", admin: false },
  { id: "clientes", rotulo: "Clientes", admin: false },
  { id: "configuracoes", rotulo: "Configurações", admin: "empresa" },
  { id: "usuarios", rotulo: "Usuários", admin: true },
] as const;

/** Ícones das seções: aparecem no lugar dos nomes quando o cabeçalho não cabe na largura da janela. */
const ICONES_ROTA: Record<(typeof ROTAS)[number]["id"], ReactNode> = {
  // Folha com a grade da planilha (a tabela de notas a emitir).
  emitir: (
    <>
      <rect x="4" y="3" width="16" height="18" rx="2" />
      <path d="M4 9h16M4 15h16M10 9v12" />
    </>
  ),
  // Notas emitidas: documento com linhas.
  emissoes: (
    <>
      <path d="M6 3h8.5L19 7.5V20a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" />
      <path d="M14 3v5h5M8.5 12.5h7M8.5 16.5h5" />
    </>
  ),
  clientes: (
    <>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2.5 20c.4-3.4 3.1-5.5 6.5-5.5s6.1 2.1 6.5 5.5M15.5 4.8a3.5 3.5 0 0 1 0 6.4M18 14.9c2 .8 3.2 2.6 3.5 5.1" />
    </>
  ),
  configuracoes: (
    <>
      <path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1" />
      <circle cx="15" cy="6" r="2" />
      <circle cx="9" cy="12" r="2" />
      <circle cx="17" cy="18" r="2" />
    </>
  ),
  // Usuários e acessos: pessoa com escudo.
  usuarios: (
    <>
      <circle cx="10" cy="7.5" r="3.5" />
      <path d="M3.5 20c.4-3.5 3-5.8 6.5-5.8 1.1 0 2.1.2 3 .7" />
      <path d="M18 13.5l3 1.1v2.5c0 1.9-1.2 3.3-3 4-1.8-.7-3-2.1-3-4v-2.5l3-1.1Z" />
    </>
  ),
};

function IconeRota({ id }: { id: keyof typeof ICONES_ROTA }) {
  return (
    <svg className="nav__icone" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {ICONES_ROTA[id]}
    </svg>
  );
}

/**
 * O cabeçalho encolhe por etapas só quando não cabe (medido, sem largura fixa: o que aparece nele muda com o papel, a
 * empresa, o certificado e o nome do usuário). Cada etapa acrescenta uma palavra em data-compacto:
 * "info" = sem prestador/certificado (também estão em Configurações); "nav" = seções só com o ícone; "marca" = sem o
 * nome do sistema e "Assinatura" vira ícone; "usuario" = o nome do usuário vira as iniciais. Mede de novo ao mudar a
 * janela, o conteúdo ou ao carregar as fontes.
 */
const ETAPAS_CABECALHO = ["", "info", "info nav", "info nav marca", "info nav marca usuario"];

function useCabecalhoCompacto() {
  // Ref por estado: o cabeçalho só aparece depois de carregar o status (e some no onboarding).
  const [topo, setTopo] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (!topo) return;
    let quadro = 0;
    const cabe = () => {
      // No celular as seções ficam numa linha própria que rola de lado: ali conta o que passa dessa linha.
      const nav = topo.querySelector<HTMLElement>(".nav");
      const navRola = nav && getComputedStyle(nav).overflowX !== "visible";
      return topo.scrollWidth <= topo.clientWidth && (!navRola || nav.scrollWidth <= nav.clientWidth);
    };
    const medir = () => {
      quadro = 0;
      for (const etapa of ETAPAS_CABECALHO) {
        topo.dataset.compacto = etapa;
        if (cabe()) return;
      }
    };
    const agendar = () => {
      if (!quadro) quadro = requestAnimationFrame(medir);
    };
    medir();
    // Evento de resize da janela (e não ResizeObserver no próprio cabeçalho): mudar a etapa muda a altura dele, e o
    // observador dispararia de novo dentro do mesmo quadro ("ResizeObserver loop"), erro que iria para o log.
    window.addEventListener("resize", agendar);
    const mudancas = new MutationObserver(agendar);
    mudancas.observe(topo, { childList: true, subtree: true, characterData: true });
    void document.fonts?.ready.then(agendar);
    return () => {
      window.removeEventListener("resize", agendar);
      mudancas.disconnect();
      if (quadro) cancelAnimationFrame(quadro);
    };
  }, [topo]);
  return setTopo;
}

function iniciais(nome: string): string {
  const partes = nome.trim().split(/\s+/).filter(Boolean);
  if (!partes.length) return "?";
  const [a, b] = [partes[0], partes.length > 1 ? partes[partes.length - 1] : ""];
  return (a[0] + (b[0] ?? "")).toUpperCase();
}
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
      {status.varias_empresas && <span className="faixa-amb__empresa">{nomeEmpresa(status)}</span>}
      <span className="faixa-amb__dica">
        {prod
          ? "as notas emitidas têm validade jurídica"
          : "ambiente de testes da Sefin — notas sem validade jurídica"}
      </span>
      {admin && <button onClick={() => (location.hash = "#/configuracoes?secao=ambiente")}>Alterar ambiente</button>}
    </div>
  );
}

function MenuUsuario({ usuario, urlAssinatura }: { usuario: UsuarioSessao; urlAssinatura?: string | null }) {
  const qc = useQueryClient();
  async function sair() {
    await api.post("/api/auth/sair").catch(() => undefined);
    location.hash = "#/emitir";
    await qc.resetQueries();
  }
  return (
    <span className="topo__usuario">
      {urlAssinatura && usuario.papel === "admin" && (
        <a className="topo__assinatura" href={urlAssinatura} target="_blank" rel="noreferrer" title="Cartão, faturas e cancelamento">
          <svg className="topo__assinatura-icone" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
            <rect x="2.5" y="5" width="19" height="14" rx="2" />
            <path d="M2.5 10h19M6.5 15h4" />
          </svg>
          <span className="topo__assinatura-texto">Assinatura</span>
        </a>
      )}
      <a className="topo__nome" href="#/conta" title={usuario.email}>
        <span className="topo__nome-texto">{usuario.nome || usuario.email}</span>
        <span className="topo__iniciais" aria-hidden="true">
          {iniciais(usuario.nome || usuario.email)}
        </span>
      </a>
      <button className="btn btn--pequeno btn--fantasma" onClick={sair}>Sair</button>
    </span>
  );
}

export function App({ sessao, usuario }: { sessao: Sessao; usuario: UsuarioSessao }) {
  useEffect(() => {
    void carregarEmitir();
  }, []);
  const [rota, setRota] = useState<Rota>(rotaAtual());
  const status = useStatus();
  const topoRef = useCabecalhoCompacto();
  const assinatura = useAssinatura();
  const [preferencias, salvarPreferencias] = usePreferencias();
  const admin = usuario.papel === "admin";
  // Administrador da empresa desta aba (o da instalação é admin em todas): certificado, configurações e tributações.
  const adminEmpresa = admin || usuario.papel_empresa === "admin";
  // Suspensa (teste sem aceite, cancelada, mensalidade não paga): a faixa da assinatura explica e oferece
  // "Continuar e assinar" no lugar do aviso comum.
  const testeEncerrado =
    assinatura.data?.disponivel && (assinatura.data.situacao === "teste_encerrado" || assinatura.data.situacao === "suspensa");

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
    if (adminEmpresa)
      return (
        <>
          <FaixaAssinatura />
          <Onboarding status={s} />
        </>
      );
    return (
      <div className="pagina pagina--estreita">
        <FaixaAssinatura />
        <div className="info-caixa">
          <span>
            {s.varias_empresas ? `A empresa ${nomeEmpresa(s)} ainda não foi configurada.` : "O sistema ainda não foi configurado."} Peça
            a um administrador para concluir a configuração inicial (certificado digital, dados da empresa e tributação padrão).{" "}
          </span>
        </div>
        <p style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <SeletorEmpresa admin={false} />
          <MenuUsuario usuario={usuario} urlAssinatura={sessao.url_assinatura} />
        </p>
        <LinkSuporte status={s} usuario={usuario} />
      </div>
    );
  }
  const rotas = ROTAS.filter((r) => !r.admin || (r.admin === "empresa" ? adminEmpresa : admin));
  const rotaVisivel = rota === "conta" || rotas.some((r) => r.id === rota) ? rota : "emitir";

  const cert = s.certificado;
  return (
    <>
      <FaixaAmbiente status={s} admin={adminEmpresa} />
      {sessao.suspenso && !testeEncerrado && (
        <div className="aviso faixa-suspenso" role="alert">
          <AvisoSuspenso sessao={sessao} />
        </div>
      )}
      <FaixaAssinatura />
      <header className="topo" ref={topoRef}>
        <a className="marca" href="#/emitir">
          <Logo />
          <span className="marca__nome">Emissor em Planilha</span>
        </a>
        <nav className="nav" aria-label="Seções">
          {rotas.map((r) => (
            <a key={r.id} href={`#/${r.id}`} aria-current={rotaVisivel === r.id ? "page" : undefined} data-rotulo={r.rotulo}>
              <IconeRota id={r.id} />
              <span className="nav__rotulo">{r.rotulo}</span>
            </a>
          ))}
        </nav>
        <div className="topo__info">
          <SeletorEmpresa admin={admin} />
          {s.prestador.cnpj && !s.varias_empresas && (
            <span>
              Prestador <strong className="num">{formatarDocumento(s.prestador.cnpj)}</strong>
            </span>
          )}
          {cert && !cert.erro && (
            <span title={`Certificado de ${cert.titular}${s.empresa.acesso === "procuracao" ? " (por procuração)" : ""}`}>
              Certificado até <strong>{new Date(cert.valido_ate).toLocaleDateString("pt-BR")}</strong>
            </span>
          )}
        </div>
        <BotaoSuporte status={s} usuario={usuario} />
        <Atualizacoes />
        <MenuUsuario usuario={usuario} urlAssinatura={sessao.url_assinatura} />
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
                {adminEmpresa ? <a href="#/configuracoes">Abrir configurações</a> : <span>Fale com um administrador.</span>}
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
        {!usuario.totp_ativo && !preferencias.aviso_2fa_dispensado && rotaVisivel !== "conta" && (
          <div style={{ padding: "12px 24px 0", maxWidth: 1480, margin: "0 auto" }}>
            <div className="info-caixa info-caixa--dispensavel">
              <span>
                Proteja o acesso ao certificado: <a href="#/conta">ative a verificação em duas etapas</a> com um aplicativo
                autenticador no celular.
              </span>
              <button
                type="button"
                className="btn-texto info-caixa__dispensar"
                title="O aviso some para você em qualquer navegador. A verificação continua disponível em Minha conta."
                onClick={() => salvarPreferencias({ aviso_2fa_dispensado: true }).catch(() => undefined)}
              >
                Não mostrar de novo
              </button>
            </div>
          </div>
        )}
        {rotaVisivel === "emitir" && (
          <Suspense
            fallback={
              <div className="carregando-tela" role="status">
                <Spinner /> Abrindo a tabela…
              </div>
            }
          >
            <Emitir status={s} admin={adminEmpresa} />
          </Suspense>
        )}
        {rotaVisivel === "emissoes" && <Emissoes admin={adminEmpresa} podeEmitir={s.pronto_para_emitir} />}
        {rotaVisivel === "clientes" && <Clientes />}
        {rotaVisivel === "configuracoes" && <Configuracoes status={s} adminInstancia={admin} />}
        {rotaVisivel === "usuarios" && <Usuarios meuId={usuario.id} />}
        {rotaVisivel === "conta" && <MinhaConta />}
      </main>
    </>
  );
}
