// Bước 1 — Kịch bản: sửa kịch bản gốc, chia cảnh (AI hoặc theo đoạn văn),
// sửa danh sách cảnh dạng BẢN NHÁP (sửa / thêm / xoá / đổi thứ tự) rồi bấm Lưu một lần.

import { h, badge, countWords, guarded, toast, activeJob, focusScene } from './ui.js';

let nextKey = 1;  // khoá tạm cho cảnh mới chưa có id (để vẽ lại danh sách không lẫn cảnh)

const sceneSig = scenes => JSON.stringify(scenes.map(s => [s.id, s.narration, s.image_prompt]));

export class Step1 {
  constructor(ctx) {
    this.ctx = ctx;
    this.draft = [];          // [{key, id|null, narration, image_prompt}]
    this.serverSig = null;    // chữ ký danh sách cảnh trên server lần đồng bộ gần nhất
    this.statusSig = null;
  }

  // ── Trạng thái bản nháp ──
  draftSig() {
    return JSON.stringify(this.draft.map(d => [d.id, d.narration.trim(), d.image_prompt.trim()]));
  }

  isDirty() {
    return this.scriptDirty() || (this.serverSig !== null && this.draftSig() !== this.serverSig);
  }

  scriptDirty() {
    // Chuẩn hoá trước khi so: textarea luôn đổi \r\n thành \n, và khoảng trắng đầu/cuối không tính
    const norm = s => s.replace(/\r\n?/g, '\n').trim();
    return this.scriptInput && norm(this.scriptInput.value) !== norm(this.project.script);
  }

  resetDraft(project) {
    this.draft = project.scenes.map(s => ({
      key: `s${s.id}`, id: s.id, narration: s.narration, image_prompt: s.image_prompt,
    }));
    this.serverSig = sceneSig(project.scenes);
  }

  // ── Vẽ ──
  mount(root) {
    this.root = root;
    const p = this.ctx.project;
    this.project = p;

    this.nameInput = h('input', {
      type: 'text', value: p.name, maxlength: 100,
      onchange: () => this.saveName(),
    });
    this.scriptInput = h('textarea', {
      rows: 8, value: p.script,
      oninput: () => this.updateScriptState(),
    });
    this.scriptInfo = h('span', { class: 'muted small' });
    this.saveScriptBtn = h('button', { class: 'btn', onclick: () => guarded(this.saveScriptBtn, () => this.saveScript()) },
      'Lưu kịch bản');
    this.splitAiBtn = h('button', { class: 'btn btn-primary', onclick: () => this.split('llm') }, '✨ Chia cảnh bằng AI');
    this.splitParaBtn = h('button', { class: 'btn', onclick: () => this.split('paragraphs') }, 'Chia theo đoạn văn');
    this.splitStatus = h('div', { class: 'split-status' });
    this.healthNotice = h('div', {});
    // Kiểm tra Ollama một lần khi mở bước 1 → cảnh báo sớm thay vì đợi bấm rồi mới lỗi
    this.ctx.api.health().then(hl => {
      if (hl.message) this.healthNotice.replaceChildren(h('div', { class: 'notice notice-warn' }, '⚠ ', hl.message));
    }).catch(() => {});

    this.saveBtn = h('button', { class: 'btn btn-primary', onclick: () => this.saveScenes() }, 'Lưu thay đổi');
    this.undoBtn = h('button', {
      class: 'btn btn-ghost',
      onclick: () => {
        if (!confirm('Bỏ mọi thay đổi chưa lưu trong danh sách cảnh?')) return;
        this.resetDraft(this.project);
        this.renderList();
      },
    }, 'Hoàn tác');
    this.dirtyLabel = h('span', { class: 'dirty-label' });
    this.sceneCount = h('h2', {});
    this.list = h('div', { class: 'scene-list' });

    root.replaceChildren(
      h('div', { class: 'card' },
        h('label', {}, 'Tên dự án', this.nameInput),
        h('label', {}, 'Kịch bản gốc', this.scriptInput),
        h('div', { class: 'row wrap' }, this.scriptInfo, h('span', { class: 'grow' }),
          this.saveScriptBtn, this.splitParaBtn, this.splitAiBtn),
        this.healthNotice,
        this.splitStatus),
      h('div', { class: 'section-head sticky' },
        this.sceneCount, h('span', { class: 'grow' }), this.dirtyLabel, this.undoBtn, this.saveBtn),
      this.list,
      h('button', { class: 'btn btn-ghost add-scene', onclick: () => this.insertAt(this.draft.length) }, '+ Thêm cảnh'));

    this.resetDraft(p);
    this.renderList();
    this.updateScriptState();
  }

  update(project) {
    this.project = project;
    const sig = sceneSig(project.scenes);
    const statusSig = JSON.stringify(project.scenes.map(s => [s.image_status, s.audio_status]));
    // Chỉ đồng bộ lại từ server khi người dùng KHÔNG có thay đổi dang dở
    const draftDirty = this.draftSig() !== this.serverSig;
    if (!draftDirty && (sig !== this.serverSig || statusSig !== this.statusSig)) {
      this.resetDraft(project);
      this.statusSig = statusSig;
      this.renderList();
    }
    if (!this.scriptDirty() && document.activeElement !== this.scriptInput) {
      this.scriptInput.value = project.script;
    }
    if (document.activeElement !== this.nameInput) this.nameInput.value = project.name;

    const job = activeJob(project, 'split');
    this.splitAiBtn.disabled = this.splitParaBtn.disabled = Boolean(job);
    this.splitStatus.replaceChildren(job
      ? h('div', { class: 'notice notice-running' }, h('span', { class: 'spinner' }),
        job.status === 'queued' ? ' Đang chờ trong hàng đợi…'
          : ' Đang chia cảnh… (AI có thể mất khoảng 1 phút, lần đầu lâu hơn vì phải nạp model)')
      : '');
    this.updateScriptState();
    this.updateDirtyState();
  }

  updateScriptState() {
    const n = countWords(this.scriptInput.value);
    const sec = n / this.ctx.settings.syllables_per_sec;
    this.scriptInfo.textContent = `${n} tiếng · khoảng ${Math.round(sec)} giây đọc`;
    this.saveScriptBtn.hidden = !this.scriptDirty();
    this.ctx.renderStepbar();
  }

  updateDirtyState() {
    const dirty = this.draftSig() !== this.serverSig;
    this.saveBtn.disabled = !dirty;
    this.undoBtn.hidden = !dirty;
    this.dirtyLabel.textContent = dirty ? '● Có thay đổi chưa lưu' : '';
    this.sceneCount.textContent = `Danh sách cảnh (${this.draft.length})`;
    this.ctx.renderStepbar();
  }

  renderList() {
    if (!this.draft.length) {
      this.list.replaceChildren(h('div', { class: 'card center muted' },
        'Chưa có cảnh. Bấm "Chia cảnh bằng AI" (hoặc "Chia theo đoạn văn") ở trên, hoặc "+ Thêm cảnh".'));
    } else {
      this.list.replaceChildren(...this.draft.map((d, i) => this.renderRow(d, i)));
    }
    this.updateDirtyState();
  }

  renderRow(d, index) {
    const serverScene = d.id ? this.project.scenes.find(s => s.id === d.id) : null;
    const [minSec, maxSec] = this.ctx.settings.scene_sec;
    const meta = h('span', { class: 'small' });
    const updateMeta = () => {
      const n = countWords(d.narration);
      const sec = n / this.ctx.settings.syllables_per_sec;
      meta.textContent = `${n} tiếng · ~${Math.round(sec)} giây`;
      // Gợi ý độ dài cảnh theo config (không chặn)
      meta.className = `small ${n && (sec < minSec || sec > maxSec) ? 'warn-text' : 'muted'}`;
      meta.title = `Nên ${minSec}–${maxSec} giây/cảnh`;
    };
    updateMeta();

    const narration = h('textarea', {
      rows: 3, value: d.narration, placeholder: 'Lời đọc của cảnh (tiếng Việt)',
      class: d.narration.trim() ? '' : 'invalid',
      oninput: e => {
        d.narration = e.target.value;
        e.target.classList.toggle('invalid', !d.narration.trim());
        updateMeta();
        this.updateDirtyState();
      },
    });
    const prompt = h('textarea', {
      rows: 2, value: d.image_prompt, placeholder: 'Mô tả ảnh bằng tiếng Anh (vd. a child watering a plant)',
      oninput: e => { d.image_prompt = e.target.value; this.updateDirtyState(); },
    });

    return h('div', { class: 'card scene-row', dataset: { sceneId: d.id || d.key } },
      h('div', { class: 'scene-row-head' },
        h('strong', {}, `Cảnh ${index + 1}`),
        d.id ? h('span', { class: 'muted small' }, `#${d.id}`) : h('span', { class: 'badge badge-missing' }, 'Mới'),
        serverScene ? badge(serverScene.image_status, 'Ảnh: ') : null,
        serverScene ? badge(serverScene.audio_status, 'Audio: ') : null,
        h('span', { class: 'grow' }),
        meta,
        h('button', { class: 'btn btn-icon', title: 'Lên trên', disabled: index === 0, onclick: () => this.move(index, -1) }, '↑'),
        h('button', { class: 'btn btn-icon', title: 'Xuống dưới', disabled: index === this.draft.length - 1, onclick: () => this.move(index, 1) }, '↓'),
        h('button', { class: 'btn btn-icon danger', title: 'Xoá cảnh', onclick: () => this.remove(index) }, '✕')),
      h('label', { class: 'small' }, 'Lời đọc', narration),
      h('label', { class: 'small' }, 'Mô tả ảnh', prompt),
      h('button', { class: 'btn btn-ghost btn-small insert', onclick: () => this.insertAt(index + 1) }, '+ chèn cảnh sau'));
  }

  // ── Sửa danh sách ──
  move(index, delta) {
    const [item] = this.draft.splice(index, 1);
    this.draft.splice(index + delta, 0, item);
    this.renderList();
    focusScene(this.list, item.id || item.key);
  }

  remove(index) {
    const d = this.draft[index];
    if (d.id && !confirm(`Xoá cảnh ${index + 1}? (Ảnh/audio của cảnh này sẽ bị xoá khi bạn bấm Lưu)`)) return;
    this.draft.splice(index, 1);
    this.renderList();
  }

  insertAt(index) {
    const item = { key: `new${nextKey++}`, id: null, narration: '', image_prompt: '' };
    this.draft.splice(index, 0, item);
    this.renderList();
    focusScene(this.list, item.key);
    this.list.querySelector(`[data-scene-id="${item.key}"] textarea`)?.focus();
  }

  // ── Lưu ──
  async saveScenes() {
    const emptyIndex = this.draft.findIndex(d => !d.narration.trim());
    if (emptyIndex >= 0) {
      toast(`Cảnh ${emptyIndex + 1} chưa có lời đọc`, 'error');
      focusScene(this.list, this.draft[emptyIndex].id || this.draft[emptyIndex].key);
      return;
    }
    if (!this.draft.length) return toast('Cần ít nhất một cảnh', 'error');
    await guarded(this.saveBtn, async () => {
      const p = await this.ctx.api.saveScenes(this.project.id, this.draft.map(d => ({
        id: d.id, narration: d.narration.trim(), image_prompt: d.image_prompt.trim(),
      })));
      this.resetDraft(p);       // trước setProject → update() thấy "không dirty"
      this.statusSig = null;    // …và ép nó vẽ lại danh sách (cảnh mới giờ đã có id, badge mới)
      this.ctx.setProject(p);
      toast('Đã lưu danh sách cảnh', 'success');
    });
  }

  async saveScript() {
    const p = await this.ctx.api.updateProject(this.project.id, { script: this.scriptInput.value.trim() });
    this.project = p;
    this.ctx.setProject(p);
    this.updateScriptState();
  }

  async saveName() {
    const name = this.nameInput.value.trim();
    if (!name || name === this.project.name) return;
    try {
      this.ctx.setProject(await this.ctx.api.updateProject(this.project.id, { name }));
    } catch (e) {
      toast(e.message, 'error');
    }
  }

  async split(mode) {
    if (!this.scriptInput.value.trim()) return toast('Kịch bản đang trống', 'error');
    const hasScenes = this.project.scenes.length > 0;
    if (hasScenes && !confirm('Chia cảnh lại sẽ THAY THẾ toàn bộ cảnh hiện có và XOÁ ảnh, audio, video đã tạo. Tiếp tục?')) return;
    if (!hasScenes && this.draftSig() !== this.serverSig
        && !confirm('Bỏ các cảnh bạn đang soạn dở để chia lại từ kịch bản?')) return;
    const btn = mode === 'llm' ? this.splitAiBtn : this.splitParaBtn;
    await guarded(btn, async () => {
      if (this.scriptDirty()) await this.saveScript();   // chia theo kịch bản MỚI NHẤT
      this.resetDraft(this.project);                      // bỏ bản nháp → nhận kết quả chia mới
      await this.ctx.api.split(this.project.id, mode, hasScenes);
      await this.ctx.refresh();
    });
  }
}
