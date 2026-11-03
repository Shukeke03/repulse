# RePulse 复脉

RePulse 是一个面向运动恢复监护的可穿戴腕表课程项目。默认首页是一块可直接操作的手表屏幕：以 240×280 固定分辨率绘制，每帧按 RGB565 色深量化，支持手机触屏点按与滑动、鼠标、四个模拟实体按键和键盘操作，为后续嵌入式腕端移植提供明确的显示与交互边界。

系统读取公开数据集中的穿戴设备脉搏波和加速度历史记录，完成演示、恢复窗口计算、保存和回放。新建演示由 QEMU 中真实运行的 FreeRTOS 固件任务执行信号处理；腕端界面运行在浏览器，Python / FastAPI 提供数据读取、服务与归档。STM32 目标已可交叉编译，显示、触摸、物理按键和传感器驱动仍待上板适配与验证。

## 演示

下面的录屏展示了如何在手表上开始演示、查看恢复过程和回放记录。想亲自操作，可以按[安装与启动](#安装与启动)在本机运行。

https://github.com/user-attachments/assets/faf840e1-f300-498f-b563-bc6dfeb435d6

[下载腕端演示视频（78 秒）](docs/demo/repulse-demo.mp4)。全程围绕手表操作：触屏设置 → FreeRTOS/QEMU 处理与波形 → 固件任务 → 关键窗口 → 保存与回放 → 键盘返回表盘。演示使用 S02 记录和关键窗口优先策略，按 1 倍速度处理；视频剪去等待片段，原始采样时间和计算窗口不变。[录制说明与章节](docs/demo/README.md)。

## 面向真实腕表的移植路径

| 部分 | 已实现 | 上板时的工作 |
| --- | --- | --- |
| 显示 | 240×280 布局、RGB565 色深、页面状态和图表 | 将 Canvas 绘图移植到目标图形库，接入屏幕驱动和 16 位帧缓冲 |
| 输入 | 点按、滑动与四键操作共用页面状态和焦点逻辑 | 适配触摸坐标、物理按键、消抖与长按事件 |
| 计算 | C 信号处理、固定窗口与质量规则在 FreeRTOS/QEMU 中运行 | 接入实际采样源，验证时序、资源占用和功耗 |
| 板级适配 | QEMU 运行路径与可交叉编译的 STM32 目标 | 完成传感器、显示与输入驱动，并进行实机联调 |

本机运行后即可在浏览器中操作腕端界面。STM32 目标已通过构建，显示、触摸、按键与传感器尚未上板验证。页面与输入设计见 [腕端界面说明](docs/WRIST_UI.md)，固件结构及构建方法见 [固件说明](docs/FIRMWARE.md)。

## 功能

- 读取本地公开记录，展示脉搏波、运动变化和信号质量。
- 按原始事件标记定位恢复起点，计算起点、60 秒和 120 秒的窗口脉率。
- 保存、检索、回放和导出处理记录；回放支持暂停、定位和逐秒推进。
- 对同一片段比较连续读取、周期读取和关键窗口优先三种策略。
- 在 QEMU 中运行 FreeRTOS 固件，查看任务与处理结果。
- 在腕端首页用鼠标点击或手机触屏点按、滑动，操作演示、记录、回放、比较、数据、系统与设置；同时支持键盘和四个按键，恢复过程页同步显示同一腕端画面。

策略比较通过屏蔽离线记录中的部分样本模拟不同读取时段，不控制实际传感器；能耗指标由工程模型估算。连续读取默认记录 S02 时，起点窗口未通过质量检查，因此两项脉率变化量显示缺测；60 秒和 120 秒窗口可用。当前项目不连接实时传感器，也未进行临床准确度验证。

## 数据来源

数据来自 PhysioNet 的 [Wearable Device Dataset from Induced Stress and Structured Exercise Sessions v1.0.1](https://physionet.org/content/wearable-device-dataset/1.0.1/)。下载脚本默认获取三条有氧骑行记录，原始 CSV 合计约 6.1 MB，保存在项目的 `data/public/` 下。

- `BVP.csv`：64 Hz 脉搏波，保留设备相对单位和正负值。
- `ACC.csv`：32 Hz 三轴加速度；程序换算为 g 并计算动态运动量。
- `tags.csv`：原始阶段标记，用于确定恢复起点。该起点对应低阻力踩踏的主动恢复阶段。
- `HR.csv`：设备从同一 BVP 计算的脉率，仅作显示对照，不作为独立心电参考。

每次处理截取起点前 10 秒至起点后 124 秒，共 134 秒。数据许可、事件映射和文件校验信息见 [数据说明](docs/DATASET.md)。原始数据不提交到仓库。

## 安装与启动

需要 Git、[Miniforge](https://github.com/conda-forge/miniforge)、`make`、`qemu-system-arm` 和 Arm GNU 工具链（`arm-none-eabi-*`）。QEMU 与 Arm 工具链需另行安装并加入 `PATH`，参考 [QEMU 下载页](https://www.qemu.org/download/)和 [Arm GNU 工具链](https://developer.arm.com/Tools%20and%20Software/GNU%20Toolchain)。项目已在 macOS Apple Silicon 上验证，并已在 Legion 主机的 Ubuntu 24.04 / WSL2 环境部署和验证。应用使用 POSIX 文件锁，不支持原生 Windows。

```sh
git clone https://github.com/Shukeke03/repulse.git
cd repulse
conda env create -f environment.yml
conda activate repulse
python -m scripts.download_dataset
make -C firmware TARGET=qemu
python run.py
```

已有环境、数据和 QEMU 固件时，激活环境后在项目根目录执行 `python run.py`；修改固件代码后重新构建。服务默认绑定 `127.0.0.1:8765`，按 `Ctrl+C` 停止；可通过 `python run.py --port 8766` 修改端口。同一数据目录只允许一个服务进程使用。

| 地址 | 内容 |
| --- | --- |
| [http://127.0.0.1:8765](http://127.0.0.1:8765) | 默认首页：腕端显示预览 |
| [http://127.0.0.1:8765/#/workbench](http://127.0.0.1:8765/#/workbench) | 工作台 |
| [http://127.0.0.1:8765/#/wrist](http://127.0.0.1:8765/#/wrist) | 腕端首页兼容地址 |
| [http://127.0.0.1:8765/docs](http://127.0.0.1:8765/docs) | API 文档 |
| [http://127.0.0.1:8765/api/datasets](http://127.0.0.1:8765/api/datasets) | 已下载的数据记录 |

前端由同一个 FastAPI 服务提供，无需另启前端服务或执行 npm 构建。

### 腕端显示预览

默认首页直接显示腕端预览，工作台位于 `/#/workbench`。屏幕以 240×280 像素绘制，每帧按 RGB565 色深量化；四个模拟实体按键分别为返回、上、下、确认，手表旁同时标注键盘 `Esc`、`↑`、`↓`、`Enter` 的对应操作。确认键打开菜单或执行选中项，上下键移动焦点或切页；返回键退回上一级，长按约 0.8 秒回到表盘。在腕端的恢复过程页长按确认约 1.2 秒，会打开停止演示的确认页。屏幕保持常亮，无操作时不自动调暗。

屏幕内的菜单、选项和按钮均可直接点按；上下滑动或鼠标滚轮浏览长列表，点左上角箭头或右划返回。恢复过程与系统状态底部提供切页按钮，回放支持点按进度条定位。

腕端菜单有新建演示、恢复过程、记录库、记录回放、策略比较、数据与片段、系统状态和设置八个应用，与电脑端共用本机服务和处理记录。记录库与回放选择列表只加载最近 20 条公开数据记录，较早记录、备注、标签和导出请使用电脑端记录库。当前腕端界面仍运行在浏览器中，尚未接入固件显示驱动或物理按键。页面结构、操作与移植边界见 [腕端界面说明](docs/WRIST_UI.md)。

## 处理路径

工作台、腕端和会话 API 新建的演示均使用 **FreeRTOS / QEMU**。主机预先写入观测文件，QEMU 模拟的 Cortex-M3 中运行固件任务，执行采集策略、脉搏波处理和窗口计算；主机读取结果并归档。QEMU 或固件未就绪时无法开始演示。

Python 仍提供 FastAPI 服务、数据读取、加速度预处理、结果存储、历史回放和离线算法校验，因此需保留 Python 环境与依赖。策略比较也使用 Python 对离线片段重算。

主机完成加速度单位换算和动态运动量计算，32 Hz 结果以前值保持方式对齐到 64 Hz 脉搏波。QEMU 使用带版本和 CRC 的 semihosting 文件通道传递观测与处理结果；UART 用于控制和诊断。固件仿真结果不代表 STM32 实机性能。

### 运行 FreeRTOS 固件

需要已下载的记录、`make`、`qemu-system-arm` 和 Arm GNU 工具链（`arm-none-eabi-*`）。本机验证使用 Miniforge 中的工具链与 Homebrew 安装的 QEMU；其他环境可参考 [QEMU 下载页](https://www.qemu.org/download/)和 [Arm GNU 工具链](https://developer.arm.com/Tools%20and%20Software/GNU%20Toolchain)。

```sh
make -C firmware TARGET=qemu
python run.py
```

构建后，从腕端首页或工作台开始演示即可使用 **FreeRTOS / QEMU**。也可在终端单独运行：

```sh
python -m scripts.run_qemu --speed 10
python -m scripts.run_qemu --gdb
```

`make -C firmware TARGET=stm32` 可交叉编译 STM32 目标。传感器、显示和按键尚未上板验证，设备入口保持不可用。完整构建和调试说明见 [固件说明](docs/FIRMWARE.md)。

## 测试

先下载公开记录并构建 QEMU 固件，再从项目根目录运行：

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

WSL2 主机上的服务绑定到本机回环地址。安装和私有访问方式见[部署说明](docs/DEPLOYMENT.md)。

如需通过 SSH 访问远程主机上的本地服务，可执行 `ssh -L 8765:127.0.0.1:8765 user@server`，将 `user@server` 替换为实际用户名和主机地址，再访问本机 `127.0.0.1:8765`。
