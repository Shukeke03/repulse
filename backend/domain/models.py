"""Validated commands and immutable session configuration. Times use integer microseconds."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ACTIVE = {"CREATED", "PREPARING", "RECOVERING", "FINALIZING"}
TERMINAL = {"COMPLETED", "CANCELLED", "INTERRUPTED", "FAILED"}
DEFAULTS = {
    "theme": "light",
    "chart_seconds": 10,
    "show_filtered": True,
    "sample_rate": 100,
    "band_low": 0.5,
    "band_high": 5.0,
    "warmup_seconds": 2.0,
    "window_seconds": 8.0,
    "stale_seconds": 2.0,
    "prepare_timeout_seconds": 1800,
    "strategy": "critical",
    "seed": 20261025,
    "contact_threshold": None,
    "motion_threshold": 0.35,
}


class SessionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="运动恢复记录", min_length=1, max_length=100)
    origin: Literal["SIM"] = "SIM"
    execution_mode: Literal["PYTHON", "FREERTOS_QEMU", "STM32_DEVICE"] = "PYTHON"
    scenario: Literal["normal", "interference", "early", "detachment", "gap", "timeout"] = "normal"
    strategy: Literal["critical", "continuous", "periodic"] = "critical"
    seed: int = Field(default=20261025, ge=0, le=2**32 - 1)
    phase: float = Field(default=0, ge=0, lt=20)
    speed: float = Field(default=1, ge=0.5, le=30)


class Command(BaseModel):
    type: Literal["start_prepare", "mark_end", "cancel"]
    expected_revision: int = Field(ge=0)


class Annotation(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    notes: str = Field(default="", max_length=10000)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("tags")
    @classmethod
    def tag_length(cls, v):
        if any(not t.strip() or len(t) > 40 for t in v):
            raise ValueError("标签长度应为1至40个字符")
        return list(dict.fromkeys(t.strip() for t in v))


class FaultConfig(BaseModel):
    target_id: str
    type: Literal[
        "noise", "spike", "drift", "motion", "detachment", "gap", "saturation", "flatline", "timeout"
    ]
    anchor: Literal["now", "t0", "prepare"] = "now"
    start_offset_s: float = Field(default=0, ge=0, le=3600)
    duration_s: float = Field(default=5, ge=0.1, le=120)
    params: dict = Field(default_factory=dict)


class ComparisonConfig(BaseModel):
    seeds: int = Field(default=3, ge=1, le=100)
    seed_start: int = Field(default=20261025, ge=0, le=2**32 - 101)
    phases: list[int] = Field(default_factory=lambda: [0, 5, 10, 15], min_length=1, max_length=20)
    scenario: Literal["normal", "interference", "early", "detachment", "gap", "timeout"] = "normal"

    @field_validator("phases")
    @classmethod
    def phases_valid(cls, v):
        if len(set(v)) != len(v) or any(p < 0 or p > 19 for p in v):
            raise ValueError("相位必须是0至19之间不重复的整数")
        return v
