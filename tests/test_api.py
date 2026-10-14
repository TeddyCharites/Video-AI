"""
Test backend FastAPI từ đầu đến cuối, KHÔNG cần GPU: chia cảnh theo đoạn văn + ảnh giả (Pillow).
TTS (CPU) và animation vẽ bảng chạy thật. Dữ liệu ghi vào outputs/tests/api_* rồi xoá khi xong.

Chạy (từ thư mục gốc project):
    .venv\\Scripts\\python tests\\test_api.py
"""

import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import load_config  # noqa: E402
from app.main import create_app  # noqa: E402

SCRIPT = "Mặt trời mọc ở đằng đông.\n\nCây xanh cần ánh sáng để lớn lên."


def wait_job(client: TestClient, job_id: str, timeout: float = 300) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error", "cancelled"):
            return job
        time.sleep(0.3)
    raise TimeoutError(f"job {job_id} chưa xong sau {timeout}s")


def make_music(path: Path, seconds: float) -> None:
    """Sinh "nhạc" thử: hợp âm Đô trưởng 48kHz stereo."""
    import numpy as np
    import soundfile as sf
    t = np.linspace(0, seconds, int(48000 * seconds), endpoint=False)
    tone = sum(np.sin(2 * np.pi * f * t) for f in (261.6, 329.6, 392.0)) * 0.2
    sf.write(str(path), np.stack([tone, tone], axis=1), 48000)


def rms(video: Path, start: float, end: float) -> float:
    """Âm lượng trung bình (RMS) của tiếng trong video từ start → end giây."""
    import subprocess
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-to", str(end), "-i", str(video),
                          "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True, check=True).stdout
    samples = np.frombuffer(raw, dtype=np.float32)
    return float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)
    print(f"  OK  {msg}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config()
    tests_dir = cfg["outputs_dir"] / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="api_", dir=tests_dir))
    cfg["outputs_dir"] = tmp
    app = create_app(cfg, overrides={"images.backend": "placeholder"})

    # Theo dõi song song: mỗi làn (gpu / cpu) không bao giờ có 2 job "running" cùng lúc,
    # nhưng hai làn thì được chạy song song với nhau
    max_running = {"gpu": 0, "cpu": 0}
    overlap = {"seen": False}
    stop_watch = threading.Event()

    def watch():
        while not stop_watch.is_set():
            running = [j.lane for j in app.state.jobs.list() if j.status == "running"]
            for lane in max_running:
                max_running[lane] = max(max_running[lane], running.count(lane))
            overlap["seen"] |= len(set(running)) == 2
            time.sleep(0.05)

    try:
        with TestClient(app) as c:
            threading.Thread(target=watch, daemon=True).start()

            print("1. Tạo dự án + chia cảnh")
            r = c.post("/api/projects", json={"name": "Test API", "script": SCRIPT})
            check(r.status_code == 201, "tạo dự án → 201")
            pid = r.json()["id"]
            check(c.post("/api/projects", json={"name": "", "script": "x"}).status_code == 422,
                  "tên rỗng → 422")
            check(c.get("/api/projects/..%2F..%2Fetc").status_code == 404, "project_id lạ → 404")

            job = c.post(f"/api/projects/{pid}/split", json={"mode": "paragraphs"}).json()
            check(wait_job(c, job["id"])["status"] == "done", "job chia cảnh xong")
            p = c.get(f"/api/projects/{pid}").json()
            check(len(p["scenes"]) == 2, "có 2 cảnh")
            check(c.post(f"/api/projects/{pid}/split", json={"mode": "paragraphs"}).status_code == 409,
                  "chia lại khi đã có cảnh mà không force → 409")

            print("2. Xuất khi chưa đủ → 409 kèm danh sách cảnh")
            r = c.post(f"/api/projects/{pid}/export")
            check(r.status_code == 409 and len(r.json()["detail"]["problems"]) == 2, "export → 409, 2 cảnh thiếu")

            print("3. Hàng đợi: 2 làn song song, chống trùng, huỷ")
            j_img = c.post(f"/api/projects/{pid}/images").json()
            j_audio = c.post(f"/api/projects/{pid}/audio").json()
            # Job audio chạy ngay (làn CPU rảnh) → job "tạo lại audio cảnh 02" phải CHỜ sau nó
            j_one = c.post(f"/api/projects/{pid}/scenes/02/audio").json()
            j_one2 = c.post(f"/api/projects/{pid}/scenes/02/audio").json()
            check(j_one["status"] == "queued" and j_one2["id"] == j_one["id"] and not j_one2["new"],
                  "bấm 'tạo lại audio' 2 lần khi đang chờ → cùng 1 job")
            check(c.post(f"/api/projects/{pid}/split", json={"mode": "paragraphs", "force": True}).status_code == 409,
                  "chia lại khi đang có tác vụ ảnh/audio → 409")
            j_cancel = c.post(f"/api/projects/{pid}/scenes/01/image").json()
            r = c.delete(f"/api/jobs/{j_cancel['id']}").json()
            check(r["status"] == "cancelled", "huỷ job đang chờ → cancelled")
            check(wait_job(c, j_img["id"])["status"] == "done", "job ảnh xong")
            check(wait_job(c, j_audio["id"])["status"] == "done", "job audio xong")
            check(wait_job(c, j_one["id"])["status"] == "done", "job tạo lại audio cảnh 02 xong")
            p = c.get(f"/api/projects/{pid}").json()
            check(p["summary"]["images"]["ready"] == 2 and p["summary"]["audio"]["ready"] == 2, "ảnh 2/2, audio 2/2")
            check(p["scenes"][0]["image_url"].startswith(f"/files/{pid}/scenes/01/image.png?v="), "có URL ảnh")
            check(c.get(p["scenes"][0]["audio_url"]).status_code == 200, "tải được audio qua /files")

            print("4. Xuất video")
            job = c.post(f"/api/projects/{pid}/export").json()
            job = wait_job(c, job["id"])
            check(job["status"] == "done", f"xuất xong ({job['message']})")
            p = c.get(f"/api/projects/{pid}").json()
            check(p["final_status"] == "ready" and p["final_url"], "final.mp4 ready")
            check(all(s["clip_status"] == "ready" for s in p["scenes"]), "mọi clip ready")

            print("5. Theo dõi phụ thuộc")
            s = c.patch(f"/api/projects/{pid}/scenes/01", json={"narration": "Mặt trời mọc rất sớm."}).json()
            check(s["audio_status"] == "stale" and s["clip_status"] == "stale" and s["image_status"] == "ready",
                  "sửa lời đọc → audio + clip stale, ảnh vẫn ready")
            s = c.patch(f"/api/projects/{pid}/scenes/02", json={"image_prompt": "a tree"}).json()
            check(s["image_status"] == "stale" and s["audio_status"] == "ready", "sửa prompt → ảnh stale")
            p = c.get(f"/api/projects/{pid}").json()
            check(p["final_status"] == "stale", "final.mp4 → stale")
            check({x["id"] for x in p["export_problems"]} == {"01", "02"}, "export/check liệt kê đúng 2 cảnh")

            # Đổi thứ tự + thêm cảnh mới + xoá không cảnh nào
            scenes = p["scenes"]
            body = {"scenes": [
                {"id": "02", "narration": scenes[1]["narration"], "image_prompt": "a tree"},
                {"id": "01", "narration": scenes[0]["narration"], "image_prompt": scenes[0]["image_prompt"]},
                {"narration": "Cảnh mới thêm vào.", "image_prompt": ""},
            ]}
            p = c.put(f"/api/projects/{pid}/scenes", json=body).json()
            check([x["id"] for x in p["scenes"]] == ["02", "01", "03"], "đổi thứ tự 02,01 + cảnh mới id 03")
            check(p["scenes"][2]["image_status"] == "missing", "cảnh mới → missing")
            check(p["scenes"][0]["audio_status"] == "ready", "chỉ đổi thứ tự → audio không stale")

            p = c.delete(f"/api/projects/{pid}/scenes/03").json()
            check(len(p["scenes"]) == 2 and not (tmp / pid / "scenes" / "03").exists(), "xoá cảnh 03 + thư mục")

            print("6. Chỉ tạo lại phần stale rồi xuất lại")
            j1 = c.post(f"/api/projects/{pid}/images").json()
            j2 = c.post(f"/api/projects/{pid}/audio").json()
            j3 = c.post(f"/api/projects/{pid}/export")
            check(j3.status_code == 409, "xuất khi job ảnh/audio CHƯA chạy xong → 409 (ảnh/audio còn stale)")
            for j in (j1, j2):
                job = wait_job(c, j["id"])
                check(job["status"] == "done" and job["total"] == 1, f"{job['label']}: chỉ làm 1 cảnh")
            job = wait_job(c, c.post(f"/api/projects/{pid}/export").json()["id"])
            # Tiến độ xuất = số clip phải vẽ lại + 10 đơn vị cho bước ghép (runner.export_video)
            check(job["status"] == "done" and job["total"] == 2 + 10, "xuất lại: vẽ lại đúng 2 clip + ghép")
            info = c.get(f"/api/projects/{pid}").json()
            check(info["final_engine"] == c.get("/api/settings").json()["render_engine"],
                  f"xuất bằng engine {info['final_engine']}")

            print("6b. Nhạc nền")
            r = c.post(f"/api/projects/{pid}/music", files={"file": ("x.txt", b"abc", "text/plain")})
            check(r.status_code == 422, "file .txt → 422")
            r = c.post(f"/api/projects/{pid}/music", files={"file": ("gia.mp3", b"khong phai am thanh" * 100, "audio/mpeg")})
            check(r.status_code == 422 and "Không đọc được" in r.json()["detail"], "file .mp3 giả → 422 (ffprobe kiểm tra)")
            wav = tmp / "nhac.wav"
            make_music(wav, seconds=5)  # ngắn hơn video → phải tự lặp
            with open(wav, "rb") as f:
                p = c.post(f"/api/projects/{pid}/music", files={"file": ("nhạc nền.wav", f, "audio/wav")}).json()
            check(p["music_url"] and p["settings"]["music"]["name"] == "nhạc nền.wav", "tải nhạc lên → có music_url")
            check(p["final_status"] == "stale", "thêm nhạc → video đã xuất thành cũ")
            p = c.patch(f"/api/projects/{pid}", json={"music_volume": 0.3, "music_duck": True}).json()
            check(p["settings"]["music"]["volume"] == 0.3, "chỉnh âm lượng nhạc → lưu 0.3")
            job = wait_job(c, c.post(f"/api/projects/{pid}/export").json()["id"])
            p = c.get(f"/api/projects/{pid}").json()
            check(job["status"] == "done" and p["final_music"], f"xuất có nhạc nền ({p['final_engine']})")
            # Đo âm lượng thật: đoạn chưa có giọng đọc (màn hình tiêu đề nếu có, hoặc khoảng lặng cuối cảnh 1)
            quiet = (1.2, 2.4) if p["final_engine"] == "remotion" and p["settings"].get("title", True) else None
            if quiet:
                level = rms(tmp / pid / "final.mp4", *quiet)
                check(level > 0.005, f"đoạn tiêu đề (không có giọng) nghe thấy nhạc: RMS={level:.4f}")
            r = c.delete(f"/api/projects/{pid}/music").json()
            check(r["music_url"] is None and not (tmp / pid / "music").exists(), "xoá nhạc → hết music_url, xoá thư mục")

            print("7. Đổi giọng → mọi audio stale; xoá dự án")
            voices = c.get("/api/voices").json()
            other = next(v["id"] for v in voices if v["id"] != p["settings"]["voice"])
            p = c.patch(f"/api/projects/{pid}", json={"voice": other}).json()
            check(all(s["audio_status"] == "stale" for s in p["scenes"]), f"đổi giọng sang {other} → audio stale")
            check(any(x["id"] == pid for x in c.get("/api/projects").json()), "dự án có trong danh sách")
            check(c.delete(f"/api/projects/{pid}").status_code == 204, "xoá dự án → 204")
            check(c.get(f"/api/projects/{pid}").status_code == 404, "xoá xong → 404")

            stop_watch.set()
            check(max_running == {"gpu": 1, "cpu": 1}, f"mỗi làn tối đa 1 job chạy cùng lúc ({max_running})")
            check(overlap["seen"], "làn GPU (ảnh) và làn CPU (audio) đã chạy song song")
    finally:
        stop_watch.set()
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nTẤT CẢ ĐỀU QUA")
    return 0


if __name__ == "__main__":
    sys.exit(main())
