"""Bước 1: chia kịch bản thành cảnh.

- split_with_llm(): gọi Ollama, LLM trả JSON {scenes: [{narration, image_prompt}]}.
- split_by_paragraphs(): không cần LLM, mỗi đoạn (cách nhau bằng dòng trống) là một cảnh.
  Dùng để test pipeline nhanh; image_prompt để trống.
"""

import json
import re
import time
import urllib.error
import urllib.request

from pipeline.gpu import report_vram

# JSON Schema truyền vào tham số "format" của Ollama → model bị ép sinh đúng cấu trúc này
SCENES_SCHEMA = {
    "type": "object",
    "properties": {
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "narration": {"type": "string"},
                    "image_prompt": {"type": "string"},
                },
                "required": ["narration", "image_prompt"],
            },
        }
    },
    "required": ["scenes"],
}

SYSTEM_PROMPT = """You split a Vietnamese narration script into scenes for a whiteboard-animation video.

Rules:
- Return ONLY JSON: {{"scenes": [{{"narration": "...", "image_prompt": "..."}}]}}
- "narration": copy the ORIGINAL Vietnamese text of the scene VERBATIM. Do not translate, summarize,
  rewrite, add or drop any words. Concatenating all narrations in order must give back the whole script.
- Split only at sentence boundaries. Each scene should be about {min_words}-{max_words} Vietnamese
  words ({min_sec}-{max_sec} seconds of speech). A single very long sentence may exceed this.
- "image_prompt": ONE short English description (max 40 words) of a single simple illustration that
  shows the scene's main idea with concrete objects, people, icons and arrows. Describe only what is
  visible. Never use the words "diagram", "chart", "label", "text", "caption" or "infographic", and
  never ask for written words, letters or numbers — image models render them as gibberish.
  Do not name abstract concepts or technical terms (e.g. "aquifer", "photosynthesis", "evaporation"):
  image models tend to write such terms into the picture. Describe what they LOOK like instead."""


class SceneSplitError(RuntimeError):
    pass


def split_by_paragraphs(script: str) -> list[dict]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", script) if p.strip()]
    return [{"narration": " ".join(p.split()), "image_prompt": ""} for p in paragraphs]


def _validate(data: dict, script: str) -> list[dict]:
    """Kiểm tra JSON từ LLM. Ném SceneSplitError (kèm lý do) nếu sai → hàm gọi sẽ thử lại."""
    scenes = data.get("scenes") if isinstance(data, dict) else None
    if not isinstance(scenes, list) or not scenes:
        raise SceneSplitError("thiếu danh sách 'scenes' hoặc danh sách rỗng")
    for i, s in enumerate(scenes, 1):
        if not isinstance(s, dict) or not str(s.get("narration", "")).strip():
            raise SceneSplitError(f"cảnh {i} thiếu 'narration'")
        if not str(s.get("image_prompt", "")).strip():
            raise SceneSplitError(f"cảnh {i} thiếu 'image_prompt'")
    # LLM hay "tóm tắt" hoặc bỏ sót câu → so độ dài lời đọc với kịch bản gốc
    narr_words = sum(len(s["narration"].split()) for s in scenes)
    script_words = len(script.split())
    ratio = narr_words / max(script_words, 1)
    if not 0.9 <= ratio <= 1.1:
        raise SceneSplitError(
            f"lời đọc có {narr_words} từ, kịch bản gốc có {script_words} từ — LLM đã sửa/bỏ nội dung")
    return [{"narration": s["narration"], "image_prompt": s["image_prompt"]} for s in scenes]


def _post(host: str, path: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        host.rstrip("/") + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def unload_ollama_model(host: str, model: str, wait_sec: float = 30) -> None:
    """Yêu cầu Ollama gỡ model khỏi VRAM ngay, rồi chờ tới khi /api/ps không còn model đó."""
    try:
        _post(host, "/api/generate", {"model": model, "keep_alive": 0}, timeout=60)
        deadline = time.time() + wait_sec
        while time.time() < deadline:
            with urllib.request.urlopen(host.rstrip("/") + "/api/ps", timeout=10) as r:
                running = [m["name"] for m in json.load(r).get("models", [])]
            if model not in running:
                return
            time.sleep(1)
        print(f"  CẢNH BÁO: Ollama vẫn giữ {model} trong bộ nhớ sau {wait_sec:.0f}s")
    except OSError as e:
        print(f"  CẢNH BÁO: không gỡ được model Ollama: {e}")


def chunk_script(script: str, max_words: int) -> list[str]:
    """Chia kịch bản dài thành các phần ≤ max_words tiếng, cắt ở ranh giới đoạn văn
    (đoạn quá dài thì cắt ở ranh giới câu). LLM xử lý phần ngắn chính xác hơn và không vượt num_ctx."""
    units = []
    for para in re.split(r"\n\s*\n", script.strip()):
        para = " ".join(para.split())
        if not para:
            continue
        if len(para.split()) <= max_words:
            units.append(para)
        else:
            units += [s for s in re.split(r"(?<=[.!?…])\s+", para) if s]
    chunks, current = [], []
    for unit in units:
        if current and sum(len(u.split()) for u in current) + len(unit.split()) > max_words:
            chunks.append("\n\n".join(current))
            current = []
        current.append(unit)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _ollama_options(llm: dict) -> dict:
    return {
        "temperature": llm["temperature"],
        "num_ctx": llm["num_ctx"],
        # Phần trọng số nằm trên RAM được ánh xạ từ file GGUF thay vì cấp phát pinned memory
        "use_mmap": llm["use_mmap"],
    }


def _split_chunk(chunk: str, system: str, llm: dict, log=print) -> list[dict]:
    """Chia MỘT phần kịch bản; JSON sai thì cho LLM xem lỗi và thử lại (tối đa max_retries lần)."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": chunk}]
    last_error = None
    for attempt in range(1 + llm["max_retries"]):
        body = {
            "model": llm["model"],
            "messages": messages,
            "format": SCENES_SCHEMA,
            "think": False,           # tắt chế độ suy nghĩ: nhanh hơn, ra JSON gọn
            "stream": False,
            "keep_alive": "5m",       # giữ model giữa các lần gọi; gỡ hẳn khi chia xong
            "options": _ollama_options(llm),
        }
        t0 = time.perf_counter()
        try:
            resp = _post(llm["host"], "/api/chat", body, timeout=llm["timeout_sec"])
        except urllib.error.HTTPError as e:
            raise SceneSplitError(f"Ollama lỗi HTTP {e.code}: {e.read().decode(errors='ignore')}") from e
        except OSError as e:
            raise SceneSplitError(
                f"không kết nối được Ollama ở {llm['host']} — mở ứng dụng Ollama rồi thử lại ({e})") from e
        content = resp["message"]["content"]
        log(f"  Lần {attempt + 1}: LLM trả lời sau {time.perf_counter() - t0:.0f}s")
        try:
            return _validate(json.loads(content), chunk)
        except (json.JSONDecodeError, SceneSplitError) as e:
            last_error = e
            log(f"  JSON không hợp lệ: {e}")
            # Cho LLM thấy câu trả lời sai và lý do, để lần sau sửa đúng chỗ
            messages += [
                {"role": "assistant", "content": content},
                {"role": "user", "content": f"Invalid output: {e}. Fix it and return the full JSON again."},
            ]
    raise SceneSplitError(f"LLM trả JSON sai sau {1 + llm['max_retries']} lần thử: {last_error}")


def split_with_llm(script: str, cfg: dict, on_progress=None) -> list[dict]:
    """on_progress(done, total, message): báo tiến độ theo từng phần của kịch bản."""
    llm, sc = cfg["llm"], cfg["scenes"]
    words_per_sec = sc["syllables_per_sec"]  # tiếng Việt: mỗi "từ" tách bằng dấu cách là một tiếng
    system = SYSTEM_PROMPT.format(
        min_sec=sc["min_sec"], max_sec=sc["max_sec"],
        min_words=round(sc["min_sec"] * words_per_sec), max_words=round(sc["max_sec"] * words_per_sec),
    )
    chunks = chunk_script(script, llm["chunk_words"])
    on_progress = on_progress or (lambda done, total, msg: None)
    try:
        scenes = []
        for i, chunk in enumerate(chunks):
            on_progress(i, len(chunks), f"AI đang chia phần {i + 1}/{len(chunks)} "
                                        f"({len(chunk.split())} tiếng)…")
            scenes += _split_chunk(chunk, system, llm)
            if i == 0:
                report_vram("trong lúc chạy LLM")
        on_progress(len(chunks), len(chunks), f"Đã chia thành {len(scenes)} cảnh")
        return scenes
    finally:
        # Quy tắc VRAM: chia cảnh xong (thành công hay lỗi) là gỡ model ngay
        unload_ollama_model(llm["host"], llm["model"])
