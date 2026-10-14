// Bước 3 — Giọng đọc: chọn giọng cho dự án; mỗi cảnh có lời đọc (tự lưu), trình phát, thời lượng, Tạo lại.

import { h, badge, fmtSec, guarded, sceneJobStatus, activeJob, autosave, focusScene } from './ui.js';

export class Step3 {
  constructor(ctx) {
    this.ctx = ctx;
    this.rows = new Map();
    this.layoutSig = null;
  }

  isDirty() { return false; }

  flush() {
    for (const r of this.rows.values()) r.saver.flush();
  }

  focusScene(id) { return focusScene(this.root, id); }

  mount(root) {
    this.root = root;
    const { voices } = this.ctx;
    this.voiceSelect = h('select', { onchange: () => this.changeVoice() },
      voices.map(v => h('option', { value: v.id }, v.label)));
    this.generateBtn = h('button', { class: 'btn btn-primary', onclick: () => this.generateAll() });
    this.info = h('span', { class: 'muted small' });
    this.list = h('div', { class: 'audio-list' });
    root.replaceChildren(
      h('div', { class: 'section-head' },
        h('h2', {}, 'Giọng đọc'), this.info, h('span', { class: 'grow' }),
        h('label', { class: 'inline' }, 'Giọng:', this.voiceSelect),
        this.generateBtn),
      this.list);
  }

  update(project) {
    this.project = project;
    const s = project.summary.audio;
    const job = activeJob(project, 'audio');
    const todo = s.total - s.ready;
    this.generateBtn.textContent = job ? 'Đang tạo audio…' : `Tạo audio còn thiếu (${todo})`;
    this.generateBtn.disabled = Boolean(job) || todo === 0;
    const total = project.summary.total_duration_sec;
    this.info.textContent = `${s.ready}/${s.total} audio${total ? ` · tổng ${fmtSec(total)}` : ''} · chạy trên CPU`;
    if (document.activeElement !== this.voiceSelect) this.voiceSelect.value = project.settings.voice;

    if (!project.scenes.length) {
      this.list.replaceChildren(h('div', { class: 'card center muted' }, 'Chưa có cảnh — quay lại bước 1 để chia cảnh.'));
      this.layoutSig = null;
      this.rows.clear();
      return;
    }
    const layoutSig = project.scenes.map(sc => sc.id).join(',');
    if (layoutSig !== this.layoutSig) {
      this.layoutSig = layoutSig;
      this.rows.clear();
      this.list.replaceChildren(...project.scenes.map(sc => this.createRow(sc)));
    }
    project.scenes.forEach((sc, i) => this.updateRow(sc, i));
  }

  createRow(scene) {
    const narration = h('textarea', { rows: 3 });
    const audio = h('audio', { controls: true, preload: 'none' });
    const row = {
      title: h('strong', {}),
      status: h('span', {}),
      duration: h('span', { class: 'muted small' }),
      error: h('p', { class: 'error-text small', hidden: true }),
      regenBtn: h('button', { class: 'btn btn-small', onclick: () => this.regenerate(scene.id, row) }),
      narration, audio, audioUrl: null,
    };
    row.el = h('div', { class: 'card audio-row', dataset: { sceneId: scene.id } },
      h('div', { class: 'scene-row-head' }, row.title, row.status, h('span', { class: 'grow' }), row.duration),
      h('label', { class: 'small' }, 'Lời đọc (tự lưu — sửa thì audio cần tạo lại)', narration),
      h('div', { class: 'row' }, audio, h('span', { class: 'grow' }), row.regenBtn),
      row.error);
    row.saver = autosave(narration, () => this.project.scenes.find(s => s.id === scene.id)?.narration ?? '',
      async value => {
        await this.ctx.api.updateScene(this.project.id, scene.id, { narration: value.trim() });
        await this.ctx.refresh();
      });
    this.rows.set(scene.id, row);
    return row.el;
  }

  updateRow(scene, index) {
    const r = this.rows.get(scene.id);
    const jobStatus = sceneJobStatus(this.project, scene, 'audio');
    r.title.textContent = `Cảnh ${index + 1}`;
    r.status.replaceChildren(badge(jobStatus || scene.audio_status));
    r.duration.textContent = scene.duration_sec ? fmtSec(scene.duration_sec) : '';

    // Chỉ đổi nguồn audio khi file thật sự đổi → không cắt ngang lúc người dùng đang nghe
    if (scene.audio_url !== r.audioUrl) {
      r.audioUrl = scene.audio_url;
      if (scene.audio_url) r.audio.src = scene.audio_url;
      else r.audio.removeAttribute('src');
    }
    r.audio.hidden = !scene.audio_url;
    r.audio.classList.toggle('dim', scene.audio_status === 'stale');

    if (document.activeElement !== r.narration && !r.saver.isPending()) r.narration.value = scene.narration;

    const audioError = scene.audio_status === 'error' && scene.error;
    r.error.hidden = !audioError;
    r.error.textContent = audioError ? scene.error : '';
    r.regenBtn.disabled = Boolean(jobStatus);
    r.regenBtn.textContent = scene.audio_url ? '↻ Tạo lại' : 'Tạo audio';
  }

  async changeVoice() {
    const voice = this.voiceSelect.value;
    const hasAudio = this.project.scenes.some(s => s.audio_status !== 'missing');
    if (hasAudio && !confirm(`Đổi giọng sang "${voice}"?\nMọi audio đã tạo sẽ chuyển sang "Cần tạo lại".`)) {
      this.voiceSelect.value = this.project.settings.voice;
      return;
    }
    await guarded(this.voiceSelect, async () => {
      this.ctx.setProject(await this.ctx.api.updateProject(this.project.id, { voice }));
    });
  }

  async generateAll() {
    this.flush();
    await guarded(this.generateBtn, async () => {
      await this.ctx.api.generateAudio(this.project.id);
      await this.ctx.refresh();
    });
  }

  async regenerate(sceneId, row) {
    await guarded(row.regenBtn, async () => {
      await row.saver.flush();
      await this.ctx.api.regenerateAudio(this.project.id, sceneId);
      await this.ctx.refresh();
    });
  }
}
