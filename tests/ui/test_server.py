"""Server riêng cho test giao diện (tests/ui/ui_test.mjs khởi động file này).

- Dữ liệu trong outputs/tests/ui_* (xoá khi tắt), KHÔNG đụng dự án thật
- Ảnh giả (placeholder) thay FLUX, bản dịch giả thay Ollama; TTS và animation chạy thật
- Tạo sẵn một dự án mẫu đã xuất video, rồi mới mở server
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    import uvicorn
    from app.config import load_config, video_size
    from app.main import create_app
    from app.storage import create_project
    from pipeline import runner

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    cfg = load_config()
    (cfg["outputs_dir"] / "tests").mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="ui_", dir=cfg["outputs_dir"] / "tests"))
    cfg["outputs_dir"] = tmp
    try:
        script = (PROJECT_ROOT / "tests" / "sample_script.txt").read_text(encoding="utf-8")
        overrides = {"images.backend": "placeholder", "translate.backend": "fake", "shorts.backend": "fake"}
        # Kịch bản mẫu chỉ vài giây → hạ ngưỡng độ dài Short để có gợi ý mà test
        cfg["shorts"].update(min_sec=4, max_sec=30, max_scenes=2)
        languages = ["vi", "en"]
        settings = {"aspect_ratio": "16:9", "resolution": list(video_size(cfg)), "fps": cfg["video"]["fps"],
                    "voices": {l: cfg["languages"][l]["voice"] for l in languages},
                    "subtitles": True, "subtitles_bilingual": False, "image_backend": "placeholder"}
        project = create_project(tmp, "UI test", script, settings, "vi", languages)
        ctx = runner.RunContext(progress=lambda d, t, m: None, overrides=overrides)
        print("Đang tạo dự án mẫu vi + en (TTS + animation thật, ~2 phút)…", flush=True)
        runner.split_scenes(project, cfg, ctx, "paragraphs")
        runner.translate_scenes(project, cfg, ctx)
        runner.generate_images(project, cfg, ctx)
        for lang in languages:
            runner.generate_audio(project, cfg, ctx, lang)
        runner.export_video(project, cfg, ctx, languages)
        print(f"READY {project.id}", flush=True)  # ui_test.mjs đợi dòng này
        uvicorn.run(create_app(cfg, overrides=overrides),
                    host="127.0.0.1", port=port, log_level="warning")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":  # bắt buộc: job chạy trong tiến trình con (multiprocessing spawn)
    main()
