// Bước 1 — Kịch bản: sửa kịch bản gốc, chia cảnh (AI hoặc theo đoạn văn), dịch sang ngôn ngữ khác,
// sửa danh sách cảnh dạng BẢN NHÁP (lời đọc / bản dịch / mô tả ảnh; thêm / xoá / đổi thứ tự) rồi bấm Lưu một lần.

import {
  h, badge, countWords, guarded, toast, activeJob, focusScene, langName, estimateSec, sceneJobStatus,
} from './ui.js';

let nextKey = 1;  // khoá tạm cho cảnh mới chưa có id (để vẽ lại danh sách không lẫn cảnh)

const TEXT_LABELS = { missing: 'Chưa dịch', ready: 'Đã dịch', stale: 'Lời gốc đã đổi', error: 'Lỗi dịch' };

export class Step1 {
  constructor(ctx) {
    this.ctx = ctx;
    this.draft = [];          // [{key, id|null, narration, image_prompt, translations: {lang: text}}]
    this.serverSig = null;    // chữ ký danh sách cảnh trên server lần đồng bộ gần nhất
    this.statusSig = null;
  }

  get source() { return this.project.source_lang; }
  get targets() { return this.project.languages.filter(l => l !== this.project.source_lang); }

  // ── Trạng thái bản nháp ──
  static sig(items) {
    return JSON.stringify(items.map(d => [d.id, d.narration.trim(), d.image_prompt.trim(),
      Object.entries(d.translations).map(([l, t]) => [l, t.trim()])]));
  }

  serverDraft(project) {
    const source = project.source_lang;
    return project.scenes.map(s => ({
      key: `s${s.id}`, id: s.id, narration: s.langs[source].narration, image_prompt: s.image_prompt,
      translations: Object.fromEntries(project.languages.filter(l => l !== source)
        .map(l => [l, s.langs[l]?.narration || ''])),
    }));
  }

  draftDirty() {
    return this.serverSig !== null && Step1.sig(this.draft) !== this.serverSig;
  }

  isDirty() {
    return this.scriptDirty() || this.draftDirty();
  }

  scriptDirty() {
    // Chuẩn hoá trước khi so: textarea luôn đổi \r\n thành \n, và khoảng trắng đầu/cuối không tính
    const norm = s => s.replace(/\r\n?/g, '\n').trim();
    return this.scriptInput && norm(this.scriptInput.value) !== norm(this.project.script);
  }

  resetDraft(project) {
    this.draft = this.serverDraft(project);
    this.serverSig = Step1.sig(this.draft);
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
    this.jobStatus = h('div', { class: 'split-status' });
    this.healthNotice = h('div', {});
    // Kiểm tra Ollama một lần khi mở bước 1 → cảnh báo sớm thay vì đợi bấm rồi mới lỗi
    this.ctx.api.health().then(hl => {
      if (hl.message) this.healthNotice.replaceChildren(h('div', { class: 'notice notice-warn' }, '⚠ ', hl.message));
    }).catch(() => {});

    this.translateBtn = h('button', { class: 'btn', onclick: () => this.translateAll() });
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

    // Short: cảnh lấy từ dự án gốc → không có kịch bản / chia cảnh, chỉ sửa từng cảnh bên dưới
    const isShort = p.kind === 'short';
    root.replaceChildren(
      h('div', { class: 'card' },
        h('label', {}, 'Tên dự án', this.nameInput),
        isShort
          ? h('p', { class: 'muted small' },
            'Short lấy cảnh từ dự án gốc (ảnh, giọng đọc, clip dùng lại). Có thể sửa lời đọc từng cảnh bên dưới — ',
            'cảnh đó sẽ được tạo lại giọng và clip khi xuất.')
          : h('div', {},
            h('label', {}, `Kịch bản gốc (${langName(this.ctx.settings, this.source)})`, this.scriptInput),
            h('div', { class: 'row wrap' }, this.scriptInfo, h('span', { class: 'grow' }),
              this.saveScriptBtn, this.splitParaBtn, this.splitAiBtn),
            this.healthNotice),
        this.jobStatus),
      h('div', { class: 'section-head sticky' },
        this.sceneCount, h('span', { class: 'grow' }), this.dirtyLabel, this.undoBtn,
        this.targets.length ? this.translateBtn : null, this.saveBtn),
      this.list,
      h('button', { class: 'btn btn-ghost add-scene', onclick: () => this.insertAt(this.draft.length) }, '+ Thêm cảnh'));

    this.resetDraft(p);
    this.renderList();
    this.updateScriptState();
  }

  update(project) {
    this.project = project;
    const serverSig = Step1.sig(this.serverDraft(project));
    const statusSig = JSON.stringify([project.scenes.map(s => [s.image_status,
      Object.entries(s.langs).map(([l, e]) => [l, e.audio_status, e.text_status, e.edited])]),
      project.jobs.filter(j => ['translate', 'translate_one'].includes(j.kind)).map(j => j.id + j.status)]);
    // Chỉ đồng bộ lại từ server khi người dùng KHÔNG có thay đổi dang dở
    if (!this.draftDirty() && (serverSig !== this.serverSig || statusSig !== this.statusSig)) {
      this.resetDraft(project);
      this.statusSig = statusSig;
      this.renderList();
    }
    if (!this.scriptDirty() && document.activeElement !== this.scriptInput) {
      this.scriptInput.value = project.script;
    }
    if (document.activeElement !== this.nameInput) this.nameInput.value = project.name;

    const split = activeJob(project, 'split');
    const translating = activeJob(project, 'translate') || activeJob(project, 'translate_one');
    this.splitAiBtn.disabled = this.splitParaBtn.disabled = Boolean(split || translating);
    const notice = (job, text) => h('div', { class: 'notice notice-running' }, h('span', { class: 'spinner' }),
      job.status === 'queued' ? ' Đang chờ trong hàng đợi…' : ` ${text}`);
    this.jobStatus.replaceChildren(
      split ? notice(split, 'Đang chia cảnh… (AI có thể mất khoảng 1 phút, lần đầu lâu hơn vì phải nạp model)')
        : translating ? notice(translating, `${translating.message} (AI dịch khoảng 1–2 phút)`) : '');
    this.updateScriptState();
    this.updateDirtyState();
  }

  updateScriptState() {
    const n = countWords(this.scriptInput.value);
    const sec = estimateSec(this.ctx.settings, this.source, this.scriptInput.value);
    this.scriptInfo.textContent = `${n} ${this.source === 'vi' ? 'tiếng' : 'từ'} · khoảng ${Math.round(sec)} giây đọc`;
    this.saveScriptBtn.hidden = !this.scriptDirty();
    this.ctx.renderStepbar();
  }

  updateDirtyState() {
    const dirty = this.draftDirty();
    this.saveBtn.disabled = !dirty;
    this.undoBtn.hidden = !dirty;
    this.dirtyLabel.textContent = dirty ? '● Có thay đổi chưa lưu' : '';
    this.sceneCount.textContent = `Danh sách cảnh (${this.draft.length})`;
    // Dịch: máy chỉ dịch được bản ĐÃ LƯU → khoá khi còn thay đổi dang dở
    const todo = this.project.scenes.filter(s => this.targets.some(l =>
      s.langs[l]?.text_status !== 'ready' && !s.langs[l]?.edited)).length;
    const busy = activeJob(this.project, 'translate') || activeJob(this.project, 'translate_one');
    this.translateBtn.textContent = busy ? 'Đang dịch…' : `🌐 Dịch tất cả (${todo})`;
    this.translateBtn.disabled = Boolean(busy) || dirty || !todo;
    this.translateBtn.title = dirty ? 'Lưu thay đổi trước rồi mới dịch' : '';
    for (const b of this.list.querySelectorAll('button.retranslate')) b.disabled = dirty || Boolean(busy);
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

  /** Thông tin độ dài: "31 tiếng · ~7 giây" — tô vàng nếu ngoài khoảng nên có. */
  meta(lang, text) {
    const [minSec, maxSec] = this.ctx.settings.scene_sec;
    const n = countWords(text);
    const sec = estimateSec(this.ctx.settings, lang, text);
    return h('span', {
      class: `small ${n && (sec < minSec || sec > maxSec) ? 'warn-text' : 'muted'}`,
      title: `Nên ${minSec}–${maxSec} giây/cảnh`,
    }, `${n} ${lang === 'vi' ? 'tiếng' : 'từ'} · ~${Math.round(sec)} giây`);
  }

  renderRow(d, index) {
    const serverScene = d.id ? this.project.scenes.find(s => s.id === d.id) : null;
    const { settings } = this.ctx;
    const meta = h('span', {}, this.meta(this.source, d.narration));

    const narration = h('textarea', {
      rows: 3, value: d.narration, placeholder: `Lời đọc của cảnh (${langName(settings, this.source)})`,
      class: d.narration.trim() ? '' : 'invalid',
      oninput: e => {
        d.narration = e.target.value;
        e.target.classList.toggle('invalid', !d.narration.trim());
        meta.replaceChildren(this.meta(this.source, d.narration));
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
        serverScene ? badge(serverScene.langs[this.source].audio_status, 'Audio: ') : null,
        h('span', { class: 'grow' }),
        meta,
        h('button', { class: 'btn btn-icon', title: 'Lên trên', disabled: index === 0, onclick: () => this.move(index, -1) }, '↑'),
        h('button', { class: 'btn btn-icon', title: 'Xuống dưới', disabled: index === this.draft.length - 1, onclick: () => this.move(index, 1) }, '↓'),
        h('button', { class: 'btn btn-icon danger', title: 'Xoá cảnh', onclick: () => this.remove(index) }, '✕')),
      h('div', { class: this.targets.length ? 'lang-columns' : '' },
        h('label', { class: 'small' }, `Lời đọc — ${langName(settings, this.source)}`, narration),
        ...this.targets.map(lang => this.renderTranslation(d, serverScene, lang))),
      h('label', { class: 'small' }, 'Mô tả ảnh', prompt),
      h('button', { class: 'btn btn-ghost btn-small insert', onclick: () => this.insertAt(index + 1) }, '+ chèn cảnh sau'));
  }

  /** Ô bản dịch của một ngôn ngữ: badge trạng thái, nút Dịch lại / Giữ bản dịch. */
  renderTranslation(d, serverScene, lang) {
    const { settings } = this.ctx;
    const entry = serverScene?.langs[lang];
    const meta = h('span', {}, this.meta(lang, d.translations[lang] || ''));
    const textarea = h('textarea', {
      rows: 3, value: d.translations[lang] || '',
      placeholder: serverScene ? `Bấm "Dịch" để AI dịch, hoặc tự gõ bản ${langName(settings, lang)}` : 'Lưu cảnh trước rồi dịch',
      oninput: e => {
        d.translations[lang] = e.target.value;
        meta.replaceChildren(this.meta(lang, e.target.value));
        this.updateDirtyState();
      },
    });
    const job = serverScene ? sceneJobStatus(this.project, serverScene, 'text', lang) : null;
    const status = job || entry?.text_status || 'missing';
    const label = job ? (job === 'running' ? 'Đang dịch…' : 'Đang chờ dịch') : TEXT_LABELS[status];
    const actions = [];
    if (serverScene) {
      actions.push(h('button', {
        class: 'btn btn-small btn-ghost retranslate', title: 'AI dịch lại cảnh này (ghi đè bản đang có)',
        disabled: Boolean(job),
        onclick: e => guarded(e.target, async () => {
          if (entry?.edited && entry.narration && !confirm('Bản dịch này đã được sửa tay. Dịch lại sẽ ghi đè. Tiếp tục?')) return;
          await this.ctx.api.retranslate(this.project.id, serverScene.id, [lang]);
          await this.ctx.refresh();
        }),
      }, entry?.narration ? '↻ Dịch lại' : 'Dịch'));
      if (entry?.text_status === 'stale' && entry.narration) {
        actions.push(h('button', {
          class: 'btn btn-small btn-ghost', title: 'Bản dịch vẫn đúng với lời gốc mới → giữ nguyên',
          onclick: e => guarded(e.target, async () => {
            await this.ctx.api.keepTranslation(this.project.id, serverScene.id, lang);
            await this.ctx.refresh();
          }),
        }, 'Giữ bản dịch'));
      }
    }
    return h('div', { class: 'translation' },
      h('div', { class: 'row wrap small' },
        h('strong', { class: 'muted' }, `Bản dịch — ${langName(settings, lang)}`),
        h('span', { class: `badge badge-${job || status}` }, label),
        entry?.edited ? h('span', { class: 'muted small', title: '"Dịch tất cả" sẽ không ghi đè bản này' }, '✎ đã sửa tay') : null,
        h('span', { class: 'grow' }), meta, ...actions),
      textarea,
      entry?.text_status === 'error' && entry.error ? h('p', { class: 'error-text small' }, entry.error) : null);
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
    const item = { key: `new${nextKey++}`, id: null, narration: '', image_prompt: '',
                   translations: Object.fromEntries(this.targets.map(l => [l, ''])) };
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
    const server = Object.fromEntries(this.serverDraft(this.project).map(d => [d.id, d]));
    await guarded(this.saveBtn, async () => {
      const p = await this.ctx.api.saveScenes(this.project.id, this.draft.map(d => {
        // Chỉ gửi bản dịch người dùng ĐÃ SỬA (khác server) → server đánh dấu "đã sửa tay"
        const changed = Object.entries(d.translations).filter(([l, t]) =>
          t.trim() && t.trim() !== (server[d.id]?.translations[l] || '').trim());
        return { id: d.id, narration: d.narration.trim(), image_prompt: d.image_prompt.trim(),
                 translations: changed.length ? Object.fromEntries(changed) : null };
      }));
      this.resetDraft(p);       // trước setProject → update() thấy "không dirty"
      this.statusSig = null;    // …và ép nó vẽ lại danh sách (cảnh mới giờ đã có id, badge mới)
      this.ctx.setProject(p);
      toast('Đã lưu danh sách cảnh', 'success');
    });
  }

  async translateAll() {
    await guarded(this.translateBtn, async () => {
      await this.ctx.api.translate(this.project.id, null);
      await this.ctx.refresh();
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
    if (hasScenes && !confirm('Chia cảnh lại sẽ THAY THẾ toàn bộ cảnh hiện có và XOÁ ảnh, audio, bản dịch, video đã tạo. Tiếp tục?')) return;
    if (!hasScenes && this.draftDirty()
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
