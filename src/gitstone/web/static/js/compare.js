// compare.js: the Compare page's version picker and swap button.
(function () {
  "use strict";

  // ---- Compare: a version picker (branches, tags, commits) instead of a bare <datalist>.
  // The list is fetched as you type, always starts from "everything" when opened (a
  // datalist shows only what matches the text already in the box), and works from the
  // keyboard. Without JavaScript the plain text boxes and their datalist still work.
  (function () {
    var form = document.querySelector("form[data-versions-url]");
    if (!form || !window.fetch) { return; }
    var url = form.getAttribute("data-versions-url");
    var GROUPS = { branch: "Branches", tag: "Tags", remote: "Remote branches", commit: "Commits" };

    form.querySelectorAll("[data-version-field]").forEach(function (field) {
      var input = field.querySelector("[data-version-input]");
      var list = field.querySelector(".combo-list");
      var toggle = field.querySelector("[data-version-toggle]");
      var picked = field.querySelector("[data-version-picked]");
      var options = [], active = -1, seq = 0, timer = null;
      input.removeAttribute("list");  // our list replaces the native one

      function isOpen() { return !list.hidden; }
      function close() {
        list.hidden = true; active = -1;
        input.setAttribute("aria-expanded", "false");
        input.removeAttribute("aria-activedescendant");
      }
      function setActive(i, scroll) {
        var nodes = list.querySelectorAll("[role=option]");
        if (!nodes.length) { active = -1; return; }
        active = (i + nodes.length) % nodes.length;
        nodes.forEach(function (n, k) { n.classList.toggle("is-active", k === active); n.setAttribute("aria-selected", k === active ? "true" : "false"); });
        input.setAttribute("aria-activedescendant", nodes[active].id);
        if (scroll !== false) { nodes[active].scrollIntoView({ block: "nearest" }); }
      }
      function note(text, cls) {
        var li = document.createElement("li");
        li.className = "combo-note " + (cls || "");
        li.setAttribute("role", "presentation");
        li.textContent = text;
        list.textContent = "";
        list.appendChild(li);
      }
      function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) { e.className = cls; }
        if (text !== undefined) { e.textContent = text; }  // text, never markup
        return e;
      }
      function render(found, current) {
        options = found; list.textContent = ""; active = -1;
        if (!found.length) {
          note("No matching version. Press Compare to try exactly what you typed.");
          return;
        }
        var group = null, preset = -1;
        found.forEach(function (v, i) {
          if (v.kind !== group) {
            group = v.kind;
            var h = el("li", "combo-heading", GROUPS[v.kind] || v.kind);
            h.setAttribute("role", "presentation");
            list.appendChild(h);
          }
          var li = el("li", "combo-option");
          li.id = list.id + "-" + i;
          li.setAttribute("role", "option");
          li.setAttribute("aria-selected", "false");
          li.setAttribute("data-index", String(i));
          var top = el("span", "combo-main");
          top.appendChild(el("strong", "", v.label));
          if (v.kind !== "commit") { top.appendChild(el("code", "", v.short)); }
          var sub = el("span", "combo-sub", [v.summary, v.date].filter(Boolean).join("  \u00b7  "));
          li.appendChild(top); li.appendChild(sub);
          if (v.value === current || v.sha === current) { li.classList.add("is-current"); preset = i; }
          list.appendChild(li);
        });
        if (preset >= 0) { setActive(preset); }
      }
      function show(query) {
        var mine = ++seq;
        list.hidden = false;
        input.setAttribute("aria-expanded", "true");
        if (!options.length) { note("Loading\u2026", "is-loading"); }
        fetch(url + "?q=" + encodeURIComponent(query), { credentials: "same-origin" })
          .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
          .then(function (found) { if (mine === seq && isOpen()) { render(found, query ? "" : input.value.trim()); } })
          .catch(function () { if (mine === seq) { note("Could not load versions. You can still type one."); } });
      }
      function choose(i) {
        var v = options[i];
        if (!v) { return; }
        input.value = v.value;
        input.removeAttribute("aria-invalid");
        picked.className = "combo-picked muted";
        picked.textContent = "";
        picked.appendChild(el("code", "", v.short));
        picked.appendChild(document.createTextNode(" " + v.summary));
        close();
      }

      input.addEventListener("input", function () {
        picked.textContent = ""; input.removeAttribute("aria-invalid");
        window.clearTimeout(timer);
        timer = window.setTimeout(function () { show(input.value.trim()); }, 120);
      });
      input.addEventListener("click", function () { if (!isOpen()) { show(""); } });
      input.addEventListener("keydown", function (event) {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          if (!isOpen()) { show(""); } else { setActive(active + (event.key === "ArrowDown" ? 1 : -1)); }
        } else if (event.key === "Enter" && isOpen() && active >= 0) {
          event.preventDefault();
          var node = list.querySelectorAll("[role=option]")[active];
          choose(parseInt(node.getAttribute("data-index"), 10));
        } else if (event.key === "Escape" && isOpen()) {
          event.preventDefault(); close();
        } else if (event.key === "Tab") { close(); }
      });
      toggle.addEventListener("mousedown", function (event) { event.preventDefault(); });
      toggle.addEventListener("click", function () {
        if (isOpen()) { close(); } else { input.focus(); show(""); }
      });
      // mousedown, not click: the input must not lose focus (and close the list) first
      list.addEventListener("mousedown", function (event) {
        event.preventDefault();
        var opt = event.target.closest && event.target.closest("[role=option]");
        if (opt) { choose(parseInt(opt.getAttribute("data-index"), 10)); }
      });
      document.addEventListener("mousedown", function (event) { if (!field.contains(event.target)) { close(); } });
    });

    // swap: exchange the two boxes (and look again, if both are filled in)
    var swap = form.querySelector("[data-compare-swap]");
    if (swap) {
      swap.addEventListener("click", function (event) {
        event.preventDefault();
        var a = form.elements.base, b = form.elements.head, t = a.value;
        a.value = b.value; b.value = t;
        if (a.value.trim() && b.value.trim()) { form.requestSubmit(); }
      });
    }
  })();
})();
