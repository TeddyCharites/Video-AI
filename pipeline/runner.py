"""Logic của các bước, dùng chung cho dòng lệnh (run_pipeline.py) và backend (app/main.py).

Mỗi hàm bước:
  - nhận RunContext để báo tiến độ (progress), ghi log, và kiểm tra yêu cầu huỷ (check)
  - đọc lại cảnh từ đĩa TRƯỚC mỗi lần cập nhật (project.update_scene) vì người dùng có thể
    đang sửa/xoá cảnh qua API trong lúc bước này chạy
  - tuân thủ quy tắc VRAM: load model → xử lý mọi cảnh cần làm → unload (kể cả khi lỗi/huỷ)
"""

import shutil
import time
from dataclasses import dataclass, field
from typing import Callable

from app.config import video_size
from app.storage import (Project, export_problems, make_scenes, mark_stale, needs_work)
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


def _select(project: Project, field_name: str, scene_ids: list[str] | None, force: bool) -> list[str]:
    """Id các cảnh cần làm: cảnh chưa 'ready', hoặc (force) mọi cảnh được chỉ định."""
    out = []
    for s in project.load_scenes():
        if scene_ids is not None and s["id"] not in scene_ids:
            continue
        if needs_work(s, field_name) or (force and scene_ids is not None):
            out.append(s["id"])
    return out


def _set_error(project: Project, scene_id: str, field_name: str, step: str, err: Exception) -> None:
    def apply(s):
        s[field_name] = "error"
        s["error"] = f"[{step}] {err}"
    project.update_scene(scene_id, apply)


def _clear_error(scene: dict, step: str) -> None:
    if scene["error"] and scene["error"].startswith(f"[{step}]"):
        scene["error"] = None


# ── Điểm vào cho job (gọi trong tiến trình con, xem app/jobs.py) ──────────────
def run_job(spec: dict, ctx: RunContext):
    """spec là dữ liệu thuần (pickle được) mô tả việc cần làm:
    {action: split|images|audio|export, outputs_dir, project_id, cfg, scene_ids?, force?, mode?}"""
    project = Project(spec["outputs_dir"], spec["project_id"])
    cfg = spec["cfg"]
    action = spec["action"]
    if action == "split":
        return len(split_scenes(project, cfg, ctx, spec.get("mode", "llm")))
    if action == "images":
        return generate_images(project, cfg, ctx, spec.get("scene_ids"), spec.get("force", False))
    if action == "audio":
        return generate_audio(project, cfg, ctx, spec.get("scene_ids"), spec.get("force", False))
    if action == "export":
        return export_video(project, cfg, ctx)
    raise ValueError(f"action không hợp lệ: {action!r}")


# ── Bước 1: chia cảnh ─────────────────────────────────────────────────────────
def split_scenes(project: Project, cfg: dict, ctx: RunContext, mode: str = "llm") -> list[dict]:
    """Chia kịch bản thành cảnh, THAY THẾ toàn bộ cảnh cũ (xoá ảnh/audio/clip cũ)."""
    from pipeline import scenes as scene_splitter
    script = project.load_info()["script"]
    ctx.progress(0, 1, "Đang chia cảnh…")
    if mode == "paragraphs":
        items = scene_splitter.split_by_paragraphs(script)
    elif mode == "llm":
        report_vram("trước khi chạy LLM")
        items = scene_splitter.split_with_llm(script, cfg, ctx.progress)
        report_vram("sau khi gỡ LLM")
    else:
        raise ValueError(f"mode không hợp lệ: {mode!r}")
    ctx.check()

    with project.lock:
        shutil.rmtree(project.dir / "scenes", ignore_errors=True)
        project.save_scenes(make_scenes(items))
        project.final_video.unlink(missing_ok=True)
        project.set_final_status("missing")
    ctx.progress(1, 1, f"Đã chia thành {len(items)} cảnh")
    return project.load_scenes()


# ── Bước 3: giọng đọc (chạy trước ảnh vì clip cần thời lượng audio) ──────────
def generate_audio(project: Project, cfg: dict, ctx: RunContext,
                   scene_ids: list[str] | None = None, force: bool = False) -> None:
    todo = _select(project, "audio_status", scene_ids, force)
    if not todo:
        ctx.progress(0, 0, "Tất cả audio đã sẵn sàng")
        return
    from pipeline.tts import TTSModel
    ctx.progress(0, len(todo), "Đang load model giọng đọc…")
    with TTSModel(cfg["tts"]) as tts:  # CPU/ONNX — không dùng VRAM
        for n, scene_id in enumerate(todo, 1):
            ctx.check()
            scene = project.get_scene(scene_id)
            if scene is None:      # bị xoá trong lúc chờ
                continue
            text = scene["narration"]  # chụp lại lời đọc dùng để tạo audio
            t0 = time.perf_counter()
            try:
                duration = tts.generate_audio(text, project.scene_dir(scene_id) / "audio.wav")
            except Exception as e:
                _set_error(project, scene_id, "audio_status", "tts", e)
                ctx.log(f"  LỖI audio cảnh {scene_id}: {e}")
                ctx.progress(n, len(todo), f"Cảnh {scene_id}: lỗi")
                continue

            def apply(s):
                s["duration_sec"] = round(duration, 3)
                # Người dùng sửa lời đọc trong lúc đang tạo → audio vừa tạo đã cũ ngay
                s["audio_status"] = "ready" if s["narration"] == text else "stale"
                _clear_error(s, "tts")
                mark_stale(s, "clip_status")
            project.update_scene(scene_id, apply)
            project.mark_final_stale()
            ctx.progress(n, len(todo), f"Cảnh {scene_id}: {duration:.1f}s audio "
                                       f"({time.perf_counter() - t0:.1f}s)")


# ── Bước 2: ảnh ───────────────────────────────────────────────────────────────
def generate_images(project: Project, cfg: dict, ctx: RunContext,
                    scene_ids: list[str] | None = None, force: bool = False) -> None:
    from pipeline.images import load_image_model, unload_image_model
    todo = _select(project, "image_status", scene_ids, force)
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
                _set_error(project, scene_id, "image_status", "images", e)
                ctx.log(f"  LỖI ảnh cảnh {scene_id}: {e}")
                ctx.progress(n, len(todo), f"Cảnh {scene_id}: lỗi")
                continue

            def apply(s):
                s["image_status"] = "ready" if s["image_prompt"] == prompt else "stale"
                _clear_error(s, "images")
                mark_stale(s, "clip_status")
            project.update_scene(scene_id, apply)
            project.mark_final_stale()
            ctx.progress(n, len(todo), f"Cảnh {scene_id}: xong ({time.perf_counter() - t0:.1f}s)")
    finally:
        unload_image_model(model)  # Quy tắc VRAM: xong bước là gỡ model, kể cả khi lỗi/huỷ


# ── Bước 4a: clip animation ───────────────────────────────────────────────────
def render_clips(project: Project, cfg: dict, ctx: RunContext,
                 scene_ids: list[str] | None = None, progress_offset: tuple[int, int] = (0, 0)) -> None:
    """progress_offset = (đã xong, tổng) của cả tác vụ khi bước này là một phần của "Xuất"."""
    whiteboard.check_ready(cfg["whiteboard"])
    long_edge = max(video_size(cfg))  # render đúng độ phân giải video (không bị giới hạn 1080)
    todo = _select(project, "clip_status", scene_ids, force=False)
    base, total = progress_offset if progress_offset[1] else (0, len(todo))
    for n, scene_id in enumerate(todo, 1):
        ctx.check()
        scene = project.get_scene(scene_id)
        if scene is None:
            continue
        if scene["image_status"] != "ready" or scene["audio_status"] != "ready":
            ctx.log(f"  Bỏ qua clip cảnh {scene_id}: cần ảnh và audio 'ready'")
            continue
        scene_dir = project.scene_dir(scene_id)
        whiteboard.remove_stale_temp(scene_dir)
        ctx.progress(base + n - 1, total, f"Đang vẽ clip cảnh {scene_id}…")
        t0 = time.perf_counter()
        try:
            whiteboard.render_clip(scene_dir / "image.png", scene, scene_dir / "clip.mp4",
                                   cfg["whiteboard"], cfg["video"]["fps"], long_edge)
        except Exception as e:
            _set_error(project, scene_id, "clip_status", "clips", e)
            ctx.log(f"  LỖI clip cảnh {scene_id}: {e}")
            continue

        def apply(s):
            s["clip_status"] = "ready"
            _clear_error(s, "clips")
        project.update_scene(scene_id, apply)
        project.mark_final_stale()
        ctx.progress(base + n, total, f"Cảnh {scene_id}: clip xong ({time.perf_counter() - t0:.1f}s)")


# ── Bước 4: xuất video (clip còn thiếu + ghép) ────────────────────────────────
def export_video(project: Project, cfg: dict, ctx: RunContext) -> float:
    scenes = project.load_scenes()
    if not scenes:
        raise StepError("Dự án chưa có cảnh nào")
    # Ảnh + audio phải đủ hết; clip thì bước này tự tạo
    problems = export_problems(scenes, fields=("image_status", "audio_status"))
    if problems:
        raise StepError("Chưa thể xuất video: " + "; ".join(
            f"cảnh {p['id']} ({', '.join(f'{k.split('_')[0]}={v}' for k, v in p['not_ready'].items())})"
            for p in problems))

    clips_todo = len(_select(project, "clip_status", None, False))
    FINAL_UNITS = 10  # bước ghép chiếm 10 "đơn vị" tiến độ → thanh tiến độ nhích đều theo khung hình
    total = clips_todo + FINAL_UNITS
    render_clips(project, cfg, ctx, progress_offset=(0, total))
    ctx.check()

    scenes = project.load_scenes()
    problems = export_problems(scenes)
    if problems:
        raise StepError("Có cảnh chưa tạo được clip: " + ", ".join(p["id"] for p in problems))

    width, height = video_size(cfg)
    fps = cfg["video"]["fps"]
    project.set_final_status("stale" if project.final_video.is_file() else "missing")
    parts = [(project.scene_dir(s["id"]) / "clip.mp4", project.scene_dir(s["id"]) / "audio.wav") for s in scenes]
    durations = [render.media_duration(clip) for clip, _ in parts]
    sub_cfg = cfg["subtitles"]
    max_words = sub_cfg["max_words"] if width >= height else max(6, sub_cfg["max_words"] * 2 // 3)

    # Remotion (tiêu đề + chuyển cảnh + phụ đề đẹp) nếu đã cài; không thì ffmpeg (đơn giản, nhanh)
    engine = cfg["render"].get("engine", "ffmpeg")
    if engine == "remotion" and not remotion_render.is_available():
        ctx.log("  Remotion chưa cài (remotion/node_modules) → dùng ffmpeg")
        engine = "ffmpeg"

    # Nhạc nền (tuỳ chọn, tải lên ở bước 4): lưu trong project.json → settings.music
    music = project.load_info().get("settings", {}).get("music")
    music_path = project.dir / music["file"] if music else None
    if music_path is not None and not music_path.is_file():
        ctx.log(f"  Không thấy file nhạc {music_path} → xuất không có nhạc nền")
        music = music_path = None

    if engine == "remotion":
        rcfg = cfg["remotion"]
        title = project.load_info()["name"] if rcfg["title"] else None
        # Remotion chỉ đọc file trong --public-dir (= scenes/) → chép tạm nhạc vào đó trong lúc render
        bgm = project.dir / "scenes" / f"_bgm{music_path.suffix}" if music_path else None
        props, cues = remotion_render.build_timeline(
            scenes, durations, fps=fps, width=width, height=height, title=title, rcfg=rcfg,
            sub_cfg=sub_cfg, subtitles_on=sub_cfg["enabled"], max_words=max_words,
            music={"src": bgm.name, "volume": music["volume"], "duck": music["duck"]} if music else None,
            music_cfg=cfg["music"])
        subtitles.write_srt(cues, project.dir / "final.srt")
        ctx.progress(clips_todo, total, f"Remotion: đang chuẩn bị ({len(scenes)} cảnh)…")

        def on_progress(frac: float, msg: str) -> None:
            ctx.progress(clips_todo + min(FINAL_UNITS - 1, int(frac * FINAL_UNITS)), total, msg)
        try:
            if bgm:
                shutil.copyfile(music_path, bgm)
            remotion_render.render(props, project.dir / "remotion_props.json", project.dir / "scenes",
                                   project.final_video, rcfg, cfg["render"]["audio_bitrate"], on_progress)
        finally:
            if bgm:
                bgm.unlink(missing_ok=True)
        duration = render.media_duration(project.final_video)
        burned = bool(props["subtitles"])
    else:
        ctx.progress(clips_todo, total, f"Đang ghép {len(scenes)} cảnh (ffmpeg)…")
        # Phụ đề: luôn ghi final.srt (để tải về / đưa lên YouTube); chỉ chèn vào hình nếu bật
        cues = subtitles.build_cues(scenes, durations, max_words)
        subtitles.write_srt(cues, project.dir / "final.srt")
        ass_file = None
        if sub_cfg["enabled"]:
            ass_file = project.dir / "final.ass"
            subtitles.write_ass(cues, ass_file, width, height, sub_cfg)
        duration = render.concat_scenes(parts, project.final_video, width, height,
                                        fps, cfg["render"]["audio_bitrate"],
                                        durations=durations, subtitles_file=ass_file,
                                        fonts_dir=sub_cfg.get("fonts_dir"),
                                        music={"path": music_path, "volume": music["volume"],
                                               "duck": music["duck"],
                                               "fade_in_sec": cfg["music"]["fade_in_sec"],
                                               "fade_out_sec": cfg["music"]["fade_out_sec"]} if music else None)
        burned = bool(ass_file)

    # Trong lúc ghép, người dùng có thể đã sửa cảnh hoặc (làn GPU chạy song song) tạo lại ảnh
    # → video vừa ghép đã cũ. "ready" chỉ khi danh sách cảnh y nguyên VÀ mọi thứ vẫn ready.
    now = project.load_scenes()
    still_same = [(s["id"], s["narration"], s["image_prompt"]) for s in scenes] == \
                 [(s["id"], s["narration"], s["image_prompt"]) for s in now] and not export_problems(now)
    project.set_final_status("ready" if still_same else "stale", final_duration_sec=round(duration, 2),
                             final_subtitles=burned, final_engine=engine, final_music=bool(music))
    ctx.progress(total, total, f"Xong: {duration:.1f}s video")
    return duration
