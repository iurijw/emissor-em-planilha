import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { copiar, Dialogo, ErroCaixa, Spinner, useToast } from "../ui";
import { useTributacoes } from "./Abas";

type Opcao = { id: string; descricao: string };
type TokenApi = {
  id: number;
  nome: string;
  prefixo: string;
  escopos: string[];
  ambiente: "producao" | "homologacao";
  criado_em: string | null;
  ultimo_uso: string | null;
  ativo: boolean;
};
type WebhookApi = {
  id: number;
  url: string;
  eventos: string[];
  ambiente: "producao" | "homologacao";
  descricao: string;
  ativo: boolean;
  falhas_seguidas: number;
};
type Entrega = {
  id: string;
  evento: string;
  status: "pendente" | "entregue" | "falhou";
  tentativas: number;
  ultimo_status: number | null;
  ultimo_erro: string | null;
  criado_em: string | null;
  proxima_em: string | null;
};
type Conector = {
  id: number;
  tipo: "asaas";
  nome: string;
  ambiente: "producao" | "homologacao";
  ambiente_origem: "producao" | "sandbox";
  caminho: string;
  gatilho: "recebido" | "confirmado";
  tributacao_id: number | null;
  tributacao_nome: string | null;
  descricao: string;
  ativo: boolean;
  ultimo_evento_em: string | null;
  eventos_com_erro: number;
};
type AvisoEntrada = {
  id: string;
  evento: string;
  pagamento_id: string | null;
  valor: string | null;
  status: "pendente" | "emitida" | "repetida" | "ignorado" | "erro";
  mensagem: string | null;
  emissao_id: number | null;
  recebido_em: string | null;
};
type FormConector = {
  id?: number;
  nome: string;
  chave: string;
  ambiente: string;
  ambiente_origem: string;
  gatilho: string;
  tributacao_id: number | null;
  descricao: string;
};
type Integracoes = {
  ambiente_atual: "producao" | "homologacao";
  tokens: TokenApi[];
  webhooks: WebhookApi[];
  conectores: Conector[];
  gatilhos: Opcao[];
  escopos: Opcao[];
  eventos: Opcao[];
  limites: { por_minuto: number; notas_por_dia: number };
};

const AMBIENTE = { producao: "Produção", homologacao: "Homologação" } as const;
const quando = (v: string | null) => (v ? new Date(v).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "nunca");

/** Segredo mostrado uma vez só (token ou segredo do webhook). */
function Segredo({ titulo, valor, texto, fechar }: { titulo: string; valor: string; texto: string; fechar: () => void }) {
  const toast = useToast();
  return (
    <Dialogo
      titulo={titulo}
      fechar={fechar}
      rodape={
        <>
          <button className="btn" onClick={async () => { await copiar(valor); toast("Copiado."); }}>Copiar</button>
          <button className="btn btn--primario" onClick={fechar}>Já guardei</button>
        </>
      }
    >
      <p style={{ marginTop: 0 }}>{texto}</p>
      <code className="num segredo-api">{valor}</code>
      <p style={{ color: "var(--grafite)", marginBottom: 0 }}>Ele não aparece de novo. Se perder, revogue e crie outro.</p>
    </Dialogo>
  );
}

export function SecaoIntegracoes() {
  const qc = useQueryClient();
  const toast = useToast();
  const dados = useQuery({ queryKey: ["integracoes"], queryFn: () => api.get<Integracoes>("/api/integracoes") });
  const [novoToken, setNovoToken] = useState<{ nome: string; escopos: string[]; ambiente: string } | null>(null);
  const [novoWebhook, setNovoWebhook] = useState<{ url: string; eventos: string[]; ambiente: string; descricao: string } | null>(null);
  const [segredo, setSegredo] = useState<{ titulo: string; valor: string; texto: string } | null>(null);
  const [revogar, setRevogar] = useState<TokenApi | null>(null);
  const [entregas, setEntregas] = useState<WebhookApi | null>(null);
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);
  const d = dados.data;

  async function executar(fn: () => Promise<void>) {
    setErro(null);
    setOcupado(true);
    try {
      await fn();
      await qc.invalidateQueries({ queryKey: ["integracoes"] });
    } catch (e) {
      setErro(e);
    } finally {
      setOcupado(false);
    }
  }

  const alternar = (lista: string[], item: string) => (lista.includes(item) ? lista.filter((x) => x !== item) : [...lista, item]);

  return (
    <section className="folha" id="integracoes">
      <h2 className="secao-titulo">Integrações (API)</h2>
      <p style={{ marginTop: 0, color: "var(--grafite)" }}>
        Outros sistemas (cobrança, ERP, loja) podem emitir, consultar e cancelar notas desta empresa com um token, e receber um
        aviso (webhook) quando a nota sai. A documentação para quem vai integrar fica em{" "}
        <a href="/api/v1/docs" target="_blank" rel="noreferrer">/api/v1/docs</a>.
      </p>
      {location.protocol === "http:" && !["localhost", "127.0.0.1"].includes(location.hostname) && (
        <div className="aviso" style={{ marginBottom: 12 }}>
          Este endereço não usa HTTPS: na rede local o token passa sem criptografia. Use-o só entre máquinas do escritório.
        </div>
      )}
      <ErroCaixa erro={dados.error} titulo="Não foi possível carregar as integrações" />
      {d && (
        <>
          <h3 className="integracoes__titulo">Tokens</h3>
          {d.tokens.length > 0 && (
            <div className="tabela-caixa" style={{ marginBottom: 12 }}>
              <table className="tabela">
                <thead>
                  <tr><th>Nome</th><th>Ambiente</th><th>Permissões</th><th>Último uso</th><th /></tr>
                </thead>
                <tbody>
                  {d.tokens.map((t) => (
                    <tr key={t.id} className={t.ativo ? undefined : "linha--apagada"}>
                      <td><b>{t.nome}</b><div className="num" style={{ color: "var(--grafite)" }}>{t.prefixo}</div></td>
                      <td>{AMBIENTE[t.ambiente]}</td>
                      <td>{t.escopos.join(", ")}</td>
                      <td>{quando(t.ultimo_uso)}</td>
                      <td className="dir">
                        {t.ativo ? (
                          <button className="btn btn--pequeno btn--fantasma" onClick={() => setRevogar(t)}>Revogar</button>
                        ) : (
                          <span style={{ color: "var(--grafite)" }}>revogado</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <button
            className="btn"
            onClick={() => setNovoToken({ nome: "", escopos: ["notas:ler", "notas:emitir"], ambiente: d.ambiente_atual })}
          >
            Novo token
          </button>
          <p style={{ color: "var(--grafite)", fontSize: "0.8125rem" }}>
            Até {d.limites.por_minuto} requisições por minuto e {d.limites.notas_por_dia} notas por dia por token.
          </p>

          <h3 className="integracoes__titulo">Webhooks</h3>
          {d.webhooks.length > 0 && (
            <div className="tabela-caixa" style={{ marginBottom: 12 }}>
              <table className="tabela">
                <thead>
                  <tr><th>Endereço</th><th>Ambiente</th><th>Eventos</th><th /></tr>
                </thead>
                <tbody>
                  {d.webhooks.map((w) => (
                    <tr key={w.id} className={w.ativo ? undefined : "linha--apagada"}>
                      <td style={{ overflowWrap: "anywhere" }}>
                        <span className="num">{w.url}</span>
                        {w.descricao && <div style={{ color: "var(--grafite)" }}>{w.descricao}</div>}
                        {w.falhas_seguidas > 0 && <div style={{ color: "var(--erro)" }}>{w.falhas_seguidas} falha(s) seguidas</div>}
                      </td>
                      <td>{AMBIENTE[w.ambiente]}</td>
                      <td>{w.eventos.join(", ")}</td>
                      <td className="dir" style={{ whiteSpace: "nowrap" }}>
                        <button
                          className="btn btn--pequeno"
                          disabled={ocupado}
                          onClick={() =>
                            executar(async () => {
                              const r = await api.post<{ ok: boolean; status: number | null; erro: string | null }>(
                                `/api/integracoes/webhooks/${w.id}/testar`,
                              );
                              toast(r.ok ? `Teste entregue (HTTP ${r.status}).` : `Teste não entregue: ${r.erro}`, r.ok ? undefined : "erro");
                            })
                          }
                        >
                          Testar
                        </button>{" "}
                        <button className="btn btn--pequeno" onClick={() => setEntregas(w)}>Entregas</button>{" "}
                        <button
                          className="btn btn--pequeno btn--fantasma"
                          disabled={ocupado}
                          onClick={() => executar(async () => { await api.put(`/api/integracoes/webhooks/${w.id}`, { ativo: !w.ativo }); })}
                        >
                          {w.ativo ? "Pausar" : "Retomar"}
                        </button>{" "}
                        <button
                          className="btn btn--pequeno btn--fantasma"
                          disabled={ocupado}
                          onClick={() => executar(async () => { await api.del(`/api/integracoes/webhooks/${w.id}`); toast("Webhook removido."); })}
                        >
                          Remover
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <button
            className="btn"
            onClick={() => setNovoWebhook({ url: "", eventos: ["nota.autorizada", "nota.rejeitada"], ambiente: d.ambiente_atual, descricao: "" })}
          >
            Novo webhook
          </button>
          <ReceberCobrancas d={d} />
          {!novoToken && !novoWebhook && !revogar && <div style={{ marginTop: 12 }}><ErroCaixa erro={erro} /></div>}
        </>
      )}

      {novoToken && d && (
        <Dialogo
          titulo="Novo token da API"
          fechar={() => setNovoToken(null)}
          rodape={
            <>
              <button className="btn" onClick={() => setNovoToken(null)}>Cancelar</button>
              <button
                className="btn btn--primario"
                disabled={ocupado || !novoToken.nome.trim() || novoToken.escopos.length === 0}
                onClick={() =>
                  executar(async () => {
                    const r = await api.post<TokenApi & { token: string }>("/api/integracoes/tokens", novoToken);
                    setNovoToken(null);
                    setSegredo({
                      titulo: `Token “${r.nome}”`,
                      valor: r.token,
                      texto: "Copie o token e cole no sistema que vai usá-lo (cabeçalho Authorization: Bearer).",
                    });
                  })
                }
              >
                {ocupado && <Spinner />} Criar token
              </button>
            </>
          }
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <label className="campo">
              <span>Nome</span>
              <input
                data-autofocus
                maxLength={80}
                value={novoToken.nome}
                placeholder="Ex.: Asaas, ERP do escritório"
                onChange={(e) => setNovoToken({ ...novoToken, nome: e.target.value })}
              />
            </label>
            <label className="campo">
              <span>Ambiente</span>
              <select value={novoToken.ambiente} onChange={(e) => setNovoToken({ ...novoToken, ambiente: e.target.value })}>
                <option value="homologacao">Homologação (testes, sem validade)</option>
                <option value="producao">Produção</option>
              </select>
              <small>O token só emite e consulta notas neste ambiente.</small>
            </label>
            <fieldset className="integracoes__opcoes">
              <legend>Permissões</legend>
              {d.escopos.map((e) => (
                <label className="opcao" key={e.id}>
                  <input
                    type="checkbox"
                    checked={novoToken.escopos.includes(e.id)}
                    onChange={() => setNovoToken({ ...novoToken, escopos: alternar(novoToken.escopos, e.id) })}
                  />
                  <span>{e.descricao}<small className="num">{e.id}</small></span>
                </label>
              ))}
            </fieldset>
            <ErroCaixa erro={erro} titulo="Token não criado" />
          </div>
        </Dialogo>
      )}

      {novoWebhook && d && (
        <Dialogo
          titulo="Novo webhook"
          fechar={() => setNovoWebhook(null)}
          rodape={
            <>
              <button className="btn" onClick={() => setNovoWebhook(null)}>Cancelar</button>
              <button
                className="btn btn--primario"
                disabled={ocupado || !novoWebhook.url.trim() || novoWebhook.eventos.length === 0}
                onClick={() =>
                  executar(async () => {
                    const r = await api.post<WebhookApi & { segredo: string }>("/api/integracoes/webhooks", novoWebhook);
                    setNovoWebhook(null);
                    setSegredo({
                      titulo: "Segredo do webhook",
                      valor: r.segredo,
                      texto:
                        "Cada aviso chega assinado com este segredo (cabeçalho X-Emissor-Assinatura). Guarde-o no sistema que recebe para conferir que o aviso veio daqui.",
                    });
                  })
                }
              >
                {ocupado && <Spinner />} Cadastrar webhook
              </button>
            </>
          }
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <label className="campo">
              <span>Endereço que recebe os avisos</span>
              <input
                data-autofocus
                type="url"
                inputMode="url"
                placeholder="https://"
                value={novoWebhook.url}
                onChange={(e) => setNovoWebhook({ ...novoWebhook, url: e.target.value })}
              />
            </label>
            <label className="campo">
              <span>Descrição (opcional)</span>
              <input maxLength={200} value={novoWebhook.descricao} onChange={(e) => setNovoWebhook({ ...novoWebhook, descricao: e.target.value })} />
            </label>
            <label className="campo">
              <span>Ambiente</span>
              <select value={novoWebhook.ambiente} onChange={(e) => setNovoWebhook({ ...novoWebhook, ambiente: e.target.value })}>
                <option value="homologacao">Homologação</option>
                <option value="producao">Produção</option>
              </select>
            </label>
            <fieldset className="integracoes__opcoes">
              <legend>Eventos</legend>
              {d.eventos.map((e) => (
                <label className="opcao" key={e.id}>
                  <input
                    type="checkbox"
                    checked={novoWebhook.eventos.includes(e.id)}
                    onChange={() => setNovoWebhook({ ...novoWebhook, eventos: alternar(novoWebhook.eventos, e.id) })}
                  />
                  <span>{e.descricao}<small className="num">{e.id}</small></span>
                </label>
              ))}
            </fieldset>
            <ErroCaixa erro={erro} titulo="Webhook não cadastrado" />
          </div>
        </Dialogo>
      )}

      {revogar && (
        <Dialogo
          titulo={`Revogar o token “${revogar.nome}”?`}
          fechar={() => setRevogar(null)}
          rodape={
            <>
              <button className="btn" onClick={() => setRevogar(null)}>Manter</button>
              <button
                className="btn btn--perigo"
                disabled={ocupado}
                onClick={() =>
                  executar(async () => {
                    await api.del(`/api/integracoes/tokens/${revogar.id}`);
                    setRevogar(null);
                    toast("Token revogado.");
                  })
                }
              >
                Revogar token
              </button>
            </>
          }
        >
          <p style={{ marginTop: 0 }}>O sistema que usa este token deixa de conseguir emitir e consultar na hora. Notas já emitidas não mudam.</p>
          <ErroCaixa erro={erro} titulo="Não foi possível revogar" />
        </Dialogo>
      )}

      {segredo && <Segredo {...segredo} fechar={() => setSegredo(null)} />}
      {entregas && <Entregas webhook={entregas} fechar={() => setEntregas(null)} />}
    </section>
  );
}

function Entregas({ webhook, fechar }: { webhook: WebhookApi; fechar: () => void }) {
  const toast = useToast();
  const lista = useQuery({
    queryKey: ["integracoes", "entregas", webhook.id],
    queryFn: () => api.get<Entrega[]>(`/api/integracoes/webhooks/${webhook.id}/entregas`),
  });
  const ROTULO = { pendente: "Aguardando", entregue: "Entregue", falhou: "Falhou" } as const;
  return (
    <Dialogo titulo="Últimas entregas" fechar={fechar} largo rodape={<button className="btn" onClick={fechar}>Fechar</button>}>
      <p className="num" style={{ marginTop: 0, overflowWrap: "anywhere", color: "var(--grafite)" }}>{webhook.url}</p>
      <ErroCaixa erro={lista.error} titulo="Não foi possível carregar as entregas" />
      {lista.data?.length === 0 && <p>Nenhum aviso enviado ainda.</p>}
      {!!lista.data?.length && (
        <div className="tabela-caixa">
          <table className="tabela">
            <thead>
              <tr><th>Quando</th><th>Evento</th><th>Situação</th><th>Resposta</th><th /></tr>
            </thead>
            <tbody>
              {lista.data.map((e) => (
                <tr key={e.id}>
                  <td>{quando(e.criado_em)}</td>
                  <td className="num">{e.evento}</td>
                  <td>
                    {ROTULO[e.status]}
                    {e.tentativas > 1 && ` (${e.tentativas} tentativas)`}
                    {e.proxima_em && <div style={{ color: "var(--grafite)" }}>próxima: {quando(e.proxima_em)}</div>}
                  </td>
                  <td style={{ overflowWrap: "anywhere" }}>{e.ultimo_erro ?? (e.ultimo_status ? `HTTP ${e.ultimo_status}` : "-")}</td>
                  <td className="dir">
                    {e.status !== "pendente" && (
                      <button
                        className="btn btn--pequeno"
                        onClick={async () => {
                          await api.post(`/api/integracoes/entregas/${e.id}/reenviar`).catch((err) => toast(err.message, "erro"));
                          toast("Reenvio agendado.");
                          lista.refetch();
                        }}
                      >
                        Reenviar
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Dialogo>
  );
}

/** Endereço que só a rede local alcança: o Asaas (na internet) não consegue entregar os avisos. */
function enderecoLocal() {
  const h = location.hostname;
  return (
    !h.includes(".") ||
    h.endsWith(".local") ||
    /^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(h)
  );
}

const QUANDO_EMITIR = { recebido: "Pagamento recebido", confirmado: "Pagamento confirmado" } as const;

/** Conector do Asaas: cada cobrança paga vira uma nota, sem programar nada. */
function ReceberCobrancas({ d }: { d: Integracoes }) {
  const qc = useQueryClient();
  const toast = useToast();
  const tributacoes = useTributacoes();
  const [form, setForm] = useState<FormConector | null>(null);
  const [criado, setCriado] = useState<{ conector: Conector; token: string } | null>(null);
  const [avisos, setAvisos] = useState<Conector | null>(null);
  const [remover, setRemover] = useState<Conector | null>(null);
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);
  const chaveConhecida = form ? /^\$aact_(prod|hmlg)_/.test(form.chave.trim()) : true;

  async function executar(fn: () => Promise<void>) {
    setErro(null);
    setOcupado(true);
    try {
      await fn();
      await qc.invalidateQueries({ queryKey: ["integracoes"] });
    } catch (e) {
      setErro(e);
    } finally {
      setOcupado(false);
    }
  }

  async function salvar(f: FormConector) {
    await executar(async () => {
      const corpo = {
        nome: f.nome,
        gatilho: f.gatilho,
        tributacao_id: f.tributacao_id,
        descricao: f.descricao,
        ...(f.chave.trim() ? { chave: f.chave.trim() } : {}),
      };
      if (f.id) {
        await api.put(`/api/integracoes/conectores/${f.id}`, corpo);
        toast("Conector alterado.");
      } else {
        const r = await api.post<Conector & { token: string }>("/api/integracoes/conectores", {
          ...corpo,
          ambiente: f.ambiente,
          ambiente_origem: chaveConhecida ? null : f.ambiente_origem,
        });
        setCriado({ conector: r, token: r.token });
      }
      setForm(null);
    });
  }

  return (
    <>
      <h3 className="integracoes__titulo">Receber cobranças (Asaas)</h3>
      <p style={{ marginTop: 0, color: "var(--grafite)" }}>
        Cada cobrança paga no Asaas vira uma nota desta empresa, com o cliente da cobrança como tomador. Aviso repetido
        não emite outra nota, e estornos não cancelam nada sozinhos.
      </p>
      {enderecoLocal() && (
        <div className="aviso" style={{ marginBottom: 12 }}>
          O Asaas avisa os pagamentos pela internet. Este sistema está num endereço da rede local, que ele não alcança: os
          avisos só chegam se o sistema tiver um endereço público (por exemplo, na hospedagem).
        </div>
      )}
      {d.conectores.length > 0 && (
        <div className="tabela-caixa" style={{ marginBottom: 12 }}>
          <table className="tabela">
            <thead>
              <tr><th>Conector</th><th>Emite quando</th><th>Tributação</th><th>Último aviso</th><th /></tr>
            </thead>
            <tbody>
              {d.conectores.map((c) => (
                <tr key={c.id} className={c.ativo ? undefined : "linha--apagada"}>
                  <td>
                    <b>{c.nome}</b>
                    <div style={{ color: "var(--grafite)" }}>
                      Asaas {c.ambiente_origem === "sandbox" ? "sandbox" : "produção"} → NFS-e em {AMBIENTE[c.ambiente].toLowerCase()}
                    </div>
                    {c.eventos_com_erro > 0 && <div style={{ color: "var(--erro)" }}>{c.eventos_com_erro} aviso(s) com erro</div>}
                  </td>
                  <td>{QUANDO_EMITIR[c.gatilho]}</td>
                  <td>{c.tributacao_nome ?? "Padrão"}</td>
                  <td>{quando(c.ultimo_evento_em)}</td>
                  <td className="dir" style={{ whiteSpace: "nowrap" }}>
                    <button className="btn btn--pequeno" onClick={() => setAvisos(c)}>Avisos</button>{" "}
                    <button
                      className="btn btn--pequeno"
                      onClick={() =>
                        setForm({
                          id: c.id,
                          nome: c.nome,
                          chave: "",
                          ambiente: c.ambiente,
                          ambiente_origem: c.ambiente_origem,
                          gatilho: c.gatilho,
                          tributacao_id: c.tributacao_id,
                          descricao: c.descricao,
                        })
                      }
                    >
                      Editar
                    </button>{" "}
                    <button
                      className="btn btn--pequeno btn--fantasma"
                      disabled={ocupado}
                      onClick={() => executar(async () => { await api.put(`/api/integracoes/conectores/${c.id}`, { ativo: !c.ativo }); })}
                    >
                      {c.ativo ? "Pausar" : "Retomar"}
                    </button>{" "}
                    <button
                      className="btn btn--pequeno btn--fantasma"
                      disabled={ocupado}
                      onClick={() =>
                        executar(async () => {
                          const r = await api.post<{ token: string }>(`/api/integracoes/conectores/${c.id}/token`);
                          setCriado({ conector: c, token: r.token });
                        })
                      }
                    >
                      Novo token
                    </button>{" "}
                    <button className="btn btn--pequeno btn--fantasma" onClick={() => setRemover(c)}>Remover</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <button
        className="btn"
        onClick={() =>
          setForm({
            nome: "Asaas",
            chave: "",
            ambiente: d.ambiente_atual,
            ambiente_origem: "producao",
            gatilho: "recebido",
            tributacao_id: null,
            descricao: "{cobranca}",
          })
        }
      >
        Conectar o Asaas
      </button>
      {!form && !remover && <div style={{ marginTop: 12 }}><ErroCaixa erro={erro} /></div>}

      {form && (
        <Dialogo
          titulo={form.id ? `Conector “${form.nome}”` : "Conectar o Asaas"}
          fechar={() => setForm(null)}
          rodape={
            <>
              <button className="btn" onClick={() => setForm(null)}>Cancelar</button>
              <button
                className="btn btn--primario"
                disabled={ocupado || (!form.id && !form.chave.trim())}
                onClick={() => salvar(form)}
              >
                {ocupado && <Spinner />} {form.id ? "Salvar" : "Conectar"}
              </button>
            </>
          }
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <label className="campo">
              <span>{form.id ? "Chave da API do Asaas (só para trocar)" : "Chave da API do Asaas"}</span>
              <input
                data-autofocus
                type="password"
                autoComplete="off"
                spellCheck={false}
                className="num"
                placeholder="$aact_prod_..."
                value={form.chave}
                onChange={(e) => setForm({ ...form, chave: e.target.value })}
              />
              <small>
                No Asaas: Integrações → Chaves de API. Ela serve só para ler o CPF/CNPJ e o endereço do cliente da cobrança e
                fica guardada cifrada.
              </small>
            </label>
            {!form.id && form.chave.trim() && !chaveConhecida && (
              <label className="campo">
                <span>Conta do Asaas</span>
                <select value={form.ambiente_origem} onChange={(e) => setForm({ ...form, ambiente_origem: e.target.value })}>
                  <option value="producao">Produção</option>
                  <option value="sandbox">Sandbox (testes)</option>
                </select>
              </label>
            )}
            {!form.id && (
              <label className="campo">
                <span>Ambiente da NFS-e</span>
                <select value={form.ambiente} onChange={(e) => setForm({ ...form, ambiente: e.target.value })}>
                  <option value="homologacao">Homologação (testes, sem validade)</option>
                  <option value="producao">Produção</option>
                </select>
                <small>Chave do sandbox do Asaas só emite em homologação.</small>
              </label>
            )}
            <label className="campo">
              <span>Emitir a nota quando</span>
              <select value={form.gatilho} onChange={(e) => setForm({ ...form, gatilho: e.target.value })}>
                {d.gatilhos.map((g) => (
                  <option key={g.id} value={g.id}>{g.descricao}</option>
                ))}
              </select>
              <small>No cartão de crédito o dinheiro fica disponível cerca de 32 dias depois da confirmação; Pix e boleto, logo.</small>
            </label>
            <label className="campo">
              <span>Tributação</span>
              <select
                value={form.tributacao_id ?? ""}
                onChange={(e) => setForm({ ...form, tributacao_id: e.target.value ? Number(e.target.value) : null })}
              >
                <option value="">Padrão das configurações</option>
                {tributacoes.data?.itens.map((t) => (
                  <option key={t.id} value={t.id}>{t.nome}</option>
                ))}
              </select>
            </label>
            <label className="campo">
              <span>Descrição da nota</span>
              <textarea
                rows={3}
                maxLength={1000}
                value={form.descricao}
                onChange={(e) => setForm({ ...form, descricao: e.target.value })}
              />
              <small>
                <code>{"{cobranca}"}</code> é a descrição da cobrança no Asaas; as variáveis da tabela também valem (ex.:{" "}
                <code>{"{mes}"}</code>, <code>{"{competencia}"}</code>).
              </small>
            </label>
            <ErroCaixa erro={erro} titulo={form.id ? "Conector não alterado" : "Asaas não conectado"} />
          </div>
        </Dialogo>
      )}

      {criado && (
        <Dialogo
          titulo="Falta cadastrar o webhook no Asaas"
          fechar={() => setCriado(null)}
          largo
          rodape={<button className="btn btn--primario" onClick={() => setCriado(null)}>Já cadastrei</button>}
        >
          <ol style={{ marginTop: 0, paddingLeft: "1.25rem", display: "flex", flexDirection: "column", gap: 8 }}>
            <li>No Asaas, abra Integrações → Webhooks e crie um webhook novo.</li>
            <li>
              Em URL, cole:
              <code className="num segredo-api">{location.origin + criado.conector.caminho}</code>
              <button className="btn btn--pequeno" onClick={async () => { await copiar(location.origin + criado.conector.caminho); toast("Copiado."); }}>
                Copiar URL
              </button>
            </li>
            <li>
              Em Token de autenticação, cole:
              <code className="num segredo-api">{criado.token}</code>
              <button className="btn btn--pequeno" onClick={async () => { await copiar(criado.token); toast("Copiado."); }}>
                Copiar token
              </button>
            </li>
            <li>
              Marque os eventos de cobrança <code>PAYMENT_RECEIVED</code>
              {criado.conector.gatilho === "confirmado" && <> e <code>PAYMENT_CONFIRMED</code></>} e salve.
            </li>
          </ol>
          <p style={{ color: "var(--grafite)", marginBottom: 0 }}>
            O token não aparece de novo. Se perder, gere outro no conector e troque no Asaas.
          </p>
        </Dialogo>
      )}

      {remover && (
        <Dialogo
          titulo={`Remover o conector “${remover.nome}”?`}
          fechar={() => setRemover(null)}
          rodape={
            <>
              <button className="btn" onClick={() => setRemover(null)}>Manter</button>
              <button
                className="btn btn--perigo"
                disabled={ocupado}
                onClick={() =>
                  executar(async () => {
                    await api.del(`/api/integracoes/conectores/${remover.id}`);
                    setRemover(null);
                    toast("Conector removido.");
                  })
                }
              >
                Remover conector
              </button>
            </>
          }
        >
          <p style={{ marginTop: 0 }}>
            Os pagamentos deixam de virar nota e o histórico de avisos é apagado. Notas já emitidas não mudam. Remova também o
            webhook no Asaas.
          </p>
          <ErroCaixa erro={erro} titulo="Não foi possível remover" />
        </Dialogo>
      )}

      {avisos && <Avisos conector={avisos} fechar={() => setAvisos(null)} />}
    </>
  );
}

function Avisos({ conector, fechar }: { conector: Conector; fechar: () => void }) {
  const toast = useToast();
  const lista = useQuery({
    queryKey: ["integracoes", "avisos", conector.id],
    queryFn: () => api.get<AvisoEntrada[]>(`/api/integracoes/conectores/${conector.id}/eventos`),
    refetchInterval: 5000,
  });
  const ROTULO = {
    pendente: "Processando",
    emitida: "Nota criada",
    repetida: "Já tinha nota",
    ignorado: "Sem nota",
    erro: "Erro",
  } as const;
  return (
    <Dialogo titulo="Avisos recebidos do Asaas" fechar={fechar} largo rodape={<button className="btn" onClick={fechar}>Fechar</button>}>
      <ErroCaixa erro={lista.error} titulo="Não foi possível carregar os avisos" />
      {lista.data?.length === 0 && <p style={{ marginTop: 0 }}>Nenhum aviso recebido ainda.</p>}
      {!!lista.data?.length && (
        <div className="tabela-caixa">
          <table className="tabela">
            <thead>
              <tr><th>Quando</th><th>Cobrança</th><th>Situação</th><th /></tr>
            </thead>
            <tbody>
              {lista.data.map((a) => (
                <tr key={a.id}>
                  <td>{quando(a.recebido_em)}</td>
                  <td>
                    <span className="num">{a.pagamento_id ?? "-"}</span>
                    <div className="num" style={{ color: "var(--grafite)" }}>{a.evento}{a.valor ? ` · R$ ${a.valor}` : ""}</div>
                  </td>
                  <td style={{ overflowWrap: "anywhere" }}>
                    <span style={a.status === "erro" ? { color: "var(--erro)" } : undefined}>{ROTULO[a.status]}</span>
                    {a.emissao_id && <span style={{ color: "var(--grafite)" }}> (nota {a.emissao_id})</span>}
                    {a.mensagem && <div style={{ color: "var(--grafite)" }}>{a.mensagem}</div>}
                  </td>
                  <td className="dir">
                    {(a.status === "erro" || a.status === "ignorado") && (
                      <button
                        className="btn btn--pequeno"
                        onClick={async () => {
                          try {
                            await api.post(`/api/integracoes/eventos-entrada/${encodeURIComponent(a.id)}/reprocessar`);
                            toast("Aviso enviado para processar de novo.");
                            lista.refetch();
                          } catch (err) {
                            toast((err as Error).message, "erro");
                          }
                        }}
                      >
                        Processar de novo
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Dialogo>
  );
}
