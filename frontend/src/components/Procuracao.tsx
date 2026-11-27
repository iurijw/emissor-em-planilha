import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type AcessoEmpresa, type CertificadoInfo, type Status } from "../api";
import { cnpjValido, formatarDocumento, soDocumento } from "../format";
import { ErroCaixa, Spinner, useToast } from "../ui";

/** Documento do dono do certificado, para mostrar ("CNPJ 00.000…" ou "CPF 000…"). */
export function donoDoCertificado(info: CertificadoInfo): string {
  if (info.cnpj) return `CNPJ ${formatarDocumento(info.cnpj)}`;
  if (info.cpf) return `CPF ${formatarDocumento(info.cpf)}`;
  return "documento não identificado";
}

/** Forma de acesso que o certificado sugere: e-CPF só emite por procuração; e-CNPJ de outra empresa, também. */
export function acessoSugerido(info: CertificadoInfo, cnpjEmpresa: string | null | undefined): AcessoEmpresa {
  if (!info.cnpj) return "procuracao";
  const empresa = soDocumento(cnpjEmpresa ?? "");
  return empresa && empresa !== info.cnpj ? "procuracao" : "certificado";
}

/**
 * De quem é o certificado: da própria empresa ou de um procurador dela. Com procurador, pede o CNPJ da empresa
 * que vai emitir (o do certificado é outro, ou nem há, no e-CPF).
 */
export function EscolhaAcesso(props: {
  cert: CertificadoInfo;
  acesso: AcessoEmpresa;
  onAcesso: (a: AcessoEmpresa) => void;
  cnpjEmpresa: string;
  onCnpjEmpresa: (v: string) => void;
  permitida: boolean;
}) {
  const { cert, acesso, permitida } = props;
  const ecpf = !cert.cnpj;
  if (!permitida) {
    return ecpf ? (
      <div className="aviso">
        <span>
          Este certificado é de pessoa física (e-CPF). Aqui as notas só podem sair com o certificado A1 da própria empresa
          (e-CNPJ): envie o dela para continuar.
        </span>
      </div>
    ) : null;
  }
  const cnpj = soDocumento(props.cnpjEmpresa);
  const cnpjInvalido = cnpj.length === 14 && !cnpjValido(cnpj);
  const mesmoDoCertificado = Boolean(cnpj && cert.cnpj && cnpj === cert.cnpj);
  return (
    <fieldset className="acesso-escolha">
      <legend>De quem é este certificado?</legend>
      <div className="acesso-escolha__ops">
        <label className="acesso-op" data-desligada={ecpf || undefined}>
          <input
            type="radio"
            name="acesso"
            value="certificado"
            checked={acesso === "certificado"}
            disabled={ecpf}
            onChange={() => props.onAcesso("certificado")}
          />
          <strong>Da própria empresa</strong>
          <span>
            {ecpf
              ? "Um e-CPF é de uma pessoa, não da empresa: para ele, use a opção ao lado."
              : `O e-CNPJ da empresa que vai emitir as notas (${formatarDocumento(cert.cnpj)}).`}
          </span>
        </label>
        <label className="acesso-op">
          <input
            type="radio"
            name="acesso"
            value="procuracao"
            checked={acesso === "procuracao"}
            onChange={() => props.onAcesso("procuracao")}
          />
          <strong>De um procurador da empresa</strong>
          <span>Contador, escritório ou sócio com procuração eletrônica da empresa para a NFS-e.</span>
        </label>
      </div>
      {acesso === "procuracao" && (
        <div className="acesso-escolha__empresa">
          <label className="campo campo--obrigatorio" style={{ maxWidth: 320 }}>
            <span>CNPJ da empresa que vai emitir</span>
            <input
              className="num"
              inputMode="text"
              autoComplete="off"
              value={formatarDocumento(props.cnpjEmpresa)}
              aria-invalid={cnpjInvalido || mesmoDoCertificado || undefined}
              data-aria-invalid-proprio
              onChange={(e) => props.onCnpjEmpresa(soDocumento(e.target.value).slice(0, 14))}
            />
            {cnpjInvalido && <small className="campo__erro">CNPJ inválido: confira os dígitos.</small>}
            {mesmoDoCertificado && (
              <small className="campo__erro">É o CNPJ do próprio certificado: nesse caso ele é da empresa, não de um procurador.</small>
            )}
          </label>
          <p className="acesso-escolha__nota">
            As notas saem com o CNPJ da empresa como prestador e são assinadas com este certificado ({donoDoCertificado(cert)}).
            A procuração é dada pela empresa no e-CAC, com acesso aos serviços da NFS-e; sem ela, a Sefin recusa o envio. Depois
            de concluir, dá para conferir em Configurações → Certificado digital.
          </p>
        </div>
      )}
    </fieldset>
  );
}

/** Pronto para seguir? (com procurador, o CNPJ da empresa precisa ser válido e diferente do do certificado) */
export function acessoCompleto(cert: CertificadoInfo, acesso: AcessoEmpresa, cnpjEmpresa: string, permitida: boolean): boolean {
  if (acesso === "certificado") return Boolean(cert.cnpj);
  if (!permitida) return false;
  const cnpj = soDocumento(cnpjEmpresa);
  return cnpjValido(cnpj) && cnpj !== cert.cnpj;
}

/** Em Configurações → Certificado digital: forma de acesso atual, troca e conferência da procuração. */
export function AcessoDaEmpresa({ status }: { status: Status }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [erro, setErro] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState<"" | "trocar" | "conferir">("");
  const [conferencia, setConferencia] = useState<{ ok: boolean; mensagem: string } | null>(null);
  const cert = status.certificado;
  if (!cert || cert.erro) return null;
  const acesso = status.empresa.acesso;
  const cnpjEmpresa = status.empresa.cnpj ?? status.prestador.cnpj;
  const doCertificado = Boolean(cert.cnpj && cnpjEmpresa && soDocumento(cnpjEmpresa) === cert.cnpj);
  const permitida = status.procuracao_permitida;

  async function trocar(novo: AcessoEmpresa) {
    setErro(null);
    setConferencia(null);
    setOcupado("trocar");
    try {
      await api.put("/api/config", { acesso: novo });
      toast(novo === "procuracao" ? "Esta empresa passa a emitir por procuração." : "Esta empresa passa a emitir com o certificado dela.");
      await qc.invalidateQueries();
    } catch (e) {
      setErro(e);
    } finally {
      setOcupado("");
    }
  }

  async function conferir() {
    setErro(null);
    setOcupado("conferir");
    try {
      setConferencia(await api.post<{ ok: boolean; mensagem: string }>(`/api/empresas/${status.empresa.id}/conferir-procuracao`));
    } catch (e) {
      setErro(e);
    } finally {
      setOcupado("");
    }
  }

  if (acesso === "procuracao") {
    return (
      <div className="acesso-atual">
        <p>
          <strong>Por procuração.</strong> As notas de {formatarDocumento(cnpjEmpresa)} saem assinadas com o certificado de{" "}
          {cert.titular} ({donoDoCertificado(cert)}). Enviar o A1 da própria empresa troca a procuração pelo certificado dela.
        </p>
        <div className="acesso-atual__acoes">
          <button className="btn btn--pequeno" disabled={Boolean(ocupado)} onClick={conferir}>
            {ocupado === "conferir" && <Spinner />} Conferir procuração
          </button>
          {doCertificado && (
            <button className="btn btn--pequeno" disabled={Boolean(ocupado)} onClick={() => trocar("certificado")}>
              {ocupado === "trocar" && <Spinner />} Usar como certificado da empresa
            </button>
          )}
        </div>
        {conferencia && (
          <div className={conferencia.ok ? "info-caixa" : "aviso"} role="status">
            <span>{conferencia.mensagem}</span>
          </div>
        )}
        <ErroCaixa erro={erro} />
      </div>
    );
  }
  if (doCertificado || !cnpjEmpresa) return null;
  return (
    <div className="acesso-atual">
      <div className="aviso">
        <span>
          Este certificado é de {cert.titular} ({donoDoCertificado(cert)}), não da empresa {formatarDocumento(cnpjEmpresa)}.
          {permitida
            ? " Se ele é de um procurador com procuração eletrônica da empresa para a NFS-e, use o botão abaixo para emitir por procuração."
            : " Envie o certificado A1 da própria empresa (e-CNPJ)."}
        </span>
      </div>
      {permitida && (
        <div className="acesso-atual__acoes">
          <button className="btn btn--primario btn--pequeno" disabled={Boolean(ocupado)} onClick={() => trocar("procuracao")}>
            {ocupado === "trocar" && <Spinner />} Sou procurador desta empresa
          </button>
        </div>
      )}
      <ErroCaixa erro={erro} />
    </div>
  );
}
