"""
Chuyển các dự án định dạng cũ (v1, chỉ tiếng Việt) sang định dạng đa ngôn ngữ (v2).

    .venv\\Scripts\\python tools\\migrate_multilang.py --dry-run   # chỉ liệt kê dự án sẽ chuyển
    .venv\\Scripts\\python tools\\migrate_multilang.py             # chuyển thật

- Sao lưu project.json / scenes.json thành *.v1.bak trước khi sửa (chỉ lần đầu).
- Chuyển scenes/<id>/{audio.wav, clip.mp4} → scenes/<id>/vi/, final.mp4 → final.vi.mp4, final.srt → final.vi.srt.
- Chạy lại bao nhiêu lần cũng an toàn; dự án đã chuyển thì bỏ qua.
Server (app.main) cũng tự gọi đúng hàm này khi khởi động, nên thường không cần chạy tay.
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import load_config  # noqa: E402
from app.storage import migrate_all  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dry-run", action="store_true", help="chỉ liệt kê, không sửa gì")
    p.add_argument("--outputs", type=Path, help="thư mục outputs (mặc định theo config.yaml)")
    args = p.parse_args()
    cfg = load_config()
    outputs = args.outputs or cfg["outputs_dir"]
    done = migrate_all(outputs, cfg["languages"]["vi"]["voice"], dry_run=args.dry_run)
    verb = "sẽ chuyển" if args.dry_run else "đã chuyển"
    print(f"{len(done)} dự án {verb}" + (": " + ", ".join(done) if done else " (tất cả đã ở định dạng mới)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
