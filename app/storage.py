"""Lưu trữ theo dự án: outputs/<project_id>/{project.json, scenes.json, scenes/<id>/, final.mp4}.

Dữ liệu trên đĩa là "nguồn sự thật": tắt app mở lại vẫn làm tiếp được.
Mọi thao tác đọc–sửa–ghi scenes.json/project.json đi qua khoá của dự án (project.lock),
vì worker (đang tạo ảnh/audio) và API (người dùng đang sửa cảnh) có thể ghi cùng lúc.
"""

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

STATUSES = ("missing", "ready", "stale", "error")
STATUS_FILES = {"image_status": "image.png", "audio_status": "audio.wav", "clip_status": "clip.mp4"}


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

    @property
    def final_video(self) -> Path:
        return self.dir / "final.mp4"

    def scene_dir(self, scene_id: str) -> Path:
        return self.dir / "scenes" / scene_id

    def exists(self) -> bool:
        return self.project_file.is_file()

    # project.json
    def load_info(self) -> dict:
        info = read_json(self.project_file)
        info.setdefault("final_status", "ready" if self.final_video.is_file() else "missing")
        return info

    def save_info(self, info: dict) -> None:
        write_json_atomic(self.project_file, info)

    def update_info(self, fn: Callable[[dict], None]) -> dict:
        with self.lock:
            info = self.load_info()
            fn(info)
            self.save_info(info)
            return info

    def set_final_status(self, status: str, **extra) -> None:
        def apply(info):
            info["final_status"] = status
            info.update(extra)
        self.update_info(apply)

    def mark_final_stale(self) -> None:
        """Có gì đó trong các cảnh đã đổi → final.mp4 hiện có (nếu có) không còn đúng."""
        with self.lock:
            if self.load_info()["final_status"] == "ready":
                self.set_final_status("stale")

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
                for field, filename in STATUS_FILES.items():
                    if s[field] == "ready" and not (self.scene_dir(s["id"]) / filename).is_file():
                        s[field] = "missing"
        with self.lock:
            if self.scenes_file.is_file():
                self.update_scenes(apply)
            if not self.final_video.is_file() and self.load_info()["final_status"] != "missing":
                self.set_final_status("missing")


def create_project(outputs_dir: Path, name: str, script_text: str, settings: dict) -> Project:
    project_id = f"{datetime.now():%Y%m%d-%H%M%S}-{slugify(name)}"
    project = Project(outputs_dir, project_id)
    if project.exists():
        raise FileExistsError(f"Dự án {project_id} đã tồn tại, thử lại sau 1 giây")
    project.save_info({
        "id": project_id,
        "name": name,
        "script": script_text.replace("\r\n", "\n").strip(),
        "settings": settings,   # bản chụp các cấu hình quan trọng lúc tạo dự án
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "final_status": "missing",
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
def new_scene(scene_id: str, order: int, narration: str, image_prompt: str) -> dict:
    return {
        "id": scene_id,
        "order": order,
        "narration": narration.strip(),
        "image_prompt": image_prompt.strip(),
        "duration_sec": None,
        "image_status": "missing",
        "audio_status": "missing",
        "clip_status": "missing",
        "error": None,
    }


def make_scenes(items: list[dict]) -> list[dict]:
    """Từ [{narration, image_prompt}, ...] (kết quả chia cảnh) → danh sách cảnh đầy đủ schema."""
    return [new_scene(f"{i:02d}", i, it["narration"], it.get("image_prompt", ""))
            for i, it in enumerate(items, start=1)]


def needs_work(scene: dict, field: str) -> bool:
    """Chỉ tạo lại những gì chưa 'ready' (missing / stale / error)."""
    return scene[field] != "ready"


def mark_stale(scene: dict, field: str) -> None:
    """Đầu vào của `field` đã đổi. 'missing' giữ nguyên (chưa có gì để mà cũ)."""
    if scene[field] != "missing":
        scene[field] = "stale"


def apply_scene_edits(project: Project, edits: list[dict]) -> tuple[list[dict], bool]:
    """Thay toàn bộ danh sách cảnh bằng `edits` (đã sắp theo thứ tự mong muốn).

    Mỗi phần tử: {"id": <id cũ hoặc None nếu là cảnh mới>, "narration", "image_prompt"}.
    Quy tắc phụ thuộc (CLAUDE.md):
      - sửa lời đọc → audio stale (và clip stale)
      - sửa prompt ảnh → ảnh stale (và clip stale)
      - cảnh mới → missing; cảnh bị xoá → xoá thư mục của nó
    Trả về (danh sách mới, có thay đổi hay không).
    """
    with project.lock:
        old = {s["id"]: s for s in project.load_scenes()}
        unknown = [e["id"] for e in edits if e.get("id") and e["id"] not in old]
        if unknown:
            raise KeyError(f"Không có cảnh: {', '.join(unknown)}")
        ids = [e["id"] for e in edits if e.get("id")]
        if len(ids) != len(set(ids)):
            raise ValueError("Một cảnh xuất hiện hai lần")

        # Cảnh mới nhận id = lớn nhất từng có + 1; KHÔNG dùng lại id của cảnh đã xoá
        next_num = max((int(i) for i in old), default=0) + 1
        result, changed = [], False
        for order, e in enumerate(edits, start=1):
            narration, prompt = e["narration"].strip(), e.get("image_prompt", "").strip()
            if not e.get("id"):
                result.append(new_scene(f"{next_num:02d}", order, narration, prompt))
                next_num += 1
                changed = True
                continue
            s = dict(old[e["id"]])
            if s["order"] != order:
                s["order"] = order
                changed = True
            if s["narration"] != narration:
                s["narration"] = narration
                mark_stale(s, "audio_status")
                mark_stale(s, "clip_status")
                changed = True
            if s["image_prompt"] != prompt:
                s["image_prompt"] = prompt
                mark_stale(s, "image_status")
                mark_stale(s, "clip_status")
                changed = True
            result.append(s)

        removed = set(old) - set(ids)
        changed = changed or bool(removed)
        project.save_scenes(result)
        for scene_id in removed:
            shutil.rmtree(project.scene_dir(scene_id), ignore_errors=True)
        if changed:
            project.mark_final_stale()
        return result, changed


def export_problems(scenes: list[dict], fields=tuple(STATUS_FILES)) -> list[dict]:
    """Các cảnh chưa sẵn sàng để xuất video: [{id, order, missing: {field: status}, error}]."""
    problems = []
    for s in scenes:
        bad = {f: s[f] for f in fields if s[f] != "ready"}
        if bad:
            problems.append({"id": s["id"], "order": s["order"], "not_ready": bad, "error": s["error"]})
    return problems
