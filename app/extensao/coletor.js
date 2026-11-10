// Coleta automatica: roda em cada pagina so enquanto a "coleta automatica" esta ligada no popup.
(() => {
  if (window.__pxColetorAtivo) return;
  window.__pxColetorAtivo = true;
  const jaEnviados = new Set();
  let varreduras = 0;

  function varrer() {
    if (varreduras++ > 40 || !window.__pxAchar) return; // paginas que mudam sem parar nao geram trafego infinito
    const novos = window.__pxAchar().filter((e) => !jaEnviados.has(e));
    if (!novos.length) return;
    novos.forEach((e) => jaEnviados.add(e));
    // so o endereco da pagina, sem parametros de busca
    chrome.runtime.sendMessage({ tipo: "coletado", fonte: location.origin + location.pathname, emails: novos });
  }

  chrome.storage.local.get(["servidor"]).then(({ servidor }) => {
    if (servidor && location.href.startsWith(servidor)) return; // nao coleta o proprio Prospector
    varrer();
    let t = null;
    new MutationObserver(() => { clearTimeout(t); t = setTimeout(varrer, 2000); })
      .observe(document.documentElement, { childList: true, subtree: true });
  });
})();
