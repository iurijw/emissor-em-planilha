import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { api, type CertificadoInfo, type Cliente, type Fiscal, type Linha, type Status } from "../api";
import { FiscalForm } from "../components/FiscalForm";
import { formatarDocumento, moeda } from "../format";
import { ErroCaixa, Spinner } from "../ui";

const PASSOS = ["Certificado digital", "Notas anteriores", "Padrões da nota", "Ambiente e numeração", "Revisão"];

export function ZonaArquivo(props: {
  aceitar: string;
  multiplo?: boolean;
  titulo: string;
  dica: string;
  onArquivos: (f: File[]) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const [ativo, setAtivo] = useState(false);
  return (
    <div
      className={`soltar ${ativo ? "soltar--ativo" : ""}`}
      role="button"
      tabIndex={0}
      onClick={() => ref.current?.click()}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && ref.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setAtivo(true);
      }}
      onDragLeave={() => setAtivo(false)}
      onDrop={(e) => {
        e.preventDefault();
        setAtivo(false);
        props.onArquivos(Array.from(e.dataTransfer.files));
      }}
    >
      <strong>{props.titulo}</strong>
      <span style={{ color: "var(--grafite)" }}>{props.dica}</span>
      <input
        ref={ref}
        type="file"
        hidden
        accept={props.aceitar}
        multiple={props.multiplo}
        onChange={(e) => {
          props.onArquivos(Array.from(e.target.files ?? []));
          e.target.value = "";
        }}
      />
    </div>
  );
}

export function CartaoCertificado({ info }: { info: CertificadoInfo }) {
  return (
    <dl className="cert-cartao">
      <dt>Titular</dt>
      <dd>{info.titular}</dd>
      <dt>CNPJ</dt>
      <dd className="num">{info.cnpj ? formatarDocumento(info.cnpj) : "—"}</dd>
      <dt>Emitido por</dt>
      <dd>{info.emissor}</dd>
      <dt>Válido até</dt>
      <dd>
        {new Date(info.valido_ate).toLocaleDateString("pt-BR")}{" "}
        <span style={{ color: info.dias_para_vencer <= 30 ? "var(--erro)" : "var(--grafite)", fontWeight: 400 }}>
          ({info.dias_para_vencer} dias)
        </span>
      </dd>
    </dl>
  );
}

export function Onboarding({ status }: { status: Status }) {
  const qc = useQueryClient();
  const [passo, setPasso] = useState(0);
  const [cert, setCert] = useState<CertificadoInfo | null>(status.certificado?.erro ? null : status.certificado);
  const [arquivoPfx, setArquivoPfx] = useState<File | null>(null);
  const [senha, setSenha] = useState("");
  const [fiscal, setFiscal] = useState<Fiscal | null>(null);
  const [importacao, setImportacao] = useState<{ notas: number; clientes: Cliente[]; linhas: Linha[]; erros: { arquivo: string; erro: string }[] } | null>(null);
  const [usarLinhas, setUsarLinhas] = useState(true);
  const [xmls, setXmls] = useState<File[]>([]);
  const [ambiente, setAmbiente] = useState<"1" | "2">("2");
  const [serie, setSerie] = useState("1");
  const [proximo, setProximo] = useState("1");
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);

  async function executar(fn: () => Promise<void>) {
    setErro(null);
    setOcupado(true);
    try {
      await fn();
    } catch (e) {
      setErro(e);
    } finally {
      setOcupado(false);
    }
  }

  const enviarCertificado = () =>
    executar(async () => {
      if (!arquivoPfx) throw new Error("Escolha o arquivo do certificado (.pfx ou .p12).");
      const fd = new FormData();
      fd.append("arquivo", arquivoPfx);
      fd.append("senha", senha);
      setCert(await api.post<CertificadoInfo>("/api/certificado", fd));
      setSenha("");
    });

  // Acumula os arquivos escolhidos (em uma ou várias vezes) e reenvia o conjunto todo,
  // para os padrões virem da nota mais recente entre todas.
  const importarXmls = (novos: File[]) =>
    executar(async () => {
      const porNome = new Map([...xmls, ...novos].map((f) => [f.name, f]));
      const files = [...porNome.values()];
      setXmls(files);
      const fd = new FormData();
      files.forEach((f) => fd.append("arquivos", f));
      const r = await api.post<{ fiscal: Fiscal; clientes: Cliente[]; linhas_sugeridas: Linha[]; erros: { arquivo: string; erro: string }[]; notas_lidas: number }>(
        "/api/onboarding/xmls",
        fd,
      );
      setImportacao({ notas: r.notas_lidas, clientes: r.clientes, linhas: r.linhas_sugeridas, erros: r.erros });
      setFiscal(r.fiscal);
    });

  const irParaPadroes = () =>
    executar(async () => {
      if (!fiscal) {
        const r = await api.get<{ fiscal: Fiscal }>("/api/onboarding/padroes");
        setFiscal({ ...r.fiscal, cnpj: r.fiscal.cnpj || cert?.cnpj || "" });
      }
      setPasso(2);
    });

  const concluir = () =>
    executar(async () => {
      await api.post("/api/onboarding/concluir", {
        fiscal,
        ambiente,
        serie: Number(serie),
        [ambiente === "1" ? "proximo_ndps_producao" : "proximo_ndps_homologacao"]: Number(proximo),
        linhas: usarLinhas && importacao ? importacao.linhas : [],
      });
      await qc.invalidateQueries();
    });

  const validarPadroes = () =>
    executar(async () => {
      // Valida no servidor sem concluir: reaproveita PUT /api/config.
      await api.put("/api/config", { fiscal });
      setPasso(3);
    });

  return (
    <div className="onb">
      <aside className="onb__lado">
        <div className="marca">
          <img src="/logo.svg" alt="" style={{ background: "#fff", borderRadius: 6, padding: 2 }} />
          <span>Emissor em Planilha</span>
        </div>
        <ol className="onb__passos">
          {PASSOS.map((p, i) => (
            <li key={p} data-estado={i === passo ? "atual" : i < passo ? "feito" : "futuro"}>
              <span className="n">{i < passo ? "✓" : i + 1}</span>
              {p}
            </li>
          ))}
        </ol>
        <p className="onb__rodape">
          NFS-e Padrão Nacional · os dados ficam salvos neste servidor. O certificado é guardado criptografado.
        </p>
      </aside>

      <section className="onb__conteudo">
        {passo === 0 && (
          <>
            <h1 className="titulo">Certificado digital da empresa</h1>
            <p className="subtitulo">
              O certificado A1 (arquivo .pfx ou .p12) assina cada nota e autentica o sistema na Sefin Nacional. Ele fica
              guardado neste servidor; você não precisará enviá-lo de novo.
            </p>
            <div style={{ marginTop: 24, display: "flex", flexDirection: "column", gap: 16 }}>
              {cert ? (
                <div className="folha">
                  <h3 className="secao-titulo">Certificado carregado</h3>
                  <CartaoCertificado info={cert} />
                  {cert.aviso && <div className="aviso" style={{ marginTop: 12 }}>{cert.aviso}</div>}
                  <button className="btn btn--pequeno" style={{ marginTop: 14 }} onClick={() => setCert(null)}>
                    Trocar certificado
                  </button>
                </div>
              ) : (
                <>
                  <ZonaArquivo
                    aceitar=".pfx,.p12"
                    titulo={arquivoPfx ? arquivoPfx.name : "Escolha ou arraste o arquivo do certificado"}
                    dica={arquivoPfx ? "Clique para trocar o arquivo" : "Arquivo .pfx ou .p12 (certificado A1)"}
                    onArquivos={(f) => setArquivoPfx(f[0] ?? null)}
                  />
                  <label className="campo" style={{ maxWidth: 360 }}>
                    <span>Senha do certificado</span>
                    <input
                      type="password"
                      value={senha}
                      autoComplete="off"
                      onChange={(e) => setSenha(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && enviarCertificado()}
                    />
                  </label>
                </>
              )}
              <ErroCaixa erro={erro} />
            </div>
            <div className="onb__acoes">
              {!cert ? (
                <button className="btn btn--primario btn--grande" disabled={ocupado || !arquivoPfx || !senha} onClick={enviarCertificado}>
                  {ocupado && <Spinner />} Validar certificado
                </button>
              ) : (
                <button className="btn btn--primario btn--grande" onClick={() => setPasso(1)}>
                  Continuar
                </button>
              )}
            </div>
          </>
        )}

        {passo === 1 && (
          <>
            <h1 className="titulo">Notas já emitidas</h1>
            <p className="subtitulo">
              Envie os XMLs das NFS-e que a empresa já emitiu. Deles saem os padrões da nota (código do serviço, regime,
              tributos) e o cadastro dos clientes com endereço. Pode pular se preferir preencher à mão.
            </p>
            <div style={{ marginTop: 24, display: "flex", flexDirection: "column", gap: 16 }}>
              <ZonaArquivo aceitar=".xml" multiplo titulo="Escolha ou arraste os XMLs" dica="Pode selecionar vários de uma vez" onArquivos={importarXmls} />
              {ocupado && (
                <p className="carregando">
                  <Spinner /> Lendo as notas…
                </p>
              )}
              {importacao && (
                <div className="folha">
                  <p style={{ margin: 0 }}>
                    <b className="num">{importacao.notas}</b> nota(s) lida(s) · <b className="num">{importacao.clientes.length}</b>{" "}
                    cliente(s) cadastrado(s).
                  </p>
                  {importacao.erros.length > 0 && (
                    <div className="aviso" style={{ marginTop: 12 }}>
                      <div>
                        <strong>Arquivos ignorados</strong>
                        <ul>
                          {importacao.erros.map((e) => (
                            <li key={e.arquivo}>
                              {e.arquivo}: {e.erro}
                            </li>
                          ))}
                        </ul>
                      </div>
                    </div>
                  )}
                  {importacao.linhas.length > 0 && (
                    <label style={{ display: "flex", gap: 8, alignItems: "flex-start", marginTop: 14 }}>
                      <input type="checkbox" checked={usarLinhas} onChange={(e) => setUsarLinhas(e.target.checked)} />
                      <span>
                        Montar a tabela de emissão com estes {importacao.linhas.length} clientes, usando o valor e a descrição
                        da última nota de cada um.
                      </span>
                    </label>
                  )}
                </div>
              )}
              <ErroCaixa erro={erro} />
            </div>
            <div className="onb__acoes">
              <button className="btn btn--grande" onClick={() => setPasso(0)}>
                Voltar
              </button>
              <button className="btn btn--primario btn--grande" disabled={ocupado} onClick={irParaPadroes}>
                {importacao ? "Continuar" : "Pular e preencher à mão"}
              </button>
            </div>
          </>
        )}

        {passo === 2 && fiscal && (
          <>
            <h1 className="titulo">Padrões da nota</h1>
            <p className="subtitulo">
              Estes dados vão em toda nota emitida. {importacao ? "Vieram da nota mais recente enviada; confira." : ""} Tomador,
              valor e descrição vêm de cada linha da tabela; a data de competência é sempre a data da emissão.
            </p>
            <div className="folha" style={{ marginTop: 24 }}>
              <FiscalForm valor={fiscal} onChange={setFiscal} />
            </div>
            <div style={{ marginTop: 16 }}>
              <ErroCaixa erro={erro} />
            </div>
            <div className="onb__acoes">
              <button className="btn btn--grande" onClick={() => setPasso(1)}>
                Voltar
              </button>
              <button className="btn btn--primario btn--grande" disabled={ocupado} onClick={validarPadroes}>
                {ocupado && <Spinner />} Continuar
              </button>
            </div>
          </>
        )}

        {passo === 3 && (
          <>
            <h1 className="titulo">Ambiente e numeração</h1>
            <p className="subtitulo">
              Comece em homologação para testar sem validade jurídica. A troca para produção fica em Configurações.
            </p>
            <div className="amb-escolha" style={{ marginTop: 24 }}>
              <button type="button" className="amb-op" aria-pressed={ambiente === "2"} onClick={() => setAmbiente("2")}>
                <strong>Homologação</strong>
                <span>Produção restrita da Sefin. As notas não têm validade jurídica.</span>
              </button>
              <button type="button" className="amb-op amb-op--prod" aria-pressed={ambiente === "1"} onClick={() => setAmbiente("1")}>
                <strong>Produção</strong>
                <span>Notas reais, com validade jurídica.</span>
              </button>
            </div>
            <div className="grade-form" style={{ marginTop: 24, maxWidth: 520 }}>
              <label className="campo">
                <span>Série da DPS</span>
                <input className="num" value={serie} onChange={(e) => setSerie(e.target.value.replace(/\D/g, ""))} />
                <small>1 a 49999. A série 70000 das notas atuais é exclusiva do emissor do site.</small>
              </label>
              <label className="campo">
                <span>Próximo número de DPS</span>
                <input className="num" value={proximo} onChange={(e) => setProximo(e.target.value.replace(/\D/g, ""))} />
                <small>Comece em 1 se esta série nunca foi usada.</small>
              </label>
            </div>
            <ErroCaixa erro={erro} />
            <div className="onb__acoes">
              <button className="btn btn--grande" onClick={() => setPasso(2)}>
                Voltar
              </button>
              <button
                className="btn btn--primario btn--grande"
                disabled={!serie || Number(serie) < 1 || Number(serie) > 49999 || !proximo || Number(proximo) < 1}
                onClick={() => setPasso(4)}
              >
                Continuar
              </button>
            </div>
          </>
        )}

        {passo === 4 && fiscal && (
          <>
            <h1 className="titulo">Tudo pronto para emitir</h1>
            <p className="subtitulo">Revise e conclua. Tudo pode ser alterado depois em Configurações.</p>
            <div className="folha" style={{ marginTop: 24 }}>
              <dl className="dl">
                <dt>Certificado</dt>
                <dd>
                  {cert?.titular} <span className="num">{cert?.cnpj && formatarDocumento(cert.cnpj)}</span>
                </dd>
                <dt>Prestador</dt>
                <dd className="num">{formatarDocumento(fiscal.cnpj)}</dd>
                <dt>Serviço</dt>
                <dd>
                  <span className="num">{fiscal.c_trib_nac}</span> · NBS <span className="num">{fiscal.c_nbs ?? "—"}</span>
                </dd>
                <dt>Ambiente</dt>
                <dd>{ambiente === "1" ? "Produção" : "Homologação"}</dd>
                <dt>Numeração</dt>
                <dd>
                  Série <span className="num">{serie}</span>, próxima DPS nº <span className="num">{proximo}</span>
                </dd>
                <dt>Tabela</dt>
                <dd>
                  {usarLinhas && importacao?.linhas.length
                    ? `${importacao.linhas.length} linhas · total ${moeda(importacao.linhas.reduce((a, l) => a + Number(l.valor || 0), 0))}`
                    : "Vazia"}
                </dd>
              </dl>
            </div>
            <div style={{ marginTop: 16 }}>
              <ErroCaixa erro={erro} titulo="Não foi possível concluir" />
            </div>
            <div className="onb__acoes">
              <button className="btn btn--grande" onClick={() => setPasso(3)}>
                Voltar
              </button>
              <button className="btn btn--primario btn--grande" disabled={ocupado} onClick={concluir}>
                {ocupado && <Spinner />} Concluir configuração
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
