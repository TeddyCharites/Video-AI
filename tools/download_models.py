"""
Tải các model KHÔNG tự tải được lúc chạy (hiện có: Kokoro-82M cho tiếng Anh) vào thư mục models/.

    .venv\\Scripts\\python tools\\download_models.py          # tải nếu chưa có (~120MB)
    .venv\\Scripts\\python tools\\download_models.py --check  # chỉ kiểm tra

FLUX, VieNeu tự tải vào HF_HOME ở lần chạy đầu; Qwen tải bằng `ollama pull` (xem README).
"""

import argparse
import sys
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import load_config  # noqa: E402

# Bản phát hành chính thức của kokoro-onnx (Apache-2.0)
KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


def download(url: str, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            print(f"\r  {dst.name}: {done / 1e6:.0f}/{total / 1e6:.0f} MB", end="", flush=True)
    tmp.replace(dst)  # tải xong mới đổi tên → không để lại file dở nếu bị ngắt
    print()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true", help="chỉ kiểm tra, không tải")
    args = p.parse_args()
    k = load_config()["tts_engines"]["kokoro"]
    missing = [path for path in (k["model_path"], k["voices_path"]) if not Path(path).is_file()]
    if not missing:
        print("Kokoro: đã có đủ model")
        return 0
    if args.check:
        print("Kokoro: thiếu " + ", ".join(m.name for m in missing))
        return 1
    for path in missing:
        download(KOKORO_RELEASE + Path(path).name, Path(path))
    print("Kokoro: xong")
    return 0


if __name__ == "__main__":
    sys.exit(main())
