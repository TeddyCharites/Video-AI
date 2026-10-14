# Video Studio Local — Hướng dẫn cho Claude Code

## Mục tiêu
Web app chạy local: người dùng nhập **kịch bản (lời thoại)** → app tạo ra **video hoàn chỉnh** dạng animation vẽ bảng (whiteboard), có giọng đọc tiếng Việt.
Dùng cá nhân, chạy hoàn toàn offline trên máy, không dùng API trả phí.

## Máy chạy
- Windows, CPU i5-12400F, GPU **RTX 5060 8GB VRAM**, RAM 32GB
- RTX 50-series: cần driver NVIDIA mới nhất và PyTorch bản có hỗ trợ CUDA cho dòng 50-series. Khi cài PyTorch, lấy lệnh cài từ trang chủ pytorch.org, chọn bản CUDA mới nhất.
- Đường dẫn file phải chạy đúng trên Windows (dùng `pathlib`, không hard-code dấu `/` hay `\`).

## Thành phần
| Bước | Việc | Công cụ | Repo / link |
|---|---|---|---|
| 1 | Chia kịch bản thành cảnh | Qwen3.6-35B-A3B (GGUF, chạy qua llama.cpp hoặc Ollama) | https://huggingface.co/Qwen/Qwen3.6-35B-A3B · GGUF: https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF |
| 2 | Tạo ảnh cho từng cảnh | FLUX.2 [klein] 4B (Diffusers, `Flux2KleinPipeline`) | https://huggingface.co/black-forest-labs/FLUX.2-klein-4B · bản 4-bit: https://huggingface.co/hlhc/FLUX.2-klein-4B-bnb-4bit |
| 3 | Tạo giọng đọc tiếng Việt | VieNeu-TTS v3 Turbo (`pip install vieneu`) | https://github.com/pnnbao97/VieNeu-TTS · model: https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo |
| 4a | Animation vẽ bảng | srt-whiteboard-animation (geeklee) | https://github.com/geeklee/srt-whiteboard-animation · bản đơn giản cho từng ảnh: https://github.com/geeklee/whiteboard-stream-animation |
| 4b | Ghép các cảnh thành video | Remotion (Node.js) | https://www.remotion.dev |

### Ghi chú quan trọng về từng thành phần (đã kiểm tra)
- **Qwen3.6-35B-A3B** là model MoE: tổng 35B nhưng mỗi token chỉ kích hoạt ~3B. Bản GGUF 4-bit (~20GB) không vừa 8GB VRAM, nhưng chạy được bằng cách để phần expert trên RAM (llama.cpp `--n-cpu-moe` / offload) với 32GB RAM. Tốc độ chấp nhận được cho việc chia cảnh. Cho phép đổi sang model nhỏ hơn trong `config.yaml` nếu chậm. Tắt chế độ "thinking" khi chia cảnh để nhanh và dễ lấy JSON.
- **FLUX.2 klein 4B** bản đầy đủ cần ~13GB VRAM → **không vừa 8GB**. Dùng bản 4-bit NF4 (link trên) hoặc `enable_model_cpu_offload()`. Cần cài `diffusers` bản mới nhất từ GitHub (`pip install git+https://github.com/huggingface/diffusers.git`). Model distilled: ~4 bước, `guidance_scale=1.0`.
- **VieNeu-TTS v3 Turbo**: API `from vieneu import Vieneu; tts = Vieneu(); audio = tts.infer(text, voice="..."); tts.save(audio, path)`. Có 20 giọng mẫu (`tts.list_preset_voices()`), xuất 48kHz. **Ưu tiên chạy trên CPU (ONNX, `Vieneu(backend="onnx")`)** để không chiếm VRAM — tốc độ trên CPU vẫn nhanh hơn thời gian thực. Temperature ~0.8 cho kết quả ổn định nhất.
- **srt-whiteboard-animation** là một *skill pack* (có `SKILL.md`), quy trình gốc: đọc file SRT → chia cảnh **25–35 giây/cảnh** → tạo tranh nét vẽ → đánh dấu vùng vẽ (`annotation.json`) → xem trước → render MP4 (dùng PyAV, không cần ffmpeg hệ thống). Phần đánh dấu vùng vốn do agent hoặc người làm.
  → **Việc đầu tiên ở giai đoạn 2: đọc kỹ `SKILL.md` và các script của repo này**, rồi đề xuất cách tích hợp tự động. Hai hướng:
  1. Dùng `whiteboard-stream-animation` (cùng tác giả): vẽ một ảnh thành animation, hỗ trợ chạy hàng loạt — đơn giản, hợp với mỗi cảnh một ảnh.
  2. Dùng `srt-whiteboard-animation` đầy đủ: app tự tạo file SRT từ thời lượng audio thật của từng cảnh, và tự sinh `annotation.json` đơn giản (ví dụ cả ảnh là một vùng hoặc chia vùng theo lưới).
  Bắt đầu với hướng 1, chạy được rồi mới thử hướng 2.
- **Độ dài cảnh**: nếu dùng hướng 2, chia cảnh theo 25–35 giây như repo gốc; nếu dùng hướng 1, có thể 8–20 giây/cảnh. Để giá trị này trong `config.yaml`.
- **Remotion** chỉ dùng để ghép các clip + audio + phụ đề/tiêu đề. Nếu repo vẽ bảng đã ghép được video thì Remotion có thể để sau.

## Quy tắc bắt buộc về VRAM
- **Không bao giờ giữ nhiều model trong VRAM cùng lúc.**
- Mỗi bước: load model → xử lý tất cả cảnh cần xử lý → unload (`del model`, `gc.collect()`, `torch.cuda.empty_cache()`) → sang bước sau.
- Với Ollama: gọi với `keep_alive: 0` để giải phóng VRAM ngay sau khi chia cảnh xong. Với llama.cpp server: tắt tiến trình sau khi chia cảnh.
- TTS chạy trên CPU nên có thể chạy cùng lúc với bước tạo ảnh trên GPU nếu muốn tăng tốc (tùy chọn, làm sau).
- Chỉ chạy **một tác vụ GPU tại một thời điểm** (dùng hàng đợi/lock). Nếu người dùng bấm nhiều nút, xếp hàng chứ không chạy song song.

## Luồng 4 bước (giao diện)
Thanh bước ở đầu trang, mỗi bước hiện trạng thái:

1. **Kịch bản** — nhập lời thoại → bấm "Chia cảnh" → LLM trả JSON → hiện danh sách cảnh để người dùng **sửa lời đọc, sửa mô tả ảnh, thêm/xóa/đổi thứ tự cảnh** → Lưu. Trạng thái: "Đã lưu".
2. **Storyboard** — tạo ảnh cho từng cảnh. Lưới thẻ cảnh, mỗi thẻ: ảnh, ô sửa prompt, nút "Tạo lại". Trạng thái: "x/N ảnh".
3. **Giọng đọc** — tạo audio cho từng cảnh. Mỗi cảnh: trình phát audio, thời lượng, nút "Tạo lại". Trạng thái: "x/N audio".
4. **Xuất video** — bảng tổng hợp: thời lượng từng cảnh + tổng video, cảnh nào thiếu/lỗi. Bấm "Xuất" → tạo animation vẽ bảng từng cảnh → Remotion ghép → hiện video để xem và tải. Có thanh tiến độ.

Trạng thái "x/N" bấm vào được để nhảy tới cảnh còn thiếu hoặc lỗi.

## Theo dõi phụ thuộc
Mỗi cảnh lưu trạng thái riêng cho ảnh và audio: `missing | ready | stale | error`.
- Sửa **lời đọc** của cảnh → audio cảnh đó thành `stale`.
- Sửa **prompt ảnh** → ảnh cảnh đó thành `stale`.
- Ảnh hoặc audio đổi → clip animation của cảnh đó thành `stale`.
- Bước 4 không cho xuất nếu còn cảnh `missing`, `stale` hoặc `error` (hiện cảnh báo rõ cảnh nào).
- Chỉ tạo lại những gì `stale`/`missing`, không chạy lại cả video.

## Lưu trữ theo dự án
```
outputs/
└── <project_id>/
    ├── project.json        # tên, kịch bản gốc, cấu hình, ngày tạo
    ├── scenes.json         # danh sách cảnh + trạng thái
    └── scenes/
        └── 01/
            ├── image.png
            ├── audio.wav
            └── clip.mp4    # animation vẽ bảng
    └── final.mp4
```
Tắt app mở lại vẫn làm tiếp được. Có trang danh sách dự án.

`scenes.json` — mỗi cảnh:
```json
{
  "id": "01",
  "order": 1,
  "narration": "Lời đọc của cảnh",
  "image_prompt": "Mô tả ảnh (tiếng Anh, phong cách nét vẽ đen trắng trên nền trắng)",
  "duration_sec": null,
  "image_status": "missing",
  "audio_status": "missing",
  "clip_status": "missing",
  "error": null
}
```
Ghi file JSON an toàn: ghi ra file tạm rồi đổi tên, tránh hỏng file khi đang ghi thì app tắt.

## Prompt chia cảnh (bước 1)
- Yêu cầu LLM trả về **JSON thuần**, đúng schema ở trên (chỉ các trường `narration`, `image_prompt`).
- Độ dài mỗi cảnh lấy từ `config.yaml` (xem ghi chú ở phần Thành phần).
- `image_prompt` viết bằng tiếng Anh, phong cách đơn giản, nét vẽ rõ, nền trắng — hợp với animation vẽ bảng.
- Kiểm tra JSON trả về; nếu sai định dạng thì tự thử lại tối đa 2 lần, sau đó báo lỗi rõ ràng.

## Kiến trúc kỹ thuật
- **Backend:** Python + FastAPI. Mỗi bước là một module riêng trong `pipeline/`.
- **Frontend:** HTML/CSS/JS thuần (hoặc React nếu cần), phục vụ bởi FastAPI. Giao diện tiếng Việt.
- **Tiến độ:** tác vụ chạy nền; frontend lấy tiến độ qua polling hoặc Server-Sent Events.
- **Remotion:** project riêng trong `remotion/`. Python gọi lệnh render của Remotion, truyền danh sách clip + audio qua file JSON props.
- **Cấu hình:** `config.yaml` (đường dẫn model, model LLM, độ phân giải, tỉ lệ khung hình 16:9 hoặc 9:16, giọng đọc).
- Mỗi model đặt sau một lớp wrapper đơn giản (ví dụ `generate_image(prompt) -> path`) để sau này dễ thay model khác.

```
video-app/
├── CLAUDE.md
├── config.yaml
├── requirements.txt
├── app/
│   ├── main.py          # FastAPI
│   ├── jobs.py          # hàng đợi tác vụ GPU
│   ├── storage.py       # đọc/ghi project.json, scenes.json
│   └── static/          # giao diện
├── pipeline/
│   ├── scenes.py        # bước 1
│   ├── images.py        # bước 2
│   ├── tts.py           # bước 3
│   ├── whiteboard.py    # bước 4a
│   └── render.py        # bước 4b
├── remotion/
└── outputs/
```

## Thứ tự làm (mỗi giai đoạn chạy được mới sang giai đoạn sau)
1. **Kiểm tra môi trường:** Python, PyTorch nhận GPU, Node.js, ffmpeg. Viết script `check_env.py`.
2. **Chạy thử từng model riêng lẻ** bằng script nhỏ trong `tests/` (mỗi model một script, in thời gian chạy và VRAM dùng).
3. **Pipeline dòng lệnh:** `python run_pipeline.py script.txt` → ra `final.mp4`, chưa có giao diện.
4. **Backend FastAPI** với API cho từng bước + lưu trữ dự án.
5. **Giao diện 4 bước.**
6. **Hoàn thiện:** tạo lại từng cảnh, theo dõi phụ thuộc, danh sách dự án, xử lý lỗi.

## Cách làm việc với mình
- Mình đang học để thoát khỏi "vibe coding". Sau mỗi giai đoạn, **tóm tắt ngắn gọn bằng tiếng Việt**: đã làm gì, file nào làm việc gì, khái niệm mới nào cần biết.
- Trước khi sửa nhiều file hoặc đổi kiến trúc, nói kế hoạch trước.
- Gợi ý commit Git sau mỗi phần chạy được.
- Gặp lỗi: đọc log, giải thích nguyên nhân, rồi mới sửa.
- Không tự ý tải model lớn hoặc cài gói nặng mà không báo trước dung lượng.
- Code có chú thích ở những chỗ quan trọng, tên biến tiếng Anh rõ nghĩa.
