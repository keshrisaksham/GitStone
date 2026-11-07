// viewer.js: Wrap / Copy / Close buttons and the side pane that opens a file beside a result list.
(function () {
  "use strict";

  // ---- code viewer buttons (work on the page and inside the side pane)
  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!target.closest) { return; }

    var wrap = target.closest("[data-toggle-wrap]");
    if (wrap) {
      var holder = document.querySelector(".code-wrap");
      if (holder) { holder.classList.toggle("wrapped"); }
      return;
    }

    var copy = target.closest("[data-copy-code]");
    if (copy) {
      var text = Array.prototype.map.call(
        document.querySelectorAll("[data-code] td.code pre"), function (p) { return p.textContent; }
      ).join("\n");
      var done = function () { copy.textContent = "Copied"; setTimeout(function () { copy.textContent = "Copy"; }, 1500); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () { copy.textContent = "Failed"; });
      } else { copy.textContent = "Not available"; }
      return;
    }

    if (target.closest("[data-close-pane]")) {
      closePane();
      return;
    }

    // ---- side pane: <a data-pane href="..."> opens the blob to the right of the list
    var link = target.closest("a[data-pane]");
    if (link && !event.ctrlKey && !event.metaKey && !event.shiftKey && event.button === 0) {
      var pane = document.getElementById("blob-pane");
      if (!pane) { return; }  // no pane on this page: follow the link normally
      event.preventDefault();
      openPane(pane, link);
    }
  });

  function closePane() {
    var pane = document.getElementById("blob-pane");
    if (!pane) { return; }
    pane.hidden = true;
    pane.textContent = "";
    var split = document.querySelector("[data-split]");
    if (split) { split.classList.remove("has-pane"); }
    document.querySelectorAll(".active-row").forEach(function (el) { el.classList.remove("active-row"); });
  }

  function openPane(pane, link) {
    var url = new URL(link.getAttribute("href"), window.location.href);
    url.searchParams.set("partial", "1");
    pane.hidden = false;
    pane.textContent = "Loading...";
    var split = document.querySelector("[data-split]");
    if (split) { split.classList.add("has-pane"); }

    document.querySelectorAll(".active-row").forEach(function (el) { el.classList.remove("active-row"); });
    var row = link.closest("tr, li");
    if (row) { row.classList.add("active-row"); }
    link.classList.add("active-row");

    fetch(url.toString(), { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) { throw new Error("HTTP " + r.status); }
        return r.text();
      })
      .then(function (html) {
        // The fragment is rendered server-side with every recovered string
        // escaped, and the CSP forbids inline script, so this is safe to insert.
        pane.innerHTML = html;
        var hit = pane.querySelector(".hl-line");
        if (hit) { hit.scrollIntoView({ block: "center" }); }
        else { pane.scrollTop = 0; }
        // Below the responsive breakpoint the pane joins the normal page
        // flow instead of scrolling in its own box, so this can change
        // whether the page itself has anything to jump through.
        window.dispatchEvent(new Event("resize"));
      })
      .catch(function () {
        window.location.href = link.getAttribute("href");  // fall back to the full page
      });
  }
})();
