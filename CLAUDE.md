# Video Studio Local — Hướng dẫn cho Claude Code

## Mục tiêu
Web app chạy local: người dùng nhập **kịch bản (lời thoại)** → app tạo ra **video hoàn chỉnh** dạng animation vẽ bảng (whiteboard), có giọng đọc.
**Đa ngôn ngữ:** kịch bản viết bằng một ngôn ngữ gốc (mặc định tiếng Việt), app dịch sang ngôn ngữ khác (hiện có tiếng Anh) và xuất video cho từng ngôn ngữ. Thiết kế để thêm ngôn ngữ mới dễ dàng.
Dùng cá nhân, chạy hoàn toàn offline trên máy, không dùng API trả phí.

## Máy chạy
- Windows, CPU i5-12400F, GPU **RTX 5060 8GB VRAM**, RAM 32GB
- RTX 50-series: cần driver NVIDIA mới nhất và PyTorch bản có hỗ trợ CUDA cho dòng 50-series. Khi cài PyTorch, lấy lệnh cài từ trang chủ pytorch.org, chọn bản CUDA mới nhất.
- Đường dẫn file phải chạy đúng trên Windows (dùng `pathlib`, không hard-code dấu `/` hay `\`).

## Thành phần
| Bước | Việc | Công cụ | Repo / link |
|---|---|---|---|
| 1 | Chia kịch bản thành cảnh | Qwen3.6-35B-A3B (GGUF, chạy qua Ollama) | https://huggingface.co/Qwen/Qwen3.6-35B-A3B · GGUF: https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF |
| 1b | Dịch lời đọc | cùng model Qwen3.6 qua Ollama | (như trên) |
| 2 | Tạo ảnh cho từng cảnh (dùng chung mọi ngôn ngữ) | FLUX.2 [klein] 4B (Diffusers, `Flux2KleinPipeline`) | https://huggingface.co/black-forest-labs/FLUX.2-klein-4B · bản 4-bit: https://huggingface.co/hlhc/FLUX.2-klein-4B-bnb-4bit |
| 3 | Giọng đọc tiếng Việt | VieNeu-TTS v3 Turbo (`pip install vieneu`) | https://github.com/pnnbao97/VieNeu-TTS · model: https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo |
| 3 | Giọng đọc tiếng Anh | Kokoro-82M ONNX int8 (`pip install kokoro-onnx`, Apache-2.0) | https://huggingface.co/hexgrad/Kokoro-82M · ONNX: https://github.com/thewh1teagle/kokoro-onnx |
| 4a | Animation vẽ bảng | whiteboard-stream-animation / srt-whiteboard-animation (geeklee) | https://github.com/geeklee/whiteboard-stream-animation · https://github.com/geeklee/srt-whiteboard-animation |
| 4b | Ghép clip + giọng + tiêu đề + phụ đề + nhạc nền | Remotion (Node.js), dự phòng ffmpeg | https://www.remotion.dev |

### Ghi chú quan trọng về từng thành phần (đã kiểm tra)
- **Qwen3.6-35B-A3B** là model MoE: tổng 35B nhưng mỗi token chỉ kích hoạt ~3B. Bản GGUF `UD-Q4_K_M` (~23GB) không vừa 8GB VRAM; Ollama để phần lớn trên RAM. **Bắt buộc `use_mmap: true`**: không có thì Ollama cấp ~18GB pinned RAM và lỗi `failed to allocate CUDA_Host buffer` (commit limit của máy chỉ ~34GB). Tắt "thinking" (`think: false`), ép JSON bằng `format: <schema>`. Kịch bản dài chia từng phần ~600 tiếng.
- **FLUX.2 klein 4B** bản 4-bit NF4 + `enable_model_cpu_offload()`: đỉnh ~2.8GB VRAM và **nhanh hơn** để cả model trên GPU (7.3s so với 11.6s/ảnh — để cả model trên GPU sát trần 8GB làm Windows tràn sang RAM chung). Model distilled: 4 bước, `guidance_scale=1.0`. Tránh prompt có "diagram", "label", tên khái niệm (vd. "aquifer") — model hay viết chữ vô nghĩa vào ảnh.
- **VieNeu-TTS v3 Turbo**: phải ép `Vieneu(backend="onnx", device="cpu")` — để "auto" thì thấy torch+CUDA sẽ chạy GPU. 25 giọng mẫu, 48kHz, RTF ~0.3 trên CPU (int8). Đọc được tiếng Anh nhưng tiếng Anh dùng Kokoro.
- **Kokoro-82M** (ONNX int8, ~120MB tải bằng `tools/download_models.py`): 28 giọng Mỹ/Anh (`am_*`, `af_*`, `bm_*`, `bf_*`), 24kHz, RTF ~0.3 trên CPU, nạp ~1s. Phiên âm qua espeak-ng (GPL-3): dùng nội bộ không sao, chỉ lưu ý khi đóng gói bán phần mềm.
- **Vẽ bảng — hai engine** (`whiteboard.engine` trong config): `stream` (hướng 1, mặc định: vẽ cả ảnh một lượt, giữ hình 3s cuối) và `regions` (hướng 2: tự sinh `annotation.json` — chia ảnh thành dải ở chỗ ít nét nhất, mỗi dải vẽ đúng lúc câu tương ứng được đọc; chậm hơn ~3 lần). Log của repo là tiếng Trung → subprocess phải đặt `PYTHONIOENCODING=utf-8`.
- **Độ dài cảnh**: 8–20 giây/cảnh (`scenes.min_sec / max_sec`). Tốc độ đọc theo ngôn ngữ (`languages.<mã>.units_per_sec`).
- **Remotion** ghép clip + audio + tiêu đề viết tay + chuyển cảnh mờ dần + phụ đề (thường / song ngữ) + nhạc nền (tự giảm khi có giọng). Python tính mọi mốc thời gian rồi truyền qua props → `.srt` và video luôn khớp. Chưa cài Remotion thì tự dùng ffmpeg.
- **Hugging Face offline**: `hf_offline: auto` — model đã có trong cache thì không hỏi mạng (nạp nhanh, chạy khi mất mạng). Phải đặt `HF_HUB_OFFLINE` TRƯỚC khi import `huggingface_hub`.

## Quy tắc bắt buộc về VRAM
- **Không bao giờ giữ nhiều model trong VRAM cùng lúc.**
- Mỗi bước: load model → xử lý tất cả cảnh cần xử lý → unload → sang bước sau. Mỗi job chạy trong **tiến trình con** → xong job là trả sạch VRAM (kể cả CUDA context); crash cũng không làm sập server.
- Với Ollama: gỡ model (`keep_alive: 0`) ngay sau khi chia cảnh / dịch xong, kể cả khi lỗi.
- Hàng đợi **2 làn**: làn GPU (chia cảnh, dịch, ảnh) và làn CPU (giọng đọc, xuất video) chạy song song; **trong mỗi làn chỉ một tác vụ tại một thời điểm**. Người dùng bấm nhiều nút → xếp hàng.

## Luồng 4 bước (giao diện)
Thanh bước ở đầu trang, mỗi bước hiện trạng thái:

1. **Kịch bản** — nhập lời thoại → "Chia cảnh" → LLM trả JSON → danh sách cảnh để **sửa lời đọc, sửa mô tả ảnh, thêm/xóa/đổi thứ tự cảnh** → Lưu. Mỗi cảnh có ô **bản dịch** cho từng ngôn ngữ dịch: "Dịch tất cả", "Dịch lại" từng cảnh, sửa tay, "Giữ bản dịch" khi lời gốc đổi. Trạng thái: "N cảnh · dịch x/N".
2. **Storyboard** — tạo ảnh cho từng cảnh (dùng chung mọi ngôn ngữ). Lưới thẻ cảnh, mỗi thẻ: ảnh, ô sửa prompt, nút "Tạo lại". Trạng thái: "x/N ảnh".
3. **Giọng đọc** — tab theo ngôn ngữ; mỗi ngôn ngữ một giọng. Mỗi cảnh: trình phát audio, thời lượng, nút "Tạo lại". Trạng thái: "vi x/N · en y/N".
4. **Xuất video** — chọn xuất **vi, en hoặc cả hai**; phụ đề thường / **song ngữ**; màn hình tiêu đề; nhạc nền. Bảng tổng hợp, cảnh nào thiếu/lỗi (theo ngôn ngữ). Mỗi ngôn ngữ một video + `.srt` (và `.srt` song ngữ). Có thanh tiến độ.

Trạng thái "x/N" bấm vào được để nhảy tới cảnh còn thiếu hoặc lỗi (đúng tab ngôn ngữ).

## Theo dõi phụ thuộc (theo ngôn ngữ)
Trạng thái: `missing | ready | stale | error`. Ảnh dùng chung; bản dịch / audio / clip / video riêng từng ngôn ngữ.
- Sửa **lời đọc gốc** → audio + clip ngôn ngữ gốc `stale`; **bản dịch + audio + clip mọi ngôn ngữ dịch `stale`**.
- Sửa **bản dịch** (vd. en) → chỉ audio + clip của ngôn ngữ đó `stale`; bản dịch được đánh dấu "đã sửa tay" ("Dịch tất cả" không ghi đè).
- Sửa **prompt ảnh** → ảnh `stale` + clip **mọi** ngôn ngữ `stale`.
- Đổi **giọng** một ngôn ngữ → audio ngôn ngữ đó `stale`.
- Bất kỳ thay đổi nào → video đã xuất của ngôn ngữ liên quan thành "cũ".
- Bước 4 không cho xuất ngôn ngữ nào còn cảnh thiếu ảnh / bản dịch / audio (hiện rõ cảnh nào, thiếu gì).
- Chỉ tạo lại những gì `stale`/`missing`, không chạy lại cả video.

## Lưu trữ theo dự án (định dạng v2)
```
outputs/
└── <project_id>/
    ├── project.json        # tên, kịch bản gốc, source_lang, languages, titles, settings (voices…), finals
    ├── scenes.json         # danh sách cảnh + trạng thái
    ├── scenes/
    │   └── 01/
    │       ├── image.png           # dùng chung
    │       ├── vi/{audio.wav, clip.mp4}
    │       └── en/{audio.wav, clip.mp4}
    ├── music/background.*  # nhạc nền (tuỳ chọn)
    ├── final.vi.mp4 · final.vi.srt · final.vi-en.srt (song ngữ)
    └── final.en.mp4 · final.en.srt
```
Tắt app mở lại vẫn làm tiếp được. Có trang danh sách dự án. Dự án định dạng cũ (v1) được tự chuyển khi khởi động (`tools/migrate_multilang.py`, có sao lưu `*.v1.bak`).

`scenes.json` — mỗi cảnh:
```json
{
  "id": "01",
  "order": 1,
  "image_prompt": "Mô tả ảnh (tiếng Anh, phong cách nét vẽ đen trắng trên nền trắng)",
  "image_status": "missing",
  "image_error": null,
  "langs": {
    "vi": {"narration": "Lời đọc gốc", "text_status": "ready", "source_hash": null, "edited": false,
           "duration_sec": null, "audio_status": "missing", "clip_status": "missing", "error": null},
    "en": {"narration": "Bản dịch", "text_status": "missing", "source_hash": "<hash lời gốc lúc dịch>", "edited": false,
           "duration_sec": null, "audio_status": "missing", "clip_status": "missing", "error": null}
  }
}
```
Ghi file JSON an toàn: ghi ra file tạm rồi đổi tên, tránh hỏng file khi đang ghi thì app tắt.

## Ngôn ngữ (config.yaml → `languages`)
Mỗi ngôn ngữ: `name`, `llm_name` (tên trong prompt), `tts` (engine), `voice`, `units_per_sec`, `subtitle_max_words`.
**Thêm ngôn ngữ mới** = thêm một mục ở đây + một engine TTS đọc được nó (`pipeline/tts.py` → `ENGINES`).

## Prompt chia cảnh (bước 1)
- Yêu cầu LLM trả về **JSON thuần**, đúng schema (chỉ `narration`, `image_prompt`), giữ nguyên văn lời đọc.
- Độ dài mỗi cảnh lấy từ `config.yaml`.
- `image_prompt` viết bằng tiếng Anh, phong cách đơn giản, nét vẽ rõ, nền trắng — hợp với animation vẽ bảng.
- Kiểm tra JSON trả về; nếu sai định dạng thì tự thử lại tối đa 2 lần, sau đó báo lỗi rõ ràng.

## Shorts từ video dài
Từ một dự án dài (bước 4, khung "✂ Shorts"): LLM gợi ý 3–5 đoạn đáng làm Short → người dùng sửa tiêu đề / đoạn cảnh → "Tạo Short".
- **Gợi ý** (`pipeline/shorts.py → suggest`, job làn GPU `suggest_shorts`): Qwen3.6 qua Ollama, ép JSON (`scene_ids`, `titles` theo mọi ngôn ngữ, `reason`), gỡ model sau khi xong. Kiểm tra: id có thật, **các cảnh liền nhau**, ≤ `shorts.max_scenes`, tổng độ dài trong `shorts.min_sec–max_sec` (±10%, tính theo audio ngôn ngữ gốc); sai thì thử lại kèm lỗi. Lưu vào `project.json → short_suggestions`.
- **Short = dự án con** (`kind: "short"`, 9:16, 720x1280). Ảnh / audio / clip **dùng lại** bằng hard link (không được thì copy) → không tạo lại gì. Mỗi cảnh con giữ `parent_scene` + `parent_sig` (prompt + mtime ảnh, hash lời + mtime audio/clip theo ngôn ngữ) để phát hiện dự án gốc đã đổi → nút "Cập nhật từ dự án gốc" (ghi đè chỉnh sửa trong Short ở cảnh đó).
- **Khung hình**: tiêu đề viết tay phía trên, clip 16:9 của video dài đặt giữa dạng "tấm thẻ" (lề, bo góc, bóng — che việc màu giấy lệch nhẹ sau khi mã hoá), phụ đề lớn bên dưới, cuối là **màn hình kết**: câu dẫn về video đầy đủ (có giọng đọc, `shorts.cta.<lang>`, sửa được) + tên video gốc gạch chân.
- **Bắt buộc Remotion** (composition `ShortVideo`); không có fallback ffmpeg.
- Dự án gốc: `shorts: [id…]`. Short: `parent: {id, name, titles}`, `titles`, `outro: {lang: {text, audio_status, duration_sec}}` (audio ở `outro/<lang>/audio.wav`). Xoá Short → gỡ khỏi danh sách của gốc. Short không chia cảnh lại, không tạo Short từ Short.
- API: `POST /api/projects/{id}/shorts/suggest` · `POST /api/projects/{id}/shorts` (`scene_ids`, `titles?`, `cta?`) · `POST /api/projects/{id}/sync-parent` · `PATCH /api/projects/{id}` nhận thêm `cta`.

## Prompt dịch (bước 1b)
- Tự nhiên như văn nói, câu ngắn; giữ thuật ngữ kỹ thuật, tên riêng, đơn vị; **độ dài tương đương bản gốc**; **giữ số câu 1–1** (cần cho phụ đề song ngữ).
- Nhiều cảnh mỗi lần gọi (JSON có id) để thuật ngữ thống nhất; kiểm tra đủ id, không rỗng, tỉ lệ độ dài; sai thì thử lại.

## Kiến trúc kỹ thuật
- **Backend:** Python + FastAPI. Mỗi bước là một module riêng trong `pipeline/`; logic bước nằm ở `pipeline/runner.py` (dùng chung cho dòng lệnh và API).
- **Frontend:** HTML/CSS/JS thuần (ES modules, không build), phục vụ bởi FastAPI. Giao diện tiếng Việt.
- **Tiến độ:** tác vụ chạy nền (tiến trình con); frontend lấy tiến độ qua polling.
- **Remotion:** project riêng trong `remotion/`. Python gọi lệnh render của Remotion, truyền mốc thời gian + clip + audio qua file JSON props.
- **Cấu hình:** `config.yaml` (model, ngôn ngữ, engine TTS, độ phân giải, tỉ lệ khung hình, phụ đề, nhạc nền…).
- Mỗi model đặt sau một lớp wrapper đơn giản để sau này dễ thay model khác.

```
video-app/
├── CLAUDE.md · README.md · config.yaml · requirements.txt · start.bat · run_pipeline.py
├── app/
│   ├── main.py          # FastAPI
│   ├── jobs.py          # hàng đợi 2 làn, job chạy trong tiến trình con
│   ├── storage.py       # project.json, scenes.json, phụ thuộc, chuyển dữ liệu v1→v2
│   ├── languages.py     # danh mục ngôn ngữ
│   └── static/          # giao diện
├── pipeline/
│   ├── runner.py        # logic các bước
│   ├── scenes.py        # bước 1 — chia cảnh
│   ├── translate.py     # bước 1b — dịch
│   ├── images.py        # bước 2
│   ├── tts.py           # bước 3 — engine VieNeu / Kokoro
│   ├── whiteboard.py · regions.py   # bước 4a
│   ├── subtitles.py     # phụ đề thường / song ngữ
│   ├── shorts.py        # gợi ý Short (LLM), tạo dự án con, đồng bộ với dự án gốc, câu kết
│   └── remotion_render.py · render.py   # bước 4b
├── remotion/            # composition React: WhiteboardVideo (video dài), ShortVideo (9:16)
├── tools/               # migrate_multilang.py, download_models.py, clean_pen_image.py
├── tests/               # test từng model, test_api.py, ui/ (jsdom)
├── models/              # Kokoro (không đưa lên Git)
└── outputs/
```

## Thứ tự làm (mỗi giai đoạn chạy được mới sang giai đoạn sau)
1. **Kiểm tra môi trường:** Python, PyTorch nhận GPU, Node.js, ffmpeg. Viết script `check_env.py`. ✅
2. **Chạy thử từng model riêng lẻ** bằng script nhỏ trong `tests/` (mỗi model một script, in thời gian chạy và VRAM dùng). ✅
3. **Pipeline dòng lệnh:** `python run_pipeline.py script.txt` → ra `final.<lang>.mp4`. ✅
4. **Backend FastAPI** với API cho từng bước + lưu trữ dự án. ✅
5. **Giao diện 4 bước.** ✅
6. **Hoàn thiện:** tạo lại từng cảnh, theo dõi phụ thuộc, danh sách dự án, xử lý lỗi, phụ đề, nhạc nền, Remotion. ✅
7. **Đa ngôn ngữ (vi, en):** dịch bằng LLM, TTS theo ngôn ngữ, phụ đề song ngữ, xuất theo ngôn ngữ. ✅
8. **Shorts từ video dài:** LLM gợi ý đoạn hay, dự án con 9:16 dùng lại ảnh/audio/clip, phụ đề lớn, câu kết. ✅

## Cách làm việc với mình
- Mình đang học để thoát khỏi "vibe coding". Sau mỗi giai đoạn, **tóm tắt ngắn gọn bằng tiếng Việt**: đã làm gì, file nào làm việc gì, khái niệm mới nào cần biết.
- Trước khi sửa nhiều file hoặc đổi kiến trúc, nói kế hoạch trước.
- Gợi ý commit Git sau mỗi phần chạy được.
- Gặp lỗi: đọc log, giải thích nguyên nhân, rồi mới sửa.
- Không tự ý tải model lớn hoặc cài gói nặng mà không báo trước dung lượng.
- Code có chú thích ở những chỗ quan trọng, tên biến tiếng Anh rõ nghĩa.
