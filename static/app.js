'use strict';

/* ------------------------------------------------------------- helpers */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  tab: 'input',
  today: new Date().toISOString().slice(0, 10),
  ovDate: new Date().toISOString().slice(0, 10),
  brDate: new Date().toISOString().slice(0, 10),
  reportMonth: new Date().toISOString().slice(0, 7),
  categories: [],
  profiles: [],
  budgets: [],
  history: [],
  histIndex: -1,
};

const fmtMoney = (n) => {
  const v = Number(n || 0);
  return '¥' + v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const WEEK_CN = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

function fmtDateCN(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(y, m - 1, d);
  return `${m} 月 ${d} 日 ${WEEK_CN[dt.getDay()]}`;
}

function shiftDate(iso, days) {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(y, m - 1, d + days);
  const p = (n) => String(n).padStart(2, '0');
  return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())}`;
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

let toastTimer = null;
function toast(msg, kind = '') {
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'toast show ' + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast ' + kind; }, 2400);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  let data = null;
  try { data = await res.json(); } catch (e) { /* empty body */ }
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data;
}

/* ----------------------------------------------------------------- tabs */
function setTab(name) {
  state.tab = name;
  $$('.tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-' + name));
  if (name === 'input') $('#cmdInput').focus();
  if (name === 'overview') { loadDay('ov'); loadReport(); }
  if (name === 'browse') loadDay('br');
}

$('#tabs').addEventListener('click', (e) => {
  const btn = e.target.closest('.tab');
  if (btn) setTab(btn.dataset.tab);
});

/* ------------------------------------------------------------ bootstrap */
async function bootstrap() {
  const data = await api('/api/bootstrap');
  state.categories = data.categories;
  state.profiles = data.profiles;
  state.budgets = data.budgets;
  state.today = data.today;
  renderQuickProfiles();
  renderProfiles();
  updateTodayLabel();
  loadDay('ov');
  loadReport();
}

function updateTodayLabel() {
  $('#todayLabel').textContent = state.today;
}

/* --------------------------------------------------------------- input */
const input = $('#cmdInput');
const feedback = $('#feedback');

function setFeedback(text, kind = '') {
  feedback.textContent = text;
  feedback.className = 'feedback ' + kind;
}

input.addEventListener('keydown', async (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    await runCommand(text);
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    if (!state.history.length) return;
    state.histIndex = Math.min(state.histIndex + 1, state.history.length - 1);
    input.value = state.history[state.history.length - 1 - state.histIndex];
  } else if (e.key === 'ArrowDown') {
    e.preventDefault();
    if (state.histIndex <= 0) { state.histIndex = -1; input.value = ''; return; }
    state.histIndex -= 1;
    input.value = state.history[state.history.length - 1 - state.histIndex];
  } else if (e.key === 'Escape') {
    input.value = '';
    setFeedback('');
  }
});

async function runCommand(text) {
  state.history.push(text);
  state.histIndex = -1;
  try {
    const res = await api('/api/command', {
      method: 'POST',
      body: JSON.stringify({ text, today: state.today }),
    });

    if (res.kind === 'help') {
      setFeedback(res.text, '');
      input.value = '';
      return;
    }

    input.value = '';
    if (res.kind === 'record') {
      const r = res.record;
      setFeedback(`✓ 记下 ${fmtMoney(r.amount)}  ${r.note || ''}` +
        (r.category_name ? `  #${r.category_name}` : '') +
        (r.tags.length ? '  ' + r.tags.map((t) => '@' + t).join(' ') : ''), 'ok');
      refreshAll();
    } else if (res.kind === 'budget') {
      setFeedback(`✓ 预算「${res.budget.title}」${fmtMoney(res.budget.amount)} · ${res.rule_text}`, 'ok');
      refreshAll();
    } else if (res.kind === 'profile') {
      setFeedback(`✓ 模板「${res.profile.name}」已创建`, 'ok');
      refreshAll();
    } else if (res.kind === 'tag') {
      setFeedback(`✓ 记录 ${res.record.id} 标签：${res.record.tags.join(', ') || '（无）'}`, 'ok');
      refreshAll();
    }
  } catch (err) {
    setFeedback('✗ ' + err.message, 'err');
    toast(err.message, 'err');
  }
}

function renderQuickProfiles() {
  const box = $('#quickProfiles');
  box.innerHTML = state.profiles.map((p) => {
    const bits = [];
    if (p.default_amount) bits.push(p.default_amount);
    if (p.category_name) bits.push('#' + p.category_name);
    return `<button class="chip" data-profile="${p.id}">${esc(p.name)}${bits.length ? ' · ' + esc(bits.join(' ')) : ''}</button>`;
  }).join('');
}

$('#quickProfiles').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-profile]');
  if (!btn) return;
  const p = state.profiles.find((x) => String(x.id) === btn.dataset.profile);
  if (!p) return;
  try {
    await api('/api/records', {
      method: 'POST',
      body: JSON.stringify({
        date: state.today,
        amount: p.default_amount || 0.01,
        note: p.name,
        category: p.category_name || null,
        tags: p.default_tags || [],
        time: p.default_time || null,
      }),
    });
    setFeedback(`✓ 快速记下「${p.name}」${fmtMoney(p.default_amount || 0.01)}`, 'ok');
    refreshAll();
  } catch (err) {
    toast(err.message, 'err');
  }
});

/* ------------------------------------------------------------ overview */
async function loadDay(which) {
  const date = which === 'ov' ? state.ovDate : state.brDate;
  let data;
  try {
    data = await api('/api/day?date=' + encodeURIComponent(date));
  } catch (err) {
    toast(err.message, 'err');
    return;
  }
  if (which === 'ov') renderOverview(data);
  else renderBrowse(data);
}

function renderOverview(data) {
  $('#ovDate').textContent = fmtDateCN(data.date);
  $('#ovTotal').textContent = fmtMoney(data.total);
  $('#ovPlanned').textContent = fmtMoney(data.planned_total);
  $('#ovCount').textContent = String(data.records.length);

  // category bars
  const byCat = {};
  for (const r of data.records) {
    const key = r.category_name || '未分类';
    byCat[key] = byCat[key] || { total: 0, color: r.category_color || '#8b8b8b' };
    byCat[key].total += r.amount;
  }
  const entries = Object.entries(byCat).sort((a, b) => b[1].total - a[1].total);
  const max = entries.length ? entries[0][1].total : 0;
  $('#ovBars').innerHTML = entries.length
    ? entries.map(([name, v]) => `
        <div class="bar-row">
          <span class="bar-name">${esc(name)}</span>
          <span class="bar-track"><span class="bar-fill" style="width:${max ? (v.total / max * 100).toFixed(1) : 0}%;background:${v.color}"></span></span>
          <span class="bar-val">${fmtMoney(v.total)}</span>
        </div>`).join('')
    : '<div class="empty">这一天没有支出记录</div>';

  // planned
  $('#ovPlannedList').innerHTML = data.planned.length
    ? data.planned.map((p) => `
        <li>
          <span class="pill cat" style="background:${p.category_color || '#8b8b8b'}">${esc(p.category_name || '通用')}</span>
          <span>${esc(p.title)}</span>
          <span style="color:var(--text-dim);font-size:12px">${esc(p.rule_text)}</span>
          <span style="margin-left:auto;font-family:var(--mono)">${fmtMoney(p.amount)}</span>
          <span class="pill">${p.done ? '已记' : '未记'}</span>
        </li>`).join('')
    : '<li class="empty">今天没有预算计划</li>';
}

/* -------------------------------------------------------------- report */
async function loadReport() {
  let data;
  try {
    data = await api('/api/report?month=' + encodeURIComponent(state.reportMonth));
  } catch (err) {
    toast(err.message, 'err');
    return;
  }
  $('#reportMonth').value = data.month;

  const diff = data.actual_total - data.planned_total;
  $('#reportSummary').innerHTML = `
    <div class="rep-item"><div class="rep-label">总支出</div><div class="rep-val">${fmtMoney(data.spent_total)}</div></div>
    <div class="rep-item"><div class="rep-label">预算计划</div><div class="rep-val">${fmtMoney(data.planned_total)}</div></div>
    <div class="rep-item"><div class="rep-label">预算内实际</div><div class="rep-val">${fmtMoney(data.actual_total)}</div></div>
    <div class="rep-item"><div class="rep-label">差额</div><div class="rep-val ${diff > 0 ? 'over' : diff < 0 ? 'under' : ''}">${diff > 0 ? '+' : ''}${fmtMoney(diff)}</div></div>
    <div class="rep-item"><div class="rep-label">笔数</div><div class="rep-val">${data.record_count}</div></div>`;

  $('#reportBudgets').innerHTML = data.budgets.length
    ? data.budgets.map((b) => {
        const ratio = b.planned ? Math.min(b.actual / b.planned * 100, 100) : 0;
        const color = b.actual > b.planned ? 'var(--bad)' : 'var(--ok)';
        return `
          <div class="rep-budget">
            <div class="rep-budget-top">
              <span class="rep-budget-title">${esc(b.title)}</span>
              <span class="rep-budget-rule">${esc(b.rule_text)} × ${b.count}</span>
              <span class="rep-budget-nums" style="color:${b.actual > b.planned ? 'var(--bad)' : 'var(--text-dim)'}">
                ${fmtMoney(b.actual)} / ${fmtMoney(b.planned)}
              </span>
            </div>
            <div class="rep-budget-bar"><div style="width:${ratio}%;background:${color}"></div></div>
          </div>`;
      }).join('')
    : '<div class="empty">这个月还没有预算</div>';

  $('#reportCats').innerHTML = data.by_category.map((c) =>
    `<span class="pill cat" style="background:${c.color}">${esc(c.name)} ${fmtMoney(c.total)} · ${c.count}笔</span>`
  ).join('');
}

/* --------------------------------------------------------------- browse */
function renderBrowse(data) {
  $('#brDate').textContent = fmtDateCN(data.date);
  $('#brCount').textContent = String(data.records.length);

  $('#brRecords').innerHTML = data.records.length
    ? data.records.map((r) => {
        const tags = (r.tags || []).map((t) =>
          `<button class="tag-pill" data-rec="${r.id}" data-untag="${esc(t)}">${esc(t)} ×</button>`).join('');
        return `
          <li data-rec="${r.id}">
            <div class="rec-main">
              <span class="rec-amount">${fmtMoney(r.amount)}</span>
              <span class="rec-note">${esc(r.note || '—')}</span>
              ${r.time ? `<span class="rec-time">${esc(r.time)}</span>` : ''}
              <button class="rec-del" data-del="${r.id}" title="删除">×</button>
            </div>
            <div class="rec-meta">
              ${r.category_name ? `<span class="pill cat" style="background:${r.category_color}">${esc(r.category_name)}</span>` : '<span class="pill">未分类</span>'}
              ${tags}
              <button class="tag-add" data-addtag="${r.id}">+ 标签</button>
            </div>
          </li>`;
      }).join('')
    : '<li class="empty">这一天还没有记录</li>';
}

$('#brRecords').addEventListener('click', async (e) => {
  const untag = e.target.closest('[data-untag]');
  if (untag) {
    const id = untag.dataset.rec;
    const tag = untag.dataset.untag;
    const rec = await findRecord(id);
    if (!rec) return;
    try {
      await api('/api/records/' + id, {
        method: 'PATCH',
        body: JSON.stringify({ tags: rec.tags.filter((t) => t !== tag) }),
      });
      loadDay('br');
    } catch (err) { toast(err.message, 'err'); }
    return;
  }

  const addBtn = e.target.closest('[data-addtag]');
  if (addBtn) {
    const id = addBtn.dataset.addtag;
    const tag = prompt('添加标签：');
    if (!tag || !tag.trim()) return;
    const rec = await findRecord(id);
    if (!rec) return;
    const tags = Array.from(new Set([...(rec.tags || []), tag.trim()]));
    try {
      await api('/api/records/' + id, { method: 'PATCH', body: JSON.stringify({ tags }) });
      loadDay('br');
    } catch (err) { toast(err.message, 'err'); }
    return;
  }

  const del = e.target.closest('[data-del]');
  if (del) {
    if (!confirm('删除这条记录？')) return;
    try {
      await api('/api/records/' + del.dataset.del, { method: 'DELETE' });
      toast('已删除', 'ok');
      refreshAll();
    } catch (err) { toast(err.message, 'err'); }
  }
});

async function findRecord(id) {
  const data = await api('/api/records?date=' + encodeURIComponent(state.brDate));
  return data.records.find((r) => String(r.id) === String(id));
}

/* -------------------------------------------------------------- profiles */
function renderProfiles() {
  $('#brProfiles').innerHTML = state.profiles.length
    ? state.profiles.map((p) => `
        <li>
          <span class="pill cat" style="background:${p.category_color || '#8b8b8b'}">${esc(p.category_name || '通用')}</span>
          <span>${esc(p.name)}</span>
          ${p.default_amount ? `<span style="margin-left:auto;font-family:var(--mono)">${fmtMoney(p.default_amount)}</span>` : ''}
          <button class="rec-del" style="opacity:1" data-delprofile="${p.id}">×</button>
        </li>`).join('')
    : '<li class="empty">还没有模板</li>';
}

$('#brProfiles').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-delprofile]');
  if (!btn) return;
  if (!confirm('删除这个模板？')) return;
  try {
    await api('/api/profiles/' + btn.dataset.delprofile, { method: 'DELETE' });
    toast('已删除', 'ok');
    refreshAll();
  } catch (err) { toast(err.message, 'err'); }
});

$('#profileForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = e.target;
  const tags = f.default_tags.value.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean);
  try {
    await api('/api/profiles', {
      method: 'POST',
      body: JSON.stringify({
        name: f.name.value.trim(),
        default_amount: f.default_amount.value ? Number(f.default_amount.value) : null,
        default_time: f.default_time.value || null,
        category: f.category.value.trim() || null,
        default_tags: tags,
      }),
    });
    f.reset();
    toast('模板已创建', 'ok');
    refreshAll();
  } catch (err) { toast(err.message, 'err'); }
});

/* --------------------------------------------------------------- wiring */
$('#ovPrev').addEventListener('click', () => { state.ovDate = shiftDate(state.ovDate, -1); loadDay('ov'); });
$('#ovNext').addEventListener('click', () => { state.ovDate = shiftDate(state.ovDate, 1); loadDay('ov'); });
$('#ovToday').addEventListener('click', () => { state.ovDate = state.today; loadDay('ov'); });
$('#brPrev').addEventListener('click', () => { state.brDate = shiftDate(state.brDate, -1); loadDay('br'); });
$('#brNext').addEventListener('click', () => { state.brDate = shiftDate(state.brDate, 1); loadDay('br'); });
$('#brToday').addEventListener('click', () => { state.brDate = state.today; loadDay('br'); });
$('#reportMonth').addEventListener('change', (e) => {
  state.reportMonth = e.target.value;
  loadReport();
});

async function refreshAll() {
  const data = await api('/api/bootstrap');
  state.categories = data.categories;
  state.profiles = data.profiles;
  state.budgets = data.budgets;
  renderQuickProfiles();
  renderProfiles();
  loadDay('ov');
  loadDay('br');
  loadReport();
}

document.addEventListener('keydown', (e) => {
  if (e.key === '/' && document.activeElement !== input) {
    e.preventDefault();
    setTab('input');
  }
});

bootstrap().then(() => setTab('input')).catch((err) => toast(err.message, 'err'));
