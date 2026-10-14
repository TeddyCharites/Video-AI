"""Phụ đề: tách lời đọc thành câu ngắn, canh thời gian, ghi .srt (tải về) và .ass (chèn vào video).

Không có mốc thời gian từng từ từ TTS, nên thời gian được CHIA THEO SỐ TIẾNG:
trong một cảnh, câu dài bao nhiêu tiếng thì chiếm bấy nhiêu phần thời lượng audio của cảnh.
Tiếng Việt đọc khá đều nên cách này lệch không quá vài trăm mili-giây.
"""

import math
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Cue:
    start: float
    end: float
    text: str


def split_phrases(text: str, max_words: int) -> list[str]:
    """Tách thành các cụm ≤ max_words tiếng, ưu tiên cắt ở dấu câu."""
    phrases = []
    # Dung sai: cụm chỉ dài hơn một chút thì để nguyên, tránh cắt ngang cụm từ chỉ vì thừa 1–2 tiếng
    hard_limit = max_words + max(2, max_words // 4)
    for sentence in re.split(r"(?<=[.!?…])\s+", " ".join(text.split())):
        if not sentence:
            continue
        if len(sentence.split()) <= hard_limit:
            phrases.append(sentence)
            continue
        # Câu dài: cắt ở dấu phẩy/chấm phẩy, rồi gộp lại cho đầy tới max_words
        current: list[str] = []
        for part in re.split(r"(?<=[,;:])\s+", sentence):
            words = part.split()
            if current and len(current) + len(words) > max_words:
                phrases.append(" ".join(current))
                current = []
            current += words
            # Vế vẫn quá dài (không có dấu phẩy) → cắt đều thành các khúc bằng nhau
            while len(current) > hard_limit:
                n_chunks = math.ceil(len(current) / max_words)
                size = math.ceil(len(current) / n_chunks)
                phrases.append(" ".join(current[:size]))
                current = current[size:]
        if current:
            phrases.append(" ".join(current))
    return phrases


def build_cues(scenes: list[dict], clip_durations: list[float], max_words: int) -> list[Cue]:
    """scenes theo thứ tự video; clip_durations = độ dài thật của từng clip (mốc bắt đầu cảnh sau)."""
    cues, offset = [], 0.0
    for scene, clip_dur in zip(scenes, clip_durations):
        speech = min(scene["duration_sec"] or clip_dur, clip_dur)  # phụ đề chỉ chạy trong lúc có tiếng
        phrases = split_phrases(scene["narration"], max_words)
        # +1 cho mỗi cụm: khoảng nghỉ ở dấu câu
        weights = [len(p.split()) + 1 for p in phrases]
        total = sum(weights) or 1
        t = offset
        for phrase, w in zip(phrases, weights):
            d = speech * w / total
            cues.append(Cue(t, t + d, phrase))
            t += d
        offset += clip_dur
    return cues


def font_size(width: int, height: int, ratio: float) -> int:
    """Cỡ chữ phụ đề theo CẠNH NGẮN của khung (720p ngang → 36px).
    Video dọc (xem trên điện thoại) to hơn 30% nhưng vẫn vừa bề ngang hẹp (720p dọc → 47px)."""
    size = min(width, height) * ratio
    return round(size * 1.3 if height > width else size)


def _srt_time(t: float) -> str:
    ms = round(t * 1000)
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _ass_time(t: float) -> str:
    cs = round(t * 100)
    return f"{cs // 360_000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def write_srt(cues: list[Cue], path: Path) -> None:
    blocks = [f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n{c.text}\n" for i, c in enumerate(cues, 1)]
    path.write_text("\n".join(blocks), encoding="utf-8")


def write_ass(cues: list[Cue], path: Path, width: int, height: int, sub_cfg: dict) -> None:
    """ASS = định dạng phụ đề có kiểu chữ; ffmpeg (libass) vẽ nó lên từng khung hình."""
    size = font_size(width, height, sub_cfg["font_size_ratio"])
    margin_v = round(height * 0.05)
    margin_h = round(width * 0.08)
    # Màu ASS là &HAABBGGRR (AA = độ TRONG SUỐT: 00 đặc, FF trong hẳn)
    # BorderStyle=3: hộp nền mờ phía sau chữ (dùng OutlineColour làm màu hộp) → dễ đọc trên nền bảng sáng
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{sub_cfg["font"]},{size},&H00FFFFFF,&H000000FF,&H50000000,&H00000000,0,0,0,0,100,100,0,0,3,{round(size * 0.25)},0,2,{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for c in cues:
        text = c.text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")
        lines.append(f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Default,,0,0,0,,{text}")
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8-sig")
