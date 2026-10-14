"""Bước 2: tạo ảnh cho từng cảnh.

Hai backend (chọn bằng images.backend trong config.yaml):
- "placeholder": vẽ ảnh nét đen đơn giản bằng Pillow — test pipeline không cần GPU.
- "flux": FLUX.2 [klein] 4B bản 4-bit NF4 (xem tests/test_flux.py để đo VRAM/tốc độ).

Quy tắc VRAM: load model MỘT lần → tạo ảnh cho mọi cảnh → unload (xem ImageModel.close()).
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.storage import replace_file
from pipeline.gpu import free_vram, report_vram


class PlaceholderImageModel:
    def __init__(self, width: int, height: int):
        self.width, self.height = width, height

    def generate_image(self, prompt: str, out_path: Path, label: str = "") -> None:
        w, h = self.width, self.height
        img = Image.new("RGB", (w, h), "white")
        draw = ImageDraw.Draw(img)
        line = max(4, w // 200)
        # Vài hình khối nét đậm để animation vẽ bảng có "nét" mà vẽ
        draw.rectangle([w * 0.06, h * 0.08, w * 0.94, h * 0.92], outline="black", width=line)
        draw.ellipse([w * 0.12, h * 0.25, w * 0.36, h * 0.25 + w * 0.24], outline="black", width=line)
        draw.line([w * 0.45, h * 0.75, w * 0.6, h * 0.3, w * 0.75, h * 0.6, w * 0.88, h * 0.25],
                  fill="black", width=line, joint="curve")
        font = ImageFont.load_default(size=h // 6)
        draw.text((w * 0.2, h * 0.72), label, fill="black", font=font, anchor="mm")
        _save_png_atomic(img, out_path)

    def close(self) -> None:
        pass


class FluxImageModel:
    """FLUX.2 [klein] 4B bản NF4 4-bit (transformer + text encoder Qwen3 đều 4-bit)."""

    def __init__(self, img_cfg: dict, width: int, height: int):
        import torch
        from diffusers import Flux2KleinPipeline
        self._torch = torch
        self.cfg, self.width, self.height = img_cfg, width, height
        self.pipe = Flux2KleinPipeline.from_pretrained(img_cfg["model"], dtype=torch.bfloat16)
        if img_cfg["cpu_offload"]:
            # Chỉ module đang chạy nằm trên GPU (đỉnh ~2.8GB ở 1280x720). Đo trên RTX 5060:
            # NHANH hơn để cả pipeline trên GPU (7.3s vs 11.6s/ảnh) vì cách kia (~6.6GB) sát trần
            # 8GB → Windows âm thầm tràn sang RAM chung (sysmem fallback) và chậm hẳn.
            self.pipe.enable_model_cpu_offload()
        else:
            self.pipe.to("cuda")

    def generate_image(self, prompt: str, out_path: Path, label: str = "") -> None:
        # Seed cố định theo cảnh → tạo lại cùng prompt cho ra cùng ảnh (dễ so sánh khi sửa prompt)
        seed = self.cfg["seed"] + (int(label) if label.isdigit() else 0)
        image = self.pipe(
            prompt=prompt, width=self.width, height=self.height,
            num_inference_steps=self.cfg["steps"], guidance_scale=self.cfg["guidance_scale"],
            generator=self._torch.Generator("cpu").manual_seed(seed),
        ).images[0]
        _save_png_atomic(image, out_path)

    def close(self) -> None:
        # Bỏ mọi tham chiếu tới model; free_vram() (gc + empty_cache) sẽ trả VRAM về
        self.pipe.maybe_free_model_hooks()
        del self.pipe


def load_image_model(cfg: dict, width: int, height: int):
    backend = cfg["images"]["backend"]
    if backend == "placeholder":
        return PlaceholderImageModel(width, height)
    if backend == "flux":
        report_vram("trước khi load FLUX")
        return FluxImageModel(cfg["images"], width, height)
    raise ValueError(f"images.backend không hợp lệ: {backend!r}")


def unload_image_model(model) -> None:
    model.close()
    del model
    free_vram()
    report_vram("sau khi unload model ảnh")


def _save_png_atomic(img: Image.Image, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.stem + ".tmp.png")
    img.save(tmp, format="PNG")
    replace_file(tmp, out_path)  # có thử lại nếu file đang bị mở (Windows)
