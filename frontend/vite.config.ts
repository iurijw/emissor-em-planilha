import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Em desenvolvimento, o Vite repassa /api para o backend. Padrão: porta 8000; para testar sem
// tocar no servidor em uso, aponte para outra instância: EMISSOR_API=http://127.0.0.1:8765 npm run dev
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": process.env.EMISSOR_API ?? "http://127.0.0.1:8000" },
  },
  build: { outDir: "dist", sourcemap: true, chunkSizeWarningLimit: 1500 },
});
