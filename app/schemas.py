"""Kiểu dữ liệu của request (Pydantic): FastAPI tự kiểm tra và trả lỗi 422 nếu sai."""

from typing import Literal

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    script: str = Field(min_length=1, max_length=100_000)
    source_lang: str | None = None                    # ngôn ngữ của kịch bản; None = config default_source_lang
    languages: list[str] | None = None                # ngôn ngữ dịch thêm (vd. ["en"]); None = config default_target_langs
    voices: dict[str, str] | None = None              # giọng theo ngôn ngữ; thiếu = giọng mặc định trong config
    aspect_ratio: Literal["16:9", "9:16"] | None = None
    subtitles: bool | None = None                     # None = theo config.yaml
    subtitles_bilingual: bool = False                 # phụ đề 2 dòng (ngôn ngữ đang đọc + ngôn ngữ kia)
    title: bool | None = None                         # màn hình tiêu đề (Remotion); None = theo config.yaml


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    script: str | None = Field(default=None, min_length=1, max_length=100_000)
    languages: list[str] | None = None                # đặt lại danh sách ngôn ngữ dịch (ngôn ngữ gốc luôn giữ)
    voices: dict[str, str] | None = None              # đổi giọng một ngôn ngữ → audio ngôn ngữ đó thành stale
    titles: dict[str, str] | None = None              # sửa tiêu đề video theo ngôn ngữ
    subtitles: bool | None = None                     # bật/tắt phụ đề → video đã xuất thành stale
    subtitles_bilingual: bool | None = None
    title: bool | None = None                         # bật/tắt màn hình tiêu đề → video đã xuất thành stale
    music_volume: float | None = Field(default=None, ge=0, le=1)   # âm lượng nhạc nền (0–1)
    music_duck: bool | None = None                    # tự giảm nhạc khi có giọng đọc
    cta: dict[str, str] | None = None                 # Short: câu kết theo ngôn ngữ → đọc lại câu kết


class ShortCreate(BaseModel):
    scene_ids: list[str] = Field(min_length=1)        # các cảnh LIỀN NHAU của dự án gốc
    titles: dict[str, str] | None = None              # tiêu đề Short theo ngôn ngữ
    cta: dict[str, str] | None = None                 # câu kết theo ngôn ngữ (None = mẫu trong config)


class SceneIn(BaseModel):
    id: str | None = None                             # None = cảnh mới
    narration: str = Field(min_length=1)              # lời đọc NGÔN NGỮ GỐC
    image_prompt: str = ""
    translations: dict[str, str] | None = None        # bản dịch người dùng đã sửa: {"en": "..."}


class ScenesReplace(BaseModel):
    scenes: list[SceneIn] = Field(min_length=1)


class SceneUpdate(BaseModel):
    narration: str | None = Field(default=None, min_length=1)   # lời gốc
    image_prompt: str | None = None
    translations: dict[str, str] | None = None        # sửa bản dịch: {"en": "..."}


class SplitRequest(BaseModel):
    mode: Literal["llm", "paragraphs"] = "llm"
    force: bool = False                               # bắt buộc true nếu dự án đã có cảnh (sẽ mất ảnh/audio cũ)


class TranslateRequest(BaseModel):
    langs: list[str] | None = None                    # None = mọi ngôn ngữ dịch của dự án


class AudioRequest(BaseModel):
    lang: str | None = None                           # None = ngôn ngữ gốc


class ExportRequest(BaseModel):
    langs: list[str] | None = None                    # None = mọi ngôn ngữ của dự án
