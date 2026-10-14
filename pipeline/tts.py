"""Bước 3: giọng đọc, chạy trên CPU (ONNX) → không dùng VRAM.

Mỗi ngôn ngữ chọn một ENGINE trong config.yaml (languages.<mã>.tts):
  - "vieneu": VieNeu-TTS v3 Turbo — tiếng Việt, 48kHz
  - "kokoro": Kokoro-82M ONNX int8 — tiếng Anh, 24kHz (Apache-2.0)
Thêm engine mới: viết một lớp có synth(text, voice) -> (audio, sample_rate) và voices(),
rồi đăng ký vào ENGINES bên dưới.
"""

from pathlib import Path

import soundfile as sf

from app.storage import replace_file

VIENEU_VOICES_REPO = "pnnbao-ump/VieNeu-TTS-v3-Turbo"

# Kokoro: tiền tố tên giọng → (ngôn ngữ, mô tả)
KOKORO_PREFIX = {"af": ("en-us", "Nữ · Mỹ"), "am": ("en-us", "Nam · Mỹ"),
                 "bf": ("en-gb", "Nữ · Anh"), "bm": ("en-gb", "Nam · Anh")}


# ── VieNeu ────────────────────────────────────────────────────────────────────
class VieneuEngine:
    def __init__(self, engine_cfg: dict, lang_cfg: dict):
        from vieneu import Vieneu
        self.cfg = engine_cfg
        # Ép backend="onnx" + device="cpu": nếu để "auto", thư viện thấy torch+CUDA sẽ chạy trên GPU
        self._tts = Vieneu(backend="onnx", device="cpu", precision=engine_cfg["precision"])

    def synth(self, text: str, voice: str):
        audio = self._tts.infer(text, voice=voice, temperature=self.cfg["temperature"])
        return audio, self._tts.sample_rate

    def close(self) -> None:
        self._tts.close()

    @staticmethod
    def voices(engine_cfg: dict, lang_cfg: dict) -> list[dict]:
        """Danh sách giọng mẫu mà KHÔNG load model (~8s): đọc file giọng trong gói + file kèm model (cache HF)."""
        import json
        from huggingface_hub import try_to_load_from_cache
        import vieneu
        from vieneu.v3turbo import _featured_rank, sorted_voices, voice_label

        presets: dict[str, dict] = {}
        sources = [Path(vieneu.__file__).parent / "assets" / "voices_v3_turbo.json"]
        cached = try_to_load_from_cache(VIENEU_VOICES_REPO, "voices_v3_turbo.json")
        if isinstance(cached, str):
            sources.append(Path(cached))
        for path in sources:
            if path.is_file():
                for name, v in json.loads(path.read_text(encoding="utf-8")).get("presets", {}).items():
                    presets[name] = {"description": v.get("description", ""), "gender": v.get("gender", ""),
                                     "featured": _featured_rank(v)}
        return [{"id": name, "label": voice_label(name, v), "gender": v["gender"],
                 "featured": v["featured"] is not None}
                for name, v in sorted_voices(presets)]


# ── Kokoro ────────────────────────────────────────────────────────────────────
class KokoroEngine:
    def __init__(self, engine_cfg: dict, lang_cfg: dict):
        from kokoro_onnx import Kokoro
        if not Path(engine_cfg["model_path"]).is_file():
            raise RuntimeError(f"Chưa có model Kokoro ({engine_cfg['model_path']}) — chạy tools/download_models.py")
        self.cfg, self.lang = engine_cfg, lang_cfg.get("tts_lang", "en-us")
        self._tts = Kokoro(str(engine_cfg["model_path"]), str(engine_cfg["voices_path"]))

    def synth(self, text: str, voice: str):
        return self._tts.create(text, voice=voice, speed=self.cfg.get("speed", 1.0), lang=self.lang)

    def close(self) -> None:
        self._tts = None

    @staticmethod
    def voices(engine_cfg: dict, lang_cfg: dict) -> list[dict]:
        """Giọng có trong voices-v1.0.bin, lọc theo ngôn ngữ (en-us / en-gb). Không load model."""
        import numpy as np
        path = Path(engine_cfg["voices_path"])
        if not path.is_file():
            return []
        want = lang_cfg.get("tts_lang", "en-us")
        names = sorted(np.load(path).files)
        out = []
        for n in names:
            lang, desc = KOKORO_PREFIX.get(n[:2], (None, None))
            if lang == want or (lang and want.startswith("en") and lang.startswith("en")):
                out.append({"id": n, "label": f"{n.split('_', 1)[1].title()} — {desc}",
                            "gender": "female" if n[1] == "f" else "male", "featured": n == lang_cfg.get("voice")})
        out.sort(key=lambda v: (KOKORO_PREFIX[v["id"][:2]][0] != want, v["id"]))  # giọng đúng vùng lên trước
        return out


ENGINES = {"vieneu": VieneuEngine, "kokoro": KokoroEngine}


def _engine_cls(cfg: dict, lang: str):
    lang_cfg = cfg["languages"][lang]
    try:
        return ENGINES[lang_cfg["tts"]], cfg["tts_engines"][lang_cfg["tts"]], lang_cfg
    except KeyError:
        raise RuntimeError(f"Ngôn ngữ {lang}: engine TTS {lang_cfg.get('tts')!r} không có") from None


def list_voices(cfg: dict, lang: str) -> list[dict]:
    cls, engine_cfg, lang_cfg = _engine_cls(cfg, lang)
    return cls.voices(engine_cfg, lang_cfg)


class TTSModel:
    """Wrapper: load engine của ngôn ngữ một lần, gọi generate_audio() cho từng cảnh, close() khi xong.
    Dùng với `with TTSModel(cfg, lang, voice) as tts:` để chắc chắn được giải phóng."""

    def __init__(self, cfg: dict, lang: str, voice: str | None = None):
        cls, engine_cfg, lang_cfg = _engine_cls(cfg, lang)
        self.voice = voice or lang_cfg["voice"]
        self._engine = cls(engine_cfg, lang_cfg)

    def generate_audio(self, text: str, out_path: Path) -> float:
        """Đọc `text`, lưu WAV ra `out_path`, trả về thời lượng (giây)."""
        audio, sample_rate = self._engine.synth(text, self.voice)
        if len(audio) == 0:
            raise RuntimeError("TTS trả về audio rỗng")
        # Ghi ra file tạm rồi đổi tên: không để lại audio.wav dở dang nếu bị ngắt giữa chừng
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_name(out_path.stem + ".tmp.wav")
        sf.write(str(tmp), audio, sample_rate)
        replace_file(tmp, out_path)  # có thử lại nếu file đang bị mở (Windows)
        return len(audio) / sample_rate

    def close(self) -> None:
        self._engine.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
