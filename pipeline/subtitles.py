"""Phụ đề: tách lời đọc thành câu ngắn, canh thời gian, ghi .srt (tải về) và .ass (chèn vào video).

Không có mốc thời gian từng từ từ TTS, nên thời gian được CHIA THEO ĐỘ DÀI:
trong một cảnh, câu dài bao nhiêu tiếng/từ thì chiếm bấy nhiêu phần thời lượng audio của cảnh.

Phụ đề SONG NGỮ: dòng chính = ngôn ngữ đang đọc (canh thời gian theo nó), dòng phụ = ngôn ngữ kia.
Hai bên ghép theo TỪNG CÂU (bản dịch được yêu cầu giữ số câu 1–1). Số câu lệch (vd. người dùng sửa bản
dịch) → chia bản phụ thành các khúc tỉ lệ với độ dài từng câu của bản chính.
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
    secondary: str | None = None   # dòng phụ (phụ đề song ngữ)


def split_sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?…])\s+", " ".join(text.split())) if s]


def split_phrases(text: str, max_words: int) -> list[str]:
    """Tách thành các cụm ≤ max_words tiếng/từ, ưu tiên cắt ở dấu câu."""
    phrases = []
    # Dung sai: cụm chỉ dài hơn một chút thì để nguyên, tránh cắt ngang cụm từ chỉ vì thừa 1–2 tiếng
    hard_limit = max_words + max(2, max_words // 4)
    for sentence in split_sentences(text):
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


def split_words_by_weights(text: str, weights: list[float]) -> list[str]:
    """Chia các từ của `text` thành len(weights) khúc liên tiếp, độ dài tỉ lệ theo weights."""
    words = text.split()
    total = sum(weights) or 1
    out, start, acc = [], 0, 0.0
    for i, w in enumerate(weights):
        acc += w
        end = len(words) if i == len(weights) - 1 else round(len(words) * acc / total)
        out.append(" ".join(words[start:max(start, end)]))
        start = max(start, end)
    return out


def scene_cues(narration: str, speech_sec: float, offset: float, max_words: int,
               secondary: str | None = None) -> list[Cue]:
    """Phụ đề của MỘT cảnh, mốc thời gian tính từ `offset` (giây, trên toàn video)."""
    if not secondary:
        phrases = split_phrases(narration, max_words)
        weights = [len(p.split()) + 1 for p in phrases]  # +1: khoảng nghỉ ở dấu câu
        total, t, cues = sum(weights) or 1, offset, []
        for phrase, w in zip(phrases, weights):
            d = speech_sec * w / total
            cues.append(Cue(t, t + d, phrase))
            t += d
        return cues

    # Song ngữ: ghép theo câu; câu quá dài thì chia cả hai bên thành cùng số khúc
    primary = split_sentences(narration)
    sec = split_sentences(secondary)
    if len(sec) != len(primary):
        sec = split_words_by_weights(secondary, [len(p.split()) for p in primary])
    weights = [len(p.split()) + 1 for p in primary]
    total, t, cues = sum(weights) or 1, offset, []
    for p_sent, s_sent, w in zip(primary, sec, weights):
        d = speech_sec * w / total
        k = max(1, math.ceil(len(p_sent.split()) / (max_words * 1.5)))  # 2 dòng/cue → cho phép dài hơn chút
        p_parts = split_words_by_weights(p_sent, [1] * k)
        s_parts = split_words_by_weights(s_sent, [len(x.split()) for x in p_parts])
        n_words = sum(len(x.split()) for x in p_parts) or 1
        for pp, sp in zip(p_parts, s_parts):
            dd = d * len(pp.split()) / n_words
            cues.append(Cue(t, t + dd, pp, sp or None))
            t += dd
    return cues


def build_cues(scenes: list[dict], clip_durations: list[float], max_words: int) -> list[Cue]:
    """scenes: [{narration, duration_sec, secondary?}] theo thứ tự video;
    clip_durations = độ dài thật của từng clip (mốc bắt đầu cảnh sau)."""
    cues, offset = [], 0.0
    for scene, clip_dur in zip(scenes, clip_durations):
        speech = min(scene["duration_sec"] or clip_dur, clip_dur)  # phụ đề chỉ chạy trong lúc có tiếng
        cues += scene_cues(scene["narration"], speech, offset, max_words, scene.get("secondary"))
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


def write_srt(cues: list[Cue], path: Path, with_secondary: bool = True) -> None:
    """with_secondary=False: chỉ ghi dòng chính (file .srt một ngôn ngữ)."""
    blocks = []
    for i, c in enumerate(cues, 1):
        text = c.text + (f"\n{c.secondary}" if with_secondary and c.secondary else "")
        blocks.append(f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n{text}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")


def _ass_escape(text: str) -> str:
    return text.replace("{", "(").replace("}", ")")


def write_ass(cues: list[Cue], path: Path, width: int, height: int, sub_cfg: dict) -> None:
    """ASS = định dạng phụ đề có kiểu chữ; ffmpeg (libass) vẽ nó lên từng khung hình."""
    size = font_size(width, height, sub_cfg["font_size_ratio"])
    small = round(size * 0.78)
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
        text = _ass_escape(c.text)
        if c.secondary:  # dòng phụ: nhỏ hơn, màu vàng nhạt (\N = xuống dòng trong ASS)
            text += rf"\N{{\fs{small}\1c&H00B5E6F5&}}{_ass_escape(c.secondary)}"
        lines.append(f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Default,,0,0,0,,{text}")
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8-sig")
