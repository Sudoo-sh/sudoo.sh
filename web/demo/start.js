// The demo's page loads the app from here, once the browser has drawn the first screen (demo/first-screen.html):
// the page itself is shown at once, and the app's files (about 330 kB compressed) and its stylesheet download behind
// it instead of in front of it. The build (vite.config.ts, demoSite) names the entry and the files it imports in data- attributes.
// An external file, because the Content-Security-Policy allows no inline script.
(function () {
  var me = document.currentScript;
  var entry = me.getAttribute("data-entry");
  var style = me.getAttribute("data-style");
  var preload = (me.getAttribute("data-preload") || "").split(" ").filter(Boolean);
  var started = false;
  function load() {
    if (started) return;
    started = true;
    preload.forEach(function (href) {
      var link = document.createElement("link");
      link.rel = "modulepreload";
      link.crossOrigin = "";
      link.href = href;
      document.head.appendChild(link);
    });
    // the app's stylesheet (already downloading, from the page's preload) applies first, then the app starts
    var sheet = document.createElement("link");
    sheet.rel = "stylesheet";
    sheet.crossOrigin = "";
    sheet.href = style;
    var run = function () {
      var script = document.createElement("script");
      script.type = "module";
      script.crossOrigin = "";
      script.src = entry;
      document.head.appendChild(script);
    };
    sheet.onload = run;
    sheet.onerror = run;
    document.head.appendChild(sheet);
  }
  // requestAnimationFrame runs just before the next frame is drawn, the timeout right after it; the second timeout
  // covers a tab opened in the background, where no frame is drawn
  requestAnimationFrame(function () {
    setTimeout(load, 0);
  });
  setTimeout(load, 500);
})();
