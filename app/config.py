"""Đọc config.yaml và đổi đường dẫn tương đối thành tuyệt đối."""

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # Đường dẫn trong config tính từ thư mục gốc project, không phụ thuộc thư mục đang đứng khi chạy
    cfg["outputs_dir"] = PROJECT_ROOT / cfg["outputs_dir"]
    cfg["whiteboard"]["repo_dir"] = PROJECT_ROOT / cfg["whiteboard"]["repo_dir"]
    if cfg["whiteboard"].get("regions_repo_dir"):
        cfg["whiteboard"]["regions_repo_dir"] = PROJECT_ROOT / cfg["whiteboard"]["regions_repo_dir"]
    if cfg["whiteboard"].get("pen_image"):
        cfg["whiteboard"]["pen_image"] = PROJECT_ROOT / cfg["whiteboard"]["pen_image"]
    return cfg


def apply_runtime_env(cfg: dict) -> None:
    """Biến môi trường cho cả tiến trình (tiến trình con của job kế thừa). Gọi một lần lúc khởi động."""
    import os
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    offline = cfg.get("hf_offline")
    if offline == "auto":
        offline = _hf_models_cached(cfg)
    if offline:
        # Model đã có trong cache → không hỏi Hugging Face mỗi lần nạp: chạy được khi mất mạng, nạp nhanh hơn
        os.environ.setdefault("HF_HUB_OFFLINE", "1")


def _hf_models_cached(cfg: dict) -> bool:
    """Cả model ảnh (FLUX) lẫn giọng đọc (VieNeu) đã nằm trong cache Hugging Face chưa?
    Chưa → để online cho lần chạy đầu tự tải về."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    checks = [(cfg["images"]["model"], "model_index.json"),
              ("pnnbao-ump/VieNeu-TTS-v3-Turbo", "config.json")]
    return all(isinstance(try_to_load_from_cache(repo, name), str) for repo, name in checks)


def video_size(cfg: dict) -> tuple[int, int]:
    """(width, height) theo tỉ lệ khung hình đang chọn."""
    width, height = cfg["video"]["resolutions"][cfg["video"]["aspect_ratio"]]
    return int(width), int(height)
