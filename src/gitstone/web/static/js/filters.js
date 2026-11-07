// filters.js: filter result rows by text, severity, and duplicates; '/' focuses the filter box.
(function () {
  "use strict";

  // ---- row filtering: <input data-filter-rows="css selector"> hides rows whose
  // data-name doesn't contain the text; <div data-severity-filter> checkboxes
  // hide rows by their data-sev. Both can be active at once.
  function applyRowFilters() {
    var text = "";
    var rowSelector = null;
    var box = document.querySelector("[data-filter-rows]");
    if (box) { text = box.value.trim().toLowerCase(); rowSelector = box.getAttribute("data-filter-rows"); }
    var sevBox = document.querySelector("[data-severity-filter]");
    var allowed = null;
    if (sevBox) {
      allowed = {};
      sevBox.querySelectorAll("input[type=checkbox]").forEach(function (c) { allowed[c.value] = c.checked; });
    }
    // <input type=checkbox data-dedupe-filter>, checked: hide rows marked
    // data-dup="1" (a finding whose pattern + matched text repeats one
    // already shown -- see secrets.py's _mark_duplicates).
    var dedupeBox = document.querySelector("[data-dedupe-filter]");
    var hideDups = !!(dedupeBox && dedupeBox.checked);
    // data-dedupe-rows names every row the switch governs (a page can list
    // results in several tables/lists); without a text filter it also picks
    // the rows we count as "visible".
    var dedupeRows = dedupeBox && dedupeBox.getAttribute("data-dedupe-rows");
    var rows = document.querySelectorAll(rowSelector || dedupeRows || ".findings tbody tr");
    var visible = 0;
    rows.forEach(function (row) {
      var name = row.getAttribute("data-name");
      var sev = row.getAttribute("data-sev");
      var hide = (text && name !== null && name.indexOf(text) === -1) ||
                 (allowed && sev && allowed[sev] === false) ||
                 (hideDups && row.getAttribute("data-dup") === "1");
      row.hidden = !!hide;
      if (!hide) { visible++; }
    });
    // rows only the dedupe switch governs (another table on the same page)
    if (dedupeRows && rowSelector) {
      document.querySelectorAll(dedupeRows).forEach(function (row) {
        if (!row.matches(rowSelector)) { row.hidden = hideDups && row.getAttribute("data-dup") === "1"; }
      });
    }
    // <p class="filter-empty" hidden> right after the filtered table/list shows
    // when the filter leaves nothing visible.
    var empty = document.querySelector(".filter-empty");
    if (empty) { empty.hidden = visible !== 0; }
  }
  document.addEventListener("input", function (event) {
    if (event.target.matches && event.target.matches("[data-filter-rows]")) { applyRowFilters(); }
  });
  document.addEventListener("change", function (event) {
    if (event.target.matches && event.target.matches("[data-dedupe-filter]")) { applyRowFilters(); return; }
    if (event.target.closest && event.target.closest("[data-severity-filter]")) { applyRowFilters(); }
  });

  // "/" jumps to the page's filter/search box, like GitHub's file finder
  document.addEventListener("keydown", function (event) {
    if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) { return; }
    var tag = (event.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") { return; }
    var box = document.querySelector("[data-filter-rows], [data-search-input]");
    if (box) { event.preventDefault(); box.focus(); box.select && box.select(); }
  });
})();
