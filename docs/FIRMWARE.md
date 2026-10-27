# FreeRTOS真实记录处理

QEMU目标运行真实FreeRTOS内核。数据来自项目内已下载的PhysioNet记录；固件中不含波形生成器。两种处理方式共用Python的加速度单位换算、1秒因果重力估计、动态运动量计算及32→64 Hz前值保持。Python同时负责校验文件、传输观测、保存结果和提供页面；QEMU中的Acquisition与DSP任务负责采集策略、PPG滤波、峰检测、质量判定和三个关键窗口。

## 数据与时间

- 输入为Empatica E4原始BVP（64Hz、有正负的a.u.）及32Hz加速度导出的动态运动量（g）。不将BVP解释为MAX30102的ADC值，不进行100Hz升采样。
- 加速度处理与片段的来源、许可证、SHA256、事件映射见 [DATASET.md](DATASET.md)。HR.csv仅作原设备输出对照，不进入DSP，也不是独立ECG金标准。
- 片段从运动负荷结束前10秒开始，恢复段固定124秒，共134秒、8576个脉搏波样本。t0来自已核对的原始阶段标记，在记录时间轴上固定为10秒，不能由网页按键移动。
- 播放倍率控制观测送入固件的节奏；窗口、滤波和结果时间仍使用原始样本时间。主机耗时和QEMU任务计数不代表STM32实机性能。

## 运行链路

```text
本地原始文件 → SHA256校验 → 带版本与CRC32的观测文件
                              ↓
                       QEMU Acquisition任务
                              ↓ 先应用采集策略
                         qRaw → C DSP
                              ↓
                固件滤波样本、窗口结果与任务诊断
                              ↓
                      Python归档 → 浏览器
```

采用QEMU官方支持的ARM semihosting文件通道传递批量观测。UART只传控制命令、确认和内核状态，避免把成块波形塞入逐字节控制串口。实际DSP输出由C写入，不由Python重算后代替。

每个实例仅使用项目 `data/transports/{id}/` 下两个受控的相对文件名：

- `observations.bin`：版本2、小端、64Hz、样本总数、t0、观察时长、策略、相位、周期宽度、播放倍率、头CRC32与负载CRC32。每条观测带序号、原始片段时间、BVP、运动量及缺口。
- `processed.bin`：版本头及固件实际处理后的样本、采集状态、区段号和滤波值。结束时检查序号、条数、输出CRC32和输入CRC32；不完整或损坏的传输不得宣布完成。

原始浮点观测在固件边界转换为IEEE754 float32；这种数值表示转换记录在输入清单中。Python从原始记录按序号关联设备参考脉率和来源时间，只用于归档与对照显示。

semihosting仅用于本项目可信固件，不接收网页传入的任意路径。运行结束或退出后删除临时通道文件，正式CSV、事件、结果和manifest另行归档。参考：[QEMU官方semihosting说明](https://www.qemu.org/docs/master/about/emulation.html#semihosting)、[Arm semihosting ABI](https://github.com/ARM-software/abi-aa/blob/main/semihosting/semihosting.rst)。

## 构建、运行与调试

在项目根目录、`repulse`环境中执行：

```sh
make -C firmware TARGET=qemu
make -C firmware TARGET=stm32
```

应用工作台可直接选择FreeRTOS处理。若要在终端运行一个独立的真实记录实例：

```sh
python -m scripts.run_qemu --dataset-id wearable-aerobic-s02 --speed 5
```

该命令准备受控输入、启动QEMU、自动开始处理并显示固件JSON诊断。按Ctrl+C安全退出。不能只运行没有观测文件的裸QEMU命令来获得演示数据。

调试时先启动：

```sh
python -m scripts.run_qemu --dataset-id wearable-aerobic-s02 --gdb
```

然后使用VS Code的QEMU GDB attach配置，或另一个终端：

```sh
arm-none-eabi-gdb firmware/build/qemu/repulse.elf -ex 'target remote localhost:3333'
```

调试实例在GDB连接并继续执行后，才会打开输入并启动内核；HTTP桥接不使用暂停启动参数，避免握手等待死锁。

## 任务、算法和能力

六个任务与优先级：Acquisition 6、DSP 5、Session 4、Feedback 3、UI 2、System 1；Idle由内核提供。所有任务和队列静态分配。qRaw含8个槽位、每块最多16点；UI、Session、Feedback各有独立长度1结果邮箱。事件走独立有界队列。

支持连续、关键窗口优先、等预算周期三种采集策略，以及0.5至10倍记录播放。周期策略按相位求解与关键窗方案相同的连续时间预算，实际64Hz采样量化差单独保留。策略关闭PPG发生在固件Acquisition向qRaw提交之前。

C算法使用原始64Hz、两个SOS节的0.5–5Hz因果带通、2秒重启预热、8秒（512样本）窗口与1秒估计步长。当前固定质量参数包括0.05a.u.最低波动、0.35g运动阈值、间期一致性和自相关检查。未实现的自定义固件算法参数会明确拒绝，配置快照记录实际参数。质量不足时给出缺测，不复制设备HR来填补。

任务心跳、状态、优先级、栈高水位、qRaw占用与丢块计数均来自真实内核。当前两目标的静态BSS约49KB；具体构建以size和map结果为准。

## 实际验证

```sh
python -m pytest tests/unit/test_firmware_protocol.py
python scripts/verify_qemu.py --dataset-id wearable-aerobic-s02 --speed 10
```

测试覆盖：S01/S02真实观测的三策略C/Python离线一致性、输入头和CRC、真实QEMU六任务、取消、固定t0、三个策略的完整134秒过程、损坏输入拒绝。有效值按0.05BPM容差比较；两实现可能报告不同数量的质量原因，但不得把无效窗口变成有效窗口。

完整验证脚本核对每个固件输出样本与所选真实输入的时间、序号、策略开关和float32值；确认8576个样本、124秒恢复段及零丢块后，才生成 [qemu-real-data.json](verification/qemu-real-data.json)。Python参考只在固件结果已经发布后离线计算。S02的起点波形有运动伪影，应保留缺测，不能为了演示完整数值改变t0或降低质量规则。

## 工具和硬件边界

本机工具为QEMU11.0.1、Arm GNU14.3.Rel1（GCC14.3.1）及FreeRTOS-Kernel V11.2.0。QEMU由Homebrew管理；Arm工具链位于Miniforge环境的 `opt/`，不在全局安装编译器。FreeRTOS官方提交和MIT许可保留于 `firmware/vendor/manifest.json` 及上游目录。

QEMU目标为MPS2 AN385 Cortex-M3，不是STM32外设模型。STM32L476RG仍使用独立启动代码、链接地址、Cortex-M4F移植及BSP，交叉编译通过；BSP明确返回传感器不可用。MAX30102、LIS2DW12、显示、按键、接线和佩戴验证尚未完成，DEVICE入口保持不可用。没有电池、电流或真实人体采集指标可由QEMU代替。
