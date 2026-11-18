import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { api, type Preferencias, type Sessao } from "./api";

/**
 * Preferências de tela do usuário (aviso do 2FA dispensado, ordem das colunas...). Vêm com a sessão e ficam no
 * servidor, então valem em qualquer navegador. Salvar atualiza a tela na hora e manda ao servidor em seguida.
 */
export function usePreferencias(): [Preferencias, (novas: Preferencias) => Promise<void>] {
  const qc = useQueryClient();
  // A mesma consulta de useSessao (App.tsx): aqui só se lê o que já está no cache.
  const sessao = useQuery({ queryKey: ["sessao"], queryFn: () => api.get<Sessao>("/api/auth/sessao"), staleTime: 60_000 });
  const atuais = sessao.data?.preferencias ?? {};
  const salvar = useCallback(
    async (novas: Preferencias) => {
      qc.setQueryData<Sessao>(["sessao"], (s) => (s ? { ...s, preferencias: { ...s.preferencias, ...novas } } : s));
      const gravadas = await api.put<Preferencias>("/api/auth/preferencias", novas);
      qc.setQueryData<Sessao>(["sessao"], (s) => (s ? { ...s, preferencias: gravadas } : s));
    },
    [qc],
  );
  return [atuais, salvar];
}
