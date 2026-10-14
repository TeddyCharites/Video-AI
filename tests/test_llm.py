"""
Chạy thử bước chia cảnh: Qwen3.6-35B-A3B qua Ollama → JSON danh sách cảnh.
Gọi đúng hàm pipeline.scenes.split_with_llm() mà pipeline dùng, in thời gian + VRAM,
và kiểm tra model đã được gỡ khỏi VRAM sau khi xong.

Cách chạy (từ thư mục gốc project, Ollama đang mở):
    python tests/test_llm.py
    python tests/test_llm.py --script duong/dan/kich_ban.txt
"""

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))  # để import được app/ và pipeline/ khi chạy từ tests/

from app.config import load_config  # noqa: E402
from pipeline.gpu import report_vram  # noqa: E402
from pipeline.scenes import split_with_llm  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--script", type=Path, default=PROJECT_ROOT / "tests" / "sample_script.txt")
    p.add_argument("--chunk-words", type=int, help="ghi đè llm.chunk_words (thử chia kịch bản dài thành nhiều phần)")
    args = p.parse_args()

    cfg = load_config()
    if args.chunk_words:
        cfg["llm"]["chunk_words"] = args.chunk_words
    script = args.script.read_text(encoding="utf-8").strip()
    print(f"Model: {cfg['llm']['model']}")
    print(f"Kịch bản: {len(script.split())} tiếng, cảnh mong muốn {cfg['scenes']['min_sec']}–{cfg['scenes']['max_sec']}s")
    report_vram("trước khi chạy")

    t0 = time.perf_counter()
    def progress(done, total, msg):
        print(f"  [{done}/{total}] {msg}", flush=True)
    scenes = split_with_llm(script, cfg, progress)  # tự gỡ model ở cuối (keep_alive=0)
    print(f"\nChia cảnh: {time.perf_counter() - t0:.0f}s (gồm cả load model lần đầu) → {len(scenes)} cảnh")
    min_sec, max_sec = cfg["scenes"]["min_sec"], cfg["scenes"]["max_sec"]
    for i, s in enumerate(scenes, 1):
        words = len(s["narration"].split())
        est = words / cfg["languages"]["vi"]["units_per_sec"]
        flag = "" if min_sec <= est <= max_sec else "  ← ngoài khoảng mong muốn"
        print(f"\n{i:02d}. {words} tiếng (~{est:.0f}s){flag}\n    {s['narration']}\n    ảnh: {s['image_prompt']}")

    report_vram("sau khi gỡ model")
    with urllib.request.urlopen(cfg["llm"]["host"] + "/api/ps", timeout=10) as r:
        running = [m["name"] for m in json.load(r)["models"]]
    print(f"Model Ollama còn trong bộ nhớ: {running or 'không có'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
