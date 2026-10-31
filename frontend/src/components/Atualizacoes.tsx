// Ícone de atualizações no topo: versão instalada e linha do tempo das versões (as novas em cima). Fica azul, com um
// ponto, quando há versão nova. Na hospedagem o painel atualiza sozinho e o ícone nunca acende. Na instalação pelo
// iniciar.bat o administrador atualiza por aqui ou liga a atualização automática ao iniciar; no Docker, o comando.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { copiar, ErroCaixa, Spinner, useToast } from "../ui";

interface Versao {
  versao: string;
  data: string | null;
  itens: string[];
  nova: boolean;
  atual: boolean;
}

export interface SituacaoAtualizacoes {
  versao_atual: string;
  instalacao: "hospedagem" | "iniciar" | "docker" | "manual";
  versoes: Versao[];
  ultima: string;
  novas: number;
  erro: string | null;
  url_versoes: string;
  automatica: boolean;
  pode_atualizar: boolean;
  atualizando: boolean;
  resultado: { ok: boolean; de: string; para: string; mensagem: string; quando: string } | null;
}

const COMANDO_DOCKER = "git pull\ndocker compose up -d";

function dataCurta(iso: string | null): string {
  if (!iso) return "";
  const [a, m, d] = iso.split("-");
  return `${d}/${m}/${a}`;
}

function SetaAcima() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
      <path
        d="M10 15.5V4.8M5.2 9.4 10 4.6l4.8 4.8"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** O servidor fecha, o iniciar.bat atualiza e abre de novo: espera o /api/saude voltar e recarrega a página. */
async function aguardarNovaVersao(de: string, espera = 6 * 60_000): Promise<boolean> {
  const limite = Date.now() + espera;
  let caiu = false;
  while (Date.now() < limite) {
    await new Promise((r) => setTimeout(r, 3000));
    try {
      const r = await fetch("/api/saude", { cache: "no-store" });
      if (!r.ok) {
        caiu = true;
        continue;
      }
      const v = (await r.json()).versao as string | undefined;
      // Versão nova no ar, ou o sistema voltou na mesma versão (a atualização falhou: a janela mostra o motivo).
      if ((v && v !== de) || caiu) return true;
    } catch {
      caiu = true;
    }
  }
  return false;
}

function Atualizando({ de, para }: { de: string; para: string }) {
  const [demorou, setDemorou] = useState(false);
  useEffect(() => {
    let vivo = true;
    aguardarNovaVersao(de).then((voltou) => {
      if (!vivo) return;
      if (voltou) location.reload();
      else setDemorou(true);
    });
    return () => {
      vivo = false;
    };
  }, [de]);
  return (
    <div className="veu" role="alertdialog" aria-live="polite" aria-label="Atualizando o sistema">
      <div className="dialogo atualizando">
        {demorou ? (
          <>
            <h2>A atualização está demorando</h2>
            <p>
              Confira a janela do <code>iniciar.bat</code> no computador que serve o sistema. Se ela tiver fechado, abra
              o <code>iniciar.bat</code> de novo.
            </p>
            <button className="btn" onClick={() => location.reload()}>
              Recarregar a página
            </button>
          </>
        ) : (
          <>
            <Spinner />
            <h2>
              Atualizando para a versão <span className="num">{para}</span>
            </h2>
            <p>
              O sistema fecha, baixa a versão nova e abre de novo. Leva de 1 a 2 minutos; esta página recarrega sozinha.
            </p>
          </>
        )}
      </div>
    </div>
  );
}

function Rodape({
  s,
  aoAtualizar,
}: {
  s: SituacaoAtualizacoes;
  aoAtualizar: (d: { de: string; para: string }) => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [ocupado, setOcupado] = useState(false);
  const [erro, setErro] = useState<unknown>(null);

  async function atualizar() {
    setOcupado(true);
    setErro(null);
    try {
      aoAtualizar(await api.post<{ de: string; para: string }>("/api/atualizacoes/aplicar"));
    } catch (e) {
      setErro(e);
      setOcupado(false);
    }
  }

  async function automatica(ligar: boolean) {
    setErro(null);
    try {
      await api.put("/api/atualizacoes/automatica", { automatica: ligar });
      await qc.invalidateQueries({ queryKey: ["atualizacoes"] });
    } catch (e) {
      setErro(e);
    }
  }

  const r = s.resultado;
  const resultadoRecente = r && Date.now() - new Date(r.quando).getTime() < 7 * 86_400_000;

  if (s.instalacao === "hospedagem") return null;
  return (
    <div className="att-rodape">
      {resultadoRecente && !r.ok && (
        <p className="att-rodape__falha">
          A atualização para a <span className="num">{r.para}</span> não foi feita: {r.mensagem}
        </p>
      )}
      {s.instalacao === "iniciar" && (
        <>
          {s.novas > 0 &&
            (s.pode_atualizar ? (
              <div className="att-rodape__acao">
                <span>O sistema fecha, atualiza e abre de novo em 1 a 2 minutos. Seus dados e notas continuam.</span>
                <button className="btn btn--pequeno btn--primario" onClick={atualizar} disabled={ocupado}>
                  {ocupado && <Spinner />} Atualizar para <span className="num">{s.ultima}</span>
                </button>
              </div>
            ) : (
              <span>Peça a um administrador para atualizar.</span>
            ))}
          {s.pode_atualizar && (
            <label className="att-rodape__auto">
              <input type="checkbox" checked={s.automatica} onChange={(e) => automatica(e.target.checked)} />
              Atualizar automaticamente ao iniciar
            </label>
          )}
        </>
      )}
      {s.instalacao === "docker" && s.novas > 0 && (
        <div className="att-rodape__cmd">
          <span>Para atualizar, rode na pasta do sistema:</span>
          <pre className="num">{COMANDO_DOCKER}</pre>
          <button
            className="btn btn--pequeno"
            onClick={() => copiar(COMANDO_DOCKER).then(() => toast("Comandos copiados", "ok"))}
          >
            Copiar
          </button>
        </div>
      )}
      {s.instalacao === "manual" && s.novas > 0 && (
        <span>
          Para atualizar, baixe a versão nova (ou rode <code>git pull</code>) e abra o sistema de novo.
        </span>
      )}
      {s.erro && <span className="att-rodape__nota">{s.erro}</span>}
      {erro != null && <ErroCaixa erro={erro} />}
    </div>
  );
}

export function Atualizacoes() {
  const [aberto, setAberto] = useState(false);
  const [atualizando, setAtualizando] = useState<{ de: string; para: string } | null>(null);
  const caixa = useRef<HTMLSpanElement>(null);
  const q = useQuery({
    queryKey: ["atualizacoes"],
    queryFn: () => api.get<SituacaoAtualizacoes>("/api/atualizacoes"),
    staleTime: 10 * 60_000,
    refetchInterval: 6 * 3600_000,
    retry: false,
  });

  useEffect(() => {
    if (!aberto) return;
    const fora = (e: MouseEvent) => {
      if (caixa.current && !caixa.current.contains(e.target as Node)) setAberto(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setAberto(false);
    document.addEventListener("mousedown", fora);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", fora);
      document.removeEventListener("keydown", esc);
    };
  }, [aberto]);

  const s = q.data;
  if (!s) return null;
  const nova = s.novas > 0;
  const titulo = nova
    ? `Versão ${s.ultima} disponível (você está na ${s.versao_atual})`
    : `Versão ${s.versao_atual}: novidades`;

  return (
    <span className="att" ref={caixa}>
      {atualizando && <Atualizando {...atualizando} />}
      <button
        className={`ic-att ${nova ? "ic-att--nova" : ""}`}
        title={titulo}
        aria-label={titulo}
        aria-haspopup="dialog"
        aria-expanded={aberto}
        onClick={() => setAberto((a) => !a)}
      >
        <SetaAcima />
        {nova && <span className="ic-att__ponto" />}
      </button>
      {aberto && (
        <section className="att-pop" role="dialog" aria-label="Atualizações">
          <div className="att-pop__cab">
            <h2>Atualizações</h2>
            <p>
              {nova ? (
                <>
                  {s.novas === 1 ? "1 versão nova" : `${s.novas} versões novas`} desde a sua (
                  <span className="num">{s.versao_atual}</span>).
                </>
              ) : s.instalacao === "hospedagem" ? (
                <>
                  Versão <span className="num">{s.versao_atual}</span>, a mais recente. A hospedagem instala as novas
                  sozinha.
                </>
              ) : (
                <>
                  Você está na versão mais recente (<span className="num">{s.versao_atual}</span>).
                </>
              )}
            </p>
          </div>
          <ol className="lt">
            {s.versoes.map((v) => (
              <li
                key={v.versao}
                className={`lt__item ${v.atual ? "lt__item--atual" : v.nova ? "lt__item--nova" : "lt__item--antiga"}`}
              >
                <div className="lt__cab">
                  <span className="num lt__n">{v.versao}</span>
                  {v.atual && <span className="lt__selo">Sua versão</span>}
                  <span className="lt__d">{dataCurta(v.data)}</span>
                </div>
                {v.itens.length > 0 ? (
                  <ul>
                    {v.itens.map((t) => (
                      <li key={t}>{t}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="lt__vazio">
                    <a href={`${s.url_versoes}/tag/v${v.versao}`} target="_blank" rel="noreferrer">
                      Ver o que mudou no GitHub
                    </a>
                  </p>
                )}
              </li>
            ))}
          </ol>
          <Rodape
            s={s}
            aoAtualizar={(d) => {
              setAberto(false);
              setAtualizando(d);
            }}
          />
        </section>
      )}
    </span>
  );
}
