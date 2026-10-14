// Tiện ích giao diện dùng chung: tạo phần tử, badge trạng thái, toast, tự lưu, trạng thái job.

/** Tạo phần tử DOM gọn: h('button', {class: 'btn', onclick: fn}, 'Chữ'). */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'value') el.value = v;            // textarea/input: phải gán thuộc tính, không phải attribute
    else if (['disabled', 'checked', 'selected', 'hidden'].includes(k)) el[k] = Boolean(v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c != null && c !== false) el.append(c instanceof Node ? c : String(c));
  }
  return el;
}

export const STATUS_LABELS = {
  missing: 'Chưa có', ready: 'Sẵn sàng', stale: 'Cần tạo lại', error: 'Lỗi',
  queued: 'Đang chờ', running: 'Đang tạo…',
};

export function badge(status, prefix = '') {
  return h('span', { class: `badge badge-${status}` }, prefix + (STATUS_LABELS[status] || status));
}

export function fmtSec(sec) {
  if (sec == null) return '—';
  if (sec < 60) return `${sec.toFixed(1)} giây`;
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return `${m} phút ${String(s).padStart(2, '0')} giây`;
}

export function fmtDate(iso) {
  return new Date(iso).toLocaleString('vi-VN', { dateStyle: 'short', timeStyle: 'short' });
}

/** Số tiếng (âm tiết) trong câu tiếng Việt = số cụm cách nhau bởi khoảng trắng. */
export const countWords = text => (text.trim() ? text.trim().split(/\s+/).length : 0);

export function toast(message, type = 'info', ms = 5000) {
  const el = h('div', { class: `toast toast-${type}` }, message);
  el.addEventListener('click', () => el.remove());
  document.getElementById('toasts').append(el);
  setTimeout(() => el.remove(), type === 'error' ? ms * 2 : ms);
}

/** Bọc thao tác bất đồng bộ: khoá nút trong lúc chạy, lỗi thì hiện toast. */
export async function guarded(button, fn) {
  if (button) button.disabled = true;
  try {
    return await fn();
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

/** Cuộn tới cảnh và nháy sáng để người dùng thấy ngay. */
export function focusScene(root, sceneId) {
  const el = root.querySelector(`[data-scene-id="${CSS.escape(sceneId)}"]`);
  if (!el) return false;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  el.classList.remove('flash');
  void el.offsetWidth;  // ép trình duyệt tính lại layout để animation chạy lại từ đầu
  el.classList.add('flash');
  return true;
}

// ── Trạng thái job ──────────────────────────────────────────────────────────
export const isActive = job => job.status === 'queued' || job.status === 'running';

const JOB_KINDS = {
  image: { one: 'image', all: 'images', status: s => s.image_status },
  audio: { one: 'audio_one', all: 'audio', status: (s, lang) => s.langs[lang]?.audio_status },
  text: { one: 'translate_one', all: 'translate', status: (s, lang) => s.langs[lang]?.text_status },
};

/** Cảnh này có đang chờ/làm ảnh (field='image'), audio của ngôn ngữ `lang` (field='audio')
 *  hay bản dịch (field='text') không? Trả về 'running' | 'queued' | null. */
export function sceneJobStatus(project, scene, field, lang = null) {
  const kinds = JOB_KINDS[field];
  let result = null;
  for (const j of project.jobs.filter(isActive)) {
    if (field === 'audio' && j.lang !== lang) continue;   // audio en không phải audio vi
    const mine = (j.kind === kinds.one && j.scene_id === scene.id)
      // Job "mọi cảnh" chỉ làm cảnh chưa ready
      || (j.kind === kinds.all && kinds.status(scene, lang) !== 'ready');
    if (!mine) continue;
    if (j.status === 'running' && j.kind === kinds.one) return 'running';
    result = j.status === 'running' ? 'running' : (result || 'queued');
  }
  return result;
}

/** Job đang chờ/chạy theo loại (và ngôn ngữ, nếu đưa vào). */
export function activeJob(project, kind, lang = undefined) {
  return project.jobs.find(j => j.kind === kind && isActive(j) && (lang === undefined || j.lang === lang)) || null;
}

/** Tên hiển thị của ngôn ngữ ("Tiếng Việt", "English"). */
export function langName(settings, code) {
  return settings.languages.find(l => l.code === code)?.name || code;
}

/** Ước lượng số giây đọc của một đoạn ở ngôn ngữ `code`. */
export function estimateSec(settings, code, text) {
  const ups = settings.languages.find(l => l.code === code)?.units_per_sec || 3;
  return countWords(text) / ups;
}

/**
 * Tự lưu một ô nhập: lưu sau khi ngừng gõ `delay` ms, hoặc ngay khi rời ô.
 * save(value) phải trả Promise. Dùng cho prompt ảnh (bước 2) và lời đọc (bước 3).
 */
export function autosave(textarea, getServerValue, save, delay = 1200) {
  let timer = null;
  let saving = Promise.resolve();
  const flush = () => {
    clearTimeout(timer);
    timer = null;
    const value = textarea.value;
    if (value.trim() === '' || value === getServerValue()) return saving;
    textarea.classList.add('saving');
    saving = saving.then(() => save(value))
      .catch(e => toast(e.message, 'error'))
      .finally(() => textarea.classList.remove('saving'));
    return saving;
  };
  textarea.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(flush, delay);
  });
  textarea.addEventListener('blur', flush);
  return { flush, isPending: () => timer !== null };
}
