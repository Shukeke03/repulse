# FreeRTOS、QEMU 与 STM32 工程

本项目含可实际运行的 FreeRTOS 内核。Python 模式在电脑端执行采样、状态和算法；QEMU 模式由 Cortex-M3 固件执行采样计划、状态转换、滤波、质量判断和三个关键窗口，Python 只传递命令、接收结果和保存记录。

## 已安装工具与来源

- Miniforge 环境：`/Users/shuxing/miniforge3/envs/repulse`。
- Arm GNU Toolchain 14.3.Rel1（GCC 14.3.1，20250623）：从 Arm 官方下载归档，解压到环境内 `opt/arm-gnu-toolchain-14.3.rel1-darwin-arm64-arm-none-eabi`，环境 `bin/` 中有编译器、GDB、size 等工具的符号链接。没有将编译器安装到全局目录。
- QEMU 11.0.1：经现有 Homebrew 安装到 `/opt/homebrew`，并由 Homebrew 管理 glib、pixman 等依赖。这是环境与 VS Code 插件以外额外安装的软件。
- FreeRTOS-Kernel V11.2.0：官方仓库提交 `0adc196d4bd52a2d91102b525b0aafc1e14a2386`，源码置于 `firmware/vendor/FreeRTOS-Kernel`，保留 MIT 许可证。清除了新克隆仓库的 Git 元数据，只保留两个使用的 GCC 架构移植端口。来源记录在 `firmware/vendor/manifest.json`。

官方资料：[QEMU MPS2 板卡文档](https://www.qemu.org/docs/master/system/arm/mps2.html)、[FreeRTOS 内核源码](https://github.com/FreeRTOS/FreeRTOS-Kernel/tree/V11.2.0)、[Arm GNU 工具链](https://developer.arm.com/Tools%20and%20Software/GNU%20Toolchain)。

## 构建与运行

在项目根目录执行：

```sh
conda activate repulse
make -C firmware TARGET=qemu
make -C firmware TARGET=stm32
make -C firmware TARGET=qemu run
```

应用中的 QEMU 会话会启动自己的独立 QEMU 子进程，通过标准输入输出上的 UART JSON 行协议连接，不与终端中手动启动的示范实例混用。退出服务、完成或取消记录后释放子进程。网页不能执行任意 shell 命令。

调试：

```sh
make -C firmware TARGET=qemu debug
arm-none-eabi-gdb firmware/build/qemu/repulse.elf -ex 'target remote :3333'
```

QEMU 目标为官方 `mps2-an385`、Cortex-M3；它不是 STM32 外设模拟器。STM32L476RG 使用独立 Cortex-M4F 编译参数、启动文件入口、链接地址和 BSP，交叉编译产物在 `firmware/build/stm32/repulse.elf`。

## 真实内核任务和通道

固件以静态内存创建 Acquisition、DSP、Session、Feedback、UI、System 六个任务。优先级为6至1，Idle 为内核任务。qRaw 有8个槽位，每块不超过16点；UI、Session、Feedback 分别有独立长度1邮箱；事件和遥测具有独立有界队列。没有用 Python 线程冒充 FreeRTOS 任务。QEMU的CMSDK UART接收缓冲为单字节，因此Session任务以1毫秒轮询命令传输，业务检查满足20毫秒期限；Idle使用WFI等待中断，避免无任务时忙等。

每条诊断快照报告真实任务心跳、`eTaskGetState`、`uxTaskGetStackHighWaterMark`、队列占用和丢块计数。System 任务初始栈1024 words，其余与设计规格一致；增加 System 栈是为有界 JSON 格式化及 UART 输出保留实测余量。诊断中的栈余量单位是words。QEMU 的这些计数不能当作 STM32 的运行时间、电流、栈余量或硬件测试结果。

数据样本包含固件采样时钟、序号、区段号、PPG、加速度、采集开关、缺口与固件滤波值。原始数据归档不从网页显示抽样恢复。

## 首版 QEMU 接入能力

- 正常恢复、起点不足场景；种子传入固件，使用与 Python 源相同的解析相位、无状态噪声公式和 ADC 单位。
- 关键窗口优先、连续采集策略；等预算周期策略请在 Python 模式使用。
- 固件权威时钟1倍速度；应用拒绝把 Python 的加速倍率伪装为内核运行。
- 首版固件使用固定算法/时间参数。若全局设置修改了这些默认参数，QEMU创建会明确拒绝，提示恢复默认或使用Python，不静默忽略。配置快照记录实际C运动质量阈值0.65，其余固定值为100Hz、0.5–5Hz、2秒预热、8秒窗口、2秒过期和1800秒准备上限；接触阈值暂不可配置。
- 支持 `start_prepare`、`mark_end`、`cancel` 命令，带command_id与expected_revision；开始确认后才发布状态，t0在固件中只写一次。
- 三个固定8秒窗口、两节因果IIR、区段重启2秒预热；支持缺测和独立变化量依赖。当前值在计划关闭、缺口和预热时失效。
- QEMU 现场故障注入与其余故障场景尚未开放；界面明确拒绝，使用 Python 信号实验室完成相应实验。

C DSP 与 Python 的原理及默认系数一致。自动对照验证关注窗口有效性和数值误差（2 BPM以内），并不把所有边界和原因组合声明为逐位等价。关键窗口保存的是脉率变化量，不提供临床风险判定。

## 验证

```sh
python -m pytest tests/unit/test_firmware_protocol.py -q
python scripts/verify_qemu.py
```

第一条包含5种完全相同观测输入的 Python 与本机编译 C DSP 对照，以及真实 QEMU 的内核握手、六任务、准备、标记、取消和归档测试，并检查未传入固件的自定义参数会被明确拒绝。第二条使用默认QEMU虚拟时钟运行完整124秒客体恢复过程（客体时间与墙钟耗时分开记录），使用收到的 QEMU 原始观测在独立 Python 处理器中离线计算，验证三个窗口并保存 `docs/verification/qemu-full-cycle.json`。离线参考不会替代实时固件结果。

测试输出和报告应以本机实际执行文件为准；未运行的环境或输入不记为通过。构建的 map 文件可用于核对静态 RAM，主线程栈与未使用空间另行留量。

## STM32 边界

STM32 工程完成了独立交叉编译，BSP 明确返回传感器不可用。预留 MAX30102（I2C1 PB6/PB7）、LIS2DW12（I2C2 PB10/PB11）、OLED（SPI1 PA5/PA7）、A/B按钮等接口；尚未接线、标定、编写并验证真实 I2C/FIFO 事务和显示驱动。没有硬件时 DEVICE 入口保持不可用，不生成电池电压、电流或人体数据。

后续上板前须完成时钟、电气、SWD、驱动时限与真实采样时间映射验证；这里的交叉编译通过不代表已完成实机功能验收。

本次完整QEMU验证通过：客体恢复124秒，墙钟耗时204.07秒，13270个时间点，三个窗口均有效，固件脉率150/94/78 BPM，独立Python参考150/93.75/77.92 BPM，原始与遥测丢块均为0。之后仅调整UART轮询周期以降低命令延迟，最新协议交互已通过自动测试。
