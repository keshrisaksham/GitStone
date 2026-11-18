// No inline scripts anywhere (the CSP forbids them). The topbar's "Check for
// updates" button asks this server to compare its version against GitHub's;
// the CSP's connect-src is 'self', so the browser never talks to GitHub
// directly -- only the server does, and only when this button is clicked.
(function () {
  "use strict";

  document.addEventListener("click", function (event) {
    var btn = event.target.closest && event.target.closest("[data-update-check]");
    if (!btn) { return; }

    var status = document.querySelector("[data-update-status]");
    var link = document.querySelector("[data-update-link]");
    if (!status) { return; }

    btn.disabled = true;
    link.hidden = true;
    status.hidden = false;
    status.className = "badge";
    status.textContent = "checking…";

    fetch("/update/check.json", { headers: { "Accept": "application/json" } })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (!data.ok) {
          status.className = "badge status-failed";
          status.textContent = data.error || "could not check";
          return;
        }
        if (data.newer) {
          status.className = "badge status-cancelled";
          status.textContent = "v" + data.latest + " available";
          link.href = data.url;
          link.hidden = false;
        } else {
          status.className = "badge status-done";
          status.textContent = "up to date (v" + data.current + ")";
        }
      })
      .catch(function () {
        status.className = "badge status-failed";
        status.textContent = "could not reach GitHub";
      })
      .finally(function () { btn.disabled = false; });
  });
})();
