// Bước 2 — Storyboard: lưới thẻ cảnh. Mỗi thẻ: ảnh, trạng thái, ô sửa prompt (tự lưu), nút Tạo lại.

import { h, badge, guarded, toast, sceneJobStatus, activeJob, autosave, focusScene } from './ui.js';

export class Step2 {
  constructor(ctx) {
    this.ctx = ctx;
    this.cards = new Map();   // scene id → {el, parts…} để cập nhật từng thẻ, không vẽ lại cả lưới
    this.layoutSig = null;
  }

  isDirty() { return false; }  // prompt tự lưu → không có gì "chưa lưu" lâu dài

  flush() {
    for (const c of this.cards.values()) c.saver.flush();
  }

  focusScene(id) { return focusScene(this.root, id); }

  mount(root) {
    this.root = root;
    this.generateBtn = h('button', { class: 'btn btn-primary', onclick: () => this.generateAll() });
    this.info = h('span', { class: 'muted small' });
    this.grid = h('div', { class: `card-grid aspect-${(this.ctx.project.settings.aspect_ratio || '16:9').replace(':', 'x')}` });
    root.replaceChildren(
      h('div', { class: 'section-head' },
        h('h2', {}, 'Storyboard'), this.info, h('span', { class: 'grow' }), this.generateBtn),
      this.grid);
  }

  update(project) {
    this.project = project;
    const s = project.summary.images;
    const job = activeJob(project, 'images');
    const todo = s.total - s.ready;
    this.generateBtn.textContent = job ? 'Đang tạo ảnh…' : `Tạo các ảnh còn thiếu (${todo})`;
    this.generateBtn.disabled = Boolean(job) || todo === 0;
    const backend = this.ctx.settings.image_backend === 'flux' ? 'FLUX.2 klein · ~8 giây/ảnh' : 'ảnh giả (placeholder)';
    this.info.textContent = `${s.ready}/${s.total} ảnh · ${backend}`;

    if (!project.scenes.length) {
      this.grid.replaceChildren(h('div', { class: 'card center muted' }, 'Chưa có cảnh — quay lại bước 1 để chia cảnh.'));
      this.layoutSig = null;
      this.cards.clear();
      return;
    }
    // Số cảnh / thứ tự đổi → dựng lại lưới; còn lại chỉ cập nhật từng thẻ
    const layoutSig = project.scenes.map(sc => sc.id).join(',');
    if (layoutSig !== this.layoutSig) {
      this.layoutSig = layoutSig;
      this.cards.clear();
      this.grid.replaceChildren(...project.scenes.map((sc, i) => this.createCard(sc, i)));
    }
    project.scenes.forEach((sc, i) => this.updateCard(sc, i));
  }

  createCard(scene, index) {
    const img = h('img', { alt: `Ảnh cảnh ${index + 1}`, loading: 'lazy' });
    const imgLink = h('a', { target: '_blank', title: 'Mở ảnh cỡ lớn' }, img);
    const placeholder = h('div', { class: 'img-placeholder' });
    const overlay = h('div', { class: 'img-overlay', hidden: true }, h('span', { class: 'spinner' }), h('span', {}));
    const status = h('span', {});
    const title = h('strong', {});
    const narration = h('p', { class: 'narration-preview muted small' });
    const error = h('p', { class: 'error-text small', hidden: true });
    const prompt = h('textarea', { rows: 3, placeholder: 'Mô tả ảnh bằng tiếng Anh…' });
    const regenBtn = h('button', { class: 'btn btn-small', onclick: () => this.regenerate(scene.id, card) });

    const card = {
      el: h('div', { class: 'card scene-card', dataset: { sceneId: scene.id } },
        h('div', { class: 'scene-card-head' }, title, h('span', { class: 'grow' }), status),
        h('div', { class: 'img-box' }, imgLink, placeholder, overlay),
        narration,
        h('label', { class: 'small' }, 'Mô tả ảnh (tự lưu)', prompt),
        error,
        h('div', { class: 'row end' }, regenBtn)),
      img, imgLink, placeholder, overlay, status, title, narration, error, prompt, regenBtn,
      imageUrl: null,
    };
    // Sửa prompt → tự lưu → server đánh dấu ảnh "Cần tạo lại"
    card.saver = autosave(prompt, () => this.sceneById(scene.id)?.image_prompt ?? '',
      async value => {
        await this.ctx.api.updateScene(this.project.id, scene.id, { image_prompt: value.trim() });
        await this.ctx.refresh();
      });
    this.cards.set(scene.id, card);
    return card.el;
  }

  updateCard(scene, index) {
    const c = this.cards.get(scene.id);
    const jobStatus = sceneJobStatus(this.project, scene, 'image');
    c.title.textContent = `Cảnh ${index + 1}`;
    c.status.replaceChildren(badge(jobStatus || scene.image_status));
    const narration = scene.langs[this.project.source_lang].narration;  // xem trước lời đọc ngôn ngữ gốc
    c.narration.textContent = narration;
    c.narration.title = narration;

    // Ảnh: chỉ đổi src khi URL đổi (URL có ?v=<thời điểm sửa>) → không nháy ảnh mỗi lần polling
    if (scene.image_url !== c.imageUrl) {
      c.imageUrl = scene.image_url;
      if (scene.image_url) {
        c.img.src = scene.image_url;
        c.imgLink.href = scene.image_url;
      }
    }
    c.imgLink.hidden = !scene.image_url;
    c.placeholder.hidden = Boolean(scene.image_url);
    c.placeholder.textContent = scene.image_status === 'error' ? 'Lỗi tạo ảnh' : 'Chưa có ảnh';
    c.img.classList.toggle('dim', scene.image_status === 'stale');
    c.overlay.hidden = !jobStatus;
    c.overlay.lastChild.textContent = jobStatus === 'running' ? ' Đang tạo…' : ' Đang chờ…';

    // Không ghi đè chữ người dùng đang gõ
    if (document.activeElement !== c.prompt && !c.saver.isPending()) c.prompt.value = scene.image_prompt;

    const imgError = scene.image_status === 'error' && scene.image_error;
    c.error.hidden = !imgError;
    c.error.textContent = imgError ? scene.image_error : '';

    c.regenBtn.disabled = Boolean(jobStatus);
    c.regenBtn.textContent = scene.image_url ? '↻ Tạo lại' : 'Tạo ảnh';
  }

  sceneById(id) {
    return this.project.scenes.find(s => s.id === id);
  }

  async generateAll() {
    this.flush();
    await guarded(this.generateBtn, async () => {
      await this.ctx.api.generateImages(this.project.id);
      await this.ctx.refresh();
    });
  }

  async regenerate(sceneId, card) {
    await guarded(card.regenBtn, async () => {
      await card.saver.flush();   // lưu prompt đang sửa dở trước, để ảnh tạo theo prompt mới
      if (!card.prompt.value.trim() && this.ctx.settings.image_backend !== 'placeholder') {
        toast('Cần nhập mô tả ảnh trước', 'error');
        return;
      }
      await this.ctx.api.regenerateImage(this.project.id, sceneId);
      await this.ctx.refresh();
    });
  }
}
