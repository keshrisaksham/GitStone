// No inline scripts anywhere (the CSP forbids them); behaviour hooks onto data- attributes.
(function () {
  "use strict";

  // <form data-confirm="..."> asks before submitting
  document.addEventListener("submit", function (event) {
    var message = event.target.getAttribute && event.target.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
    }
  });

  // <select class="ref-picker" data-url-template="/x/__REF__/y"> jumps to that URL
  document.addEventListener("change", function (event) {
    var select = event.target;
    if (select.classList && select.classList.contains("ref-picker")) {
      var template = select.getAttribute("data-url-template");
      window.location.href = template.replace("__REF__", encodeURIComponent(select.value));
    }
  });

  // Run page: follow a queued/running run by polling for new log text.
  // Polling rather than SSE: simple, proxy-friendly, and a log doesn't need
  // sub-second updates.
  var run = document.getElementById("run");
  if (run && run.getAttribute("data-active") === "true") {
    var url = run.getAttribute("data-status-url");
    var offset = parseInt(run.getAttribute("data-offset"), 10) || 0;
    var logEl = document.getElementById("log");
    var MAX_CHARS = 400000;
    logEl.scrollTop = logEl.scrollHeight;

    var poll = function () {
      fetch(url + "?offset=" + offset, { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var stick = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 24;
          if (data.log) {
            var text = logEl.textContent + data.log;
            logEl.textContent = text.length > MAX_CHARS ? text.slice(text.length - MAX_CHARS) : text;
            if (stick) { logEl.scrollTop = logEl.scrollHeight; }
          }
          offset = data.offset;

          var badge = document.getElementById("run-status");
          badge.textContent = data.status;
          badge.className = "badge status-" + data.status;
          Object.keys(data.stats || {}).forEach(function (key) {
            var el = document.querySelector('[data-stat="' + key + '"]');
            if (el) { el.textContent = data.stats[key]; }
          });

          if (data.active) {
            setTimeout(poll, 1000);
          } else {
            window.location.reload();  // final state, stats and links
          }
        })
        .catch(function () { setTimeout(poll, 3000); });
    };
    setTimeout(poll, 800);
  }
})();
