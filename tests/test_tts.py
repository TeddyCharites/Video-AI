"""
Chạy thử VieNeu-TTS v3 Turbo trên CPU (ONNX Runtime) — đọc một câu tiếng Việt, lưu ra WAV.

Cách chạy (từ thư mục gốc project, đã kích hoạt .venv):
    python tests/test_tts.py
    python tests/test_tts.py --text "Câu khác" --voice <voice_id>
    python tests/test_tts.py --list-voices
    python tests/test_tts.py --precision int8     # bản nhẹ hơn, nhanh hơn

Lần chạy đầu sẽ tải model về HF_HOME (D:\\ai-models\\huggingface):
    fp32 (mặc định): ~610 MB  |  int8: ~300 MB
"""

import argparse
import os
import sys
import time
from pathlib import Path

# Thư mục gốc project = cha của thư mục tests/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "outputs" / "tests" / "tts_test.wav"
DEFAULT_TEXT = (
    "Xin chào, đây là bài kiểm tra giọng đọc tiếng Việt. "
    "Ứng dụng sẽ biến kịch bản của bạn thành một video vẽ bảng hoàn chỉnh."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test VieNeu-TTS v3 Turbo trên CPU")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="Câu tiếng Việt cần đọc")
    parser.add_argument("--voice", default=None, help="voice_id giọng mẫu (bỏ trống = giọng mặc định)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Đường dẫn file WAV đầu ra")
    parser.add_argument("--precision", choices=["fp32", "int8"], default="fp32",
                        help="fp32 = chất lượng tốt nhất; int8 = nhanh/nhẹ hơn")
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--list-voices", action="store_true", help="Chỉ in danh sách giọng mẫu rồi thoát")
    return parser.parse_args()


def main() -> int:
    # Console Windows mặc định dùng cp1252 → in tiếng Việt bị UnicodeEncodeError. Ép UTF-8.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args()

    # Cảnh báo nếu model sẽ bị tải vào ổ C (ổ C gần đầy)
    hf_home = os.environ.get("HF_HOME")
    print(f"HF_HOME = {hf_home}")
    if not hf_home or Path(hf_home).drive.upper() == "C:":
        print("CẢNH BÁO: HF_HOME chưa trỏ sang ổ D — model sẽ tải vào ổ C. Dừng lại.")
        return 1

    from vieneu import Vieneu

    # --- 1. Load model ---
    # QUAN TRỌNG: phải ép backend="onnx" + device="cpu".
    # Nếu để "auto", thư viện thấy torch + CUDA trong venv sẽ chọn PyTorch/GPU → chiếm VRAM.
    t0 = time.perf_counter()
    tts = Vieneu(backend="onnx", device="cpu", precision=args.precision)
    load_sec = time.perf_counter() - t0
    print(f"Load model: {load_sec:.1f} s (backend={tts.backend}, precision={args.precision})")

    voices = tts.list_preset_voices()  # [(label, voice_id), ...]
    if args.list_voices:
        print(f"\n{len(voices)} giọng mẫu:")
        for label, voice_id in voices:
            print(f"  {voice_id:20s} {label}")
        return 0

    # --- 2. Tổng hợp giọng nói ---
    print(f"Giọng: {args.voice or '(mặc định)'}")
    print(f"Văn bản: {args.text}")
    t1 = time.perf_counter()
    audio = tts.infer(args.text, voice=args.voice, temperature=args.temperature)
    infer_sec = time.perf_counter() - t1

    # --- 3. Lưu file ---
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tts.save(audio, args.output)

    # RTF (real-time factor) = thời gian xử lý / thời lượng audio. < 1 nghĩa là nhanh hơn thời gian thực.
    audio_sec = len(audio) / tts.sample_rate
    rtf = infer_sec / audio_sec if audio_sec > 0 else float("inf")
    print(f"\nTổng hợp: {infer_sec:.1f} s cho {audio_sec:.1f} s audio → RTF = {rtf:.2f}")
    print(f"Sample rate: {tts.sample_rate} Hz")
    print(f"Đã lưu: {args.output}")

    tts.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
