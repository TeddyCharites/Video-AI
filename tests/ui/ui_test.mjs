// Test giao diện: chạy app.js THẬT trong jsdom (DOM giả lập), thao tác như người dùng
// (gõ, bấm, chuyển bước), kiểm tra cả DOM lẫn dữ liệu trên server.
//
// Chạy:   cd tests\ui  →  npm install (lần đầu)  →  npm test
// Tự khởi động tests/ui/test_server.py (dữ liệu tạm, ảnh giả, TTS thật) rồi tự tắt khi xong.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import { JSDOM, VirtualConsole } from 'jsdom';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const PORT = 8765;
const BASE = `http://127.0.0.1:${PORT}`;
const realFetch = globalThis.fetch;

/** File WAV thử (mono 16-bit 22050Hz, sóng sin 440Hz) — đủ để server/ffprobe nhận là âm thanh. */
function makeWav(seconds) {
  const rate = 22050, n = Math.round(rate * seconds);
  const buf = Buffer.alloc(44 + n * 2);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVE', 8);
  buf.write('fmt ', 12); buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34);
  buf.write('data', 36); buf.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) buf.writeInt16LE(Math.round(8000 * Math.sin(2 * Math.PI * 440 * i / rate)), 44 + i * 2);
  return buf;
}

// ── Khởi động server test, đợi dòng "READY <project_id>" ──
const python = path.join(ROOT, '.venv', 'Scripts', 'python.exe');
const server = spawn(python, [path.join(HERE, 'test_server.py'), String(PORT)], {
  cwd: ROOT, env: { ...process.env, PYTHONUTF8: '1' },
});
// Windows: kill() tắt cưỡng bức → "finally" dọn dẹp của test_server.py không chạy → tự dọn ở đây
const stopServer = () => {
  try { server.kill(); } catch {}
  const testsDir = path.join(ROOT, 'outputs', 'tests');
  for (const name of fs.existsSync(testsDir) ? fs.readdirSync(testsDir) : []) {
    if (name.startsWith('ui_')) {
      try { fs.rmSync(path.join(testsDir, name), { recursive: true, force: true, maxRetries: 10, retryDelay: 300 }); } catch {}
    }
  }
};
process.on('exit', stopServer);
let serverLog = '';
server.stderr.on('data', d => { serverLog += d; });
const PID = await new Promise((resolve, reject) => {
  server.stdout.on('data', d => {
    serverLog += d;
    const m = String(d).match(/READY (\S+)/);
    if (m) resolve(m[1]);
  });
  server.on('exit', code => reject(new Error(`server test thoát (mã ${code}):\n${serverLog.slice(-2000)}`)));
});
for (let i = 0; i < 50; i++) {  // đợi uvicorn mở cổng
  try { await realFetch(BASE + '/api/settings'); break; } catch { await new Promise(r => setTimeout(r, 200)); }
}
console.log(`Server test sẵn sàng, dự án mẫu ${PID}`);

const api = async (path, opts) => (await realFetch(BASE + path, opts)).json();

const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', e => { if (!/Not implemented/.test(e.message)) errors.push('jsdomError: ' + e.message); });
vc.on('error', (...a) => errors.push('console.error: ' + a.join(' ')));
process.on('unhandledRejection', e => errors.push('unhandledRejection: ' + (e?.stack || e)));

const html = await (await realFetch(BASE + '/')).text();
const dom = new JSDOM(html, { url: BASE + '/#/', pretendToBeVisual: true, virtualConsole: vc });
const w = dom.window;
w.Element.prototype.scrollIntoView = () => {};
w.HTMLMediaElement.prototype.load = () => {};
w.CSS = { escape: s => String(s).replace(/["\\]/g, '\\$&') };
let confirmAnswer = true;
const confirms = [];
w.confirm = msg => { confirms.push(msg); return confirmAnswer; };
Object.assign(globalThis, {
  window: w, document: w.document, location: w.location, Node: w.Node, CSS: w.CSS,
  requestAnimationFrame: w.requestAnimationFrame.bind(w), confirm: w.confirm,
  fetch: (url, opts) => realFetch(new URL(url, BASE), opts),
});
w.addEventListener('error', e => errors.push('window error: ' + e.message));

const $ = sel => w.document.querySelector(sel);
const $$ = sel => [...w.document.querySelectorAll(sel)];
const text = el => el?.textContent.replace(/\s+/g, ' ').trim();
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, msg, timeout = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    try { if (await fn()) return; } catch {}
    await sleep(100);
  }
  throw new Error('TIMEOUT: ' + msg);
}
let passed = 0;
function ok(cond, msg) {
  if (!cond) throw new Error('FAIL: ' + msg);
  passed++;
  console.log('  OK ', msg);
}
function type(el, value) {
  el.focus();
  el.value = value;
  el.dispatchEvent(new w.Event('input', { bubbles: true }));
}
const click = el => el.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
const go = hash => { w.location.hash = hash; };
const toasts = () => $$('.toast').map(text);
const btnByText = (t, root = w.document) => [...root.querySelectorAll('button')].find(b => text(b).includes(t));

await import(pathToFileURL(path.join(ROOT, 'app', 'static', 'js', 'app.js')).href);

try {
  const settings = await api('/api/settings');
  const proj = () => api(`/api/projects/${PID}`);

  console.log('1. Danh sách dự án');
  await until(() => $$('.project-card').length === 1, 'thẻ dự án');
  const card = text($('.project-card'));
  ok(card.includes('Tiếng Việt + English') && card.includes('VI: đã xuất') && card.includes('EN: đã xuất'),
     'thẻ dự án: 2 ngôn ngữ, cả 2 đã xuất');
  ok($('form.form').hidden, 'form dự án mới đang ẩn');

  console.log('2. Bước 1 — Kịch bản + bản dịch');
  go(`#/p/${PID}/1`);
  await until(() => $$('.scene-row').length === 3, '3 hàng cảnh');
  const steps = () => $$('.step').map(text);
  ok(steps()[0].includes('3 cảnh · dịch 3/3') && steps()[2].includes('vi 3/3 · en 3/3') && steps()[3].includes('Đã xuất'),
     `thanh bước: ${steps().join(' | ')}`);
  const row = i => $$('.scene-row')[i];
  const areas = i => row(i).querySelectorAll('textarea');  // [lời gốc, bản dịch en, mô tả ảnh]
  ok(areas(0).length === 3 && text(row(0)).includes('Bản dịch — English') && text(row(0)).includes('Đã dịch')
     && areas(0)[1].value.startsWith('[en]'), 'mỗi cảnh: lời gốc + bản dịch English (Đã dịch)');

  type(areas(0)[0], areas(0)[0].value + ' Thêm một câu.');
  ok(text($('.dirty-label')).includes('chưa lưu') && btnByText('Dịch tất cả').disabled,
     'sửa lời gốc → "chưa lưu", nút Dịch bị khoá tới khi lưu');
  confirmAnswer = false;
  go(`#/p/${PID}/2`);
  await sleep(300);
  ok(confirms.at(-1)?.includes('chưa lưu') && w.location.hash.endsWith('/1'), 'rời trang khi chưa lưu → hỏi, chọn ở lại');
  confirmAnswer = true;
  type(areas(1)[1], 'Green trees need light to grow.');  // sửa tay bản dịch cảnh 2
  click(btnByText('Lưu thay đổi'));
  await until(() => toasts().some(t => t.includes('Đã lưu danh sách cảnh')), 'toast đã lưu');
  let p = await proj();
  ok(p.scenes[0].langs.en.text_status === 'stale' && p.scenes[0].langs.en.audio_status === 'stale'
     && p.scenes[0].langs.vi.audio_status === 'stale', 'server: sửa lời vi → audio vi + bản dịch + audio en stale');
  ok(p.scenes[1].langs.en.edited && p.scenes[1].langs.en.audio_status === 'stale' && p.scenes[1].langs.vi.audio_status === 'ready',
     'server: sửa bản dịch cảnh 2 → "đã sửa tay", chỉ audio en stale');
  await until(() => text(row(0)).includes('Lời gốc đã đổi') && btnByText('Giữ bản dịch', row(0)), 'badge bản dịch cũ');
  ok(text(row(1)).includes('đã sửa tay'), 'cảnh 2 hiện nhãn "✎ đã sửa tay"');
  click(btnByText('Giữ bản dịch', row(0)));
  await until(async () => (await proj()).scenes[0].langs.en.text_status === 'ready', 'giữ bản dịch', 5000);
  ok(true, 'bấm "Giữ bản dịch" → bản dịch cảnh 1 ready lại');

  click(btnByText('+ Thêm cảnh'));
  type($$('.scene-row')[3].querySelector('textarea'), 'Cảnh cuối cùng để kết thúc.');
  click(btnByText('Lưu thay đổi'));
  await until(async () => (await proj()).scenes.length === 4, 'lưu cảnh mới');
  await until(() => text($$('.step')[0]).includes('dịch 3/4') && btnByText('Dịch tất cả (1)'), 'nút Dịch tất cả (1)', 5000);
  ok(true, 'thêm cảnh → "dịch 3/4", nút "🌐 Dịch tất cả (1)"');
  click(btnByText('Dịch tất cả'));
  await until(() => text($$('.step')[0]).includes('dịch 4/4'), 'dịch xong', 30000);
  p = await proj();
  ok(p.scenes[3].langs.en.narration.startsWith('[en]') && p.scenes[1].langs.en.narration === 'Green trees need light to grow.',
     '"Dịch tất cả": dịch cảnh mới, KHÔNG ghi đè bản đã sửa tay');

  console.log('3. Bấm x/N ở bước 3 → nhảy tới cảnh + đúng tab ngôn ngữ');
  click($$('.step')[2].querySelector('.step-status'));
  await until(() => w.location.hash.endsWith('/3') && $$('.audio-row').length === 4, 'sang bước 3');
  await sleep(150);
  const flashed = $('.audio-row.flash');
  ok(flashed && $('.lang-tab.active')?.dataset.lang === 'vi', `nháy sáng cảnh ${flashed?.dataset.sceneId} ở tab Tiếng Việt`);

  console.log('4. Bước 2 — Storyboard (ảnh dùng chung)');
  go(`#/p/${PID}/2`);
  await until(() => $$('.scene-card').length === 4, '4 thẻ');
  const cards = $$('.scene-card');
  ok(text(cards[3]).includes('Cảnh cuối cùng'), 'thẻ hiện lời đọc ngôn ngữ gốc');
  const prompt2 = cards[1].querySelector('textarea');
  type(prompt2, 'a big yellow sun over green leaves');
  prompt2.dispatchEvent(new w.Event('blur'));
  await until(async () => (await proj()).scenes[1].image_status === 'stale', 'prompt tự lưu', 5000);
  p = await proj();
  ok(p.scenes[1].langs.vi.clip_status === 'stale' && p.scenes[1].langs.en.clip_status === 'stale',
     'sửa mô tả ảnh → clip MỌI ngôn ngữ stale');
  click(btnByText('Tạo các ảnh còn thiếu'));
  await until(() => text($$('.step')[1]).includes('4/4 ảnh'), '4/4 ảnh', 30000);
  ok(true, 'tạo ảnh còn thiếu → 4/4');

  console.log('5. Bước 3 — Giọng đọc theo tab ngôn ngữ');
  go(`#/p/${PID}/3`);
  await until(() => $$('.audio-row').length === 4 && $$('.lang-tab').length === 2, 'bước 3 có 2 tab');
  ok(text($$('.lang-tab')[0]).startsWith('Tiếng Việt') && text($$('.lang-tab')[1]).startsWith('English'), 'tab: Tiếng Việt / English');
  click(btnByText('Tạo audio Tiếng Việt còn thiếu'));
  await until(() => !$('#job-panel').hidden, 'panel tác vụ', 5000);
  // Lỗi người dùng từng gặp: polling mỗi giây vẽ lại thanh bước → bấm "không ăn". Phần tử phải giữ nguyên.
  const stepLink = $$('.step')[3], cancelBtn = $('#job-panel .job button');
  await sleep(2500);
  ok(stepLink.isConnected && cancelBtn.isConnected && $$('.step')[3] === stepLink,
     'đang polling: thanh bước và nút Huỷ giữ nguyên phần tử (không mất cú bấm)');
  await until(() => text($$('.lang-tab')[0]).includes('4/4'), 'audio vi 4/4', 120000);
  ok(true, 'tạo audio Tiếng Việt → 4/4');

  click($$('.lang-tab')[1]);  // sang tab English
  await until(() => $('.lang-tab.active')?.dataset.lang === 'en' && $$('.audio-row textarea')[3].value.startsWith('[en]'),
              'tab English', 5000);
  await until(() => [...$('.section-head select').options].some(o => o.value === 'am_michael'), 'giọng Kokoro', 5000);
  ok($('.section-head select').value === 'am_michael', 'tab English: lời đọc = bản dịch, giọng Kokoro am_michael');
  click(btnByText('Tạo audio English còn thiếu'));
  await until(() => text($$('.lang-tab')[1]).includes('4/4'), 'audio en 4/4', 120000);
  ok(text($$('.step')[2]).includes('vi 4/4 · en 4/4'), 'thanh bước: vi 4/4 · en 4/4');

  const sel = $('.section-head select');
  sel.value = 'af_heart';
  sel.dispatchEvent(new w.Event('change'));
  await until(() => text($$('.lang-tab')[1]).includes('0/4'), 'đổi giọng en', 5000);
  ok(confirms.at(-1).includes('Đổi giọng English') && text($$('.lang-tab')[0]).includes('4/4'),
     'đổi giọng English (có hỏi) → audio en 0/4, vi vẫn 4/4');

  console.log('6. Bước 4 — Xuất theo ngôn ngữ');
  go(`#/p/${PID}/4`);
  await until(() => $$('.summary-table tbody tr').length === 4, 'bảng 4 cảnh');
  const langBox = l => $$('.export-box label.checkbox').find(x => text(x) === (l === 'vi' ? 'Tiếng Việt' : 'English'))?.querySelector('input');
  ok(langBox('vi')?.checked && langBox('en')?.checked, 'ô chọn ngôn ngữ xuất: Tiếng Việt + English');
  ok(text($('.notice-warn')).includes('(English): audio cần làm lại') && btnByText('Xuất lại').disabled,
     'chọn cả 2: báo English thiếu audio, nút xuất khoá');
  langBox('en').checked = false;
  langBox('en').dispatchEvent(new w.Event('change'));
  await until(() => !$('.notice-warn') || !text($('.notice-warn')).includes('English'), 'bỏ chọn English', 5000);
  ok(!btnByText('Xuất lại').disabled, 'chỉ chọn Tiếng Việt → hết cảnh báo, nút xuất bật');
  const checkbox = label => $$('.export-box label.checkbox').find(l => text(l).includes(label))?.querySelector('input');
  const bi = checkbox('song ngữ');
  bi.checked = true;
  bi.dispatchEvent(new w.Event('change'));
  await until(async () => (await proj()).settings.subtitles_bilingual === true, 'bật song ngữ', 5000);
  ok(true, 'bật "Phụ đề song ngữ" → lưu vào dự án');
  const oldVideo = $('.result-card[data-lang="vi"] video').src;
  click(btnByText('Xuất lại'));
  await until(() => $('.export-progress'), 'tiến độ xuất', 5000);
  await until(async () => (await proj()).finals.vi.status === 'ready', 'xuất vi xong', 180000);
  await until(() => $('.result-card[data-lang="vi"] video').src !== oldVideo, 'video vi mới', 5000);
  p = await proj();
  ok(p.finals.vi.bilingual === 'en' && $('.result-card[data-lang="vi"] a[href*="final.vi-en.srt"]'),
     'video vi mới + link "Phụ đề song ngữ" (final.vi-en.srt)');
  ok(p.finals.en.status === 'stale' && text($('.result-card[data-lang="en"]')).includes('bản cũ'),
     'video English vẫn hiện, kèm cảnh báo "bản cũ"');

  langBox('en').checked = true;
  langBox('en').dispatchEvent(new w.Event('change'));
  await until(() => $('.notice-warn button.link'), 'cảnh báo en', 5000);
  click($('.notice-warn button.link'));
  await until(() => w.location.hash.endsWith('/3') && $('.lang-tab.active')?.dataset.lang === 'en', 'link → bước 3 tab en', 5000);
  ok(true, 'bấm "audio cần làm lại" của English → bước 3, tab English');

  console.log('6b. Nhạc nền');
  go(`#/p/${PID}/4`);
  await until(() => $('.music-box'), 'khung nhạc', 5000);
  ok(text($('.music-box')).includes('Chưa có nhạc nền') && $('.music-controls').hidden, 'khung nhạc nền: chưa có nhạc');
  const form = new FormData();
  form.append('file', new Blob([makeWav(3)], { type: 'audio/wav' }), 'Nhạc thử.wav');
  await realFetch(`${BASE}/api/projects/${PID}/music`, { method: 'POST', body: form });
  await until(() => !$('.music-controls').hidden, 'giao diện nhận nhạc mới', 20000);
  ok(text($('.music-box')).includes('Nhạc thử.wav') && $('input.volume').value === '15', 'có nhạc: tên bài, âm lượng 15%');
  const vol = $('input.volume');
  vol.value = '30';
  vol.dispatchEvent(new w.Event('input'));
  vol.dispatchEvent(new w.Event('change'));
  await until(async () => (await proj()).settings.music.volume === 0.3, 'lưu âm lượng', 5000);
  ok(true, 'kéo thanh âm lượng → lưu 30%');
  click(btnByText('Xoá nhạc', $('.music-box')));
  await until(() => $('.music-controls').hidden, 'xoá nhạc', 5000);
  ok((await proj()).music_url === null, 'xoá nhạc → về "chưa có nhạc"');

  console.log('6c. Shorts từ video dài');
  await until(() => $('.shorts-box'), 'khung Shorts', 5000);
  click(btnByText('Gợi ý bằng AI', $('.shorts-box')));
  await until(() => $$('.short-suggestion').length > 0, 'thẻ gợi ý Short', 30000);
  const sug = $('.short-suggestion');
  ok(sug.querySelector('input').value.startsWith('[vi] Short') && text(sug).includes('Từ'),
     `${$$('.short-suggestion').length} gợi ý: tiêu đề sửa được, chọn đoạn cảnh`);
  const [fromSel, toSel] = sug.querySelectorAll('select');
  fromSel.value = '0'; fromSel.dispatchEvent(new w.Event('change'));
  toSel.value = '1'; toSel.dispatchEvent(new w.Event('change'));
  ok(text(sug).includes('2 cảnh ·'), 'đổi "Từ cảnh 1 đến cảnh 2" → cập nhật số cảnh + thời lượng');
  type(sug.querySelector('input'), 'Vì sao lá xanh?');
  click(btnByText('Tạo Short', sug));
  await until(() => $('.short-banner') && $('.short-texts'), 'sang Short mới (bước 4)', 10000);
  const cid = decodeURIComponent(w.location.hash.match(/#\/p\/([^/]+)/)[1]);
  ok(cid !== PID && text($('.short-banner')).includes('Short của «UI test»') && text($('.short-banner')).includes('đang khớp'),
     'Short mới: băng "✂ Short của «UI test» — đang khớp với dự án gốc"');
  ok($('.short-texts input').value === 'Vì sao lá xanh?' && $('.result-card.vertical') && !btnByText('Màn hình tiêu đề'),
     'bước 4 của Short: ô tiêu đề + câu kết, khung video dọc');
  let c = await api(`/api/projects/${cid}`);
  ok(c.scenes.length === 2 && c.scenes.every(s => s.langs.vi.audio_status === 'ready' && s.image_status === 'ready'),
     'Short dùng lại ảnh + audio của cảnh 1–2');
  type($('.short-texts textarea'), 'Xem video đầy đủ trên kênh nhé!');
  click(btnByText('Lưu', $('.short-texts')));
  await until(async () => (await api(`/api/projects/${cid}`)).outro.vi.text === 'Xem video đầy đủ trên kênh nhé!', 'lưu câu kết', 5000);
  ok(true, 'sửa câu kết → lưu vào Short');

  await realFetch(`${BASE}/api/projects/${PID}/scenes/01`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image_prompt: 'a single green leaf in sunlight' }) });
  go(`#/p/${encodeURIComponent(cid)}/1`);  // chuyển bước → tải lại dữ liệu (khi rảnh, polling 15s/lần)
  await until(() => text($('.short-banner')).includes('dự án gốc đã đổi') && !btnByText('Cập nhật từ dự án gốc').hidden,
              'băng báo dự án gốc đã đổi', 10000);
  ok(text($('.short-banner')).includes('cảnh 1 (ảnh)'), 'sửa ảnh cảnh 1 ở dự án gốc → Short báo "cảnh 1 (ảnh)"');
  click(btnByText('Cập nhật từ dự án gốc'));
  await until(() => toasts().some(t => t.includes('Đã cập nhật 1 cảnh')), 'đồng bộ', 5000);
  c = await api(`/api/projects/${cid}`);
  ok(confirms.at(-1).includes('Chỉnh sửa trong Short') && c.scenes[0].image_prompt === 'a single green leaf in sunlight'
     && text($('.short-banner')).includes('đang khớp'), 'bấm "Cập nhật từ dự án gốc" (có hỏi) → chép cảnh 1 mới');

  go(`#/p/${encodeURIComponent(cid)}/1`);
  await until(() => $$('.scene-row').length === 2, 'bước 1 của Short');
  ok(!btnByText('Chia cảnh bằng AI') && text($('.step-content .card')).includes('Short lấy cảnh từ dự án gốc'),
     'bước 1 của Short: không có kịch bản / chia cảnh');
  click($('.short-banner a'));
  await until(() => w.location.hash.startsWith(`#/p/${PID}/4`) && $$('.short-row').length === 1, 'về dự án gốc', 10000);
  ok(text($('.short-row')).includes('Vì sao lá xanh?') && text($('.short-row')).includes('cảnh 1–2'),
     'dự án gốc liệt kê Short: "Vì sao lá xanh?" (cảnh 1–2)');

  console.log('7. Tạo dự án mới (vi + en) từ form');
  go('#/');
  await until(() => $$('.project-card').length === 2, 'danh sách');
  ok($$('.short-children .project-card').length === 1 && text($('.short-children')).includes('✂ Short'),
     'danh sách dự án: Short lồng dưới dự án gốc, có nhãn "✂ Short"');
  click(btnByText('+ Dự án mới'));
  const enBox = $$('form.form label.checkbox').find(l => text(l) === 'English')?.querySelector('input');
  ok(!$('form.form').hidden && $('form.form select').value === 'vi' && enBox?.checked,
     'form: ngôn ngữ kịch bản Tiếng Việt, mặc định làm thêm bản English');
  type($('form.form input'), 'Dự án thử giao diện');
  type($('form.form textarea'), 'Đoạn một.\n\nĐoạn hai.\n\nĐoạn ba.');
  $('form.form').dispatchEvent(new w.Event('submit', { cancelable: true }));
  await until(() => /#\/p\/.+\/1$/.test(w.location.hash) && $('.scene-list .card'), 'vào bước 1 dự án mới', 10000);
  ok(text($$('.step')[0]).includes('Chưa chia cảnh'), 'dự án mới: "Chưa chia cảnh"');
  click(btnByText('Chia theo đoạn văn'));
  await until(() => $$('.scene-row').length === 3, '3 cảnh sau khi chia', 10000);
  ok(text($$('.step')[0]).includes('3 cảnh · dịch 0/3') && text($$('.scene-row')[0]).includes('Chưa dịch'),
     'chia xong: "dịch 0/3", bản dịch "Chưa dịch"');

  await sleep(500);
  ok(errors.length === 0, 'không có lỗi JS nào' + (errors.length ? ':\n' + errors.join('\n') : ''));
  console.log(`\nTẤT CẢ ĐỀU QUA (${passed} kiểm tra)`);
  process.exit(0);
} catch (e) {
  console.log('\n' + e.message);
  if (errors.length) console.log('Lỗi JS:\n' + errors.join('\n'));
  console.log('Toasts:', toasts());
  process.exit(1);
}
