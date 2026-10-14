// Điểm vào của giao diện: điều hướng theo URL hash, tải dự án, polling tiến độ,
// thanh 4 bước và panel tác vụ. Nội dung từng bước nằm trong step1..4.js.
//
// Mỗi "view" (trang dự án / từng bước) có cùng giao diện:
//   mount(root)       vẽ lần đầu
//   update(project)   cập nhật khi có dữ liệu mới (polling) — KHÔNG xoá chữ người dùng đang gõ
//   isDirty()         còn thay đổi chưa lưu không (để cảnh báo khi rời trang)
//   focusScene(id)    cuộn tới một cảnh (tuỳ chọn)
//   flush()           lưu ngay các ô đang chờ tự lưu (tuỳ chọn)

import { api } from './api.js';
import { h, toast, isActive, focusScene, guarded } from './ui.js';
import { ProjectsPage } from './projects.js';
import { Step1 } from './step1.js';
import { Step2 } from './step2.js';
import { Step3 } from './step3.js';
import { Step4 } from './step4.js';

const STEPS = [
  { n: 1, title: 'Kịch bản', View: Step1 },
  { n: 2, title: 'Storyboard', View: Step2 },
  { n: 3, title: 'Giọng đọc', View: Step3 },
  { n: 4, title: 'Xuất video', View: Step4 },
];

const app = {
  settings: null,
  voices: {},               // giọng theo ngôn ngữ (tải khi cần, giữ lại dùng tiếp)
  langByProject: {},        // ngôn ngữ đang xem ở bước 3/4 của từng dự án
  route: null,
  project: null,
  view: null,
  stepbar: null,
  pollTimer: null,
  loadedAt: Date.now(),     // chỉ báo toast cho job kết thúc SAU khi mở trang
  seenJobs: new Set(),
  cursor: {},               // vị trí "cảnh thiếu kế tiếp" khi bấm x/N nhiều lần
  pendingFocus: null,       // cảnh cần cuộn tới sau khi chuyển bước
};

const root = document.getElementById('app');
const jobPanel = document.getElementById('job-panel');
const topbarProject = document.getElementById('topbar-project');

// ── Điều hướng ──────────────────────────────────────────────────────────────
function parseRoute() {
  const m = location.hash.match(/^#\/p\/([^/]+)(?:\/(\d))?/);
  if (m) return { page: 'project', id: decodeURIComponent(m[1]), step: Math.min(4, Math.max(1, Number(m[2] || 1))) };
  return { page: 'list' };
}

export function navigate(hash) {
  if (location.hash === hash) onRoute();
  else location.hash = hash;
}

/** Nhảy tới một cảnh ở một bước (dùng cho x/N, danh sách cảnh thiếu ở bước 4…).
 *  lang: chuyển luôn sang tab ngôn ngữ đó (bước 3). */
function goToScene(step, sceneId, lang = null) {
  if (lang) setLang(lang);
  app.pendingFocus = sceneId;
  const hash = `#/p/${encodeURIComponent(app.project.id)}/${step}`;
  if (location.hash === hash) {
    focusScene(root, sceneId);
    app.pendingFocus = null;
  } else {
    location.hash = hash;
  }
}

let currentHash = location.hash;
let revertingHash = false;
window.addEventListener('hashchange', () => {
  if (revertingHash) { revertingHash = false; return; }
  // hashchange không huỷ được → nếu người dùng không muốn rời đi thì đặt lại hash cũ
  if (app.view?.isDirty?.() && !confirm('Có thay đổi chưa lưu. Rời đi và bỏ các thay đổi đó?')) {
    revertingHash = true;
    location.hash = currentHash;
    return;
  }
  app.view?.flush?.();
  currentHash = location.hash;
  onRoute();
});

window.addEventListener('beforeunload', e => {
  if (app.view?.isDirty?.()) { e.preventDefault(); e.returnValue = ''; }
});

async function onRoute() {
  clearTimeout(app.pollTimer);
  const route = parseRoute();
  const prev = app.route;
  app.route = route;
  app.view = null;

  if (route.page === 'list') {
    app.project = null;
    topbarProject.textContent = '';
    jobPanel.hidden = true;
    const page = new ProjectsPage(ctx);
    app.view = page;
    page.mount(root);
    return;
  }

  // Đổi dự án → tải mới; cùng dự án chỉ đổi bước → dùng dữ liệu đang có, rồi làm mới nền
  if (!app.project || prev?.page !== 'project' || prev.id !== route.id) {
    root.replaceChildren(h('p', { class: 'muted center' }, 'Đang tải dự án…'));
    try {
      app.project = await api.getProject(route.id);
    } catch (e) {
      root.replaceChildren(h('div', { class: 'card center' },
        h('p', {}, e.status === 404 ? 'Không tìm thấy dự án.' : e.message),
        h('a', { href: '#/', class: 'btn' }, '← Về danh sách dự án')));
      return;
    }
    if (app.route !== route) return;  // người dùng đã chuyển trang trong lúc chờ
    for (const j of app.project.jobs) app.seenJobs.add(j.id + j.status);
  }
  renderProjectPage(route.step);
  refresh();
}

// ── Trang dự án ─────────────────────────────────────────────────────────────
function renderProjectPage(step) {
  const p = app.project;
  topbarProject.textContent = p.name;
  document.title = `${p.name} — Video Studio Local`;

  app.stepbar = h('nav', { class: 'stepbar' });
  app.shortBanner = null;
  const content = h('section', { class: 'step-content' });
  root.replaceChildren(app.stepbar, ...(p.kind === 'short' ? [createShortBanner()] : []), content);

  const { View } = STEPS[step - 1];
  app.view = new View(ctx);
  app.view.mount(content);
  app.view.update(p);
  renderStepbar();
  renderShortBanner();
  renderJobPanel();

  if (app.pendingFocus) {
    const id = app.pendingFocus;
    app.pendingFocus = null;
    // Đợi trình duyệt vẽ xong rồi mới cuộn
    requestAnimationFrame(() => (app.view.focusScene?.(id) ?? focusScene(content, id)));
  }
}

function stepStatus(n) {
  const p = app.project;
  const s = p.summary;
  const jobs = p.jobs.filter(isActive);
  const busy = kinds => jobs.some(j => kinds.includes(j.kind));
  const targets = p.languages.filter(l => l !== p.source_lang);
  const clsOf = (ready, total, running) =>
    running ? 'running' : total && ready === total ? 'ready' : ready ? 'stale' : 'missing';
  switch (n) {
    case 1: {
      if (busy(['split'])) return { text: 'Đang chia cảnh…', cls: 'running' };
      if (app.route.step === 1 && app.view?.isDirty?.()) return { text: 'Chưa lưu', cls: 'stale' };
      if (!p.scenes.length) return { text: 'Chưa chia cảnh', cls: 'missing' };
      if (!targets.length) return { text: `Đã lưu · ${p.scenes.length} cảnh`, cls: 'ready' };
      // Có ngôn ngữ dịch: hiện thêm tiến độ bản dịch, bấm vào để tới cảnh chưa dịch
      const missing = targets.flatMap(l => s.langs[l].text.not_ready_ids.map(id => ({ id, lang: l })));
      const ready = targets.reduce((a, l) => a + s.langs[l].text.ready, 0);
      const total = targets.reduce((a, l) => a + s.langs[l].text.total, 0);
      return { text: `${p.scenes.length} cảnh · dịch ${ready}/${total}`,
               cls: clsOf(ready, total, busy(['translate', 'translate_one'])), missing };
    }
    case 2: {
      const c = s.images;
      return { text: `${c.ready}/${c.total} ảnh`, cls: clsOf(c.ready, c.total, busy(['images', 'image'])),
               missing: c.not_ready_ids.map(id => ({ id })) };
    }
    case 3: {
      // Mỗi ngôn ngữ một con số: "vi 8/8 · en 5/8"
      const parts = p.languages.map(l => ({ l, ...s.langs[l].audio }));
      const ready = parts.reduce((a, x) => a + x.ready, 0), total = parts.reduce((a, x) => a + x.total, 0);
      const text = parts.length === 1 ? `${ready}/${total} audio` : parts.map(x => `${x.l} ${x.ready}/${x.total}`).join(' · ');
      return { text, cls: clsOf(ready, total, busy(['audio', 'audio_one'])),
               missing: parts.flatMap(x => x.not_ready_ids.map(id => ({ id, lang: x.l }))) };
    }
    case 4: {
      const job = jobs.find(j => j.kind === 'export');
      if (job) return { text: `Đang xuất ${job.total ? Math.round(100 * job.done / job.total) : 0}%`, cls: 'running' };
      const st = p.languages.map(l => p.finals[l]?.status || 'missing');
      if (st.every(x => x === 'ready')) return { text: 'Đã xuất', cls: 'ready' };
      if (st.some(x => x !== 'missing')) {
        const label = { ready: '✓', stale: 'cũ', missing: '—' };
        return { text: p.languages.map((l, i) => `${l} ${label[st[i]]}`).join(' · '), cls: 'stale' };
      }
      const anyProblem = p.languages.some(l => p.export_problems[l].length);
      return { text: anyProblem ? 'Chưa đủ cảnh' : 'Sẵn sàng xuất', cls: 'missing' };
    }
  }
}

// Thanh bước được TẠO MỘT LẦN rồi chỉ cập nhật chữ/class. Không được thay phần tử mỗi lần polling:
// nếu phần tử bị thay giữa lúc nhấn và nhả chuột, trình duyệt không phát sự kiện click → bấm "không ăn".
function renderStepbar() {
  if (!app.stepbar || !app.project) return;
  if (!app.stepbar.children.length) {
    const pid = encodeURIComponent(app.project.id);
    app.stepbar.append(...STEPS.map(({ n, title }) => {
      const status = h('span', { class: 'step-status badge' });
      // Bấm vào "x/N" → nhảy tới cảnh thiếu/lỗi kế tiếp (vòng lại từ đầu)
      status.addEventListener('click', e => {
        const items = status.missing || [];  // [{id, lang?}]
        if (!items.length) return;         // không có cảnh thiếu → để link bước hoạt động bình thường
        e.preventDefault();
        e.stopPropagation();
        app.cursor[n] = ((app.cursor[n] ?? -1) + 1) % items.length;
        const { id, lang } = items[app.cursor[n]];
        goToScene(n, id, lang);
      });
      return h('a', { href: `#/p/${pid}/${n}`, class: 'step', dataset: { step: n } },
        h('span', { class: 'step-num' }, n),
        h('span', { class: 'step-title' }, title),
        status);
    }));
  }
  for (const link of app.stepbar.children) {
    const n = Number(link.dataset.step);
    const st = stepStatus(n);
    const status = link.querySelector('.step-status');
    link.classList.toggle('active', app.route.step === n);
    status.className = `step-status badge badge-${st.cls}${st.missing?.length ? ' clickable' : ''}`;
    status.textContent = st.text;
    status.title = st.missing?.length ? 'Bấm để tới cảnh còn thiếu/lỗi' : '';
    status.missing = st.missing;
  }
}

// ── Short: băng thông tin dự án gốc ─────────────────────────────────────────
const CHANGE_LABELS = { image: 'ảnh', removed: 'đã bị xoá ở dự án gốc' };

function createShortBanner() {
  const b = app.shortBanner = {
    link: h('a', {}),
    changes: h('span', { class: 'small' }),
    syncBtn: h('button', { class: 'btn btn-small' }, '↻ Cập nhật từ dự án gốc'),
  };
  b.syncBtn.addEventListener('click', () => {
    if (!confirm('Chép lại từ dự án gốc các cảnh đã đổi (ảnh, lời đọc, giọng, clip)?\n'
      +'Chỉnh sửa trong Short ở những cảnh đó sẽ bị thay.')) return;
    guarded(b.syncBtn, async () => {
      const r = await api.syncParent(app.project.id);
      setProject(r);
      toast(r.synced ? `Đã cập nhật ${r.synced} cảnh từ dự án gốc` : 'Không có gì để cập nhật', 'success');
    });
  });
  b.el = h('div', { class: 'short-banner' }, h('span', {}, '✂ Short của '), b.link, b.changes,
    h('span', { class: 'grow' }), b.syncBtn);
  return b.el;
}

function renderShortBanner() {
  const b = app.shortBanner;
  const info = app.project?.parent_info;
  if (!b || !info) return;
  b.link.textContent = `«${info.name}»`;
  if (info.exists) b.link.href = `#/p/${encodeURIComponent(info.id)}/4`;
  else b.link.removeAttribute('href');
  const changes = info.changes || [];
  const pos = id => app.project.scenes.findIndex(s => s.id === id) + 1;
  const syncable = changes.filter(c => !c.what.includes('removed'));
  b.changes.textContent = !info.exists ? ' — dự án gốc đã bị xoá'
    : changes.length ? ` — dự án gốc đã đổi: ${changes.map(c => `cảnh ${pos(c.id)} (${
      c.what.map(w => CHANGE_LABELS[w] || `lời/giọng ${w}`).join(', ')})`).join('; ')}`
    : ' — đang khớp với dự án gốc';
  b.changes.className = `small ${changes.length || !info.exists ? 'warn-text' : 'muted'}`;
  b.syncBtn.hidden = !syncable.length;
  b.syncBtn.disabled = app.project.jobs.some(isActive);
}

function renderJobPanel() {
  const jobs = app.project?.jobs.filter(isActive) || [];
  jobPanel.hidden = jobs.length === 0;
  // Mỗi job một khối, giữ nguyên qua các lần polling (theo id) → nút "Huỷ" không bị thay khi đang bấm
  app.jobEls ??= new Map();
  for (const [id, el] of app.jobEls) {
    if (!jobs.some(j => j.id === id)) { el.root.remove(); app.jobEls.delete(id); }
  }
  if (!jobPanel.querySelector('.job-panel-title')) jobPanel.replaceChildren(h('div', { class: 'job-panel-title' }));
  jobPanel.querySelector('.job-panel-title').textContent = `Tác vụ (${jobs.length})`;
  for (const j of jobs) {
    let el = app.jobEls.get(j.id);
    if (!el) {
      el = { label: h('strong', {}), msg: h('div', { class: 'job-msg muted' }),
             bar: h('div', { class: 'progress-bar' }), progress: h('div', { class: 'progress' }) };
      el.cancel = h('button', {
        class: 'btn btn-small btn-ghost',
        onclick: async () => {
          el.cancel.disabled = true;
          try { await api.cancelJob(j.id); refresh(); } catch (e) { toast(e.message, 'error'); }
        },
      });
      el.progress.append(el.bar);
      el.root = h('div', { class: 'job' }, h('div', { class: 'job-head' }, el.label, el.cancel), el.msg, el.progress);
      jobPanel.append(el.root);
      app.jobEls.set(j.id, el);
    }
    const pct = j.total ? Math.round(100 * j.done / j.total) : null;
    el.label.textContent = j.label;
    el.msg.textContent = j.status === 'queued' ? 'Đang chờ trong hàng đợi…' : j.message;
    el.cancel.disabled = j.cancel_requested;
    el.cancel.textContent = j.cancel_requested ? 'Đang huỷ…' : 'Huỷ';
    el.progress.classList.toggle('indeterminate', pct == null && j.status === 'running');
    el.bar.style.width = `${pct ?? 0}%`;
  }
}

// Báo toast khi job kết thúc (chỉ job kết thúc sau khi mở trang)
function notifyFinishedJobs() {
  for (const j of app.project.jobs) {
    if (isActive(j) || app.seenJobs.has(j.id + j.status)) continue;
    app.seenJobs.add(j.id + j.status);
    if (j.finished_at * 1000 < app.loadedAt) continue;
    if (j.status === 'done') toast(`${j.label}: xong. ${j.message || ''}`, 'success');
    else if (j.status === 'error') toast(`${j.label} lỗi: ${j.error}`, 'error');
    else if (j.status === 'cancelled') toast(`${j.label}: đã huỷ`);
  }
}

// ── Làm mới dữ liệu + polling ───────────────────────────────────────────────
let refreshing = null;
let refreshAgain = false;

/** Tải lại dự án và cập nhật mọi phần. Gọi nhiều lần liền nhau → gộp lại. */
async function refresh() {
  if (refreshing) { refreshAgain = true; return refreshing; }
  refreshing = (async () => {
    do {
      refreshAgain = false;
      const route = app.route;
      if (route?.page !== 'project') return;
      try {
        const p = await api.getProject(route.id);
        if (app.route !== route) return;
        setProject(p);
      } catch (e) {
        if (e.status === 0) toast(e.message, 'error', 3000);
      }
    } while (refreshAgain);
  })();
  try { await refreshing; } finally { refreshing = null; schedulePoll(); }
}

/** Dùng khi API trả về sẵn dự án mới (vd. sau khi lưu) — khỏi gọi lại GET. */
function setProject(p) {
  app.project = p;
  topbarProject.textContent = p.name;
  notifyFinishedJobs();
  app.view?.update(p);
  renderStepbar();
  renderShortBanner();
  renderJobPanel();
}

function schedulePoll() {
  clearTimeout(app.pollTimer);
  if (app.route?.page !== 'project' || !app.project) return;
  // Có job đang chạy → hỏi mỗi giây; rảnh → 15 giây (vẫn thấy thay đổi từ dòng lệnh)
  const active = app.project.jobs.some(isActive);
  app.pollTimer = setTimeout(refresh, active ? 1000 : 15000);
}

function setLang(lang) {
  if (app.project) app.langByProject[app.project.id] = lang;
}

// Đối tượng truyền cho các view
const ctx = {
  api,
  get settings() { return app.settings; },
  /** Danh sách giọng của một ngôn ngữ (tải một lần rồi giữ lại). */
  async voices(lang) {
    app.voices[lang] ??= await api.voices(lang);
    return app.voices[lang];
  },
  /** Ngôn ngữ đang xem ở bước 3/4 (nhớ theo dự án; mặc định ngôn ngữ gốc). */
  get lang() {
    const p = app.project;
    const l = p && app.langByProject[p.id];
    return p && p.languages.includes(l) ? l : p?.source_lang;
  },
  setLang,
  get project() { return app.project; },
  refresh,
  setProject,
  goToScene,
  navigate,
  renderStepbar,
};

// ── Khởi động ───────────────────────────────────────────────────────────────
(async function init() {
  try {
    app.settings = await api.settings();
  } catch (e) {
    root.replaceChildren(h('div', { class: 'card center' }, h('p', {}, e.message)));
    return;
  }
  onRoute();
})();
