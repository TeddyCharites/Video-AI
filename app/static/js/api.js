// Gọi API backend. Mọi lỗi được chuyển thành ApiError với thông báo tiếng Việt dễ hiểu.

export class ApiError extends Error {
  constructor(status, message, detail) {
    super(message);
    this.status = status;   // 0 = không kết nối được server
    this.detail = detail;   // nội dung "detail" gốc từ FastAPI (vd. danh sách cảnh thiếu khi xuất)
  }
}

function errorMessage(status, data) {
  const d = data?.detail;
  if (typeof d === 'string') return d;
  if (d && !Array.isArray(d) && d.message) return d.message;
  if (Array.isArray(d)) {
    // Lỗi 422 của FastAPI/Pydantic: [{loc: ["body", "name"], msg: "..."}]
    return 'Dữ liệu không hợp lệ: ' + d.map(e => `${(e.loc || []).slice(1).join('.')}: ${e.msg}`).join('; ');
  }
  return `Lỗi máy chủ (${status})`;
}

/** Gửi file (multipart/form-data) — không đặt Content-Type để trình duyệt tự thêm "boundary". */
async function upload(path, file) {
  const form = new FormData();
  form.append('file', file);
  let res;
  try {
    res = await fetch(path, { method: 'POST', body: form });
  } catch {
    throw new ApiError(0, 'Không kết nối được server.');
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, errorMessage(res.status, data), data?.detail);
  return data;
}

async function request(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw new ApiError(0, 'Không kết nối được server. Cửa sổ chạy "python -m app.main" còn mở không?');
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, errorMessage(res.status, data), data?.detail);
  return data;
}

const p = id => `/api/projects/${encodeURIComponent(id)}`;
const s = (id, sid) => `${p(id)}/scenes/${encodeURIComponent(sid)}`;

export const api = {
  settings: () => request('GET', '/api/settings'),
  voices: lang => request('GET', `/api/voices?lang=${encodeURIComponent(lang)}`),
  health: () => request('GET', '/api/health'),

  listProjects: () => request('GET', '/api/projects'),
  createProject: body => request('POST', '/api/projects', body),
  getProject: id => request('GET', p(id)),
  updateProject: (id, body) => request('PATCH', p(id), body),
  deleteProject: id => request('DELETE', p(id)),

  split: (id, mode, force) => request('POST', `${p(id)}/split`, { mode, force }),
  saveScenes: (id, scenes) => request('PUT', `${p(id)}/scenes`, { scenes }),
  updateScene: (id, sid, body) => request('PATCH', s(id, sid), body),
  translate: (id, langs) => request('POST', `${p(id)}/translate`, { langs }),
  retranslate: (id, sid, langs) => request('POST', `${s(id, sid)}/translate`, { langs }),
  keepTranslation: (id, sid, lang) => request('POST', `${s(id, sid)}/translations/${encodeURIComponent(lang)}/keep`),

  generateImages: id => request('POST', `${p(id)}/images`),
  regenerateImage: (id, sid) => request('POST', `${s(id, sid)}/image`),
  generateAudio: (id, lang) => request('POST', `${p(id)}/audio`, { lang }),
  regenerateAudio: (id, sid, lang) => request('POST', `${s(id, sid)}/audio`, { lang }),

  exportVideo: (id, langs) => request('POST', `${p(id)}/export`, { langs }),
  suggestShorts: id => request('POST', `${p(id)}/shorts/suggest`),
  createShort: (id, body) => request('POST', `${p(id)}/shorts`, body),
  syncParent: id => request('POST', `${p(id)}/sync-parent`),
  uploadMusic: (id, file) => upload(`${p(id)}/music`, file),
  deleteMusic: id => request('DELETE', `${p(id)}/music`),
  cancelJob: jobId => request('DELETE', `/api/jobs/${encodeURIComponent(jobId)}`),
};
