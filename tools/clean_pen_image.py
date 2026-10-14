"""
Xoá dòng chữ tiếng Trung trên thân bút của ảnh bàn tay mặc định (repo whiteboard-stream-animation).
Ghi ra bản sạch assets/drawing-hand-clean.png, KHÔNG sửa file gốc trong third_party/.

Chạy bằng venv của repo vẽ bảng (có sẵn OpenCV):
    third_party\\whiteboard-stream-animation\\.venv\\Scripts\\python tools\\clean_pen_image.py
"""

from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "third_party" / "whiteboard-stream-animation" / "assets" / "drawing-hand.png"
DST = PROJECT_ROOT / "assets" / "drawing-hand-clean.png"

# Trục thân bút đo trên ảnh gốc 1069x1472: đi qua tâm chữ đầu tiên, nghiêng ~39.5°
AXIS_POINT = np.array([440.0, 335.0])
AXIS_ANGLE_DEG = 39.5
BAND_START, BAND_END = -60, 450   # đoạn dọc trục có chữ (px)
BAND_HALF_WIDTH = 46              # nửa bề rộng dải, nhỏ hơn thân bút để không ăn vào đường viền
DARK_THRESHOLD = 170              # điểm ảnh xám hơn mức này trong dải = chữ


def main() -> None:
    rgba = cv2.imread(str(SRC), cv2.IMREAD_UNCHANGED)
    if rgba is None or rgba.shape[2] != 4:
        raise SystemExit(f"Không đọc được ảnh RGBA: {SRC}")
    bgr, alpha = rgba[:, :, :3].copy(), rgba[:, :, 3]

    # 1. Dải hình chữ nhật xoay theo trục bút
    d = np.array([np.cos(np.radians(AXIS_ANGLE_DEG)), np.sin(np.radians(AXIS_ANGLE_DEG))])
    n = np.array([-d[1], d[0]])  # pháp tuyến
    corners = [AXIS_POINT + d * t + n * s
               for t, s in [(BAND_START, -BAND_HALF_WIDTH), (BAND_END, -BAND_HALF_WIDTH),
                            (BAND_END, BAND_HALF_WIDTH), (BAND_START, BAND_HALF_WIDTH)]]
    band = np.zeros(alpha.shape, np.uint8)
    cv2.fillPoly(band, [np.round(corners).astype(np.int32)], 255)

    # 2. Trong dải: điểm tối = chữ. Nới rộng vài px để xoá cả viền răng cưa quanh nét chữ
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    text_mask = ((gray < DARK_THRESHOLD) & (band > 0)).astype(np.uint8) * 255
    text_mask = cv2.dilate(text_mask, np.ones((5, 5), np.uint8), iterations=1)

    # 3. Inpaint: lấp vùng chữ bằng màu loang từ xung quanh (giữ được bóng xám của thân bút)
    cleaned = cv2.inpaint(bgr, text_mask, inpaintRadius=7, flags=cv2.INPAINT_TELEA)

    DST.parent.mkdir(parents=True, exist_ok=True)
    out = np.dstack([cleaned, alpha])
    # imencode + write_bytes thay cho imwrite: an toàn với đường dẫn có ký tự đặc biệt trên Windows
    ok, buf = cv2.imencode(".png", out)
    if not ok:
        raise SystemExit("Không mã hoá được PNG")
    DST.write_bytes(buf.tobytes())
    print(f"Đã xoá {int((text_mask > 0).sum())} điểm ảnh chữ → {DST}")


if __name__ == "__main__":
    main()
