import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  api,
  baixar,
  dadosTributacao,
  type CertificadoInfo,
  type Config,
  type DadosTributacao,
  type ErroEmissao,
  type Fiscal,
  type Status,
  type Tributacao,
} from "../api";
import { useTributacoes } from "../components/Abas";
import { CamposEmpresa, CamposTributacao } from "../components/FiscalForm";
import { ImportarClientesXml } from "../components/ImportarClientesXml";
import { Dialogo, ErroCaixa, MensagensSefin, Spinner, useToast } from "../ui";
import { CartaoCertificado, ZonaArquivo } from "./Onboarding";

const SECOES = [
  ["ambiente", "Ambiente"],
  ["certificado", "Certificado digital"],
  ["empresa", "Dados da empresa"],
  ["tributacoes", "Tributações"],
  ["clientes-xml", "Clientes via XML"],
  ["numeracao", "Numeração"],
  ["conexao", "Conexão com a Sefin"],
  ["diagnostico", "Diagnóstico"],
] as const;

export function Configuracoes({ status }: { status: Status }) {
  const cfg = useQuery({ queryKey: ["config"], queryFn: () => api.get<Config>("/api/config") });
  useEffect(() => {
    const secao = new URLSearchParams(location.hash.split("?")[1] || "").get("secao");
    if (secao) setTimeout(() => document.getElementById(secao)?.scrollIntoView({ behavior: "smooth" }), 50);
  }, []);
  return (
    <div className="pagina">
      <div className="cabecalho-pagina">
        <div>
          <h1 className="titulo">Configurações</h1>
          <p className="subtitulo">Valem para todas as próximas emissões. Notas já emitidas não mudam.</p>
        </div>
      </div>
      <ErroCaixa erro={cfg.error} titulo="Não foi possível carregar as configurações" />
      {status.problemas.length > 0 && (
        <div className="erro-caixa" style={{ marginBottom: 16 }}>
          <div>
            <strong>Emissão bloqueada</strong>
            <ul>{status.problemas.map((p) => <li key={p}>{p}</li>)}</ul>
          </div>
        </div>
      )}
      {cfg.data && (
        <div className="config">
          <nav className="config__menu" aria-label="Seções das configurações">
            {SECOES.map(([id, rotulo]) => (
              <a key={id} href={`#/configuracoes?secao=${id}`} onClick={(e) => { e.preventDefault(); document.getElementById(id)?.scrollIntoView({ behavior: "smooth" }); }}>
                {rotulo}
              </a>
            ))}
          </nav>
          <div>
            <SecaoAmbiente cfg={cfg.data} />
            <SecaoCertificado status={status} />
            <SecaoEmpresa cfg={cfg.data} />
            <SecaoTributacoes />
            <section className="folha" id="clientes-xml">
              <h2 className="secao-titulo">Cadastro automático de clientes via XML</h2>
              <ImportarClientesXml />
            </section>
            <SecaoNumeracao cfg={cfg.data} />
            <SecaoConexao ambiente={cfg.data.ambiente} />
            <SecaoDiagnostico versao={status.versao} />
          </div>
        </div>
      )}
    </div>
  );
}

function useSalvar() {
  const qc = useQueryClient();
  const toast = useToast();
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);
  async function salvar(corpo: Partial<Config> | { fiscal: Fiscal }, msg: string) {
    setErro(null);
    setOcupado(true);
    try {
      await api.put("/api/config", corpo);
      toast(msg);
      await qc.invalidateQueries();
      return true;
    } catch (e) {
      setErro(e);
      return false;
    } finally {
      setOcupado(false);
    }
  }
  return { salvar, erro, ocupado };
}

function SecaoAmbiente({ cfg }: { cfg: Config }) {
  const { salvar, erro, ocupado } = useSalvar();
  const [confirmar, setConfirmar] = useState(false);
  const trocar = (amb: "1" | "2") => {
    if (amb === cfg.ambiente) return;
    if (amb === "1") setConfirmar(true);
    else salvar({ ambiente: "2" }, "Ambiente alterado para Homologação.");
  };
  return (
    <section className="folha" id="ambiente">
      <h2 className="secao-titulo">Ambiente</h2>
      <div className="amb-escolha">
        <button type="button" className="amb-op" aria-pressed={cfg.ambiente === "2"} onClick={() => trocar("2")} disabled={ocupado}>
          <strong>Homologação</strong>
          <span>Produção restrita da Sefin, para testes. Notas sem validade jurídica.</span>
        </button>
        <button type="button" className="amb-op amb-op--prod" aria-pressed={cfg.ambiente === "1"} onClick={() => trocar("1")} disabled={ocupado}>
          <strong>Produção</strong>
          <span>Notas reais, com validade jurídica e cobrança de ISS.</span>
        </button>
      </div>
      <div style={{ marginTop: 12 }}><ErroCaixa erro={erro} /></div>
      {confirmar && (
        <Dialogo
          titulo="Passar a emitir em produção?"
          fechar={() => setConfirmar(false)}
          rodape={
            <>
              <button className="btn" onClick={() => setConfirmar(false)}>Continuar em homologação</button>
              <button
                className="btn btn--escuro"
                disabled={ocupado}
                onClick={async () => (await salvar({ ambiente: "1" }, "Ambiente alterado para Produção.")) && setConfirmar(false)}
              >
                Mudar para produção
              </button>
            </>
          }
        >
          <p style={{ marginTop: 0 }}>
            A partir daqui, cada nota emitida tem validade jurídica e gera ISS. Confira antes:
          </p>
          <ul>
            <li>Os testes em homologação foram autorizados e o PDF saiu correto.</li>
            <li>A numeração de produção está certa (próxima DPS nº <b className="num">{cfg.proximo_ndps_producao}</b>, série <b className="num">{cfg.serie}</b>).</li>
            <li>As tributações (código de serviço, tributos) estão iguais às das notas atuais.</li>
          </ul>
        </Dialogo>
      )}
    </section>
  );
}

function SecaoCertificado({ status }: { status: Status }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [trocando, setTrocando] = useState(false);
  const [arquivo, setArquivo] = useState<File | null>(null);
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);
  const cert = status.certificado;

  async function enviar() {
    if (!arquivo) return;
    setErro(null);
    setOcupado(true);
    try {
      const fd = new FormData();
      fd.append("arquivo", arquivo);
      fd.append("senha", senha);
      const r = await api.post<CertificadoInfo>("/api/certificado", fd);
      toast(r.aviso ?? "Certificado substituído.", r.aviso ? "erro" : "ok");
      setTrocando(false);
      setSenha("");
      setArquivo(null);
      qc.invalidateQueries();
    } catch (e) {
      setErro(e);
    } finally {
      setOcupado(false);
    }
  }

  return (
    <section className="folha" id="certificado">
      <h2 className="secao-titulo">Certificado digital</h2>
      {cert?.erro && <div className="erro-caixa">{cert.erro}</div>}
      {cert && !cert.erro && <CartaoCertificado info={cert} />}
      {!trocando ? (
        <button className="btn" style={{ marginTop: 14 }} onClick={() => setTrocando(true)}>
          Substituir certificado
        </button>
      ) : (
        <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 12 }}>
          <ZonaArquivo aceitar=".pfx,.p12" titulo={arquivo ? arquivo.name : "Escolha o novo certificado"} dica=".pfx ou .p12" onArquivos={(f) => setArquivo(f[0] ?? null)} />
          <label className="campo" style={{ maxWidth: 320 }}>
            <span>Senha do certificado</span>
            <input type="password" autoComplete="off" value={senha} onChange={(e) => setSenha(e.target.value)} />
          </label>
          <ErroCaixa erro={erro} titulo="Certificado não aceito" />
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn" onClick={() => setTrocando(false)}>Cancelar</button>
            <button className="btn btn--primario" disabled={!arquivo || !senha || ocupado} onClick={enviar}>
              {ocupado && <Spinner />} Validar e salvar
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function SecaoNumeracao({ cfg }: { cfg: Config }) {
  const { salvar, erro, ocupado } = useSalvar();
  const [v, setV] = useState({ serie: String(cfg.serie), h: String(cfg.proximo_ndps_homologacao), p: String(cfg.proximo_ndps_producao) });
  const so = (s: string) => s.replace(/\D/g, "");
  return (
    <section className="folha" id="numeracao">
      <h2 className="secao-titulo">Numeração da DPS</h2>
      <p style={{ marginTop: 0, color: "var(--grafite)" }}>
        Cada nota enviada consome um número. Homologação e produção têm contadores separados. Só altere se souber que a
        numeração precisa pular (por exemplo, se esta série já foi usada por outro sistema).
      </p>
      <div className="grade-form" style={{ maxWidth: 720 }}>
        <label className="campo">
          <span>Série</span>
          <input className="num" value={v.serie} onChange={(e) => setV({ ...v, serie: so(e.target.value) })} />
          <small>1 a 49999.</small>
        </label>
        <label className="campo">
          <span>Próxima DPS — homologação</span>
          <input className="num" value={v.h} onChange={(e) => setV({ ...v, h: so(e.target.value) })} />
        </label>
        <label className="campo">
          <span>Próxima DPS — produção</span>
          <input className="num" value={v.p} onChange={(e) => setV({ ...v, p: so(e.target.value) })} />
        </label>
      </div>
      <div style={{ marginTop: 12 }}><ErroCaixa erro={erro} titulo="Numeração não salva" /></div>
      <button
        className="btn btn--primario"
        style={{ marginTop: 14 }}
        disabled={ocupado}
        onClick={() => salvar({ serie: Number(v.serie), proximo_ndps_homologacao: Number(v.h), proximo_ndps_producao: Number(v.p) }, "Numeração salva.")}
      >
        Salvar numeração
      </button>
    </section>
  );
}

/** Campos da empresa no `fiscal` das configurações; o resto é a tributação padrão. */
const CAMPOS_EMPRESA = ["cnpj", "inscricao_municipal", "c_mun_emissor", "fone", "email", "op_simp_nac", "reg_ap_trib_sn", "reg_esp_trib"] as const;

function SecaoEmpresa({ cfg }: { cfg: Config }) {
  const { salvar, erro, ocupado } = useSalvar();
  const [fiscal, setFiscal] = useState<Fiscal | null>(cfg.fiscal);
  if (!fiscal || !cfg.fiscal) return null;
  const atual = cfg.fiscal;
  const empresa = Object.fromEntries(CAMPOS_EMPRESA.map((k) => [k, fiscal[k]]));
  return (
    <section className="folha" id="empresa">
      <h2 className="secao-titulo">Dados da empresa</h2>
      <CamposEmpresa valor={fiscal} onChange={setFiscal} />
      <div style={{ marginTop: 16 }}><ErroCaixa erro={erro} titulo="Dados da empresa não salvos" /></div>
      <button
        className="btn btn--primario"
        style={{ marginTop: 14 }}
        disabled={ocupado}
        // Só os campos da empresa: a tributação padrão pode ter mudado no diálogo enquanto isto estava aberto.
        onClick={() => salvar({ fiscal: { ...atual, ...empresa } }, "Dados da empresa salvos.")}
      >
        Salvar dados da empresa
      </button>
    </section>
  );
}

type Edicao = { id: number | "padrao" | null; nome: string; dados: DadosTributacao };

function SecaoTributacoes() {
  const lista = useTributacoes();
  const cfg = useQuery({ queryKey: ["config"], queryFn: () => api.get<Config>("/api/config") });
  const qc = useQueryClient();
  const toast = useToast();
  const [editando, setEditando] = useState<Edicao | null>(null);
  const [excluir, setExcluir] = useState<Tributacao | null>(null);
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);
  const padrao = editando?.id === "padrao";

  async function salvar() {
    if (!editando) return;
    setErro(null);
    setOcupado(true);
    try {
      if (editando.id === "padrao") {
        // A tributação padrão mora nas configurações, junto dos dados da empresa.
        if (!cfg.data?.fiscal) throw new Error("Configurações não carregadas. Recarregue a página.");
        await api.put("/api/config", { fiscal: { ...cfg.data.fiscal, ...editando.dados } });
        toast("Tributação padrão salva.");
        qc.invalidateQueries();
      } else {
        const corpo = { nome: editando.nome, dados: editando.dados };
        if (editando.id) await api.put(`/api/tributacoes/${editando.id}`, corpo);
        else await api.post("/api/tributacoes", corpo);
        toast(`Tributação “${editando.nome.trim()}” salva.`);
        qc.invalidateQueries({ queryKey: ["tributacoes"] });
        qc.invalidateQueries({ queryKey: ["abas"] });
      }
      setEditando(null);
    } catch (e) {
      setErro(e);
    } finally {
      setOcupado(false);
    }
  }

  async function confirmarExclusao() {
    if (!excluir) return;
    setOcupado(true);
    setErro(null);
    try {
      await api.del(`/api/tributacoes/${excluir.id}`);
      toast(`Tributação “${excluir.nome}” excluída.`);
      setExcluir(null);
      qc.invalidateQueries({ queryKey: ["tributacoes"] });
    } catch (e) {
      setErro(e);
    } finally {
      setOcupado(false);
    }
  }

  const itens = lista.data?.itens ?? [];
  const base = lista.data?.padrao;
  return (
    <section className="folha" id="tributacoes">
      <h2 className="secao-titulo">Tributações</h2>
      <p style={{ marginTop: 0, color: "var(--grafite)" }}>
        Código do serviço, ISS, retenções, PIS/COFINS e IBS/CBS de cada tipo de nota. A padrão vale para toda aba da tabela
        que não escolher outra; crie mais se alguns clientes têm tributação diferente (por exemplo, ISS retido).
      </p>
      <ErroCaixa erro={lista.error} titulo="Não foi possível carregar as tributações" />
      {base && (
        <div className="tabela-caixa" style={{ marginBottom: 14 }}>
          <table className="tabela">
            <thead>
              <tr>
                <th>Nome</th>
                <th>Resumo</th>
                <th>Abas que usam</th>
                <th />
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  <b>Padrão</b>
                </td>
                <td>{lista.data?.padrao_resumo}</td>
                <td style={{ color: "var(--grafite)" }}>as sem tributação própria</td>
                <td className="dir" style={{ whiteSpace: "nowrap" }}>
                  <button className="btn btn--pequeno" onClick={() => setEditando({ id: "padrao", nome: "Padrão", dados: dadosTributacao(base) })}>
                    Editar
                  </button>
                </td>
              </tr>
              {itens.map((t) => (
                <tr key={t.id}>
                  <td><b>{t.nome}</b></td>
                  <td>{t.resumo}</td>
                  <td>{t.abas.length ? t.abas.join(", ") : <span style={{ color: "var(--grafite-claro)" }}>nenhuma</span>}</td>
                  <td className="dir" style={{ whiteSpace: "nowrap" }}>
                    <button className="btn btn--pequeno" onClick={() => setEditando({ id: t.id, nome: t.nome, dados: dadosTributacao(t.dados) })}>
                      Editar
                    </button>{" "}
                    <button className="btn btn--pequeno btn--fantasma" onClick={() => setExcluir(t)}>
                      Excluir
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <button className="btn" disabled={!base} onClick={() => base && setEditando({ id: null, nome: "", dados: dadosTributacao(base) })}>
        Nova tributação
      </button>
      {!editando && !excluir && <div style={{ marginTop: 12 }}><ErroCaixa erro={erro} /></div>}

      {editando && (
        <Dialogo
          titulo={padrao ? "Tributação padrão" : editando.id ? `Tributação “${editando.nome}”` : "Nova tributação"}
          fechar={() => setEditando(null)}
          largo
          rodape={
            <>
              <button className="btn" onClick={() => setEditando(null)}>Cancelar</button>
              <button className="btn btn--primario" disabled={ocupado || !editando.nome.trim()} onClick={salvar}>
                {ocupado && <Spinner />} Salvar tributação
              </button>
            </>
          }
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 26 }}>
            {padrao ? (
              <p style={{ margin: 0, color: "var(--grafite)" }}>
                Usada pelas abas sem tributação própria e pela nota avulsa quando nenhuma outra é escolhida.
              </p>
            ) : (
              <label className="campo" style={{ maxWidth: 420 }}>
                <span>Nome</span>
                <input
                  value={editando.nome}
                  maxLength={60}
                  autoFocus
                  placeholder="Ex.: ISS retido pelo tomador"
                  onChange={(e) => setEditando({ ...editando, nome: e.target.value })}
                />
                {!editando.id && <small>Começa igual à tributação padrão; mude só o que for diferente.</small>}
              </label>
            )}
            <CamposTributacao
              valor={editando.dados}
              onChange={(d) => setEditando({ ...editando, dados: d })}
              regime={lista.data?.regime}
            />
            <ErroCaixa erro={erro} titulo="Tributação não salva" />
          </div>
        </Dialogo>
      )}
      {excluir && (
        <Dialogo
          titulo={`Excluir a tributação “${excluir.nome}”?`}
          fechar={() => setExcluir(null)}
          rodape={
            <>
              <button className="btn" onClick={() => setExcluir(null)}>Manter</button>
              <button className="btn btn--perigo" disabled={ocupado} onClick={confirmarExclusao}>Excluir tributação</button>
            </>
          }
        >
          <p style={{ marginTop: 0 }}>Notas já emitidas não mudam. Abas que usam esta tributação precisam trocar de tributação antes.</p>
          <ErroCaixa erro={erro} titulo="Não foi possível excluir" />
        </Dialogo>
      )}
    </section>
  );
}

function SecaoConexao({ ambiente }: { ambiente: string }) {
  const [r, setR] = useState<{ ok: boolean; ambiente: string; mensagem?: string; erro?: ErroEmissao } | null>(null);
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);
  async function testar() {
    setOcupado(true);
    setErro(null);
    setR(null);
    try {
      setR(await api.post("/api/config/testar-conexao"));
    } catch (e) {
      setErro(e);
    } finally {
      setOcupado(false);
    }
  }
  return (
    <section className="folha" id="conexao">
      <h2 className="secao-titulo">Conexão com a Sefin</h2>
      <p style={{ marginTop: 0, color: "var(--grafite)" }}>
        Testa a conexão segura com a Sefin Nacional em {ambiente === "1" ? "produção" : "homologação"} usando o certificado. Nenhuma
        nota é emitida.
      </p>
      <button className="btn" onClick={testar} disabled={ocupado}>
        {ocupado && <Spinner />} Testar conexão
      </button>
      <div style={{ marginTop: 12 }}>
        {r?.ok && <div className="info-caixa">✓ {r.mensagem} ({r.ambiente})</div>}
        {r && !r.ok && r.erro && <div className="erro-caixa"><MensagensSefin erro={r.erro} /></div>}
        <ErroCaixa erro={erro} titulo="Teste não executado" />
      </div>
    </section>
  );
}

function SecaoDiagnostico({ versao }: { versao: string }) {
  const toast = useToast();
  const [ocupado, setOcupado] = useState(false);
  return (
    <section className="folha" id="diagnostico">
      <h2 className="secao-titulo">Diagnóstico</h2>
      <p style={{ marginTop: 0, color: "var(--grafite)" }}>
        Gera um arquivo com os registros dos últimos 7 dias (envios à Sefin, respostas e erros) para análise técnica. Não inclui o
        certificado nem a senha.
      </p>
      <button
        className="btn"
        disabled={ocupado}
        onClick={async () => {
          setOcupado(true);
          await baixar("/api/diagnostico?dias=7").catch((e) => toast(e.message, "erro"));
          setOcupado(false);
        }}
      >
        {ocupado && <Spinner />} Baixar arquivo de diagnóstico
      </button>
      <p style={{ color: "var(--grafite)", fontSize: 12, marginBottom: 0 }}>Versão {versao}</p>
    </section>
  );
}
