// Usuários (administrador): convidar, mudar papel, desativar, redefinir acesso; e o registro de auditoria.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, type Papel, type RegistroAuditoria, type Usuario } from "../api";
import { EmpresasDoUsuario, useEmpresas } from "../components/Empresas";
import { copiar, Dialogo, ErroCaixa, Spinner, useToast } from "../ui";

const PAPEL: Record<Papel, string> = { admin: "Administrador", operador: "Operador" };

const ACAO: Record<string, string> = {
  primeiro_acesso: "Criou o administrador",
  login: "Entrou",
  login_falhou: "Tentativa de login recusada",
  logout: "Saiu",
  usuario_bloqueado: "Usuário bloqueado por tentativas",
  senha_alterada: "Trocou a senha",
  senha_redefinida: "Definiu nova senha pelo link",
  convite_aceito: "Aceitou o convite",
  "2fa_ativada": "Ativou a verificação em duas etapas",
  "2fa_desativada": "Desativou a verificação em duas etapas",
  usuario_convidado: "Convidou usuário",
  usuario_alterado: "Alterou usuário",
  acesso_redefinido: "Redefiniu o acesso de usuário",
  certificado_enviado: "Enviou certificado digital",
  onboarding_concluido: "Concluiu a configuração inicial",
  configuracao_alterada: "Alterou configurações",
  lote_criado: "Emitiu lote",
  lote_interrompido: "Interrompeu lote",
  nota_cancelada: "Cancelou NFS-e",
  cancelamento_recusado: "Cancelamento de NFS-e não concluído",
  assinatura_confirmada: "Confirmou a assinatura",
  assinatura_recusada: "Confirmação da assinatura recusada pelo cartão",
  assinatura_falhou: "Confirmação da assinatura não concluída",
  assinatura_nao_continuar: "Disse que não vai continuar com a assinatura",
  atualizacao_pedida: "Pediu a atualização do sistema",
  atualizacao_automatica: "Alterou a atualização automática",
  empresa_criada: "Adicionou empresa",
  empresa_alterada: "Alterou empresa",
  empresa_certificado: "Trocou o certificado da empresa",
  empresa_desativada: "Desativou empresa",
  empresa_reativada: "Reativou empresa",
  empresa_descartada: "Desistiu de empresa nova",
  usuario_excluido: "Excluiu usuário",
  certificado_guardado: "Guardou certificado para nova empresa",
  certificado_apagado: "Apagou certificado sem uso",
  clientes_compartilhados: "Passou a compartilhar os clientes entre as empresas",
  clientes_separados: "Separou os clientes por empresa",
  usuario_empresas: "Alterou as empresas de usuário",
};

function dataHora(s: string | null) {
  return s ? new Date(s).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—";
}

export function Usuarios({ meuId }: { meuId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const lista = useQuery({ queryKey: ["usuarios"], queryFn: () => api.get<Usuario[]>("/api/usuarios") });
  const [convidando, setConvidando] = useState(false);
  const [link, setLink] = useState<{ email: string; link: string } | null>(null);
  const [redefinir, setRedefinir] = useState<Usuario | null>(null);
  const [empresasDe, setEmpresasDe] = useState<Usuario | null>(null);
  const [excluir, setExcluir] = useState<Usuario | null>(null);
  const [excluindo, setExcluindo] = useState(false);
  const [erro, setErro] = useState<unknown>(null);
  const empresas = useEmpresas();
  const varias = (empresas.data?.empresas.length ?? 0) > 1;
  const nomeEmpresa = (id: number) => empresas.data?.empresas.find((e) => e.id === id)?.nome ?? `Empresa ${id}`;

  async function alterar(u: Usuario, corpo: Partial<Pick<Usuario, "papel" | "ativo">>, msg: string) {
    setErro(null);
    try {
      await api.put(`/api/usuarios/${u.id}`, corpo);
      toast(msg);
      qc.invalidateQueries({ queryKey: ["usuarios"] });
      qc.invalidateQueries({ queryKey: ["auditoria"] });
    } catch (e) {
      setErro(e);
    }
  }

  async function confirmarExclusao(u: Usuario) {
    setErro(null);
    setExcluindo(true);
    try {
      await api.del(`/api/usuarios/${u.id}`);
      toast("Usuário excluído.");
      setExcluir(null);
      qc.invalidateQueries({ queryKey: ["usuarios"] });
      qc.invalidateQueries({ queryKey: ["auditoria"] });
    } catch (e) {
      setErro(e);
      setExcluir(null);
    } finally {
      setExcluindo(false);
    }
  }

  return (
    <div className="pagina">
      <div className="cabecalho-pagina">
        <div>
          <h1 className="titulo">Usuários</h1>
          <p className="subtitulo">
            Administradores cuidam do certificado, das configurações e dos usuários. Operadores editam a tabela, emitem e cancelam
            notas. Tudo fica registrado na auditoria abaixo.
          </p>
        </div>
        <div className="cabecalho-pagina__acoes">
          <button className="btn btn--primario" onClick={() => setConvidando(true)}>Convidar usuário</button>
        </div>
      </div>
      <ErroCaixa erro={lista.error ?? erro} />
      <div className="tabela-caixa">
        <table className="tabela">
          <thead>
            <tr>
              <th>Nome</th>
              <th>E-mail</th>
              <th>Papel</th>
              <th>Situação</th>
              <th>Último acesso</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(lista.data ?? []).map((u) => (
              <tr key={u.id} style={u.ativo ? undefined : { opacity: 0.55 }}>
                <td>{u.nome || "—"}{u.id === meuId && " (você)"}</td>
                <td>{u.email}</td>
                <td>
                  <select
                    className="input"
                    value={u.papel}
                    onChange={(e) => alterar(u, { papel: e.target.value as Papel }, "Papel alterado.")}
                    aria-label={`Papel de ${u.email}`}
                  >
                    <option value="admin">{PAPEL.admin}</option>
                    <option value="operador">{PAPEL.operador}</option>
                  </select>
                  {varias && (
                    <div className="usuario-empresas">
                      {u.papel === "admin" ? (
                        "Todas as empresas"
                      ) : (
                        <>
                          {u.empresas.length === 0
                            ? "Nenhuma empresa"
                            : u.empresas.map((e) => `${nomeEmpresa(e.empresa_id)}${e.papel === "admin" ? " (admin)" : ""}`).join(", ")}{" "}
                          <button className="btn-texto" onClick={() => setEmpresasDe(u)}>Alterar</button>
                        </>
                      )}
                    </div>
                  )}
                </td>
                <td>
                  {!u.ativo ? "Desativado" : u.convite_pendente ? "Convite pendente" : u.bloqueado ? "Bloqueado (tentativas)" : "Ativo"}
                  {u.totp_ativo && " · 2 etapas"}
                </td>
                <td>{dataHora(u.ultimo_login)}</td>
                <td style={{ whiteSpace: "nowrap", textAlign: "right" }}>
                  <button className="btn btn--pequeno" onClick={() => setRedefinir(u)} disabled={!u.ativo}>
                    {u.convite_pendente ? "Novo convite" : "Redefinir acesso"}
                  </button>{" "}
                  {u.id !== meuId && (
                    <button
                      className="btn btn--pequeno btn--fantasma"
                      onClick={() => alterar(u, { ativo: !u.ativo }, u.ativo ? "Usuário desativado." : "Usuário reativado.")}
                    >
                      {u.ativo ? "Desativar" : "Reativar"}
                    </button>
                  )}
                  {!u.ativo && u.id !== meuId && (
                    <button className="btn btn--pequeno btn--fantasma btn--perigo" onClick={() => setExcluir(u)}>
                      Excluir
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Auditoria />

      {excluir && (
        <Dialogo
          titulo="Excluir usuário?"
          fechar={excluindo ? undefined : () => setExcluir(null)}
          rodape={
            <>
              <button className="btn" onClick={() => setExcluir(null)} disabled={excluindo}>Cancelar</button>
              <button className="btn btn--perigo" onClick={() => confirmarExclusao(excluir)} disabled={excluindo}>
                {excluindo && <Spinner />} Excluir
              </button>
            </>
          }
        >
          <p>
            {excluir.nome || excluir.email} sai da lista de usuários de vez. As ações dele continuam na auditoria, com o e-mail.
            Para voltar a dar acesso, será preciso convidar de novo.
          </p>
        </Dialogo>
      )}

      {convidando && (
        <Convidar
          fechar={() => setConvidando(false)}
          criado={(email, l) => {
            setConvidando(false);
            setLink({ email, link: l });
            qc.invalidateQueries({ queryKey: ["usuarios"] });
          }}
        />
      )}
      {empresasDe && <EmpresasDoUsuario usuario={empresasDe} fechar={() => setEmpresasDe(null)} />}
      {redefinir && (
        <Redefinir
          usuario={redefinir}
          fechar={() => setRedefinir(null)}
          feito={(l) => {
            setLink({ email: redefinir.email, link: l });
            setRedefinir(null);
            qc.invalidateQueries({ queryKey: ["usuarios"] });
          }}
        />
      )}
      {link && <LinkGerado {...link} fechar={() => setLink(null)} />}
    </div>
  );
}

function Convidar({ fechar, criado }: { fechar: () => void; criado: (email: string, link: string) => void }) {
  const [email, setEmail] = useState("");
  const [nome, setNome] = useState("");
  const [papel, setPapel] = useState<Papel>("operador");
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);
  async function enviar(e: FormEvent) {
    e.preventDefault();
    setErro(null);
    setOcupado(true);
    try {
      const r = await api.post<{ usuario: Usuario; link: string }>("/api/usuarios", { email, nome, papel });
      criado(r.usuario.email, r.link);
    } catch (err) {
      setErro(err);
    } finally {
      setOcupado(false);
    }
  }
  return (
    <Dialogo titulo="Convidar usuário" fechar={fechar}>
      <form className="acesso__form" onSubmit={enviar}>
        <label className="campo">
          <span>E-mail (será o login)</span>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
        </label>
        <label className="campo">
          <span>Nome</span>
          <input value={nome} onChange={(e) => setNome(e.target.value)} />
        </label>
        <label className="campo">
          <span>Papel</span>
          <select value={papel} onChange={(e) => setPapel(e.target.value as Papel)}>
            <option value="operador">Operador: tabela, emissão e cancelamento</option>
            <option value="admin">Administrador: tudo, inclusive certificado e usuários</option>
          </select>
        </label>
        <ErroCaixa erro={erro} />
        <div><button className="btn btn--primario" disabled={ocupado}>{ocupado && <Spinner />} Gerar convite</button></div>
      </form>
    </Dialogo>
  );
}

function Redefinir({ usuario, fechar, feito }: { usuario: Usuario; fechar: () => void; feito: (link: string) => void }) {
  const [remover2fa, setRemover2fa] = useState(false);
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);
  async function confirmar() {
    setErro(null);
    setOcupado(true);
    try {
      const r = await api.post<{ link: string }>(`/api/usuarios/${usuario.id}/redefinir`, { remover_2fa: remover2fa });
      feito(r.link);
    } catch (err) {
      setErro(err);
    } finally {
      setOcupado(false);
    }
  }
  return (
    <Dialogo
      titulo={usuario.convite_pendente ? "Gerar novo convite" : "Redefinir acesso"}
      fechar={fechar}
      rodape={
        <>
          <button className="btn" onClick={fechar}>Voltar</button>
          <button className="btn btn--primario" onClick={confirmar} disabled={ocupado}>{ocupado && <Spinner />} Gerar link</button>
        </>
      }
    >
      <p style={{ marginTop: 0 }}>
        {usuario.convite_pendente
          ? `O convite anterior de ${usuario.email} deixa de valer.`
          : `A senha atual de ${usuario.email} deixa de valer e os navegadores conectados saem. A pessoa define uma nova senha pelo link.`}
      </p>
      {usuario.totp_ativo && (
        <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input type="checkbox" checked={remover2fa} onChange={(e) => setRemover2fa(e.target.checked)} />
          Desligar também a verificação em duas etapas (perdeu o celular)
        </label>
      )}
      <ErroCaixa erro={erro} />
    </Dialogo>
  );
}

function LinkGerado({ email, link, fechar }: { email: string; link: string; fechar: () => void }) {
  const toast = useToast();
  const completo = location.origin + link;
  return (
    <Dialogo
      titulo="Envie este link"
      fechar={fechar}
      rodape={
        <>
          <button className="btn" onClick={fechar}>Fechar</button>
          <button className="btn btn--primario" onClick={() => copiar(completo).then(() => toast("Link copiado."))}>Copiar link</button>
        </>
      }
    >
      <p style={{ marginTop: 0 }}>
        Mande para <strong>{email}</strong> (e-mail, WhatsApp…). O link vale por 7 dias e só pode ser usado uma vez; ele não será
        mostrado de novo.
      </p>
      <input className="input num" readOnly value={completo} onFocus={(e) => e.target.select()} style={{ width: "100%" }} />
    </Dialogo>
  );
}

function Auditoria() {
  const [pagina, setPagina] = useState(1);
  const q = useQuery({
    queryKey: ["auditoria", pagina],
    queryFn: () => api.get<{ total: number; por_pagina: number; itens: RegistroAuditoria[] }>(`/api/auditoria?pagina=${pagina}`),
  });
  const paginas = q.data ? Math.max(1, Math.ceil(q.data.total / q.data.por_pagina)) : 1;
  return (
    <section style={{ marginTop: 28 }}>
      <h2 className="secao-titulo">Auditoria</h2>
      <ErroCaixa erro={q.error} />
      <div className="tabela-caixa">
        <table className="tabela">
          <thead>
            <tr>
              <th>Quando</th>
              <th>Usuário</th>
              <th>Ação</th>
              <th>Detalhe</th>
              <th>IP</th>
            </tr>
          </thead>
          <tbody>
            {(q.data?.itens ?? []).map((a, i) => (
              <tr key={i}>
                <td className="num" style={{ whiteSpace: "nowrap" }}>{dataHora(a.quando)}</td>
                <td>{a.usuario ?? "—"}</td>
                <td>{ACAO[a.acao] ?? a.acao}</td>
                <td>{a.detalhe ?? ""}</td>
                <td className="num">{a.ip ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {paginas > 1 && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 10 }}>
          <button className="btn btn--pequeno" disabled={pagina <= 1} onClick={() => setPagina(pagina - 1)}>Anteriores</button>
          <span>Página {pagina} de {paginas}</span>
          <button className="btn btn--pequeno" disabled={pagina >= paginas} onClick={() => setPagina(pagina + 1)}>Seguintes</button>
        </div>
      )}
    </section>
  );
}
