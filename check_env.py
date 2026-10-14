"""Giai đoạn 1: kiểm tra môi trường cho Video Studio Local.

Chạy:  .venv\\Scripts\\python.exe check_env.py
Mỗi mục in OK / WARN / FAIL. Script thoát với mã 1 nếu có mục FAIL.
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
MIN_FREE_GB_MODELS = 40  # Qwen GGUF ~20GB + FLUX ~vài GB + dư địa

results: list[tuple[str, str, str]] = []  # (status, name, detail)


def report(status: str, name: str, detail: str) -> None:
    results.append((status, name, detail))
    print(f"[{status:4}] {name}: {detail}")


def run(cmd: list[str]) -> str | None:
    """Chạy lệnh, trả về dòng đầu của output; None nếu lệnh không có/lỗi."""
    exe = shutil.which(cmd[0])
    if exe is None:
        return None
    try:
        out = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (out.stdout or out.stderr).strip()
    return text.splitlines()[0] if text else None


def check_python() -> None:
    ver = sys.version.split()[0]
    in_venv = sys.prefix != sys.base_prefix
    status = "OK" if sys.version_info >= (3, 10) else "FAIL"
    report(status, "Python", f"{ver} ({'venv' if in_venv else 'KHÔNG trong venv'}) {sys.executable}")
    if not in_venv:
        report("WARN", "venv", "Nên chạy bằng .venv\\Scripts\\python.exe")


def check_torch() -> None:
    try:
        import torch
    except ImportError:
        report("FAIL", "PyTorch", "chưa cài")
        return
    if not torch.cuda.is_available():
        report("FAIL", "PyTorch CUDA", f"torch {torch.__version__} không thấy GPU")
        return
    name = torch.cuda.get_device_name(0)
    cap = torch.cuda.get_device_capability(0)
    arch = f"sm_{cap[0]}{cap[1]}"
    # RTX 50-series là sm_120: bản torch phải được build kèm kiến trúc này
    if arch not in torch.cuda.get_arch_list():
        report("FAIL", "PyTorch CUDA", f"torch {torch.__version__} không hỗ trợ {arch} ({name})")
        return
    # Phép tính thật trên GPU, vì is_available() có thể True mà kernel vẫn lỗi
    x = torch.randn(1024, 1024, device="cuda", dtype=torch.float16)
    (x @ x).sum().item()
    free, total = torch.cuda.mem_get_info()
    report("OK", "PyTorch CUDA",
           f"torch {torch.__version__}, CUDA {torch.version.cuda}, {name} {arch}, "
           f"VRAM trống {free / 2**30:.1f}/{total / 2**30:.1f} GB")
    del x
    torch.cuda.empty_cache()


def check_cli(name: str, cmd: list[str], required: bool) -> None:
    out = run(cmd)
    if out:
        report("OK", name, out)
    else:
        report("FAIL" if required else "WARN", name, "không tìm thấy trong PATH")


def check_ollama() -> None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=3) as r:
            version = json.load(r)["version"]
        report("OK", "Ollama server", f"đang chạy, v{version}")
    except OSError:
        report("WARN", "Ollama server", "không kết nối được 127.0.0.1:11434 (chưa mở Ollama?)")
    # App Ollama có cài đặt "Model location" riêng, ghi đè biến môi trường OLLAMA_MODELS,
    # nên đọc đường dẫn thật mà server đang dùng từ server.log
    log_path = Path(os.environ.get("LOCALAPPDATA", "")) / "Ollama" / "server.log"
    actual = None
    if log_path.is_file():
        for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "OLLAMA_MODELS:" in line:
                actual = line.split("OLLAMA_MODELS:", 1)[1].split()[0].replace("\\\\", "\\")
    if actual is None:
        report("WARN", "Thư mục model Ollama", "không đọc được từ server.log")
    elif Path(actual).anchor.upper().startswith("C:"):
        report("WARN", "Thư mục model Ollama",
               f"{actual} (ổ C!). Đổi trong Ollama > Settings > Model location")
    else:
        report("OK", "Thư mục model Ollama", actual)


def check_disk() -> None:
    hf_home = os.environ.get("HF_HOME")
    if not hf_home:
        report("WARN", "HF_HOME", "chưa đặt, model HuggingFace sẽ lưu vào ổ C")
    for label, path in [("Ổ dự án", PROJECT_DIR), ("Ổ lưu model", Path(hf_home or Path.home()))]:
        anchor = Path(path.anchor)
        free_gb = shutil.disk_usage(anchor).free / 2**30
        status = "OK" if free_gb >= MIN_FREE_GB_MODELS else "WARN"
        report(status, label, f"{anchor} còn {free_gb:.0f} GB")


def check_project_path() -> None:
    path_str = str(PROJECT_DIR)
    if "onedrive" in path_str.lower():
        report("WARN", "Đường dẫn dự án", "nằm trong OneDrive, nên chuyển ra ngoài")
    elif not path_str.isascii() or " " in path_str:
        report("WARN", "Đường dẫn dự án", f"có ký tự đặc biệt/dấu cách: {path_str}")
    else:
        report("OK", "Đường dẫn dự án", path_str)


def main() -> int:
    print("=== Kiểm tra môi trường Video Studio Local ===\n")
    check_python()
    check_torch()
    check_cli("NVIDIA driver", ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"], True)
    check_cli("Node.js", ["node", "--version"], True)
    check_cli("Git", ["git", "--version"], False)
    check_cli("ffmpeg", ["ffmpeg", "-version"], False)
    check_cli("Ollama CLI", ["ollama", "--version"], True)
    check_ollama()
    check_disk()
    check_project_path()

    fails = sum(1 for s, _, _ in results if s == "FAIL")
    warns = sum(1 for s, _, _ in results if s == "WARN")
    print(f"\nKết quả: {fails} FAIL, {warns} WARN")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
