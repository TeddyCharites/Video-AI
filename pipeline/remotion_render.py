"""Bước 4b (bản đầy đủ): ghép video bằng Remotion — tiêu đề "viết tay", chuyển cảnh mờ dần, phụ đề.

Python tính MỌI mốc thời gian (frame bắt đầu từng cảnh, đoạn chồng lấn khi chuyển cảnh, phụ đề)
rồi ghi ra props.json; composition React trong remotion/ chỉ việc vẽ theo đó. Một nguồn sự thật duy nhất
→ file .srt và video luôn khớp nhau.

Gọi: node remotion/node_modules/@remotion/cli/remotion-cli.js render ... (không cần npx/cmd).
Tiến độ đọc từ output của Remotion ("… 1200/3900") để báo về giao diện.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

from app.storage import replace_file
from pipeline import subtitles

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REMOTION_DIR = PROJECT_ROOT / "remotion"
CLI = REMOTION_DIR / "node_modules" / "@remotion" / "cli" / "remotion-cli.js"


def is_available() -> bool:
    return CLI.is_file() and shutil.which("node") is not None


def build_timeline(scenes: list[dict], clip_durations: list[float], *, fps: int, width: int, height: int,
                   title: str | None, rcfg: dict, sub_cfg: dict, subtitles_on: bool,
                   max_words: int, music: dict | None = None,
                   music_cfg: dict | None = None) -> tuple[dict, list[subtitles.Cue]]:
    """Trả về (props cho Remotion, phụ đề theo giây trên toàn video — để ghi .srt).
    music = {"src": tên file trong --public-dir, "volume": 0–1, "duck": bool} hoặc None."""
    overlap = round(rcfg["transition_sec"] * fps)
    # Chồng lấn không được dài hơn đoạn im lặng cuối clip (tail_ms) → giọng hai cảnh không đè nhau
    shortest = min((round(d * fps) for d in clip_durations), default=0)
    overlap = max(0, min(overlap, shortest // 3))

    props_title, cursor = None, 0
    if title:
        title_frames = round(rcfg["title_sec"] * fps)
        props_title = {"text": title, "subtitle": rcfg.get("title_subtitle") or None,
                       "from": 0, "durationInFrames": title_frames}
        cursor = title_frames

    scene_items, cues_abs, speech = [], [], []
    for i, (scene, dur) in enumerate(zip(scenes, clip_durations)):
        has_prev = i > 0 or props_title is not None
        start = max(0, cursor - overlap) if has_prev else cursor
        frames = max(1, round(dur * fps))
        scene_items.append({
            "id": scene["id"],
            "clip": f"{scene['id']}/clip.mp4",      # tương đối với --public-dir = <dự án>/scenes
            "audio": f"{scene['id']}/audio.wav",
            "from": start, "durationInFrames": frames,
            "fadeInFrames": overlap if has_prev else 0,
        })
        # Khoảng có giọng đọc (audio bắt đầu cùng cảnh) — để nhạc nền tự nhỏ lại đúng lúc này
        speech.append([start, start + round((scene["duration_sec"] or dur) * fps)])
        # Phụ đề của cảnh tính tương đối trong cảnh, rồi cộng mốc bắt đầu của cảnh
        for c in subtitles.build_cues([scene], [dur], max_words):
            cues_abs.append(subtitles.Cue(start / fps + c.start, start / fps + c.end, c.text))
        cursor = start + frames

    props = {
        "width": width, "height": height, "fps": fps, "durationInFrames": max(1, cursor),
        "background": rcfg["background"],
        "title": props_title,
        "scenes": scene_items,
        "subtitles": [{"from": round(c.start * fps), "durationInFrames": max(1, round((c.end - c.start) * fps)),
                       "text": c.text} for c in cues_abs] if subtitles_on else [],
        "subtitleStyle": {"fontSize": subtitles.font_size(width, height, sub_cfg["font_size_ratio"]),
                          "fontFamily": sub_cfg["font"], "bottom": round(height * 0.05)},
        "titleFont": rcfg["title_font"],
        "music": None,
    }
    if music:
        mc = music_cfg or {}
        props["music"] = {
            "src": music["src"], "volume": music["volume"], "duck": music["duck"],
            "duckLevel": mc.get("duck_level", 0.35),
            "rampFrames": max(1, round(mc.get("duck_ramp_sec", 0.4) * fps)),
            "fadeInFrames": round(mc.get("fade_in_sec", 1.0) * fps),
            "fadeOutFrames": round(mc.get("fade_out_sec", 2.5) * fps),
            "speech": speech,
        }
    return props, cues_abs


def render(props: dict, props_path: Path, public_dir: Path, out_path: Path, rcfg: dict,
           audio_bitrate: str, on_progress=None) -> None:
    """Render props → out_path (MP4 H.264 + AAC). on_progress(phần_trăm 0..1, thông báo)."""
    if not is_available():
        raise RuntimeError(f"Chưa cài Remotion: chạy 'npm install' trong {REMOTION_DIR}")
    props_path.write_text(json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp = out_path.with_name(out_path.stem + ".tmp.mp4")
    cmd = [
        shutil.which("node"), str(CLI), "render", "src/index.jsx", "WhiteboardVideo", str(tmp),
        f"--props={props_path}", f"--public-dir={public_dir}",
        f"--concurrency={rcfg['concurrency']}", "--crf=20", f"--audio-bitrate={audio_bitrate}",
        "--overwrite", "--log=info",
    ]
    total = props["durationInFrames"]
    proc = subprocess.Popen(cmd, cwd=REMOTION_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    tail, buf, last_pct = [], b"", -1
    # Remotion vẽ thanh tiến độ bằng \r (ghi đè dòng) → đọc từng khối, tách theo cả \r lẫn \n
    while True:
        chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
        if not chunk:
            break
        buf += chunk
        *lines, buf = re.split(rb"[\r\n]", buf)
        for raw in lines:
            line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw.decode("utf-8", "replace")).strip()  # bỏ mã màu ANSI
            if not line:
                continue
            tail = (tail + [line])[-25:]
            m = re.search(r"(\d+)\s*/\s*(\d+)", line)
            if m and on_progress and int(m.group(2)) == total:
                stage = "Đang mã hoá" if re.search(r"encod", line, re.I) else "Đang vẽ khung hình"
                pct = int(m.group(1)) / total
                # "Rendered" và "Encoded" đều đếm tới total → chia 0–80% / 80–100%
                overall = 0.8 + 0.2 * pct if stage == "Đang mã hoá" else 0.8 * pct
                if int(overall * 100) != last_pct:
                    last_pct = int(overall * 100)
                    on_progress(overall, f"Remotion: {stage.lower()} {m.group(1)}/{total}")
    proc.wait()
    if proc.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Remotion render lỗi:\n" + "\n".join(tail[-15:]))
    replace_file(tmp, out_path)  # có thử lại nếu file đang bị mở (Windows)
