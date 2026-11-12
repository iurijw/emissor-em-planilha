import { useEffect, useId, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { Spinner } from "../ui";

/** Resposta de ``GET /api/cep/{cep}`` (BrasilAPI, com ViaCEP de reserva). */
export type EnderecoCep = {
  fonte: string;
  cep: string;
  logradouro: string | null;
  bairro: string | null;
  c_mun: string | null;
  municipio: string | null;
};

const soDigitos = (v: string | null | undefined) => (v ?? "").replace(/\D/g, "");

function mascara(v: string | null | undefined): string {
  const d = soDigitos(v);
  return d.length > 5 ? `${d.slice(0, 5)}-${d.slice(5)}` : d;
}

type Estado = { tipo: "parado" } | { tipo: "buscando" } | { tipo: "ok"; texto: string } | { tipo: "erro"; texto: string };

/**
 * CEP no topo do endereço: ao completar os 8 dígitos, busca logradouro, bairro e município e entrega em
 * ``aoEncontrar`` (quem usa decide o que trocar). Só busca o que a pessoa digitou, nunca o CEP já gravado.
 */
export function CampoCep({
  valor,
  onChange,
  aoEncontrar,
  disabled,
  obrigatorio,
}: {
  valor: string | null;
  onChange: (cep: string | null) => void;
  aoEncontrar: (e: EnderecoCep) => void;
  disabled?: boolean;
  obrigatorio?: boolean;
}) {
  const id = useId();
  const [estado, setEstado] = useState<Estado>({ tipo: "parado" });
  const pedido = useRef(0);
  // A resposta usa o aoEncontrar mais novo, com o endereço como está quando ela chega.
  const aoEncontrarRef = useRef(aoEncontrar);
  aoEncontrarRef.current = aoEncontrar;
  const digitos = soDigitos(valor);
  useEffect(() => () => void (pedido.current += 1), []);

  // Busca só a partir do que se digita: um CEP que chega pelo cadastro ou pela Receita não troca nada.
  const buscar = (cep: string) => {
    const n = ++pedido.current;
    setEstado({ tipo: "buscando" });
    api
      .get<EnderecoCep>(`/api/cep/${cep}`)
      .then((r) => {
        if (n !== pedido.current) return;
        aoEncontrarRef.current(r);
        const achou = [r.logradouro, r.bairro, r.c_mun].filter(Boolean).length;
        setEstado(
          achou === 3
            ? { tipo: "ok", texto: "Endereço preenchido pelo CEP. Falta só o número." }
            : r.c_mun
              ? { tipo: "ok", texto: "CEP geral da cidade: preencha a rua e o bairro." }
              : { tipo: "erro", texto: "O CEP existe, mas veio sem o município. Preencha à mão." },
        );
      })
      .catch((e) => {
        if (n !== pedido.current) return;
        setEstado({
          tipo: "erro",
          texto:
            e instanceof ApiError && e.status === 404
              ? "CEP não encontrado. Confira o número ou preencha o endereço à mão."
              : "Não deu para consultar o CEP agora. Preencha o endereço à mão.",
        });
      });
  };

  const mudar = (texto: string) => {
    const novo = soDigitos(texto).slice(0, 8);
    onChange(novo || null);
    if (novo === digitos) return;
    if (novo.length === 8) buscar(novo);
    else {
      pedido.current += 1;
      setEstado({ tipo: "parado" });
    }
  };

  return (
    <div className={`campo largo campo-cep${estado.tipo === "erro" ? " campo-cep--erro" : ""}${obrigatorio ? " campo-cep--obrigatorio" : ""}`}>
      <label htmlFor={id}>CEP</label>
      <div className="campo-cep__linha">
        <input
          id={id}
          className="num"
          inputMode="numeric"
          autoComplete="postal-code"
          placeholder="00000-000"
          value={mascara(valor)}
          onChange={(e) => mudar(e.target.value)}
          disabled={disabled}
          aria-describedby={`${id}-situacao`}
        />
        <span className="campo-cep__situacao" id={`${id}-situacao`} aria-live="polite">
          {estado.tipo === "buscando" ? (
            <>
              <Spinner /> Buscando o endereço…
            </>
          ) : estado.tipo === "parado" ? (
            digitos ? "" : "Digite o CEP para preencher o endereço."
          ) : (
            estado.texto
          )}
        </span>
      </div>
    </div>
  );
}
