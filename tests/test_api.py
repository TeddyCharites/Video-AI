"""
Test backend FastAPI từ đầu đến cuối, KHÔNG cần GPU / Ollama:
chia cảnh theo đoạn văn, ảnh giả (Pillow), bản dịch giả ("[en] …"). TTS (VieNeu + Kokoro, CPU),
animation vẽ bảng và ghép video chạy thật. Dữ liệu ghi vào outputs/tests/api_* rồi xoá khi xong.

Chạy (từ thư mục gốc project):
    .venv\\Scripts\\python tests\\test_api.py
"""

import json
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

SCRIPT = "Mặt trời mọc ở đằng đông. Trời sáng dần.\n\nCây xanh cần ánh sáng để lớn lên."
OVERRIDES = {"images.backend": "placeholder", "translate.backend": "fake", "shorts.backend": "fake"}


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


def make_v1_project(outputs: Path) -> str:
    """Dự án định dạng CŨ (v1, trước đa ngôn ngữ) để kiểm tra việc tự chuyển khi server khởi động."""
    import numpy as np
    import soundfile as sf
    pid = "20200101-000000-du-an-cu"
    d = outputs / pid
    (d / "scenes" / "01").mkdir(parents=True)
    (d / "project.json").write_text(json.dumps({
        "id": pid, "name": "Dự án cũ", "script": "Câu cũ.", "created_at": "2020-01-01T00:00:00",
        "settings": {"voice": "Thiện Minh", "aspect_ratio": "16:9"}, "final_status": "missing"},
        ensure_ascii=False), encoding="utf-8")
    (d / "scenes.json").write_text(json.dumps([{
        "id": "01", "order": 1, "narration": "Câu cũ.", "image_prompt": "a sun", "duration_sec": 1.0,
        "image_status": "missing", "audio_status": "ready", "clip_status": "missing", "error": None}],
        ensure_ascii=False), encoding="utf-8")
    sf.write(str(d / "scenes" / "01" / "audio.wav"), np.zeros(48000, dtype=np.float32), 48000)
    return pid


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
    old_pid = make_v1_project(tmp)
    app = create_app(cfg, overrides=OVERRIDES)

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

            print("0. Tự chuyển dự án định dạng cũ khi server khởi động")
            p = c.get(f"/api/projects/{old_pid}").json()
            check(p["schema_version"] == 2 and p["languages"] == ["vi"] and p["settings"]["voices"] == {"vi": "Thiện Minh"},
                  "project.json → v2, languages [vi], voices {vi}")
            check(p["scenes"][0]["langs"]["vi"]["narration"] == "Câu cũ." and p["scenes"][0]["langs"]["vi"]["audio_url"],
                  "scenes.json → langs.vi, audio.wav đã chuyển vào scenes/01/vi/")
            check((tmp / old_pid / "project.json.v1.bak").is_file(), "có bản sao lưu *.v1.bak")

            print("1. Tạo dự án vi + en, chia cảnh")
            r = c.post("/api/projects", json={"name": "Test API", "script": SCRIPT})
            check(r.status_code == 201, "tạo dự án → 201")
            p = r.json()
            pid = p["id"]
            check(p["source_lang"] == "vi" and p["languages"] == ["vi", "en"], "mặc định: gốc vi, dịch sang en")
            check(c.post("/api/projects", json={"name": "x", "script": "x", "languages": ["xx"]}).status_code == 422,
                  "ngôn ngữ lạ → 422")
            check(c.get("/api/voices?lang=en").json()[0]["id"].startswith(("af_", "am_")), "giọng tiếng Anh (Kokoro)")
            job = c.post(f"/api/projects/{pid}/split", json={"mode": "paragraphs"}).json()
            check(wait_job(c, job["id"])["status"] == "done", "chia cảnh xong")
            p = c.get(f"/api/projects/{pid}").json()
            check(len(p["scenes"]) == 2 and p["scenes"][0]["langs"]["en"]["text_status"] == "missing",
                  "2 cảnh, bản dịch en: missing")

            print("2. Chưa dịch thì chưa đọc / chưa xuất được tiếng Anh")
            r = c.post(f"/api/projects/{pid}/scenes/01/audio", json={"lang": "en"})
            check(r.status_code == 409, "tạo audio en khi chưa có bản dịch → 409")
            r = c.post(f"/api/projects/{pid}/export", json={"langs": ["en"]})
            fields = {f for pr in r.json()["detail"]["problems"] for f in pr["not_ready"]}
            check(r.status_code == 409 and "text_status" in fields, "xuất en → 409, liệt kê thiếu bản dịch")

            print("3. Dịch (bản dịch giả) → bản dịch + tiêu đề en")
            job = wait_job(c, c.post(f"/api/projects/{pid}/translate", json={}).json()["id"])
            p = c.get(f"/api/projects/{pid}").json()
            en1 = p["scenes"][0]["langs"]["en"]
            check(job["status"] == "done" and en1["text_status"] == "ready" and en1["narration"].startswith("[en]"),
                  "dịch xong: text_status ready")
            check(p["titles"]["en"] == "[en] Test API", "tiêu đề video tiếng Anh được dịch")

            print("4. Ảnh + audio vi + audio en (2 làn song song, chống trùng theo ngôn ngữ)")
            j_img = c.post(f"/api/projects/{pid}/images").json()
            j_vi = c.post(f"/api/projects/{pid}/audio", json={"lang": "vi"}).json()
            j_en = c.post(f"/api/projects/{pid}/audio", json={"lang": "en"}).json()
            j_en2 = c.post(f"/api/projects/{pid}/audio", json={"lang": "en"}).json()
            check(j_vi["id"] != j_en["id"] and j_en2["id"] == j_en["id"],
                  "audio vi và en là 2 job; bấm audio en 2 lần → cùng 1 job")
            for j in (j_img, j_vi, j_en):
                check(wait_job(c, j["id"])["status"] == "done", f"{j['label']} xong")
            p = c.get(f"/api/projects/{pid}").json()
            s = p["summary"]
            check(s["images"]["ready"] == 2 and s["langs"]["vi"]["audio"]["ready"] == 2
                  and s["langs"]["en"]["audio"]["ready"] == 2, "ảnh 2/2, audio vi 2/2, audio en 2/2")
            check("/scenes/01/en/audio.wav" in p["scenes"][0]["langs"]["en"]["audio_url"], "audio en ở scenes/01/en/")

            print("5. Xuất cả vi lẫn en")
            job = wait_job(c, c.post(f"/api/projects/{pid}/export", json={"langs": ["vi", "en"]}).json()["id"])
            p = c.get(f"/api/projects/{pid}").json()
            check(job["status"] == "done", f"xuất xong ({job['message']})")
            check(all(p["finals"][l]["status"] == "ready" and p["finals"][l]["url"] and p["finals"][l]["srt_url"]
                      for l in ("vi", "en")), "final.vi.mp4 + final.en.mp4 + .srt đều ready")
            srt_en = (tmp / pid / "final.en.srt").read_text(encoding="utf-8")
            check("[en]" in srt_en and "Mặt trời" in srt_en, "phụ đề en lấy từ bản dịch")
            check(p["finals"]["en"]["engine"] == c.get("/api/settings").json()["render_engine"], "đúng engine ghép video")

            print("6. Theo dõi phụ thuộc theo ngôn ngữ")
            sc = c.patch(f"/api/projects/{pid}/scenes/01", json={"narration": "Mặt trời mọc rất sớm. Trời sáng dần."}).json()
            vi, en = sc["langs"]["vi"], sc["langs"]["en"]
            check(vi["audio_status"] == "stale" and vi["clip_status"] == "stale", "sửa lời vi → audio + clip vi stale")
            check(en["text_status"] == "stale" and en["audio_status"] == "stale" and en["clip_status"] == "stale",
                  "sửa lời vi → bản dịch + audio + clip en stale")
            check(sc["image_status"] == "ready", "ảnh không bị ảnh hưởng")
            p = c.get(f"/api/projects/{pid}").json()
            check(p["finals"]["vi"]["status"] == "stale" and p["finals"]["en"]["status"] == "stale", "cả 2 video → stale")

            sc = c.patch(f"/api/projects/{pid}/scenes/02",
                         json={"translations": {"en": "Green trees need light to grow."}}).json()
            check(sc["langs"]["en"]["edited"] and sc["langs"]["en"]["text_status"] == "ready"
                  and sc["langs"]["en"]["audio_status"] == "stale" and sc["langs"]["vi"]["audio_status"] == "ready",
                  "sửa bản dịch en → chỉ audio en stale, đánh dấu 'đã sửa tay'")
            sc = c.post(f"/api/projects/{pid}/scenes/01/translations/en/keep").json()
            check(sc["langs"]["en"]["text_status"] == "ready", "giữ bản dịch cũ → text_status ready")

            job = wait_job(c, c.post(f"/api/projects/{pid}/translate", json={}).json()["id"])
            p = c.get(f"/api/projects/{pid}").json()
            check(p["scenes"][1]["langs"]["en"]["narration"] == "Green trees need light to grow.",
                  "'Dịch tất cả' KHÔNG ghi đè bản dịch đã sửa tay")
            job = wait_job(c, c.post(f"/api/projects/{pid}/scenes/02/translate", json={}).json()["id"])
            sc = c.get(f"/api/projects/{pid}").json()["scenes"][1]
            check(sc["langs"]["en"]["narration"].startswith("[en]") and not sc["langs"]["en"]["edited"],
                  "'Dịch lại' một cảnh → ghi đè, bỏ dấu 'đã sửa tay'")

            sc = c.patch(f"/api/projects/{pid}/scenes/02", json={"image_prompt": "a tree"}).json()
            check(sc["image_status"] == "stale" and sc["langs"]["vi"]["clip_status"] == "stale"
                  and sc["langs"]["en"]["clip_status"] == "stale", "sửa mô tả ảnh → ảnh + clip MỌI ngôn ngữ stale")

            print("7. Nhạc nền + phụ đề song ngữ, xuất lại phần đã cũ")
            r = c.post(f"/api/projects/{pid}/music", files={"file": ("gia.mp3", b"x" * 500, "audio/mpeg")})
            check(r.status_code == 422, "file nhạc giả → 422 (ffprobe kiểm tra)")
            wav = tmp / "nhac.wav"
            make_music(wav, seconds=5)  # ngắn hơn video → phải tự lặp
            with open(wav, "rb") as f:
                p = c.post(f"/api/projects/{pid}/music", files={"file": ("nhạc.wav", f, "audio/wav")}).json()
            check(p["music_url"] is not None, "tải nhạc lên")
            p = c.patch(f"/api/projects/{pid}", json={"music_volume": 0.3, "subtitles_bilingual": True}).json()
            check(p["settings"]["subtitles_bilingual"] and p["settings"]["music"]["volume"] == 0.3,
                  "bật phụ đề song ngữ, âm lượng nhạc 0.3")
            jobs = [c.post(f"/api/projects/{pid}/images").json(),
                    c.post(f"/api/projects/{pid}/audio", json={"lang": "vi"}).json(),
                    c.post(f"/api/projects/{pid}/audio", json={"lang": "en"}).json()]
            for j in jobs:
                job = wait_job(c, j["id"])
                check(job["status"] == "done", f"{job['label']}: làm lại {job['total']} cảnh")
            job = wait_job(c, c.post(f"/api/projects/{pid}/export", json={"langs": ["vi"]}).json()["id"])
            p = c.get(f"/api/projects/{pid}").json()
            check(job["status"] == "done" and p["finals"]["vi"]["status"] == "ready"
                  and p["finals"]["en"]["status"] == "stale", "chỉ xuất vi → vi ready, en vẫn stale")
            check(p["finals"]["vi"]["bilingual"] == "en" and p["finals"]["vi"]["srt_bilingual_url"],
                  "có final.vi-en.srt (song ngữ)")
            bi = (tmp / pid / "final.vi-en.srt").read_text(encoding="utf-8").split("\n\n")[0].splitlines()
            check(len(bi) == 4 and bi[3].startswith("[en]"), f"mỗi dòng phụ đề song ngữ có 2 dòng: {bi[2]!r} / {bi[3]!r}")
            if p["finals"]["vi"]["engine"] == "remotion" and p["settings"].get("title", True):
                level = rms(tmp / pid / "final.vi.mp4", 1.2, 2.4)
                check(level > 0.005, f"màn hình tiêu đề (không có giọng) nghe thấy nhạc: RMS={level:.4f}")
            r = c.delete(f"/api/projects/{pid}/music").json()
            check(r["music_url"] is None, "xoá nhạc")

            print("7b. Shorts: gợi ý, tạo dự án con, xuất 9:16, đồng bộ với dự án gốc")
            job = wait_job(c, c.post(f"/api/projects/{pid}/shorts/suggest").json()["id"])
            check(job["status"] == "error" and "quá ngắn" in (job["error"] or ""),
                  f"video quá ngắn → gợi ý Shorts báo lỗi rõ ràng ({job['error']})")
            r = c.post(f"/api/projects/{pid}/shorts", json={"scene_ids": ["02", "01"]})
            check(r.status_code == 422, "cảnh không liền nhau / sai thứ tự → 422")
            r = c.post(f"/api/projects/{pid}/shorts", json={"scene_ids": ["01", "02"], "titles": {"vi": "Short thử"}})
            check(r.status_code == 201, "tạo Short → 201")
            child = r.json()
            cid = child["id"]
            check(child["kind"] == "short" and child["settings"]["aspect_ratio"] == "9:16"
                  and child["parent_info"]["id"] == pid and child["parent_info"]["changes"] == [],
                  "Short là dự án con 9:16, liên kết dự án gốc")
            # Short chép nguyên trạng thái của gốc (vi đã xuất lại → ready; clip en ở gốc còn stale → stale)
            parent = c.get(f"/api/projects/{pid}").json()
            same = lambda s, ps: (s["image_status"] == ps["image_status"] and all(
                s["langs"][l][k] == ps["langs"][l][k] for l in ("vi", "en") for k in ("audio_status", "clip_status")))
            check(all(same(s, ps) for s, ps in zip(child["scenes"], parent["scenes"]))
                  and all(s["langs"]["vi"]["clip_status"] == "ready" for s in child["scenes"])
                  and (tmp / cid / "scenes" / "01" / "vi" / "clip.mp4").is_file(),
                  "dùng lại ảnh + audio + clip của dự án gốc (không tạo lại)")
            check(child["outro"]["en"]["text"].startswith("Want the full story"), "câu kết tiếng Anh theo config")
            parent = c.get(f"/api/projects/{pid}").json()
            check([s["id"] for s in parent["shorts"]] == [cid] and parent["shorts"][0]["scene_ids"] == ["01", "02"],
                  "dự án gốc liệt kê Short con")
            check(c.post(f"/api/projects/{cid}/split", json={"mode": "paragraphs", "force": True}).status_code == 409,
                  "Short không chia cảnh lại → 409")
            check(c.post(f"/api/projects/{cid}/shorts/suggest").status_code == 409, "không gợi ý Short từ Short → 409")
            p = c.patch(f"/api/projects/{cid}", json={"cta": {"vi": "Xem bản đầy đủ nhé!"}}).json()
            check(p["outro"]["vi"]["text"] == "Xem bản đầy đủ nhé!", "sửa câu kết")
            check(c.patch(f"/api/projects/{pid}", json={"cta": {"vi": "x"}}).status_code == 422,
                  "dự án dài không có câu kết → 422")
            if c.get("/api/settings").json()["render_engine"] == "remotion":
                job = wait_job(c, c.post(f"/api/projects/{cid}/export", json={"langs": ["vi"]}).json()["id"])
                p = c.get(f"/api/projects/{cid}").json()
                check(job["status"] == "done" and p["finals"]["vi"]["status"] == "ready", f"xuất Short ({job['message']})")
                from pipeline.render import video_size
                check(video_size(tmp / cid / "final.vi.mp4") == (720, 1280), "Short 720x1280 (9:16)")
                clips = sum(s["langs"]["vi"]["duration_sec"] for s in p["scenes"])
                check(p["finals"]["vi"]["duration_sec"] > clips + 2 and p["outro"]["vi"]["audio_status"] == "ready",
                      f"có màn hình kết + giọng đọc câu kết ({p['finals']['vi']['duration_sec']}s)")
            else:
                print("  (bỏ qua xuất Short: chưa cài Remotion)")
            c.patch(f"/api/projects/{pid}/scenes/01", json={"image_prompt": "a sunrise over hills, black ink on white"})
            p = c.get(f"/api/projects/{cid}").json()
            changes = p["parent_info"]["changes"]
            check(len(changes) == 1 and changes[0]["id"] == "01" and changes[0]["what"] == ["image"],
                  f"sửa cảnh ở dự án gốc → Short thấy thay đổi ({changes})")
            r = c.post(f"/api/projects/{cid}/sync-parent").json()
            sc = r["scenes"][0]
            check(r["synced"] == 1 and sc["image_prompt"].startswith("a sunrise") and sc["image_status"] == "stale"
                  and r["parent_info"]["changes"] == [] and r["finals"]["vi"]["status"] in ("stale", "missing"),
                  "cập nhật từ dự án gốc → mô tả ảnh mới, ảnh cần làm lại, video cũ")
            check(c.delete(f"/api/projects/{cid}").status_code == 204
                  and c.get(f"/api/projects/{pid}").json()["shorts"] == [], "xoá Short → gỡ khỏi dự án gốc")

            print("8. Đổi giọng, bớt / thêm ngôn ngữ")
            p = c.patch(f"/api/projects/{pid}", json={"voices": {"en": "af_heart"}}).json()
            check(all(s["langs"]["en"]["audio_status"] == "stale" for s in p["scenes"])
                  and all(s["langs"]["vi"]["audio_status"] == "ready" for s in p["scenes"]),
                  "đổi giọng en → chỉ audio en stale")
            check(c.patch(f"/api/projects/{pid}", json={"voices": {"en": "Thiện Minh"}}).status_code == 422,
                  "giọng không thuộc ngôn ngữ → 422")
            p = c.patch(f"/api/projects/{pid}", json={"languages": []}).json()
            check(p["languages"] == ["vi"] and "en" not in p["scenes"][0]["langs"]
                  and not (tmp / pid / "scenes" / "01" / "en").exists() and not (tmp / pid / "final.en.mp4").exists(),
                  "bỏ tiếng Anh → xoá dữ liệu en")
            p = c.patch(f"/api/projects/{pid}", json={"languages": ["en"]}).json()
            check(p["languages"] == ["vi", "en"] and p["scenes"][0]["langs"]["en"]["text_status"] == "missing",
                  "thêm lại tiếng Anh → bản dịch missing")

            print("9. Xoá dự án")
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
