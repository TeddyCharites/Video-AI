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
  console.log('1. Danh sách dự án');
  await until(() => $$('.project-card').length === 1, 'thẻ dự án');
  ok(text($('.project-card')).includes('3 cảnh') && text($('.project-card')).includes('Đã xuất video'), 'thẻ dự án: 3 cảnh, đã xuất');
  ok($('form.form').hidden, 'form dự án mới đang ẩn');

  console.log('2. Bước 1 — Kịch bản');
  go(`#/p/${PID}/1`);
  await until(() => $$('.scene-row').length === 3, '3 hàng cảnh');
  const steps = $$('.step').map(text);
  ok(steps.length === 4 && steps[0].includes('Đã lưu · 3 cảnh') && steps[1].includes('3/3 ảnh')
     && steps[2].includes('3/3 audio') && steps[3].includes('Đã xuất'), `thanh bước: ${steps.join(' | ')}`);
  ok($('#topbar-project').textContent === 'UI test', 'tên dự án trên topbar');
  const firstNarr = $('.scene-row textarea');
  type(firstNarr, firstNarr.value + ' Thêm một câu.');
  ok(text($('.dirty-label')).includes('chưa lưu') && text($$('.step')[0]).includes('Chưa lưu'), 'gõ → "Có thay đổi chưa lưu" + thanh bước "Chưa lưu"');
  ok(!btnByText('Lưu thay đổi').disabled, 'nút Lưu bật');

  // Rời bước khi chưa lưu → hỏi xác nhận; trả lời "không" → ở lại
  confirmAnswer = false;
  go(`#/p/${PID}/2`);
  await sleep(300);
  ok(confirms.at(-1)?.includes('chưa lưu') && w.location.hash.endsWith('/1') && $$('.scene-row').length === 3,
     'rời trang khi chưa lưu → hỏi, chọn ở lại');
  confirmAnswer = true;

  // Thêm cảnh mới (rỗng) → lưu bị chặn; điền vào → lưu được
  click(btnByText('+ Thêm cảnh'));
  ok($$('.scene-row').length === 4, 'thêm cảnh → 4 hàng');
  click(btnByText('Lưu thay đổi'));
  await sleep(200);
  ok(toasts().some(t => t.includes('chưa có lời đọc')), 'lưu khi cảnh mới trống → báo lỗi');
  type($$('.scene-row')[3].querySelector('textarea'), 'Cảnh cuối cùng để kết thúc.');
  // Đổi thứ tự: đưa cảnh mới lên vị trí 3
  click($$('.scene-row')[3].querySelector('button[title="Lên trên"]'));
  ok(text($$('.scene-row')[2]).includes('Mới'), 'đổi thứ tự trong bản nháp');
  click(btnByText('Lưu thay đổi'));
  await until(() => toasts().some(t => t.includes('Đã lưu danh sách cảnh')), 'toast đã lưu');
  let p = await api(`/api/projects/${PID}`);
  ok(p.scenes.length === 4 && p.scenes[2].id === '04' && p.scenes[2].image_status === 'missing', 'server: 4 cảnh, cảnh mới id 04 ở vị trí 3');
  ok(p.scenes[0].audio_status === 'stale' && p.scenes[0].image_status === 'ready', 'server: sửa lời đọc cảnh 1 → audio stale');
  ok(p.final_status === 'stale', 'server: final.mp4 stale');
  ok(!text($('.dirty-label')) && text($$('.step')[0]).includes('Đã lưu · 4 cảnh'), 'sau lưu: hết "chưa lưu"');
  ok(text($$('.step')[1]).includes('3/4 ảnh') && text($$('.step')[2]).includes('2/4 audio'), 'thanh bước cập nhật x/N');

  console.log('3. Bấm x/N → nhảy tới cảnh thiếu');
  click($$('.step')[1].querySelector('.step-status'));
  await until(() => w.location.hash.endsWith('/2') && $$('.scene-card').length === 4, 'sang bước 2');
  await sleep(100);
  ok($('[data-scene-id="04"]').classList.contains('flash'), 'cảnh 04 (thiếu ảnh) được nháy sáng');

  console.log('4. Bước 2 — Storyboard');
  const cards = $$('.scene-card');
  ok(cards[0].querySelector('img').src.includes('/files/') && !cards[0].querySelector('.img-placeholder').textContent.includes('Lỗi'), 'thẻ 1 có ảnh');
  ok(!cards[2].querySelector('.img-placeholder').hidden && text(cards[2]).includes('Chưa có'), 'thẻ cảnh mới: "Chưa có ảnh"');
  ok(text(btnByText('Tạo các ảnh còn thiếu')).includes('(1)'), 'nút "Tạo các ảnh còn thiếu (1)"');
  // Sửa prompt cảnh 2 → tự lưu khi rời ô → ảnh stale
  const prompt2 = cards[1].querySelector('textarea');
  const oldSrc = cards[1].querySelector('img').src;
  type(prompt2, 'a big yellow sun over green leaves');
  prompt2.dispatchEvent(new w.Event('blur'));
  await until(async () => (await api(`/api/projects/${PID}`)).scenes[1].image_status === 'stale', 'prompt tự lưu → stale', 5000);
  ok(true, 'sửa prompt + rời ô → tự lưu, ảnh cảnh 2 "Cần tạo lại"');
  await until(() => text(cards[1].querySelector('.badge')).includes('Cần tạo lại'), 'badge cập nhật', 5000);
  ok(cards[1].querySelector('img').classList.contains('dim'), 'ảnh cũ bị làm mờ');
  // Tạo lại riêng cảnh 2
  click(btnByText('Tạo lại', cards[1]));
  await until(() => cards[1].querySelector('img').src !== oldSrc, 'ảnh cảnh 2 đổi URL', 20000);
  ok(text(cards[1].querySelector('.badge')).includes('Sẵn sàng'), 'tạo lại cảnh 2 → ảnh mới, "Sẵn sàng"');
  // Tạo tất cả ảnh còn thiếu
  click(btnByText('Tạo các ảnh còn thiếu'));
  await until(() => text($$('.step')[1]).includes('4/4 ảnh'), '4/4 ảnh', 20000);
  ok(btnByText('Tạo các ảnh còn thiếu').disabled, 'đủ ảnh → nút tạo bị khoá');

  console.log('5. Bước 3 — Giọng đọc');
  click($$('.step')[2]);  // bấm vào thẻ bước 3 (link)
  go(`#/p/${PID}/3`);
  await until(() => $$('.audio-row').length === 4, '4 hàng audio');
  const rows = $$('.audio-row');
  ok(rows[1].querySelector('audio').src.includes('/files/') && text(rows[1]).includes('giây'), 'hàng 2 có audio + thời lượng');
  ok(text(rows[0].querySelector('.badge')).includes('Cần tạo lại') && text(rows[2]).includes('Chưa có'), 'cảnh 1 stale, cảnh mới chưa có');
  ok($('select').value === 'Thiện Minh', 'ô chọn giọng = giọng của dự án');
  click(btnByText('Tạo audio còn thiếu'));
  await until(() => !$('#job-panel').hidden, 'panel tác vụ hiện', 5000);
  ok(text($('#job-panel')).includes('Tạo giọng đọc'), 'panel tác vụ: "Tạo giọng đọc"');
  // Lỗi người dùng gặp: đang có job → polling mỗi giây vẽ lại thanh bước → phần tử bị thay giữa lúc
  // nhấn/nhả chuột → bấm "không ăn". Phần tử phải được GIỮ NGUYÊN qua các lần polling.
  const stepLink = $$('.step')[3], cancelBtn = $('#job-panel .job button');
  await sleep(2500);  // ≥ 2 lần polling
  ok(stepLink.isConnected && cancelBtn.isConnected && $$('.step')[3] === stepLink,
     'đang polling: thanh bước và nút Huỷ giữ nguyên phần tử (không mất cú bấm)');
  click(stepLink);  // bấm THẬT vào link bước 4 (không đổi hash bằng tay)
  await until(() => w.location.hash.endsWith('/4') && $('.summary-table'), 'bấm thanh bước → bước 4', 5000);
  ok($$('.step')[3].classList.contains('active'), 'bấm thanh bước khi đang có job → sang bước 4, bước 4 sáng');
  click($$('.step')[2]);
  await until(() => w.location.hash.endsWith('/3') && $$('.audio-row').length === 4, 'quay lại bước 3', 5000);
  await until(() => text($$('.step')[2]).includes('4/4 audio'), '4/4 audio', 90000);
  await until(() => $('#job-panel').hidden, 'panel ẩn khi xong', 5000);
  ok(true, 'tạo audio xong → 4/4, panel tác vụ ẩn');
  ok(toasts().some(t => t.includes('Tạo giọng đọc: xong')), 'toast "Tạo giọng đọc: xong"');

  console.log('6. Bước 4 — Xuất video');
  go(`#/p/${PID}/4`);
  await until(() => $$('.summary-table tbody tr').length === 4, 'bảng 4 cảnh');
  ok(text($('.stats')).includes('4/4') && !$('.notice-warn')?.textContent.includes('Chưa thể xuất'), 'đủ ảnh/audio, không có cảnh báo thiếu');
  ok(text($('.export-box')).includes('bản cũ'), 'cảnh báo video hiện có là bản cũ');
  const exportBtn = btnByText('Xuất lại video');
  ok(exportBtn && !exportBtn.disabled, 'nút "Xuất lại video" bật');
  const oldVideo = $('video').src;
  click(exportBtn);
  await until(() => $('.export-progress'), 'thanh tiến độ xuất', 5000);
  ok(true, 'hiện thanh tiến độ khi xuất');
  await until(() => text($$('.step')[3]).includes('Đã xuất'), 'xuất xong', 180000);
  await until(() => $('video').src !== oldVideo, 'video mới', 5000);
  ok(!text($('.export-box')).includes('bản cũ') && $('a[download]').href.includes('final.mp4'), 'video mới, link tải, hết cảnh báo');
  // Tìm checkbox theo nhãn chữ (thứ tự các ô có thể đổi)
  const checkbox = label => $$('.export-box label.checkbox').find(l => text(l).includes(label))?.querySelector('input');
  const subBox = checkbox('phụ đề');
  ok($('a[download][href*="final.srt"]') && subBox.checked, 'có link tải phụ đề .srt, ô "Chèn phụ đề" đang bật');
  const settings = await api('/api/settings');
  if (settings.render_engine === 'remotion') {
    ok(checkbox('tiêu đề')?.checked === true, 'có Remotion → hiện ô "Màn hình tiêu đề" (đang bật)');
  } else {
    ok(!checkbox('tiêu đề'), 'không có Remotion → ẩn ô "Màn hình tiêu đề"');
  }
  // Tắt phụ đề → video đã xuất thành "cũ" (cần xuất lại)
  subBox.checked = false;
  subBox.dispatchEvent(new w.Event('change'));
  await until(() => text($$('.step')[3]).includes('Video cũ'), 'tắt phụ đề → video cũ', 5000);
  ok((await api(`/api/projects/${PID}`)).settings.subtitles === false, 'tắt phụ đề → lưu vào dự án, bước 4 báo "Video cũ"');

  console.log('6b. Nhạc nền');
  const musicBox = $('.music-box');
  ok(musicBox && text(musicBox).includes('Chưa có nhạc nền') && $('.music-controls').hidden, 'khung nhạc nền: chưa có nhạc');
  // jsdom không chọn file thật được → tải lên qua API như trình duyệt sẽ gửi (multipart), rồi kiểm tra giao diện
  const form = new FormData();
  form.append('file', new Blob([makeWav(3)], { type: 'audio/wav' }), 'Nhạc thử.wav');
  await realFetch(`${BASE}/api/projects/${PID}/music`, { method: 'POST', body: form });
  await until(() => !$('.music-controls').hidden, 'giao diện nhận nhạc mới', 20000);
  ok(text(musicBox).includes('Nhạc thử.wav') && $('.music-controls audio').src.includes('/music/background.wav'),
     'có nhạc: hiện tên bài + trình phát');
  ok($('input.volume').value === '15' && $('.music-controls input[type=checkbox]').checked, 'âm lượng mặc định 15%, bật tự giảm nhạc');
  const vol = $('input.volume');
  vol.value = '30';
  vol.dispatchEvent(new w.Event('input'));
  vol.dispatchEvent(new w.Event('change'));
  await until(async () => (await api(`/api/projects/${PID}`)).settings.music.volume === 0.3, 'lưu âm lượng', 5000);
  ok(text(musicBox).includes('30%'), 'kéo thanh âm lượng → lưu 30%');
  const duckBox = $('.music-controls input[type=checkbox]');
  duckBox.checked = false;
  duckBox.dispatchEvent(new w.Event('change'));
  await until(async () => (await api(`/api/projects/${PID}`)).settings.music.duck === false, 'lưu ducking', 5000);
  ok(true, 'bỏ chọn "tự giảm nhạc" → lưu');
  click(btnByText('Xoá nhạc', musicBox));
  await until(() => $('.music-controls').hidden, 'xoá nhạc', 5000);
  ok(confirms.at(-1).includes('Xoá nhạc nền') && (await api(`/api/projects/${PID}`)).music_url === null, 'xoá nhạc (có hỏi) → về "chưa có nhạc"');

  console.log('7. Đổi giọng (bước 3) → audio stale; bước 4 chặn xuất và chỉ ra cảnh');
  go(`#/p/${PID}/3`);
  await until(() => $$('.audio-row').length === 4, 'bước 3');
  const sel = $('select');
  sel.value = [...sel.options].find(o => o.value !== 'Thiện Minh').value;
  sel.dispatchEvent(new w.Event('change'));
  await until(() => text($$('.step')[2]).includes('0/4 audio'), 'audio 0/4', 5000);
  ok(confirms.at(-1).includes('Đổi giọng'), 'đổi giọng có hỏi xác nhận → 0/4 audio');
  go(`#/p/${PID}/4`);
  await until(() => $('.notice-warn'), 'cảnh báo', 5000);
  ok(text($('.notice-warn')).includes('còn 4 cảnh') && btnByText('Xuất lại video').disabled, 'bước 4: "còn 4 cảnh chưa sẵn sàng", nút xuất khoá');
  click($('.notice-warn button.link'));
  await until(() => w.location.hash.endsWith('/3'), 'link → bước 3', 5000);
  ok(true, 'bấm "audio cần tạo lại" → nhảy sang bước 3');

  console.log('8. Tạo dự án mới từ form + chia theo đoạn văn');
  go('#/');
  await until(() => $$('.project-card').length === 1, 'danh sách');
  click(btnByText('+ Dự án mới'));
  ok(!$('form.form').hidden, 'mở form');
  type($('form.form input'), 'Dự án thử giao diện');
  type($('form.form textarea'), 'Đoạn một.\n\nĐoạn hai.\n\nĐoạn ba.');
  $('form.form').dispatchEvent(new w.Event('submit', { cancelable: true }));
  await until(() => /#\/p\/.+\/1$/.test(w.location.hash) && $$('.scene-row').length === 0 && $('.scene-list .card'), 'vào bước 1 dự án mới', 5000);
  ok(text($$('.step')[0]).includes('Chưa chia cảnh'), 'dự án mới: "Chưa chia cảnh"');
  click(btnByText('Chia theo đoạn văn'));
  await until(() => $$('.scene-row').length === 3, '3 cảnh sau khi chia', 10000);
  ok(text($$('.step')[0]).includes('Đã lưu · 3 cảnh'), 'chia theo đoạn văn → 3 cảnh');
  click(btnByText('Chia theo đoạn văn'));
  await sleep(300);
  ok(confirms.at(-1).includes('THAY THẾ'), 'chia lại khi đã có cảnh → hỏi xác nhận');

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
