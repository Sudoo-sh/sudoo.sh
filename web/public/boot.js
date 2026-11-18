// Runs before the page is drawn (a small file, because the panel's Content-Security-Policy allows no inline script):
// the language and direction of the page, and the light or dark theme, as last used in this browser, so an Arabic
// page never flashes left to right. The app then follows the signed-in account's choice (src/i18n/locale.ts).
(function () {
  var root = document.documentElement;
  var lang = null;
  try {
    lang = localStorage.getItem("sudoo.lang");
    var theme = localStorage.getItem("sudoo.theme");
    if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
  } catch (e) {
    /* storage blocked: the defaults below */
  }
  if (lang !== "en" && lang !== "ar") {
    var cookie = /(?:^|;\s*)sudoo_lang=(en|ar)(?:;|$)/.exec(document.cookie);
    lang = cookie ? cookie[1] : null;
  }
  if (!lang) {
    var list = navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language || "en"];
    for (var i = 0; i < list.length && !lang; i++) {
      var primary = String(list[i]).toLowerCase().split("-")[0];
      if (primary === "en" || primary === "ar") lang = primary;
    }
  }
  lang = lang || "en";
  root.lang = lang;
  root.dir = lang === "ar" ? "rtl" : "ltr";
})();
