// functions.js: the search box and filters on the Help page. Without
// JavaScript every function is simply listed.
(function () {
  "use strict";

  var search = document.querySelector("[data-fn-search]");
  var chips = document.querySelectorAll("[data-fn-filter]");
  if (!search || !chips.length) { return; }
  var current = "all";

  function matches(card) {
    var text = search.value.trim().toLowerCase();
    if (text && (card.getAttribute("data-name") || "").indexOf(text) === -1) { return false; }
    var kinds = (card.getAttribute("data-kinds") || "").split(" ");
    var network = card.getAttribute("data-network") === "yes";
    if (current === "all") { return true; }
    if (current === "network") { return network; }
    if (current === "offline") { return !network; }
    return kinds.indexOf(current) !== -1;
  }

  function apply() {
    var shown = 0;
    document.querySelectorAll("[data-fn-section]").forEach(function (section) {
      var visible = 0;
      section.querySelectorAll("[data-fn-card]").forEach(function (card) {
        var ok = matches(card);
        card.hidden = !ok;
        if (ok) { visible++; }
      });
      section.hidden = visible === 0;
      var jump = section.id && document.querySelector('[data-fn-jump="' + section.id + '"]');
      if (jump) { jump.hidden = visible === 0; }
      shown += visible;
    });
    var empty = document.querySelector("[data-fn-empty]");
    if (empty) { empty.hidden = shown !== 0; }
  }

  search.addEventListener("input", apply);
  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      current = chip.getAttribute("data-fn-filter");
      chips.forEach(function (c) {
        var on = c === chip;
        c.classList.toggle("is-on", on);
        c.setAttribute("aria-pressed", on ? "true" : "false");
      });
      apply();
    });
  });
})();
