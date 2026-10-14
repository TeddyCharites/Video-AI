// Trang danh sách dự án + form tạo dự án mới.

import { h, fmtDate, fmtSec, toast, guarded, langName } from './ui.js';

const FINAL_LABELS = { ready: 'đã xuất', stale: 'video cũ', missing: 'chưa xuất' };

export class ProjectsPage {
  constructor(ctx) {
    this.ctx = ctx;
  }

  mount(root) {
    document.title = 'Video Studio Local';
    this.list = h('div', { class: 'project-list' }, h('p', { class: 'muted' }, 'Đang tải…'));
    this.form = this.renderForm();
    root.replaceChildren(
      h('div', { class: 'page-head' },
        h('h1', {}, 'Dự án'),
        h('button', { class: 'btn btn-primary', onclick: () => this.toggleForm(true) }, '+ Dự án mới')),
      this.form,
      this.list);
    this.load();
  }

  isDirty() {
    return !this.form.hidden && (this.nameInput.value.trim() !== '' || this.scriptInput.value.trim() !== '');
  }

  toggleForm(show) {
    this.form.hidden = !show;
    if (show) this.nameInput.focus();
  }

  renderForm() {
    const { settings } = this.ctx;
    this.nameInput = h('input', { type: 'text', maxlength: 100, placeholder: 'Ví dụ: Vì sao lá cây màu xanh' });
    this.scriptInput = h('textarea', { rows: 10, placeholder: 'Dán lời thoại của video vào đây…' });
    // Ngôn ngữ gốc = ngôn ngữ của kịch bản; các ngôn ngữ khác sẽ được dịch tự động (sửa được)
    this.sourceSelect = h('select', { onchange: () => this.renderTargets() },
      settings.languages.map(l => h('option', { value: l.code, selected: l.code === settings.default_source_lang }, l.name)));
    this.targetsBox = h('div', { class: 'row wrap' });
    this.voiceSelect = h('select', {});
    this.aspectSelect = h('select', {},
      settings.aspect_ratios.map(a => h('option', { value: a, selected: a === settings.default_aspect_ratio },
        a === '16:9' ? '16:9 (ngang, YouTube)' : '9:16 (dọc, TikTok/Shorts)')));
    this.subtitlesBox = h('input', { type: 'checkbox', checked: settings.default_subtitles });
    this.bilingualBox = h('input', { type: 'checkbox' });
    this.renderTargets();

    const submit = h('button', { class: 'btn btn-primary', type: 'submit' }, 'Tạo dự án');
    return h('form', {
      class: 'card form', hidden: true,
      onsubmit: e => {
        e.preventDefault();
        const name = this.nameInput.value.trim();
        const script = this.scriptInput.value.trim();
        if (!name || !script) return toast('Cần nhập tên dự án và kịch bản', 'error');
        const source = this.sourceSelect.value;
        guarded(submit, async () => {
          const p = await this.ctx.api.createProject({
            name, script, source_lang: source,
            languages: [...this.targetsBox.querySelectorAll('input:checked')].map(i => i.value),
            voices: { [source]: this.voiceSelect.value },
            aspect_ratio: this.aspectSelect.value,
            subtitles: this.subtitlesBox.checked, subtitles_bilingual: this.bilingualBox.checked,
          });
          this.nameInput.value = this.scriptInput.value = '';  // để không bị hỏi "chưa lưu" khi chuyển trang
          this.ctx.navigate(`#/p/${encodeURIComponent(p.id)}/1`);
        });
      },
    },
      h('h2', {}, 'Dự án mới'),
      h('label', {}, 'Tên dự án', this.nameInput),
      h('label', {}, 'Kịch bản (lời thoại)', this.scriptInput),
      h('div', { class: 'row wrap' },
        h('label', {}, 'Ngôn ngữ kịch bản', this.sourceSelect),
        h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Làm thêm bản'), this.targetsBox),
        h('label', { class: 'grow' }, 'Giọng đọc (ngôn ngữ gốc)', this.voiceSelect),
        h('label', {}, 'Khung hình', this.aspectSelect)),
      h('div', { class: 'row end wrap' },
        h('label', { class: 'inline checkbox' }, this.subtitlesBox, 'Chèn phụ đề'),
        h('label', { class: 'inline checkbox grow' }, this.bilingualBox, 'Phụ đề song ngữ'),
        h('button', { class: 'btn btn-ghost', type: 'button', onclick: () => this.toggleForm(false) }, 'Huỷ'),
        submit));
  }

  /** Ô chọn ngôn ngữ dịch + danh sách giọng theo ngôn ngữ gốc đang chọn. */
  async renderTargets() {
    const { settings } = this.ctx;
    const source = this.sourceSelect.value;
    const checked = new Set([...this.targetsBox.querySelectorAll('input:checked')].map(i => i.value));
    const firstTime = !this.targetsBox.children.length;
    this.targetsBox.replaceChildren(...settings.languages.filter(l => l.code !== source).map(l =>
      h('label', { class: 'inline checkbox' },
        h('input', { type: 'checkbox', value: l.code,
                     checked: firstTime ? settings.default_target_langs.includes(l.code) : checked.has(l.code) }),
        l.name)));
    const voices = await this.ctx.voices(source);
    const def = settings.languages.find(l => l.code === source)?.default_voice;
    this.voiceSelect.replaceChildren(...voices.map(v => h('option', { value: v.id, selected: v.id === def }, v.label)));
  }

  async load() {
    let projects;
    try {
      projects = await this.ctx.api.listProjects();
    } catch (e) {
      this.list.replaceChildren(h('p', { class: 'error-text' }, e.message));
      return;
    }
    if (!projects.length) {
      this.list.replaceChildren(h('div', { class: 'card center muted' },
        'Chưa có dự án nào. Bấm "+ Dự án mới" để bắt đầu.'));
      this.toggleForm(true);
      return;
    }
    // Short hiện lồng dưới dự án gốc; Short mất dự án gốc thì hiện như dự án thường
    const ids = new Set(projects.map(p => p.id));
    const childrenOf = new Map();
    for (const p of projects) {
      if (p.parent_id && ids.has(p.parent_id)) {
        if (!childrenOf.has(p.parent_id)) childrenOf.set(p.parent_id, []);
        childrenOf.get(p.parent_id).push(p);
      }
    }
    this.list.replaceChildren(...projects.filter(p => !(p.parent_id && ids.has(p.parent_id))).map(p => {
      const kids = childrenOf.get(p.id) || [];
      return kids.length
        ? h('div', { class: 'project-group' }, this.renderCard(p),
          h('div', { class: 'short-children' }, ...kids.map(k => this.renderCard(k, true))))
        : this.renderCard(p);
    }));
  }

  renderCard(p, nested = false) {
    const s = p.summary;
    const { settings } = this.ctx;
    const href = `#/p/${encodeURIComponent(p.id)}/1`;
    const del = h('button', {
      class: 'btn btn-small btn-ghost danger', title: 'Xoá dự án',
      onclick: e => {
        e.preventDefault();
        e.stopPropagation();
        if (!confirm(`Xoá dự án "${p.name}"?\nToàn bộ ảnh, audio, video của dự án sẽ bị xoá vĩnh viễn.`)) return;
        guarded(del, async () => {
          await this.ctx.api.deleteProject(p.id);
          toast(`Đã xoá "${p.name}"`);
          this.load();
        });
      },
    }, 'Xoá');
    const duration = s.langs[p.source_lang]?.total_duration_sec;
    const isShort = p.kind === 'short';
    return h('a', { href, class: `card project-card${nested ? ' nested' : ''}` },
      h('div', { class: 'project-card-head' },
        isShort ? h('span', { class: 'badge badge-short' }, '✂ Short') : null,
        h('h3', {}, p.name),
        p.busy ? h('span', { class: 'badge badge-running' }, 'Đang chạy') : null,
        del),
      h('div', { class: 'muted small' },
        `${fmtDate(p.created_at)} · ${p.settings?.aspect_ratio || ''} · `
        + p.languages.map(l => langName(settings, l)).join(' + ')),
      h('div', { class: 'project-stats' },
        h('span', {}, `${p.scene_count} cảnh`),
        h('span', {}, `Ảnh ${s.images.ready}/${s.images.total}`),
        ...p.languages.map(l => h('span', {}, `Audio ${l} ${s.langs[l].audio.ready}/${s.langs[l].audio.total}`)),
        duration ? h('span', {}, fmtSec(duration)) : null),
      h('div', { class: 'project-stats' },
        ...p.languages.map(l => {
          const st = p.finals[l]?.status || 'missing';
          return h('span', { class: `badge badge-${st}` }, `${l.toUpperCase()}: ${FINAL_LABELS[st] || st}`);
        })));
  }
}
