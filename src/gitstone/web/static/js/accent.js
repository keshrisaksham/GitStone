// Loaded synchronously from <head> like theme.js: the chosen accent colour is on
// <html data-accent> before first paint. Green is the default.
(function () {
  "use strict";
  var KEY = "gitstone-accent";
  var NAMES = ["green", "amber", "cyan", "magenta", "blue", "red"];
  var chosen = null;
  try { chosen = window.localStorage.getItem(KEY); } catch (e) { /* storage blocked */ }
  if (NAMES.indexOf(chosen) === -1) { chosen = "green"; }
  document.documentElement.setAttribute("data-accent", chosen);

  document.addEventListener("DOMContentLoaded", function () {
    var swatches = document.querySelectorAll("[data-accent-set]");
    function mark() {
      swatches.forEach(function (s) {
        s.setAttribute("aria-pressed", s.getAttribute("data-accent-set") === chosen ? "true" : "false");
      });
    }
    swatches.forEach(function (s) {
      s.addEventListener("click", function () {
        chosen = s.getAttribute("data-accent-set");
        document.documentElement.setAttribute("data-accent", chosen);
        try { window.localStorage.setItem(KEY, chosen); } catch (e) { /* storage blocked */ }
        mark();
      });
    });
    mark();
  });
})();
