"""Logic của các bước, dùng chung cho dòng lệnh (run_pipeline.py) và backend (app/main.py).

Mỗi hàm bước:
  - nhận RunContext để báo tiến độ (progress), ghi log, và kiểm tra yêu cầu huỷ (check)
  - đọc lại cảnh từ đĩa TRƯỚC mỗi lần cập nhật (project.update_scene) vì người dùng có thể
    đang sửa/xoá cảnh qua API trong lúc bước này chạy
  - tuân thủ quy tắc VRAM: load model → xử lý mọi cảnh cần làm → unload (kể cả khi lỗi/huỷ)

Đa ngôn ngữ: ảnh dùng chung; dịch, audio, clip, video làm riêng TỪNG ngôn ngữ (scene["langs"][lang]).
"""

import shutil
import time
from dataclasses import dataclass, field
from typing import Callable

from app.config import video_size
from app.storage import (Project, export_problems, make_scenes, mark_stale, needs_work, set_translation,
                         text_hash)
from pipeline import remotion_render, render, subtitles, whiteboard
from pipeline.gpu import report_vram


class Cancelled(Exception):
    """Người dùng huỷ tác vụ. Chỉ được ném ra ở ranh giới giữa hai cảnh → không để file dở."""


class StepError(RuntimeError):
    """Lỗi khiến cả bước không chạy được (khác với lỗi của riêng một cảnh)."""


@dataclass
class RunContext:
    progress: Callable[[int, int, str], None] = lambda done, total, msg: None
    log: Callable[[str], None] = print
    is_cancelled: Callable[[], bool] = lambda: False
    # Ghi đè cấu hình cho riêng lần chạy này (vd. {"images.backend": "placeholder"})
    overrides: dict = field(default_factory=dict)

    def check(self) -> None:
        if self.is_cancelled():
            raise Cancelled()


# ── Tiện ích ──────────────────────────────────────────────────────────────────
def _meta(project: Project) -> tuple[dict, str, list[str]]:
    info = project.load_info()
    return info, info["source_lang"], info["languages"]


def _targets(project: Project, langs: list[str] | None) -> list[str]:
    """Ngôn ngữ dịch (khác ngôn ngữ gốc) của dự án, lọc theo `langs` nếu có."""
    _, source, languages = _meta(project)
    return [l for l in languages if l != source and (langs is None or l in langs)]


def _select_images(project: Project, scene_ids: list[str] | None, force: bool) -> list[str]:
    return [s["id"] for s in project.load_scenes()
            if (scene_ids is None or s["id"] in scene_ids)
            and (needs_work(s, "image_status") or (force and scene_ids is not None))]


def _select_lang(project: Project, lang: str, field_name: str, scene_ids: list[str] | None,
                 force: bool) -> list[str]:
    """Id các cảnh cần làm `field_name` cho ngôn ngữ `lang`: chưa 'ready', hoặc (force) mọi cảnh chỉ định."""
    out = []
    for s in project.load_scenes():
        entry = s["langs"].get(lang)
        if entry is None or (scene_ids is not None and s["id"] not in scene_ids):
            continue
        if needs_work(entry, field_name) or (force and scene_ids is not None):
            out.append(s["id"])
    return out


def _lang_error(project: Project, scene_id: str, lang: str, field_name: str, step: str, err: Exception) -> None:
    def apply(s):
        s["langs"][lang][field_name] = "error"
        s["langs"][lang]["error"] = f"[{step}] {err}"
    project.update_scene(scene_id, apply)


def _clear_error(entry: dict, key: str, step: str) -> None:
    if entry.get(key) and entry[key].startswith(f"[{step}]"):
        entry[key] = None


def lang_view(scene: dict, lang: str) -> dict:
    """Cảnh nhìn từ một ngôn ngữ: {id, narration, duration_sec} — dạng mà whiteboard/subtitles cần."""
    entry = scene["langs"][lang]
    return {"id": scene["id"], "narration": entry["narration"], "duration_sec": entry["duration_sec"]}


# ── Điểm vào cho job (gọi trong tiến trình con, xem app/jobs.py) ──────────────
def run_job(spec: dict, ctx: RunContext):
    """spec là dữ liệu thuần (pickle được) mô tả việc cần làm:
    {action: split|translate|images|audio|export|suggest_shorts, outputs_dir, project_id, cfg,
     scene_ids?, force?, mode?, lang?, langs?}"""
    project = Project(spec["outputs_dir"], spec["project_id"])
    cfg = spec["cfg"]
    action = spec["action"]
    if action == "split":
        return len(split_scenes(project, cfg, ctx, spec.get("mode", "llm")))
    if action == "translate":
        return translate_scenes(project, cfg, ctx, spec.get("langs"), spec.get("scene_ids"), spec.get("force", False))
    if action == "images":
        return generate_images(project, cfg, ctx, spec.get("scene_ids"), spec.get("force", False))
    if action == "audio":
        return generate_audio(project, cfg, ctx, spec["lang"], spec.get("scene_ids"), spec.get("force", False))
    if action == "suggest_shorts":
        from pipeline import shorts
        return shorts.suggest(project, cfg, ctx)
    if action == "export":
        return export_video(project, cfg, ctx, spec["langs"])
    raise ValueError(f"action không hợp lệ: {action!r}")


# ── Bước 1: chia cảnh ─────────────────────────────────────────────────────────
def split_scenes(project: Project, cfg: dict, ctx: RunContext, mode: str = "llm") -> list[dict]:
    """Chia kịch bản thành cảnh, THAY THẾ toàn bộ cảnh cũ (xoá ảnh/audio/clip/video cũ)."""
    from pipeline import scenes as scene_splitter
    info, source, languages = _meta(project)
    script = info["script"]
    ctx.progress(0, 1, "Đang chia cảnh…")
    if mode == "paragraphs":
        items = scene_splitter.split_by_paragraphs(script)
    elif mode == "llm":
        report_vram("trước khi chạy LLM")
        items = scene_splitter.split_with_llm(script, cfg, ctx.progress, lang=source)
        report_vram("sau khi gỡ LLM")
    else:
        raise ValueError(f"mode không hợp lệ: {mode!r}")
    ctx.check()

    with project.lock:
        shutil.rmtree(project.dir / "scenes", ignore_errors=True)
        project.save_scenes(make_scenes(items, source, languages))
        for lang in languages:
            project.final_video(lang).unlink(missing_ok=True)
            project.set_final(lang, "missing")
    ctx.progress(1, 1, f"Đã chia thành {len(items)} cảnh")
    return project.load_scenes()


# ── Bước 1b: dịch lời đọc ─────────────────────────────────────────────────────
def translate_scenes(project: Project, cfg: dict, ctx: RunContext, langs: list[str] | None = None,
                     scene_ids: list[str] | None = None, force: bool = False) -> int:
    """Dịch lời gốc sang các ngôn ngữ dịch. Mặc định chỉ dịch cảnh chưa 'ready' và KHÔNG ghi đè
    bản dịch người dùng đã sửa tay (edited); force + scene_ids = dịch lại đúng các cảnh đó."""
    from pipeline import translate
    info, source, _ = _meta(project)
    targets = _targets(project, langs)
    plan = []  # [(lang, [scene_id, ...], cần dịch tiêu đề?)]
    for lang in targets:
        ids = []
        for s in project.load_scenes():
            entry = s["langs"].get(lang)
            if entry is None or (scene_ids is not None and s["id"] not in scene_ids):
                continue
            if force and scene_ids is not None:
                ids.append(s["id"])
            elif entry["text_status"] != "ready" and not entry["edited"]:
                ids.append(s["id"])
        plan.append((lang, ids, not info.get("titles", {}).get(lang)))
    chunk_size = cfg["translate"]["chunk_scenes"]
    total = sum(max(1, -(-len(ids) // chunk_size)) if ids else int(need_title) for _, ids, need_title in plan)
    if not total:
        ctx.progress(0, 0, "Bản dịch đã đầy đủ")
        return 0

    done, translated = 0, 0
    fake = ctx.overrides.get("translate.backend") == "fake"
    try:
        for lang, ids, need_title in plan:
            scenes = {s["id"]: s for s in project.load_scenes()}
            items = [{"id": i, "text": scenes[i]["langs"][source]["narration"]} for i in ids if i in scenes]
            batches = translate.chunks(items, chunk_size) or [[]]
            for b_index, batch in enumerate(batches):
                ctx.check()
                title = info["name"] if (need_title and b_index == 0) else None
                if not batch and not title:
                    continue
                ctx.progress(done, total, f"Đang dịch sang {cfg['languages'][lang]['name']} "
                                          f"({len(batch)} cảnh)…")
                # Chụp lại lời gốc lúc gửi đi: dịch xong mà lời gốc đã bị sửa thì bản dịch đó đã cũ
                hashes = {it["id"]: text_hash(it["text"]) for it in batch}
                try:
                    if fake:  # test không cần Ollama (xem translate.fake_translate)
                        result, new_title = translate.fake_translate(batch, lang, title)
                    elif batch:
                        result, new_title = translate.translate_items(batch, source, lang, cfg, title=title,
                                                                      log=ctx.log)
                    else:  # mọi cảnh đã dịch, chỉ còn thiếu tiêu đề → dịch nó như một mục
                        only_title, _ = translate.translate_items([{"id": "title", "text": title}],
                                                                  source, lang, cfg, log=ctx.log)
                        result, new_title = {}, only_title["title"]
                except translate.TranslateError as e:
                    if "không kết nối" in str(e):
                        raise StepError(str(e)) from e
                    for it in batch:
                        _lang_error(project, it["id"], lang, "text_status", "translate", e)
                    ctx.log(f"  LỖI dịch {lang}: {e}")
                    done += 1
                    continue
                for scene_id, text in result.items():
                    def apply(s, text=text, scene_id=scene_id):
                        if text_hash(s["langs"][source]["narration"]) == hashes[scene_id]:
                            set_translation(s, lang, text, source, by_user=False)
                    if project.update_scene(scene_id, apply) is not None:
                        translated += 1
                if new_title:
                    def set_title(info, t=new_title):
                        info.setdefault("titles", {})[lang] = t
                    project.update_info(set_title)
                project.mark_final_stale([lang])
                done += 1
                ctx.progress(done, total, f"{cfg['languages'][lang]['name']}: đã dịch {len(result)} cảnh")
    finally:
        if not fake:
            translate.unload(cfg)  # Quy tắc VRAM: xong là gỡ LLM khỏi bộ nhớ, kể cả khi lỗi/huỷ
    ctx.progress(total, total, f"Đã dịch {translated} cảnh")
    return translated


# ── Bước 3: giọng đọc (chạy trước ảnh vì clip cần thời lượng audio) ──────────
def generate_audio(project: Project, cfg: dict, ctx: RunContext, lang: str,
                   scene_ids: list[str] | None = None, force: bool = False) -> None:
    info, source, _ = _meta(project)
    lang_name = cfg["languages"][lang]["name"]
    todo = []
    for scene_id in _select_lang(project, lang, "audio_status", scene_ids, force):
        entry = project.get_scene(scene_id)["langs"][lang]
        # Bản dịch chưa có / đã cũ thì chưa đọc được (đọc bản cũ chỉ phí công)
        if lang == source or (entry["text_status"] == "ready" and entry["narration"]):
            todo.append(scene_id)
    if not todo:
        ctx.progress(0, 0, f"{lang_name}: không có audio nào cần tạo")
        return
    from pipeline.tts import TTSModel
    voice = info.get("settings", {}).get("voices", {}).get(lang)
    ctx.progress(0, len(todo), f"Đang load giọng đọc {lang_name}…")
    with TTSModel(cfg, lang, voice) as tts:  # CPU/ONNX — không dùng VRAM
        for n, scene_id in enumerate(todo, 1):
            ctx.check()
            scene = project.get_scene(scene_id)
            if scene is None:      # bị xoá trong lúc chờ
                continue
            text = scene["langs"][lang]["narration"]  # chụp lại lời đọc dùng để tạo audio
            t0 = time.perf_counter()
            try:
                duration = tts.generate_audio(text, project.lang_dir(scene_id, lang) / "audio.wav")
            except Exception as e:
                _lang_error(project, scene_id, lang, "audio_status", "tts", e)
                ctx.log(f"  LỖI audio {lang} cảnh {scene_id}: {e}")
                ctx.progress(n, len(todo), f"Cảnh {scene_id}: lỗi")
                continue

            def apply(s):
                entry = s["langs"][lang]
                entry["duration_sec"] = round(duration, 3)
                # Người dùng sửa lời đọc trong lúc đang tạo → audio vừa tạo đã cũ ngay
                entry["audio_status"] = "ready" if entry["narration"] == text else "stale"
                _clear_error(entry, "error", "tts")
                mark_stale(entry, "clip_status")
            project.update_scene(scene_id, apply)
            project.mark_final_stale([lang])
            ctx.progress(n, len(todo), f"{lang_name} cảnh {scene_id}: {duration:.1f}s audio "
                                       f"({time.perf_counter() - t0:.1f}s)")


# ── Bước 2: ảnh (dùng chung mọi ngôn ngữ) ─────────────────────────────────────
def generate_images(project: Project, cfg: dict, ctx: RunContext,
                    scene_ids: list[str] | None = None, force: bool = False) -> None:
    from pipeline.images import load_image_model, unload_image_model
    todo = _select_images(project, scene_ids, force)
    if not todo:
        ctx.progress(0, 0, "Tất cả ảnh đã sẵn sàng")
        return
    img_cfg = {**cfg["images"]}
    if ctx.overrides.get("images.backend"):
        img_cfg["backend"] = ctx.overrides["images.backend"]
    width, height = video_size(cfg)
    ctx.progress(0, len(todo), "Đang load model ảnh…")
    model = load_image_model({**cfg, "images": img_cfg}, width, height)
    try:
        for n, scene_id in enumerate(todo, 1):
            ctx.check()
            scene = project.get_scene(scene_id)
            if scene is None:
                continue
            prompt = scene["image_prompt"]
            t0 = time.perf_counter()
            try:
                if img_cfg["backend"] != "placeholder" and not prompt.strip():
                    raise ValueError("thiếu image_prompt")
                model.generate_image(prompt + img_cfg["style_suffix"],
                                     project.scene_dir(scene_id) / "image.png", label=scene_id)
            except Exception as e:
                def fail(s, e=e):
                    s["image_status"] = "error"
                    s["image_error"] = f"[images] {e}"
                project.update_scene(scene_id, fail)
                ctx.log(f"  LỖI ảnh cảnh {scene_id}: {e}")
                ctx.progress(n, len(todo), f"Cảnh {scene_id}: lỗi")
                continue

            def apply(s):
                s["image_status"] = "ready" if s["image_prompt"] == prompt else "stale"
                s["image_error"] = None
                for entry in s["langs"].values():  # ảnh đổi → clip MỌI ngôn ngữ phải vẽ lại
                    mark_stale(entry, "clip_status")
            project.update_scene(scene_id, apply)
            project.mark_final_stale()
            ctx.progress(n, len(todo), f"Cảnh {scene_id}: xong ({time.perf_counter() - t0:.1f}s)")
    finally:
        unload_image_model(model)  # Quy tắc VRAM: xong bước là gỡ model, kể cả khi lỗi/huỷ


# ── Bước 4a: clip animation (riêng từng ngôn ngữ — độ dài theo audio) ────────
def render_clips(project: Project, cfg: dict, ctx: RunContext, lang: str,
                 scene_ids: list[str] | None = None, progress_offset: tuple[int, int] = (0, 0)) -> None:
    """progress_offset = (đã xong, tổng) của cả tác vụ khi bước này là một phần của "Xuất"."""
    whiteboard.check_ready(cfg["whiteboard"])
    long_edge = max(video_size(cfg))  # render đúng độ phân giải video (không bị giới hạn 1080)
    todo = _select_lang(project, lang, "clip_status", scene_ids, force=False)
    base, total = progress_offset if progress_offset[1] else (0, len(todo))
    for n, scene_id in enumerate(todo, 1):
        ctx.check()
        scene = project.get_scene(scene_id)
        if scene is None:
            continue
        entry = scene["langs"][lang]
        if scene["image_status"] != "ready" or entry["audio_status"] != "ready":
            ctx.log(f"  Bỏ qua clip {lang} cảnh {scene_id}: cần ảnh và audio 'ready'")
            continue
        out_dir = project.lang_dir(scene_id, lang)
        whiteboard.remove_stale_temp(out_dir)
        ctx.progress(base + n - 1, total, f"Đang vẽ clip {lang} cảnh {scene_id}…")
        t0 = time.perf_counter()
        try:
            whiteboard.render_clip(project.scene_dir(scene_id) / "image.png", lang_view(scene, lang),
                                   out_dir / "clip.mp4", cfg["whiteboard"], cfg["video"]["fps"], long_edge)
        except Exception as e:
            _lang_error(project, scene_id, lang, "clip_status", "clips", e)
            ctx.log(f"  LỖI clip {lang} cảnh {scene_id}: {e}")
            continue

        def apply(s):
            s["langs"][lang]["clip_status"] = "ready"
            _clear_error(s["langs"][lang], "error", "clips")
        project.update_scene(scene_id, apply)
        project.mark_final_stale([lang])
        ctx.progress(base + n, total, f"{lang} cảnh {scene_id}: clip xong ({time.perf_counter() - t0:.1f}s)")


# ── Bước 4: xuất video (clip còn thiếu + ghép), cho một hoặc nhiều ngôn ngữ ──
FINAL_UNITS = 10  # bước ghép chiếm 10 "đơn vị" tiến độ → thanh tiến độ nhích đều theo khung hình


def describe_problems(problems: list[dict]) -> str:
    return "; ".join(f"{p['lang']} cảnh {p['id']} ({', '.join(f'{k.split('_')[0]}={v}' for k, v in p['not_ready'].items())})"
                     for p in problems)


def export_video(project: Project, cfg: dict, ctx: RunContext, langs: list[str]) -> dict[str, float]:
    info, source, languages = _meta(project)
    langs = [l for l in languages if l in langs]  # giữ thứ tự của dự án
    if not langs:
        raise StepError("Chưa chọn ngôn ngữ nào để xuất")
    scenes = project.load_scenes()
    if not scenes:
        raise StepError("Dự án chưa có cảnh nào")
    # Ảnh + bản dịch + audio của mọi ngôn ngữ được chọn phải đủ hết; clip thì bước này tự tạo
    problems = [p for lang in langs for p in export_problems(scenes, lang, source)]
    if problems:
        raise StepError("Chưa thể xuất video: " + describe_problems(problems))

    clips_todo = {lang: len(_select_lang(project, lang, "clip_status", None, False)) for lang in langs}
    total = sum(clips_todo.values()) + FINAL_UNITS * len(langs)
    done, durations = 0, {}
    for lang in langs:
        render_clips(project, cfg, ctx, lang, progress_offset=(done, total))
        done += clips_todo[lang]
        ctx.check()
        compose = _compose_short if info.get("kind") == "short" else _compose
        durations[lang] = compose(project, cfg, ctx, lang, (done, total))
        done += FINAL_UNITS
    ctx.progress(total, total, "Xong: " + ", ".join(f"{l} {d:.1f}s" for l, d in durations.items()))
    return durations


def _compose(project: Project, cfg: dict, ctx: RunContext, lang: str, progress: tuple[int, int]) -> float:
    """Ghép clip + audio (+ tiêu đề, chuyển cảnh, phụ đề, nhạc) của MỘT ngôn ngữ → final.<lang>.mp4."""
    info, source, languages = _meta(project)
    base, total = progress
    scenes = project.load_scenes()
    problems = export_problems(scenes, lang, source, include_clips=True)
    if problems:
        raise StepError("Có cảnh chưa tạo được clip: " + describe_problems(problems))

    width, height = video_size(cfg)
    fps = cfg["video"]["fps"]
    settings = info.get("settings", {})
    lang_cfg = cfg["languages"][lang]
    project.set_final(lang, "stale" if project.final_video(lang).is_file() else "missing")
    parts = [(project.lang_dir(s["id"], lang) / "clip.mp4", project.lang_dir(s["id"], lang) / "audio.wav")
             for s in scenes]
    durations = [render.media_duration(clip) for clip, _ in parts]
    sub_cfg = cfg["subtitles"]
    max_words = lang_cfg["subtitle_max_words"]
    if height > width:
        max_words = max(5, max_words * 2 // 3)

    # Phụ đề song ngữ: dòng phụ = ngôn ngữ còn lại có bản dịch đầy đủ (mặc định: ngôn ngữ gốc ↔ bản dịch đầu tiên)
    secondary = None
    if settings.get("subtitles_bilingual") and len(languages) > 1:
        candidates = [source] if lang != source else [l for l in languages if l != source]
        for cand in candidates:
            if all(s["langs"].get(cand, {}).get("narration") for s in scenes):
                secondary = cand
                break
        if not secondary:
            ctx.log(f"  Phụ đề song ngữ: chưa có đủ bản {candidates} → chỉ phụ đề {lang}")
    views = []
    for s in scenes:
        v = lang_view(s, lang)
        if secondary:
            v["secondary"] = s["langs"][secondary]["narration"]
        views.append(v)

    # Remotion (tiêu đề + chuyển cảnh + phụ đề đẹp) nếu đã cài; không thì ffmpeg (đơn giản, nhanh)
    engine = cfg["render"].get("engine", "ffmpeg")
    if engine == "remotion" and not remotion_render.is_available():
        ctx.log("  Remotion chưa cài (remotion/node_modules) → dùng ffmpeg")
        engine = "ffmpeg"

    # Nhạc nền (tuỳ chọn, tải lên ở bước 4): dùng chung mọi ngôn ngữ
    music = settings.get("music")
    music_path = project.dir / music["file"] if music else None
    if music_path is not None and not music_path.is_file():
        ctx.log(f"  Không thấy file nhạc {music_path} → xuất không có nhạc nền")
        music = music_path = None

    final = project.final_video(lang)
    name = lang_cfg["name"]
    if engine == "remotion":
        rcfg = cfg["remotion"]
        title = (info.get("titles", {}).get(lang) or info["name"]) if rcfg["title"] else None
        # Remotion chỉ đọc file trong --public-dir (= scenes/) → chép tạm nhạc vào đó trong lúc render
        bgm = project.dir / "scenes" / f"_bgm{music_path.suffix}" if music_path else None
        props, cues = remotion_render.build_timeline(
            views, durations, lang=lang, fps=fps, width=width, height=height, title=title, rcfg=rcfg,
            sub_cfg=sub_cfg, subtitles_on=sub_cfg["enabled"], max_words=max_words,
            music={"src": bgm.name, "volume": music["volume"], "duck": music["duck"]} if music else None,
            music_cfg=cfg["music"])
        ctx.progress(base, total, f"Remotion {name}: đang chuẩn bị ({len(scenes)} cảnh)…")

        def on_progress(frac: float, msg: str) -> None:
            ctx.progress(base + min(FINAL_UNITS - 1, int(frac * FINAL_UNITS)), total, f"{name} — {msg}")
        try:
            if bgm:
                shutil.copyfile(music_path, bgm)
            remotion_render.render(props, project.dir / f"remotion_props.{lang}.json", project.dir / "scenes",
                                   final, rcfg, cfg["render"]["audio_bitrate"], on_progress)
        finally:
            if bgm:
                bgm.unlink(missing_ok=True)
        duration = render.media_duration(final)
        burned = bool(props["subtitles"])
    else:
        ctx.progress(base, total, f"Đang ghép {len(scenes)} cảnh {name} (ffmpeg)…")
        cues = subtitles.build_cues(views, durations, max_words)
        ass_file = None
        if sub_cfg["enabled"]:
            ass_file = project.dir / f"final.{lang}.ass"
            subtitles.write_ass(cues, ass_file, width, height, sub_cfg)
        duration = render.concat_scenes(parts, final, width, height, fps, cfg["render"]["audio_bitrate"],
                                        durations=durations, subtitles_file=ass_file,
                                        fonts_dir=sub_cfg.get("fonts_dir"),
                                        music={"path": music_path, "volume": music["volume"],
                                               "duck": music["duck"],
                                               "fade_in_sec": cfg["music"]["fade_in_sec"],
                                               "fade_out_sec": cfg["music"]["fade_out_sec"]} if music else None)
        burned = bool(ass_file)

    # Phụ đề để tải về: luôn có bản một ngôn ngữ; thêm bản song ngữ nếu đang bật
    subtitles.write_srt(cues, project.srt_path(lang), with_secondary=False)
    if secondary:
        subtitles.write_srt(cues, project.srt_path(lang, secondary))

    # Trong lúc ghép, người dùng có thể đã sửa cảnh hoặc (làn GPU chạy song song) tạo lại ảnh
    # → video vừa ghép đã cũ. "ready" chỉ khi các cảnh y nguyên VÀ mọi thứ vẫn ready.
    def sig(ss):
        return [(s["id"], s["langs"][lang]["narration"], s["image_prompt"]) for s in ss]
    now = project.load_scenes()
    still_same = sig(scenes) == sig(now) and not export_problems(now, lang, source, include_clips=True)
    project.set_final(lang, "ready" if still_same else "stale", duration_sec=round(duration, 2),
                      subtitles=burned, bilingual=secondary, engine=engine, music=bool(music))
    return duration


def _compose_short(project: Project, cfg: dict, ctx: RunContext, lang: str, progress: tuple[int, int]) -> float:
    """Ghép một SHORT (dự án con, khung 9:16): clip đặt giữa khung dọc, tiêu đề cố định phía trên,
    phụ đề lớn, câu kết có giọng đọc + màn hình kết dẫn về video đầy đủ. Chỉ làm bằng Remotion."""
    from pipeline import shorts
    if not remotion_render.is_available():
        raise StepError("Xuất Short cần Remotion — chạy 'npm install' trong thư mục remotion/ (xem README)")
    info, source, languages = _meta(project)
    base, total = progress
    scenes = project.load_scenes()
    problems = export_problems(scenes, lang, source, include_clips=True)
    if problems:
        raise StepError("Có cảnh chưa tạo được clip: " + describe_problems(problems))

    ctx.progress(base, total, f"Short {lang}: đang đọc câu kết…")
    outro_sec = shorts.ensure_outro_audio(project, cfg, lang, ctx)
    info = project.load_info()
    settings, lang_cfg = info.get("settings", {}), cfg["languages"][lang]
    width, height = video_size(cfg)
    fps = cfg["video"]["fps"]
    project.set_final(lang, "stale" if project.final_video(lang).is_file() else "missing")
    clips = [project.lang_dir(s["id"], lang) / "clip.mp4" for s in scenes]
    durations = [render.media_duration(c) for c in clips]
    # Khung của clip (clip giữ tỉ lệ ảnh gốc; ảnh tạo lại trong Short thì đã là ảnh dọc)
    probe = render.video_size(clips[0])
    clip_aspect = probe[0] / probe[1] if probe else 16 / 9
    max_words = cfg["shorts"]["subtitle_max_words"].get(lang, lang_cfg["subtitle_max_words"])

    secondary = None
    if settings.get("subtitles_bilingual") and len(languages) > 1:
        cand = source if lang != source else next(l for l in languages if l != source)
        if all(s["langs"].get(cand, {}).get("narration") for s in scenes):
            secondary = cand
    views = []
    for s in scenes:
        v = lang_view(s, lang)
        if secondary:
            v["secondary"] = s["langs"][secondary]["narration"]
        views.append(v)

    music = settings.get("music")
    music_path = project.dir / music["file"] if music else None
    if music_path is not None and not music_path.is_file():
        music = music_path = None
    # Remotion chỉ đọc file trong --public-dir (= scenes/) → chép tạm nhạc + câu kết vào đó
    bgm = project.dir / "scenes" / f"_bgm{music_path.suffix}" if music_path else None
    outro_tmp = project.dir / "scenes" / f"_outro_{lang}.wav"
    props, cues = remotion_render.build_short_timeline(
        views, durations, lang=lang, fps=fps, width=width, height=height,
        title=info.get("titles", {}).get(lang) or info["name"], clip_aspect=clip_aspect,
        outro={"src": outro_tmp.name, "duration_sec": outro_sec, "text": info["outro"][lang]["text"],
               "parent_title": info.get("parent", {}).get("titles", {}).get(lang) or info.get("parent", {}).get("name", "")},
        rcfg=cfg["remotion"], sub_cfg=cfg["subtitles"], shorts_cfg=cfg["shorts"],
        subtitles_on=cfg["subtitles"]["enabled"], max_words=max_words,
        music={"src": bgm.name, "volume": music["volume"], "duck": music["duck"]} if music else None,
        music_cfg=cfg["music"])
    name = lang_cfg["name"]

    def on_progress(frac: float, msg: str) -> None:
        ctx.progress(base + min(FINAL_UNITS - 1, int(frac * FINAL_UNITS)), total, f"Short {name} — {msg}")
    final = project.final_video(lang)
    try:
        shutil.copyfile(shorts.outro_path(project, lang), outro_tmp)
        if bgm:
            shutil.copyfile(music_path, bgm)
        remotion_render.render(props, project.dir / f"remotion_props.{lang}.json", project.dir / "scenes",
                               final, cfg["remotion"], cfg["render"]["audio_bitrate"], on_progress,
                               composition="ShortVideo")
    finally:
        outro_tmp.unlink(missing_ok=True)
        if bgm:
            bgm.unlink(missing_ok=True)
    duration = render.media_duration(final)
    subtitles.write_srt(cues, project.srt_path(lang), with_secondary=False)
    if secondary:
        subtitles.write_srt(cues, project.srt_path(lang, secondary))

    def sig(ss):
        return [(s["id"], s["langs"][lang]["narration"], s["image_prompt"]) for s in ss]
    now = project.load_scenes()
    still_same = sig(scenes) == sig(now) and not export_problems(now, lang, source, include_clips=True)
    project.set_final(lang, "ready" if still_same else "stale", duration_sec=round(duration, 2),
                      subtitles=bool(props["subtitles"]), bilingual=secondary, engine="remotion", music=bool(music))
    return duration
