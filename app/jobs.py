"""Hàng đợi tác vụ với 2 LÀN, mỗi làn một luồng điều phối, chạy lần lượt từng job của làn đó:

  - làn "gpu": chia cảnh (Ollama) và tạo ảnh (FLUX) → không bao giờ có 2 model trên GPU
  - làn "cpu": giọng đọc (VieNeu ONNX) và xuất video (vẽ bảng + ffmpeg)
Hai làn chạy SONG SONG (CLAUDE.md: TTS trên CPU được chạy cùng lúc với tạo ảnh trên GPU).

Mỗi job chạy trong một TIẾN TRÌNH CON (multiprocessing):
  - xong job là tiến trình con thoát → hệ điều hành thu hồi sạch VRAM/RAM (kể cả CUDA context)
  - model có crash (vd. hết VRAM, lỗi driver) thì chỉ job đó lỗi, server vẫn sống
Tiến trình con gửi tiến độ về qua Queue; yêu cầu huỷ gửi sang qua Event.

Job nằm trong RAM; trạng thái thật của từng cảnh nằm trên đĩa (scenes.json), nên tắt app
chỉ mất danh sách job, không mất dữ liệu.
"""

import itertools
import multiprocessing as mp
import queue as queue_mod
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field

ACTIVE = ("queued", "running")
FINISHED = ("done", "error", "cancelled")
LANES = {"split": "gpu", "translate": "gpu", "translate_one": "gpu", "suggest_shorts": "gpu",
         "images": "gpu", "image": "gpu",
         "audio": "cpu", "audio_one": "cpu", "export": "cpu"}

# "spawn": tiến trình con là Python mới tinh (mặc định trên Windows; ép luôn để Linux cũng giống)
_mp = mp.get_context("spawn")


@dataclass
class Job:
    id: str
    project_id: str
    kind: str                      # split | translate | translate_one | images | image | audio | audio_one | export
    scene_id: str | None           # None = mọi cảnh cần làm
    label: str                     # mô tả tiếng Việt cho giao diện
    spec: dict = field(repr=False) # dữ liệu cho pipeline.runner.run_job
    status: str = "queued"         # queued → running → done | error | cancelled
    done: int = 0
    total: int = 0
    message: str = "Đang chờ…"
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    cancel_requested: bool = False
    lang: str | None = None        # ngôn ngữ (audio của vi và của en là 2 job khác nhau)

    @property
    def lane(self) -> str:
        return LANES[self.kind]

    def to_dict(self) -> dict:
        return {
            "id": self.id, "project_id": self.project_id, "kind": self.kind, "lane": self.lane,
            "scene_id": self.scene_id, "lang": self.lang, "label": self.label, "status": self.status,
            "done": self.done, "total": self.total, "message": self.message, "error": self.error,
            "created_at": self.created_at, "started_at": self.started_at,
            "finished_at": self.finished_at, "cancel_requested": self.cancel_requested,
        }


# ── Phần chạy trong tiến trình con ────────────────────────────────────────────
def _child_main(spec: dict, events, cancel_event) -> None:
    """Hàm top-level (pickle được) chạy trong tiến trình con."""
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    from pipeline.runner import Cancelled, RunContext, run_job

    ctx = RunContext(
        progress=lambda done, total, msg: events.put(("progress", done, total, msg)),
        log=lambda msg: print(f"[job {spec['job_id']}] {msg}", flush=True),
        is_cancelled=cancel_event.is_set,
        overrides=spec.get("overrides", {}),
    )
    try:
        run_job(spec, ctx)
    except Cancelled:
        events.put(("cancelled",))
    except Exception as e:
        traceback.print_exc()
        events.put(("error", str(e) or type(e).__name__))
    else:
        events.put(("done",))


# ── Phần chạy trong server ────────────────────────────────────────────────────
class JobQueue:
    MAX_HISTORY = 200  # giữ lại bấy nhiêu job đã xong để giao diện còn hiển thị

    def __init__(self, use_subprocess: bool = True):
        self.use_subprocess = use_subprocess
        self._jobs: dict[str, Job] = {}
        self._queues: dict[str, deque[Job]] = {"gpu": deque(), "cpu": deque()}
        self._cancel_events: dict[str, object] = {}
        self._cond = threading.Condition()   # báo cho các luồng điều phối biết có job mới
        self._ids = itertools.count(1)
        self._stop = False
        self._threads: list[threading.Thread] = []

    # ── Vòng đời ──
    def start(self) -> None:
        for lane in self._queues:
            t = threading.Thread(target=self._run_lane, args=(lane,), name=f"lane-{lane}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5) -> None:
        with self._cond:
            self._stop = True
            for job in self._jobs.values():
                if job.status in ACTIVE:
                    self._request_cancel(job)
            self._cond.notify_all()
        for t in self._threads:
            t.join(timeout)

    # ── API cho main.py ──
    def submit(self, project_id: str, kind: str, label: str, spec: dict,
               scene_id: str | None = None, lang: str | None = None) -> tuple[Job, bool]:
        """Thêm job. Nếu job giống hệt đang CHỜ thì trả lại job đó (không tạo trùng).
        Trả về (job, có phải job mới không)."""
        with self._cond:
            lane_queue = self._queues[LANES[kind]]
            for job in lane_queue:
                if (job.project_id, job.kind, job.scene_id, job.lang) == (project_id, kind, scene_id, lang):
                    return job, False
            # Chỉ gộp với job đang CHỜ. Job đang CHẠY có thể đã đi qua cảnh vừa được sửa,
            # nên vẫn xếp thêm job mới (nó chỉ làm phần còn chưa 'ready').
            job = Job(id=str(next(self._ids)), project_id=project_id, kind=kind,
                      scene_id=scene_id, label=label, spec=spec, lang=lang)
            self._jobs[job.id] = job
            lane_queue.append(job)
            self._trim_history()
            self._cond.notify_all()
            return job, True

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self, project_id: str | None = None) -> list[Job]:
        jobs = [j for j in self._jobs.values() if project_id is None or j.project_id == project_id]
        return sorted(jobs, key=lambda j: j.created_at)

    def has_active(self, project_id: str, kinds: tuple[str, ...] | None = None) -> bool:
        return any(j.status in ACTIVE and (kinds is None or j.kind in kinds) for j in self.list(project_id))

    def cancel(self, job_id: str) -> Job | None:
        """Job đang chờ → huỷ ngay. Job đang chạy → đặt cờ; nó dừng ở ranh giới cảnh kế tiếp."""
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None or job.status in FINISHED:
                return job
            if job.status == "queued":
                self._queues[job.lane].remove(job)
                job.cancel_requested = True
                self._finish(job, "cancelled", "Đã huỷ")
            else:
                self._request_cancel(job)
                job.message = "Đang huỷ (chờ xong cảnh hiện tại)…"
            return job

    def _request_cancel(self, job: Job) -> None:
        job.cancel_requested = True
        ev = self._cancel_events.get(job.id)
        if ev is not None:
            ev.set()

    # ── Luồng điều phối của mỗi làn ──
    def _run_lane(self, lane: str) -> None:
        q = self._queues[lane]
        while True:
            with self._cond:
                while not q and not self._stop:
                    self._cond.wait()
                if self._stop:
                    return
                job = q.popleft()
                job.status, job.started_at, job.message = "running", time.time(), "Đang khởi động…"
            print(f"[job {job.id}] Bắt đầu ({lane}): {job.label} ({job.project_id})", flush=True)
            try:
                if self.use_subprocess:
                    self._execute_subprocess(job)
                else:
                    self._execute_inline(job)
            except Exception as e:  # lỗi của chính bộ điều phối — không để làn chết
                traceback.print_exc()
                job.error = f"Lỗi nội bộ: {e}"
                self._finish(job, "error", "Lỗi")
            print(f"[job {job.id}] Kết thúc: {job.status} sau {job.finished_at - job.started_at:.1f}s", flush=True)

    def _execute_subprocess(self, job: Job) -> None:
        events = _mp.Queue()
        cancel_event = _mp.Event()
        if job.cancel_requested:
            cancel_event.set()
        self._cancel_events[job.id] = cancel_event
        spec = {**job.spec, "job_id": job.id}
        proc = _mp.Process(target=_child_main, args=(spec, events, cancel_event),
                           name=f"job-{job.id}", daemon=True)
        proc.start()
        result = None
        try:
            # Đọc tiến độ cho tới khi có kết quả cuối, hoặc tiến trình con chết bất thường
            while result is None:
                try:
                    msg = events.get(timeout=0.5)
                except queue_mod.Empty:
                    if not proc.is_alive():
                        break
                    continue
                if msg[0] == "progress":
                    job.done, job.total, job.message = msg[1], msg[2], msg[3]
                else:
                    result = msg
            proc.join(timeout=30)
        finally:
            self._cancel_events.pop(job.id, None)
            if proc.is_alive():
                proc.kill()

        if result is None:
            job.error = (f"Tiến trình xử lý dừng bất thường (mã thoát {proc.exitcode}). "
                         "Có thể do hết bộ nhớ/VRAM — xem cửa sổ server để biết chi tiết.")
            self._finish(job, "error", "Lỗi")
        elif result[0] == "done":
            self._finish(job, "done", job.message if job.total else "Xong")
        elif result[0] == "cancelled":
            self._finish(job, "cancelled", "Đã huỷ")
        else:
            job.error = result[1]
            self._finish(job, "error", "Lỗi")

    def _execute_inline(self, job: Job) -> None:
        """Chạy ngay trong luồng (không tiến trình con) — chỉ để debug."""
        from pipeline.runner import Cancelled, RunContext, run_job

        def progress(done, total, msg):
            job.done, job.total, job.message = done, total, msg
        ctx = RunContext(progress=progress, is_cancelled=lambda: job.cancel_requested,
                         overrides=job.spec.get("overrides", {}))
        try:
            run_job(job.spec, ctx)
        except Cancelled:
            self._finish(job, "cancelled", "Đã huỷ")
        except Exception as e:
            traceback.print_exc()
            job.error = str(e)
            self._finish(job, "error", "Lỗi")
        else:
            self._finish(job, "done", job.message if job.total else "Xong")

    def _finish(self, job: Job, status: str, message: str) -> None:
        job.status, job.message, job.finished_at = status, message, time.time()
        if job.started_at is None:
            job.started_at = job.finished_at

    def _trim_history(self) -> None:
        finished = [j for j in self.list() if j.status in FINISHED]
        for j in finished[: max(0, len(finished) - self.MAX_HISTORY)]:
            del self._jobs[j.id]
