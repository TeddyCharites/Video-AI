"""Kiểu dữ liệu của request (Pydantic): FastAPI tự kiểm tra và trả lỗi 422 nếu sai."""

from typing import Literal

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    script: str = Field(min_length=1, max_length=100_000)
    voice: str | None = None                          # None = giọng trong config.yaml
    aspect_ratio: Literal["16:9", "9:16"] | None = None
    subtitles: bool | None = None                     # None = theo config.yaml
    title: bool | None = None                         # màn hình tiêu đề (Remotion); None = theo config.yaml


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    script: str | None = Field(default=None, min_length=1, max_length=100_000)
    voice: str | None = None                          # đổi giọng → mọi audio thành stale
    subtitles: bool | None = None                     # bật/tắt phụ đề → video đã xuất thành stale
    title: bool | None = None                         # bật/tắt màn hình tiêu đề → video đã xuất thành stale
    music_volume: float | None = Field(default=None, ge=0, le=1)   # âm lượng nhạc nền (0–1)
    music_duck: bool | None = None                    # tự giảm nhạc khi có giọng đọc


class SceneIn(BaseModel):
    id: str | None = None                             # None = cảnh mới
    narration: str = Field(min_length=1)
    image_prompt: str = ""


class ScenesReplace(BaseModel):
    scenes: list[SceneIn] = Field(min_length=1)


class SceneUpdate(BaseModel):
    narration: str | None = Field(default=None, min_length=1)
    image_prompt: str | None = None


class SplitRequest(BaseModel):
    mode: Literal["llm", "paragraphs"] = "llm"
    force: bool = False                               # bắt buộc true nếu dự án đã có cảnh (sẽ mất ảnh/audio cũ)
