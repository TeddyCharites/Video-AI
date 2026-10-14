"""Hướng 2 (srt-whiteboard-animation): tự sinh annotation.json để vẽ ảnh THEO VÙNG, khớp lời đọc.

Repo gốc để người/agent đánh dấu vùng bằng tay. Ở đây làm tự động, đơn giản nhưng ổn định:
  1. Chia lời đọc của cảnh thành k nhóm câu (k ≤ max_regions).
  2. Chia ảnh thành k dải (dọc với ảnh ngang, ngang với ảnh dọc). Đường cắt đặt ở chỗ ÍT NÉT nhất
     gần các mốc chia đều "lượng mực" → hạn chế cắt ngang qua giữa một vật thể.
  3. Dải i được vẽ đúng lúc nhóm câu i đang được đọc (thời điểm chia theo số tiếng, như phụ đề).
Các dải phủ kín toàn ảnh và không chồng nhau → không vùng nào lộ nét trước lượt của nó.
"""

import json
import re
from pathlib import Path

import numpy as np
from PIL import Image


def sentence_groups(narration: str, k: int) -> list[str]:
    """Gộp các câu liên tiếp thành k nhóm có số tiếng xấp xỉ nhau."""
    sentences = [s for s in re.split(r"(?<=[.!?…])\s+", " ".join(narration.split())) if s]
    if len(sentences) <= k:
        return sentences
    total = sum(len(s.split()) for s in sentences)
    groups, current, acc = [], [], 0
    for i, s in enumerate(sentences):
        current.append(s)
        acc += len(s.split())
        remaining_sentences = len(sentences) - i - 1
        remaining_groups = k - len(groups) - 1
        target = total * (len(groups) + 1) / k
        next_len = len(sentences[i + 1].split()) if remaining_sentences else 0
        # Đóng nhóm khi dừng ở đây gần mục tiêu hơn là thêm câu kế tiếp (acc + nửa câu sau ≥ mục tiêu),
        # hoặc khi số câu còn lại vừa đủ chia cho các nhóm còn lại
        if remaining_groups > 0 and (acc + next_len / 2 >= target or remaining_sentences == remaining_groups):
            groups.append(" ".join(current))
            current = []
    if current:
        groups.append(" ".join(current))
    return groups


def find_cuts(ink_profile: np.ndarray, k: int, search_ratio: float = 0.12) -> list[int]:
    """k-1 vị trí cắt: mỗi vị trí ở cột/hàng ít mực nhất quanh mốc chia đều lượng mực."""
    n = len(ink_profile)
    cumulative = np.cumsum(ink_profile)
    total = cumulative[-1]
    if total <= 0:  # ảnh trắng trơn → chia đều
        return [round(n * i / k) for i in range(1, k)]
    cuts, window = [], max(4, int(n * search_ratio))
    smooth = np.convolve(ink_profile, np.ones(9) / 9, mode="same")
    for i in range(1, k):
        target = int(np.searchsorted(cumulative, total * i / k))
        lo = max(cuts[-1] + n // (4 * k) if cuts else 1, target - window)
        hi = min(n - 1, target + window)
        cuts.append(lo + int(np.argmin(smooth[lo:hi])) if hi > lo else target)
    return cuts


def build_annotation(image_path: Path, narration: str, speech_sec: float, max_regions: int,
                     start_ms: int = 200) -> dict:
    with Image.open(image_path) as img:
        gray = np.asarray(img.convert("L"), dtype=np.float32)
    h, w = gray.shape
    ink = np.clip(230 - gray, 0, None)  # nền trắng/be ≈ 0, nét càng đậm càng nhiều "mực"
    horizontal = w >= h                 # ảnh ngang: dải dọc vẽ trái → phải; ảnh dọc: trên → dưới
    profile = ink.sum(axis=0) if horizontal else ink.sum(axis=1)

    groups = sentence_groups(narration, max(1, max_regions))
    k = len(groups) or 1
    edges = [0, *find_cuts(profile, k), w if horizontal else h]

    # Thời gian: chia speech_sec theo số tiếng mỗi nhóm; mỗi vùng vẽ trong 90% khung giờ của nó
    weights = [len(g.split()) + 1 for g in groups] or [1]
    total_w = sum(weights)
    elements, t = [], start_ms
    speech_ms = max(1000, speech_sec * 1000 - start_ms)
    for i, (a, b) in enumerate(zip(edges, edges[1:])):
        slot = speech_ms * weights[i] / total_w
        region = ({"x": int(a), "y": 0, "width": int(b - a), "height": h} if horizontal
                  else {"x": 0, "y": int(a), "width": w, "height": int(b - a)})
        elements.append({
            "id": f"r{i + 1}",
            "label": f"Vùng {i + 1}",
            "sequence": i + 1,
            "narrativeRole": "tự động theo thứ tự câu",
            "subtitle": groups[i] if i < len(groups) else "",
            "type": "auto",
            "region": region,
            "reveal": {"direction": "left_to_right" if horizontal else "top_to_bottom",
                       "startMs": int(t), "durationMs": int(slot * 0.9),
                       "maskPaddingPx": 0, "protectedRegions": []},
            "handPath": {"start": [region["x"], region["y"]],
                         "end": [region["x"] + region["width"], region["y"] + region["height"]],
                         "easing": "easeInOut"},
        })
        t += slot
    return {"sceneId": image_path.parent.name, "canvas": {"width": w, "height": h},
            "storyBasis": narration, "sceneDurationMs": int(start_ms + speech_ms), "elements": elements}


def write_annotation(path: Path, annotation: dict) -> None:
    path.write_text(json.dumps(annotation, ensure_ascii=False, indent=2), encoding="utf-8")
