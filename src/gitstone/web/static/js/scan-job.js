// scan-job.js: poll a background scan and reload to show its results when it ends.
(function () {
  "use strict";

  // ---- background scan page: poll its status, reload to show results when it ends
  (function () {
    var box = document.querySelector("[data-scan-status]");
    if (!box || !window.fetch) { return; }
    var url = box.getAttribute("data-scan-status");
    var phase = box.querySelector("[data-scan-phase]");
    var count = box.querySelector("[data-scan-count]");
    var bar = box.querySelector("[data-scan-bar]");

    function poll() {
      fetch(url, { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
        .then(function (s) {
          if (!s.active) { window.location.reload(); return; }
          if (phase) { phase.textContent = s.phase; }
          if (count) {
            count.textContent = (s.total ? s.done + " / " + s.total + " \u00b7 " : "") + Math.round(s.elapsed) + "s";
          }
          if (bar) {
            if (s.total) { bar.max = s.total; bar.value = s.done; } else { bar.removeAttribute("value"); }
          }
          window.setTimeout(poll, 1000);
        })
        .catch(function () { window.setTimeout(poll, 3000); });
    }
    window.setTimeout(poll, 500);
  })();
})();
