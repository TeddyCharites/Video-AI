"""Danh mục ngôn ngữ (đọc từ config.yaml → languages).

Mọi chỗ cần biết "ngôn ngữ X đọc bằng engine nào, giọng gì, tốc độ bao nhiêu" đều hỏi qua đây,
nên thêm ngôn ngữ mới chỉ cần thêm một mục trong config.yaml (và một engine TTS đọc được nó).
"""


def codes(cfg: dict) -> list[str]:
    """Mã các ngôn ngữ app hỗ trợ, theo thứ tự trong config."""
    return list(cfg["languages"])


def get(cfg: dict, code: str) -> dict:
    try:
        return cfg["languages"][code]
    except KeyError:
        raise ValueError(f"Ngôn ngữ không được hỗ trợ: {code!r} (có: {', '.join(codes(cfg))})") from None


def name(cfg: dict, code: str) -> str:
    return get(cfg, code)["name"]


def validate(cfg: dict, langs: list[str]) -> list[str]:
    """Bỏ trùng, giữ thứ tự, báo lỗi nếu có mã lạ."""
    out = []
    for code in langs:
        get(cfg, code)
        if code not in out:
            out.append(code)
    return out


def units(text: str) -> int:
    """Số "đơn vị đọc": tiếng (tiếng Việt) hoặc từ (tiếng Anh) — đều là cụm cách nhau bởi khoảng trắng."""
    return len(text.split())


def estimate_sec(cfg: dict, code: str, text: str) -> float:
    return units(text) / get(cfg, code)["units_per_sec"]


def public_info(cfg: dict) -> list[dict]:
    """Thông tin gửi cho giao diện."""
    return [{"code": c, "name": v["name"], "units_per_sec": v["units_per_sec"],
             "default_voice": v["voice"], "tts": v["tts"]} for c, v in cfg["languages"].items()]
