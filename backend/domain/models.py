# 定义会话、命令、备注和比较任务的参数格式及默认值。
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


# 校验新会话的数据源、执行方式、采集策略与播放速度。
class SessionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="有氧运动恢复记录", min_length=1, max_length=100)
    origin: Literal["PUBLIC_DATA"] = "PUBLIC_DATA"
    execution_mode: Literal["PYTHON", "FREERTOS_QEMU", "STM32_DEVICE"] = "PYTHON"
    dataset_id: str = Field(default=DEFAULT_DATASET, min_length=1, max_length=100)
    strategy: Literal["continuous", "critical", "periodic"] = "continuous"
    phase: int = Field(default=0, ge=0, le=19)
    speed: float = Field(default=1, ge=0.5, le=10)


# 限制会话控制命令，并用修订号防止过期操作。
class Command(BaseModel):
    type: Literal["start_prepare", "cancel"]
    expected_revision: int = Field(ge=0)


# 保存用户填写的记录名称、备注和标签。
class Annotation(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    notes: str = Field(default="", max_length=10000)
    tags: list[str] = Field(default_factory=list, max_length=20)

    # 检查标签长度，清理空白并去除重复项。
    @field_validator("tags")
    @classmethod
    def tag_length(cls, value):
        if any(not tag.strip() or len(tag) > 40 for tag in value):
            raise ValueError("标签长度应为1至40个字符")
        return list(dict.fromkeys(tag.strip() for tag in value))


# 定义需要成组比较的真实片段和周期相位。
class ComparisonConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_ids: list[str] = Field(default_factory=lambda: [DEFAULT_DATASET], min_length=1, max_length=10)
    phases: list[int] = Field(default_factory=lambda: [0, 5, 10, 15], min_length=1, max_length=20)

    # 保证比较相位不重复且位于0至19秒之间。
    @field_validator("phases")
    @classmethod
    def phases_valid(cls, value):
        if len(set(value)) != len(value) or any(p < 0 or p > 19 for p in value):
            raise ValueError("相位必须是0至19之间不重复的整数")
        return value

    # 避免同一真实片段被重复加入比较。
    @field_validator("dataset_ids")
    @classmethod
    def datasets_valid(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("同一数据片段不能重复加入比较")
        return value
