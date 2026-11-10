// Acha emails na pagina (links mailto: e texto visivel). Nao guarda nem envia nada: so devolve a lista.
(() => {
  const RE = /[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,24}/g;
  const FALSO = /\.(png|jpe?g|gif|svg|webp|css|js|ico|woff2?|ttf|map|pdf)$/i;
  const AT = /\s*[\[({]\s*(?:at|arroba)\s*[\])}]\s*/gi;
  const DOT = /\s*[\[({]\s*(?:dot|ponto)\s*[\])}]\s*/gi;

  window.__pxAchar = () => {
    const achados = new Set();
    const add = (s) => {
      const e = (s || "").trim().toLowerCase().replace(/^\.+|\.+$/g, "");
      if (e.length <= 254 && e.includes("@") && !FALSO.test(e)) achados.add(e);
    };
    for (const a of document.querySelectorAll('a[href^="mailto:" i]')) {
      let alvo = a.getAttribute("href").slice(7).split("?")[0];
      try { alvo = decodeURIComponent(alvo); } catch (_) { /* mantem como esta */ }
      alvo.split(",").forEach(add);
    }
    const texto = ((document.body && document.body.innerText) || "").replace(AT, "@").replace(DOT, ".");
    (texto.match(RE) || []).forEach(add);
    return [...achados];
  };
})();
