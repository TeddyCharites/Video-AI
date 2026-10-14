"""Dịch lời đọc sang ngôn ngữ khác bằng LLM local (Ollama — cùng model với bước chia cảnh).

Gửi nhiều cảnh trong MỘT lần gọi (JSON có id) để thuật ngữ được dịch thống nhất giữa các cảnh.
Kết quả bị ép theo JSON schema và được kiểm tra: đủ cảnh, đúng id, không rỗng, độ dài hợp lý
so với bản gốc. Sai → cho LLM xem lỗi và thử lại (tối đa llm.max_retries lần).
"""

import json
import re
import time
import urllib.error

from app import languages as langs
from pipeline.scenes import SceneSplitError, _ollama_options, _post, unload_ollama_model

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "items": {
            "type": "array",
            "items": {"type": "object",
                      "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                      "required": ["id", "text"]},
        },
    },
    "required": ["items"],
}

SYSTEM_PROMPT = """You translate the narration of an educational whiteboard video from {src} to {dst}.
The translation will be read aloud by a text-to-speech voice and shown as subtitles.

Rules:
- Return ONLY JSON: {{"title": "...", "items": [{{"id": "...", "text": "..."}}]}}
- Translate EVERY item. Keep the same ids, in the same order. Translate "title" too if given.
- Natural, spoken {dst}: short, clear sentences that sound good when read aloud.
- Keep technical terms accurate: use the standard {dst} term; keep proper names, units, symbols and
  abbreviations (e.g. "p-n", "kW") unchanged.
- Keep the SAME NUMBER OF SENTENCES as the original item, one-to-one and in the same order.
- Similar length to the original: do not add explanations, do not drop or summarize anything.
- Plain text only: no quotes around the text, no markdown, no translator notes."""


class TranslateError(SceneSplitError):
    pass


def sentence_count(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?…])\s+", text.strip()) if s])


def _validate(data: dict, items: list[dict], src: str, dst: str, cfg: dict) -> dict[str, str]:
    got = {str(it.get("id")): str(it.get("text", "")).strip() for it in (data.get("items") or [])
           if isinstance(it, dict)}
    want = [it["id"] for it in items]
    missing = [i for i in want if not got.get(i)]
    if missing:
        raise TranslateError(f"thiếu bản dịch cho id {', '.join(missing)}")
    lo, hi = cfg["translate"]["length_ratio"]
    for it in items:
        ratio = langs.units(got[it["id"]]) / max(1, langs.units(it["text"]))
        if not lo <= ratio <= hi:
            raise TranslateError(f"id {it['id']}: độ dài bản dịch bất thường so với bản gốc (tỉ lệ {ratio:.2f})")
    return {i: got[i] for i in want}


def translate_items(items: list[dict], src: str, dst: str, cfg: dict, title: str | None = None,
                    log=print) -> tuple[dict[str, str], str | None]:
    """items = [{id, text}] → ({id: bản dịch}, tiêu đề đã dịch). KHÔNG gỡ model (hàm gọi lo việc đó)."""
    llm = cfg["llm"]
    system = SYSTEM_PROMPT.format(src=langs.get(cfg, src)["llm_name"], dst=langs.get(cfg, dst)["llm_name"])
    payload = {"items": [{"id": it["id"], "text": it["text"]} for it in items]}
    if title:
        payload["title"] = title
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    last_error = None
    for attempt in range(1 + llm["max_retries"]):
        body = {"model": llm["model"], "messages": messages, "format": SCHEMA, "think": False,
                "stream": False, "keep_alive": "5m",
                "options": {**_ollama_options(llm), "temperature": cfg["translate"]["temperature"]}}
        t0 = time.perf_counter()
        try:
            resp = _post(llm["host"], "/api/chat", body, timeout=llm["timeout_sec"])
        except urllib.error.HTTPError as e:
            raise TranslateError(f"Ollama lỗi HTTP {e.code}: {e.read().decode(errors='ignore')}") from e
        except OSError as e:
            raise TranslateError(f"không kết nối được Ollama ở {llm['host']} — mở ứng dụng Ollama rồi thử lại ({e})") from e
        content = resp["message"]["content"]
        log(f"  Dịch {src}→{dst} ({len(items)} cảnh), lần {attempt + 1}: {time.perf_counter() - t0:.0f}s")
        try:
            data = json.loads(content)
            result = _validate(data, items, src, dst, cfg)
            new_title = None
            if title:
                new_title = (data.get("title") or "").strip() or None
            mismatched = [i for i in result if sentence_count(result[i]) != sentence_count(
                next(it["text"] for it in items if it["id"] == i))]
            if mismatched:  # không bắt buộc (phụ đề song ngữ có cách dự phòng) → chỉ ghi log
                log(f"  Lưu ý: số câu khác bản gốc ở cảnh {', '.join(mismatched)}")
            return result, new_title
        except (json.JSONDecodeError, TranslateError) as e:
            last_error = e
            log(f"  Bản dịch không hợp lệ: {e}")
            messages += [{"role": "assistant", "content": content},
                         {"role": "user", "content": f"Invalid output: {e}. Fix it and return the full JSON again."}]
    raise TranslateError(f"LLM dịch sai sau {1 + llm['max_retries']} lần thử: {last_error}")


def fake_translate(items: list[dict], dst: str, title: str | None) -> tuple[dict[str, str], str | None]:
    """Bản dịch GIẢ cho test không cần Ollama: giữ nguyên câu, thêm nhãn ngôn ngữ ở đầu mỗi câu."""
    def tag(text):
        return " ".join(f"[{dst}] {s}" for s in re.split(r"(?<=[.!?…])\s+", text.strip()) if s)
    return {it["id"]: tag(it["text"]) for it in items}, (f"[{dst}] {title}" if title else None)


def chunks(items: list[dict], size: int) -> list[list[dict]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def unload(cfg: dict) -> None:
    unload_ollama_model(cfg["llm"]["host"], cfg["llm"]["model"])
