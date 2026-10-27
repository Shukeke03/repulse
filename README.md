# RePulse 复脉

RePulse 是一个运动恢复监护课程项目。系统读取公开的穿戴设备历史记录，处理脉搏波和加速度数据，展示恢复过程，并计算固定时间窗口内的脉率。浏览器界面由 FastAPI 提供；项目同时支持主机端信号处理和基于 QEMU 的 FreeRTOS 固件仿真。

## 演示

https://github.com/user-attachments/assets/462c99d1-ae6f-4af0-a102-d0aa058504e8

[下载演示视频（约 42 秒）](docs/demo/repulse-demo.mp4)。视频使用 10 倍播放速度，原始采样时间和计算窗口不变。

## 功能

- 读取本地公开记录，展示脉搏波、运动变化和信号质量。
- 按原始事件标记定位恢复起点，计算起点、60 秒和 120 秒的窗口脉率。
- 保存、检索、回放和导出处理记录；回放支持暂停、定位和逐秒推进。
- 对同一片段比较连续读取、周期读取和关键窗口优先三种策略。
- 在 QEMU 中运行 FreeRTOS 固件，查看任务与处理结果。

策略比较通过屏蔽离线记录中的部分样本模拟不同读取时段，不控制实际传感器；能耗指标由工程模型估算。连续读取默认记录 S02 时，起点窗口未通过质量检查，因此两项脉率变化量显示缺测；60 秒和 120 秒窗口可用。当前项目不连接实时传感器，也未进行临床准确度验证。

## 数据来源

数据来自 PhysioNet 的 [Wearable Device Dataset from Induced Stress and Structured Exercise Sessions v1.0.1](https://physionet.org/content/wearable-device-dataset/1.0.1/)。下载脚本默认获取三条有氧骑行记录，原始 CSV 合计约 6.1 MB，保存在项目的 `data/public/` 下。

- `BVP.csv`：64 Hz 脉搏波，保留设备相对单位和正负值。
- `ACC.csv`：32 Hz 三轴加速度；程序换算为 g 并计算动态运动量。
- `tags.csv`：原始阶段标记，用于确定恢复起点。该起点对应低阻力踩踏的主动恢复阶段。
- `HR.csv`：设备从同一 BVP 计算的脉率，仅作显示对照，不作为独立心电参考。

每次处理截取起点前 10 秒至起点后 124 秒，共 134 秒。数据许可、事件映射和文件校验信息见 [数据说明](docs/DATASET.md)。原始数据不提交到仓库。

## 安装与启动

需要 Git 和 [Miniforge](https://github.com/conda-forge/miniforge)。项目已在 macOS Apple Silicon 上验证。应用使用 POSIX 文件锁，不支持原生 Windows；可尝试在 WSL2 中运行，但尚未验证。

```sh
git clone https://github.com/Shukeke03/repulse.git
cd repulse
conda env create -f environment.yml
conda activate repulse
python -m scripts.download_dataset
python run.py
```

已有环境和数据时，激活环境后在项目根目录执行 `python run.py`。服务默认绑定 `127.0.0.1:8765`，按 `Ctrl+C` 停止；可通过 `python run.py --port 8766` 修改端口。同一数据目录只允许一个服务进程使用。

| 地址 | 内容 |
| --- | --- |
| [http://127.0.0.1:8765](http://127.0.0.1:8765) | Web 界面 |
| [http://127.0.0.1:8765/docs](http://127.0.0.1:8765/docs) | API 文档 |
| [http://127.0.0.1:8765/api/datasets](http://127.0.0.1:8765/api/datasets) | 已下载的数据记录 |

前端由同一个 FastAPI 服务提供，无需另启前端服务或执行 npm 构建。

## 处理路径

工作台的“处理环境”对应两条执行路径。FastAPI、数据读取和结果存储均在主机端运行。

| 工作台选项 | 信号处理位置 | 说明 |
| --- | --- | --- |
| Python · 本机处理 | 主机端 Python 进程 | `SessionEngine` 调用 `Processor`，完成脉搏波滤波、质量判定和窗口计算。 |
| FreeRTOS / QEMU | QEMU 模拟的 Cortex-M3 中 | 主机预先写入观测文件，固件任务执行采集策略、脉搏波处理和窗口计算；主机读取结果并归档。 |

两条路径共用主机端的加速度单位换算和动态运动量计算，32 Hz 结果以前值保持方式对齐到 64 Hz 脉搏波。QEMU 使用带版本和 CRC 的 semihosting 文件通道传递观测与处理结果；UART 用于控制和诊断。固件仿真结果不代表 STM32 实机性能。

### 运行 FreeRTOS 固件

需要已下载的记录、`make`、`qemu-system-arm` 和 Arm GNU 工具链（`arm-none-eabi-*`）。本机验证使用 Miniforge 中的工具链与 Homebrew 安装的 QEMU；其他环境可参考 [QEMU 下载页](https://www.qemu.org/download/)和 [Arm GNU 工具链](https://developer.arm.com/Tools%20and%20Software/GNU%20Toolchain)。

```sh
make -C firmware TARGET=qemu
python run.py
```

构建后，在工作台选择 **FreeRTOS / QEMU**。也可在终端单独运行：

```sh
python -m scripts.run_qemu --speed 10
python -m scripts.run_qemu --gdb
```

`make -C firmware TARGET=stm32` 可交叉编译 STM32 目标。传感器、显示和按键尚未上板验证，设备入口保持不可用。完整构建和调试说明见 [固件说明](docs/FIRMWARE.md)。

## 测试

先下载公开记录，再从项目根目录运行：

```sh
python -m pytest tests/unit tests/api
export PLAYWRIGHT_BROWSERS_PATH="$(python -c 'import sys; print(sys.prefix)')/share/playwright-browsers"
python -m playwright install chromium
python -m pytest tests/e2e/browser_test.py
ruff check backend tests scripts
```

浏览器测试要求本地数据可用。部分单元及接口测试在数据缺失时会跳过；QEMU 相关测试在固件或 QEMU 不可用时会跳过。测试使用独立运行目录，不会修改原始 CSV。

策略比较结果写入 `data/comparisons/cli-<dataset_id>/`；对同一记录再次运行会覆盖该目录中的 `results.json` 和 `metrics.csv`。QEMU 验证可指定输出路径，避免覆盖仓库中的历史报告：

```sh
python -m scripts.quick_compare
mkdir -p data/verification
python -m scripts.verify_qemu --output data/verification/qemu-real-data.json
```

测试范围与既有结果见 [验证记录](docs/VERIFICATION.md)。

## 开发与目录

VS Code 可打开 `repulse.code-workspace`，选择 `repulse` 环境的 Python 解释器。F5 启动调试；任务菜单提供数据下载、测试和固件构建。项目配置默认环境路径为 `~/miniforge3/envs/repulse`，安装位置不同时需调整 `.vscode/` 中的相应配置。

| 路径 | 内容 |
| --- | --- |
| `backend/` | API、数据源、信号处理、执行器与存储 |
| `web/` | HTML、CSS、JavaScript 和 Canvas 图表 |
| `firmware/` | FreeRTOS 固件、板级适配与内核源码 |
| `scripts/` | 数据下载、策略比较、导出和验证脚本 |
| `tests/` | 单元、接口和浏览器测试 |
| `docs/` | 数据来源、固件、文件结构和验证说明 |
| `data/` | 本地公开数据、处理记录、临时传输文件与导出文件 |

目录和文件的详细职责见 [项目结构](docs/PROJECT_STRUCTURE.md)。`REPULSE_DATA_DIR` 可指定运行记录目录，`REPULSE_DATASETS_DIR` 可指定公开数据根目录。备份运行记录时，应同时保留 SQLite 索引与记录文件。

如需通过 SSH 访问远程主机上的本地服务，可执行 `ssh -L 8765:127.0.0.1:8765 user@server`，将 `user@server` 替换为实际用户名和主机地址，再访问本机 `127.0.0.1:8765`。
