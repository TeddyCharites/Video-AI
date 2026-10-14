// Trang danh sách dự án + form tạo dự án mới.

import { h, fmtDate, fmtSec, toast, guarded } from './ui.js';

const FINAL_LABELS = { ready: 'Đã xuất video', stale: 'Video cũ', missing: 'Chưa xuất' };

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
    const { settings, voices } = this.ctx;
    this.nameInput = h('input', { type: 'text', maxlength: 100, placeholder: 'Ví dụ: Vì sao lá cây màu xanh' });
    this.scriptInput = h('textarea', { rows: 10, placeholder: 'Dán lời thoại của video vào đây…' });
    this.voiceSelect = h('select', {},
      voices.map(v => h('option', { value: v.id, selected: v.id === settings.default_voice }, v.label)));
    this.aspectSelect = h('select', {},
      settings.aspect_ratios.map(a => h('option', { value: a, selected: a === settings.default_aspect_ratio },
        a === '16:9' ? '16:9 (ngang, YouTube)' : '9:16 (dọc, TikTok/Shorts)')));

    this.subtitlesBox = h('input', { type: 'checkbox', checked: settings.default_subtitles });
    const submit = h('button', { class: 'btn btn-primary', type: 'submit' }, 'Tạo dự án');
    return h('form', {
      class: 'card form', hidden: true,
      onsubmit: e => {
        e.preventDefault();
        const name = this.nameInput.value.trim();
        const script = this.scriptInput.value.trim();
        if (!name || !script) return toast('Cần nhập tên dự án và kịch bản', 'error');
        guarded(submit, async () => {
          const p = await this.ctx.api.createProject({
            name, script, voice: this.voiceSelect.value, aspect_ratio: this.aspectSelect.value,
            subtitles: this.subtitlesBox.checked,
          });
          this.nameInput.value = this.scriptInput.value = '';  // để không bị hỏi "chưa lưu" khi chuyển trang
          this.ctx.navigate(`#/p/${encodeURIComponent(p.id)}/1`);
        });
      },
    },
      h('h2', {}, 'Dự án mới'),
      h('label', {}, 'Tên dự án', this.nameInput),
      h('label', {}, 'Kịch bản (lời thoại)', this.scriptInput),
      h('div', { class: 'row' },
        h('label', { class: 'grow' }, 'Giọng đọc', this.voiceSelect),
        h('label', {}, 'Khung hình', this.aspectSelect)),
      h('div', { class: 'row end' },
        h('label', { class: 'inline checkbox grow' }, this.subtitlesBox, 'Chèn phụ đề'),
        h('button', { class: 'btn btn-ghost', type: 'button', onclick: () => this.toggleForm(false) }, 'Huỷ'),
        submit));
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
    this.list.replaceChildren(...projects.map(p => this.renderCard(p)));
  }

  renderCard(p) {
    const s = p.summary;
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
    return h('a', { href, class: 'card project-card' },
      h('div', { class: 'project-card-head' },
        h('h3', {}, p.name),
        p.busy ? h('span', { class: 'badge badge-running' }, 'Đang chạy') : null,
        del),
      h('div', { class: 'muted small' },
        `${fmtDate(p.created_at)} · ${p.settings?.aspect_ratio || ''} · ${p.settings?.voice || ''}`),
      h('div', { class: 'project-stats' },
        h('span', {}, `${p.scene_count} cảnh`),
        h('span', {}, `Ảnh ${s.images.ready}/${s.images.total}`),
        h('span', {}, `Audio ${s.audio.ready}/${s.audio.total}`),
        s.total_duration_sec ? h('span', {}, fmtSec(s.total_duration_sec)) : null,
        h('span', { class: `badge badge-${p.final_status === 'ready' ? 'ready' : p.final_status === 'stale' ? 'stale' : 'missing'}` },
          FINAL_LABELS[p.final_status] || p.final_status)));
  }
}
