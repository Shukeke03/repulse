# RePulse 复脉

**运动恢复关键时间窗监护系统**：用可复现合成信号演示运动后的脉率恢复监护，结合流式信号处理、浏览器可视化和真实 FreeRTOS 仿真。适用于课程设计、算法实验与嵌入式软件演示。

准备至少6秒后标记运动结束，系统继续观察124秒，分析起点、60秒与120秒附近的固定8秒窗口。窗口不可靠时保留缺测与原因，分别计算可用的 ΔPR60、ΔPR120，不用其他时段填补关键窗口。

![RePulse 实时监护界面](docs/screenshots/02-monitor.png)

## 功能与架构

| 页面 | 功能 |
| --- | --- |
| 工作台 | 选择执行环境、模拟场景和采集策略，新建或继续记录 |
| 实时监护 | PPG与运动曲线、质量判定、关键窗口、腕端128×64模拟屏、冻结视图 |
| 记录库 | 搜索筛选、详情、备注标签、批量删除、JSON/CSV/原始ZIP导出 |
| 记录回放 | 独立时钟、播放速度、逐秒、定位重建、原结果与重算结果、PNG导出 |
| 策略比较 | 连续、等预算周期、关键窗优先；相同输入的后台配对实验 |
| 信号与故障 | 向活动Python模拟会话注入、停止或撤销故障，保留事件 |
| 系统状态 | 服务、存储、时钟与实际执行环境诊断 |
| 设置 | 显示偏好、默认策略和算法配置；活动会话配置保持冻结 |

- **前端**：原生 HTML/CSS/JavaScript、Canvas、Hash 路由。
- **后端**：Python 3.12、FastAPI、Uvicorn、Pydantic、WebSocket。
- **算法与数据**：NumPy/SciPy 因果滤波、质量规则、SQLite索引及CSV/JSONL归档。
- **固件**：FreeRTOS V11.2.0、QEMU Cortex-M3目标、STM32L476RG独立构建目标。

前端静态文件和后端API由**同一个 FastAPI 服务**提供，无需另外启动前端服务器或运行 npm 构建。浏览器关闭、切页或冻结图表不会自动取消后台采集。

## 本地部署：Miniforge（推荐）

准备 Git 和 [Miniforge](https://github.com/conda-forge/miniforge)。本项目已在 macOS Apple Silicon 验证；Python服务使用POSIX文件锁，Windows请使用WSL2，原生Windows尚未适配。

```sh
git clone https://github.com/Shukeke03/repulse.git
cd repulse
conda env create -f environment.yml
conda activate repulse
python run.py
```

私有仓库需要先使用有访问权限的GitHub账号完成Git认证。若环境已经创建，后续只需在项目根目录执行：

```sh
conda activate repulse
python run.py
```

启动后用浏览器打开：

| 地址 | 用途 |
| --- | --- |
| [http://127.0.0.1:8765](http://127.0.0.1:8765) | 系统可视化UI |
| [http://127.0.0.1:8765/docs](http://127.0.0.1:8765/docs) | 后端交互式API文档 |
| [http://127.0.0.1:8765/api/bootstrap](http://127.0.0.1:8765/api/bootstrap) | 服务能力、默认配置与活动会话 |

服务默认只监听 `127.0.0.1`。一次运行一个进程、一个worker；同一数据目录只允许一个服务。按 `Ctrl+C` 停止。端口占用时可先使用已有实例，或通过 `python run.py --port 8766` 更改端口，并为第二个实例指定不同的数据目录。

## 可选部署：Python虚拟环境

已有Python 3.12的macOS/Linux环境也可以使用venv。在克隆后的项目根目录执行：

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python run.py
```

上述命令按源码可编辑模式安装，请保留完整项目目录并在根目录运行。`requirements.lock.txt`记录已验证机器上的Python包版本，`conda-osx-arm64.lock.txt`仅对应本次macOS arm64环境；跨平台安装优先使用 `environment.yml` 或 `pyproject.toml`。

## 数据保存与远程服务器运行

默认数据目录为项目中的 `data/`，首次启动自动创建。可改为独立持久目录：

```sh
export REPULSE_DATA_DIR="$HOME/repulse-data"
python run.py
```

备份时先停止服务，再备份整个数据目录，保留SQLite索引、会话文件和manifest的一致性。运行数据、导出包、编译产物和本地环境不纳入Git提交。

在Linux服务器上完成上述环境安装后，保持 `python run.py` 在服务器终端中运行；在本地另开终端建立SSH转发：

```sh
ssh -L 8765:127.0.0.1:8765 user@server
```

将 `user@server` 换成实际SSH目标，然后在本地浏览器访问 `http://127.0.0.1:8765`。应用当前为单用户本地模式，没有账号认证；远程使用采用上述SSH转发。仓库没有提供Docker或systemd部署配置。

## 第一次使用

1. 工作台选择Python、正常恢复和关键窗口优先，点击“开始准备”。
2. 等待至少6秒，点击“标记运动结束”。提前标记仍保留真实起点，起点窗口会显示历史不足。
3. 等待124秒恢复监护完成；计划关闭采集时读数失效，曲线保留断线。冻结只冻结视图。
4. 在记录库查看三个窗口和两个变化量，回放记录或导出数据。
5. 在策略比较页运行快速实验：3个种子×4个相位×3种策略，共36次运行。

“起点不足”交互场景由用户按键决定真实起点；离线实验使用固定2秒准备。当前数据均为工程合成输入，窗口脉率变化量不输出临床风险等级。

## 可选：FreeRTOS/QEMU 与 STM32构建

Python模式不需要QEMU、交叉编译器或开发板。若要运行真实FreeRTOS，需要额外准备 `make`、`qemu-system-arm` 和 Arm GNU `arm-none-eabi` 工具链，并将命令加入当前终端的 `PATH`。

macOS可按 [QEMU官方安装说明](https://www.qemu.org/download/) 使用已有Homebrew：

```sh
brew install qemu
```

从 [Arm GNU Toolchain官方页面](https://developer.arm.com/Tools%20and%20Software/GNU%20Toolchain) 选择适合主机架构的 **arm-none-eabi** 归档并解压。可以将其放在Miniforge环境的 `opt/` 下，再将解压目录的 `bin/` 加入 `PATH`。本次验证使用 Arm GNU 14.3.Rel1、QEMU 11.0.1。确认可用后构建：

```sh
arm-none-eabi-gcc --version
qemu-system-arm --version
make -C firmware TARGET=qemu
make -C firmware TARGET=stm32
python run.py
```

构建QEMU后在工作台选择FreeRTOS QEMU。应用会为会话启动并管理自己的QEMU进程。若要在终端独立观察固件协议：

```sh
make -C firmware TARGET=qemu run
```

| 能力 | Python | FreeRTOS QEMU |
| --- | --- | --- |
| 状态与DSP所有者 | Python引擎 | 固件；Python仅转发与保存 |
| 场景 | 六种预设及九种故障 | 正常恢复、提前标记 |
| 策略 | 连续、等预算周期、关键窗优先 | 连续、关键窗优先 |
| 诊断 | 应用状态 | 真实六任务、优先级、栈高水位、队列与丢块 |

QEMU首版采用固定算法与时间参数；修改对应全局默认值后，创建QEMU会话会明确提示恢复默认或使用Python。固件配置快照记录实际参数。QEMU虚拟时间与主机墙钟耗时分开记录，不代表STM32实机时间或电流。

STM32L476RG目标已交叉编译，真实传感器、I2C/FIFO、显示和按键尚未上板验证，DEVICE入口保持不可用。固件接口、工具来源及已知边界见 [FIRMWARE.md](docs/FIRMWARE.md)。FreeRTOS源码与MIT许可证随仓库保留于 `firmware/vendor/`。

## 测试与VS Code

单元与接口测试：

```sh
python -m pytest tests/unit tests/api
```

浏览器端到端测试需先安装专用Chromium：

```sh
export PLAYWRIGHT_BROWSERS_PATH="$(python -c 'import sys; print(sys.prefix)')/share/playwright-browsers"
python -m playwright install chromium
python -m pytest tests/e2e/browser_test.py
```

其他验证命令：

```sh
python -m scripts.quick_compare
python scripts/verify_qemu.py
ruff check backend tests scripts
```

本次macOS完整环境执行了 **80项测试，全部通过**；另已完成真实QEMU的完整124秒恢复过程及同观测Python参考对照。新机器缺少QEMU或已构建ELF时，相关固件测试会明确跳过。浏览器测试使用临时数据目录，不修改日常记录。完整覆盖范围与待完善项见 [VERIFICATION.md](docs/VERIFICATION.md)，算法契约与估算能耗模型见 [ALGORITHM_CONTRACT.md](docs/ALGORITHM_CONTRACT.md)。

VS Code打开 `repulse.code-workspace`，安装推荐的Python、Pylance、Debugpy；固件开发另用C/C++与Cortex-Debug。选择 `repulse` 环境解释器后，可使用F5调试或“终端 → 运行任务”中的启动、测试、比较和固件构建任务。VS Code配置默认按 `~/miniforge3/envs/repulse` 查找环境；若Miniforge安装在其他位置，选择实际解释器，并相应调整 `.vscode/settings.json`、`tasks.json` 与 `launch.json` 中的工具链和浏览器路径。

## 工程目录

```text
backend/          API、实时/回放引擎、DSP、合成源、SQLite保存与比较进程
web/              原生前端、8个页面与Canvas图表
firmware/         共享任务/DSP、独立QEMU与STM32启动/BSP/链接脚本
scripts/          离线比较、导出与QEMU验收
scenarios/        场景说明
.vscode/          开发任务和调试配置
tests/            单元、接口、固件协议与浏览器测试
docs/             设计、契约、截图与验收证据
data/             本地持久化数据（Git忽略）
```

原始记录以CSV/JSONL落盘，终态生成带SHA256的manifest，再更新SQLite索引。服务退出或重启时未完成记录标为 `INTERRUPTED`；备注与标签独立保存，回放不会改写原始文件。详细设计要求见 [SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md)，未完成的设计项不计为已验收。
