const $ = (id) => document.getElementById(id);
const PADRAO = { servidor: "http://localhost:8000", chave: "", auto: false };
const LOCAL = ["localhost", "127.0.0.1"];

const msg = (texto, ok = true) => { $("msg").textContent = texto; $("msg").className = ok ? "ok" : "erro"; };
const enviar = (m) => chrome.runtime.sendMessage(m);

async function desenhar() {
  const c = { ...PADRAO, ...(await chrome.storage.local.get(Object.keys(PADRAO))) };
  const { stats = {}, fila = [] } = await chrome.storage.local.get(["stats", "fila"]);
  $("servidor").value = c.servidor;
  $("chave").value = c.chave;
  $("auto").checked = !!c.auto;
  $("s-col").textContent = stats.coletados || 0;
  $("s-novos").textContent = stats.novos || 0;
  $("s-ja").textContent = stats.ja_existiam || 0;
  $("estado").textContent = c.chave
    ? (fila.length ? fila.length + " email(s) aguardando envio" : "Conectado. Pronto para coletar.")
    : "Falta a chave: abra Configurar.";
  if (!c.chave) $("config").open = true;
  if (stats.ultimoErro) msg(stats.ultimoErro, false);
  return c;
}

// pede ao Chrome permissao para falar com um endereco que nao seja o local (precisa vir direto do clique)
function permissaoServidor(url) {
  const o = new URL(url);
  return LOCAL.includes(o.hostname) ? Promise.resolve(true) : chrome.permissions.request({ origins: [o.origin + "/*"] });
}

$("salvar").addEventListener("click", async () => {
  let url;
  try {
    url = new URL($("servidor").value.trim());
    if (!/^https?:$/.test(url.protocol)) throw new Error("protocolo");
  } catch (_) {
    return msg("Endereco invalido. Exemplo: http://localhost:8000", false);
  }
  if (!(await permissaoServidor(url.href))) return msg("Sem permissao para esse endereco.", false);
  await chrome.storage.local.set({ servidor: url.origin, chave: $("chave").value.trim() });
  const r = await enviar({ tipo: "testar" });
  if (r.ok) msg("Conectado ao workspace " + r.workspace + ".");
  else msg(r.erro || "Nao consegui conectar.", false);
  desenhar();
});

$("coletar").addEventListener("click", async () => {
  const [aba] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!aba || !/^https?:/.test(aba.url || "")) return msg("Abra uma pagina de site (http/https) primeiro.", false);
  try {
    await chrome.scripting.executeScript({ target: { tabId: aba.id }, files: ["achar.js"] });
    const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: aba.id }, func: () => window.__pxAchar() });
    if (!result.length) return msg("Nenhum email nesta pagina.", false);
    const u = new URL(aba.url);
    const r = await enviar({ tipo: "enviar-agora", fonte: u.origin + u.pathname, emails: result });
    if (r.ok) msg(r.achados + " email(s) achados e enviados.");
    else msg(r.erro || "Nao consegui enviar.", false);
  } catch (e) {
    msg("Nao consegui ler esta pagina: " + (e.message || e), false);
  }
  desenhar();
});

$("auto").addEventListener("change", async (ev) => {
  const ligar = ev.target.checked;
  if (ligar && !(await chrome.permissions.request({ origins: ["<all_urls>"] }))) {
    ev.target.checked = false;
    return msg("Sem permissao para ler os sites, a coleta automatica nao liga.", false);
  }
  await enviar({ tipo: "auto", ligado: ligar });
  msg(ligar ? "Coleta automatica ligada. Vale para paginas que abrir ou recarregar." : "Coleta automatica desligada.");
});

$("abrir").addEventListener("click", async (ev) => {
  ev.preventDefault();
  const c = await desenhar();
  chrome.tabs.create({ url: c.servidor.replace(/\/+$/, "") + "/hunter/contatos?origem=extensao_navegador" });
});

desenhar();
