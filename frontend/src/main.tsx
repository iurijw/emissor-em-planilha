import "@fontsource-variable/archivo/standard.css";
import "@fontsource-variable/martian-mono/standard.css";
import "@glideapps/glide-data-grid/dist/index.css";
import "./styles.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Raiz } from "./App";
import { instalarCapturaDeErros, ToastProvider } from "./ui";

instalarCapturaDeErros();

// Aviso "ligando o sistema" enquanto a máquina da hospedagem acorda (ver public/sw.js). Só em HTTPS/localhost.
if (import.meta.env.PROD && "serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 5_000 } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <Raiz />
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
