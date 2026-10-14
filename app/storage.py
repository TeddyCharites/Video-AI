"""Lưu trữ theo dự án (định dạng v2, đa ngôn ngữ):

outputs/<project_id>/
├── project.json          # tên, kịch bản gốc, ngôn ngữ, giọng theo ngôn ngữ, trạng thái video theo ngôn ngữ
├── scenes.json           # cảnh: ảnh dùng chung + langs.<mã> (lời đọc, audio, clip riêng từng ngôn ngữ)
├── scenes/<id>/image.png
├── scenes/<id>/<lang>/{audio.wav, clip.mp4, annotation.json}
├── music/background.*    # nhạc nền (tuỳ chọn)
└── final.<lang>.mp4 · final.<lang>.srt · final.<lang>-<lang2>.srt (song ngữ)

Dữ liệu trên đĩa là "nguồn sự thật": tắt app mở lại vẫn làm tiếp được.
Mọi thao tác đọc–sửa–ghi scenes.json/project.json đi qua khoá của dự án (project.lock),
vì worker (đang tạo ảnh/audio) và API (người dùng đang sửa cảnh) có thể ghi cùng lúc.
"""

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Callable

SCHEMA_VERSION = 2
STATUSES = ("missing", "ready", "stale", "error")
LANG_FILES = {"audio_status": "audio.wav", "clip_status": "clip.mp4"}  # file trong scenes/<id>/<lang>/


# ── Ghi file an toàn ──────────────────────────────────────────────────────────
def replace_file(src: Path, dst: Path, attempts: int = 25, wait: float = 0.2) -> None:
    """os.replace có thử lại. Trên Windows, KHÔNG thay được file đang bị tiến trình khác mở
    (trình duyệt đang phát video, tiến trình vẽ bảng đang đọc ảnh…) → PermissionError tạm thời."""
    import time
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(wait)


def write_json_atomic(path: Path, data) -> None:
    """Ghi ra file tạm cùng thư mục rồi os.replace → file đích luôn là bản cũ hoặc bản mới
    hoàn chỉnh, không bao giờ là bản ghi dở (kể cả khi app bị tắt giữa chừng)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())  # đẩy hẳn xuống đĩa trước khi đổi tên
        replace_file(Path(tmp), path)  # đổi tên là thao tác nguyên tử trên cùng ổ đĩa
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── Khoá theo dự án ───────────────────────────────────────────────────────────
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _project_lock(project_id: str) -> threading.RLock:
    # RLock (khoá "tái nhập"): cùng một luồng được lấy lại khoá nó đang giữ mà không tự kẹt
    with _locks_guard:
        return _locks.setdefault(project_id, threading.RLock())


# ── Dự án ─────────────────────────────────────────────────────────────────────
def slugify(text: str) -> str:
    """'Bài học số 1!' → 'bai-hoc-so-1' (bỏ dấu, chỉ giữ a-z0-9 cho tên thư mục an toàn)."""
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "du-an"


_PROJECT_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9-]+$")


def is_valid_project_id(project_id: str) -> bool:
    """Chặn id kiểu '../../Windows' — id luôn do create_project() sinh ra theo mẫu cố định."""
    return bool(_PROJECT_ID_RE.match(project_id))


class Project:
    def __init__(self, outputs_dir: Path, project_id: str):
        if not is_valid_project_id(project_id):
            raise ValueError(f"project_id không hợp lệ: {project_id!r}")
        self.id = project_id
        self.dir = outputs_dir / project_id

    @property
    def lock(self) -> threading.RLock:
        return _project_lock(self.id)

    @property
    def project_file(self) -> Path:
        return self.dir / "project.json"

    @property
    def scenes_file(self) -> Path:
        return self.dir / "scenes.json"

    def final_video(self, lang: str) -> Path:
        return self.dir / f"final.{lang}.mp4"

    def srt_path(self, lang: str, secondary: str | None = None) -> Path:
        """final.vi.srt, hoặc final.vi-en.srt cho phụ đề song ngữ (dòng chính vi, dòng phụ en)."""
        return self.dir / (f"final.{lang}-{secondary}.srt" if secondary else f"final.{lang}.srt")

    def scene_dir(self, scene_id: str) -> Path:
        return self.dir / "scenes" / scene_id

    def lang_dir(self, scene_id: str, lang: str) -> Path:
        return self.dir / "scenes" / scene_id / lang

    def exists(self) -> bool:
        return self.project_file.is_file()

    # project.json
    def load_info(self) -> dict:
        info = read_json(self.project_file)
        info.setdefault("finals", {})
        for lang in info.get("languages", []):
            info["finals"].setdefault(lang, {"status": "ready" if self.final_video(lang).is_file() else "missing"})
        return info

    def save_info(self, info: dict) -> None:
        write_json_atomic(self.project_file, info)

    def update_info(self, fn: Callable[[dict], None]) -> dict:
        with self.lock:
            info = self.load_info()
            fn(info)
            self.save_info(info)
            return info

    def languages(self) -> list[str]:
        return self.load_info()["languages"]

    def source_lang(self) -> str:
        return self.load_info()["source_lang"]

    def set_final(self, lang: str, status: str, **extra) -> None:
        def apply(info):
            info["finals"].setdefault(lang, {})
            info["finals"][lang]["status"] = status
            info["finals"][lang].update(extra)
        self.update_info(apply)

    def mark_final_stale(self, langs: list[str] | None = None) -> None:
        """Có gì đó đã đổi → video đã xuất của các ngôn ngữ này (mặc định: mọi ngôn ngữ) không còn đúng."""
        def apply(info):
            for lang in langs or info["languages"]:
                f = info["finals"].get(lang)
                if f and f.get("status") == "ready":
                    f["status"] = "stale"
        self.update_info(apply)

    # scenes.json — luôn trả về danh sách đã sắp theo "order"
    def load_scenes(self) -> list[dict]:
        if not self.scenes_file.is_file():
            return []
        return sorted(read_json(self.scenes_file), key=lambda s: s["order"])

    def save_scenes(self, scenes: list[dict]) -> None:
        write_json_atomic(self.scenes_file, scenes)

    def update_scenes(self, fn: Callable[[list[dict]], None]) -> list[dict]:
        """Đọc – sửa (fn sửa trực tiếp danh sách) – ghi, trong khoá của dự án."""
        with self.lock:
            scenes = self.load_scenes()
            fn(scenes)
            self.save_scenes(scenes)
            return scenes

    def get_scene(self, scene_id: str) -> dict | None:
        return next((s for s in self.load_scenes() if s["id"] == scene_id), None)

    def update_scene(self, scene_id: str, fn: Callable[[dict], None]) -> dict | None:
        """Sửa một cảnh. Trả về None nếu cảnh đã bị xoá (vd. người dùng xoá trong lúc job đang chạy)."""
        found = {}

        def apply(scenes):
            for s in scenes:
                if s["id"] == scene_id:
                    fn(s)
                    found["scene"] = s
        self.update_scenes(apply)
        return found.get("scene")

    def sync_status_with_files(self) -> None:
        """Nếu scenes.json nói 'ready' mà file đã bị xoá → coi như 'missing'."""
        def apply(scenes):
            for s in scenes:
                if s["image_status"] == "ready" and not (self.scene_dir(s["id"]) / "image.png").is_file():
                    s["image_status"] = "missing"
                for lang, entry in s["langs"].items():
                    for field, filename in LANG_FILES.items():
                        if entry[field] == "ready" and not (self.lang_dir(s["id"], lang) / filename).is_file():
                            entry[field] = "missing"
        with self.lock:
            if self.scenes_file.is_file():
                self.update_scenes(apply)

            def finals(info):
                for lang, f in info["finals"].items():
                    if f.get("status") != "missing" and not self.final_video(lang).is_file():
                        f["status"] = "missing"
            self.update_info(finals)


def create_project(outputs_dir: Path, name: str, script_text: str, settings: dict,
                   source_lang: str, languages: list[str]) -> Project:
    """languages: mọi ngôn ngữ của dự án, gồm cả ngôn ngữ gốc (được đưa lên đầu)."""
    project_id = f"{datetime.now():%Y%m%d-%H%M%S}-{slugify(name)}"
    project = Project(outputs_dir, project_id)
    if project.exists():
        raise FileExistsError(f"Dự án {project_id} đã tồn tại, thử lại sau 1 giây")
    languages = [source_lang] + [l for l in languages if l != source_lang]
    project.save_info({
        "schema_version": SCHEMA_VERSION,
        "id": project_id,
        "name": name,
        "script": script_text.replace("\r\n", "\n").strip(),
        "source_lang": source_lang,
        "languages": languages,
        "titles": {source_lang: name},   # tiêu đề video theo ngôn ngữ (bản dịch thêm khi dịch)
        "settings": settings,             # bản chụp cấu hình lúc tạo dự án (giọng theo ngôn ngữ…)
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "finals": {lang: {"status": "missing"} for lang in languages},
    })
    project.save_scenes([])
    return project


def list_projects(outputs_dir: Path) -> list[dict]:
    """Thông tin các dự án, mới nhất trước."""
    if not outputs_dir.is_dir():
        return []
    infos = []
    for p in outputs_dir.glob("*/project.json"):
        if is_valid_project_id(p.parent.name):  # bỏ qua outputs/tests và thư mục lạ
            infos.append(Project(outputs_dir, p.parent.name).load_info())
    return sorted(infos, key=lambda i: i["created_at"], reverse=True)


def delete_project(project: Project) -> None:
    with project.lock:
        shutil.rmtree(project.dir)


# ── Cảnh ──────────────────────────────────────────────────────────────────────
def text_hash(text: str) -> str:
    """Dấu vân tay của một lời đọc: bản dịch ghi lại nó để biết mình được dịch từ phiên bản nào."""
    return hashlib.sha1(" ".join(text.split()).encode("utf-8")).hexdigest()[:12]


def new_lang_entry(narration: str = "", *, is_source: bool) -> dict:
    return {
        "narration": narration.strip(),
        # Ngôn ngữ gốc: lời đọc do người viết → luôn "ready". Bản dịch: "missing" tới khi được dịch.
        "text_status": "ready" if is_source else "missing",
        "source_hash": None,   # bản dịch: text_hash của lời gốc lúc dịch
        "edited": False,       # bản dịch đã được người dùng sửa tay
        "duration_sec": None,
        "audio_status": "missing",
        "clip_status": "missing",
        "error": None,
    }


def new_scene(scene_id: str, order: int, narration: str, image_prompt: str,
              source_lang: str, languages: list[str]) -> dict:
    scene = {
        "id": scene_id,
        "order": order,
        "image_prompt": image_prompt.strip(),
        "image_status": "missing",
        "image_error": None,
        "langs": {},
    }
    ensure_languages(scene, source_lang, languages)
    scene["langs"][source_lang]["narration"] = narration.strip()
    return scene


def make_scenes(items: list[dict], source_lang: str, languages: list[str]) -> list[dict]:
    """Từ [{narration, image_prompt}, ...] (kết quả chia cảnh) → danh sách cảnh đầy đủ schema."""
    return [new_scene(f"{i:02d}", i, it["narration"], it.get("image_prompt", ""), source_lang, languages)
            for i, it in enumerate(items, start=1)]


def ensure_languages(scene: dict, source_lang: str, languages: list[str]) -> None:
    """Thêm mục còn thiếu cho ngôn ngữ mới thêm vào dự án."""
    for lang in languages:
        scene["langs"].setdefault(lang, new_lang_entry(is_source=(lang == source_lang)))


def needs_work(entry: dict, field: str) -> bool:
    """Chỉ tạo lại những gì chưa 'ready' (missing / stale / error)."""
    return entry[field] != "ready"


def mark_stale(entry: dict, field: str) -> None:
    """Đầu vào của `field` đã đổi. 'missing' giữ nguyên (chưa có gì để mà cũ)."""
    if entry[field] != "missing":
        entry[field] = "stale"


# ── Quy tắc phụ thuộc ─────────────────────────────────────────────────────────
# Sửa lời gốc       → audio + clip ngôn ngữ gốc stale; bản dịch + audio + clip MỌI ngôn ngữ dịch stale
# Sửa bản dịch (en) → audio + clip ngôn ngữ đó stale
# Sửa mô tả ảnh     → ảnh stale + clip MỌI ngôn ngữ stale (ảnh dùng chung)
def set_source_narration(scene: dict, text: str, source_lang: str) -> bool:
    text = text.strip()
    src = scene["langs"][source_lang]
    if src["narration"] == text:
        return False
    src["narration"] = text
    mark_stale(src, "audio_status")
    mark_stale(src, "clip_status")
    for lang, entry in scene["langs"].items():
        if lang != source_lang:
            mark_stale(entry, "text_status")
            mark_stale(entry, "audio_status")
            mark_stale(entry, "clip_status")
    return True


def set_translation(scene: dict, lang: str, text: str, source_lang: str, *, by_user: bool) -> bool:
    """Ghi bản dịch. by_user=True: người dùng sửa tay → coi như bản dịch hợp lệ cho lời gốc HIỆN TẠI."""
    text = text.strip()
    entry = scene["langs"][lang]
    changed = entry["narration"] != text
    entry["narration"] = text
    entry["text_status"] = "ready" if text else "missing"
    entry["source_hash"] = text_hash(scene["langs"][source_lang]["narration"])
    entry["edited"] = by_user or (entry["edited"] and not changed)
    entry["error"] = None if entry["error"] and entry["error"].startswith("[translate]") else entry["error"]
    if changed:
        mark_stale(entry, "audio_status")
        mark_stale(entry, "clip_status")
    return changed


def set_image_prompt(scene: dict, prompt: str) -> bool:
    prompt = prompt.strip()
    if scene["image_prompt"] == prompt:
        return False
    scene["image_prompt"] = prompt
    mark_stale(scene, "image_status")
    for entry in scene["langs"].values():
        mark_stale(entry, "clip_status")
    return True


def apply_scene_edits(project: Project, edits: list[dict]) -> tuple[list[dict], set[str]]:
    """Thay toàn bộ danh sách cảnh bằng `edits` (đã sắp theo thứ tự mong muốn).

    Mỗi phần tử: {"id": <id cũ hoặc None nếu là cảnh mới>, "narration": <lời gốc>, "image_prompt",
                  "translations": {<lang>: <bản dịch>} (tuỳ chọn — chỉ gửi bản dịch người dùng đã sửa)}.
    Trả về (danh sách mới, các ngôn ngữ có thay đổi → video đã xuất của chúng thành "cũ").
    """
    with project.lock:
        info = project.load_info()
        source, languages = info["source_lang"], info["languages"]
        old = {s["id"]: s for s in project.load_scenes()}
        unknown = [e["id"] for e in edits if e.get("id") and e["id"] not in old]
        if unknown:
            raise KeyError(f"Không có cảnh: {', '.join(unknown)}")
        ids = [e["id"] for e in edits if e.get("id")]
        if len(ids) != len(set(ids)):
            raise ValueError("Một cảnh xuất hiện hai lần")
        for e in edits:
            for lang in (e.get("translations") or {}):
                if lang not in languages or lang == source:
                    raise ValueError(f"Ngôn ngữ dịch không thuộc dự án: {lang}")

        # Cảnh mới nhận id = lớn nhất từng có + 1; KHÔNG dùng lại id của cảnh đã xoá
        next_num = max((int(i) for i in old), default=0) + 1
        result, changed_langs = [], set()
        for order, e in enumerate(edits, start=1):
            if not e.get("id"):
                s = new_scene(f"{next_num:02d}", order, e["narration"], e.get("image_prompt", ""), source, languages)
                next_num += 1
                changed_langs.update(languages)
            else:
                s = json.loads(json.dumps(old[e["id"]]))  # bản sao sâu
                ensure_languages(s, source, languages)
                if s["order"] != order:
                    s["order"] = order
                    changed_langs.update(languages)
                if set_source_narration(s, e["narration"], source):
                    changed_langs.update(languages)
                if set_image_prompt(s, e.get("image_prompt", "")):
                    changed_langs.update(languages)
            for lang, text in (e.get("translations") or {}).items():
                if text is not None and text.strip() != s["langs"][lang]["narration"]:
                    set_translation(s, lang, text, source, by_user=True)
                    changed_langs.add(lang)
            result.append(s)

        removed = set(old) - set(ids)
        if removed:
            changed_langs.update(languages)
        project.save_scenes(result)
        for scene_id in removed:
            shutil.rmtree(project.scene_dir(scene_id), ignore_errors=True)
        if changed_langs:
            project.mark_final_stale(sorted(changed_langs))
        return result, changed_langs


def export_problems(scenes: list[dict], lang: str, source_lang: str, include_clips: bool = False) -> list[dict]:
    """Các cảnh chưa sẵn sàng để xuất video ngôn ngữ `lang`: [{id, order, not_ready: {field: status}, error}].
    field: image_status | text_status (bản dịch) | audio_status | clip_status."""
    problems = []
    for s in scenes:
        entry = s["langs"].get(lang)
        bad = {}
        if s["image_status"] != "ready":
            bad["image_status"] = s["image_status"]
        if entry is None:
            bad["text_status"] = "missing"
        else:
            if lang != source_lang and entry["text_status"] != "ready":
                bad["text_status"] = entry["text_status"]
            if entry["audio_status"] != "ready":
                bad["audio_status"] = entry["audio_status"]
            if include_clips and entry["clip_status"] != "ready":
                bad["clip_status"] = entry["clip_status"]
        if bad:
            problems.append({"id": s["id"], "order": s["order"], "lang": lang, "not_ready": bad,
                             "error": (entry or {}).get("error") or s.get("image_error")})
    return problems


# ── Chuyển dự án định dạng cũ (v1, chỉ tiếng Việt) sang v2 ─────────────────────
def needs_migration(project_dir: Path) -> bool:
    pf = project_dir / "project.json"
    return pf.is_file() and read_json(pf).get("schema_version", 1) < SCHEMA_VERSION


def migrate_project(project_dir: Path, default_voice: str, source_lang: str = "vi",
                    dry_run: bool = False, log: Callable[[str], None] = print) -> bool:
    """v1 → v2. An toàn khi chạy lại / bị ngắt giữa chừng:
      1. sao lưu project.json, scenes.json (*.v1.bak) — chỉ lần đầu
      2. chuyển file: scenes/<id>/{audio.wav, clip.mp4, annotation.json} → scenes/<id>/<lang>/, final.* → final.<lang>.*
         (file đã chuyển thì bỏ qua)
      3. ghi scenes.json mới, rồi CUỐI CÙNG ghi project.json kèm schema_version = 2 (dấu "đã xong")
    Trả về True nếu dự án cần (và đã, nếu không dry_run) chuyển."""
    if not needs_migration(project_dir):
        return False
    lang = source_lang
    info = read_json(project_dir / "project.json")
    scenes_path = project_dir / "scenes.json"
    raw_scenes = read_json(scenes_path) if scenes_path.is_file() else []
    log(f"[migrate] {project_dir.name}: {len(raw_scenes)} cảnh" + (" (dry-run)" if dry_run else ""))
    if dry_run:
        return True

    for name in ("project.json", "scenes.json"):
        src, bak = project_dir / name, project_dir / f"{name}.v1.bak"
        if src.is_file() and not bak.exists():
            shutil.copy2(src, bak)

    def move(src: Path, dst: Path) -> None:
        if src.is_file() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            replace_file(src, dst)

    for s in raw_scenes:
        sdir = project_dir / "scenes" / s["id"]
        for fname in ("audio.wav", "clip.mp4", "annotation.json"):
            move(sdir / fname, sdir / lang / fname)
        for tmp in sdir.glob("wb_*"):  # thư mục tạm còn sót của bước vẽ bảng
            shutil.rmtree(tmp, ignore_errors=True)
    for old, new in (("final.mp4", f"final.{lang}.mp4"), ("final.srt", f"final.{lang}.srt"),
                     ("final.ass", f"final.{lang}.ass"), ("remotion_props.json", f"remotion_props.{lang}.json")):
        move(project_dir / old, project_dir / new)

    new_scenes = []
    for s in raw_scenes:
        if "langs" in s:  # cảnh đã ở định dạng mới (lần chạy trước bị ngắt sau khi ghi scenes.json)
            new_scenes.append(s)
            continue
        err = s.get("error")
        image_err = err if err and err.startswith("[images]") else None
        entry = new_lang_entry(s.get("narration", ""), is_source=True)
        entry.update(duration_sec=s.get("duration_sec"), audio_status=s.get("audio_status", "missing"),
                     clip_status=s.get("clip_status", "missing"), error=None if image_err else err)
        new_scenes.append({"id": s["id"], "order": s["order"], "image_prompt": s.get("image_prompt", ""),
                           "image_status": s.get("image_status", "missing"), "image_error": image_err,
                           "langs": {lang: entry}})
    write_json_atomic(scenes_path, new_scenes)

    settings = dict(info.get("settings", {}))
    voice = settings.pop("voice", None) or default_voice
    settings["voices"] = {lang: voice}
    # Dự án rất cũ (trước khi có final_status) → dựa vào việc file video có tồn tại không
    has_video = (project_dir / f"final.{lang}.mp4").is_file()
    final = {"status": info.pop("final_status", "ready" if has_video else "missing")}
    for key in ("final_duration_sec", "final_subtitles", "final_engine", "final_music"):
        if key in info:
            final[key.removeprefix("final_")] = info.pop(key)
    info.update(schema_version=SCHEMA_VERSION, source_lang=lang, languages=[lang],
                titles={lang: info.get("name", "")}, settings=settings, finals={lang: final})
    write_json_atomic(project_dir / "project.json", info)  # cuối cùng: đánh dấu đã chuyển xong
    return True


def migrate_all(outputs_dir: Path, default_voice: str, **kw) -> list[str]:
    done = []
    for pf in sorted(outputs_dir.glob("*/project.json")):
        if is_valid_project_id(pf.parent.name) and migrate_project(pf.parent, default_voice, **kw):
            done.append(pf.parent.name)
    return done
