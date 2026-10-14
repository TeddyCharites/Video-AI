"""Backend FastAPI của Video Studio Local.

Chạy:  .venv\\Scripts\\python -m app.main          → http://127.0.0.1:8000  (tài liệu API: /docs)

Mọi tác vụ nặng (chia cảnh, dịch, ảnh, audio, xuất) KHÔNG chạy trong request: endpoint chỉ xếp job
vào hàng đợi (app/jobs.py) rồi trả về ngay (HTTP 202). Frontend hỏi tiến độ bằng GET /api/jobs.

Đa ngôn ngữ: ảnh dùng chung; lời đọc / bản dịch / audio / clip / video theo từng ngôn ngữ (?lang=…).
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

from app import languages as langs
from app import storage
from app.config import apply_runtime_env, load_config, video_size
from app.jobs import JobQueue
from app.schemas import (AudioRequest, ExportRequest, ProjectCreate, ProjectUpdate, ScenesReplace,
                         SceneUpdate, ShortCreate, SplitRequest, TranslateRequest)
from app.storage import Project, export_problems, mark_stale, replace_file
from pipeline import remotion_render, render, shorts
from pipeline.tts import list_voices

STATIC_DIR = Path(__file__).resolve().parent / "static"
JOB_LABELS = {"split": "Chia cảnh", "translate": "Dịch lời đọc", "translate_one": "Dịch lại",
              "images": "Tạo ảnh", "image": "Tạo lại ảnh",
              "audio": "Tạo giọng đọc", "audio_one": "Tạo lại giọng đọc", "export": "Xuất video",
              "suggest_shorts": "Gợi ý Shorts"}


def create_app(cfg: dict | None = None, overrides: dict | None = None) -> FastAPI:
    """Factory: test có thể truyền cfg riêng (thư mục outputs tạm) và overrides
    (vd. {"images.backend": "placeholder"} để không cần GPU)."""
    cfg = cfg or load_config()
    apply_runtime_env(cfg)
    overrides = overrides or {}
    outputs_dir: Path = cfg["outputs_dir"]
    outputs_dir.mkdir(parents=True, exist_ok=True)
    # Dự án định dạng cũ (v1, chỉ tiếng Việt) → tự chuyển sang v2 (có sao lưu *.v1.bak)
    migrated = storage.migrate_all(outputs_dir, cfg["languages"]["vi"]["voice"])
    if migrated:
        print(f"Đã chuyển {len(migrated)} dự án sang định dạng đa ngôn ngữ: {', '.join(migrated)}")
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

    def check_lang(project: Project, lang: str | None) -> str:
        """Ngôn ngữ hợp lệ của dự án (None = ngôn ngữ gốc)."""
        info = project.load_info()
        lang = lang or info["source_lang"]
        if lang not in info["languages"]:
            raise HTTPException(422, f"Dự án không có ngôn ngữ {lang!r} (có: {', '.join(info['languages'])})")
        return lang

    def check_voice(lang: str, voice: str) -> None:
        if voice not in {v["id"] for v in list_voices(cfg, lang)}:
            raise HTTPException(422, f"Không có giọng {voice!r} cho {langs.name(cfg, lang)}")

    def project_cfg(project: Project) -> dict:
        """Cấu hình cho riêng dự án: tỉ lệ khung hình, phụ đề, tiêu đề lấy từ project.json
        (giọng theo ngôn ngữ thì runner đọc thẳng từ project.json)."""
        pcfg = copy.deepcopy(cfg)
        settings = project.load_info().get("settings", {})
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
        view = {**s, "image_url": file_url(project, f"{base}/image.png"), "langs": {}}
        for lang, entry in s["langs"].items():
            view["langs"][lang] = {**entry,
                                   "audio_url": file_url(project, f"{base}/{lang}/audio.wav"),
                                   "clip_url": file_url(project, f"{base}/{lang}/clip.mp4")}
        return view

    def summary(scenes: list[dict], info: dict) -> dict:
        """images: ảnh (dùng chung); langs.<mã>: text (bản dịch) / audio / clips + tổng thời lượng."""
        n = len(scenes)

        def count(get) -> dict:
            not_ready = [s["id"] for s in scenes if get(s) != "ready"]
            return {"ready": n - len(not_ready), "total": n, "not_ready_ids": not_ready}
        per_lang = {}
        for lang in info["languages"]:
            def field(name, lang=lang):
                return lambda s: s["langs"].get(lang, {}).get(name, "missing")
            known = [s["langs"][lang]["duration_sec"] for s in scenes
                     if s["langs"].get(lang, {}).get("duration_sec")]
            per_lang[lang] = {"text": count(field("text_status")), "audio": count(field("audio_status")),
                              "clips": count(field("clip_status")),
                              "total_duration_sec": round(sum(known), 2) if known else None}
        return {"images": count(lambda s: s["image_status"]), "langs": per_lang}

    def project_view(project: Project) -> dict:
        project.sync_status_with_files()
        info = project.load_info()
        scenes = project.load_scenes()
        source = info["source_lang"]
        finals = {}
        for lang in info["languages"]:
            f = dict(info["finals"].get(lang, {"status": "missing"}))
            f["url"] = file_url(project, f"final.{lang}.mp4")
            f["srt_url"] = file_url(project, f"final.{lang}.srt")
            f["srt_bilingual_url"] = (file_url(project, f"final.{lang}-{f['bilingual']}.srt")
                                      if f.get("bilingual") else None)
            finals[lang] = f
        return {
            **info,
            "finals": finals,
            "scenes": [scene_view(project, s) for s in scenes],
            "summary": summary(scenes, info),
            # Bước 4: ảnh + bản dịch + audio phải ready hết; clip thì "Xuất" tự tạo
            "export_problems": {lang: export_problems(scenes, lang, source) for lang in info["languages"]},
            "music_url": (file_url(project, info["settings"]["music"]["file"])
                          if info.get("settings", {}).get("music") else None),
            "jobs": [j.to_dict() for j in jobs.list(project.id)],
            **short_links(project, info),
        }

    def short_links(project: Project, info: dict) -> dict:
        """Dự án gốc: danh sách Short con. Short: thông tin dự án gốc + cảnh nào ở gốc đã đổi."""
        out = {"kind": info.get("kind", "long")}
        if info.get("kind") == "short":
            parent = shorts.parent_project(project, info)
            out["parent_info"] = {**info.get("parent", {}), "exists": parent is not None,
                                  "changes": shorts.parent_changes(project, info) if parent else []}
        else:
            items = []
            for cid in info.get("shorts", []):
                try:
                    child = Project(outputs_dir, cid)
                except ValueError:
                    continue
                if child.exists():
                    ci = child.load_info()
                    items.append({"id": cid, "titles": ci.get("titles", {}),
                                  "scene_ids": [s.get("parent_scene") for s in child.load_scenes()],
                                  "finals": {l: (ci["finals"].get(l) or {}).get("status", "missing")
                                             for l in ci["languages"]}})
            out["shorts"] = items
        return out

    def enqueue(project: Project, kind: str, action: str, scene_id: str | None = None,
                lang: str | None = None, label_extra: str = "", **params) -> JSONResponse:
        """Xếp job. spec là dữ liệu thuần vì job chạy trong TIẾN TRÌNH CON (app/jobs.py)."""
        if kind != "split":
            ensure_not_splitting(project)
        spec = {"action": action, "outputs_dir": outputs_dir, "project_id": project.id,
                "cfg": project_cfg(project), "overrides": overrides, **params}
        if lang is not None:
            spec["lang"] = lang
        if scene_id is not None:
            spec.update(scene_ids=[scene_id], force=True)  # tạo lại một cảnh: làm dù đang 'ready'
        label = JOB_LABELS[kind] + (f" cảnh {scene_id}" if scene_id else "") + label_extra
        job, is_new = jobs.submit(project.id, kind, label, spec, scene_id, lang)
        return JSONResponse({**job.to_dict(), "new": is_new}, status_code=202)

    def ensure_not_splitting(project: Project) -> None:
        # Đang chia cảnh lại thì mọi sửa đổi/tác vụ trên cảnh cũ sẽ vô nghĩa → chặn
        if jobs.has_active(project.id, ("split",)):
            raise HTTPException(409, "Dự án đang chia cảnh lại, đợi xong rồi làm tiếp")

    # ── Cài đặt chung ─────────────────────────────────────────────────────────
    @app.get("/api/settings")
    def get_settings():
        return {"languages": langs.public_info(cfg),
                "default_source_lang": cfg["default_source_lang"],
                "default_target_langs": cfg["default_target_langs"],
                "aspect_ratios": list(cfg["video"]["resolutions"]),
                "default_aspect_ratio": cfg["video"]["aspect_ratio"],
                "default_subtitles": cfg["subtitles"]["enabled"],
                "default_title": cfg["remotion"]["title"],
                # Remotion có sẵn → giao diện hiện tuỳ chọn "Màn hình tiêu đề"
                "render_engine": ("remotion" if cfg["render"].get("engine") == "remotion"
                                  and remotion_render.is_available() else "ffmpeg"),
                "scene_sec": [cfg["scenes"]["min_sec"], cfg["scenes"]["max_sec"]],
                "short_sec": [cfg["shorts"]["min_sec"], cfg["shorts"]["max_sec"]],
                "image_backend": overrides.get("images.backend", cfg["images"]["backend"])}

    @app.get("/api/voices")
    def get_voices(lang: str = Query(default=None)):
        lang = lang or cfg["default_source_lang"]
        try:
            langs.get(cfg, lang)  # ném ValueError nếu ngôn ngữ không được hỗ trợ
        except ValueError as e:
            raise HTTPException(422, str(e))
        return list_voices(cfg, lang)

    @app.get("/api/health")
    def get_health():
        """Ollama có đang chạy và có model chưa — để giao diện cảnh báo trước khi bấm "Chia cảnh"/"Dịch"."""
        import json
        import urllib.request
        llm = cfg["llm"]
        try:
            with urllib.request.urlopen(llm["host"].rstrip("/") + "/api/tags", timeout=2) as r:
                names = [m["name"] for m in json.load(r).get("models", [])]
        except OSError:
            return {"ollama": False, "llm_model": False,
                    "message": "Ollama chưa chạy — mở ứng dụng Ollama để dùng \"Chia cảnh bằng AI\" và \"Dịch\"."}
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
                           "summary": summary(scenes, info), "busy": jobs.has_active(p.id),
                           "parent_id": (info.get("parent") or {}).get("id")})
        return result

    @app.post("/api/projects", status_code=201)
    def post_project(body: ProjectCreate):
        source = body.source_lang or cfg["default_source_lang"]
        targets = cfg["default_target_langs"] if body.languages is None else body.languages
        try:
            languages = langs.validate(cfg, [source, *targets])  # ném ValueError nếu có mã lạ
        except ValueError as e:
            raise HTTPException(422, str(e))
        voices = {lang: langs.get(cfg, lang)["voice"] for lang in languages}
        for lang, voice in (body.voices or {}).items():
            if lang in voices:
                check_voice(lang, voice)
                voices[lang] = voice
        aspect = body.aspect_ratio or cfg["video"]["aspect_ratio"]
        settings = {
            "aspect_ratio": aspect,
            "resolution": list(video_size({**cfg, "video": {**cfg["video"], "aspect_ratio": aspect}})),
            "fps": cfg["video"]["fps"],
            "voices": voices,
            "subtitles": cfg["subtitles"]["enabled"] if body.subtitles is None else body.subtitles,
            "subtitles_bilingual": body.subtitles_bilingual,
            "title": cfg["remotion"]["title"] if body.title is None else body.title,
            "llm_model": cfg["llm"]["model"],
            "image_backend": overrides.get("images.backend", cfg["images"]["backend"]),
        }
        try:
            project = storage.create_project(outputs_dir, body.name.strip(), body.script.strip(), settings,
                                             source, languages)
        except FileExistsError as e:
            raise HTTPException(409, str(e))
        return project_view(project)

    @app.get("/api/projects/{project_id}")
    def get_project_detail(project_id: str):
        return project_view(get_project(project_id))

    @app.patch("/api/projects/{project_id}")
    def patch_project(project_id: str, body: ProjectUpdate):
        project = get_project(project_id)
        info = project.load_info()
        source = info["source_lang"]
        for lang, voice in (body.voices or {}).items():
            check_lang(project, lang)
            check_voice(lang, voice)
        new_languages = None
        if body.languages is not None:
            try:
                new_languages = langs.validate(cfg, [source, *body.languages])
            except ValueError as e:
                raise HTTPException(422, str(e))
            if jobs.has_active(project.id):
                raise HTTPException(409, "Dự án đang có tác vụ chạy — đợi xong rồi đổi ngôn ngữ")
        voice_changed: list[str] = []
        stale_langs: set[str] = set()

        def apply(info):
            if body.name is not None:
                info["name"] = body.name.strip()
                info.setdefault("titles", {})[source] = info["name"]
            if body.script is not None:
                info["script"] = body.script.strip()   # không tự chia lại; người dùng bấm "Chia cảnh"
            for lang, voice in (body.voices or {}).items():
                if info["settings"].setdefault("voices", {}).get(lang) != voice:
                    info["settings"]["voices"][lang] = voice
                    voice_changed.append(lang)
            for lang, text in (body.titles or {}).items():
                if lang in info["languages"] and text.strip() and info.setdefault("titles", {}).get(lang) != text.strip():
                    info["titles"][lang] = text.strip()
                    stale_langs.add(lang)
            for key in ("subtitles", "subtitles_bilingual", "title"):
                value = getattr(body, key)
                if value is not None and value != info["settings"].get(key):
                    info["settings"][key] = value
                    stale_langs.update(info["languages"])
            music = info["settings"].get("music")
            if (body.music_volume is not None or body.music_duck is not None) and not music:
                raise HTTPException(409, "Dự án chưa có nhạc nền")
            if body.music_volume is not None and round(body.music_volume, 3) != music.get("volume"):
                music["volume"] = round(body.music_volume, 3)
                stale_langs.update(info["languages"])
            if body.music_duck is not None and body.music_duck != music.get("duck"):
                music["duck"] = body.music_duck
                stale_langs.update(info["languages"])
            if new_languages is not None and new_languages != info["languages"]:
                for lang in new_languages:
                    info["finals"].setdefault(lang, {"status": "missing"})
                    info["settings"].setdefault("voices", {}).setdefault(lang, langs.get(cfg, lang)["voice"])
                for lang in set(info["languages"]) - set(new_languages):
                    info["finals"].pop(lang, None)
                info["languages"] = new_languages

        with project.lock:
            old_languages = info["languages"]
            project.update_info(apply)
            if voice_changed or new_languages is not None:
                def update_scenes(scenes):
                    for s in scenes:
                        for lang in voice_changed:  # đổi giọng → audio (và clip) ngôn ngữ đó đều cũ
                            mark_stale(s["langs"][lang], "audio_status")
                            mark_stale(s["langs"][lang], "clip_status")
                        if new_languages is not None:
                            storage.ensure_languages(s, source, new_languages)
                            for lang in set(s["langs"]) - set(new_languages):
                                del s["langs"][lang]
                project.update_scenes(update_scenes)
            if new_languages is not None:  # bỏ ngôn ngữ → xoá file của nó
                for lang in set(old_languages) - set(new_languages):
                    for d in (project.dir / "scenes").glob(f"*/{lang}"):
                        shutil.rmtree(d, ignore_errors=True)
                    for f in project.dir.glob(f"final.{lang}.*"):
                        f.unlink(missing_ok=True)
            stale_langs.update(voice_changed)
            if stale_langs:
                project.mark_final_stale(sorted(stale_langs & set(project.languages())))
        if body.cta:
            if info.get("kind") != "short":
                raise HTTPException(422, "Chỉ Short mới có câu kết")
            shorts.write_outro_text(project, body.cta)
        return project_view(project)

    @app.delete("/api/projects/{project_id}", status_code=204)
    def delete_project(project_id: str):
        project = get_project(project_id)
        if jobs.has_active(project.id):
            raise HTTPException(409, "Dự án đang có tác vụ chạy/chờ, huỷ chúng trước")
        parent = shorts.parent_project(project)
        storage.delete_project(project)
        if parent is not None:  # xoá Short → bỏ khỏi danh sách Short của dự án gốc
            def unlink(info):
                info["shorts"] = [x for x in info.get("shorts", []) if x != project_id]
            parent.update_info(unlink)

    # ── Bước 1: kịch bản, cảnh, bản dịch ──────────────────────────────────────
    @app.post("/api/projects/{project_id}/split")
    def post_split(project_id: str, body: SplitRequest):
        project = get_project(project_id)
        if project.load_info().get("kind") == "short":
            raise HTTPException(409, "Short lấy cảnh từ dự án gốc — không chia cảnh lại")
        if project.load_scenes() and not body.force:
            raise HTTPException(409, "Dự án đã có cảnh. Chia lại sẽ XOÁ ảnh/audio hiện có — gửi force=true")
        # Chia lại sẽ xoá thư mục cảnh → không được chạy cùng lúc với tác vụ khác của dự án
        if jobs.has_active(project.id, tuple(k for k in JOB_LABELS if k != "split")):
            raise HTTPException(409, "Dự án đang có tác vụ dịch/ảnh/audio/xuất chạy — đợi xong hoặc huỷ trước khi chia lại")
        return enqueue(project, "split", "split", mode=body.mode)

    def scene_edits(scenes: list[dict], source: str) -> list[dict]:
        return [{"id": s["id"], "narration": s["langs"][source]["narration"], "image_prompt": s["image_prompt"]}
                for s in scenes]

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
            source = project.source_lang()
            scenes = project.load_scenes()
            if not any(s["id"] == scene_id for s in scenes):
                raise HTTPException(404, "Không tìm thấy cảnh")
            edits = scene_edits(scenes, source)
            for e in edits:
                if e["id"] == scene_id:
                    if body.narration is not None:
                        e["narration"] = body.narration
                    if body.image_prompt is not None:
                        e["image_prompt"] = body.image_prompt
                    e["translations"] = body.translations
            try:
                storage.apply_scene_edits(project, edits)
            except ValueError as e:
                raise HTTPException(422, str(e))
        return scene_view(project, project.get_scene(scene_id))

    @app.post("/api/projects/{project_id}/scenes/{scene_id}/translations/{lang}/keep")
    def keep_translation(project_id: str, scene_id: str, lang: str):
        """Giữ bản dịch hiện tại (đã cũ vì lời gốc đổi) — coi như người dùng xác nhận nó vẫn đúng."""
        project = get_project(project_id)
        lang = check_lang(project, lang)
        source = project.source_lang()
        if lang == source:
            raise HTTPException(422, "Ngôn ngữ gốc không có bản dịch")
        scene = project.update_scene(scene_id, lambda s: storage.set_translation(
            s, lang, s["langs"][lang]["narration"], source, by_user=True))
        if scene is None:
            raise HTTPException(404, "Không tìm thấy cảnh")
        return scene_view(project, scene)

    @app.delete("/api/projects/{project_id}/scenes/{scene_id}")
    def delete_scene(project_id: str, scene_id: str):
        project = get_project(project_id)
        ensure_not_splitting(project)
        with project.lock:
            scenes = project.load_scenes()
            if not any(s["id"] == scene_id for s in scenes):
                raise HTTPException(404, "Không tìm thấy cảnh")
            storage.apply_scene_edits(project, [e for e in scene_edits(scenes, project.source_lang())
                                                if e["id"] != scene_id])
        return project_view(project)

    def require_scene(project: Project, scene_id: str) -> None:
        if project.get_scene(scene_id) is None:
            raise HTTPException(404, "Không tìm thấy cảnh")

    def require_scenes(project: Project) -> None:
        if not project.load_scenes():
            raise HTTPException(409, "Dự án chưa có cảnh — chia cảnh trước")

    def target_langs(project: Project, wanted: list[str] | None) -> list[str]:
        info = project.load_info()
        targets = [l for l in info["languages"] if l != info["source_lang"]]
        if wanted is not None:
            bad = [l for l in wanted if l not in targets]
            if bad:
                raise HTTPException(422, f"Không phải ngôn ngữ dịch của dự án: {', '.join(bad)}")
            targets = [l for l in targets if l in wanted]
        if not targets:
            raise HTTPException(409, "Dự án chưa có ngôn ngữ dịch nào")
        return targets

    @app.post("/api/projects/{project_id}/translate")
    def post_translate(project_id: str, body: TranslateRequest):
        project = get_project(project_id)
        require_scenes(project)
        targets = target_langs(project, body.langs)
        return enqueue(project, "translate", "translate", langs=targets,
                       label_extra=" → " + ", ".join(langs.name(cfg, l) for l in targets))

    @app.post("/api/projects/{project_id}/scenes/{scene_id}/translate")
    def post_scene_translate(project_id: str, scene_id: str, body: TranslateRequest):
        project = get_project(project_id)
        require_scene(project, scene_id)
        targets = target_langs(project, body.langs)
        return enqueue(project, "translate_one", "translate", scene_id, langs=targets,
                       label_extra=" → " + ", ".join(langs.name(cfg, l) for l in targets))

    # ── Shorts (từ dự án dài) ─────────────────────────────────────────────────
    @app.post("/api/projects/{project_id}/shorts/suggest")
    def post_suggest_shorts(project_id: str):
        project = get_project(project_id)
        require_scenes(project)
        if project.load_info().get("kind") == "short":
            raise HTTPException(409, "Không tạo Short từ một Short")
        return enqueue(project, "suggest_shorts", "suggest_shorts")

    @app.post("/api/projects/{project_id}/shorts", status_code=201)
    def post_short(project_id: str, body: ShortCreate):
        """Tạo Short (dự án con 9:16) từ các cảnh liền nhau — dùng lại ảnh / audio / clip có sẵn."""
        parent = get_project(project_id)
        ensure_not_splitting(parent)
        try:
            with parent.lock:
                child = shorts.create_short(parent, cfg, body.scene_ids, body.titles, body.cta)
        except shorts.ShortsError as e:
            raise HTTPException(422, str(e))
        return project_view(child)

    @app.post("/api/projects/{project_id}/sync-parent")
    def post_sync_parent(project_id: str):
        """Short: chép lại từ dự án gốc các cảnh đã đổi."""
        project = get_project(project_id)
        if jobs.has_active(project.id):
            raise HTTPException(409, "Short đang có tác vụ chạy — đợi xong rồi cập nhật")
        try:
            n = shorts.sync_from_parent(project)
        except shorts.ShortsError as e:
            raise HTTPException(409, str(e))
        return {**project_view(project), "synced": n}

    # ── Bước 2 & 3: ảnh, giọng đọc ────────────────────────────────────────────
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
    def post_audio(project_id: str, body: AudioRequest | None = None):
        project = get_project(project_id)
        require_scenes(project)
        lang = check_lang(project, body.lang if body else None)
        return enqueue(project, "audio", "audio", lang=lang, label_extra=f" ({langs.name(cfg, lang)})")

    @app.post("/api/projects/{project_id}/scenes/{scene_id}/audio")
    def post_scene_audio(project_id: str, scene_id: str, body: AudioRequest | None = None):
        project = get_project(project_id)
        require_scene(project, scene_id)
        lang = check_lang(project, body.lang if body else None)
        scene = project.get_scene(scene_id)
        if lang != project.source_lang() and scene["langs"][lang]["text_status"] != "ready":
            raise HTTPException(409, f"Cảnh {scene_id} chưa có bản dịch {langs.name(cfg, lang)} — dịch trước")
        return enqueue(project, "audio_one", "audio", scene_id, lang=lang,
                       label_extra=f" ({langs.name(cfg, lang)})")

    # ── Bước 4: nhạc nền (dùng chung mọi ngôn ngữ) ────────────────────────────
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

    # ── Bước 4: xuất video (một hoặc nhiều ngôn ngữ) ─────────────────────────
    def export_langs(project: Project, wanted: list[str] | None) -> list[str]:
        info = project.load_info()
        if wanted is None:
            return info["languages"]
        for lang in wanted:
            check_lang(project, lang)
        chosen = [l for l in info["languages"] if l in wanted]
        if not chosen:
            raise HTTPException(422, "Chưa chọn ngôn ngữ nào để xuất")
        return chosen

    @app.get("/api/projects/{project_id}/export/check")
    def get_export_check(project_id: str, langs_: str | None = Query(default=None, alias="langs")):
        """?langs=vi,en — cảnh nào còn thiếu ảnh / bản dịch / audio cho từng ngôn ngữ."""
        project = get_project(project_id)
        project.sync_status_with_files()
        chosen = export_langs(project, langs_.split(",") if langs_ else None)
        scenes, source = project.load_scenes(), project.source_lang()
        problems = [p for lang in chosen for p in export_problems(scenes, lang, source)]
        return {"ok": bool(scenes) and not problems, "langs": chosen, "problems": problems}

    @app.post("/api/projects/{project_id}/export")
    def post_export(project_id: str, body: ExportRequest | None = None):
        project = get_project(project_id)
        project.sync_status_with_files()
        chosen = export_langs(project, body.langs if body else None)
        scenes, source = project.load_scenes(), project.source_lang()
        if not scenes:
            raise HTTPException(409, "Dự án chưa có cảnh")
        problems = [p for lang in chosen for p in export_problems(scenes, lang, source)]
        if problems:
            # Lỗi có cấu trúc: giao diện dùng để liệt kê cảnh nào thiếu/lỗi và nhảy tới đó
            raise HTTPException(409, {"message": "Còn cảnh chưa sẵn sàng", "problems": problems})
        return enqueue(project, "export", "export", langs=chosen,
                       label_extra=" (" + ", ".join(langs.name(cfg, l) for l in chosen) + ")")

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
    if STATIC_DIR.is_dir():  # giao diện
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
