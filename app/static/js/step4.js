// Bước 4 — Xuất video: chọn ngôn ngữ cần xuất (một hoặc nhiều), phụ đề thường / song ngữ, nhạc nền;
// bảng tổng hợp từng cảnh, cảnh báo cảnh thiếu/lỗi, tiến độ, xem & tải video + phụ đề của từng ngôn ngữ.

import { h, badge, fmtSec, guarded, activeJob, focusScene, toast, langName } from './ui.js';
import { ShortsPanel } from './shorts.js';

const FIELD_LABELS = { image_status: 'ảnh', text_status: 'bản dịch', audio_status: 'audio', clip_status: 'clip' };
const FIELD_STEP = { image_status: 2, text_status: 1, audio_status: 3 };

export class Step4 {
  constructor(ctx) {
    this.ctx = ctx;
    this.exportLangs = null;   // ngôn ngữ đang chọn để xuất (mặc định: tất cả)
    this.results = new Map();  // lang → khung kết quả (tạo một lần: giữ <video> khi polling)
  }

  isDirty() { return false; }

  focusScene(id) { return focusScene(this.root, id); }

  mount(root) {
    this.root = root;
    const project = this.ctx.project;
    const { settings } = this.ctx;
    this.exportLangs = new Set(project.languages);
    this.stats = h('div', { class: 'stats' });
    this.problems = h('div', {});
    this.exportBtn = h('button', { class: 'btn btn-primary btn-large', onclick: () => this.export() }, 'Xuất video');
    this.langBoxes = project.languages.map(l => {
      const box = h('input', { type: 'checkbox', checked: true, value: l,
        onchange: () => { box.checked ? this.exportLangs.add(l) : this.exportLangs.delete(l); this.update(this.project); } });
      return h('label', { class: 'inline checkbox' }, box, langName(settings, l));
    });
    this.progress = h('div', {});
    this.resultsBox = h('div', { class: 'results' });
    this.subtitlesBox = h('input', { type: 'checkbox', onchange: () => this.toggleOption('subtitles', this.subtitlesBox) });
    this.bilingualBox = h('input', { type: 'checkbox', onchange: () => this.toggleOption('subtitles_bilingual', this.bilingualBox) });
    this.titleBox = h('input', { type: 'checkbox', onchange: () => this.toggleOption('title', this.titleBox) });
    const remotion = settings.render_engine === 'remotion';
    const isShort = project.kind === 'short';
    const multi = project.languages.length > 1;
    this.tableHead = h('thead', {});
    this.table = h('tbody', {});
    this.musicBox = this.createMusicBox();

    root.replaceChildren(
      h('div', { class: 'section-head' }, h('h2', {}, 'Xuất video')),
      this.stats,
      this.problems,
      h('div', { class: 'card export-box' },
        h('div', { class: 'row wrap' }, this.exportBtn,
          multi ? h('div', { class: 'row wrap' }, h('span', { class: 'small muted' }, 'Ngôn ngữ:'), ...this.langBoxes) : null,
          h('span', { class: 'grow' }),
          remotion && !isShort ? h('label', { class: 'inline checkbox' }, this.titleBox, 'Màn hình tiêu đề') : null,
          h('label', { class: 'inline checkbox' }, this.subtitlesBox, 'Chèn phụ đề'),
          multi ? h('label', { class: 'inline checkbox', title: 'Phụ đề 2 dòng: ngôn ngữ đang đọc + ngôn ngữ kia' },
            this.bilingualBox, 'Phụ đề song ngữ') : null),
        h('p', { class: 'muted small' }, isShort
          ? 'Short dọc 9:16: tiêu đề phía trên, clip vẽ bảng ở giữa, phụ đề lớn, cuối video là câu kết dẫn về video đầy đủ. Cần Remotion.'
          : remotion
          ? 'Tạo animation vẽ bảng cho các cảnh chưa có clip, rồi Remotion ghép tiêu đề, chuyển cảnh, giọng đọc, phụ đề và nhạc nền. Mỗi ngôn ngữ là một video riêng.'
          : 'Tạo animation vẽ bảng cho các cảnh chưa có clip, rồi ghép cùng giọng đọc, phụ đề và nhạc nền. Mỗi ngôn ngữ là một video riêng.'),
        this.progress),
      this.resultsBox,
      isShort ? this.createShortBox() : null,
      this.musicBox,
      h('div', { class: 'card table-wrap' }, h('table', { class: 'summary-table' }, this.tableHead, this.table)));
    if (!isShort) {  // dự án dài: khung gợi ý / danh sách Short
      this.shortsPanel = new ShortsPanel(this.ctx);
      this.shortsPanel.mount(root);
    }
  }

  update(project) {
    this.project = project;
    const { settings } = this.ctx;
    const scenes = project.scenes;
    const job = activeJob(project, 'export');
    const s = project.summary;
    const chosen = project.languages.filter(l => this.exportLangs.has(l));

    this.stats.replaceChildren(
      stat('Số cảnh', scenes.length),
      stat('Ảnh', `${s.images.ready}/${s.images.total}`),
      ...project.languages.map(l => stat(`Audio ${langName(settings, l)}`,
        `${s.langs[l].audio.ready}/${s.langs[l].audio.total}`
        + (s.langs[l].total_duration_sec ? ` · ${fmtSec(s.langs[l].total_duration_sec)}` : ''))));

    // Cảnh thiếu/lỗi của các ngôn ngữ ĐANG CHỌN: liệt kê rõ cảnh nào, thiếu gì, kèm nút nhảy tới đúng bước.
    // Chỉ vẽ lại khi danh sách đổi — vẽ lại mỗi lần polling sẽ thay các nút đang được bấm (mất click).
    const problems = chosen.flatMap(l => project.export_problems[l]);
    const problemsSig = JSON.stringify([problems, scenes.map(sc => sc.id)]);
    if (problemsSig !== this.problemsSig) {
      this.problemsSig = problemsSig;
      this.renderProblems(problems, scenes);
    }

    const anyFinal = project.languages.some(l => project.finals[l]?.url);
    this.exportBtn.disabled = Boolean(job) || problems.length > 0 || !scenes.length || !chosen.length;
    this.exportBtn.textContent = job ? 'Đang xuất…'
      : `${anyFinal ? 'Xuất lại' : 'Xuất'} ${chosen.length > 1 ? `${chosen.length} video` : 'video'}`;
    for (const label of this.langBoxes) label.querySelector('input').disabled = Boolean(job);
    for (const [box, key, def] of [[this.subtitlesBox, 'subtitles', settings.default_subtitles],
                                   [this.titleBox, 'title', settings.default_title],
                                   [this.bilingualBox, 'subtitles_bilingual', false]]) {
      box.checked = project.settings[key] ?? def;
      box.disabled = Boolean(job);
    }

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

    for (const l of project.languages) this.renderResult(project, l);
    this.updateMusicBox(project, job);
    this.renderTable(project);
    this.shortsPanel?.update(project);
    if (this.shortBox) this.updateShortBox(project, job);
  }

  renderTable(project) {
    const langs = project.languages;
    const multi = langs.length > 1;
    const sig = langs.join(',');
    if (this.headSig !== sig) {  // tiêu đề bảng: cột audio/clip cho từng ngôn ngữ
      this.headSig = sig;
      this.tableHead.replaceChildren(h('tr', {},
        h('th', {}, '#'), h('th', {}, 'Lời đọc'), h('th', {}, 'Ảnh'),
        ...langs.flatMap(l => {
          const suffix = multi ? ` ${l}` : '';
          return [h('th', {}, `Thời lượng${suffix}`), h('th', {}, `Audio${suffix}`), h('th', {}, `Clip${suffix}`)];
        })));
    }
    const source = project.source_lang;
    this.table.replaceChildren(...project.scenes.map((sc, i) => h('tr', { dataset: { sceneId: sc.id } },
      h('td', {}, i + 1),
      h('td', { class: 'narration-cell', title: sc.langs[source].narration }, sc.langs[source].narration),
      h('td', {}, badge(sc.image_status)),
      ...langs.flatMap(l => {
        const e = sc.langs[l];
        return [h('td', { class: 'nowrap' }, e.duration_sec ? fmtSec(e.duration_sec) : '—'),
                h('td', {}, badge(e.audio_status)), h('td', {}, badge(e.clip_status))];
      }))));
  }

  // ── Kết quả từng ngôn ngữ ──────────────────────────────────────────────────
  renderResult(project, lang) {
    const final = project.finals[lang] || {};
    let r = this.results.get(lang);
    if (!r) {
      r = {
        title: h('h3', {}),
        notice: h('div', {}),
        video: h('video', { controls: true, preload: 'metadata', class: 'final-video' }),
        download: h('a', { class: 'btn', download: true }, '⬇ Tải video'),
        srt: h('a', { class: 'btn btn-ghost', download: true }, '⬇ Phụ đề (.srt)'),
        srtBi: h('a', { class: 'btn btn-ghost', download: true }, '⬇ Phụ đề song ngữ'),
        info: h('span', { class: 'muted small' }),
        url: null,
      };
      r.el = h('div', { class: `card result-card${project.kind === 'short' ? ' vertical' : ''}`, dataset: { lang } },
        h('div', { class: 'row' }, r.title, h('span', { class: 'grow' }), r.info),
        r.notice, r.video, h('div', { class: 'row wrap' }, r.download, r.srt, r.srtBi));
      this.results.set(lang, r);
      this.resultsBox.append(r.el);
    }
    r.el.hidden = !final.url;
    if (!final.url) return;
    const { settings } = this.ctx;
    const name = langName(settings, lang);
    r.title.textContent = `🎬 ${name}`;
    // Chỉ đổi src khi video mới được tạo → không tua lại video người dùng đang xem
    if (final.url !== r.url) {
      r.url = final.url;
      r.video.src = final.url;
      r.download.href = final.url;
      r.download.setAttribute('download', `${project.name} (${lang}).mp4`);
    }
    r.srt.hidden = !final.srt_url;
    if (final.srt_url) {
      r.srt.href = final.srt_url;
      r.srt.setAttribute('download', `${project.name} (${lang}).srt`);
    }
    r.srtBi.hidden = !final.srt_bilingual_url;
    if (final.srt_bilingual_url) {
      r.srtBi.href = final.srt_bilingual_url;
      r.srtBi.setAttribute('download', `${project.name} (${lang}-${final.bilingual}).srt`);
    }
    r.info.textContent = final.duration_sec ? `Thời lượng ${fmtSec(final.duration_sec)}` : '';
    r.notice.replaceChildren(final.status === 'stale'
      ? h('div', { class: 'notice notice-warn' },
        `Dự án đã thay đổi sau lần xuất này. Video ${name} bên dưới là bản cũ — xuất lại để cập nhật.`)
      : '');
  }

  // ── Short: tiêu đề + câu kết theo từng ngôn ngữ ─────────────────────────────
  createShortBox() {
    const { settings } = this.ctx;
    this.shortFields = this.project?.languages ?? this.ctx.project.languages;
    this.shortInputs = this.shortFields.map(lang => ({
      lang,
      title: h('input', { type: 'text', maxlength: 100 }),
      cta: h('textarea', { rows: 2, maxlength: 200 }),
      audio: h('span', { class: 'small' }),
    }));
    this.shortSaveBtn = h('button', { class: 'btn btn-primary', onclick: () => this.saveShortTexts() }, 'Lưu');
    this.shortBox = h('div', { class: 'card short-texts' },
      h('div', { class: 'row' }, h('h3', {}, '📱 Tiêu đề & câu kết'), h('span', { class: 'grow' }), this.shortSaveBtn),
      h('p', { class: 'muted small' },
        'Tiêu đề hiện suốt Short (tối đa 2 dòng). Câu kết được đọc ở màn hình cuối, dẫn người xem sang video đầy đủ. ',
        'Sửa xong → xuất lại để cập nhật video.'),
      ...this.shortInputs.map(f => h('div', { class: 'short-lang' },
        h('strong', {}, langName(settings, f.lang)),
        h('label', {}, 'Tiêu đề', f.title),
        h('label', {}, 'Câu kết', f.cta), f.audio)));
    return this.shortBox;
  }

  updateShortBox(project, job) {
    // Không ghi đè ô người dùng đang gõ: chỉ điền lại khi giá trị trên server đổi
    for (const f of this.shortInputs) {
      const title = project.titles?.[f.lang] || '';
      const cta = project.outro?.[f.lang]?.text || '';
      if (f.title.dataset.server !== title) { f.title.value = title; f.title.dataset.server = title; }
      if (f.cta.dataset.server !== cta) { f.cta.value = cta; f.cta.dataset.server = cta; }
      const o = project.outro?.[f.lang] || {};
      f.audio.textContent = o.audio_status === 'ready'
        ? `Giọng câu kết: ${fmtSec(o.duration_sec)}` : 'Giọng câu kết sẽ được tạo khi xuất video';
      f.audio.className = 'small muted';
    }
    this.shortSaveBtn.disabled = Boolean(job);
  }

  async saveShortTexts() {
    const titles = {}, cta = {};
    for (const f of this.shortInputs) {
      if (f.title.value.trim()) titles[f.lang] = f.title.value.trim();
      if (f.cta.value.trim()) cta[f.lang] = f.cta.value.trim();
    }
    await guarded(this.shortSaveBtn, async () => {
      this.ctx.setProject(await this.ctx.api.updateProject(this.project.id, { titles, cta }));
      toast('Đã lưu tiêu đề và câu kết', 'success');
    });
  }

  // ── Nhạc nền (dùng chung mọi ngôn ngữ) ────────────────────────────────────
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
      'Nhạc tự lặp cho đủ dài video, mờ dần ở đầu và cuối, dùng chung cho mọi ngôn ngữ.');
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
    const { settings } = this.ctx;
    const multi = this.project.languages.length > 1;
    this.problems.replaceChildren(problems.length
      ? h('div', { class: 'notice notice-warn' },
        h('strong', {}, `Chưa thể xuất: còn ${new Set(problems.map(p => p.id)).size} cảnh chưa sẵn sàng`),
        h('ul', {}, problems.map(p => {
          const idx = scenes.findIndex(sc => sc.id === p.id) + 1;
          return h('li', {},
            `Cảnh ${idx}${multi ? ` (${langName(settings, p.lang)})` : ''}: `,
            Object.entries(p.not_ready).map(([f, st]) =>
              h('button', { class: 'link', onclick: () => this.ctx.goToScene(FIELD_STEP[f], p.id, p.lang) },
                `${FIELD_LABELS[f]} ${badgeText(st)}`)).flatMap((b, i) => (i ? [', ', b] : [b])),
            p.error ? h('span', { class: 'error-text small' }, ` — ${p.error}`) : null);
        })))
      : '');
  }

  /** Bật/tắt một tuỳ chọn xuất video của dự án (phụ đề, song ngữ, tiêu đề) → video đã xuất thành "cũ". */
  async toggleOption(name, box) {
    await guarded(box, async () => {
      this.ctx.setProject(await this.ctx.api.updateProject(this.project.id, { [name]: box.checked }));
    });
  }

  async export() {
    const langs = this.project.languages.filter(l => this.exportLangs.has(l));
    await guarded(this.exportBtn, async () => {
      await this.ctx.api.exportVideo(this.project.id, langs);
      await this.ctx.refresh();
    });
  }
}

function stat(label, value) {
  return h('div', { class: 'stat card' }, h('div', { class: 'stat-value' }, value), h('div', { class: 'muted small' }, label));
}

function badgeText(status) {
  return { missing: 'chưa có', stale: 'cần làm lại', error: 'bị lỗi' }[status] || status;
}
