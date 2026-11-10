// Fila e envio para o Prospector. So o endereco de email e o endereco da pagina saem do navegador.
const PADRAO = { servidor: "http://localhost:8000", chave: "", auto: false };
const MAX_VISTOS = 20000;
let trava = Promise.resolve(); // uma operacao de armazenamento por vez

const exclusivo = (fn) => { const r = trava.then(fn, fn); trava = r.catch(() => {}); return r; };
const cfg = async () => ({ ...PADRAO, ...(await chrome.storage.local.get(Object.keys(PADRAO))) });

async function api(c, caminho, corpo) {
  if (!c.chave) throw new Error("Cole a chave nas configuracoes da extensao.");
  const r = await fetch(c.servidor.replace(/\/+$/, "") + caminho, {
    method: corpo ? "POST" : "GET",
    headers: { Authorization: "Bearer " + c.chave, ...(corpo ? { "Content-Type": "application/json" } : {}) },
    body: corpo ? JSON.stringify(corpo) : undefined,
  });
  if (r.status === 401) throw new Error("Chave invalida ou revogada. Gere outra no Prospector.");
  if (r.status === 403) throw new Error("Esta chave nao tem permissao para adicionar contatos.");
  if (!r.ok) throw new Error("O Prospector respondeu com erro " + r.status + ".");
  return r.json();
}

async function somar(parcial) {
  const { stats = {} } = await chrome.storage.local.get("stats");
  for (const [k, v] of Object.entries(parcial)) stats[k] = typeof v === "number" ? (stats[k] || 0) + v : v;
  await chrome.storage.local.set({ stats });
}

// envia a fila agrupada por pagina (a API recebe uma pagina por lote); o que falhar continua na fila
function enviarFila() {
  return exclusivo(async () => {
    const c = await cfg();
    const { fila = [] } = await chrome.storage.local.get("fila");
    if (!fila.length) return { enviados: 0 };
    const porFonte = new Map();
    for (const it of fila) porFonte.set(it.fonte, [...(porFonte.get(it.fonte) || []), it.email]);
    const feitos = new Set();
    let enviados = 0;
    try {
      for (const [fonte, emails] of porFonte) {
        for (let i = 0; i < emails.length; i += 500) {
          const lote = emails.slice(i, i + 500);
          const r = await api(c, "/api/extensao/emails", { fonte, emails: lote });
          lote.forEach((e) => feitos.add(fonte + "|" + e));
          enviados += lote.length;
          await somar({ novos: r.novos, ja_existiam: r.ja_existiam, ruido: r.ruido, ultimoErro: "" });
        }
      }
    } catch (e) {
      await chrome.storage.local.set({ fila: fila.filter((x) => !feitos.has(x.fonte + "|" + x.email)) });
      await somar({ ultimoErro: String(e.message || e) });
      return { enviados, erro: String(e.message || e) };
    }
    await chrome.storage.local.set({ fila: [] });
    return { enviados };
  });
}

function aceitar(fonte, emails) {
  return exclusivo(async () => {
    const { vistos = {}, fila = [] } = await chrome.storage.local.get(["vistos", "fila"]);
    const agora = Date.now();
    const naFila = new Set(fila.map((x) => x.fonte + "|" + x.email));
    let novos = 0;
    for (const email of emails) {
      if (vistos[email] || naFila.has(fonte + "|" + email)) continue;
      vistos[email] = agora;
      fila.push({ fonte, email });
      novos++;
    }
    const chaves = Object.keys(vistos);
    if (chaves.length > MAX_VISTOS) {
      chaves.sort((a, b) => vistos[a] - vistos[b]).slice(0, chaves.length - MAX_VISTOS).forEach((k) => delete vistos[k]);
    }
    await chrome.storage.local.set({ vistos, fila });
    await somar({ coletados: novos });
    return novos;
  });
}

async function registrarColetor(ligado) {
  try { await chrome.scripting.unregisterContentScripts({ ids: ["px"] }); } catch (_) { /* nao estava registrado */ }
  if (ligado) {
    await chrome.scripting.registerContentScripts([{
      id: "px", matches: ["<all_urls>"], js: ["achar.js", "coletor.js"], runAt: "document_idle", persistAcrossSessions: true,
    }]);
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, responder) => {
  (async () => {
    if (msg.tipo === "coletado") {
      await aceitar(msg.fonte, msg.emails);
      chrome.alarms.create("enviar", { delayInMinutes: 0.5 });
      return { ok: true };
    }
    if (msg.tipo === "enviar-agora") { // coleta manual: enfileira (mesmo os ja vistos) e envia na hora
      const { vistos = {} } = await chrome.storage.local.get("vistos");
      msg.emails.forEach((e) => { delete vistos[e]; });
      await chrome.storage.local.set({ vistos });
      const novos = await aceitar(msg.fonte, msg.emails);
      const r = await enviarFila();
      return { ok: !r.erro, achados: msg.emails.length, novos, ...r };
    }
    if (msg.tipo === "testar") return { ok: true, ...(await api(await cfg(), "/api/extensao/ping")) };
    if (msg.tipo === "auto") {
      await chrome.storage.local.set({ auto: msg.ligado });
      await registrarColetor(msg.ligado);
      return { ok: true };
    }
    return { ok: false };
  })().then(responder, (e) => responder({ ok: false, erro: String(e.message || e) }));
  return true; // resposta assincrona
});

chrome.alarms.onAlarm.addListener((a) => { if (a.name === "enviar") enviarFila(); });
chrome.runtime.onInstalled.addListener(async () => { await registrarColetor((await cfg()).auto); });
chrome.runtime.onStartup.addListener(async () => { await registrarColetor((await cfg()).auto); enviarFila(); });
