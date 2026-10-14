"""Bước 4b: ghép clip animation + audio của từng cảnh thành final.mp4 bằng ffmpeg.

Dùng MỘT lệnh ffmpeg với filter "concat":
  - mỗi clip được chuẩn hoá về cùng fps / kích thước (repo vẽ bảng ra 1080x600 → scale lên 1280x720)
  - audio mỗi cảnh được đệm im lặng (apad) cho dài đúng bằng clip → tiếng và hình luôn khớp từng cảnh
  - nối tất cả, chèn phụ đề (nếu có, filter "subtitles" của libass), rồi mã hoá lại MỘT lần (H.264 + AAC)
"""

import shutil
import subprocess
from pathlib import Path

from app.storage import replace_file


def _require(tool: str) -> str:
    exe = shutil.which(tool)
    if exe is None:
        raise RuntimeError(f"Không tìm thấy {tool} trong PATH")
    return exe


def media_duration(path: Path) -> float:
    out = subprocess.run(
        [_require("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def concat_scenes(parts: list[tuple[Path, Path]], out_path: Path, width: int, height: int,
                  fps: int, audio_bitrate: str = "192k", durations: list[float] | None = None,
                  subtitles_file: Path | None = None, fonts_dir: str | None = None,
                  music: dict | None = None) -> float:
    """parts = [(clip.mp4, audio.wav), ...] theo thứ tự cảnh. Trả về thời lượng video (giây).
    durations: độ dài từng clip nếu đã đo sẵn. subtitles_file: file .ass để chèn vào hình.
    music: {"path", "volume", "duck", "fade_in_sec", "fade_out_sec"} — nhạc nền (tuỳ chọn)."""
    durations = durations or [media_duration(clip) for clip, _ in parts]
    inputs, filters, concat_in = [], [], []
    for i, ((clip, audio), d) in enumerate(zip(parts, durations)):
        inputs += ["-i", str(clip), "-i", str(audio)]
        v, a = 2 * i, 2 * i + 1  # chỉ số input của clip và audio cảnh i
        filters.append(
            f"[{v}:v]fps={fps},scale={width}:{height},setsar=1,format=yuv420p[v{i}]")
        filters.append(
            f"[{a}:a]aresample=48000,apad=whole_dur={d:.3f},atrim=0:{d:.3f}[a{i}]")
        concat_in.append(f"[v{i}][a{i}]")
    filters.append(f"{''.join(concat_in)}concat=n={len(parts)}:v=1:a=1[vc][speech]")
    filters.append("[speech]aformat=channel_layouts=stereo[sp]")
    if music:
        total = sum(durations)
        m = 2 * len(parts)  # chỉ số input của nhạc (thêm vào cuối danh sách input)
        inputs += ["-stream_loop", "-1", "-i", str(music["path"])]  # lặp vô hạn, cắt bằng atrim
        fade_out = min(music["fade_out_sec"], total / 2)
        filters.append(
            f"[{m}:a]aresample=48000,aformat=channel_layouts=stereo,volume={music['volume']:.3f},"
            f"atrim=0:{total:.3f},afade=t=in:d={music['fade_in_sec']:.2f},"
            f"afade=t=out:st={total - fade_out:.3f}:d={fade_out:.2f}[mus]")
        if music["duck"]:
            # Ducking: dùng chính giọng đọc làm "tín hiệu điều khiển" nén nhạc → có giọng thì nhạc nhỏ lại
            filters.append("[sp]asplit=2[sp1][key]")
            filters.append("[mus][key]sidechaincompress=threshold=0.015:ratio=8:attack=40:release=500[mus2]")
            filters.append("[sp1][mus2]amix=inputs=2:duration=first:normalize=0[a]")
        else:
            filters.append("[sp][mus]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        filters.append("[sp]anull[a]")
    if subtitles_file:
        # Trong filtergraph, ':' là dấu phân cách tham số → không viết được "D:/…" trơn.
        # Cách gọn: chạy ffmpeg với thư mục làm việc = thư mục chứa file phụ đề, chỉ ghi tên file.
        sub = f"subtitles=filename={subtitles_file.name}"
        if fonts_dir:
            # "C:\Windows\Fonts" → 'C\:/Windows/Fonts' (dấu ':' phải escape bằng '\')
            sub += ":fontsdir='" + fonts_dir.replace("\\", "/").replace(":", r"\:") + "'"
        filters.append(f"[vc]{sub}[v]")
    else:
        filters.append("[vc]null[v]")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.stem + ".tmp.mp4")
    cmd = [
        _require("ffmpeg"), "-y", "-loglevel", "error", "-stats",
        *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-movflags", "+faststart",   # đưa metadata lên đầu file → trình duyệt phát được ngay khi đang tải
        str(tmp),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                         cwd=subtitles_file.parent if subtitles_file else None)
    if res.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg lỗi:\n{res.stderr.strip()[-2000:]}")
    replace_file(tmp, out_path)  # có thử lại nếu file đang bị mở (Windows)
    return media_duration(out_path)
