/* Fiveserver Admin Panel — client-side JS */
"use strict";

/* ── Theme toggle ───────────────────────────────────────── */
function applyTheme(pref) {
  var html = document.documentElement;
  if (pref === "light")      html.dataset.theme = "light";
  else if (pref === "dark")  html.dataset.theme = "dark";
  else                       delete html.dataset.theme;
  var dark = pref === "dark" || (!pref && window.matchMedia("(prefers-color-scheme: dark)").matches);
  window.__theme = dark ? "dark" : "light";
  document.querySelectorAll(".theme-btn").forEach(function (b) {
    b.classList.toggle("active", b.dataset.themeVal === (pref || "auto"));
  });
}

/* ── Sortable table headers ─────────────────────────────── */
function initSortableHeaders() {
  document.querySelectorAll("th.sortable").forEach(function (th) {
    th.addEventListener("click", function () {
      var col = th.dataset.sort;
      if (!col) return;
      var url = new URL(window.location.href);
      var currentSort = url.searchParams.get("sort");
      var currentDir  = url.searchParams.get("dir") || "asc";
      var nextDir = (currentSort === col && currentDir === "asc") ? "desc" : "asc";
      url.searchParams.set("sort", col);
      url.searchParams.set("dir", nextDir);
      url.searchParams.set("offset", "0");
      window.location.href = url.toString();
    });
  });
}

document.addEventListener("DOMContentLoaded", function () {
  initSortableHeaders();
  var stored = localStorage.getItem("theme") || "auto";
  applyTheme(stored === "auto" ? null : stored);
  document.querySelectorAll(".theme-btn").forEach(function (b) {
    b.classList.toggle("active", b.dataset.themeVal === stored);
    b.addEventListener("click", function () {
      var val = b.dataset.themeVal;
      localStorage.setItem("theme", val);
      applyTheme(val === "auto" ? null : val);
    });
  });
});
