(function () {
  "use strict";

  function debounce(fn, delay) {
    var timer;
    return function () {
      var args = arguments;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(null, args); }, delay);
    };
  }

  function queryTokens(value) {
    return value.toLocaleLowerCase().split(/[^\p{L}\p{N}_]+/u).filter(Boolean);
  }

  function searchPage() {
    var input = document.getElementById("search-input");
    var output = document.getElementById("search-results");
    var status = document.getElementById("search-status");
    var dataElement = document.getElementById("search-data");
    if (!input || !output || !dataElement) return;
    var records = JSON.parse(dataElement.textContent || "[]");
    var activeKinds = {};
    document.querySelectorAll("[data-search-kind]").forEach(function (button) {
      activeKinds[button.dataset.searchKind] = true;
      button.addEventListener("click", function () {
        activeKinds[button.dataset.searchKind] = !activeKinds[button.dataset.searchKind];
        button.classList.toggle("is-selected", activeKinds[button.dataset.searchKind]);
        render(input.value);
      });
    });
    function render(value) {
      var tokens = queryTokens(value);
      if (!tokens.length) {
        output.innerHTML = '<div class="empty-state">Enter a query to search this corpus.</div>';
        status.textContent = "";
        return;
      }
      var matches = records.filter(function (record) {
        var haystack = [record.title, record.text, (record.tags || []).join(" "), record.channel].join(" ").toLocaleLowerCase();
        return activeKinds[record.kind] && tokens.every(function (token) { return haystack.indexOf(token) !== -1; });
      });
      matches.sort(function (a, b) {
        var aExact = a.title.toLocaleLowerCase() === value.toLocaleLowerCase().trim();
        var bExact = b.title.toLocaleLowerCase() === value.toLocaleLowerCase().trim();
        if (aExact !== bExact) return aExact ? -1 : 1;
        var aPrefix = a.title.toLocaleLowerCase().indexOf(value.toLocaleLowerCase().trim()) === 0;
        var bPrefix = b.title.toLocaleLowerCase().indexOf(value.toLocaleLowerCase().trim()) === 0;
        if (aPrefix !== bPrefix) return aPrefix ? -1 : 1;
        return a.title.localeCompare(b.title) || a.id.localeCompare(b.id);
      });
      status.textContent = matches.length + " result" + (matches.length === 1 ? "" : "s");
      output.innerHTML = matches.length ? matches.map(function (record) {
        return '<article class="search-result"><span class="result-kind">' + escapeHtml(record.kind) + '</span><h2><a href="' + escapeAttribute(record.url) + '">' + escapeHtml(record.title) + '</a></h2><p>' + escapeHtml(record.text) + '</p><span class="muted">' + escapeHtml(record.channel || "") + '</span></article>';
      }).join("") : '<div class="empty-state">No records match every search token.</div>';
      if (window.history && window.history.replaceState) {
        var query = value ? "?q=" + encodeURIComponent(value) : "";
        window.history.replaceState(null, "", window.location.pathname + query);
      }
    }
    function escapeHtml(value) { return String(value).replace(/[&<>"']/g, function (char) { return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]; }); }
    function escapeAttribute(value) { return escapeHtml(value); }
    input.addEventListener("input", debounce(function () { render(input.value); }, 120));
    render(input.value);
  }

  function filters() {
    document.querySelectorAll("[data-filter-input]").forEach(function (input) {
      var target = document.getElementById(input.dataset.filterInput);
      if (!target) return;
      input.addEventListener("input", function () {
        var value = input.value.toLocaleLowerCase().trim();
        target.querySelectorAll("[data-filter-value]").forEach(function (row) {
          row.hidden = value && row.dataset.filterValue.indexOf(value) === -1;
        });
      });
    });
    document.querySelectorAll("[data-filter-group]").forEach(function (button) {
      button.addEventListener("click", function () {
        var group = button.dataset.filterGroup;
        document.querySelectorAll('[data-filter-group="' + group + '"]').forEach(function (item) { item.classList.remove("is-selected"); });
        button.classList.add("is-selected");
        var value = button.dataset.filterValue;
        if (group === "ideas") {
          document.querySelectorAll(".idea-card").forEach(function (card) {
            card.hidden = value !== "all" && card.querySelector(".fit-pill") && card.querySelector(".fit-pill").textContent.trim() !== value;
          });
        }
        if (group === "claims") {
          document.querySelectorAll(".claim-card").forEach(function (card) {
            card.hidden = value !== "all" && card.dataset.verification !== value;
          });
        }
      });
    });
    var claimType = document.querySelector("[data-filter-select='claim-type']");
    if (claimType) claimType.addEventListener("change", function () {
      document.querySelectorAll(".claim-card").forEach(function (card) { card.hidden = claimType.value !== "all" && card.dataset.claimType !== claimType.value; });
    });
  }

  searchPage();
  filters();
}());
