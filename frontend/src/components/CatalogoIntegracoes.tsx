import { MARCAS, type Marca } from "./marcas";

type Situacao = "conectado" | "disponivel" | "api" | "breve";
type Item = { id: string; marca: Marca | null; titulo: string; texto: string; situacao: Situacao; acao?: () => void; rotuloAcao?: string };

const SELO: Record<Situacao, string> = {
  conectado: "Conectado",
  disponivel: "Disponível",
  api: "Pela API",
  breve: "Em breve",
};

export function LogoMarca({ marca, tamanho = 40 }: { marca: Marca | null; tamanho?: number }) {
  if (!marca) {
    return (
      <span className="logo-marca logo-marca--api" style={{ width: tamanho, height: tamanho }} aria-hidden="true">
        {"{ }"}
      </span>
    );
  }
  return (
    <span className="logo-marca" style={{ width: tamanho, height: tamanho, background: marca.cor }} aria-hidden="true">
      {marca.caminho ? (
        <svg viewBox="0 0 24 24" width="58%" height="58%" fill="#fff">
          <path d={marca.caminho} />
        </svg>
      ) : (
        <span className="logo-marca__nome">{marca.nome.toLowerCase()}</span>
      )}
    </span>
  );
}

/** Vitrine das integrações: o que já liga direto, o que funciona pela API e o que vem depois. */
export function CatalogoIntegracoes({
  conectoresAsaas,
  abrirAsaas,
  irParaApi,
}: {
  conectoresAsaas: number;
  abrirAsaas: () => void;
  irParaApi: () => void;
}) {
  const itens: Item[] = [
    {
      id: "asaas",
      marca: MARCAS.asaas,
      titulo: "Asaas",
      texto: "Cada cobrança paga vira uma nota, com o cliente da cobrança como tomador.",
      situacao: conectoresAsaas > 0 ? "conectado" : "disponivel",
      acao: abrirAsaas,
      rotuloAcao: conectoresAsaas > 0 ? "Ver conector" : "Conectar",
    },
    {
      id: "shopee",
      marca: MARCAS.shopee,
      titulo: "Shopee",
      texto: "Afiliados: importe o relatório de comissões na tabela, uma linha por loja (Emitir → Importar).",
      situacao: "disponivel",
    },
    {
      id: "api",
      marca: null,
      titulo: "Seu sistema",
      texto: "ERP, loja ou cobrança própria emitem e consultam notas com um token, e recebem webhooks.",
      situacao: "api",
      acao: irParaApi,
      rotuloAcao: "Criar token",
    },
    ...(["n8n", "zapier", "make"] as const).map((id) => ({
      id,
      marca: MARCAS[id],
      titulo: MARCAS[id].nome,
      texto: "Automação sem programar: chame a API com o token e receba os webhooks.",
      situacao: "api" as const,
      acao: irParaApi,
      rotuloAcao: "Criar token",
    })),
    {
      id: "stripe",
      marca: MARCAS.stripe,
      titulo: "Stripe",
      texto: "Fatura paga vira nota. Por enquanto, ligue pela API ou por uma automação.",
      situacao: "breve",
    },
    {
      id: "mercadopago",
      marca: MARCAS.mercadopago,
      titulo: "Mercado Pago",
      texto: "Pagamento aprovado vira nota. Por enquanto, ligue pela API ou por uma automação.",
      situacao: "breve",
    },
  ];

  return (
    <ul className="catalogo-integracoes" aria-label="Integrações">
      {itens.map((i) => (
        <li key={i.id} className={`integracao integracao--${i.situacao}`}>
          <LogoMarca marca={i.marca} />
          <div className="integracao__corpo">
            <div className="integracao__linha">
              <h4 className="integracao__titulo">{i.titulo}</h4>
              <span className={`integracao__selo integracao__selo--${i.situacao}`}>{SELO[i.situacao]}</span>
            </div>
            <p className="integracao__texto">{i.texto}</p>
            {i.acao && (
              <button className="btn btn--pequeno integracao__acao" onClick={i.acao}>
                {i.rotuloAcao}
              </button>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
