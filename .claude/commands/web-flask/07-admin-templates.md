Implement the admin HTML/CSS templates and stylesheet.

This step is templates + CSS only. No Python changes.

Create branch `web-flask/step-07-admin-templates` from `web-flask` before making any changes.

## Design goals

The previous admin was XML+XSLT — a painful experience. Build a proper, usable admin panel:
- Sidebar navigation always visible
- Dashboard with at-a-glance stats cards
- Clean data tables with action buttons
- Inline forms (no separate pages for simple actions)
- Works offline — no external CDN (all CSS in `flask-web/static/admin.css`)
- Readable on a desktop browser; mobile not required

## flask-web/static/admin.css

Write a complete stylesheet (~150-200 lines) covering:
- CSS reset / box-sizing
- Layout: `body { display: flex; }`, sidebar fixed-width (220px), main content fills rest
- Sidebar: dark background (#1e2a38), white nav links, active state highlight
- Topbar: light gray background, page title on left
- Cards: white background, subtle shadow, grid of 4 cards on dashboard
- Tables: `border-collapse: collapse`, alternating row colors, `th` with dark header
- Buttons: `.btn-primary` (blue), `.btn-danger` (red), `.btn-secondary` (gray), small size
- Forms: `input[type=text]`, `input[type=number]`, `select` — consistent styling
- Status badges: `.badge-online` (green), `.badge-offline` (gray), `.badge-locked` (orange)
- Log viewer: monospace font, dark background (#1a1a2e), white text, overflow scroll
- Pagination: Previous/Next links styled as buttons
- No JavaScript required for any admin functionality

## flask-web/templates/admin/layout.html

Base layout for all admin pages. Extends `templates/base.html`.

```html
<!-- Sidebar -->
<nav class="sidebar">
  <div class="sidebar-header">Fiveserver Admin</div>
  <ul>
    <li><a href="/admin">Dashboard</a></li>
    <li><a href="/admin/users">Users</a></li>
    <li><a href="/admin/banned">Ban List</a></li>
    <li><a href="/admin/log">Server Log</a></li>
    <li><a href="/admin/settings">Settings</a></li>
    <li class="divider"></li>
    <li><a href="/stats">Stats</a></li>
  </ul>
</nav>
<main class="content">
  <div class="topbar"><h1>{% block title %}{% endblock %}</h1></div>
  <div class="page-body">{% block body %}{% endblock %}</div>
</main>
```

## flask-web/templates/admin/home.html

Dashboard page. Template variables: `user_count`, `online_users` (list), `lobbies` (list from config).

- 3 stat cards: Total Users, Online Now, Active Lobbies
- Table of online users: columns Username, Lobby, Connected Since
- "No players online" message when list is empty

## flask-web/templates/admin/users.html

Template variables: `users` (list of dicts), `offset`, `limit`, `total`, `query`.

- Search box at top: `<form method="GET"><input name="q" value="{{ query }}"><button>Search</button></form>`
- Table columns: ID, Username, Serial (truncated), Locked, Deleted, Last Updated, Actions
- Actions per row: [Lock] button (POST to /admin/userlock), [Delete] button (POST to /admin/userkill)
- Pagination: Previous / Next links using offset+limit
- Locked users shown with `.badge-locked` badge

## flask-web/templates/admin/user_detail.html

Template variables: `user` (dict), `profiles` (list).

- User info card: ID, username, serial, locked status, updated_on
- Recovery link if user is locked (shows the `/modifyUser/<nonce>` URL)
- Profiles table: ordinal, name, rank, points, wins, losses, disconnects, seconds_played

## flask-web/templates/admin/profile_detail.html

Template variables: `profile` (dict), `stats` (dict with wins/losses/draws/goals/streak/best_streak/seconds_played).

- Stat cards: Rank, Points, Wins, Losses, Draws, Goals For, Goals Against, Win%, Play Time, Streak, Best Streak
- Recent matches table if available

## flask-web/templates/admin/log.html

Template variables: `lines` (list of strings), `line_count` (int).

- Line count selector: `<form method="GET"><select name="lines"><option>30</option><option>100</option><option>500</option></select></form>`
- Log output: `<pre class="log-viewer">{{ lines | join('\n') }}</pre>`
- Auto-scroll to bottom via inline `<script>document.querySelector('.log-viewer').scrollTop = 999999</script>`

## flask-web/templates/admin/banned.html

Template variables: `banned_list` (list of strings).

- Table of current banned IPs/networks with [Remove] button (POST to /admin/ban-remove)
- Add ban form at bottom: `<input name="ip" placeholder="192.168.1.0/24">` + [Add Ban] button

## flask-web/templates/admin/settings.html

Template variables: `debug` (bool), `max_users` (int), `store_settings` (bool), `roster_check` (bool).

- Single form with all settings:
  - Debug mode: checkbox
  - Max users: number input
  - Store player settings: checkbox
  - Roster hash check: checkbox
- Single [Save Settings] button → POST /admin/settings (or separate forms per section — your call, but single form is simpler)

## flask-web/templates/admin/confirm.html

Generic action result page. Template variable: `message` (string), optional `detail` (string).
Used by userlock (shows recovery URL), userkill (shows "User deleted"), server-ip, ps.

## flask-web/templates/stats/home.html and stats/users.html

Same structure as admin equivalents but without action buttons. Extends `admin/layout.html`.

## Verification

Start the dev server: `python flask-web/run.py`
- Open `https://localhost/admin` → dashboard renders with sidebar
- Open `https://localhost/admin/users` → users table renders
- Open `https://localhost/admin/log` → log viewer renders
- Open `https://localhost/admin/settings` → settings form renders
- CSS loads from `/static/admin.css`

`python -m unittest flask-web/tests/test_admin.py` — still passes.

After verification, merge `web-flask/step-07-admin-templates` → `web-flask`.
