"""Pipeline dòng lệnh — kịch bản → final.<lang>.mp4. Logic từng bước nằm ở pipeline/runner.py
(dùng chung với backend FastAPI); file này chỉ đọc tham số và in tiến độ.

Chạy:
    .venv\\Scripts\\python run_pipeline.py script.txt                                 # vi + ngôn ngữ dịch mặc định
    .venv\\Scripts\\python run_pipeline.py script.txt --langs vi                      # chỉ tiếng Việt
    .venv\\Scripts\\python run_pipeline.py script.txt --no-llm --placeholder-images   # test nhanh, không GPU
    .venv\\Scripts\\python run_pipeline.py --project-id <id>                          # chạy tiếp dự án cũ
    .venv\\Scripts\\python run_pipeline.py --project-id <id> --steps tts,clips,final  # chỉ vài bước

Thứ tự bước và tài nguyên (chỉ 1 model nằm trong VRAM tại một thời điểm):
    scenes (GPU/Ollama) → translate (GPU/Ollama) → tts (CPU) → images (GPU/FLUX) → clips (CPU) → final (CPU)
TTS chạy trước ảnh vì animation cần biết thời lượng audio thật của từng cảnh.

Chạy lại sẽ bỏ qua phần đã "ready", chỉ làm phần missing / stale / error.
"""

import argparse
import sys
import time
from pathlib import Path

from app import languages as langs
from app.config import apply_runtime_env, load_config, video_size
from app.storage import Project, create_project
from pipeline import runner

STEPS = ["scenes", "translate", "tts", "images", "clips", "final"]
STEP_LABELS = {"scenes": "1. Chia cảnh", "translate": "2. Dịch lời đọc", "tts": "3. Giọng đọc",
               "images": "4. Tạo ảnh", "clips": "5. Animation vẽ bảng", "final": "6. Ghép video"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Video Studio Local — pipeline dòng lệnh")
    p.add_argument("script", nargs="?", type=Path, help="File kịch bản .txt (UTF-8)")
    p.add_argument("--project-id", help="Chạy tiếp một dự án đã có trong outputs/")
    p.add_argument("--name", help="Tên dự án (mặc định: tên file kịch bản)")
    p.add_argument("--source-lang", help="Ngôn ngữ của kịch bản (mặc định: default_source_lang trong config)")
    p.add_argument("--langs", help="Ngôn ngữ của dự án mới / ngôn ngữ cần làm, vd. vi,en "
                                   "(mặc định: ngôn ngữ gốc + default_target_langs)")
    p.add_argument("--steps", default=",".join(STEPS), help=f"Các bước cần chạy, mặc định: {','.join(STEPS)}")
    p.add_argument("--resplit", action="store_true", help="Chia cảnh lại dù đã có scenes.json (mất ảnh/audio cũ)")
    p.add_argument("--no-llm", action="store_true", help="Chia cảnh theo đoạn văn, không gọi LLM")
    p.add_argument("--placeholder-images", action="store_true", help="Ảnh giả bằng Pillow thay cho FLUX")
    args = p.parse_args()
    args.steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    args.langs = [l.strip() for l in args.langs.split(",") if l.strip()] if args.langs else None
    unknown = set(args.steps) - set(STEPS)
    if unknown:
        p.error(f"bước không hợp lệ: {', '.join(sorted(unknown))}")
    if not args.script and not args.project_id:
        p.error("cần file kịch bản hoặc --project-id")
    return args


def open_project(args, cfg: dict) -> Project:
    if args.project_id:
        project = Project(cfg["outputs_dir"], args.project_id)
        if not project.exists():
            raise SystemExit(f"Không tìm thấy dự án {project.dir}")
        return project
    script_text = args.script.read_text(encoding="utf-8").strip()
    if not script_text:
        raise SystemExit(f"File kịch bản rỗng: {args.script}")
    source = args.source_lang or cfg["default_source_lang"]
    languages = langs.validate(cfg, [source, *(args.langs or cfg["default_target_langs"])])
    settings = {
        "aspect_ratio": cfg["video"]["aspect_ratio"],
        "resolution": list(video_size(cfg)),
        "fps": cfg["video"]["fps"],
        "voices": {lang: cfg["languages"][lang]["voice"] for lang in languages},
        "subtitles": cfg["subtitles"]["enabled"],
        "subtitles_bilingual": False,
        "llm_model": None if args.no_llm else cfg["llm"]["model"],
        "image_backend": cfg["images"]["backend"],
    }
    return create_project(cfg["outputs_dir"], args.name or args.script.stem, script_text, settings,
                          source, languages)


def print_progress(done: int, total: int, msg: str) -> None:
    print(f"  [{done}/{total}] {msg}" if total else f"  {msg}", flush=True)


def main() -> int:
    # Console Windows mặc định cp1252 → in tiếng Việt bị UnicodeEncodeError
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args()
    cfg = load_config()
    apply_runtime_env(cfg)

    from app.storage import migrate_all
    migrate_all(cfg["outputs_dir"], cfg["languages"]["vi"]["voice"])  # dự án định dạng cũ → v2
    project = open_project(args, cfg)
    print(f"Dự án: {project.id}\nThư mục: {project.dir}")
    project.sync_status_with_files()
    # Dự án cũ dùng tỉ lệ/phụ đề đã lưu lúc tạo, không phải giá trị hiện tại trong config.yaml
    info = project.load_info()
    settings, source = info.get("settings", {}), info["source_lang"]
    work_langs = [l for l in info["languages"] if args.langs is None or l in args.langs]
    print(f"Ngôn ngữ: {', '.join(work_langs)} (gốc: {source})")
    cfg["video"]["aspect_ratio"] = settings.get("aspect_ratio") or cfg["video"]["aspect_ratio"]
    if settings.get("subtitles") is not None:
        cfg["subtitles"]["enabled"] = settings["subtitles"]
    if settings.get("title") is not None:
        cfg["remotion"]["title"] = settings["title"]

    ctx = runner.RunContext(progress=print_progress)
    if args.placeholder_images:
        ctx.overrides["images.backend"] = "placeholder"

    def step_scenes():
        if project.load_scenes() and not args.resplit:
            print("  Đã có scenes.json → bỏ qua (dùng --resplit để chia lại)")
            return
        for s in runner.split_scenes(project, cfg, ctx, "paragraphs" if args.no_llm else "llm"):
            text = s["langs"][source]["narration"]
            print(f"  {s['id']}. ({len(text.split())} tiếng) {text[:70]}…")
            if s["image_prompt"]:
                print(f"      ảnh: {s['image_prompt'][:90]}")

    def step_translate():
        targets = [l for l in work_langs if l != source]
        if targets:
            runner.translate_scenes(project, cfg, ctx, targets)
        else:
            print("  Không có ngôn ngữ dịch")

    def step_tts():
        for lang in work_langs:
            runner.generate_audio(project, cfg, ctx, lang)

    def step_clips():
        for lang in work_langs:
            runner.render_clips(project, cfg, ctx, lang)

    def step_final():
        for lang, duration in runner.export_video(project, cfg, ctx, work_langs).items():
            path = project.final_video(lang)
            print(f"  {path.name}: {duration:.1f}s, {path.stat().st_size / 2**20:.1f} MB")

    step_funcs = {
        "scenes": step_scenes,
        "translate": step_translate,
        "tts": step_tts,
        "images": lambda: runner.generate_images(project, cfg, ctx),
        "clips": step_clips,
        "final": step_final,
    }

    t_start = time.perf_counter()
    for step in STEPS:  # luôn theo thứ tự chuẩn, dù --steps viết lộn xộn
        if step not in args.steps:
            continue
        print(f"\n=== {STEP_LABELS[step]} ===")
        t0 = time.perf_counter()
        try:
            step_funcs[step]()
        except Exception as e:
            print(f"\nDỪNG ở bước '{step}': {e}")
            print(f"Sửa xong, chạy tiếp bằng: python run_pipeline.py --project-id {project.id}")
            return 1
        print(f"  ({time.perf_counter() - t0:.1f}s)")

    errors = [(s["id"], err) for s in project.load_scenes()
              for err in [s.get("image_error")] + [e["error"] for e in s["langs"].values()] if err]
    print(f"\nTổng thời gian: {time.perf_counter() - t_start:.0f}s")
    if errors:
        print(f"{len(errors)} lỗi:")
        for scene_id, err in errors:
            print(f"  cảnh {scene_id}: {err}")
        return 1
    for lang in work_langs:
        if project.final_video(lang).is_file():
            print(f"Video {lang}: {project.final_video(lang)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
