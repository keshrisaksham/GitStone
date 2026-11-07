// No inline scripts anywhere (the CSP forbids them); behaviour hooks onto data- attributes.
// common.js: confirm dialogs, the ref picker, contextual back links, the jump-to-top/bottom button.
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

  // ---- contextual "back" link: <a data-back-link hidden> on a page you only
  // ever land on by following a link (a blob, a commit, a diff...) points back
  // at wherever that was, so there's an in-app way back besides the browser's
  // own Back button. Driven by document.referrer rather than a query-string
  // parameter, so it works for every link to these pages without every
  // template having to thread one through. Same-origin only, and never shown
  // on a plain reload (referrer == this exact page). Optional
  // data-back-fallback(-label) gives a default target/label (e.g. "Secrets
  // scan") to use instead of hiding the link when there's no usable referrer.
  (function initBackLinks() {
    var links = document.querySelectorAll("[data-back-link]");
    if (!links.length) { return; }
    var ref = document.referrer;
    var here = window.location.origin + window.location.pathname + window.location.search;
    var useRef = ref && ref.indexOf(window.location.origin + "/") === 0 && ref !== here;
    links.forEach(function (link) {
      if (useRef) {
        var path = ref.slice(window.location.origin.length);
        var label = "Back";
        if (/\/search\//.test(path)) { label = "Back to search"; }
        else if (/\/secrets(\?|$)/.test(path)) { label = "Back to secrets scan"; }
        else if (/\/commits\//.test(path)) { label = "Back to commits"; }
        else if (/\/commit\//.test(path)) { label = "Back to commit"; }
        else if (/\/compare(\?|$)/.test(path)) { label = "Back to compare"; }
        else if (/\/difference(\?|$)/.test(path)) { label = "Back to difference"; }
        else if (/\/timeline(\?|$)/.test(path)) { label = "Back to timeline"; }
        else if (/\/tree\//.test(path)) { label = "Back to files"; }
        link.href = ref;
        link.textContent = "← " + label;
        link.hidden = false;
        return;
      }
      var fallback = link.getAttribute("data-back-fallback");
      if (fallback) {
        link.href = fallback;
        link.textContent = "← " + (link.getAttribute("data-back-fallback-label") || "Back");
        link.hidden = false;
      } else {
        link.hidden = true;
      }
    });
  })();

  // ---- jump to top/bottom: one button that does whichever of the two is
  // useful from where you are -- an arrow pointing at wherever a click takes
  // you, flipping direction once you're past the midpoint of the page. Shown
  // only once the page actually has enough to scroll through.
  (function initJumpNav() {
    var btn = document.querySelector("[data-jump-nav]");
    if (!btn) { return; }
    var SHOW_PAST = 200;  // page must overflow the viewport by this much
    var ticking = false;
    var direction = "bottom";

    function update() {
      ticking = false;
      var doc = document.documentElement;
      var scrollable = doc.scrollHeight - window.innerHeight;
      btn.hidden = scrollable < SHOW_PAST;
      if (btn.hidden) { return; }
      var top = window.scrollY || doc.scrollTop;
      direction = top > scrollable / 2 ? "top" : "bottom";
      btn.textContent = direction === "top" ? "↑" : "↓";
      var label = direction === "top" ? "Jump to top" : "Jump to bottom";
      btn.title = label;
      btn.setAttribute("aria-label", label);
    }

    function onScrollOrResize() {
      if (ticking) { return; }
      ticking = true;
      window.requestAnimationFrame(update);
    }

    window.addEventListener("scroll", onScrollOrResize, { passive: true });
    window.addEventListener("resize", onScrollOrResize);
    btn.addEventListener("click", function () {
      var doc = document.documentElement;
      window.scrollTo({ top: direction === "top" ? 0 : doc.scrollHeight, behavior: "smooth" });
    });
    update();
  })();
})();
