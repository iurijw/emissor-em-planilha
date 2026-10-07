// Formatação e validação no padrão brasileiro (espelha backend/src/emissor/nfse/documentos.py).

export function soDocumento(v: string): string {
  return (v || "").replace(/[^0-9A-Za-z]/g, "").toUpperCase();
}

export function formatarDocumento(v: string | null | undefined): string {
  const s = soDocumento(v || "");
  if (s.length === 14) return `${s.slice(0, 2)}.${s.slice(2, 5)}.${s.slice(5, 8)}/${s.slice(8, 12)}-${s.slice(12)}`;
  if (s.length === 11) return `${s.slice(0, 3)}.${s.slice(3, 6)}.${s.slice(6, 9)}-${s.slice(9)}`;
  return v || "";
}

export function cnpjValido(v: string): boolean {
  const s = soDocumento(v);
  if (!/^[0-9A-Z]{12}[0-9]{2}$/.test(s) || new Set(s).size === 1) return false;
  const val = (c: string) => c.charCodeAt(0) - 48;
  const p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2];
  const p2 = [6, ...p1];
  for (const [pesos, pos] of [
    [p1, 12],
    [p2, 13],
  ] as const) {
    const soma = pesos.reduce((acc, p, i) => acc + val(s[i]) * p, 0);
    let dv = 11 - (soma % 11);
    if (dv >= 10) dv = 0;
    if (Number(s[pos]) !== dv) return false;
  }
  return true;
}

export function cpfValido(v: string): boolean {
  const s = (v || "").replace(/\D/g, "");
  if (s.length !== 11 || new Set(s).size === 1) return false;
  for (const pos of [9, 10]) {
    let soma = 0;
    for (let i = 0; i < pos; i++) soma += Number(s[i]) * (pos + 1 - i);
    if (Number(s[pos]) !== ((soma * 10) % 11) % 10) return false;
  }
  return true;
}

export function documentoValido(v: string): boolean {
  const s = soDocumento(v);
  return s.length === 11 ? cpfValido(s) : cnpjValido(s);
}

const moedaFmt = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

export function moeda(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "";
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? moedaFmt.format(n) : String(v);
}

/** "1.234,56" | "1234.56" | "R$ 450" → "1234.56"; "" se vazio; null se inválido. */
export function parseValor(v: string): string | null {
  let s = (v || "").replace(/R\$|\s| /g, "");
  if (!s) return "";
  if (s.includes(",")) s = s.replace(/\./g, "").replace(",", ".");
  else if ((s.match(/\./g) || []).length > 1) s = s.replace(/\./g, "");
  const n = Number(s);
  return Number.isFinite(n) ? n.toFixed(2) : null;
}

export function dataHora(v: string | null | undefined): string {
  if (!v) return "";
  const d = new Date(v);
  return Number.isNaN(d.getTime())
    ? v
    : d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function data(v: string | null | undefined): string {
  if (!v) return "";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? v : d.toLocaleDateString("pt-BR");
}

export function cep(v: string | null | undefined): string {
  const s = (v || "").replace(/\D/g, "");
  return s.length === 8 ? `${s.slice(0, 5)}-${s.slice(5)}` : v || "";
}

export const STATUS_ROTULO: Record<string, string> = {
  pendente: "Na fila",
  processando: "Enviando",
  autorizada: "Autorizada",
  rejeitada: "Rejeitada",
  erro: "Erro",
  nao_enviada: "Não enviada",
};

/** Texto copiado do Excel/Sheets (TSV; células com tab/quebra/aspas vêm entre aspas). */
export function parseTSV(texto: string): string[][] {
  const t = texto.replace(/\r\n?/g, "\n").replace(/\n$/, "");
  const linhas: string[][] = [];
  let linha: string[] = [];
  let cel = "";
  let aspas = false;
  for (let i = 0; i < t.length; i++) {
    const c = t[i];
    if (aspas) {
      if (c === '"' && t[i + 1] === '"') {
        cel += '"';
        i++;
      } else if (c === '"') aspas = false;
      else cel += c;
    } else if (c === '"' && cel === "") aspas = true;
    else if (c === "\t") {
      linha.push(cel);
      cel = "";
    } else if (c === "\n") {
      linha.push(cel);
      linhas.push(linha);
      linha = [];
      cel = "";
    } else cel += c;
  }
  linha.push(cel);
  linhas.push(linha);
  return linhas;
}

