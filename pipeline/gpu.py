"""Tiện ích VRAM dùng chung cho các bước chạy GPU."""

import gc
import shutil
import subprocess
import sys


def vram_used_mb() -> tuple[int, int] | None:
    """(đã dùng, tổng) MB của GPU 0, tính cho MỌI tiến trình (kể cả Ollama).
    Dùng nvidia-smi thay vì torch để không tạo CUDA context (~300MB) chỉ để đo."""
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        ).stdout.splitlines()[0]
        used, total = (int(x) for x in out.split(","))
        return used, total
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return None


def report_vram(label: str) -> None:
    info = vram_used_mb()
    if info:
        print(f"  [VRAM] {label}: {info[0]} / {info[1]} MB đang dùng")


def free_vram() -> None:
    """Gọi sau khi đã `del model`. Chỉ đụng tới torch nếu tiến trình đã import nó."""
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()
