// Telas de acesso: login (com 2FA), primeiro acesso (cria o administrador) e convite/redefinição de senha.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api, ApiError, type Sessao } from "../api";
import { ErroCaixa, Spinner } from "../ui";

export const SENHA_MIN = 10;

function Moldura({ titulo, subtitulo, children }: { titulo: string; subtitulo?: ReactNode; children: ReactNode }) {
  return (
    <div className="acesso">
      <div className="acesso__cartao">
        <div className="marca acesso__marca">
          <img src="/logo.svg" alt="" />
          <span>Emissor em Planilha</span>
        </div>
        <h1 className="titulo">{titulo}</h1>
        {subtitulo && <p className="subtitulo">{subtitulo}</p>}
        {children}
      </div>
    </div>
  );
}

/** Depois de entrar: recarrega a sessão e todos os dados. */
function useAoEntrar() {
  const qc = useQueryClient();
  return async () => {
    await qc.resetQueries();
  };
}

export function tokenDaUrl(): string {
  return new URLSearchParams(location.hash.split("?")[1] || "").get("token") || "";
}

/** Tira o token do endereço (e do histórico) e volta à tela principal. */
function limparHash() {
  history.replaceState(null, "", location.pathname + location.search + "#/emitir");
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

export function TelaLogin({ sessao }: { sessao: Sessao }) {
  const aoEntrar = useAoEntrar();
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [codigo, setCodigo] = useState("");
  const [pedeCodigo, setPedeCodigo] = useState(false);
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setErro(null);
    setOcupado(true);
    try {
      await api.post("/api/auth/entrar", { email, senha, codigo: pedeCodigo ? codigo : null });
      await aoEntrar();
    } catch (err) {
      if (err instanceof ApiError && err.corpo?.precisa_codigo) {
        // Primeira resposta com senha certa só pede o código: não é erro.
        if (pedeCodigo) setErro(err);
        setPedeCodigo(true);
        setCodigo("");
      } else setErro(err);
    } finally {
      setOcupado(false);
    }
  }

  return (
    <Moldura titulo="Entrar" subtitulo={sessao.suspenso ? <AvisoSuspenso sessao={sessao} /> : undefined}>
      <form className="acesso__form" onSubmit={enviar}>
        <label className="campo">
          <span>E-mail</span>
          <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus={!pedeCodigo} disabled={pedeCodigo} />
        </label>
        <label className="campo">
          <span>Senha</span>
          <input type="password" autoComplete="current-password" value={senha} onChange={(e) => setSenha(e.target.value)} required disabled={pedeCodigo} />
        </label>
        {pedeCodigo && (
          <label className="campo">
            <span>Código do aplicativo autenticador</span>
            <input
              className="num"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={codigo}
              onChange={(e) => setCodigo(e.target.value)}
              required
              autoFocus
            />
            <small>Os 6 dígitos que aparecem no aplicativo agora. Sem o celular, use um dos códigos de recuperação.</small>
          </label>
        )}
        <ErroCaixa erro={erro} />
        <button className="btn btn--primario btn--grande" disabled={ocupado}>
          {ocupado && <Spinner />} {pedeCodigo ? "Confirmar" : "Entrar"}
        </button>
        {pedeCodigo && (
          <button type="button" className="btn btn--fantasma" onClick={() => { setPedeCodigo(false); setErro(null); }}>
            Voltar
          </button>
        )}
        <p className="acesso__dica">Esqueceu a senha? Peça a um administrador para redefinir o seu acesso.</p>
      </form>
    </Moldura>
  );
}

function CamposSenha(props: { senha: string; setSenha: (s: string) => void; confirma: string; setConfirma: (s: string) => void }) {
  const curta = props.senha.length > 0 && props.senha.length < SENHA_MIN;
  const diferente = props.confirma.length > 0 && props.confirma !== props.senha;
  return (
    <>
      <label className={`campo ${curta ? "campo--erro" : ""}`}>
        <span>Senha</span>
        <input type="password" autoComplete="new-password" value={props.senha} onChange={(e) => props.setSenha(e.target.value)} required minLength={SENHA_MIN} />
        <small>Pelo menos {SENHA_MIN} caracteres. Uma frase fácil de lembrar funciona bem.</small>
      </label>
      <label className={`campo ${diferente ? "campo--erro" : ""}`}>
        <span>Repita a senha</span>
        <input type="password" autoComplete="new-password" value={props.confirma} onChange={(e) => props.setConfirma(e.target.value)} required />
        {diferente && <span className="campo__erro">As senhas não conferem.</span>}
      </label>
    </>
  );
}

export function TelaPrimeiroAcesso({ sessao }: { sessao: Sessao }) {
  const aoEntrar = useAoEntrar();
  const [token, setToken] = useState(tokenDaUrl());
  const [nome, setNome] = useState("");
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [confirma, setConfirma] = useState("");
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);
  const pedeToken = !sessao.primeiro_acesso_sem_codigo && !tokenDaUrl();

  async function enviar(e: FormEvent) {
    e.preventDefault();
    if (senha !== confirma) return;
    setErro(null);
    setOcupado(true);
    try {
      await api.post("/api/auth/primeiro-acesso", { token: token || null, nome, email, senha });
      await aoEntrar();
      limparHash();
    } catch (err) {
      setErro(err);
    } finally {
      setOcupado(false);
    }
  }

  return (
    <Moldura
      titulo="Criar o administrador"
      subtitulo="Primeiro acesso: este usuário cuida do certificado, das configurações e dos demais usuários."
    >
      <form className="acesso__form" onSubmit={enviar}>
        {pedeToken && (
          <label className="campo">
            <span>Código de primeiro acesso</span>
            <input className="num" value={token} onChange={(e) => setToken(e.target.value.trim())} required autoComplete="off" />
            <small>
              Está no arquivo <code>data/primeiro-acesso.txt</code> do servidor e na janela onde o Emissor foi iniciado. Abrindo
              o sistema no próprio servidor (endereço 127.0.0.1), o código não é pedido.
            </small>
          </label>
        )}
        <label className="campo">
          <span>Seu nome</span>
          <input autoComplete="name" value={nome} onChange={(e) => setNome(e.target.value)} />
        </label>
        <label className="campo">
          <span>E-mail (é o seu login)</span>
          <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <CamposSenha senha={senha} setSenha={setSenha} confirma={confirma} setConfirma={setConfirma} />
        <ErroCaixa erro={erro} />
        <button className="btn btn--primario btn--grande" disabled={ocupado || senha !== confirma}>
          {ocupado && <Spinner />} Criar administrador e entrar
        </button>
      </form>
    </Moldura>
  );
}

export function TelaConvite() {
  const aoEntrar = useAoEntrar();
  const [token] = useState(tokenDaUrl);
  const [info, setInfo] = useState<{ email: string; nome: string; finalidade: string } | null>(null);
  const [erroInfo, setErroInfo] = useState<unknown>(null);
  const [nome, setNome] = useState("");
  const [senha, setSenha] = useState("");
  const [confirma, setConfirma] = useState("");
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);

  useEffect(() => {
    api
      .post<{ email: string; nome: string; finalidade: string }>("/api/auth/convite/ver", { token })
      .then((i) => {
        setInfo(i);
        setNome(i.nome);
      })
      .catch(setErroInfo);
  }, [token]);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    if (senha !== confirma) return;
    setErro(null);
    setOcupado(true);
    try {
      await api.post("/api/auth/convite", { token, senha, nome });
      await aoEntrar();
      limparHash();
    } catch (err) {
      setErro(err);
    } finally {
      setOcupado(false);
    }
  }

  if (erroInfo)
    return (
      <Moldura titulo="Link inválido">
        <ErroCaixa erro={erroInfo} />
        <p style={{ marginTop: 16 }}>
          <a href="#/emitir" onClick={() => setTimeout(() => location.reload(), 0)}>Ir para o login</a>
        </p>
      </Moldura>
    );
  if (!info) return <div className="carregando">Carregando…</div>;
  const redefinicao = info.finalidade === "redefinicao";
  return (
    <Moldura
      titulo={redefinicao ? "Definir nova senha" : "Criar sua senha"}
      subtitulo={
        <>
          Acesso de <strong>{info.email}</strong>. {redefinicao ? "A senha anterior não vale mais." : "Este link só pode ser usado uma vez."}
        </>
      }
    >
      <form className="acesso__form" onSubmit={enviar}>
        {!redefinicao && (
          <label className="campo">
            <span>Seu nome</span>
            <input autoComplete="name" value={nome} onChange={(e) => setNome(e.target.value)} />
          </label>
        )}
        <input type="email" autoComplete="username" value={info.email} readOnly hidden />
        <CamposSenha senha={senha} setSenha={setSenha} confirma={confirma} setConfirma={setConfirma} />
        <ErroCaixa erro={erro} />
        <button className="btn btn--primario btn--grande" disabled={ocupado || senha !== confirma}>
          {ocupado && <Spinner />} Salvar senha e entrar
        </button>
      </form>
    </Moldura>
  );
}

export function AvisoSuspenso({ sessao }: { sessao: Sessao }) {
  return (
    <span>
      A assinatura está suspensa. Dá para entrar, consultar e baixar as notas já emitidas, mas não emitir nem cancelar.
      {sessao.url_assinatura && (
        <>
          {" "}
          <a href={sessao.url_assinatura} target="_blank" rel="noreferrer">
            Regularizar a assinatura
          </a>
          .
        </>
      )}
    </span>
  );
}
