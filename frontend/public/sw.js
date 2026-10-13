/*
 * Aviso "ligando o sistema".
 *
 * Na hospedagem a máquina do cliente dorme quando ninguém usa e liga no primeiro acesso; enquanto isso o navegador
 * ficaria com a tela em branco. Este service worker só cuida da abertura da página: se o servidor não responder
 * em ESPERA_MS (ou responder com erro 5xx/sem conexão), mostra uma página própria que consulta /api/saude e
 * recarrega sozinha quando o sistema responde. Nada do app fica guardado aqui: a página real sempre vem do
 * servidor, então uma versão nova chega como antes.
 *
 * Só funciona em HTTPS ou localhost (exigência dos navegadores); na rede local por HTTP nada muda.
 */
const ESPERA_MS = 1200;
// Depois que /api/saude respondeu, as aberturas seguintes vão direto ao servidor (evita voltar ao aviso numa
// conexão lenta, em que a página demora mais que ESPERA_MS mesmo com o sistema ligado).
const DIRETO_MS = 60_000;
let respondeuEm = 0;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname === "/api/saude") {
    e.respondWith(
      fetch(req).then((r) => {
        if (r.ok) respondeuEm = Date.now();
        return r;
      }),
    );
    return;
  }
  if (req.mode !== "navigate" || req.method !== "GET" || url.pathname.startsWith("/api/")) return;
  if (Date.now() - respondeuEm < DIRETO_MS) return;
  e.respondWith(abrir(req));
});

function abrir(req) {
  const rede = fetch(req);
  return new Promise((resolve) => {
    let feito = false;
    const aviso = () => {
      if (!feito) {
        feito = true;
        resolve(paginaLigando());
      }
    };
    const timer = setTimeout(aviso, ESPERA_MS);
    rede.then(
      (r) => {
        if (feito) return;
        clearTimeout(timer);
        if (r.status >= 500) return aviso();
        feito = true;
        resolve(r);
      },
      () => {
        clearTimeout(timer);
        aviso();
      },
    );
  });
}

function paginaLigando() {
  return new Response(HTML, {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
  });
}

const HTML = `<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ligando… · Emissor em Planilha</title>
<style>
  :root {
    --preto: #020202; --tinta: #0b1014; --grafite: #56656f; --azul: #0098d7; --azul-texto: #0079ad;
    --papel: #f9fdfe; --linha: #d6e4eb;
    --f-ui: "Archivo Variable", "Archivo", "Segoe UI", system-ui, sans-serif;
    --f-num: "Martian Mono Variable", "Martian Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; }
  body {
    background: var(--papel); color: var(--tinta); font-family: var(--f-ui);
    display: grid; grid-template-rows: 6px 1fr; min-height: 100%;
  }
  .faixa { background: repeating-linear-gradient(-45deg, var(--azul) 0 10px, #7fcbeb 10px 20px); background-size: 28.28px 28.28px; animation: corre 1.2s linear infinite; }
  main { display: grid; place-items: center; padding: 32px 16px; }
  .cartao { width: min(440px, 100%); }
  .marca { display: flex; align-items: center; gap: 10px; font-weight: 700; font-size: 15px; letter-spacing: .01em; }
  .marca svg { width: 30px; height: 30px; }
  h1 { font-size: clamp(30px, 7vw, 40px); line-height: 1.05; font-weight: 800; font-stretch: 125%; letter-spacing: -.02em; margin: 36px 0 14px; }
  h1 em { font-style: normal; color: var(--azul); }
  p { font-size: 16px; line-height: 1.55; color: var(--grafite); margin: 0 0 28px; }
  .fita { display: flex; gap: 3px; height: 34px; }
  .fita i { flex: 1; border-radius: 2px; background: #e3ecf0; transition: background .3s; }
  .fita i.on { background: var(--azul); }
  .fita i.vez { background: repeating-linear-gradient(-45deg, #9fd8f0 0 4px, #c9eaf8 4px 8px); background-size: 11.3px 11.3px; animation: corre .8s linear infinite; }
  .legenda { display: flex; justify-content: space-between; margin-top: 10px; font-size: 13px; color: var(--grafite); }
  .legenda b { font-family: var(--f-num); font-weight: 500; color: var(--tinta); }
  .demora { display: none; margin-top: 28px; padding: 12px 14px; border-left: 3px solid #8a5a00; background: #fff5db; color: #5c3c00; font-size: 14px; line-height: 1.5; }
  .demora.ver { display: block; }
  button { font: inherit; font-weight: 600; color: var(--azul-texto); background: none; border: 0; padding: 0; text-decoration: underline; cursor: pointer; }
  @keyframes corre { to { background-position: 28.28px 0; } }
  @media (prefers-reduced-motion: reduce) { .faixa, .fita i.vez { animation: none; } }
</style>
</head>
<body>
<div class="faixa" aria-hidden="true"></div>
<main>
  <div class="cartao">
    <div class="marca">
      <svg viewBox="0 0 64 64" aria-hidden="true"><defs><mask id="g"><rect width="64" height="64" fill="#fff"/><rect x="12" y="26" width="10" height="8" rx="1"/><rect x="26" y="26" width="26" height="8" rx="1"/><rect x="12" y="38" width="10" height="8" rx="1"/><rect x="26" y="38" width="26" height="8" rx="1"/><rect x="12" y="50" width="10" height="8" rx="1"/><rect x="26" y="50" width="26" height="8" rx="1"/></mask></defs><g mask="url(#g)"><polygon points="7,2 41,2 57,18 57,62 7,62" fill="#0098D7"/><polygon points="41,2 41,18 57,18" fill="#020202"/></g></svg>
      Emissor em Planilha
    </div>
    <h1>Ligando o seu <em>emissor</em>…</h1>
    <p role="status">Quando fica um tempo sem uso, o sistema descansa. Ele já está acordando e esta página abre sozinha em alguns segundos.</p>
    <div class="fita" id="fita" aria-hidden="true"></div>
    <div class="legenda"><span>Aguardando o sistema</span><span><b id="seg">0</b> s</span></div>
    <div class="demora" id="demora" role="alert">Está demorando mais que o normal. Confira a sua conexão com a internet ou <button type="button" onclick="location.reload()">tente de novo</button>. Se continuar assim, fale com o suporte.</div>
  </div>
</main>
<script>
  var N = 20, fita = document.getElementById("fita"), segs = [];
  for (var i = 0; i < N; i++) { var s = document.createElement("i"); fita.appendChild(s); segs.push(s); }
  var inicio = Date.now(), pronto = false;
  function desenhar() {
    var t = (Date.now() - inicio) / 1000;
    document.getElementById("seg").textContent = Math.floor(t);
    // Enche rápido no começo e devagar depois: a maioria liga em poucos segundos.
    var cheio = Math.min(N - 1, Math.floor(N * (1 - Math.exp(-t / 6))));
    for (var i = 0; i < N; i++) segs[i].className = pronto || i < cheio ? "on" : i === cheio ? "vez" : "";
    if (t > 45) document.getElementById("demora").className = "demora ver";
  }
  function consultar() {
    var c = new AbortController(), t = setTimeout(function () { c.abort(); }, 8000);
    fetch("/api/saude", { cache: "no-store", signal: c.signal })
      .then(function (r) { clearTimeout(t); if (!r.ok) throw 0; pronto = true; desenhar(); setTimeout(function () { location.reload(); }, 250); })
      .catch(function () { clearTimeout(t); setTimeout(consultar, 1000); });
  }
  setInterval(desenhar, 250); desenhar(); consultar();
</script>
</body>
</html>`;
