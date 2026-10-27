"""Commands for processing local public recordings; all source timestamps are immutable."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ACTIVE = {"CREATED", "PREPARING", "RECOVERING", "FINALIZING"}
TERMINAL = {"COMPLETED", "CANCELLED", "INTERRUPTED", "FAILED"}
DEFAULT_DATASET = "wearable-aerobic-s02"
DEFAULTS = {
    "theme": "light",
    "chart_seconds": 10,
    "show_filtered": False,
    "ui_hz": 10,
    "screen_timeout_s": 30,
    "sample_rate": 64,
    "sample_hz": 64,
    "band_low": 0.5,
    "band_high": 5.0,
    "warmup_seconds": 2.0,
    "window_seconds": 8.0,
    "stale_seconds": 2.0,
    "min_signal_std": 0.05,
    "motion_threshold": 0.35,
    "contact_threshold": None,
    "strategy": "continuous",
    "dataset_id": DEFAULT_DATASET,
}


class SessionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="有氧运动恢复记录", min_length=1, max_length=100)
    origin: Literal["PUBLIC_DATA"] = "PUBLIC_DATA"
    execution_mode: Literal["PYTHON", "FREERTOS_QEMU", "STM32_DEVICE"] = "PYTHON"
    dataset_id: str = Field(default=DEFAULT_DATASET, min_length=1, max_length=100)
    strategy: Literal["continuous", "critical", "periodic"] = "continuous"
    phase: int = Field(default=0, ge=0, le=19)
    speed: float = Field(default=1, ge=0.5, le=10)


class Command(BaseModel):
    type: Literal["start_prepare", "cancel"]
    expected_revision: int = Field(ge=0)


class Annotation(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    notes: str = Field(default="", max_length=10000)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("tags")
    @classmethod
    def tag_length(cls, value):
        if any(not tag.strip() or len(tag) > 40 for tag in value):
            raise ValueError("标签长度应为1至40个字符")
        return list(dict.fromkeys(tag.strip() for tag in value))


class ComparisonConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_ids: list[str] = Field(default_factory=lambda: [DEFAULT_DATASET], min_length=1, max_length=10)
    phases: list[int] = Field(default_factory=lambda: [0, 5, 10, 15], min_length=1, max_length=20)

    @field_validator("phases")
    @classmethod
    def phases_valid(cls, value):
        if len(set(value)) != len(value) or any(p < 0 or p > 19 for p in value):
            raise ValueError("相位必须是0至19之间不重复的整数")
        return value

    @field_validator("dataset_ids")
    @classmethod
    def datasets_valid(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("同一数据片段不能重复加入比较")
        return value
