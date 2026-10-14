"""Bước 4a: biến ảnh của một cảnh thành clip animation vẽ bảng (không có tiếng).

Gọi scripts/stream_render.py của repo geeklee/whiteboard-stream-animation qua subprocess, bằng
venv RIÊNG của repo (third_party/.../.venv) để opencv/av không lẫn vào venv chính có torch.
Tạo venv đó bằng:  python third_party/whiteboard-stream-animation/scripts/prepare_env.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from app.storage import replace_file


def repo_python(repo_dir: Path) -> Path:
    if sys.platform.startswith("win"):
        return repo_dir / ".venv" / "Scripts" / "python.exe"
    return repo_dir / ".venv" / "bin" / "python"


def check_ready(wb_cfg: dict) -> None:
    repo_dir: Path = wb_cfg["repo_dir"]
    script = repo_dir / "scripts" / "stream_render.py"
    if not script.is_file():
        raise RuntimeError(f"Không thấy {script}. Clone repo whiteboard-stream-animation vào {repo_dir}")
    if not repo_python(repo_dir).is_file():
        raise RuntimeError(f"Chưa có venv của repo. Chạy: python {repo_dir / 'scripts' / 'prepare_env.py'}")
    if wb_cfg.get("engine") == "regions":
        regions_script = wb_cfg["regions_repo_dir"] / "scripts" / "render_stream_whiteboard.py"
        if not regions_script.is_file():
            raise RuntimeError(f"Không thấy {regions_script}. Clone repo srt-whiteboard-animation "
                               f"vào {wb_cfg['regions_repo_dir']} hoặc đặt whiteboard.engine: stream")
    if wb_cfg.get("pen_image") and not wb_cfg["pen_image"].is_file():
        raise RuntimeError(f"Không thấy ảnh bàn tay {wb_cfg['pen_image']} "
                           "(tạo bằng tools/clean_pen_image.py hoặc để trống pen_image)")


def render_clip(image_path: Path, scene: dict, out_path: Path, wb_cfg: dict, fps: int, long_edge: int) -> None:
    """Render clip của một cảnh → out_path (.mp4, H.264). Clip dài = audio + tail_ms.

    Hai "engine" (whiteboard.engine trong config.yaml):
      - "stream"  (hướng 1, whiteboard-stream-animation): vẽ cả ảnh một lượt, giữ hình 3s cuối.
      - "regions" (hướng 2, srt-whiteboard-animation): vẽ từng vùng đúng lúc câu tương ứng được đọc
                   (annotation.json tự sinh, xem pipeline/regions.py), giữ hình cuối ≥ 0.5s.
    """
    total_ms = int(round(scene["duration_sec"] * 1000)) + int(wb_cfg["tail_ms"])
    engine = wb_cfg.get("engine", "stream")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Mỗi lần render một thư mục tạm riêng (script để lại file thô / đặt tên theo giây).
    # Đặt cạnh out_path để đổi tên không phải chép qua ổ khác.
    with tempfile.TemporaryDirectory(dir=out_path.parent, prefix="wb_") as tmp_dir:
        python = str(repo_python(wb_cfg["repo_dir"]))  # 2 repo cùng phụ thuộc (opencv, numpy, av) → dùng chung venv
        if engine == "regions":
            from pipeline.regions import build_annotation, write_annotation
            annotation = build_annotation(image_path, scene["narration"], scene["duration_sec"],
                                          wb_cfg["max_regions"])
            ann_path = out_path.with_name("annotation.json")  # để lại cạnh clip: xem/sửa tay được
            write_annotation(ann_path, annotation)
            cmd = [python, str(wb_cfg["regions_repo_dir"] / "scripts" / "render_stream_whiteboard.py"),
                   str(image_path), str(ann_path), str(Path(tmp_dir) / "clip.mp4")]
            if wb_cfg.get("pen_image"):
                cmd.append(str(wb_cfg["pen_image"]))  # tham số vị trí thứ 4 = ảnh bàn tay
            cmd += ["--total-ms", str(total_ms), "--fps", str(fps), "--cap-long-edge", str(long_edge)]
        else:
            # stream_render.py dành cố định 3s cuối để "ngắm" hình hoàn chỉnh; phần vẽ = total - 3s.
            # Cảnh quá ngắn → kéo dài clip (audio được đệm im lặng khi ghép, xem render.py)
            total_ms = max(total_ms, int(wb_cfg.get("min_clip_ms", 0)))
            cmd = [python, str(wb_cfg["repo_dir"] / "scripts" / "stream_render.py"),
                   str(image_path), "--out-dir", tmp_dir,
                   "--total-ms", str(total_ms), "--fps", str(fps), *wb_cfg.get("extra_args", [])]
            if wb_cfg.get("pen_image"):
                cmd += ["--pen-image", str(wb_cfg["pen_image"])]

        # Script in log tiếng Trung; console Windows mặc định cp1252 sẽ làm nó crash khi print
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", env=env)
        output = None
        for line in res.stdout.splitlines():
            if line.startswith("OUTPUT="):          # quy ước của cả 2 repo: dòng cuối là OUTPUT=<đường dẫn>
                output = Path(line.split("=", 1)[1].strip())
        if res.returncode != 0 or output is None or not output.is_file():
            tail = "\n".join((res.stdout + res.stderr).strip().splitlines()[-15:])
            raise RuntimeError(f"Vẽ bảng ({engine}) lỗi (mã {res.returncode}):\n{tail}")
        replace_file(output, out_path)  # có thử lại nếu file đang bị mở (Windows)


def remove_stale_temp(scene_dir: Path) -> None:
    """Dọn thư mục tạm còn sót nếu lần trước bị tắt ngang."""
    for d in scene_dir.glob("wb_*"):
        shutil.rmtree(d, ignore_errors=True)
