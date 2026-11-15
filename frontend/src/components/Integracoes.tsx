import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { copiar, Dialogo, ErroCaixa, Spinner, useToast } from "../ui";

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
type Integracoes = {
  ambiente_atual: "producao" | "homologacao";
  tokens: TokenApi[];
  webhooks: WebhookApi[];
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
