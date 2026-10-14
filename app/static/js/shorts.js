// Khung "✂ Shorts" ở bước 4 của dự án DÀI: AI gợi ý 3–5 đoạn đáng làm Short (các cảnh liền nhau, 30–60s),
// người dùng chỉnh tiêu đề / đoạn cảnh rồi "Tạo Short" → dự án con 9:16 dùng lại ảnh, audio, clip có sẵn.
//
// Phần tử được tạo một lần, chỉ vẽ lại khi dữ liệu đổi (tránh thay nút giữa lúc đang bấm khi polling).

import { h, fmtSec, guarded, toast, activeJob, langName } from './ui.js';

export class ShortsPanel {
  constructor(ctx) {
    this.ctx = ctx;
    this.suggestSig = null;
    this.listSig = null;
  }

  mount(parentEl) {
    this.suggestBtn = h('button', { class: 'btn btn-primary', onclick: () => this.suggest() }, '✨ Gợi ý bằng AI');
    this.status = h('div', {});
    this.suggestions = h('div', { class: 'short-suggestions' });
    this.list = h('div', { class: 'short-list' });
    this.el = h('div', { class: 'card shorts-box' },
      h('div', { class: 'row wrap' }, h('h3', {}, '✂ Shorts từ video này'), h('span', { class: 'grow' }), this.suggestBtn),
      h('p', { class: 'muted small' },
        `AI chọn các đoạn hay (vài cảnh liền nhau, ${this.ctx.settings.short_sec.join('–')} giây) để làm video dọc 9:16 — dùng lại ảnh, giọng đọc và `,
        'animation có sẵn, thêm tiêu đề, phụ đề lớn và câu kết dẫn về video đầy đủ. Mỗi Short là một dự án con.'),
      this.status, this.suggestions, this.list);
    parentEl.append(this.el);
  }

  update(project) {
    this.project = project;
    const job = activeJob(project, 'suggest_shorts');
    this.suggestBtn.disabled = Boolean(job) || !project.scenes.length;
    this.suggestBtn.textContent = job ? 'Đang gợi ý…' : (project.short_suggestions ? '↻ Gợi ý lại' : '✨ Gợi ý bằng AI');
    this.status.replaceChildren(job
      ? h('div', { class: 'notice notice-running' }, h('span', { class: 'spinner' }),
        job.status === 'queued' ? ' Đang chờ trong hàng đợi…' : ' AI đang đọc kịch bản và chọn đoạn hay (khoảng 1–2 phút)…')
      : '');

    const items = project.short_suggestions?.items || [];
    const sig = JSON.stringify([items, project.scenes.map(s => [s.id, s.langs[project.source_lang].duration_sec])]);
    if (sig !== this.suggestSig) {
      this.suggestSig = sig;
      this.suggestions.replaceChildren(...items.map((it, i) => this.renderSuggestion(it, i)));
    }
    const shorts = project.shorts || [];
    const listSig = JSON.stringify(shorts);
    if (listSig !== this.listSig) {
      this.listSig = listSig;
      this.list.replaceChildren(...(shorts.length
        ? [h('h4', {}, `Short đã tạo (${shorts.length})`), ...shorts.map(s => this.renderShort(s))] : []));
    }
  }

  /** Thời lượng (giây, theo audio ngôn ngữ gốc) của đoạn cảnh từ vị trí a tới b. */
  seconds(a, b) {
    const { project, ctx } = this;
    const src = project.source_lang;
    const ups = ctx.settings.languages.find(l => l.code === src)?.units_per_sec || 4;
    return project.scenes.slice(a, b + 1).reduce((sum, s) => {
      const e = s.langs[src];
      return sum + (e.duration_sec || e.narration.split(/\s+/).length / ups);
    }, 0);
  }

  renderSuggestion(it, index) {
    const { project, ctx } = this;
    const src = project.source_lang;
    const ids = project.scenes.map(s => s.id);
    let from = Math.max(0, ids.indexOf(it.scene_ids[0]));
    let to = Math.max(from, ids.indexOf(it.scene_ids[it.scene_ids.length - 1]));
    const titleInput = h('input', { type: 'text', value: it.titles[src] || '', maxlength: 100 });
    const option = (i, sel) => h('option', { value: i, selected: i === sel }, `Cảnh ${i + 1}`);
    const fromSel = h('select', {}, ids.map((_, i) => option(i, from)));
    const toSel = h('select', {}, ids.map((_, i) => option(i, to)));
    const info = h('span', { class: 'small' });
    const create = h('button', { class: 'btn btn-primary btn-small' }, 'Tạo Short');
    const refresh = () => {
      from = Number(fromSel.value);
      to = Math.max(from, Number(toSel.value));
      toSel.value = to;
      const sec = this.seconds(from, to);
      const [min, max] = ctx.settings.short_sec;
      info.textContent = `${to - from + 1} cảnh · ${fmtSec(sec)}`;
      info.className = `small ${sec < min * 0.9 || sec > max * 1.1 ? 'warn-text' : 'muted'}`;
      info.title = `Nên ${min}–${max} giây`;
    };
    fromSel.addEventListener('change', refresh);
    toSel.addEventListener('change', refresh);
    refresh();
    create.addEventListener('click', () => guarded(create, async () => {
      const titles = { ...it.titles, [src]: titleInput.value.trim() || it.titles[src] };
      const child = await ctx.api.createShort(project.id, { scene_ids: ids.slice(from, to + 1), titles });
      toast(`Đã tạo Short "${titles[src]}"`, 'success');
      ctx.navigate(`#/p/${encodeURIComponent(child.id)}/4`);
    }));
    return h('div', { class: 'short-suggestion' },
      h('div', { class: 'row wrap' }, h('strong', {}, `#${index + 1}`), titleInput),
      h('div', { class: 'row wrap small' }, 'Từ', fromSel, 'đến', toSel, info, h('span', { class: 'grow' }), create),
      it.reason ? h('p', { class: 'muted small' }, '💡 ', it.reason) : null,
      h('p', { class: 'muted small short-preview' }, project.scenes.slice(from, to + 1)
        .map(s => s.langs[src].narration).join(' ')));
  }

  renderShort(s) {
    const { project, ctx } = this;
    const title = s.titles[project.source_lang] || s.id;
    const pos = s.scene_ids.map(id => project.scenes.findIndex(x => x.id === id) + 1).filter(n => n > 0);
    const range = pos.length ? `cảnh ${pos[0]}${pos.length > 1 ? `–${pos[pos.length - 1]}` : ''}` : '';
    const label = { ready: 'đã xuất', stale: 'video cũ', missing: 'chưa xuất' };
    return h('a', { class: 'short-row', href: `#/p/${encodeURIComponent(s.id)}/4` },
      h('span', {}, '📱 ', h('strong', {}, title)), h('span', { class: 'muted small' }, range),
      h('span', { class: 'grow' }),
      ...Object.entries(s.finals).map(([l, st]) =>
        h('span', { class: `badge badge-${st}`, title: langName(ctx.settings, l) }, `${l.toUpperCase()}: ${label[st] || st}`)));
  }

  async suggest() {
    if (this.project.short_suggestions && !confirm('Gợi ý lại sẽ thay danh sách đề xuất hiện tại. Tiếp tục?')) return;
    await guarded(this.suggestBtn, async () => {
      await this.ctx.api.suggestShorts(this.project.id);
      await this.ctx.refresh();
    });
  }
}
