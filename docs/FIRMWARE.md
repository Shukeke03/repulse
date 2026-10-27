# FreeRTOS 固件

项目提供两个固件构建目标：`qemu` 对应 QEMU MPS2 AN385（Cortex-M3），用于运行 FreeRTOS 仿真；`stm32` 对应 STM32L476RG（Cortex-M4F），目前仅完成交叉编译。STM32 的传感器、显示和按键接口尚未接入。

QEMU 固件仿真与主机端处理使用同一份 PhysioNet 记录。主机负责读取和校验文件、加速度预处理、传输、归档及界面推送；固件负责采集策略、PPG 滤波、峰检测、质量判断和窗口计算。

## 输入与时钟

- 原始输入为 64 Hz BVP 和 32 Hz 加速度。主机将 ACC 换算为 g，通过 1 秒因果重力估计提取动态运动量，再以前值保持对齐到 64 Hz 时间轴。数据来源、许可和处理细节见 [DATASET.md](DATASET.md)。
- `HR.csv` 仅供设备输出对照，不进入 DSP，也不是独立心电参考。
- 片段包括主动恢复标记前 10 秒和标记后 124 秒，共 134 秒、8576 个 BVP 样本。恢复起点固定为片段内第 10 秒。
- QEMU 启动前，主机一次性写入完整观测文件。播放倍率控制固件按记录时间读取和推进的速度；采样时间、滤波参数和窗口边界保持不变。主机耗时不代表 STM32 实机性能。

## 数据通道

```text
原始 CSV → 主机校验与加速度预处理 → observations.bin
                                         ↓
                              QEMU Acquisition 任务
                                         ↓ 应用采集策略
                                   qRaw → C DSP
                                         ↓
                    固件窗口结果 / processed.bin / UART 诊断
                                         ↓
                                主机归档 → 浏览器
```

批量观测通过 ARM semihosting 文件通道传输；UART 用于控制命令、确认、窗口结果和内核诊断。每个实例在 `data/transports/{id}/` 使用固定的相对文件名：

- `observations.bin`：版本 2、小端格式；文件头包含采样率、样本数、起点、时长、策略、相位、周期宽度、播放倍率及头部和负载 CRC32。观测行包含序号、片段时间、BVP、动态运动量和缺口标志。
- `processed.bin`：版本头及固件输出的采样状态、区段号、原始值与滤波值。会话结束时核对序号、样本数和输入/输出 CRC32。

观测写入文件时转换为 IEEE 754 float32。主机按样本序号关联来源时间、XYZ 加速度和设备参考脉率，用于归档和展示；固件结果由 C 代码计算。运行结束后清理临时通道文件，会话 CSV、事件、结果和清单另行保存。

semihosting 通道仅使用程序指定的相对文件名。协议参考：[QEMU semihosting](https://www.qemu.org/docs/master/about/emulation.html#semihosting)、[Arm semihosting ABI](https://github.com/ARM-software/abi-aa/blob/main/semihosting/semihosting.rst)。

## 构建与运行

在项目根目录的 `repulse` 环境中构建：

```sh
make -C firmware TARGET=qemu
make -C firmware TARGET=stm32
```

工作台可选择 FreeRTOS/QEMU 执行环境。终端运行示例：

```sh
python -m scripts.run_qemu --dataset-id wearable-aerobic-s02 --speed 5
```

脚本准备观测文件、启动 QEMU，并在握手后开始处理；按 Ctrl+C 退出。

GDB 调试时先启动暂停的仿真实例：

```sh
python -m scripts.run_qemu --dataset-id wearable-aerobic-s02 --gdb
```

然后使用 VS Code 的 QEMU GDB attach 配置，或在另一个终端连接并继续执行：

```sh
arm-none-eabi-gdb firmware/build/qemu/repulse.elf -ex 'target remote localhost:3333' -ex 'continue'
```

## 任务与算法

| 任务 | 优先级 | 职责 |
| --- | ---: | --- |
| Acquisition | 6 | 读取观测并应用采集策略 |
| DSP | 5 | 信号滤波、质量检查、脉率估计和窗口计算 |
| Session | 4 | 命令处理、会话状态和结束控制 |
| Feedback | 3 | 读取结果邮箱；反馈驱动待实现 |
| UI | 2 | 读取结果邮箱；设备屏幕驱动待实现 |
| System | 1 | 输出握手、事件和状态快照 |

上述任务及队列使用静态分配，Idle 任务由内核提供。`qRaw` 有 8 个槽位，每块最多 16 个样本；UI、Session、Feedback 各有长度为 1 的结果邮箱，事件使用独立有界队列。

固件支持连续、关键窗口优先、周期三种读取策略及 0.5–10 倍播放；网页提供 1、2、5、10 倍选项。周期策略以 20 秒为周期，按相位求解与关键窗口策略相同的连续时间预算；64 Hz 采样产生的量化差单独记录。PPG 读取开关在 Acquisition 提交 `qRaw` 前应用。

C DSP 使用两个 SOS 节的 0.5–5 Hz 因果带通、2 秒预热、8 秒（512 样本）窗口和 1 秒估计步长。固定参数包括 0.05 a.u. 最低波动阈值和 0.35 g 运动阈值，以及间期和自相关一致性检查。未支持的固件算法参数会被拒绝；质量不足的窗口返回缺测及原因。

任务状态、优先级、栈高水位和队列占用由 FreeRTOS API 读取，心跳由应用任务递增。`raw_drops` 统计 `qRaw` 或 `qEvents` 的发送失败；`telemetry_drops` 当前固定为 0，不能用于判断遥测是否丢失。两目标在已记录构建中的静态 BSS 约为 49 KB；具体大小以构建输出和 map 文件为准。

## 验证

```sh
python -m pytest tests/unit/test_firmware_protocol.py
python scripts/verify_qemu.py --dataset-id wearable-aerobic-s02 --speed 10 --output data/verification/qemu-real-data.json
```

测试覆盖 S01/S02 在三种策略下的 C/Python 窗口结果对照、输入头及 CRC 校验、QEMU 任务、取消、固定恢复起点、三策略完整 134 秒流程和损坏输入拒绝。有效脉率使用 0.05 BPM 绝对容差；两种实现返回的质量原因数量可能不同。

完整验证脚本核对固件输出样本的时间、序号、策略开关和 float32 值，并在固件处理完成后用 Python 独立重算窗口结果。已有一次运行记录见 [qemu-real-data.json](verification/qemu-real-data.json)。S02 起点窗口因峰间期和自相关一致性未通过质量检查而缺测。

## 工具与硬件

已记录的验证环境为 QEMU 11.0.1、Arm GNU 14.3.Rel1（GCC 14.3.1）和 FreeRTOS-Kernel V11.2.0。本机 QEMU 由 Homebrew 管理，Arm 工具链位于 Miniforge 环境。FreeRTOS 上游版本与 MIT 许可见 `firmware/vendor/manifest.json` 和内核目录。

QEMU 目标不模拟 STM32 外设。STM32 目标使用独立启动代码、链接地址、Cortex-M4F 移植层和 BSP；当前 BSP 报告传感器不可用。MAX30102、LIS2DW12、显示、按键、接线、佩戴及实测电流均尚未验证。
