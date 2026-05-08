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

document.addEventListener("DOMContentLoaded", function () {
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
