# Video Studio Local

Nhập **kịch bản tiếng Việt** → nhận về **video vẽ bảng (whiteboard animation)** hoàn chỉnh: có giọng đọc,
phụ đề, màn hình tiêu đề, chuyển cảnh và nhạc nền — bằng **tiếng Việt, tiếng Anh hoặc cả hai** (tự dịch, phụ đề song ngữ).
Chạy **hoàn toàn offline** trên một máy Windows có GPU NVIDIA 8GB — không dùng API trả phí.

![Bàn tay vẽ cảnh, có phụ đề tiếng Việt](docs/demo.gif)

| Màn hình tiêu đề | Storyboard do AI vẽ (8 cảnh) |
|---|---|
| ![Tiêu đề viết tay](docs/title.jpg) | ![Storyboard](docs/storyboard.jpg) |

## Tính năng

- **Chia cảnh bằng AI** — LLM chạy local đọc kịch bản, chia thành cảnh 8–20 giây, viết mô tả ảnh cho từng cảnh
  (lời đọc giữ nguyên văn). Kịch bản dài được chia nhiều phần để không vượt ngữ cảnh.
- **Vẽ ảnh** từng cảnh theo phong cách nét vẽ bảng (FLUX.2 [klein] 4B, bản 4-bit — chỉ ~2.8GB VRAM).
- **Đa ngôn ngữ**: tự dịch lời đọc sang tiếng Anh bằng chính LLM local (giữ thuật ngữ, độ dài tương đương, giữ số câu),
  sửa được bản dịch; mỗi ngôn ngữ một giọng đọc, một video, một file phụ đề; tuỳ chọn **phụ đề song ngữ**.
  Thêm ngôn ngữ mới chỉ cần khai báo trong `config.yaml`.
- **Giọng đọc** tự nhiên chạy trên CPU: tiếng Việt 25 giọng (VieNeu-TTS), tiếng Anh 28 giọng (Kokoro-82M).
- **Animation vẽ bảng**: bàn tay vẽ từng nét rồi tô màu; tuỳ chọn vẽ **theo vùng khớp từng câu đang đọc**.
- **Ghép video bằng Remotion**: tiêu đề viết tay, chuyển cảnh mờ dần, phụ đề, **nhạc nền tự nhỏ lại khi có giọng đọc**.
  Xuất kèm file phụ đề `.srt`.
- **Tạo Shorts từ video dài**: AI gợi ý 3–5 đoạn hay (vài cảnh liền nhau, 30–60 giây) → video dọc 9:16 dùng lại
  ảnh, giọng đọc và animation có sẵn (không tạo lại), thêm tiêu đề, **phụ đề lớn** và **câu kết dẫn về video đầy đủ**.
  Mỗi Short là một dự án con, biết khi dự án gốc thay đổi và cập nhật lại được.
- **Giao diện web 4 bước** (Kịch bản → Storyboard → Giọng đọc → Xuất video): sửa lời đọc / mô tả ảnh,
  thêm / xoá / đổi thứ tự cảnh, **tạo lại từng cảnh**, xem tiến độ.
- **Theo dõi phụ thuộc**: sửa lời đọc thì chỉ audio cảnh đó "cần tạo lại" (và bản dịch của nó), sửa bản dịch thì chỉ
  audio ngôn ngữ đó, sửa mô tả thì chỉ ảnh cảnh đó — không bao giờ phải chạy lại cả video.
- **An toàn VRAM**: mỗi tác vụ GPU chạy trong một tiến trình riêng, xong là trả sạch VRAM; tác vụ GPU (ảnh) và
  CPU (giọng đọc, xuất video) chạy song song; tắt app mở lại vẫn làm tiếp được.

## Cách hoạt động

| Bước | Việc | Model / công cụ | Chạy trên |
|---|---|---|---|
| 1 | Chia kịch bản thành cảnh + viết mô tả ảnh | [Qwen3.6-35B-A3B](https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF) qua Ollama | GPU + RAM |
| 1b | Dịch lời đọc (vi ↔ en) | cùng model Qwen3.6 | GPU + RAM |
| 2 | Vẽ ảnh từng cảnh (dùng chung mọi ngôn ngữ) | [FLUX.2 [klein] 4B NF4](https://huggingface.co/hlhc/FLUX.2-klein-4B-bnb-4bit) (diffusers) | GPU |
| 3 | Giọng đọc tiếng Việt | [VieNeu-TTS v3 Turbo](https://github.com/pnnbao97/VieNeu-TTS) (ONNX) | CPU |
| 3 | Giọng đọc tiếng Anh | [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) ([kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx), int8) | CPU |
| 4a | Animation vẽ bảng từng cảnh | [whiteboard-stream-animation](https://github.com/geeklee/whiteboard-stream-animation) / [srt-whiteboard-animation](https://github.com/geeklee/srt-whiteboard-animation) | CPU |
| 4b | Ghép tiêu đề, chuyển cảnh, giọng, phụ đề, nhạc | [Remotion](https://www.remotion.dev) (hoặc ffmpeg) | CPU |

Ví dụ thực tế: kịch bản 540 tiếng → video 2 phút 10 giây, 8 cảnh, mất khoảng **6 phút** trên i5-12400F + RTX 5060 8GB.

## Yêu cầu

- **Windows 10/11**, GPU NVIDIA **8GB VRAM** trở lên (đã thử trên RTX 5060), **32GB RAM** (LLM để phần lớn trên RAM)
- ~**35GB** ổ trống cho model (Qwen ~23GB, FLUX ~4.8GB, VieNeu ~0.7GB, Kokoro ~0.12GB, Remotion ~0.5GB)
- [Python 3.12](https://www.python.org), [Node.js 20+](https://nodejs.org), [Git](https://git-scm.com),
  [ffmpeg](https://ffmpeg.org) trong PATH (bản có libass để chèn phụ đề), [Ollama](https://ollama.com), driver NVIDIA mới

## Cài đặt

> Nên đặt biến môi trường `HF_HOME` (cache model Hugging Face) và thư mục model của Ollama
> (Ollama → Settings → Model location) sang ổ còn nhiều chỗ trống trước khi cài.

```bat
git clone <url-repo-này> video-studio-local
cd video-studio-local

:: 1. Python + PyTorch (lấy lệnh đúng bản CUDA của bạn ở pytorch.org; RTX 50-series cần CUDA 12.8+)
python -m venv .venv
.venv\Scripts\pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python check_env.py

:: 2. Hai repo vẽ bảng (dùng chung một venv riêng)
git clone --depth 1 https://github.com/geeklee/whiteboard-stream-animation third_party\whiteboard-stream-animation
git clone --depth 1 https://github.com/geeklee/srt-whiteboard-animation  third_party\srt-whiteboard-animation
python third_party\whiteboard-stream-animation\scripts\prepare_env.py

:: 3. LLM chia cảnh + dịch (~23GB) và giọng đọc tiếng Anh Kokoro (~120MB, vào models\)
ollama pull hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M
.venv\Scripts\python tools\download_models.py

:: 4. Remotion (~510MB: node_modules + Chrome Headless Shell). Bỏ qua thì app tự ghép bằng ffmpeg.
cd remotion && npm install && npx remotion browser ensure && cd ..
```

**Lần chạy đầu** FLUX (~4.8GB) và VieNeu (~0.7GB) tự tải về `HF_HOME`. Từ lần sau app tự chuyển sang chạy
offline (`hf_offline: auto` trong `config.yaml`) nên nạp model nhanh hơn và không cần mạng.

## Sử dụng

Bấm đúp **`start.bat`** trong File Explorer → server chạy và trình duyệt mở http://127.0.0.1:8000.
(Ứng dụng Ollama phải đang chạy để dùng "Chia cảnh bằng AI".)

0. **Dự án mới** — chọn ngôn ngữ kịch bản và các ngôn ngữ muốn làm thêm (mặc định: tiếng Việt + English).
1. **Kịch bản** — dán lời thoại → "✨ Chia cảnh bằng AI" (1–2 phút) hoặc "Chia theo đoạn văn".
   Sửa lời đọc / mô tả ảnh, thêm / xoá / đổi thứ tự cảnh → **Lưu thay đổi** → "🌐 Dịch tất cả".
   Sửa bản dịch ngay cạnh lời gốc; "↻ Dịch lại" từng cảnh; "Giữ bản dịch" khi lời gốc đổi mà bản dịch vẫn đúng.
2. **Storyboard** — "Tạo các ảnh còn thiếu". Ảnh chưa ưng: sửa mô tả (tự lưu) → "↻ Tạo lại".
3. **Giọng đọc** — mỗi ngôn ngữ một tab, một giọng → "Tạo audio … còn thiếu". Bước 2 và 3 chạy song song được.
4. **Xuất video** — chọn ngôn ngữ (vi, en hoặc cả hai); bật/tắt tiêu đề, phụ đề, **phụ đề song ngữ**;
   chọn **nhạc nền** (mp3/wav/m4a…, âm lượng, tự giảm khi có giọng) → "Xuất" → xem, tải `.mp4` và `.srt` từng ngôn ngữ.

**Shorts** — ở bước 4 của một video dài, khung "✂ Shorts từ video này" → "✨ Gợi ý bằng AI" (khoảng 1–2 phút).
Mỗi gợi ý: sửa tiêu đề, chọn lại "Từ cảnh … đến cảnh …" (thời lượng cập nhật ngay) → **Tạo Short** → mở dự án con
ở bước 4: sửa tiêu đề và câu kết từng ngôn ngữ → "Xuất". Short nằm lồng dưới dự án gốc trong danh sách dự án.
Khi dự án gốc đổi (ảnh, lời đọc, giọng…), băng "✂ Short của «…»" báo cảnh nào đổi và có nút "Cập nhật từ dự án gốc".
Shorts cần Remotion.

Trạng thái mỗi phần: *Chưa có · Sẵn sàng · Cần tạo lại · Lỗi*. Bấm vào "x/N" trên thanh bước để nhảy tới cảnh còn thiếu.

**Dòng lệnh** (không cần giao diện):

```bat
.venv\Scripts\python run_pipeline.py kich_ban.txt                                 :: vi + en → outputs\<id>\final.vi.mp4, final.en.mp4
.venv\Scripts\python run_pipeline.py kich_ban.txt --langs vi                      :: chỉ tiếng Việt
.venv\Scripts\python run_pipeline.py kich_ban.txt --no-llm --placeholder-images --langs vi   :: thử nhanh, không cần GPU
.venv\Scripts\python run_pipeline.py --project-id <id>                            :: chạy tiếp dự án dở
```

## Cấu hình — `config.yaml`

| Mục | Ý nghĩa |
|---|---|
| `scenes.min_sec / max_sec` | Độ dài mong muốn mỗi cảnh (mặc định 8–20 giây) |
| `llm.model`, `llm.chunk_words` | Model Ollama chia cảnh (đổi sang model nhỏ hơn nếu chậm); kịch bản dài chia từng phần ~600 tiếng |
| `llm.use_mmap` | Giữ `true` trên Windows — không thì Ollama có thể lỗi `failed to allocate CUDA_Host buffer` |
| `images.cpu_offload` | `true`: ~2.8GB VRAM, và trên card 8GB còn *nhanh hơn* để cả model trên GPU |
| `languages.<mã>` | Từng ngôn ngữ: tên, engine TTS, giọng mặc định, tốc độ đọc, số chữ mỗi dòng phụ đề. **Thêm ngôn ngữ = thêm một mục** |
| `default_source_lang`, `default_target_langs` | Ngôn ngữ gốc / ngôn ngữ dịch sẵn cho dự án mới |
| `tts_engines.vieneu / kokoro` | Engine giọng đọc: `precision` (int8 nhanh / fp32), đường dẫn model Kokoro, tốc độ |
| `translate.*` | Nhiệt độ LLM khi dịch, số cảnh mỗi lần gọi, khoảng tỉ lệ độ dài hợp lệ |
| `whiteboard.engine` | `stream`: vẽ cả ảnh một lượt · `regions`: vẽ từng vùng khớp từng câu (chậm hơn ~3 lần) |
| `render.engine` | `remotion`: tiêu đề, chuyển cảnh, phụ đề, nhạc nền · `ffmpeg`: ghép thẳng, rất nhanh |
| `shorts.*` | Độ dài Short (`min_sec`/`max_sec`), số gợi ý, số cảnh tối đa, cỡ chữ tiêu đề/phụ đề, câu kết mặc định theo ngôn ngữ |
| `remotion.*`, `subtitles.*`, `music.*` | Font/thời lượng tiêu đề, chuyển cảnh; cỡ chữ phụ đề; âm lượng và ducking nhạc nền |
| `hf_offline` | `auto` (mặc định): offline khi model đã có trong cache, online để tải ở lần đầu · `true` / `false`: ép buộc |

## Kiến trúc

```
app/        main.py      FastAPI: API cho từng bước, phục vụ giao diện và file
            jobs.py      hàng đợi 2 làn (GPU / CPU), mỗi job chạy trong tiến trình con
            storage.py   dự án, trạng thái missing/ready/stale/error theo ngôn ngữ, ghi JSON an toàn, chuyển dữ liệu v1→v2
            languages.py danh mục ngôn ngữ (từ config.yaml)
            static/      giao diện HTML/CSS/JS thuần (không cần build)
pipeline/   runner.py    logic các bước, dùng chung cho dòng lệnh và backend
            scenes.py · translate.py · images.py · tts.py (VieNeu / Kokoro) · whiteboard.py · regions.py
            subtitles.py (thường / song ngữ) · shorts.py (gợi ý Short, dự án con, đồng bộ với gốc)
            remotion_render.py (tính mốc thời gian → props) · render.py (ffmpeg)
remotion/   src/WhiteboardVideo.jsx — tiêu đề, chuyển cảnh, phụ đề, nhạc nền · src/ShortVideo.jsx — Short dọc 9:16
tools/      migrate_multilang.py (dự án cũ → định dạng đa ngôn ngữ) · download_models.py · clean_pen_image.py
outputs/<id>/  project.json · scenes.json · scenes/<NN>/image.png · scenes/<NN>/<lang>/{audio.wav, clip.mp4}
               final.<lang>.mp4 · final.<lang>.srt · final.vi-en.srt (song ngữ)
               (Short: thêm outro/<lang>/audio.wav — giọng đọc câu kết)
```

Python tính mọi mốc thời gian (cảnh, chuyển cảnh, phụ đề, đoạn có giọng đọc) rồi truyền cho Remotion qua props,
nên video và file `.srt` luôn khớp nhau. Chỉnh giao diện video trực tiếp: `cd remotion && npm run studio`.

## Kiểm tra

```bat
.venv\Scripts\python check_env.py                 :: môi trường
.venv\Scripts\python tests\test_tts.py            :: từng model: test_tts / test_flux / test_llm / test_translate
.venv\Scripts\python tests\compare_tts_en.py      :: nghe so sánh VieNeu và Kokoro đọc tiếng Anh
.venv\Scripts\python tests\test_api.py            :: backend, hàng đợi, đa ngôn ngữ, nhạc nền, Shorts (không cần GPU/Ollama)
cd tests\ui && npm install && npm test            :: giao diện, chạy trong jsdom (không cần GPU)
```

## Sự cố thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| Bấm `start.bat` trong VS Code chỉ mở file | Bấm đúp trong File Explorer, hoặc chạy `.\start.bat` trong terminal |
| "Ollama chưa chạy" | Mở ứng dụng Ollama |
| Ollama `failed to allocate CUDA_Host buffer` | Giữ `llm.use_mmap: true` |
| Ảnh có chữ lạ | Sửa mô tả ảnh (bỏ tên khái niệm, "diagram", "label"…) → Tạo lại |
| Tác vụ "dừng bất thường" | Thường do hết RAM/VRAM — đóng bớt ứng dụng nặng rồi chạy lại phần lỗi |
| Lỗi tải model ở lần chạy đầu | Kiểm tra mạng; đặt `hf_offline: false` nếu đã đổi sang model khác chưa tải |
| "Chưa có model Kokoro" | Chạy `tools\download_models.py` |
| Dự án cũ (trước khi có đa ngôn ngữ) | Tự chuyển khi mở app (có sao lưu `*.v1.bak`); chạy tay: `tools\migrate_multilang.py --dry-run` |

## Giấy phép

Mã nguồn dự án: [MIT](LICENSE). Các model và thư viện bên thứ ba giữ giấy phép riêng của chúng (xem dưới).

## Ghi công

- Animation vẽ bảng: [geeklee/whiteboard-stream-animation](https://github.com/geeklee/whiteboard-stream-animation),
  [geeklee/srt-whiteboard-animation](https://github.com/geeklee/srt-whiteboard-animation) (MIT).
  `assets/drawing-hand-clean.png` sửa từ ảnh bàn tay của repo này — xem [assets/THIRD_PARTY_LICENSE.txt](assets/THIRD_PARTY_LICENSE.txt).
- Model: Qwen3.6 (Alibaba Qwen), FLUX.2 [klein] (Black Forest Labs), VieNeu-TTS (pnnbao97),
  MOSS Audio Tokenizer (OpenMOSS), Kokoro-82M (hexgrad, Apache-2.0). Mỗi model có giấy phép riêng — xem trang
  của model trước khi dùng cho mục đích thương mại. Kokoro phiên âm qua espeak-ng (GPL-3).
- [Remotion](https://www.remotion.dev) có [giấy phép riêng](https://www.remotion.dev/license):
  miễn phí cho cá nhân và công ty nhỏ, công ty lớn hơn cần mua license.
