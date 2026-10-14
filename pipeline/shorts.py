"""Tạo Shorts (9:16, 30–60 giây) từ một dự án video dài.

- suggest(): LLM đọc các cảnh (lời đọc + thời lượng thật) → đề xuất 3–5 đoạn gồm các cảnh LIỀN NHAU,
  kèm tiêu đề cho mọi ngôn ngữ của dự án. Kết quả được kiểm tra (cảnh có thật, liền nhau, đủ 30–60s…);
  sai thì cho LLM xem lỗi và thử lại.
- create_short(): tạo DỰ ÁN CON liên kết với dự án gốc, dùng lại ảnh / audio / clip vẽ bảng có sẵn
  (hard link nếu cùng ổ đĩa → không tốn thêm dung lượng; không được thì chép).
- parent_changes() / sync_from_parent(): phát hiện cảnh nào ở dự án gốc đã đổi sau khi tạo Short
  (so "dấu vân tay" lưu lúc chép) và chép lại phần đã đổi.
- ensure_outro_audio(): đọc câu kết ("Xem video đầy đủ…") bằng giọng của từng ngôn ngữ.
"""

import json
import os
import shutil
import time
import urllib.error
from pathlib import Path

from app import languages as langs
from app.storage import LANG_FILES, Project, create_project, mark_stale, replace_file, text_hash

SHORT_RESOLUTION = [720, 1280]


class ShortsError(RuntimeError):
    pass


# ── Gợi ý bằng LLM ────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a video editor turning a long educational whiteboard video into YouTube Shorts / TikToks.

You get the scenes of the long video: id, length in seconds and the {src} narration.
Pick {count} of the most interesting, self-contained moments. Each Short:
- uses 1 to {max_scenes} CONSECUTIVE scenes (no gaps, keep the original order),
- lasts {min_sec}-{max_sec} seconds in total (add up the "sec" values),
- makes sense on its own and ends on an idea, a surprise or a useful fact (a good hook),
- does not overlap much with the other Shorts.

Return ONLY JSON: {{"shorts": [{{"scene_ids": ["03", "04"], "titles": {{{title_keys}}}, "reason": "..."}}]}}
- "titles": a short catchy title (max 8 words) for each language: {title_langs}. No quotes, no emojis.
- "reason": one short sentence in {src} explaining why this moment works as a Short."""


def _schema(languages: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {"shorts": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "scene_ids": {"type": "array", "items": {"type": "string"}},
                "titles": {"type": "object", "properties": {l: {"type": "string"} for l in languages},
                           "required": languages},
                "reason": {"type": "string"},
            },
            "required": ["scene_ids", "titles", "reason"],
        }}},
        "required": ["shorts"],
    }


def scene_seconds(scene: dict, lang: str, cfg: dict) -> float:
    """Thời lượng cảnh theo audio thật; chưa có audio thì ước lượng theo tốc độ đọc."""
    entry = scene["langs"][lang]
    return entry["duration_sec"] or langs.estimate_sec(cfg, lang, entry["narration"])


def validate(items: list[dict], scenes: list[dict], source: str, languages: list[str],
             cfg: dict) -> tuple[list[dict], list[str]]:
    """→ (đề xuất hợp lệ, lý do các đề xuất bị loại)."""
    sc = cfg["shorts"]
    index = {s["id"]: i for i, s in enumerate(scenes)}
    valid, errors, seen = [], [], set()
    for n, it in enumerate(items, 1):
        ids = [str(i) for i in (it.get("scene_ids") or [])]
        positions = [index.get(i) for i in ids]
        if not ids or None in positions:
            errors.append(f"short {n}: scene id không tồn tại ({ids})")
            continue
        if positions != list(range(positions[0], positions[0] + len(ids))):
            errors.append(f"short {n}: các cảnh {ids} không liền nhau / sai thứ tự")
            continue
        if len(ids) > sc["max_scenes"]:
            errors.append(f"short {n}: quá {sc['max_scenes']} cảnh")
            continue
        total = sum(scene_seconds(scenes[p], source, cfg) for p in positions)
        # Dung sai 10%: thời lượng audio thật có thể lệch chút so với con số LLM thấy
        if not sc["min_sec"] * 0.9 <= total <= sc["max_sec"] * 1.1:
            errors.append(f"short {n}: tổng {total:.0f}s, cần {sc['min_sec']}–{sc['max_sec']}s")
            continue
        titles = {l: str((it.get("titles") or {}).get(l, "")).strip().strip('"«»') for l in languages}
        if not all(titles.values()):
            errors.append(f"short {n}: thiếu tiêu đề cho {[l for l, t in titles.items() if not t]}")
            continue
        key = tuple(ids)
        if key in seen:
            continue
        seen.add(key)
        valid.append({"scene_ids": ids, "titles": titles, "reason": str(it.get("reason", "")).strip(),
                      "duration_sec": round(total, 1)})
    return valid, errors


def fake_suggest(scenes: list[dict], source: str, languages: list[str], cfg: dict) -> list[dict]:
    """Đề xuất GIẢ cho test không cần Ollama: các cửa sổ cảnh liền nhau đủ độ dài, không chồng nhau."""
    sc, out, i = cfg["shorts"], [], 0
    while i < len(scenes) and len(out) < sc["count"]:
        total, j = 0.0, i
        while j < len(scenes) and j - i < sc["max_scenes"] and total < sc["min_sec"]:
            total += scene_seconds(scenes[j], source, cfg)
            j += 1
        if total >= sc["min_sec"] * 0.9:
            ids = [s["id"] for s in scenes[i:j]]
            out.append({"scene_ids": ids, "titles": {l: f"[{l}] Short {len(out) + 1}" for l in languages},
                        "reason": "đề xuất giả (test)"})
        i = j if j > i else i + 1
    return out


def suggest(project: Project, cfg: dict, ctx) -> list[dict]:
    """Gợi ý Shorts cho dự án dài, lưu vào project.json (short_suggestions) và trả về."""
    from pipeline.scenes import _ollama_options, _post, unload_ollama_model
    info = project.load_info()
    source, languages = info["source_lang"], info["languages"]
    scenes = project.load_scenes()
    sc = cfg["shorts"]
    total = sum(scene_seconds(s, source, cfg) for s in scenes)
    if total < sc["min_sec"] * 0.9:
        raise ShortsError(f"Video quá ngắn ({total:.0f}s) để cắt Short {sc['min_sec']}–{sc['max_sec']}s")

    ctx.progress(0, 1, "AI đang đọc kịch bản và chọn đoạn hay…")
    if ctx.overrides.get("shorts.backend") == "fake":
        items, errors = validate(fake_suggest(scenes, source, languages, cfg), scenes, source, languages, cfg)
    else:
        llm = cfg["llm"]
        system = SYSTEM_PROMPT.format(
            src=langs.get(cfg, source)["llm_name"], count=sc["count"], max_scenes=sc["max_scenes"],
            min_sec=sc["min_sec"], max_sec=sc["max_sec"],
            title_keys=", ".join(f'"{l}": "..."' for l in languages),
            title_langs=", ".join(f"{l} = {langs.get(cfg, l)['llm_name']}" for l in languages))
        payload = [{"id": s["id"], "sec": round(scene_seconds(s, source, cfg)), "text": s["langs"][source]["narration"]}
                   for s in scenes]
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps({"scenes": payload}, ensure_ascii=False)}]
        items, errors = [], []
        try:
            for attempt in range(1 + llm["max_retries"]):
                ctx.check()
                body = {"model": llm["model"], "messages": messages, "format": _schema(languages),
                        "think": False, "stream": False, "keep_alive": "5m",
                        "options": {**_ollama_options(llm), "temperature": 0.5}}
                t0 = time.perf_counter()
                try:
                    resp = _post(llm["host"], "/api/chat", body, timeout=llm["timeout_sec"])
                except urllib.error.HTTPError as e:
                    raise ShortsError(f"Ollama lỗi HTTP {e.code}: {e.read().decode(errors='ignore')}") from e
                except OSError as e:
                    raise ShortsError(f"không kết nối được Ollama ở {llm['host']} — mở ứng dụng Ollama rồi thử lại ({e})") from e
                content = resp["message"]["content"]
                ctx.log(f"  Gợi ý Shorts, lần {attempt + 1}: {time.perf_counter() - t0:.0f}s")
                try:
                    raw = json.loads(content).get("shorts") or []
                except json.JSONDecodeError as e:
                    raw, errors = [], [f"JSON sai: {e}"]
                else:
                    items, errors = validate(raw, scenes, source, languages, cfg)
                if len(items) >= min(3, sc["count"]):
                    break
                ctx.log(f"  Mới có {len(items)} đề xuất hợp lệ; lỗi: {'; '.join(errors)}")
                messages += [{"role": "assistant", "content": content},
                             {"role": "user", "content": "Some Shorts are invalid: " + "; ".join(errors)
                              + f". Return {sc['count']} valid Shorts as full JSON again."}]
        finally:
            unload_ollama_model(llm["host"], llm["model"])  # Quy tắc VRAM: xong là gỡ LLM
    if not items:
        raise ShortsError("Không tìm được đoạn nào hợp lệ để làm Short: " + "; ".join(errors))

    def save(i):
        i["short_suggestions"] = {"items": items, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    project.update_info(save)
    ctx.progress(1, 1, f"Đã gợi ý {len(items)} Short")
    return items


# ── Tạo dự án con ─────────────────────────────────────────────────────────────
def link_or_copy(src: Path, dst: Path) -> None:
    """Hard link (cùng ổ đĩa: tức thì, không tốn dung lượng) hoặc chép. Sửa file gốc sau này luôn qua
    os.replace (file MỚI) → bản ở dự án con giữ nguyên nội dung cũ: không ai ghi đè được file của ai."""
    if not src.is_file():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".linking")
    tmp.unlink(missing_ok=True)
    try:
        os.link(src, tmp)
    except OSError:
        shutil.copy2(src, tmp)
    replace_file(tmp, dst)


def _mtime(path: Path) -> int | None:
    return path.stat().st_mtime_ns if path.is_file() else None


def scene_signature(parent: Project, scene: dict) -> dict:
    """Dấu vân tay một cảnh của dự án gốc: đổi chữ hoặc file được tạo lại → chữ ký đổi."""
    sig = {"image": [scene["image_prompt"], _mtime(parent.scene_dir(scene["id"]) / "image.png")]}
    for lang, entry in scene["langs"].items():
        d = parent.lang_dir(scene["id"], lang)
        sig[lang] = [text_hash(entry["narration"]), _mtime(d / "audio.wav"), _mtime(d / "clip.mp4")]
    return sig


def _copy_scene_files(parent: Project, child: Project, parent_id: str, child_id: str, languages: list[str]) -> None:
    link_or_copy(parent.scene_dir(parent_id) / "image.png", child.scene_dir(child_id) / "image.png")
    for lang in languages:
        for filename in LANG_FILES.values():
            link_or_copy(parent.lang_dir(parent_id, lang) / filename, child.lang_dir(child_id, lang) / filename)


def _child_scene(parent_scene: dict, child_id: str, order: int, languages: list[str], sig: dict) -> dict:
    s = json.loads(json.dumps(parent_scene))
    s.update(id=child_id, order=order, parent_scene=parent_scene["id"], parent_sig=sig)
    s["langs"] = {l: e for l, e in s["langs"].items() if l in languages}
    return s


def create_short(parent: Project, cfg: dict, scene_ids: list[str], titles: dict[str, str] | None = None,
                 cta: dict[str, str] | None = None) -> Project:
    info = parent.load_info()
    if info.get("kind") == "short":
        raise ShortsError("Không tạo Short từ một Short")
    source, languages = info["source_lang"], info["languages"]
    scenes = parent.load_scenes()
    by_id = {s["id"]: s for s in scenes}
    order = [s["id"] for s in scenes]
    if not scene_ids or any(i not in by_id for i in scene_ids):
        raise ShortsError("Cảnh không tồn tại trong dự án gốc")
    pos = [order.index(i) for i in scene_ids]
    if pos != list(range(pos[0], pos[0] + len(pos))):
        raise ShortsError("Các cảnh của một Short phải liền nhau")
    titles = {l: (titles or {}).get(l) or f"{info['titles'].get(l, info['name'])} (Short)" for l in languages}
    parent_titles = {l: info.get("titles", {}).get(l) or info["name"] for l in languages}
    cta_text = {l: (cta or {}).get(l) or cfg["shorts"]["cta"].get(l, cfg["shorts"]["cta"]["en"]).format(
        title=parent_titles[l]) for l in languages}

    pset = info.get("settings", {})
    settings = {
        "aspect_ratio": "9:16", "resolution": SHORT_RESOLUTION, "fps": pset.get("fps", cfg["video"]["fps"]),
        "voices": dict(pset.get("voices", {})), "subtitles": True,
        "subtitles_bilingual": pset.get("subtitles_bilingual", False),
        "source_aspect": pset.get("aspect_ratio", "16:9"),   # khung của clip gốc (đặt giữa khung dọc)
        "image_backend": pset.get("image_backend"), "llm_model": pset.get("llm_model"),
    }
    script = "\n\n".join(by_id[i]["langs"][source]["narration"] for i in scene_ids)
    child = create_project(parent.dir.parent, f"Short {titles[source]}", script, settings, source, languages)

    child_scenes = []
    for n, pid in enumerate(scene_ids, 1):
        cid = f"{n:02d}"
        child_scenes.append(_child_scene(by_id[pid], cid, n, languages, scene_signature(parent, by_id[pid])))
        _copy_scene_files(parent, child, pid, cid, languages)
    child.save_scenes(child_scenes)

    music = pset.get("music")
    if music and (parent.dir / music["file"]).is_file():  # nhạc nền: dùng chung bài của video gốc
        link_or_copy(parent.dir / music["file"], child.dir / music["file"])

    def setup(ci):
        ci.update(kind="short", titles=titles,
                  parent={"id": parent.id, "name": info["name"], "titles": parent_titles},
                  outro={l: {"text": cta_text[l], "audio_status": "missing", "duration_sec": None}
                         for l in languages})
        if music:
            ci["settings"]["music"] = dict(music)
    child.update_info(setup)
    parent.update_info(lambda pi: pi.setdefault("shorts", []).append(child.id))
    child.sync_status_with_files()
    return child


# ── Đồng bộ với dự án gốc ─────────────────────────────────────────────────────
def parent_project(child: Project, info: dict | None = None) -> Project | None:
    info = info or child.load_info()
    p = info.get("parent")
    if not p:
        return None
    parent = Project(child.dir.parent, p["id"])
    return parent if parent.exists() else None


def parent_changes(child: Project, info: dict | None = None) -> list[dict]:
    """Cảnh nào ở dự án gốc đã đổi (lời đọc, ảnh, audio, clip) sau khi chép sang Short."""
    parent = parent_project(child, info)
    if parent is None:
        return []
    by_id = {s["id"]: s for s in parent.load_scenes()}
    changes = []
    for s in child.load_scenes():
        ps = by_id.get(s.get("parent_scene"))
        if ps is None:
            changes.append({"id": s["id"], "parent_scene": s.get("parent_scene"), "what": ["removed"]})
            continue
        now, then = scene_signature(parent, ps), s.get("parent_sig", {})
        what = [k for k in now if now[k] != then.get(k)]
        if what:
            changes.append({"id": s["id"], "parent_scene": ps["id"], "what": what})
    return changes


def sync_from_parent(child: Project) -> int:
    """Chép lại từ dự án gốc các cảnh đã đổi (ghi đè chỉnh sửa trong Short ở các cảnh đó)."""
    info = child.load_info()
    parent = parent_project(child, info)
    if parent is None:
        raise ShortsError("Không còn dự án gốc")
    changed = {c["id"]: c for c in parent_changes(child, info) if "removed" not in c["what"]}
    if not changed:
        return 0
    by_id = {s["id"]: s for s in parent.load_scenes()}
    with child.lock:
        scenes = child.load_scenes()
        for i, s in enumerate(scenes):
            if s["id"] in changed:
                ps = by_id[changed[s["id"]]["parent_scene"]]
                _copy_scene_files(parent, child, ps["id"], s["id"], info["languages"])
                scenes[i] = _child_scene(ps, s["id"], s["order"], info["languages"], scene_signature(parent, ps))
        child.save_scenes(scenes)
        child.sync_status_with_files()
        child.mark_final_stale()
    return len(changed)


# ── Câu kết (giọng đọc) ───────────────────────────────────────────────────────
def outro_path(child: Project, lang: str) -> Path:
    return child.dir / "outro" / lang / "audio.wav"


def ensure_outro_audio(child: Project, cfg: dict, lang: str, ctx) -> float:
    """Đọc câu kết của ngôn ngữ `lang` nếu chưa có / đã đổi chữ. Trả về thời lượng (giây)."""
    info = child.load_info()
    outro = info["outro"][lang]
    path = outro_path(child, lang)
    if outro["audio_status"] == "ready" and path.is_file() and outro.get("text_hash") == text_hash(outro["text"]):
        return outro["duration_sec"]
    from pipeline.tts import TTSModel
    ctx.log(f"  Đọc câu kết ({lang}): {outro['text']}")
    voice = info.get("settings", {}).get("voices", {}).get(lang)
    with TTSModel(cfg, lang, voice) as tts:
        duration = tts.generate_audio(outro["text"], path)

    def save(i):
        i["outro"][lang].update(audio_status="ready", duration_sec=round(duration, 3),
                                text_hash=text_hash(outro["text"]))
    child.update_info(save)
    return duration


def write_outro_text(child: Project, texts: dict[str, str]) -> list[str]:
    """Sửa câu kết → audio câu kết và video của ngôn ngữ đó thành cũ. Trả về các ngôn ngữ đã đổi."""
    changed = []

    def apply(i):
        for lang, text in texts.items():
            if lang in i.get("outro", {}) and text.strip() and i["outro"][lang]["text"] != text.strip():
                i["outro"][lang]["text"] = text.strip()
                mark_stale(i["outro"][lang], "audio_status")
                changed.append(lang)
    child.update_info(apply)
    if changed:
        child.mark_final_stale(changed)
    return changed
