"""
Chạy thử bước dịch lời đọc bằng LLM (Ollama) — dùng đúng pipeline.translate mà app dùng.
In bản dịch, kiểm tra số câu khớp 1–1, tỉ lệ độ dài, thời gian; xác nhận model được gỡ khỏi VRAM.

Chạy (Ollama đang mở):
    .venv\\Scripts\\python tests\\test_translate.py
    .venv\\Scripts\\python tests\\test_translate.py --script tests\\long_script.txt --to en
"""

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import languages as langs  # noqa: E402
from app.config import load_config  # noqa: E402
from pipeline import translate  # noqa: E402
from pipeline.gpu import report_vram  # noqa: E402
from pipeline.scenes import split_by_paragraphs  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--script", type=Path, default=PROJECT_ROOT / "tests" / "long_script.txt")
    p.add_argument("--src", default="vi")
    p.add_argument("--to", default="en")
    args = p.parse_args()
    cfg = load_config()

    items = [{"id": f"{i:02d}", "text": s["narration"]}
             for i, s in enumerate(split_by_paragraphs(args.script.read_text(encoding="utf-8")), 1)]
    print(f"Dịch {len(items)} đoạn {args.src} → {args.to} bằng {cfg['llm']['model']}")
    report_vram("trước")
    t0 = time.perf_counter()
    results, title = {}, None
    try:
        for i, batch in enumerate(translate.chunks(items, cfg["translate"]["chunk_scenes"])):
            res, t = translate.translate_items(batch, args.src, args.to, cfg,
                                               title="Vòng tuần hoàn của nước" if i == 0 else None)
            results.update(res)
            title = title or t
    finally:
        translate.unload(cfg)
    elapsed = time.perf_counter() - t0

    same_sentences = 0
    for it in items:
        en = results[it["id"]]
        n_src, n_dst = translate.sentence_count(it["text"]), translate.sentence_count(en)
        same_sentences += n_src == n_dst
        ratio = langs.units(en) / langs.units(it["text"])
        print(f"\n{it['id']}. câu {n_src}→{n_dst}{'' if n_src == n_dst else '  ← LỆCH'} | "
              f"tỉ lệ từ {ratio:.2f} | ~{langs.estimate_sec(cfg, args.to, en):.0f}s đọc\n    {en}")
    print(f"\nTiêu đề: {title}")
    print(f"Thời gian: {elapsed:.0f}s | số câu khớp 1–1: {same_sentences}/{len(items)} đoạn")
    report_vram("sau khi gỡ model")
    with urllib.request.urlopen(cfg["llm"]["host"] + "/api/ps", timeout=10) as r:
        print("Model Ollama còn trong bộ nhớ:", [m["name"] for m in json.load(r)["models"]] or "không có")
    return 0


if __name__ == "__main__":
    sys.exit(main())
