// sort.js: click a <th data-sort-key="x"> to sort its table's rows by the
// matching data-sort-x attribute on each <tr>. Click again to reverse, a
// third click returns to the order the server sent (ranking, dedupe groups,
// ...), which a plain alphabetical sort would otherwise destroy for good.
(function () {
  "use strict";

  function baseOrder(tbody) {
    if (!tbody._baseOrder) {
      tbody._baseOrder = Array.prototype.slice.call(tbody.rows);
    }
    return tbody._baseOrder;
  }

  function sortRows(table, key, dir) {
    var tbody = table.tBodies[0];
    var rows = baseOrder(tbody).slice();
    if (dir !== "none") {
      rows.sort(function (a, b) {
        var av = a.getAttribute("data-sort-" + key) || "";
        var bv = b.getAttribute("data-sort-" + key) || "";
        var cmp = av < bv ? -1 : av > bv ? 1 : 0;
        return dir === "asc" ? cmp : -cmp;
      });
    }
    rows.forEach(function (row) { tbody.appendChild(row); });
  }

  document.addEventListener("click", function (event) {
    var th = event.target.closest("[data-sort-key]");
    if (!th) { return; }
    var table = th.closest("table");
    if (!table) { return; }
    var cycle = { none: "asc", asc: "desc", desc: "none" };
    var next = cycle[th.getAttribute("data-sort-dir") || "none"];
    table.querySelectorAll("[data-sort-key]").forEach(function (h) {
      h.removeAttribute("data-sort-dir");
      h.setAttribute("aria-sort", "none");
    });
    if (next !== "none") {
      th.setAttribute("data-sort-dir", next);
      th.setAttribute("aria-sort", next === "asc" ? "ascending" : "descending");
    }
    sortRows(table, th.getAttribute("data-sort-key"), next);
  });
})();
