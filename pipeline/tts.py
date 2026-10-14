"""Bước 3: giọng đọc tiếng Việt bằng VieNeu-TTS v3 Turbo, chạy trên CPU (ONNX) → không dùng VRAM."""

from pathlib import Path

import soundfile as sf

from app.storage import replace_file


VOICES_REPO = "pnnbao-ump/VieNeu-TTS-v3-Turbo"


def list_voices() -> list[dict]:
    """Danh sách giọng mẫu [{id, label, gender, featured}] mà KHÔNG load model (~8s).
    Đọc giống Vieneu: file giọng có sẵn trong gói + file giọng kèm model (trong cache HF, không tải mạng)."""
    import json
    from huggingface_hub import try_to_load_from_cache
    import vieneu
    from vieneu.v3turbo import _featured_rank, sorted_voices, voice_label

    presets: dict[str, dict] = {}
    sources = [Path(vieneu.__file__).parent / "assets" / "voices_v3_turbo.json"]
    cached = try_to_load_from_cache(VOICES_REPO, "voices_v3_turbo.json")
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


class TTSModel:
    """Wrapper: load một lần, gọi generate_audio() cho từng cảnh, close() khi xong.
    Dùng với `with TTSModel(cfg) as tts:` để chắc chắn được giải phóng."""

    def __init__(self, tts_cfg: dict):
        from vieneu import Vieneu
        self.cfg = tts_cfg
        # Ép backend="onnx" + device="cpu": nếu để "auto", thư viện thấy torch+CUDA sẽ chạy trên GPU
        self._tts = Vieneu(backend="onnx", device="cpu", precision=tts_cfg["precision"])

    def generate_audio(self, text: str, out_path: Path) -> float:
        """Đọc `text`, lưu WAV 48kHz ra `out_path`, trả về thời lượng (giây)."""
        audio = self._tts.infer(text, voice=self.cfg["voice"], temperature=self.cfg["temperature"])
        if len(audio) == 0:
            raise RuntimeError("TTS trả về audio rỗng")
        # Ghi ra file tạm rồi đổi tên: không để lại audio.wav dở dang nếu bị ngắt giữa chừng
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_name(out_path.stem + ".tmp.wav")
        sf.write(str(tmp), audio, self._tts.sample_rate)
        replace_file(tmp, out_path)  # có thử lại nếu file đang bị mở (Windows)
        return len(audio) / self._tts.sample_rate

    def close(self) -> None:
        self._tts.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
