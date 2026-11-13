(function () {
  'use strict';

  // ---------- Helpers ----------
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function esc(s) { return s == null ? '' : String(s).replace(/[&<>"']/g, function (c) { return ESC[c]; }); }

  function fmtTime(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return iso || '—';
    var sameDay = d.toDateString() === new Date().toDateString();
    var time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    return sameDay ? time : d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ', ' + time;
  }
  function fmtDur(sec) {
    if (sec == null || isNaN(sec)) return '—';
    sec = Math.max(0, Math.round(sec));
    if (sec < 60) return sec + 's';
    if (sec < 3600) return Math.floor(sec / 60) + 'm ' + String(sec % 60).padStart(2, '0') + 's';
    if (sec < 86400) return Math.floor(sec / 3600) + 'h ' + String(Math.floor(sec % 3600 / 60)).padStart(2, '0') + 'm';
    return Math.floor(sec / 86400) + 'd ' + Math.floor(sec % 86400 / 3600) + 'h';
  }
  function since(iso) { return (Date.now() - new Date(iso).getTime()) / 1000; }
  function timeTag(iso) { return iso ? '<time title="' + esc(iso) + '">' + esc(fmtTime(iso)) + '</time>' : '—'; }

  function highlight(json) {
    return String(json).replace(/[&<>]/g, function (c) { return ESC[c]; }).replace(
      /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g,
      function (m, str, colon, lit) {
        if (str) return colon ? '<span class="j-key">' + str + '</span>' + colon : '<span class="j-str">' + str + '</span>';
        return '<span class="' + (lit ? 'j-lit' : 'j-num') + '">' + m + '</span>';
      });
  }

  var toastTimer;
  function toast(msg) {
    var el = $('#toast');
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove('show'); }, 1800);
  }
  function copy(text, label) {
    var done = function () { toast((label || 'Copied') + ' to clipboard'); };
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text).then(done, function () { toast('Copy failed'); });
    var ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); done(); } catch (e) { toast('Copy failed'); }
    ta.remove();
  }
  function api(url, opts) {
    return fetch(url, Object.assign({ credentials: 'same-origin' }, opts)).then(function (r) {
      if (r.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.href); throw new Error('unauthenticated'); }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  // ---------- Every page: theme, copy buttons, local times, JSON colouring ----------
  $('#theme-toggle') && $('#theme-toggle').addEventListener('click', function () {
    var light = document.documentElement.dataset.theme !== 'light';
    if (light) document.documentElement.dataset.theme = 'light'; else delete document.documentElement.dataset.theme;
    try { localStorage.setItem('theme', light ? 'light' : 'dark'); } catch (e) {}
  });
  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest('[data-copy]');
    if (!btn) return;
    var target = btn.dataset.copy.charAt(0) === '#' ? $(btn.dataset.copy).textContent : btn.dataset.copy;
    copy(target);
  });
  $$('time[data-ts]').forEach(function (t) { t.title = t.dataset.ts; t.textContent = fmtTime(t.dataset.ts); });
  $$('pre.json').forEach(function (p) { p.innerHTML = highlight(p.textContent); });

  if (!$('#console')) return;

  // ---------- Console state ----------
  var SEVS = ['critical', 'major', 'minor', 'warning', 'info'];
  var SEV_LABEL = { critical: 'Critical', major: 'Major', minor: 'Minor', warning: 'Warning', info: 'Info' };
  var POLL_MS = 4000;
  var S = { data: null, view: 'alarms', scope: 'active', sevs: {}, evState: '', q: '', paused: false, error: false,
            lastOk: 0, drawer: null, seenAlarms: null, seenEvents: null, lastJson: '' };

  function sevBadge(sev, label) {
    return '<span class="badge sev-' + esc(sev) + '">' + esc(label || SEV_LABEL[sev] || sev) + '</span>';
  }
  function matches(obj, payload) {
    if (!S.q) return true;
    var hay = [obj.rule, obj.alarm_id, obj.source, obj.description, obj.state, obj.severity_label, obj.id].join(' ');
    if (payload) hay += ' ' + JSON.stringify(payload);
    return hay.toLowerCase().indexOf(S.q) !== -1;
  }
  function anySev() { return SEVS.some(function (s) { return S.sevs[s]; }); }

  // ---------- Render: KPIs ----------
  function renderKpis() {
    var st = S.data.stats, html = '';
    html += '<div class="kpi' + (st.active ? '' : ' zero') + '"><div class="kpi-label">Active alarms</div><div class="kpi-value">' + st.active +
            '</div><div class="kpi-sub">' + (st.active ? st.unacked + ' unacknowledged' : 'All clear') + '</div></div>';
    SEVS.slice(0, 4).forEach(function (s) {
      var n = st.by_severity[s] || 0;
      html += '<button class="kpi sev-' + s + (n ? '' : ' zero') + (S.sevs[s] ? ' on' : '') + '" data-sev="' + s + '" title="Filter by ' + SEV_LABEL[s] + '">' +
              '<div class="kpi-label">' + SEV_LABEL[s] + '</div><div class="kpi-value">' + n + '</div><div class="kpi-sub">active</div></button>';
    });
    var mttr = st.mttr_s == null ? '—' : fmtDur(st.mttr_s);
    html += '<div class="kpi sev-cleared"><div class="kpi-label">Cleared</div><div class="kpi-value">' + st.cleared +
            '</div><div class="kpi-sub">Mean time to clear ' + mttr + '</div></div>';
    html += '<div class="kpi"><div class="kpi-label">Webhooks received</div><div class="kpi-value">' + st.events_total +
            '</div><div class="kpi-sub">' + st.events_window + ' in the last hour</div></div>';
    $('#kpis').innerHTML = html;
  }

  // ---------- Render: event-rate chart ----------
  function renderChart() {
    var series = S.data.stats.series, n = series.length, H = 80, W = n * 10;
    var max = Math.max.apply(null, series.map(function (b) { return b.raised + b.cleared; }).concat([1]));
    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" role="img" aria-label="Webhooks per minute, last 60 minutes">' +
              '<line x1="0" x2="' + W + '" y1="' + (H - 0.5) + '" y2="' + (H - 0.5) + '"/>';
    series.forEach(function (b, i) {
      var hr = b.raised / max * (H - 6), hc = b.cleared / max * (H - 6), x = i * 10;
      svg += '<g data-i="' + i + '"><rect class="slot" x="' + x + '" y="0" width="10" height="' + H + '"/>';
      if (b.raised) svg += '<rect class="raised" x="' + (x + 1.5) + '" y="' + (H - 1 - hr) + '" width="7" height="' + hr + '" rx="1"/>';
      if (b.cleared) svg += '<rect class="cleared" x="' + (x + 1.5) + '" y="' + (H - 1 - hr - hc - (b.raised ? 1 : 0)) + '" width="7" height="' + hc + '" rx="1"/>';
      svg += '</g>';
    });
    $('#chart').innerHTML = svg + '</svg><span class="chart-max">' + max + '/min</span><span class="chart-tip" hidden></span>';
  }
  $('#chart').addEventListener('mousemove', function (ev) {
    var g = ev.target.closest('g[data-i]'), tip = $('.chart-tip', this);
    if (!g || !S.data) { if (tip) tip.hidden = true; return; }
    var i = +g.dataset.i, b = S.data.stats.series[i];
    var t = new Date(b.t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    tip.textContent = t + ' · ' + b.raised + ' raised · ' + b.cleared + ' cleared';
    tip.style.left = Math.min(Math.max((i + 0.5) / S.data.stats.series.length * 100, 12), 88) + '%';
    tip.hidden = false;
  });
  $('#chart').addEventListener('mouseleave', function () { var tip = $('.chart-tip', this); if (tip) tip.hidden = true; });

  // ---------- Render: alarms table ----------
  function alarmKey(a) { return a.alarm_id + '|' + a.last_event_id; }
  function renderAlarms() {
    var all = S.data.alarms, st = S.data.stats;
    $('#n-active').textContent = st.active; $('#n-cleared').textContent = st.cleared; $('#n-all').textContent = all.length;
    var rows = all.filter(function (a) {
      if (S.scope === 'active' && !a.active) return false;
      if (S.scope === 'cleared' && a.active) return false;
      if (anySev() && !S.sevs[a.severity]) return false;
      return matches(a);
    });
    var seen = S.seenAlarms, next = {};
    all.forEach(function (a) { next[alarmKey(a)] = 1; });
    var html = rows.map(function (a) {
      var fresh = seen && !seen[alarmKey(a)];
      var dur = a.active
        ? (a.raised_at ? '<span class="num" data-since="' + esc(a.raised_at) + '">' + fmtDur(since(a.raised_at)) + '</span>' : '—')
        : '<span class="num muted">' + fmtDur(a.duration_s) + '</span>';
      var ack = a.ack
        ? '<span class="acked"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>' + esc(a.ack.by) + '</span>'
        : (a.active ? '<button class="btn sm" data-ack="' + esc(a.alarm_id) + '">Acknowledge</button>' : '<span class="muted">—</span>');
      return '<tr class="row sev-' + esc(a.severity) + (a.active ? '' : ' is-cleared') + (fresh ? ' flash' : '') +
        (S.drawer && S.drawer.alarmId === a.alarm_id ? ' selected' : '') + '" data-alarm="' + esc(a.alarm_id) + '" tabindex="0">' +
        '<td class="stripe">' + sevBadge(a.severity, a.severity_label) + '</td>' +
        '<td class="wrap"><div class="title">' + esc(a.rule || 'Alarm') + '</div><div class="sub">' + esc(a.alarm_id) + '</div></td>' +
        '<td>' + (a.source ? esc(a.source) : '<span class="muted">—</span>') + '</td>' +
        '<td><span class="state' + (a.active ? '' : ' cleared') + '">' + esc(a.state) + '</span></td>' +
        '<td class="num">' + timeTag(a.raised_at || a.first_seen) + '</td>' +
        '<td>' + dur + '</td>' +
        '<td><span class="pill">' + a.event_count + '</span>' + (a.raise_count > 1 ? ' <span class="muted" title="Raised ' + a.raise_count + ' times">↻ ' + a.raise_count + '</span>' : '') + '</td>' +
        '<td>' + ack + '</td></tr>';
    }).join('');
    if (!rows.length) {
      var filtered = S.q || anySev();
      html = '<tr><td colspan="8"><div class="empty">' + (!all.length
        ? '<h3>Waiting for the first webhook</h3><div>POST an alarm to <span class="mono">/webhook/alarms</span> and it will appear here.</div><a class="btn" href="#integration">Send a test alarm</a>'
        : filtered ? '<h3>No alarms match</h3><div>Try clearing the search or severity filters.</div><button class="btn" id="reset-filters">Clear filters</button>'
        : S.scope === 'active' ? '<h3>All clear</h3><div>No active alarms right now.</div>'
        : '<h3>Nothing here yet</h3><div>No alarms in this view.</div>') + '</div></td></tr>';
    }
    $('#alarms-body').innerHTML = html;
    $('#alarms-foot').textContent = 'Showing ' + rows.length + ' of ' + all.length + ' alarm' + (all.length === 1 ? '' : 's');
    S.seenAlarms = next;
    $$('#sev-chips .chip').forEach(function (c) { c.classList.toggle('on', !!S.sevs[c.dataset.sev]); });
    $$('#scope button').forEach(function (b) { b.classList.toggle('on', b.dataset.scope === S.scope); });
  }

  // ---------- Render: event log ----------
  function renderEvents() {
    var all = S.data.events;
    var rows = all.filter(function (e) {
      if (S.evState && String(e.state).toUpperCase() !== S.evState) return false;
      return matches(e, e.payload);
    });
    var seen = S.seenEvents, next = {};
    all.forEach(function (e) { next[e.id] = 1; });
    var html = rows.map(function (e) {
      var cleared = String(e.state).toUpperCase() === 'CLEARED';
      return '<tr class="row' + (seen && !seen[e.id] ? ' flash' : '') + '" data-alarm="' + esc(e.alarm_id) + '" data-event="' + esc(e.id) + '" tabindex="0">' +
        '<td class="num stripe sev-' + (cleared ? 'cleared' : esc(e.severity)) + '">' + timeTag(e.received_at) + '</td>' +
        '<td><span class="state' + (cleared ? ' cleared' : '') + '">' + esc(e.state) + '</span></td>' +
        '<td>' + (e.severity_label ? sevBadge(e.severity, e.severity_label) : '<span class="muted">—</span>') + '</td>' +
        '<td>' + esc(e.rule || '—') + '</td>' +
        '<td class="mono">' + esc(e.alarm_id) + '</td>' +
        '<td>' + (e.source ? esc(e.source) : '<span class="muted">—</span>') + '</td>' +
        '<td class="mono muted">' + esc(String(e.id).slice(0, 8)) + '</td></tr>';
    }).join('');
    if (!rows.length) {
      html = '<tr><td colspan="7"><div class="empty">' + (all.length
        ? '<h3>No events match</h3><div>Try a different search or state.</div>'
        : '<h3>No webhooks received yet</h3><div>Every POST to <span class="mono">/webhook/alarms</span> is logged here.</div>') + '</div></td></tr>';
    }
    $('#events-body').innerHTML = html;
    var total = S.data.stats.events_total;
    $('#events-foot').textContent = 'Showing ' + rows.length + ' of ' + total + ' event' + (total === 1 ? '' : 's') +
      (total > all.length ? ' (latest ' + all.length + ' loaded)' : '');
    S.seenEvents = next;
  }

  function renderNav() {
    var st = S.data.stats, a = $('#nav-alarms'), e = $('#nav-events');
    a.hidden = !st.active; a.textContent = st.active;
    a.classList.toggle('hot', (st.by_severity.critical || 0) > 0);
    e.hidden = !st.events_total; e.textContent = st.events_total;
    document.title = (st.active ? '(' + st.active + ') ' : '') + 'Alarm Console · Webhook Receiver';
  }
  function render() {
    if (!S.data) return;
    renderKpis(); renderChart(); renderAlarms(); renderEvents(); renderNav();
  }

  // ---------- Live polling ----------
  function renderLive() {
    var el = $('#live');
    el.classList.toggle('paused', S.paused);
    el.classList.toggle('error', S.error && !S.paused);
    $('#live-text').textContent = S.paused ? 'Paused' : S.error ? 'Reconnecting…' : S.lastOk ? 'Live' : 'Connecting…';
  }
  function poll(force) {
    if ((S.paused || document.hidden) && !force) return Promise.resolve();
    return api('/api/console').then(function (data) {
      S.error = false; S.lastOk = Date.now();
      var json = JSON.stringify(data);
      if (json !== S.lastJson) {
        S.lastJson = json; S.data = data; render();
        if (S.drawer) refreshDrawer();
      }
      renderLive();
    }).catch(function (err) {
      if (err.message !== 'unauthenticated') { S.error = true; renderLive(); }
    });
  }
  setInterval(poll, POLL_MS);
  setInterval(function () {
    $$('[data-since]').forEach(function (el) { el.textContent = fmtDur(since(el.dataset.since)); });
  }, 1000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });
  $('#live').addEventListener('click', function () { S.paused = !S.paused; renderLive(); if (!S.paused) poll(); });

  // ---------- Views ----------
  var TITLES = { alarms: 'Alarms', events: 'Event log', integration: 'Integration' };
  function setView() {
    var v = location.hash.replace('#', '');
    S.view = TITLES[v] ? v : 'alarms';
    $$('#console > section').forEach(function (s) { s.hidden = s.dataset.view !== S.view; });
    $$('#nav a').forEach(function (a) { a.classList.toggle('active', a.dataset.view === S.view); });
    $('#view-title').textContent = TITLES[S.view];
    $('#search-wrap').style.visibility = S.view === 'integration' ? 'hidden' : '';
  }
  window.addEventListener('hashchange', setView);

  // ---------- Filters ----------
  $('#search').addEventListener('input', function () { S.q = this.value.trim().toLowerCase(); render(); });
  $('#scope').addEventListener('click', function (ev) {
    var b = ev.target.closest('button'); if (!b) return;
    S.scope = b.dataset.scope; renderAlarms();
  });
  $('#ev-state').addEventListener('click', function (ev) {
    var b = ev.target.closest('button'); if (!b) return;
    S.evState = b.dataset.state;
    $$('button', this).forEach(function (x) { x.classList.toggle('on', x === b); });
    renderEvents();
  });
  function toggleSev(sev) { S.sevs[sev] = !S.sevs[sev]; renderKpis(); renderAlarms(); }
  $('#sev-chips').addEventListener('click', function (ev) { var c = ev.target.closest('.chip'); if (c) toggleSev(c.dataset.sev); });
  $('#kpis').addEventListener('click', function (ev) { var k = ev.target.closest('button.kpi'); if (k) toggleSev(k.dataset.sev); });
  document.addEventListener('keydown', function (ev) {
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if (ev.key === '/' && !typing && S.view !== 'integration') { ev.preventDefault(); $('#search').focus(); }
    if (ev.key === 'Escape') { if (S.drawer) closeDrawer(); else if (document.activeElement === $('#search')) $('#search').blur(); }
    if (ev.key === 'Enter' && document.activeElement.matches && document.activeElement.matches('tr.row')) document.activeElement.click();
  });

  // ---------- Row actions ----------
  function setAck(alarmId, on) {
    return api('/api/alarms/' + encodeURIComponent(alarmId) + '/ack', { method: on ? 'POST' : 'DELETE' })
      .then(function () { toast(on ? 'Alarm acknowledged' : 'Acknowledgement removed'); return poll(true); })
      .catch(function () { toast('Could not update the alarm'); });
  }
  $('#console').addEventListener('click', function (ev) {
    if (ev.target.closest('#reset-filters')) { S.q = ''; $('#search').value = ''; S.sevs = {}; render(); return; }
    var ackBtn = ev.target.closest('[data-ack]');
    if (ackBtn) { ackBtn.disabled = true; setAck(ackBtn.dataset.ack, true); return; }
    var row = ev.target.closest('tr.row');
    if (row) openDrawer(row.dataset.alarm, row.dataset.event);
  });

  // ---------- Drawer ----------
  function openDrawer(alarmId, eventId) {
    S.drawer = { alarmId: alarmId, eventId: eventId || null, open: {} };
    if (eventId) S.drawer.open[eventId] = true;
    document.body.classList.add('drawer-open');
    $('#drawer').setAttribute('aria-hidden', 'false');
    $('#drawer').innerHTML = '<div class="drawer-body muted">Loading…</div>';
    $$('tr.row').forEach(function (r) { r.classList.toggle('selected', r.dataset.alarm === alarmId && (!eventId || r.dataset.event === eventId)); });
    refreshDrawer(true);
  }
  function closeDrawer() {
    S.drawer = null;
    document.body.classList.remove('drawer-open');
    $('#drawer').setAttribute('aria-hidden', 'true');
    $$('tr.row.selected').forEach(function (r) { r.classList.remove('selected'); });
  }
  function refreshDrawer(first) {
    var d = S.drawer;
    api('/api/alarms/' + encodeURIComponent(d.alarmId)).then(function (res) {
      if (S.drawer !== d) return;
      if (first && !d.eventId && res.events.length) d.open[res.events[res.events.length - 1].id] = true;
      renderDrawer(res.alarm, res.events);
    }).catch(function () {
      if (S.drawer === d && first) $('#drawer').innerHTML = '<div class="drawer-body"><div class="empty"><h3>Could not load this alarm</h3><button class="btn" data-close>Close</button></div></div>';
    });
  }
  function renderDrawer(a, events) {
    var d = S.drawer, body = $('.drawer-body', $('#drawer')), scroll = body ? body.scrollTop : 0;
    var dur = a.active
      ? (a.raised_at ? '<span data-since="' + esc(a.raised_at) + '">' + fmtDur(since(a.raised_at)) + '</span> and counting' : '—')
      : fmtDur(a.duration_s);
    var timeline = events.slice().reverse().map(function (e) {
      var cleared = String(e.state).toUpperCase() === 'CLEARED';
      var json = JSON.stringify(e.payload, null, 2);
      return '<li class="' + (cleared ? 'cleared' : '') + (e.id === d.eventId ? ' focus' : '') + '">' +
        '<details data-event="' + esc(e.id) + '"' + (d.open[e.id] ? ' open' : '') + '>' +
        '<summary><b>' + esc(e.state) + '</b><span class="when">' + esc(fmtTime(e.received_at)) + '</span><span class="tog"></span></summary>' +
        '<div class="code"><pre>' + highlight(json) + '</pre><button class="btn sm" data-copy="' + esc(json) + '">Copy</button></div>' +
        '</details></li>';
    }).join('');
    $('#drawer').className = 'drawer sev-' + (a.active ? a.severity : 'cleared');
    $('#drawer').innerHTML =
      '<div class="drawer-head"><div class="row1">' + sevBadge(a.severity, a.severity_label) +
        '<span class="state' + (a.active ? '' : ' cleared') + '">' + esc(a.state) + '</span><span class="spacer"></span>' +
        '<button class="btn ghost icon" data-close aria-label="Close"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>' +
        '<h2>' + esc(a.rule || 'Alarm') + '</h2>' + (a.description ? '<div class="desc">' + esc(a.description) + '</div>' : '') + '</div>' +
      '<div class="drawer-body">' +
        '<div class="drawer-actions">' +
          (a.ack ? '<button class="btn" data-unack>Remove acknowledgement</button>'
                 : a.active ? '<button class="btn primary" data-ackd>Acknowledge</button>' : '') +
          '<button class="btn" data-copy="' + esc(a.alarm_id) + '">Copy alarm ID</button>' +
          '<a class="btn" href="/alarm/' + encodeURIComponent(a.alarm_id) + '">Open full page</a></div>' +
        '<dl class="meta">' +
          '<div class="full"><dt>Alarm ID</dt><dd class="mono">' + esc(a.alarm_id) + '</dd></div>' +
          '<div><dt>Source</dt><dd>' + esc(a.source || '—') + '</dd></div>' +
          '<div><dt>Duration</dt><dd>' + dur + '</dd></div>' +
          '<div><dt>First seen</dt><dd>' + timeTag(a.first_seen) + '</dd></div>' +
          '<div><dt>Last update</dt><dd>' + timeTag(a.last_seen) + '</dd></div>' +
          '<div><dt>Events</dt><dd>' + a.event_count + ' (' + a.raise_count + ' raised)</dd></div>' +
          '<div><dt>Acknowledged</dt><dd>' + (a.ack ? esc(a.ack.by) + ' · ' + esc(fmtTime(a.ack.at)) : 'No') + '</dd></div>' +
        '</dl>' +
        '<div><div class="section-title">Timeline · newest first</div><ol class="timeline">' + timeline + '</ol></div>' +
      '</div>';
    $('.drawer-body', $('#drawer')).scrollTop = scroll;
  }
  $('#drawer').addEventListener('click', function (ev) {
    if (ev.target.closest('[data-close]')) return closeDrawer();
    var id = S.drawer && S.drawer.alarmId;
    if (ev.target.closest('[data-ackd]')) setAck(id, true).then(function () { if (S.drawer) refreshDrawer(); });
    if (ev.target.closest('[data-unack]')) setAck(id, false).then(function () { if (S.drawer) refreshDrawer(); });
  });
  $('#drawer').addEventListener('toggle', function (ev) {
    var det = ev.target;
    if (S.drawer && det.dataset && det.dataset.event) S.drawer.open[det.dataset.event] = det.open;
  }, true);
  $('#scrim').addEventListener('click', closeDrawer);

  // ---------- Integration: endpoints + test sender ----------
  var WEBHOOK = location.origin + '/webhook/alarms';
  $('#url-webhook').textContent = WEBHOOK;
  $('#url-health').textContent = WEBHOOK + '/health';
  $('#curl').textContent = 'curl -X POST ' + WEBHOOK + ' \\\n  -H "Content-Type: application/json" \\\n  -d \'{"alarmId": "demo-001", "state": "RAISED", "alarmSeverity": "Major", "rule": "Interface down", "source": "core-sw-01"}\'';

  function newId() { return 'alm-' + Math.random().toString(16).slice(2, 10); }
  $('#sim-id').value = newId();
  $('#sim-newid').addEventListener('click', function () { $('#sim-id').value = newId(); });

  function logLine(ok, text) {
    var log = $('#sim-log');
    if ($('.muted', log)) log.innerHTML = '';
    var t = new Date().toLocaleTimeString([], { hour12: false });
    log.insertAdjacentHTML('afterbegin', '<div><span class="muted">' + t + '</span> <span class="' + (ok ? 'ok' : 'err') + '">' + esc(text) + '</span></div>');
  }
  function send(payload) {
    return fetch('/webhook/alarms', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      .then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); })
      .then(function (res) {
        logLine(res.status === 200, res.status + ' ' + payload.state + ' ' + payload.alarmId + ' → ' + JSON.stringify(res.body));
      })
      .catch(function (err) { logLine(false, 'Request failed: ' + err.message); });
  }
  function buildPayload(id, state, sev, rule, source) {
    var now = new Date().toISOString();
    var p = { alarmId: id, state: state, alarmSeverity: sev, rule: rule, source: source };
    p[state === 'CLEARED' ? 'clearedTime' : 'raisedTime'] = now;
    return p;
  }
  $('#sim').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var state = (ev.submitter && ev.submitter.dataset.state) || 'RAISED';
    send(buildPayload($('#sim-id').value.trim(), state, $('#sim-sev').value, $('#sim-rule').value.trim(), $('#sim-source').value.trim()))
      .then(function () { poll(true); });
  });

  var SAMPLE_RULES = [
    ['Interface down', 'Critical'], ['BGP peer lost', 'Critical'], ['High CPU utilisation', 'Major'], ['Packet loss above 5%', 'Major'],
    ['Memory above 90%', 'Major'], ['Disk space low', 'Minor'], ['Fan speed degraded', 'Minor'], ['Config drift detected', 'Warning'],
    ['Latency above threshold', 'Warning'], ['Power supply redundancy lost', 'Critical'], ['Optical RX power low', 'Minor']
  ];
  var SAMPLE_SOURCES = ['core-sw-01', 'core-sw-02', 'edge-rtr-ams-1', 'edge-rtr-lon-3', 'fw-dmz-02', 'olt-east-14', 'agg-sw-07', 'dc1-leaf-22'];
  function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }
  $('#sim-burst').addEventListener('click', function () {
    var btn = this, jobs = [];
    btn.disabled = true;
    for (var i = 0; i < 6; i++) { var r = pick(SAMPLE_RULES); jobs.push(buildPayload(newId(), 'RAISED', r[1], r[0], pick(SAMPLE_SOURCES))); }
    var active = S.data ? S.data.alarms.filter(function (a) { return a.active; }) : [];
    active.sort(function () { return Math.random() - 0.5; }).slice(0, Math.min(2, active.length)).forEach(function (a) {
      jobs.push(buildPayload(a.alarm_id, 'CLEARED', a.severity_label || 'Minor', a.rule || 'Alarm', a.source || ''));
    });
    jobs.reduce(function (p, job) { return p.then(function () { return send(job); }); }, Promise.resolve())
      .then(function () { btn.disabled = false; toast(jobs.length + ' test events sent'); poll(true); });
  });

  // ---------- Go ----------
  setView();
  renderLive();
  poll(true);
})();
