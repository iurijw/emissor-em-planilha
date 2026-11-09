import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import {
  api,
  trocarEmpresa,
  type CertificadoGuardado,
  type CertificadoInfo,
  type Empresa,
  type ListaEmpresas,
  type Status,
  type Usuario,
} from "../api";
import { formatarDocumento } from "../format";
import { ZonaArquivo } from "../pages/Onboarding";
import { Dialogo, ErroCaixa, Spinner, useToast } from "../ui";

/** Nome da empresa desta aba (o curto, se definido; senão o CNPJ). */
export function nomeEmpresa(status: Status): string {
  return status.empresa.nome || (status.empresa.cnpj ? formatarDocumento(status.empresa.cnpj) : "Empresa");
}

export function useEmpresas() {
  return useQuery({ queryKey: ["empresas"], queryFn: () => api.get<ListaEmpresas>("/api/empresas"), staleTime: 30_000 });
}

function Borda({ ambiente }: { ambiente: string }) {
  // A borda do crachá repete a faixa do ambiente: hachurada = homologação, preta = produção.
  return <span className={`cracha__borda ${ambiente === "1" ? "cracha__borda--prod" : ""}`} aria-hidden />;
}

function linhaDoc(e: Empresa) {
  return e.cnpj ? formatarDocumento(e.cnpj) : "sem CNPJ";
}

/** Crachá da empresa no topo: qual empresa esta aba emite, e a lista para trocar. Some com uma empresa só. */
export function SeletorEmpresa({ admin }: { admin: boolean }) {
  const lista = useEmpresas();
  const [aberto, setAberto] = useState(false);
  const [busca, setBusca] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!aberto) return;
    const fora = (ev: MouseEvent) => !ref.current?.contains(ev.target as Node) && setAberto(false);
    const esc = (ev: KeyboardEvent) => ev.key === "Escape" && setAberto(false);
    document.addEventListener("mousedown", fora);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", fora);
      document.removeEventListener("keydown", esc);
    };
  }, [aberto]);
  const d = lista.data;
  if (!d || d.empresas.length < 2) return null;
  const atual = d.empresas.find((e) => e.id === d.atual) ?? d.empresas[0];
  const termo = busca.trim().toLowerCase();
  const filtradas = termo
    ? d.empresas.filter((e) => e.nome.toLowerCase().includes(termo) || (e.cnpj || "").includes(termo.replace(/\D/g, "") || "§"))
    : d.empresas;
  return (
    <div className="cracha" ref={ref}>
      <button className="cracha__botao" aria-haspopup="listbox" aria-expanded={aberto} onClick={() => setAberto((v) => !v)} title="Trocar de empresa">
        <Borda ambiente={atual.ambiente} />
        <span className="cracha__texto">
          <span className="cracha__nome">{atual.nome}</span>
          <span className="cracha__doc num">{linhaDoc(atual)}</span>
        </span>
        <span className="cracha__seta" aria-hidden>▾</span>
      </button>
      {aberto && (
        <div className="cracha__lista" role="listbox" aria-label="Empresas">
          {d.empresas.length > 6 && (
            <input
              className="cracha__busca"
              autoFocus
              placeholder="Buscar por nome ou CNPJ"
              value={busca}
              onChange={(e) => setBusca(e.target.value)}
            />
          )}
          {filtradas.map((e) => (
            <button
              key={e.id}
              role="option"
              aria-selected={e.id === atual.id}
              className="cracha__item"
              onClick={() => (e.id === atual.id ? setAberto(false) : trocarEmpresa(e.id))}
            >
              <Borda ambiente={e.ambiente} />
              <span className="cracha__texto">
                <span className="cracha__nome">{e.nome}</span>
                <span className="cracha__doc">
                  <span className="num">{linhaDoc(e)}</span>
                  {e.acesso === "procuracao" && <span className="cracha__marca">por procuração</span>}
                  {!e.onboarding_concluido && <span className="cracha__marca">configurar</span>}
                </span>
              </span>
              <span className="cracha__amb">{e.ambiente === "1" ? "Produção" : "Homologação"}</span>
            </button>
          ))}
          {filtradas.length === 0 && <p className="cracha__vazio">Nenhuma empresa com “{busca}”.</p>}
          {admin && (
            <a className="cracha__gerenciar" href="#/configuracoes?secao=empresas" onClick={() => setAberto(false)}>
              Gerenciar empresas
            </a>
          )}
        </div>
      )}
    </div>
  );
}

// --- Configurações → Empresas -------------------------------------------------------------------------------

export function SecaoEmpresas({ empresaAtual }: { empresaAtual: number }) {
  const lista = useEmpresas();
  const qc = useQueryClient();
  const toast = useToast();
  const [adicionar, setAdicionar] = useState(false);
  const [editando, setEditando] = useState<Empresa | null>(null);
  const [desativar, setDesativar] = useState<Empresa | null>(null);
  const [conferencia, setConferencia] = useState<Record<number, { ok: boolean; mensagem: string } | "conferindo">>({});
  const [erro, setErro] = useState<unknown>(null);
  const d = lista.data;
  if (!d) return <ErroCaixa erro={lista.error} titulo="Não foi possível carregar as empresas" />;

  async function conferir(e: Empresa) {
    setConferencia((c) => ({ ...c, [e.id]: "conferindo" }));
    try {
      const r = await api.post<{ ok: boolean; mensagem: string }>(`/api/empresas/${e.id}/conferir-procuracao`);
      setConferencia((c) => ({ ...c, [e.id]: r }));
    } catch (x) {
      setConferencia((c) => ({ ...c, [e.id]: { ok: false, mensagem: x instanceof Error ? x.message : String(x) } }));
    }
  }

  async function reativar(id: number) {
    setErro(null);
    try {
      await api.post(`/api/empresas/${id}/reativar`);
      toast("Empresa reativada.");
      await qc.invalidateQueries({ queryKey: ["empresas"] });
    } catch (x) {
      setErro(x);
    }
  }

  return (
    <section className="folha" id="empresas">
      <h2 className="secao-titulo">Empresas</h2>
      <p className="secao-texto">
        Cada empresa emite com o próprio CNPJ, numeração e tributações. Ela pode usar o certificado A1 dela ou o certificado do
        escritório, por procuração eletrônica.
      </p>
      <ul className="empresas">
        {d.empresas.map((e) => {
          const c = conferencia[e.id];
          return (
            <li key={e.id} className="empresas__item">
              <Borda ambiente={e.ambiente} />
              <div className="empresas__corpo">
                <div className="empresas__linha">
                  <strong>{e.nome}</strong>
                  {e.id === empresaAtual && <span className="empresas__aqui">aberta nesta aba</span>}
                </div>
                <div className="empresas__meta">
                  <span className="num">{linhaDoc(e)}</span>
                  <span>{e.ambiente === "1" ? "Produção" : "Homologação"}</span>
                  <span>
                    {e.acesso === "procuracao"
                      ? `Por procuração: certificado de ${e.certificado?.titular ?? "?"}`
                      : e.certificado
                        ? `Certificado da empresa até ${new Date(e.certificado.valido_ate).toLocaleDateString("pt-BR")}`
                        : "Sem certificado"}
                  </span>
                  {!e.onboarding_concluido && <span className="empresas__pendente">configuração inicial pendente</span>}
                </div>
                {c && c !== "conferindo" && <div className={c.ok ? "info-caixa" : "aviso"} style={{ marginTop: 8 }}>{c.mensagem}</div>}
              </div>
              <div className="empresas__acoes">
                {e.id !== empresaAtual && (
                  <button className="btn btn--pequeno" onClick={() => trocarEmpresa(e.id)}>
                    {e.onboarding_concluido ? "Abrir" : "Configurar"}
                  </button>
                )}
                {e.acesso === "procuracao" && e.id === empresaAtual && (
                  <button className="btn btn--pequeno" disabled={c === "conferindo"} onClick={() => conferir(e)}>
                    {c === "conferindo" && <Spinner />} Conferir procuração
                  </button>
                )}
                <button className="btn btn--pequeno btn--fantasma" onClick={() => setEditando(e)}>Editar</button>
                {d.empresas.length > 1 && (
                  <button className="btn btn--pequeno btn--fantasma" onClick={() => setDesativar(e)}>Desativar</button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      <ErroCaixa erro={erro} />
      {d.pode_adicionar ? (
        <button className="btn btn--primario" style={{ marginTop: 14 }} onClick={() => setAdicionar(true)}>
          Adicionar empresa
        </button>
      ) : (
        d.limite !== null && <p className="secao-texto">Limite de {d.limite} empresa(s) nesta instalação.</p>
      )}
      {d.desativadas.length > 0 && (
        <details className="empresas__desativadas">
          <summary>Desativadas ({d.desativadas.length})</summary>
          <ul>
            {d.desativadas.map((x) => (
              <li key={x.id}>
                {x.nome} <span className="num">{x.cnpj ? formatarDocumento(x.cnpj) : ""}</span>{" "}
                <button className="btn-texto" onClick={() => reativar(x.id)}>Reativar</button>
              </li>
            ))}
          </ul>
        </details>
      )}
      <CompartilharClientes compartilhar={d.compartilhar_clientes} empresas={d.empresas} />
      {adicionar && <AdicionarEmpresa fechar={() => setAdicionar(false)} />}
      {editando && <EditarEmpresa empresa={editando} fechar={() => setEditando(null)} />}
      {desativar && <DesativarEmpresa empresa={desativar} fechar={() => setDesativar(null)} />}
    </section>
  );
}

function EditarEmpresa({ empresa, fechar }: { empresa: Empresa; fechar: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [nome, setNome] = useState(empresa.nome_definido ?? "");
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);
  const certs = useQuery({ queryKey: ["certificados"], queryFn: () => api.get<CertificadoGuardado[]>("/api/certificados") });
  const [certId, setCertId] = useState<number | null>(empresa.certificado?.id ?? null);

  async function salvar() {
    setErro(null);
    setOcupado(true);
    try {
      if (nome !== (empresa.nome_definido ?? "")) await api.put(`/api/empresas/${empresa.id}`, { nome });
      if (certId && certId !== empresa.certificado?.id) await api.post(`/api/empresas/${empresa.id}/usar-certificado`, { certificado_id: certId });
      toast("Empresa atualizada.");
      await qc.invalidateQueries();
      fechar();
    } catch (x) {
      setErro(x);
    } finally {
      setOcupado(false);
    }
  }

  return (
    <Dialogo
      titulo={`Editar ${empresa.nome}`}
      fechar={fechar}
      rodape={
        <>
          <button className="btn" onClick={fechar}>Cancelar</button>
          <button className="btn btn--primario" disabled={ocupado} onClick={salvar}>{ocupado && <Spinner />} Salvar</button>
        </>
      }
    >
      <label className="campo">
        <span>Nome curto (aparece no seletor de empresa)</span>
        <input value={nome} maxLength={80} placeholder={empresa.nome} onChange={(e) => setNome(e.target.value)} />
      </label>
      <label className="campo" style={{ marginTop: 14 }}>
        <span>Certificado usado para emitir</span>
        <select value={certId ?? ""} onChange={(e) => setCertId(Number(e.target.value) || null)}>
          {(certs.data ?? []).map((c) => (
            <option key={c.id} value={c.id} disabled={c.vencido}>
              {c.titular} ({c.cnpj ? formatarDocumento(c.cnpj) : c.cpf ? formatarDocumento(c.cpf) : "?"})
              {c.cnpj === empresa.cnpj ? " · da própria empresa" : " · por procuração"}
              {c.vencido ? " · vencido" : ""}
            </option>
          ))}
        </select>
      </label>
      <p className="secao-texto">
        Um certificado de outro CNPJ ou CPF só emite pela empresa com procuração eletrônica para a NFS-e. Para enviar um certificado
        novo da própria empresa, use Configurações → Certificado digital com a empresa aberta.
      </p>
      <ErroCaixa erro={erro} />
    </Dialogo>
  );
}

function DesativarEmpresa({ empresa, fechar }: { empresa: Empresa; fechar: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);
  async function confirmar() {
    setOcupado(true);
    try {
      await api.post(`/api/empresas/${empresa.id}/desativar`);
      toast("Empresa desativada.");
      await qc.invalidateQueries();
      fechar();
    } catch (x) {
      setErro(x);
    } finally {
      setOcupado(false);
    }
  }
  return (
    <Dialogo
      titulo={`Desativar ${empresa.nome}?`}
      fechar={fechar}
      rodape={
        <>
          <button className="btn" onClick={fechar}>Cancelar</button>
          <button className="btn btn--perigo" disabled={ocupado} onClick={confirmar}>{ocupado && <Spinner />} Desativar</button>
        </>
      }
    >
      <p>
        A empresa sai do seletor e não emite mais. As notas já emitidas continuam guardadas e voltam a aparecer se você reativar a
        empresa. O certificado dela é apagado do servidor se nenhuma outra empresa o usa.
      </p>
      <ErroCaixa erro={erro} />
    </Dialogo>
  );
}

// --- Adicionar empresa: com o A1 dela ou por procuração ------------------------------------------------------

type Modo = "certificado" | "procuracao";

export function AdicionarEmpresa({ fechar }: { fechar: () => void }) {
  const [modo, setModo] = useState<Modo | null>(null);
  const certs = useQuery({ queryKey: ["certificados"], queryFn: () => api.get<CertificadoGuardado[]>("/api/certificados") });
  const [certId, setCertId] = useState<number | null>(null);
  const [certNovo, setCertNovo] = useState<(CertificadoInfo & { id: number }) | null>(null);
  const [arquivo, setArquivo] = useState<File | null>(null);
  const [senha, setSenha] = useState("");
  const [cnpj, setCnpj] = useState("");
  const [nome, setNome] = useState("");
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);
  const qc = useQueryClient();

  // Procuradores já guardados: certificados de outro CNPJ/CPF (o do escritório, normalmente).
  const procuradores = (certs.data ?? []).filter((c) => !c.vencido);

  async function enviarCertificado(): Promise<number> {
    if (!arquivo) throw new Error("Escolha o arquivo do certificado (.pfx ou .p12).");
    const fd = new FormData();
    fd.append("arquivo", arquivo);
    fd.append("senha", senha);
    const r = await api.post<CertificadoInfo & { id: number }>("/api/certificados", fd);
    setCertNovo(r);
    setSenha("");
    return r.id;
  }

  async function criar() {
    setErro(null);
    setOcupado(true);
    try {
      let id = modo === "procuracao" ? certId : certNovo?.id ?? null;
      if (!id) id = await enviarCertificado();
      const corpo = { certificado_id: id, cnpj: modo === "procuracao" ? cnpj : null, nome: nome || null };
      const emp = await api.post<Empresa>("/api/empresas", corpo);
      await qc.invalidateQueries({ queryKey: ["empresas"] });
      trocarEmpresa(emp.id); // abre a empresa nova, que começa pela configuração inicial
    } catch (x) {
      setErro(x);
    } finally {
      setOcupado(false);
    }
  }

  const precisaArquivo = modo === "certificado" ? !certNovo : !certId;
  const pronto =
    modo !== null &&
    (precisaArquivo ? Boolean(arquivo && senha) : true) &&
    (modo === "certificado" || cnpj.replace(/\W/g, "").length === 14);

  return (
    <Dialogo
      largo
      titulo="Adicionar empresa"
      fechar={fechar}
      rodape={
        <>
          <button className="btn" onClick={modo ? () => setModo(null) : fechar}>{modo ? "Voltar" : "Cancelar"}</button>
          {modo && (
            <button className="btn btn--primario" disabled={!pronto || ocupado} onClick={criar}>
              {ocupado && <Spinner />} Adicionar e configurar
            </button>
          )}
        </>
      }
    >
      {!modo && (
        <div className="modos">
          <button className="modo" onClick={() => setModo("certificado")}>
            <strong>Com o certificado A1 da empresa</strong>
            <span>Envie o arquivo .pfx da empresa nova. O CNPJ vem do próprio certificado.</span>
          </button>
          <button className="modo" onClick={() => setModo("procuracao")}>
            <strong>Por procuração</strong>
            <span>
              Use o certificado do escritório (e-CNPJ ou e-CPF) para emitir pela empresa, com a procuração eletrônica que ela deu na
              Receita Federal. Não precisa do certificado da empresa.
            </span>
          </button>
        </div>
      )}

      {modo === "certificado" && (
        <div className="pilha">
          {certNovo ? (
            <div className="info-caixa">
              Certificado de {certNovo.titular} ({certNovo.cnpj ? formatarDocumento(certNovo.cnpj) : "sem CNPJ"}), válido até{" "}
              {new Date(certNovo.valido_ate).toLocaleDateString("pt-BR")}.
            </div>
          ) : (
            <CamposArquivo arquivo={arquivo} senha={senha} setArquivo={setArquivo} setSenha={setSenha} titulo="Certificado A1 da empresa" />
          )}
          <label className="campo">
            <span>Nome curto (opcional, aparece no seletor)</span>
            <input value={nome} maxLength={80} placeholder="Ex.: Filial Centro" onChange={(e) => setNome(e.target.value)} />
          </label>
        </div>
      )}

      {modo === "procuracao" && (
        <div className="pilha">
          <label className="campo">
            <span>CNPJ da empresa que vai emitir</span>
            <input className="num" inputMode="text" value={cnpj} placeholder="00.000.000/0000-00" onChange={(e) => setCnpj(e.target.value.toUpperCase())} />
          </label>
          <fieldset className="campo-grupo">
            <legend>Certificado do procurador</legend>
            {procuradores.map((c) => (
              <label key={c.id} className="opcao">
                <input type="radio" name="procurador" checked={certId === c.id} onChange={() => setCertId(c.id)} />
                <span>
                  {c.titular} <span className="num">{c.cnpj ? formatarDocumento(c.cnpj) : c.cpf ? formatarDocumento(c.cpf) : ""}</span>
                  <small>
                    válido até {new Date(c.valido_ate).toLocaleDateString("pt-BR")}
                    {c.empresas.length > 0 && ` · usado por ${c.empresas.length} empresa(s)`}
                  </small>
                </span>
              </label>
            ))}
            <label className="opcao">
              <input type="radio" name="procurador" checked={certId === null} onChange={() => setCertId(null)} />
              <span>Enviar outro certificado</span>
            </label>
          </fieldset>
          {certId === null && (
            <CamposArquivo arquivo={arquivo} senha={senha} setArquivo={setArquivo} setSenha={setSenha} titulo="Certificado do procurador (e-CNPJ ou e-CPF)" />
          )}
          <label className="campo">
            <span>Nome curto (opcional)</span>
            <input value={nome} maxLength={80} placeholder="Ex.: Padaria do João" onChange={(e) => setNome(e.target.value)} />
          </label>
          <details className="ajuda">
            <summary>Como dar a procuração</summary>
            <p>
              A empresa que vai emitir entra no e-CAC da Receita Federal e cadastra uma procuração eletrônica para o CNPJ ou CPF do
              certificado do procurador, com o serviço da NFS-e. Também vale o vínculo do contador no cadastro do CNPJ. Depois de
              adicionar a empresa, use “Conferir procuração” em Configurações → Empresas.
            </p>
          </details>
        </div>
      )}
      <ErroCaixa erro={erro} titulo="Não foi possível adicionar a empresa" />
    </Dialogo>
  );
}

function CamposArquivo(props: {
  arquivo: File | null;
  senha: string;
  setArquivo: (f: File | null) => void;
  setSenha: (s: string) => void;
  titulo: string;
}) {
  return (
    <>
      <ZonaArquivo
        aceitar=".pfx,.p12"
        titulo={props.arquivo ? props.arquivo.name : props.titulo}
        dica={props.arquivo ? "Clique para trocar o arquivo" : "Arquivo .pfx ou .p12"}
        onArquivos={(f) => props.setArquivo(f[0] ?? null)}
      />
      <label className="campo" style={{ maxWidth: 320 }}>
        <span>Senha do certificado</span>
        <input type="password" autoComplete="off" value={props.senha} onChange={(e) => props.setSenha(e.target.value)} />
      </label>
    </>
  );
}

// --- clientes compartilhados ----------------------------------------------------------------------------------

function CompartilharClientes({ compartilhar, empresas }: { compartilhar: boolean; empresas: Empresa[] }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [confirmar, setConfirmar] = useState(false);
  const [destino, setDestino] = useState<number>(empresas[0]?.id ?? 1);
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);
  const previa = useQuery({
    queryKey: ["compartilhar-clientes"],
    queryFn: () => api.get<{ compartilhar: boolean; clientes: number; repetidos: number }>("/api/instancia/clientes"),
    enabled: confirmar,
  });
  if (empresas.length < 2 && !compartilhar) return null;

  async function aplicar() {
    setErro(null);
    setOcupado(true);
    try {
      await api.put("/api/instancia/clientes", { compartilhar: !compartilhar, empresa_destino: destino });
      toast(compartilhar ? "Cada empresa voltou a ter os seus clientes." : "Clientes compartilhados entre as empresas.");
      await qc.invalidateQueries();
      setConfirmar(false);
    } catch (x) {
      setErro(x);
    } finally {
      setOcupado(false);
    }
  }

  return (
    <div className="compartilhar">
      <label className="opcao">
        <input type="checkbox" checked={compartilhar} onChange={() => setConfirmar(true)} />
        <span>
          Compartilhar o cadastro de clientes entre todas as empresas
          <small>
            {compartilhar
              ? "Todas as empresas usam o mesmo cadastro de clientes."
              : "Cada empresa tem os seus clientes (quem acessa uma empresa não vê os clientes das outras)."}
          </small>
        </span>
      </label>
      {confirmar && (
        <Dialogo
          titulo={compartilhar ? "Separar os clientes por empresa?" : "Compartilhar os clientes?"}
          fechar={() => setConfirmar(false)}
          rodape={
            <>
              <button className="btn" onClick={() => setConfirmar(false)}>Cancelar</button>
              <button className="btn btn--primario" disabled={ocupado} onClick={aplicar}>
                {ocupado && <Spinner />} {compartilhar ? "Separar" : "Compartilhar"}
              </button>
            </>
          }
        >
          {compartilhar ? (
            <>
              <p>Cada empresa recebe uma cópia dos clientes para quem já emitiu nota. Os outros clientes ficam com a empresa abaixo.</p>
              <label className="campo">
                <span>Clientes sem nota vão para</span>
                <select value={destino} onChange={(e) => setDestino(Number(e.target.value))}>
                  {empresas.map((e) => (
                    <option key={e.id} value={e.id}>{e.nome}</option>
                  ))}
                </select>
              </label>
            </>
          ) : (
            <>
              <p>
                Os cadastros de todas as empresas viram um só. O mesmo CNPJ/CPF em duas empresas vira um cliente: fica o cadastro
                atualizado por último, completado com o que faltar nos outros.
              </p>
              {previa.data && (
                <p>
                  <b className="num">{previa.data.clientes}</b> cliente(s), <b className="num">{previa.data.repetidos}</b> em mais de
                  uma empresa.
                </p>
              )}
              <div className="aviso">Quem tem acesso a qualquer empresa passa a ver todos os clientes.</div>
            </>
          )}
          <ErroCaixa erro={erro} />
        </Dialogo>
      )}
    </div>
  );
}

// --- empresas de cada usuário (tela Usuários) -----------------------------------------------------------------

export function EmpresasDoUsuario({ usuario, fechar }: { usuario: Usuario; fechar: () => void }) {
  const lista = useEmpresas();
  const qc = useQueryClient();
  const toast = useToast();
  const [sel, setSel] = useState<Record<number, "admin" | "operador" | "">>(
    Object.fromEntries(usuario.empresas.map((e) => [e.empresa_id, e.papel])),
  );
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);
  async function salvar() {
    setErro(null);
    setOcupado(true);
    try {
      const empresas = Object.entries(sel)
        .filter(([, p]) => p)
        .map(([id, papel]) => ({ empresa_id: Number(id), papel }));
      await api.put(`/api/usuarios/${usuario.id}/empresas`, { empresas });
      toast("Acesso às empresas atualizado.");
      await qc.invalidateQueries({ queryKey: ["usuarios"] });
      fechar();
    } catch (x) {
      setErro(x);
    } finally {
      setOcupado(false);
    }
  }
  return (
    <Dialogo
      titulo={`Empresas de ${usuario.nome || usuario.email}`}
      fechar={fechar}
      rodape={
        <>
          <button className="btn" onClick={fechar}>Cancelar</button>
          <button className="btn btn--primario" disabled={ocupado} onClick={salvar}>{ocupado && <Spinner />} Salvar</button>
        </>
      }
    >
      <table className="tabela">
        <thead>
          <tr>
            <th>Empresa</th>
            <th>Acesso</th>
          </tr>
        </thead>
        <tbody>
          {(lista.data?.empresas ?? []).map((e) => (
            <tr key={e.id}>
              <td>
                {e.nome} <span className="num" style={{ color: "var(--grafite)" }}>{linhaDoc(e)}</span>
              </td>
              <td>
                <select value={sel[e.id] ?? ""} onChange={(ev) => setSel((s) => ({ ...s, [e.id]: ev.target.value as "admin" | "operador" | "" }))}>
                  <option value="">Sem acesso</option>
                  <option value="operador">Operador</option>
                  <option value="admin">Administrador desta empresa</option>
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="secao-texto">
        O operador emite, cancela e baixa notas. O administrador da empresa também troca o certificado, as configurações e as
        tributações dela. Usuários e empresas só o administrador do sistema gerencia.
      </p>
      <ErroCaixa erro={erro} />
    </Dialogo>
  );
}
