// Minha conta: trocar a senha e ligar/desligar a verificação em duas etapas (aplicativo autenticador).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, type UsuarioSessao } from "../api";
import { copiar, Dialogo, ErroCaixa, Spinner, useToast } from "../ui";
import { SENHA_MIN } from "./Acesso";

type Conta = UsuarioSessao & { codigos_recuperacao_restantes: number };

export function MinhaConta() {
  const conta = useQuery({ queryKey: ["conta"], queryFn: () => api.get<Conta>("/api/auth/conta") });
  return (
    <div className="pagina pagina--estreita">
      <div className="cabecalho-pagina">
        <div>
          <h1 className="titulo">Minha conta</h1>
          {conta.data && (
            <p className="subtitulo">
              {conta.data.nome || conta.data.email} · {conta.data.email} · {conta.data.papel === "admin" ? "Administrador" : "Operador"}
            </p>
          )}
        </div>
      </div>
      <ErroCaixa erro={conta.error} />
      {conta.data && (
        <>
          <DoisFatores conta={conta.data} />
          <TrocarSenha />
        </>
      )}
    </div>
  );
}

function TrocarSenha() {
  const toast = useToast();
  const [atual, setAtual] = useState("");
  const [nova, setNova] = useState("");
  const [confirma, setConfirma] = useState("");
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);
  async function enviar(e: FormEvent) {
    e.preventDefault();
    if (nova !== confirma) return;
    setErro(null);
    setOcupado(true);
    try {
      const r = await api.post<{ mensagem: string }>("/api/auth/senha", { senha_atual: atual, nova_senha: nova });
      toast(r.mensagem);
      setAtual("");
      setNova("");
      setConfirma("");
    } catch (err) {
      setErro(err);
    } finally {
      setOcupado(false);
    }
  }
  return (
    <section className="folha">
      <h2 className="secao-titulo">Senha</h2>
      <form className="acesso__form" onSubmit={enviar}>
        <label className="campo">
          <span>Senha atual</span>
          <input type="password" autoComplete="current-password" value={atual} onChange={(e) => setAtual(e.target.value)} required />
        </label>
        <label className="campo">
          <span>Nova senha</span>
          <input type="password" autoComplete="new-password" value={nova} onChange={(e) => setNova(e.target.value)} required minLength={SENHA_MIN} />
          <small>Pelo menos {SENHA_MIN} caracteres. Os outros navegadores conectados serão desconectados.</small>
        </label>
        <label className={`campo ${confirma && confirma !== nova ? "campo--erro" : ""}`}>
          <span>Repita a nova senha</span>
          <input type="password" autoComplete="new-password" value={confirma} onChange={(e) => setConfirma(e.target.value)} required />
        </label>
        <ErroCaixa erro={erro} />
        <div>
          <button className="btn btn--primario" disabled={ocupado || nova !== confirma}>
            {ocupado && <Spinner />} Trocar senha
          </button>
        </div>
      </form>
    </section>
  );
}

function DoisFatores({ conta }: { conta: Conta }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [etapa, setEtapa] = useState<null | "senha" | "qr" | "codigos" | "desligar">(null);
  const [senha, setSenha] = useState("");
  const [codigo, setCodigo] = useState("");
  const [qr, setQr] = useState<{ segredo: string; qr: string } | null>(null);
  const [codigos, setCodigos] = useState<string[]>([]);
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);

  const fechar = () => {
    setEtapa(null);
    setSenha("");
    setCodigo("");
    setErro(null);
    setQr(null);
  };
  async function passo(f: () => Promise<void>) {
    setErro(null);
    setOcupado(true);
    try {
      await f();
    } catch (err) {
      setErro(err);
    } finally {
      setOcupado(false);
    }
  }

  return (
    <section className="folha">
      <h2 className="secao-titulo">Verificação em duas etapas</h2>
      {conta.totp_ativo ? (
        <>
          <p>
            <strong>Ativa.</strong> Ao entrar, além da senha, é pedido o código do aplicativo autenticador. Restam{" "}
            {conta.codigos_recuperacao_restantes} código(s) de recuperação.
          </p>
          <button className="btn btn--perigo" onClick={() => setEtapa("desligar")}>Desativar</button>
        </>
      ) : (
        <>
          <div className="aviso" style={{ marginBottom: 12 }}>
            <span>
              Recomendado: com a senha, qualquer pessoa emitiria e cancelaria notas com o certificado da empresa. Com a
              verificação em duas etapas, também é preciso o celular.
            </span>
          </div>
          <button className="btn btn--primario" onClick={() => setEtapa("senha")}>Ativar com aplicativo autenticador</button>
        </>
      )}

      {etapa === "senha" && (
        <Dialogo titulo="Ativar verificação em duas etapas" fechar={fechar}>
          <form
            className="acesso__form"
            onSubmit={(e) => {
              e.preventDefault();
              passo(async () => {
                setQr(await api.post<{ segredo: string; qr: string }>("/api/auth/2fa/iniciar", { senha }));
                setEtapa("qr");
              });
            }}
          >
            <label className="campo">
              <span>Confirme sua senha</span>
              <input type="password" autoComplete="current-password" value={senha} onChange={(e) => setSenha(e.target.value)} required autoFocus />
            </label>
            <ErroCaixa erro={erro} />
            <div><button className="btn btn--primario" disabled={ocupado}>{ocupado && <Spinner />} Continuar</button></div>
          </form>
        </Dialogo>
      )}

      {etapa === "qr" && qr && (
        <Dialogo titulo="Ler o QR Code no celular" fechar={fechar}>
          <form
            className="acesso__form"
            onSubmit={(e) => {
              e.preventDefault();
              passo(async () => {
                const r = await api.post<{ codigos_recuperacao: string[] }>("/api/auth/2fa/ativar", { codigo });
                setCodigos(r.codigos_recuperacao);
                setEtapa("codigos");
                qc.invalidateQueries({ queryKey: ["conta"] });
                qc.invalidateQueries({ queryKey: ["sessao"] });
              });
            }}
          >
            <p style={{ margin: 0 }}>
              Abra um aplicativo autenticador (Google Authenticator, Microsoft Authenticator, 2FAS, Aegis…), toque em adicionar
              conta e leia o código abaixo.
            </p>
            <img className="qr-2fa" src={qr.qr} alt="QR Code para o aplicativo autenticador" />
            <p style={{ margin: 0, fontSize: 13 }}>
              Sem câmera? Digite a chave: <code className="num" style={{ wordBreak: "break-all" }}>{qr.segredo}</code>
            </p>
            <label className="campo">
              <span>Código de 6 dígitos que apareceu no aplicativo</span>
              <input className="num" inputMode="numeric" autoComplete="one-time-code" value={codigo} onChange={(e) => setCodigo(e.target.value)} required autoFocus />
            </label>
            <ErroCaixa erro={erro} />
            <div><button className="btn btn--primario" disabled={ocupado}>{ocupado && <Spinner />} Ativar</button></div>
          </form>
        </Dialogo>
      )}

      {etapa === "codigos" && (
        <Dialogo
          titulo="Guarde os códigos de recuperação"
          rodape={
            <>
              <button className="btn" onClick={() => copiar(codigos.join("\n")).then(() => toast("Códigos copiados."))}>Copiar</button>
              <button className="btn btn--primario" onClick={() => { fechar(); toast("Verificação em duas etapas ativada."); }}>Já guardei</button>
            </>
          }
        >
          <p style={{ marginTop: 0 }}>
            Se perder o celular, cada código abaixo substitui o do aplicativo <strong>uma vez</strong>. Eles não serão mostrados de
            novo: guarde num lugar seguro (gerenciador de senhas ou papel).
          </p>
          <ul className="codigos-recuperacao num">
            {codigos.map((c) => <li key={c}>{c}</li>)}
          </ul>
        </Dialogo>
      )}

      {etapa === "desligar" && (
        <Dialogo titulo="Desativar verificação em duas etapas" fechar={fechar}>
          <form
            className="acesso__form"
            onSubmit={(e) => {
              e.preventDefault();
              passo(async () => {
                await api.post("/api/auth/2fa/desativar", { senha, codigo });
                fechar();
                toast("Verificação em duas etapas desativada.");
                qc.invalidateQueries({ queryKey: ["conta"] });
                qc.invalidateQueries({ queryKey: ["sessao"] });
              });
            }}
          >
            <label className="campo">
              <span>Senha</span>
              <input type="password" autoComplete="current-password" value={senha} onChange={(e) => setSenha(e.target.value)} required autoFocus />
            </label>
            <label className="campo">
              <span>Código do aplicativo (ou de recuperação)</span>
              <input className="num" autoComplete="one-time-code" value={codigo} onChange={(e) => setCodigo(e.target.value)} required />
            </label>
            <ErroCaixa erro={erro} />
            <div><button className="btn btn--perigo" disabled={ocupado}>{ocupado && <Spinner />} Desativar</button></div>
          </form>
        </Dialogo>
      )}
    </section>
  );
}
