"""Pipeline dòng lệnh — kịch bản → final.mp4. Logic từng bước nằm ở pipeline/runner.py
(dùng chung với backend FastAPI); file này chỉ đọc tham số và in tiến độ.

Chạy:
    .venv\\Scripts\\python run_pipeline.py script.txt
    .venv\\Scripts\\python run_pipeline.py script.txt --no-llm --placeholder-images   # test nhanh, không GPU
    .venv\\Scripts\\python run_pipeline.py --project-id <id>                          # chạy tiếp dự án cũ
    .venv\\Scripts\\python run_pipeline.py --project-id <id> --steps tts,clips,final  # chỉ vài bước

Thứ tự bước và tài nguyên (chỉ 1 model nằm trong VRAM tại một thời điểm):
    scenes (GPU/Ollama) → tts (CPU) → images (GPU/FLUX) → clips (CPU) → final (CPU/ffmpeg)
TTS chạy trước ảnh vì animation cần biết thời lượng audio thật của từng cảnh.

Chạy lại sẽ bỏ qua phần đã "ready", chỉ làm phần missing / stale / error.
"""

import argparse
import sys
import time
from pathlib import Path

from app.config import apply_runtime_env, load_config, video_size
from app.storage import Project, create_project
from pipeline import runner

STEPS = ["scenes", "tts", "images", "clips", "final"]
STEP_LABELS = {"scenes": "1. Chia cảnh", "tts": "2. Giọng đọc", "images": "3. Tạo ảnh",
               "clips": "4. Animation vẽ bảng", "final": "5. Ghép video"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Video Studio Local — pipeline dòng lệnh")
    p.add_argument("script", nargs="?", type=Path, help="File kịch bản .txt (UTF-8)")
    p.add_argument("--project-id", help="Chạy tiếp một dự án đã có trong outputs/")
    p.add_argument("--name", help="Tên dự án (mặc định: tên file kịch bản)")
    p.add_argument("--steps", default=",".join(STEPS), help=f"Các bước cần chạy, mặc định: {','.join(STEPS)}")
    p.add_argument("--resplit", action="store_true", help="Chia cảnh lại dù đã có scenes.json (mất ảnh/audio cũ)")
    p.add_argument("--no-llm", action="store_true", help="Chia cảnh theo đoạn văn, không gọi LLM")
    p.add_argument("--placeholder-images", action="store_true", help="Ảnh giả bằng Pillow thay cho FLUX")
    args = p.parse_args()
    args.steps = [s.strip() for s in args.steps.split(",") if s.strip()]
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
    settings = {
        "aspect_ratio": cfg["video"]["aspect_ratio"],
        "resolution": list(video_size(cfg)),
        "fps": cfg["video"]["fps"],
        "voice": cfg["tts"]["voice"],
        "subtitles": cfg["subtitles"]["enabled"],
        "llm_model": None if args.no_llm else cfg["llm"]["model"],
        "image_backend": cfg["images"]["backend"],
    }
    return create_project(cfg["outputs_dir"], args.name or args.script.stem, script_text, settings)


def print_progress(done: int, total: int, msg: str) -> None:
    print(f"  [{done}/{total}] {msg}" if total else f"  {msg}", flush=True)


def main() -> int:
    # Console Windows mặc định cp1252 → in tiếng Việt bị UnicodeEncodeError
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args()
    cfg = load_config()
    apply_runtime_env(cfg)

    project = open_project(args, cfg)
    print(f"Dự án: {project.id}\nThư mục: {project.dir}")
    project.sync_status_with_files()
    # Dự án cũ dùng giọng/tỉ lệ đã lưu lúc tạo, không phải giá trị hiện tại trong config.yaml
    settings = project.load_info().get("settings", {})
    cfg["tts"]["voice"] = settings.get("voice") or cfg["tts"]["voice"]
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
            print(f"  {s['id']}. ({len(s['narration'].split())} tiếng) {s['narration'][:70]}…")
            if s["image_prompt"]:
                print(f"      ảnh: {s['image_prompt'][:90]}")

    def step_final():
        duration = runner.export_video(project, cfg, ctx)
        print(f"  final.mp4: {duration:.1f}s, {project.final_video.stat().st_size / 2**20:.1f} MB")

    step_funcs = {
        "scenes": step_scenes,
        "tts": lambda: runner.generate_audio(project, cfg, ctx),
        "images": lambda: runner.generate_images(project, cfg, ctx),
        "clips": lambda: runner.render_clips(project, cfg, ctx),
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

    errors = [s for s in project.load_scenes() if s["error"]]
    print(f"\nTổng thời gian: {time.perf_counter() - t_start:.0f}s")
    if errors:
        print(f"{len(errors)} cảnh còn lỗi:")
        for s in errors:
            print(f"  cảnh {s['id']}: {s['error']}")
        return 1
    if project.final_video.is_file():
        print(f"Video: {project.final_video}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
