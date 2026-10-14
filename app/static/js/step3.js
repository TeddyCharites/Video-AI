// Bước 3 — Giọng đọc, theo TỪNG ngôn ngữ (tab): chọn giọng; mỗi cảnh có lời đọc (tự lưu),
// trình phát, thời lượng, Tạo lại. Ngôn ngữ dịch: lời đọc chính là bản dịch (sửa ở đây = sửa bản dịch).

import { h, badge, fmtSec, guarded, sceneJobStatus, activeJob, autosave, focusScene, langName } from './ui.js';

export class Step3 {
  constructor(ctx) {
    this.ctx = ctx;
    this.rows = new Map();
    this.layoutSig = null;
    this.voicesLang = null;
  }

  isDirty() { return false; }

  flush() {
    for (const r of this.rows.values()) r.saver.flush();
  }

  focusScene(id) { return focusScene(this.root, id); }

  get lang() { return this.ctx.lang; }

  mount(root) {
    this.root = root;
    const project = this.ctx.project;
    // Tab ngôn ngữ — tạo một lần, chỉ cập nhật chữ/class (không thay phần tử khi polling)
    this.tabs = h('div', { class: 'lang-tabs', hidden: project.languages.length < 2 },
      ...project.languages.map(l => h('button', {
        class: 'lang-tab', dataset: { lang: l },
        onclick: () => { this.flush(); this.ctx.setLang(l); this.update(this.project); },
      })));
    this.voiceSelect = h('select', { onchange: () => this.changeVoice() });
    this.generateBtn = h('button', { class: 'btn btn-primary', onclick: () => this.generateAll() });
    this.info = h('span', { class: 'muted small' });
    this.notice = h('div', {});
    this.list = h('div', { class: 'audio-list' });
    root.replaceChildren(
      h('div', { class: 'section-head' },
        h('h2', {}, 'Giọng đọc'), this.tabs, this.info, h('span', { class: 'grow' }),
        h('label', { class: 'inline' }, 'Giọng:', this.voiceSelect),
        this.generateBtn),
      this.notice,
      this.list);
  }

  async loadVoices(lang) {
    if (this.voicesLang === lang) return;
    this.voicesLang = lang;
    // Xoá ngay danh sách của tab cũ: không để người dùng chọn nhầm giọng ngôn ngữ khác trong lúc chờ
    this.voiceSelect.replaceChildren(h('option', { value: '' }, 'Đang tải giọng…'));
    this.voiceSelect.disabled = true;
    const voices = await this.ctx.voices(lang);
    if (this.voicesLang !== lang) return;  // người dùng đã chuyển tab trong lúc chờ
    this.voiceSelect.replaceChildren(...voices.map(v => h('option', { value: v.id }, v.label)));
    this.voiceSelect.value = this.project.settings.voices?.[lang] || '';
    this.voiceSelect.disabled = false;
  }

  update(project) {
    this.project = project;
    const lang = this.lang;
    const { settings } = this.ctx;
    for (const tab of this.tabs.children) {
      const l = tab.dataset.lang, a = project.summary.langs[l].audio;
      tab.classList.toggle('active', l === lang);
      tab.textContent = `${langName(settings, l)} ${a.ready}/${a.total}`;
    }
    this.loadVoices(lang);
    if (document.activeElement !== this.voiceSelect && this.voiceSelect.options.length) {
      this.voiceSelect.value = project.settings.voices?.[lang] || '';
    }

    const s = project.summary.langs[lang];
    const job = activeJob(project, 'audio', lang);
    const isSource = lang === project.source_lang;
    // Ngôn ngữ dịch: chỉ đọc được cảnh đã có bản dịch
    const readable = project.scenes.filter(sc => isSource || sc.langs[lang]?.text_status === 'ready');
    const todo = readable.filter(sc => sc.langs[lang].audio_status !== 'ready').length;
    this.generateBtn.textContent = job ? 'Đang tạo audio…' : `Tạo audio ${langName(settings, lang)} còn thiếu (${todo})`;
    this.generateBtn.disabled = Boolean(job) || todo === 0;
    const total = s.total_duration_sec;
    this.info.textContent = `${s.audio.ready}/${s.audio.total} audio${total ? ` · tổng ${fmtSec(total)}` : ''} · chạy trên CPU`;
    const untranslated = project.scenes.length - readable.length;
    this.notice.replaceChildren(!isSource && untranslated
      ? h('div', { class: 'notice notice-warn' },
        `${untranslated} cảnh chưa có bản dịch ${langName(settings, lang)} (hoặc bản dịch đã cũ) — `,
        h('a', { href: `#/p/${encodeURIComponent(project.id)}/1` }, 'sang bước 1 để dịch'), '.')
      : '');

    if (!project.scenes.length) {
      this.list.replaceChildren(h('div', { class: 'card center muted' }, 'Chưa có cảnh — quay lại bước 1 để chia cảnh.'));
      this.layoutSig = null;
      this.rows.clear();
      return;
    }
    const layoutSig = lang + ':' + project.scenes.map(sc => sc.id).join(',');
    if (layoutSig !== this.layoutSig) {  // đổi tab hoặc đổi danh sách cảnh → dựng lại
      this.layoutSig = layoutSig;
      this.rows.clear();
      this.list.replaceChildren(...project.scenes.map(sc => this.createRow(sc, lang)));
    }
    project.scenes.forEach((sc, i) => this.updateRow(sc, i, lang));
  }

  createRow(scene, lang) {
    const isSource = lang === this.project.source_lang;
    const narration = h('textarea', { rows: 3 });
    const audio = h('audio', { controls: true, preload: 'none' });
    const row = {
      title: h('strong', {}),
      status: h('span', {}),
      textStatus: h('span', {}),
      duration: h('span', { class: 'muted small' }),
      error: h('p', { class: 'error-text small', hidden: true }),
      regenBtn: h('button', { class: 'btn btn-small', onclick: () => this.regenerate(scene.id, row) }),
      narration, audio, audioUrl: null,
    };
    row.el = h('div', { class: 'card audio-row', dataset: { sceneId: scene.id } },
      h('div', { class: 'scene-row-head' }, row.title, row.status, row.textStatus, h('span', { class: 'grow' }), row.duration),
      h('label', { class: 'small' },
        isSource ? 'Lời đọc (tự lưu — sửa thì audio cần tạo lại)'
          : 'Bản dịch (tự lưu — sửa ở đây cũng như sửa ở bước 1)', narration),
      h('div', { class: 'row' }, audio, h('span', { class: 'grow' }), row.regenBtn),
      row.error);
    const serverText = () => this.project.scenes.find(s => s.id === scene.id)?.langs[lang]?.narration ?? '';
    row.saver = autosave(narration, serverText, async value => {
      const body = isSource ? { narration: value.trim() } : { translations: { [lang]: value.trim() } };
      await this.ctx.api.updateScene(this.project.id, scene.id, body);
      await this.ctx.refresh();
    });
    this.rows.set(scene.id, row);
    return row.el;
  }

  updateRow(scene, index, lang) {
    const r = this.rows.get(scene.id);
    const entry = scene.langs[lang];
    const isSource = lang === this.project.source_lang;
    const jobStatus = sceneJobStatus(this.project, scene, 'audio', lang);
    r.title.textContent = `Cảnh ${index + 1}`;
    r.status.replaceChildren(badge(jobStatus || entry.audio_status, 'Audio: '));
    const needsText = !isSource && entry.text_status !== 'ready';
    r.textStatus.replaceChildren(needsText
      ? h('span', { class: `badge badge-${entry.text_status}` },
        entry.text_status === 'stale' ? 'Bản dịch đã cũ' : 'Chưa dịch')
      : '');
    r.duration.textContent = entry.duration_sec ? fmtSec(entry.duration_sec) : '';

    // Chỉ đổi nguồn audio khi file thật sự đổi → không cắt ngang lúc người dùng đang nghe
    if (entry.audio_url !== r.audioUrl) {
      r.audioUrl = entry.audio_url;
      if (entry.audio_url) r.audio.src = entry.audio_url;
      else r.audio.removeAttribute('src');
    }
    r.audio.hidden = !entry.audio_url;
    r.audio.classList.toggle('dim', entry.audio_status === 'stale');

    if (document.activeElement !== r.narration && !r.saver.isPending()) r.narration.value = entry.narration;

    const audioError = entry.audio_status === 'error' && entry.error;
    r.error.hidden = !audioError;
    r.error.textContent = audioError ? entry.error : '';
    r.regenBtn.disabled = Boolean(jobStatus) || needsText;
    r.regenBtn.title = needsText ? 'Cần bản dịch trước' : '';
    r.regenBtn.textContent = entry.audio_url ? '↻ Tạo lại' : 'Tạo audio';
  }

  async changeVoice() {
    const lang = this.lang, voice = this.voiceSelect.value;
    const hasAudio = this.project.scenes.some(s => s.langs[lang].audio_status !== 'missing');
    const name = langName(this.ctx.settings, lang);
    if (hasAudio && !confirm(`Đổi giọng ${name} sang "${voice}"?\nMọi audio ${name} đã tạo sẽ chuyển sang "Cần tạo lại".`)) {
      this.voiceSelect.value = this.project.settings.voices?.[lang] || '';
      return;
    }
    await guarded(this.voiceSelect, async () => {
      this.ctx.setProject(await this.ctx.api.updateProject(this.project.id, { voices: { [lang]: voice } }));
    });
  }

  async generateAll() {
    this.flush();
    await guarded(this.generateBtn, async () => {
      await this.ctx.api.generateAudio(this.project.id, this.lang);
      await this.ctx.refresh();
    });
  }

  async regenerate(sceneId, row) {
    await guarded(row.regenBtn, async () => {
      await row.saver.flush();
      await this.ctx.api.regenerateAudio(this.project.id, sceneId, this.lang);
      await this.ctx.refresh();
    });
  }
}
