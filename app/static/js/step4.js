// Bước 4 — Xuất video: bảng tổng hợp từng cảnh, cảnh báo cảnh thiếu/lỗi, nút Xuất, tiến độ, xem & tải video.

import { h, badge, fmtSec, guarded, activeJob, focusScene, toast } from './ui.js';

const FIELD_LABELS = { image_status: 'ảnh', audio_status: 'audio', clip_status: 'clip' };
const FIELD_STEP = { image_status: 2, audio_status: 3 };

export class Step4 {
  constructor(ctx) {
    this.ctx = ctx;
    this.finalUrl = null;
  }

  isDirty() { return false; }

  focusScene(id) { return focusScene(this.root, id); }

  mount(root) {
    this.root = root;
    this.stats = h('div', { class: 'stats' });
    this.problems = h('div', {});
    this.exportBtn = h('button', { class: 'btn btn-primary btn-large', onclick: () => this.export() }, 'Xuất video');
    this.progress = h('div', {});
    this.result = h('div', {});
    this.video = h('video', { controls: true, preload: 'metadata', class: 'final-video' });
    this.downloadLink = h('a', { class: 'btn', download: true }, '⬇ Tải video');
    this.srtLink = h('a', { class: 'btn btn-ghost', download: true }, '⬇ Phụ đề (.srt)');
    this.subtitlesBox = h('input', { type: 'checkbox', onchange: () => this.toggleOption('subtitles', this.subtitlesBox) });
    this.titleBox = h('input', { type: 'checkbox', onchange: () => this.toggleOption('title', this.titleBox) });
    const remotion = this.ctx.settings.render_engine === 'remotion';
    this.table = h('tbody', {});
    this.musicBox = this.createMusicBox();

    root.replaceChildren(
      h('div', { class: 'section-head' }, h('h2', {}, 'Xuất video')),
      this.stats,
      this.problems,
      h('div', { class: 'card export-box' },
        h('div', { class: 'row wrap' }, this.exportBtn,
          h('span', { class: 'muted small grow' },
            remotion
              ? 'Tạo animation vẽ bảng cho các cảnh chưa có clip, rồi Remotion ghép tiêu đề, chuyển cảnh, giọng đọc và phụ đề thành một video.'
              : 'Tạo animation vẽ bảng cho các cảnh chưa có clip, rồi ghép tất cả cùng giọng đọc thành một video.'),
          remotion ? h('label', { class: 'inline checkbox' }, this.titleBox, 'Màn hình tiêu đề') : null,
          h('label', { class: 'inline checkbox' }, this.subtitlesBox, 'Chèn phụ đề vào video')),
        this.progress,
        this.result),
      this.musicBox,
      h('div', { class: 'card table-wrap' },
        h('table', { class: 'summary-table' },
          h('thead', {}, h('tr', {},
            h('th', {}, '#'), h('th', {}, 'Lời đọc'), h('th', {}, 'Thời lượng'),
            h('th', {}, 'Ảnh'), h('th', {}, 'Audio'), h('th', {}, 'Clip'))),
          this.table)));
  }

  update(project) {
    this.project = project;
    const scenes = project.scenes;
    const job = activeJob(project, 'export');
    const s = project.summary;

    this.stats.replaceChildren(
      stat('Số cảnh', scenes.length),
      stat('Tổng thời lượng', s.total_duration_sec ? fmtSec(s.total_duration_sec) : '—'),
      stat('Ảnh', `${s.images.ready}/${s.images.total}`),
      stat('Audio', `${s.audio.ready}/${s.audio.total}`),
      stat('Clip', `${s.clips.ready}/${s.clips.total}`));

    // Cảnh thiếu/lỗi: liệt kê rõ cảnh nào, thiếu gì, kèm nút nhảy tới đúng bước.
    // Chỉ vẽ lại khi danh sách đổi — vẽ lại mỗi lần polling sẽ thay các nút đang được bấm (mất click).
    const problems = project.export_problems;
    const problemsSig = JSON.stringify([problems, scenes.map(sc => sc.id)]);
    if (problemsSig !== this.problemsSig) {
      this.problemsSig = problemsSig;
      this.renderProblems(problems, scenes);
    }

    this.exportBtn.disabled = Boolean(job) || problems.length > 0 || !scenes.length;
    this.subtitlesBox.checked = project.settings.subtitles ?? this.ctx.settings.default_subtitles;
    this.subtitlesBox.disabled = Boolean(job);
    this.titleBox.checked = project.settings.title ?? this.ctx.settings.default_title;
    this.titleBox.disabled = Boolean(job);
    this.exportBtn.textContent = job ? 'Đang xuất…' : project.final_url ? 'Xuất lại video' : 'Xuất video';

    if (job) {
      const pct = job.total ? Math.round(100 * job.done / job.total) : 0;
      this.progress.replaceChildren(h('div', { class: 'export-progress' },
        h('div', { class: 'row' }, h('span', { class: 'spinner' }),
          h('span', {}, job.status === 'queued' ? 'Đang chờ các tác vụ khác…' : job.message),
          h('span', { class: 'grow' }), h('strong', {}, `${pct}%`)),
        h('div', { class: 'progress' }, h('div', { class: 'progress-bar', style: `width:${pct}%` }))));
    } else {
      this.progress.replaceChildren();
    }

    this.renderResult(project);
    this.updateMusicBox(project, job);
    this.table.replaceChildren(...scenes.map((sc, i) => h('tr', { dataset: { sceneId: sc.id } },
      h('td', {}, i + 1),
      h('td', { class: 'narration-cell', title: sc.narration }, sc.narration),
      h('td', { class: 'nowrap' }, sc.duration_sec ? fmtSec(sc.duration_sec) : '—'),
      h('td', {}, badge(sc.image_status)),
      h('td', {}, badge(sc.audio_status)),
      h('td', {}, badge(sc.clip_status)))));
  }

  // ── Nhạc nền ──────────────────────────────────────────────────────────────
  createMusicBox() {
    const m = this.music = {};
    m.fileInput = h('input', {
      type: 'file', accept: '.mp3,.wav,.m4a,.aac,.ogg,.flac,audio/*', hidden: true,
      onchange: () => this.uploadMusic(),
    });
    m.chooseBtn = h('button', { class: 'btn', onclick: () => m.fileInput.click() });
    m.name = h('strong', {});
    m.info = h('span', { class: 'muted small' });
    m.audio = h('audio', { controls: true, preload: 'none' });
    m.volume = h('input', { type: 'range', min: 0, max: 50, step: 1, class: 'volume' });
    m.volumeLabel = h('span', { class: 'small nowrap' });
    m.volume.addEventListener('input', () => { m.volumeLabel.textContent = `${m.volume.value}%`; });
    // Chỉ lưu khi THẢ thanh trượt ("change"), không phải mỗi lần kéo ("input")
    m.volume.addEventListener('change', () => this.saveMusic({ music_volume: m.volume.value / 100 }, m.volume));
    m.duck = h('input', { type: 'checkbox', onchange: () => this.saveMusic({ music_duck: m.duck.checked }, m.duck) });
    m.removeBtn = h('button', {
      class: 'btn btn-small btn-ghost danger',
      onclick: () => {
        if (!confirm('Xoá nhạc nền khỏi dự án?')) return;
        guarded(m.removeBtn, async () => this.ctx.setProject(await this.ctx.api.deleteMusic(this.project.id)));
      },
    }, 'Xoá nhạc');
    m.empty = h('p', { class: 'muted small' },
      'Chưa có nhạc nền. Chọn file mp3/wav/m4a… (nhạc của bạn hoặc nhạc miễn phí bản quyền). ',
      'Nhạc tự lặp cho đủ dài video, mờ dần ở đầu và cuối.');
    m.controls = h('div', { class: 'music-controls' },
      h('div', { class: 'row wrap' }, m.name, m.info, h('span', { class: 'grow' }), m.removeBtn),
      m.audio,
      h('div', { class: 'row wrap' },
        h('label', { class: 'inline' }, 'Âm lượng', m.volume, m.volumeLabel),
        h('label', { class: 'inline checkbox' }, m.duck, 'Tự giảm nhạc khi có giọng đọc')));
    return h('div', { class: 'card music-box' },
      h('div', { class: 'row' }, h('h3', {}, '🎵 Nhạc nền'), h('span', { class: 'grow' }), m.chooseBtn, m.fileInput),
      m.empty, m.controls);
  }

  updateMusicBox(project, job) {
    const m = this.music;
    const music = project.settings.music;
    m.empty.hidden = Boolean(music);
    m.controls.hidden = !music;
    m.chooseBtn.textContent = music ? 'Đổi nhạc…' : 'Chọn file nhạc…';
    m.chooseBtn.disabled = m.removeBtn.disabled = Boolean(job);  // đang xuất → không đổi nhạc
    if (!music) return;
    m.name.textContent = music.name;
    m.info.textContent = `${fmtSec(music.duration_sec)}`;
    if (project.music_url !== m.url) {  // chỉ đổi khi là file mới → không cắt ngang lúc đang nghe
      m.url = project.music_url;
      m.audio.src = project.music_url;
    }
    if (document.activeElement !== m.volume) {
      m.volume.value = Math.round(music.volume * 100);
      m.volumeLabel.textContent = `${m.volume.value}%`;
    }
    m.duck.checked = music.duck;
  }

  async uploadMusic() {
    const m = this.music;
    const file = m.fileInput.files[0];
    m.fileInput.value = '';  // cho phép chọn lại đúng file đó lần sau
    if (!file) return;
    m.chooseBtn.textContent = 'Đang tải lên…';
    await guarded(m.chooseBtn, async () => {
      this.ctx.setProject(await this.ctx.api.uploadMusic(this.project.id, file));
      toast(`Đã thêm nhạc nền "${file.name}"`, 'success');
    });
    this.updateMusicBox(this.project, null);
  }

  async saveMusic(body, control) {
    await guarded(control, async () => {
      this.ctx.setProject(await this.ctx.api.updateProject(this.project.id, body));
    });
  }

  renderProblems(problems, scenes) {
    this.problems.replaceChildren(problems.length
      ? h('div', { class: 'notice notice-warn' },
        h('strong', {}, `Chưa thể xuất: còn ${problems.length} cảnh chưa sẵn sàng`),
        h('ul', {}, problems.map(p => {
          const idx = scenes.findIndex(sc => sc.id === p.id) + 1;
          return h('li', {},
            `Cảnh ${idx}: `,
            Object.entries(p.not_ready).map(([f, st]) =>
              h('button', { class: 'link', onclick: () => this.ctx.goToScene(FIELD_STEP[f], p.id) },
                `${FIELD_LABELS[f]} ${badgeText(st)}`)).flatMap((b, i) => (i ? [', ', b] : [b])),
            p.error ? h('span', { class: 'error-text small' }, ` — ${p.error}`) : null);
        })))
      : '');
  }

  renderResult(project) {
    if (!project.final_url) {
      this.finalUrl = null;
      this.result.replaceChildren();
      return;
    }
    // Chỉ đổi src khi video mới được tạo → không tua lại video người dùng đang xem
    if (project.final_url !== this.finalUrl) {
      this.finalUrl = project.final_url;
      this.video.src = project.final_url;
      this.downloadLink.href = project.final_url;
      this.downloadLink.setAttribute('download', `${project.name}.mp4`);
    }
    if (project.srt_url) {
      this.srtLink.href = project.srt_url;
      this.srtLink.setAttribute('download', `${project.name}.srt`);
    }
    this.result.replaceChildren(
      project.final_status === 'stale'
        ? h('div', { class: 'notice notice-warn' },
          'Các cảnh đã thay đổi sau lần xuất này. Video bên dưới là bản cũ — bấm "Xuất lại video" để cập nhật.')
        : '',
      this.video,
      h('div', { class: 'row' },
        this.downloadLink,
        project.srt_url ? this.srtLink : '',
        project.final_duration_sec ? h('span', { class: 'muted small' }, `Thời lượng ${fmtSec(project.final_duration_sec)}`) : ''));
  }

  /** Bật/tắt một tuỳ chọn xuất video của dự án (phụ đề, tiêu đề) → video đã xuất thành "cũ". */
  async toggleOption(name, box) {
    await guarded(box, async () => {
      this.ctx.setProject(await this.ctx.api.updateProject(this.project.id, { [name]: box.checked }));
    });
  }

  async export() {
    await guarded(this.exportBtn, async () => {
      await this.ctx.api.exportVideo(this.project.id);
      await this.ctx.refresh();
    });
  }
}

function stat(label, value) {
  return h('div', { class: 'stat card' }, h('div', { class: 'stat-value' }, value), h('div', { class: 'muted small' }, label));
}

function badgeText(status) {
  return { missing: 'chưa có', stale: 'cần tạo lại', error: 'bị lỗi' }[status] || status;
}
