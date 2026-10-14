"""
So sánh TTS tiếng Anh chạy trên CPU: VieNeu v3 Turbo (đang dùng cho tiếng Việt) và Kokoro-82M (ONNX int8).
Đọc cùng các đoạn mẫu, lưu WAV để nghe, in bảng thời gian nạp / tổng hợp / RTF.

Chạy (từ thư mục gốc project):
    .venv\\Scripts\\python tests\\compare_tts_en.py
Kết quả: outputs\\tests\\tts_en\\<model>_<giọng>_<số đoạn>.wav

Giấy phép trọng số: VieNeu Apache-2.0, Kokoro-82M Apache-2.0 (dùng thương mại được).
Kokoro chuyển chữ → phiên âm bằng espeak-ng (GPL-3): dùng nội bộ không sao, chỉ lưu ý khi đóng gói bán phần mềm.
"""

import sys
import time
from pathlib import Path

import soundfile as sf

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
OUT_DIR = PROJECT_ROOT / "outputs" / "tests" / "tts_en"

TEXTS = [
    "Every hour and a half, the Sun sends enough energy to Earth to power all of humanity for a whole year.",
    "When photons hit the solar cell, they knock electrons loose. The p-n junction pushes them in one direction, "
    "and that steady flow of electrons is an electric current.",
    "A good panel can keep working for more than 25 years, and a 5 kW rooftop system can cover most of a family's daily needs.",
]


def run_model(name: str, load, synth, voices: list[str]) -> list[dict]:
    t0 = time.perf_counter()
    model = load()
    load_sec = time.perf_counter() - t0
    rows = []
    for voice in voices:
        total_audio = total_synth = 0.0
        for i, text in enumerate(TEXTS, 1):
            t1 = time.perf_counter()
            audio, sr = synth(model, text, voice)
            total_synth += time.perf_counter() - t1
            total_audio += len(audio) / sr
            safe = voice.replace(" ", "_")
            sf.write(str(OUT_DIR / f"{name}_{safe}_{i}.wav"), audio, sr)
        rows.append({"model": name, "voice": voice, "load": load_sec, "synth": total_synth,
                     "audio": total_audio, "rtf": total_synth / total_audio})
    return rows


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    from app.config import apply_runtime_env, load_config
    cfg = load_config()
    apply_runtime_env(cfg)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []

    # 1. VieNeu (CPU/ONNX) — giọng mẫu tiếng Việt đọc câu tiếng Anh
    def vieneu_load():
        from vieneu import Vieneu
        return Vieneu(backend="onnx", device="cpu", precision=cfg["tts_engines"]["vieneu"]["precision"])

    def vieneu_synth(tts, text, voice):
        return tts.infer(text, voice=voice, temperature=0.8), tts.sample_rate
    rows += run_model("vieneu", vieneu_load, vieneu_synth, ["Thiện Minh", "Ngọc Huyền"])

    # 2. Kokoro-82M (ONNX int8)
    kcfg = cfg["tts_engines"]["kokoro"]

    def kokoro_load():
        from kokoro_onnx import Kokoro
        return Kokoro(str(kcfg["model_path"]), str(kcfg["voices_path"]))

    def kokoro_synth(k, text, voice):
        return k.create(text, voice=voice, speed=1.0, lang="en-us")
    rows += run_model("kokoro", kokoro_load, kokoro_synth, ["am_michael", "af_heart"])

    print(f"\n{'Model':8s} {'Giọng':12s} {'Nạp':>6s} {'Tổng hợp':>9s} {'Audio':>7s} {'RTF':>6s}")
    for r in rows:
        print(f"{r['model']:8s} {r['voice']:12s} {r['load']:5.1f}s {r['synth']:8.1f}s {r['audio']:6.1f}s {r['rtf']:6.2f}")
    print(f"\nNghe thử: {OUT_DIR}")
    print("  vieneu_Thiện_Minh_1.wav / kokoro_am_michael_1.wav (giọng nam) — vieneu_Ngọc_Huyền_1.wav / kokoro_af_heart_1.wav (nữ)")
    print("  Đoạn 2 có thuật ngữ (photons, p-n junction), đoạn 3 có con số (25 years, 5 kW).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
