// Loaded synchronously from <head> (the CSP forbids inline scripts), so the saved
// or system theme is on <html> before the first paint and nothing flashes.
(function () {
  "use strict";
  var theme = null;
  try { theme = window.localStorage.getItem("gitstone-theme"); } catch (e) { /* storage blocked */ }
  if (theme !== "light" && theme !== "dark") {
    theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.documentElement.setAttribute("data-theme", theme);
})();

(function () {
  "use strict";

  // ---- light / dark: the toggle in the top bar remembers the choice
  document.addEventListener("click", function (event) {
    var btn = event.target.closest && event.target.closest("[data-theme-toggle]");
    if (!btn) { return; }
    var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { window.localStorage.setItem("gitstone-theme", next); } catch (e) { /* storage blocked */ }
  });
})();
