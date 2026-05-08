/* Fiveserver public site — all client-side JS in one file, no inline scripts */
"use strict";

/* ── Registration form hash ─────────────────────────────── */
function makeHash() {
  var a = document.getElementById("serial").value;
  a = a.replace(/^\s+/, "").replace(/\s+$/, "").replace(/-/g, "").toUpperCase();
  if (!a.match(/^[A-Z0-9]{20}$/)) {
    alert("Invalid serial number. Must be 20 alphanumeric characters.");
    return false;
  }
  document.getElementById("serial").value = a;
  while (a.length < 36) { a += "\0"; }
  var u = document.getElementById("username").value.replace(/^\s+/, "").replace(/\s+$/, "");
  if (u.length < 3 || !u.match(/^[0-9a-zA-Z]+$/)) {
    alert("Invalid username. Must be 3+ characters, letters and digits only.");
    return false;
  }
  var p = document.getElementById("password").value;
  if (p.length < 3) {
    alert("Password too short. Must be at least 3 characters.");
    return false;
  }
  document.getElementById("hash").value = hex_md5(a + u + "-" + p);
  return true;
}

/* ── Mobile nav hamburger ───────────────────────────────── */
function initNavHamburger() {
  var btn = document.querySelector(".pub-nav-hamburger");
  var links = document.querySelector(".pub-nav-links");
  if (!btn || !links) return;
  btn.addEventListener("click", function () {
    links.classList.toggle("open");
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
      var currentDir  = url.searchParams.get("dir") || "desc";
      var nextDir = (currentSort === col && currentDir === "desc") ? "asc" : "desc";
      url.searchParams.set("sort", col);
      url.searchParams.set("dir", nextDir);
      url.searchParams.set("page", "1");
      window.location.href = url.toString();
    });
  });
}

/* ── Per-page selector ──────────────────────────────────── */
function initPerPageSelector() {
  var sel = document.getElementById("per-page-select");
  if (!sel) return;
  sel.addEventListener("change", function () {
    var url = new URL(window.location.href);
    url.searchParams.set("per_page", sel.value);
    url.searchParams.set("page", "1");
    window.location.href = url.toString();
  });
}

/* ── Division filter pills ──────────────────────────────── */
function initDivisionFilter() {
  document.querySelectorAll(".div-pill").forEach(function (pill) {
    pill.addEventListener("click", function (e) {
      e.preventDefault();
      var div = pill.dataset.div;
      var url = new URL(window.location.href);
      if (div === "all") {
        url.searchParams.delete("div");
      } else {
        url.searchParams.set("div", div);
      }
      url.searchParams.set("page", "1");
      window.location.href = url.toString();
    });
  });
}

/* ── Profile autocomplete ───────────────────────────────── */
function initProfileSearch() {
  var input = document.getElementById("profile-search");
  if (!input) return;
  var wrap = input.parentElement;
  wrap.style.position = "relative";

  var dropdown = document.createElement("div");
  dropdown.className = "autocomplete-dropdown";
  dropdown.style.display = "none";
  dropdown.style.top = (input.offsetHeight + 2) + "px";
  dropdown.style.left = "0";
  dropdown.style.width = input.offsetWidth + "px";
  wrap.appendChild(dropdown);

  var debounce;
  input.addEventListener("input", function () {
    clearTimeout(debounce);
    var term = input.value.trim();
    if (term.length < 3) { dropdown.style.display = "none"; return; }
    debounce = setTimeout(function () {
      fetch("/api/profiles/search?q=" + encodeURIComponent(term))
        .then(function (r) { return r.json(); })
        .then(function (names) {
          dropdown.innerHTML = "";
          if (!names.length) { dropdown.style.display = "none"; return; }
          names.forEach(function (name) {
            var item = document.createElement("div");
            item.className = "autocomplete-item";
            item.textContent = name;
            item.addEventListener("click", function () {
              input.value = name;
              dropdown.style.display = "none";
              input.closest("form").submit();
            });
            dropdown.appendChild(item);
          });
          dropdown.style.display = "block";
        })
        .catch(function () { dropdown.style.display = "none"; });
    }, 200);
  });

  document.addEventListener("click", function (e) {
    if (!wrap.contains(e.target)) dropdown.style.display = "none";
  });
}

/* ── Home carousel (live / recent matches) ──────────────── */
function initCarousel() {
  var wrap = document.querySelector(".carousel-wrap");
  if (!wrap) return;

  var slides    = wrap.querySelectorAll(".carousel-slide");
  var dots      = wrap.querySelectorAll(".carousel-dot");
  var prevBtn   = wrap.querySelector(".carousel-arrow.prev");
  var nextBtn   = wrap.querySelector(".carousel-arrow.next");
  var refreshBtn = wrap.querySelector(".carousel-refresh");
  var titleEl   = wrap.querySelector(".carousel-title");
  var onlineEl  = document.getElementById("stat-online");

  var titles = ["Live Matches", "Recent Matches"];
  var current = 0;

  function showSlide(idx) {
    current = (idx + slides.length) % slides.length;
    slides.forEach(function (s, i) { s.classList.toggle("active", i === current); });
    dots.forEach(function (d, i)   { d.classList.toggle("active", i === current); });
    if (titleEl) titleEl.textContent = titles[current];
  }

  if (prevBtn)   prevBtn.addEventListener("click",    function () { showSlide(current - 1); });
  if (nextBtn)   nextBtn.addEventListener("click",    function () { showSlide(current + 1); });
  dots.forEach(function (d, i) { d.addEventListener("click", function () { showSlide(i); }); });

  function escHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function buildLiveTable(matches) {
    if (!matches.length) {
      return '<div class="carousel-empty">No live matches right now</div>';
    }
    var rows = matches.map(function (m) {
      return "<tr>" +
        "<td>" + escHtml(m.home_profile || "?") + "</td>" +
        "<td class='score'>" + escHtml(m.score || "0:0") + "</td>" +
        "<td>" + escHtml(m.away_profile || "?") + "</td>" +
        "<td class='meta'>" + escHtml(m.lobby || "") + " · " + (m.match_time || 0) + " min</td>" +
        "</tr>";
    }).join("");
    return "<table class='carousel-table'><tbody>" + rows + "</tbody></table>";
  }

  function flag(teamId) {
    if (!teamId) return "";
    return "<img class='team-flag' src='/static/flags/" + escHtml(String(teamId)) + ".png'" +
      " onerror=\"this.style.visibility='hidden';this.style.width='0';this.style.margin='0'\" alt=''> ";
  }

  function buildRecentTable(matches) {
    if (!matches.length) {
      return '<div class="carousel-empty">No recent matches</div>';
    }
    var rows = matches.map(function (m) {
      return "<tr>" +
        "<td>" + flag(m.team_id_home) + "<a href='/profiles?q=" + encodeURIComponent(m.home_name || "") + "'>" + escHtml(m.home_name || "?") + "</a></td>" +
        "<td class='score'>" + escHtml(m.score_home) + "&ndash;" + escHtml(m.score_away) + "</td>" +
        "<td>" + flag(m.team_id_away) + "<a href='/profiles?q=" + encodeURIComponent(m.away_name || "") + "'>" + escHtml(m.away_name || "?") + "</a></td>" +
        "<td class='meta'>" + escHtml(m.played_on || "") + "</td>" +
        "</tr>";
    }).join("");
    return "<table class='carousel-table'><tbody>" + rows + "</tbody></table>";
  }

  function refresh() {
    fetch("/api/live-matches")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (slides[0]) slides[0].innerHTML = buildLiveTable(data.live || []);
        if (slides[1]) slides[1].innerHTML = buildRecentTable(data.finished || []);
        if (onlineEl)  onlineEl.textContent = data.online != null ? data.online : "—";
      })
      .catch(function () {
        if (slides[0]) slides[0].innerHTML = '<div class="carousel-empty">Could not load live matches</div>';
      });
  }

  if (refreshBtn) refreshBtn.addEventListener("click", refresh);

  showSlide(0);
  refresh();
  setInterval(refresh, 30000);
}

/* ── Boot ───────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", function () {
  initNavHamburger();
  initSortableHeaders();
  initPerPageSelector();
  initDivisionFilter();
  initProfileSearch();
  initCarousel();
});
