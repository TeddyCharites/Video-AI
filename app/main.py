"""Backend FastAPI của Video Studio Local.

Chạy:  .venv\\Scripts\\python -m app.main          → http://127.0.0.1:8000  (tài liệu API: /docs)

Mọi tác vụ nặng (chia cảnh, ảnh, audio, xuất) KHÔNG chạy trong request: endpoint chỉ xếp job
vào hàng đợi (app/jobs.py) rồi trả về ngay (HTTP 202). Frontend hỏi tiến độ bằng GET /api/jobs.
"""

import copy
import os
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import storage
from app.config import apply_runtime_env, load_config, video_size
from app.jobs import JobQueue
from app.schemas import (ProjectCreate, ProjectUpdate, SceneIn, ScenesReplace, SceneUpdate,
                         SplitRequest)
from app.storage import Project, export_problems, mark_stale, replace_file
from pipeline import remotion_render, render
from pipeline.tts import list_voices

STATIC_DIR = Path(__file__).resolve().parent / "static"
JOB_LABELS = {"split": "Chia cảnh", "images": "Tạo ảnh", "image": "Tạo lại ảnh",
              "audio": "Tạo giọng đọc", "audio_one": "Tạo lại giọng đọc", "export": "Xuất video"}


def create_app(cfg: dict | None = None, overrides: dict | None = None) -> FastAPI:
    """Factory: test có thể truyền cfg riêng (thư mục outputs tạm) và overrides
    (vd. {"images.backend": "placeholder"} để không cần GPU)."""
    cfg = cfg or load_config()
    apply_runtime_env(cfg)
    overrides = overrides or {}
    outputs_dir: Path = cfg["outputs_dir"]
    outputs_dir.mkdir(parents=True, exist_ok=True)
    jobs = JobQueue()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        jobs.start()
        yield
        jobs.stop()

    app = FastAPI(title="Video Studio Local", lifespan=lifespan)
    app.state.jobs = jobs
    app.state.cfg = cfg

    # ── Tiện ích ──────────────────────────────────────────────────────────────
    def get_project(project_id: str) -> Project:
        try:
            project = Project(outputs_dir, project_id)
        except ValueError:
            raise HTTPException(404, "Không tìm thấy dự án")
        if not project.exists():
            raise HTTPException(404, "Không tìm thấy dự án")
        return project

    def project_cfg(project: Project) -> dict:
        """Cấu hình cho riêng dự án: giọng đọc + tỉ lệ khung hình lấy từ project.json."""
        pcfg = copy.deepcopy(cfg)
        settings = project.load_info().get("settings", {})
        if settings.get("voice"):
            pcfg["tts"]["voice"] = settings["voice"]
        if settings.get("aspect_ratio"):
            pcfg["video"]["aspect_ratio"] = settings["aspect_ratio"]
        if settings.get("subtitles") is not None:
            pcfg["subtitles"]["enabled"] = settings["subtitles"]
        if settings.get("title") is not None:
            pcfg["remotion"]["title"] = settings["title"]
        return pcfg

    def file_url(project: Project, rel: str) -> str | None:
        path = project.dir / rel
        if not path.is_file():
            return None
        # ?v=<thời điểm sửa>: file được tạo lại → URL đổi → trình duyệt không dùng bản cache cũ
        return f"/files/{project.id}/{rel}?v={path.stat().st_mtime_ns}"

    def scene_view(project: Project, s: dict) -> dict:
        base = f"scenes/{s['id']}"
        return {**s,
                "image_url": file_url(project, f"{base}/image.png"),
                "audio_url": file_url(project, f"{base}/audio.wav"),
                "clip_url": file_url(project, f"{base}/clip.mp4")}

    def summary(scenes: list[dict]) -> dict:
        n = len(scenes)

        def count(field: str) -> dict:
            not_ready = [s["id"] for s in scenes if s[field] != "ready"]
            return {"ready": n - len(not_ready), "total": n, "not_ready_ids": not_ready}
        known = [s["duration_sec"] for s in scenes if s["duration_sec"]]
        return {"images": count("image_status"), "audio": count("audio_status"),
                "clips": count("clip_status"),
                "total_duration_sec": round(sum(known), 2) if known else None}

    def project_view(project: Project) -> dict:
        project.sync_status_with_files()
        info = project.load_info()
        scenes = project.load_scenes()
        return {
            **info,
            "scenes": [scene_view(project, s) for s in scenes],
            "summary": summary(scenes),
            # Bước 4: ảnh + audio phải ready hết; clip thì "Xuất" tự tạo
            "export_problems": export_problems(scenes, fields=("image_status", "audio_status")),
            "final_url": file_url(project, "final.mp4"),
            "srt_url": file_url(project, "final.srt"),
            "music_url": (file_url(project, info["settings"]["music"]["file"])
                          if info.get("settings", {}).get("music") else None),
            "jobs": [j.to_dict() for j in jobs.list(project.id)],
        }

    def enqueue(project: Project, kind: str, action: str, scene_id: str | None = None,
                **params) -> JSONResponse:
        """Xếp job. spec là dữ liệu thuần vì job chạy trong TIẾN TRÌNH CON (app/jobs.py)."""
        if kind != "split":
            ensure_not_splitting(project)
        spec = {"action": action, "outputs_dir": outputs_dir, "project_id": project.id,
                "cfg": project_cfg(project), "overrides": overrides, **params}
        if scene_id is not None:
            spec.update(scene_ids=[scene_id], force=True)  # tạo lại một cảnh: làm dù đang 'ready'
        label = JOB_LABELS[kind] + (f" cảnh {scene_id}" if scene_id else "")
        job, is_new = jobs.submit(project.id, kind, label, spec, scene_id)
        return JSONResponse({**job.to_dict(), "new": is_new}, status_code=202)

    def ensure_not_splitting(project: Project) -> None:
        # Đang chia cảnh lại thì mọi sửa đổi/tác vụ trên cảnh cũ sẽ vô nghĩa → chặn
        if jobs.has_active(project.id, ("split",)):
            raise HTTPException(409, "Dự án đang chia cảnh lại, đợi xong rồi làm tiếp")

    # ── Cài đặt chung ─────────────────────────────────────────────────────────
    @app.get("/api/settings")
    def get_settings():
        return {"default_voice": cfg["tts"]["voice"], "aspect_ratios": list(cfg["video"]["resolutions"]),
                "default_aspect_ratio": cfg["video"]["aspect_ratio"],
                "default_subtitles": cfg["subtitles"]["enabled"],
                "default_title": cfg["remotion"]["title"],
                # Remotion có sẵn → giao diện hiện tuỳ chọn "Màn hình tiêu đề"
                "render_engine": ("remotion" if cfg["render"].get("engine") == "remotion"
                                  and remotion_render.is_available() else "ffmpeg"),
                "scene_sec": [cfg["scenes"]["min_sec"], cfg["scenes"]["max_sec"]],
                "syllables_per_sec": cfg["scenes"]["syllables_per_sec"],  # để ước lượng giây khi đang gõ
                "image_backend": overrides.get("images.backend", cfg["images"]["backend"])}

    @app.get("/api/voices")
    def get_voices():
        return list_voices()

    @app.get("/api/health")
    def get_health():
        """Ollama có đang chạy và có model chưa — để giao diện cảnh báo trước khi bấm "Chia cảnh bằng AI"."""
        import json
        import urllib.request
        llm = cfg["llm"]
        try:
            with urllib.request.urlopen(llm["host"].rstrip("/") + "/api/tags", timeout=2) as r:
                names = [m["name"] for m in json.load(r).get("models", [])]
        except OSError:
            return {"ollama": False, "llm_model": False,
                    "message": "Ollama chưa chạy — mở ứng dụng Ollama để dùng \"Chia cảnh bằng AI\"."}
        has_model = llm["model"] in names
        return {"ollama": True, "llm_model": has_model,
                "message": None if has_model else f"Ollama chưa có model {llm['model']} (chạy: ollama pull {llm['model']})"}

    # ── Dự án ─────────────────────────────────────────────────────────────────
    @app.get("/api/projects")
    def get_projects():
        result = []
        for info in storage.list_projects(outputs_dir):
            p = Project(outputs_dir, info["id"])
            scenes = p.load_scenes()
            result.append({**info, "script": None, "scene_count": len(scenes),
                           "summary": summary(scenes), "busy": jobs.has_active(p.id)})
        return result

    @app.post("/api/projects", status_code=201)
    def post_project(body: ProjectCreate):
        if body.voice and body.voice not in {v["id"] for v in list_voices()}:
            raise HTTPException(422, f"Không có giọng '{body.voice}'")
        aspect = body.aspect_ratio or cfg["video"]["aspect_ratio"]
        settings = {
            "aspect_ratio": aspect,
            "resolution": list(video_size({**cfg, "video": {**cfg["video"], "aspect_ratio": aspect}})),
            "fps": cfg["video"]["fps"],
            "voice": body.voice or cfg["tts"]["voice"],
            "subtitles": cfg["subtitles"]["enabled"] if body.subtitles is None else body.subtitles,
            "title": cfg["remotion"]["title"] if body.title is None else body.title,
            "llm_model": cfg["llm"]["model"],
            "image_backend": overrides.get("images.backend", cfg["images"]["backend"]),
        }
        try:
            project = storage.create_project(outputs_dir, body.name.strip(), body.script.strip(), settings)
        except FileExistsError as e:
            raise HTTPException(409, str(e))
        return project_view(project)

    @app.get("/api/projects/{project_id}")
    def get_project_detail(project_id: str):
        return project_view(get_project(project_id))

    @app.patch("/api/projects/{project_id}")
    def patch_project(project_id: str, body: ProjectUpdate):
        project = get_project(project_id)
        if body.voice is not None and body.voice not in {v["id"] for v in list_voices()}:
            raise HTTPException(422, f"Không có giọng '{body.voice}'")
        voice_changed = output_changed = False

        def apply(info):
            nonlocal voice_changed, output_changed
            if body.name is not None:
                info["name"] = body.name.strip()
            if body.script is not None:
                info["script"] = body.script.strip()   # không tự chia lại; người dùng bấm "Chia cảnh"
            if body.voice is not None and body.voice != info["settings"].get("voice"):
                info["settings"]["voice"] = body.voice
                voice_changed = True
            if body.subtitles is not None and body.subtitles != info["settings"].get("subtitles"):
                info["settings"]["subtitles"] = body.subtitles
                output_changed = True
            music = info["settings"].get("music")
            if (body.music_volume is not None or body.music_duck is not None) and not music:
                raise HTTPException(409, "Dự án chưa có nhạc nền")
            if body.music_volume is not None and round(body.music_volume, 3) != music.get("volume"):
                music["volume"] = round(body.music_volume, 3)
                output_changed = True
            if body.music_duck is not None and body.music_duck != music.get("duck"):
                music["duck"] = body.music_duck
                output_changed = True
            if body.title is not None and body.title != info["settings"].get("title"):
                info["settings"]["title"] = body.title
                output_changed = True
        with project.lock:
            project.update_info(apply)
            if voice_changed:  # đổi giọng → mọi audio (và clip) đều cũ
                def stale_all(scenes):
                    for s in scenes:
                        mark_stale(s, "audio_status")
                        mark_stale(s, "clip_status")
                project.update_scenes(stale_all)
            if voice_changed or output_changed:
                project.mark_final_stale()
        return project_view(project)

    @app.delete("/api/projects/{project_id}", status_code=204)
    def delete_project(project_id: str):
        project = get_project(project_id)
        if jobs.has_active(project.id):
            raise HTTPException(409, "Dự án đang có tác vụ chạy/chờ, huỷ chúng trước")
        storage.delete_project(project)

    # ── Bước 1: kịch bản & cảnh ───────────────────────────────────────────────
    @app.post("/api/projects/{project_id}/split")
    def post_split(project_id: str, body: SplitRequest):
        project = get_project(project_id)
        if project.load_scenes() and not body.force:
            raise HTTPException(409, "Dự án đã có cảnh. Chia lại sẽ XOÁ ảnh/audio hiện có — gửi force=true")
        # Chia lại sẽ xoá thư mục cảnh → không được chạy cùng lúc với tác vụ khác của dự án
        if jobs.has_active(project.id, tuple(k for k in JOB_LABELS if k != "split")):
            raise HTTPException(409, "Dự án đang có tác vụ ảnh/audio/xuất chạy — đợi xong hoặc huỷ trước khi chia lại")
        return enqueue(project, "split", "split", mode=body.mode)

    @app.put("/api/projects/{project_id}/scenes")
    def put_scenes(project_id: str, body: ScenesReplace):
        """Lưu cả danh sách cảnh sau khi sửa / thêm / xoá / đổi thứ tự (thứ tự = thứ tự trong mảng)."""
        project = get_project(project_id)
        ensure_not_splitting(project)
        try:
            storage.apply_scene_edits(project, [s.model_dump() for s in body.scenes])
        except (KeyError, ValueError) as e:
            raise HTTPException(422, str(e).strip("'\""))
        return project_view(project)

    @app.patch("/api/projects/{project_id}/scenes/{scene_id}")
    def patch_scene(project_id: str, scene_id: str, body: SceneUpdate):
        project = get_project(project_id)
        ensure_not_splitting(project)
        with project.lock:  # đọc rồi ghi trong cùng một khoá: không lẫn với worker
            scenes = project.load_scenes()
            if not any(s["id"] == scene_id for s in scenes):
                raise HTTPException(404, "Không tìm thấy cảnh")
            edits = [SceneIn(id=s["id"],
                             narration=(body.narration if s["id"] == scene_id and body.narration is not None
                                        else s["narration"]),
                             image_prompt=(body.image_prompt if s["id"] == scene_id and body.image_prompt is not None
                                           else s["image_prompt"])).model_dump()
                     for s in scenes]
            storage.apply_scene_edits(project, edits)
        return scene_view(project, project.get_scene(scene_id))

    @app.delete("/api/projects/{project_id}/scenes/{scene_id}")
    def delete_scene(project_id: str, scene_id: str):
        project = get_project(project_id)
        ensure_not_splitting(project)
        with project.lock:
            scenes = project.load_scenes()
            if not any(s["id"] == scene_id for s in scenes):
                raise HTTPException(404, "Không tìm thấy cảnh")
            storage.apply_scene_edits(project, [
                {"id": s["id"], "narration": s["narration"], "image_prompt": s["image_prompt"]}
                for s in scenes if s["id"] != scene_id])
        return project_view(project)

    # ── Bước 2 & 3: ảnh, giọng đọc ────────────────────────────────────────────
    def require_scene(project: Project, scene_id: str) -> None:
        if project.get_scene(scene_id) is None:
            raise HTTPException(404, "Không tìm thấy cảnh")

    def require_scenes(project: Project) -> None:
        if not project.load_scenes():
            raise HTTPException(409, "Dự án chưa có cảnh — chia cảnh trước")

    @app.post("/api/projects/{project_id}/images")
    def post_images(project_id: str):
        project = get_project(project_id)
        require_scenes(project)
        return enqueue(project, "images", "images")

    @app.post("/api/projects/{project_id}/scenes/{scene_id}/image")
    def post_scene_image(project_id: str, scene_id: str):
        project = get_project(project_id)
        require_scene(project, scene_id)
        return enqueue(project, "image", "images", scene_id)

    @app.post("/api/projects/{project_id}/audio")
    def post_audio(project_id: str):
        project = get_project(project_id)
        require_scenes(project)
        return enqueue(project, "audio", "audio")

    @app.post("/api/projects/{project_id}/scenes/{scene_id}/audio")
    def post_scene_audio(project_id: str, scene_id: str):
        project = get_project(project_id)
        require_scene(project, scene_id)
        return enqueue(project, "audio_one", "audio", scene_id)

    # ── Bước 4: nhạc nền ──────────────────────────────────────────────────────
    MUSIC_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}

    def ensure_not_exporting(project: Project) -> None:
        # Đang xuất thì file nhạc đang được đọc → không thay/xoá giữa chừng
        if jobs.has_active(project.id, ("export",)):
            raise HTTPException(409, "Đang xuất video — đợi xong rồi đổi nhạc nền")

    @app.post("/api/projects/{project_id}/music")
    def post_music(project_id: str, file: UploadFile = File(...)):
        """Tải nhạc nền lên (multipart/form-data, trường "file"). Thay nhạc cũ nếu có."""
        project = get_project(project_id)
        ensure_not_exporting(project)
        ext = Path(file.filename or "").suffix.lower()
        if ext not in MUSIC_EXTS:
            raise HTTPException(422, f"Chỉ nhận file nhạc {', '.join(sorted(MUSIC_EXTS))}")
        max_bytes = int(cfg["music"]["max_upload_mb"] * 2**20)
        music_dir = project.dir / "music"
        music_dir.mkdir(exist_ok=True)
        tmp = music_dir / f"upload.tmp{ext}"
        size = 0
        try:
            with open(tmp, "wb") as f:  # đọc từng MB: không giữ cả file lớn trong RAM
                while chunk := file.file.read(2**20):
                    size += len(chunk)
                    if size > max_bytes:
                        raise HTTPException(413, f"File nhạc quá lớn (tối đa {cfg['music']['max_upload_mb']} MB)")
                    f.write(chunk)
            try:  # đuôi file có thể sai → hỏi ffprobe xem có thật là âm thanh đọc được không
                duration = render.media_duration(tmp)
            except Exception:
                raise HTTPException(422, "Không đọc được file âm thanh này")
            if duration < 1:
                raise HTTPException(422, "File nhạc quá ngắn")
            for old in music_dir.glob("background.*"):
                old.unlink()
            dst = music_dir / f"background{ext}"
            replace_file(tmp, dst)
        finally:
            tmp.unlink(missing_ok=True)

        def apply(info):
            prev = info["settings"].get("music") or {}
            info["settings"]["music"] = {
                "file": f"music/{dst.name}", "name": Path(file.filename).name,
                "duration_sec": round(duration, 1),
                # giữ âm lượng/ducking đã chỉnh nếu chỉ thay bài
                "volume": prev.get("volume", cfg["music"]["volume"]),
                "duck": prev.get("duck", cfg["music"]["duck"]),
            }
        with project.lock:
            project.update_info(apply)
            project.mark_final_stale()
        return project_view(project)

    @app.delete("/api/projects/{project_id}/music")
    def delete_music(project_id: str):
        project = get_project(project_id)
        ensure_not_exporting(project)
        with project.lock:
            def apply(info):
                info["settings"]["music"] = None
            project.update_info(apply)
            shutil.rmtree(project.dir / "music", ignore_errors=True)
            project.mark_final_stale()
        return project_view(project)

    # ── Bước 4: xuất video ────────────────────────────────────────────────────
    @app.get("/api/projects/{project_id}/export/check")
    def get_export_check(project_id: str):
        project = get_project(project_id)
        project.sync_status_with_files()
        scenes = project.load_scenes()
        problems = export_problems(scenes, fields=("image_status", "audio_status"))
        return {"ok": bool(scenes) and not problems, "problems": problems, "summary": summary(scenes)}

    @app.post("/api/projects/{project_id}/export")
    def post_export(project_id: str):
        project = get_project(project_id)
        project.sync_status_with_files()
        scenes = project.load_scenes()
        if not scenes:
            raise HTTPException(409, "Dự án chưa có cảnh")
        problems = export_problems(scenes, fields=("image_status", "audio_status"))
        if problems:
            # Lỗi có cấu trúc: giao diện dùng để liệt kê cảnh nào thiếu/lỗi và nhảy tới đó
            raise HTTPException(409, {"message": "Còn cảnh chưa sẵn sàng", "problems": problems})
        return enqueue(project, "export", "export")

    # ── Tác vụ ────────────────────────────────────────────────────────────────
    @app.get("/api/jobs")
    def get_jobs(project_id: str | None = Query(default=None)):
        return [j.to_dict() for j in jobs.list(project_id)]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Không tìm thấy tác vụ")
        return job.to_dict()

    @app.delete("/api/jobs/{job_id}")
    def cancel_job(job_id: str):
        job = jobs.cancel(job_id)
        if job is None:
            raise HTTPException(404, "Không tìm thấy tác vụ")
        return job.to_dict()

    # ── File tĩnh ─────────────────────────────────────────────────────────────
    # StaticFiles tự chặn đường dẫn kiểu ../ ra ngoài outputs/
    app.mount("/files", StaticFiles(directory=outputs_dir), name="files")
    if STATIC_DIR.is_dir():  # giao diện (giai đoạn 5)
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    return app


def main() -> None:
    import argparse
    import socket
    import threading
    import webbrowser

    import uvicorn
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    parser = argparse.ArgumentParser(description="Video Studio Local")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--open", action="store_true", help="mở trình duyệt khi server sẵn sàng")
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}"

    with socket.socket() as s:  # cổng đã bị chiếm → rất có thể app đang chạy sẵn
        if s.connect_ex(("127.0.0.1", args.port)) == 0:
            print(f"Cổng {args.port} đang được dùng — có thể app đã chạy rồi. Mở {url}")
            if args.open:
                webbrowser.open(url)
            return

    if args.open:
        def open_when_ready():
            for _ in range(60):  # đợi tối đa ~30s cho server khởi động xong
                with socket.socket() as s:
                    if s.connect_ex(("127.0.0.1", args.port)) == 0:
                        webbrowser.open(url)
                        return
                threading.Event().wait(0.5)
        threading.Thread(target=open_when_ready, daemon=True).start()

    print(f"Video Studio Local: {url}  (Ctrl+C để tắt)")
    # Chỉ nghe trên 127.0.0.1: app chỉ dành cho máy này, không mở ra mạng LAN
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
