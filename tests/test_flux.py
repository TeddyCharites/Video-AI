"""
Chạy thử FLUX.2 [klein] 4B bản 4-bit NF4 trên GPU — tạo 1 ảnh, in thời gian và VRAM đỉnh.

Cách chạy (từ thư mục gốc project):
    python tests/test_flux.py                      # cả pipeline trên GPU
    python tests/test_flux.py --offload            # enable_model_cpu_offload(): ít VRAM hơn, chậm hơn
    python tests/test_flux.py --width 768 --height 512

Lần chạy đầu tải ~4.8 GB về HF_HOME (D:\\ai-models\\huggingface).
"""

import argparse
import gc
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_ID = "hlhc/FLUX.2-klein-4B-bnb-4bit"
DEFAULT_PROMPT = (
    "A green leaf with a magnifying glass showing tiny cells inside, sunlight rays from the top corner, "
    "simple black line art drawing on a plain white background, clean bold outlines, "
    "whiteboard sketch style, minimal shading, no text"
)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--steps", type=int, default=4)
    p.add_argument("--offload", action="store_true", help="enable_model_cpu_offload()")
    p.add_argument("--count", type=int, default=2, help="số ảnh (ảnh đầu có warm-up, ảnh sau đo tốc độ thật)")
    args = p.parse_args()

    hf_home = os.environ.get("HF_HOME", "")
    print(f"HF_HOME = {hf_home}")
    if not hf_home or Path(hf_home).drive.upper() == "C:":
        print("CẢNH BÁO: HF_HOME chưa trỏ sang ổ D. Dừng lại.")
        return 1

    import torch
    from diffusers import Flux2KleinPipeline

    free, total = torch.cuda.mem_get_info()
    print(f"VRAM trống trước khi load: {free / 2**30:.2f} / {total / 2**30:.2f} GB")

    t0 = time.perf_counter()
    pipe = Flux2KleinPipeline.from_pretrained(MODEL_ID, torch_dtype=torch.bfloat16)
    if args.offload:
        # Chỉ module đang chạy (text encoder → transformer → VAE) nằm trên GPU, còn lại ở RAM
        pipe.enable_model_cpu_offload()
    else:
        pipe.to("cuda")
    print(f"Load model: {time.perf_counter() - t0:.1f} s (offload={args.offload})")
    print(f"VRAM sau load (torch): {torch.cuda.memory_allocated() / 2**30:.2f} GB")

    out_dir = PROJECT_ROOT / "outputs" / "tests"
    out_dir.mkdir(parents=True, exist_ok=True)
    torch.cuda.reset_peak_memory_stats()
    for i in range(args.count):
        t1 = time.perf_counter()
        image = pipe(
            prompt=args.prompt, width=args.width, height=args.height,
            num_inference_steps=args.steps, guidance_scale=1.0,
            generator=torch.Generator("cpu").manual_seed(42 + i),
        ).images[0]
        path = out_dir / f"flux_{'offload' if args.offload else 'gpu'}_{i + 1}.png"
        image.save(path)
        print(f"Ảnh {i + 1}: {time.perf_counter() - t1:.1f} s → {path}")

    peak = torch.cuda.max_memory_allocated() / 2**30
    print(f"VRAM đỉnh (torch): {peak:.2f} GB")

    # Giải phóng — đúng quy trình sẽ dùng trong pipeline/images.py
    del pipe
    gc.collect()
    torch.cuda.empty_cache()
    free, _ = torch.cuda.mem_get_info()
    print(f"VRAM trống sau unload: {free / 2**30:.2f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
