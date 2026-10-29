// Aceite da cobrança no fim do teste grátis (só na hospedagem): faixa nos últimos dias do teste, diálogo de
// confirmação e "Continuar e assinar" depois que o teste acabou sem aceite (instância suspensa).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { Dialogo, ErroCaixa, Spinner, useToast } from "../ui";

export interface SituacaoAssinatura {
  disponivel: boolean;
  situacao?: "sem_assinatura" | "teste" | "ativa" | "teste_encerrado" | "suspensa";
  perguntar?: boolean;
  confirmacao?: "marcar" | "pendente" | "aceita" | "recusada" | null;
  teste_ate?: string | null;
  suspensa_desde?: string | null;
  dias_ate_parar?: number;
  valor?: string;
  cartao?: string | null;
  pode_confirmar: boolean;
}

/** A faixa aparece nos últimos 3 dias do teste; o diálogo abre sozinho (uma vez por dia) nas últimas 24 h. */
const FAIXA_HORAS = 72;
const DIALOGO_HORAS = 24;

export function useAssinatura() {
  return useQuery({
    queryKey: ["assinatura"],
    queryFn: () => api.get<SituacaoAssinatura>("/api/assinatura"),
    staleTime: 60_000,
    refetchInterval: 10 * 60_000,
    retry: false,
  });
}

function dia(d: Date): string {
  return d.toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" });
}

function hora(d: Date): string {
  return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

/** "hoje às 08:58", "amanhã às 08:58" ou "em 3 dias (sex., 31/10)". */
export function quandoTermina(iso: string, agora = new Date()): string {
  const fim = new Date(iso);
  const meiaNoite = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const dias = Math.round((meiaNoite(fim) - meiaNoite(agora)) / 86_400_000);
  if (dias <= 0) return `hoje às ${hora(fim)}`;
  if (dias === 1) return `amanhã às ${hora(fim)}`;
  return `em ${dias} dias (${dia(fim)}, às ${hora(fim)})`;
}

/** "R$ 59,90", sem quebrar a linha entre o símbolo e o número. */
function valorCurto(s: SituacaoAssinatura): string {
  return (s.valor ?? "").replace(/ por .*/, "").replace(" ", "\u00a0");
}

function disponivelAte(s: SituacaoAssinatura): string | null {
  if (!s.suspensa_desde) return null;
  const d = new Date(new Date(s.suspensa_desde).getTime() + (s.dias_ate_parar ?? 15) * 86_400_000);
  return d.toLocaleDateString("pt-BR");
}

function horasRestantes(s: SituacaoAssinatura): number {
  return s.teste_ate ? (new Date(s.teste_ate).getTime() - Date.now()) / 3_600_000 : Infinity;
}

/** Faixa abaixo da do ambiente. Também abre o diálogo de confirmação. */
export function FaixaAssinatura() {
  const q = useAssinatura();
  const [dialogo, setDialogo] = useState<"confirmar" | "nao" | null>(null);
  const s = q.data;
  const noTeste = !!s?.disponivel && s.situacao === "teste" && !!s.perguntar && !!s.teste_ate;
  const encerrado = !!s?.disponivel && s.situacao === "teste_encerrado";
  const horas = s ? horasRestantes(s) : Infinity;

  useEffect(() => {
    // Nas últimas 24 h, o administrador vê a pergunta ao abrir o sistema (uma vez por dia neste navegador).
    if (!noTeste || !s?.pode_confirmar || horas > DIALOGO_HORAS || s.confirmacao === "recusada") return;
    const chave = `emissor:assinatura-perguntada:${new Date().toDateString()}`;
    try {
      if (localStorage.getItem(chave)) return;
      localStorage.setItem(chave, "1");
    } catch {
      /* sem armazenamento: pergunta a cada abertura */
    }
    setDialogo("confirmar");
  }, [noTeste, s?.pode_confirmar, s?.confirmacao, horas]);

  if (!s || (!encerrado && !(noTeste && horas <= FAIXA_HORAS))) return null;
  const recusou = s.confirmacao === "recusada";

  return (
    <>
      <div className={`faixa-assinatura ${encerrado ? "faixa-assinatura--encerrado" : ""}`} role={encerrado ? "alert" : "note"}>
        <p>
          {encerrado ? (
            <>
              <strong>O teste grátis terminou sem a confirmação da assinatura.</strong> A emissão está bloqueada; as notas
              emitidas continuam disponíveis para consulta e download
              {disponivelAte(s) ? ` até ${disponivelAte(s)}` : ""}.
            </>
          ) : recusou ? (
            <>
              <strong>Você indicou que não vai continuar.</strong> O teste grátis termina {quandoTermina(s.teste_ate!)}; depois
              disso a emissão fica bloqueada e nada é cobrado.
            </>
          ) : (
            <>
              <strong>Seu teste grátis termina {quandoTermina(s.teste_ate!)}.</strong> Para continuar emitindo, confirme a
              assinatura de {s.valor}. Sem a confirmação, nada é cobrado e a emissão fica bloqueada.
            </>
          )}
        </p>
        {s.pode_confirmar ? (
          <div className="faixa-assinatura__acoes">
            <button className="btn btn--pequeno btn--primario" onClick={() => setDialogo("confirmar")}>
              {encerrado ? "Continuar e assinar" : recusou ? "Quero continuar" : "Continuar com a assinatura"}
            </button>
            {!encerrado && !recusou && (
              <button className="btn btn--pequeno btn--fantasma" onClick={() => setDialogo("nao")}>
                Não vou continuar
              </button>
            )}
          </div>
        ) : (
          <span className="faixa-assinatura__dica">Peça a um administrador para confirmar a assinatura.</span>
        )}
      </div>
      {dialogo === "confirmar" && <DialogoConfirmar s={s} fechar={() => setDialogo(null)} />}
      {dialogo === "nao" && <DialogoNaoContinuar s={s} fechar={() => setDialogo(null)} />}
    </>
  );
}

function DialogoConfirmar({ s, fechar }: { s: SituacaoAssinatura; fechar: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [acordo, setAcordo] = useState(false);
  const [ocupado, setOcupado] = useState(false);
  const [erro, setErro] = useState<unknown>(null);
  const [reativando, setReativando] = useState(false);
  const encerrado = s.situacao === "teste_encerrado";
  const valor = valorCurto(s);
  const cartao = s.cartao ?? "o cartão cadastrado na assinatura";

  async function confirmar() {
    setOcupado(true);
    setErro(null);
    try {
      const r = await api.post<{ ok: boolean; reativar?: boolean }>("/api/assinatura/confirmar");
      if (r.reativar) {
        setReativando(true);
        await aguardarReativacao();
        location.reload();
        return;
      }
      toast("Assinatura confirmada. Obrigado por continuar com o Emissor em Planilha.");
      await qc.invalidateQueries({ queryKey: ["assinatura"] });
      fechar();
    } catch (e) {
      setErro(e);
    } finally {
      setOcupado(false);
    }
  }

  const recusado = erro instanceof ApiError && erro.corpo?.cartao_recusado === true;
  const fecharSeLivre = ocupado || reativando ? undefined : fechar;

  if (reativando)
    return (
      <Dialogo titulo="Pagamento aprovado">
        <p className="assinatura-reativando">
          <Spinner /> Reativando o seu emissor. Isso leva menos de um minuto e a página recarrega sozinha.
        </p>
      </Dialogo>
    );

  return (
    <Dialogo
      titulo={encerrado ? "Continuar e assinar" : "Continuar com a assinatura"}
      fechar={fecharSeLivre}
      rodape={
        <>
          <button className="btn" onClick={fechar} disabled={ocupado}>
            Agora não
          </button>
          <button className="btn btn--primario" onClick={confirmar} disabled={!acordo || ocupado}>
            {ocupado && <Spinner />} Confirmar e pagar {valor}
          </button>
        </>
      }
    >
      <dl className="assinatura-resumo">
        <div>
          <dt>Cobrança agora</dt>
          <dd className="num">{valor}</dd>
        </div>
        <div>
          <dt>Cartão</dt>
          <dd>{cartao}</dd>
        </div>
        <div>
          <dt>Próximas cobranças</dt>
          <dd>
            {valor} todo dia {new Date().getDate()}, automaticamente
          </dd>
        </div>
      </dl>
      <p className="assinatura-texto">
        {encerrado
          ? "A emissão volta a funcionar assim que o pagamento for aprovado. As notas, os clientes e as configurações continuam como estavam."
          : "O teste grátis termina agora e o sistema continua funcionando sem interrupção."}{" "}
        Você pode cancelar quando quiser no portal da assinatura, sem multa nem fidelidade.
      </p>
      <label className="assinatura-acordo">
        <input type="checkbox" checked={acordo} onChange={(e) => setAcordo(e.target.checked)} disabled={ocupado} />
        Estou de acordo com a cobrança de {valor} agora e com a renovação automática todo mês.
      </label>
      {recusado ? (
        <div className="erro-caixa" role="alert">
          <div>
            <strong>{(erro as ApiError).message}</strong>
            Troque o cartão em Minha conta, no botão Gerenciar assinatura, e tente de novo.
          </div>
        </div>
      ) : (
        <ErroCaixa erro={erro} titulo="A assinatura não foi confirmada" />
      )}
    </Dialogo>
  );
}

/** A instância reinicia sem a suspensão: espera o /api/saude dizer que voltou. */
async function aguardarReativacao(): Promise<void> {
  const limite = Date.now() + 3 * 60_000;
  while (Date.now() < limite) {
    await new Promise((r) => setTimeout(r, 3000));
    try {
      const r = await fetch("/api/saude", { cache: "no-store" });
      if (r.ok && (await r.json()).suspenso === false) return;
    } catch {
      /* reiniciando */
    }
  }
}

function DialogoNaoContinuar({ s, fechar }: { s: SituacaoAssinatura; fechar: () => void }) {
  const qc = useQueryClient();
  const [ocupado, setOcupado] = useState(false);
  const [erro, setErro] = useState<unknown>(null);

  async function recusar() {
    setOcupado(true);
    setErro(null);
    try {
      await api.post("/api/assinatura/recusar");
      await qc.invalidateQueries({ queryKey: ["assinatura"] });
      fechar();
    } catch (e) {
      setErro(e);
    } finally {
      setOcupado(false);
    }
  }

  return (
    <Dialogo
      titulo="Não continuar com a assinatura"
      fechar={ocupado ? undefined : fechar}
      rodape={
        <>
          <button className="btn" onClick={fechar} disabled={ocupado}>
            Voltar
          </button>
          <button className="btn btn--perigo" onClick={recusar} disabled={ocupado}>
            {ocupado && <Spinner />} Não vou continuar
          </button>
        </>
      }
    >
      <p className="assinatura-texto">
        Nada é cobrado. O sistema funciona normalmente até o fim do teste, {quandoTermina(s.teste_ate!)}. Depois, a emissão
        fica bloqueada e as notas emitidas ficam disponíveis para consulta e download por {s.dias_ate_parar ?? 15} dias.
      </p>
      <p className="assinatura-texto">
        Se mudar de ideia, é só clicar em <strong>Continuar e assinar</strong> no aviso do topo, a qualquer momento: a
        cobrança usa o mesmo cartão e a emissão volta na hora.
      </p>
      <ErroCaixa erro={erro} />
    </Dialogo>
  );
}
