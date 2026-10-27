# 项目文件与目录说明

本文列出 RePulse 的目录与文件职责。固件目录包含在 QEMU 中运行的 FreeRTOS 实现，以及尚未完成实机验证的 STM32 目标。

## 根目录与开发配置

```text
repulse/
├── README.md                    项目介绍、数据来源、安装启动、部署与使用方法
├── run.py                       本地服务入口，解析端口并启动FastAPI
├── pyproject.toml               Python项目元数据、依赖声明及pytest/Ruff配置
├── environment.yml              创建Miniforge环境所需的依赖清单
├── requirements.lock.txt        已安装Python包的版本快照
├── conda-osx-arm64.lock.txt      macOS Apple Silicon的Conda精确环境快照
├── repulse.code-workspace       VS Code工作区入口
├── .gitignore                   忽略运行数据、构建产物、缓存和本地环境文件
├── .vscode/                     VS Code开发、运行和调试配置
│   ├── extensions.json          推荐安装的Python、C/C++和Cortex调试插件
│   ├── settings.json            Python解释器、C头文件、测试和终端环境配置
│   ├── launch.json              Python服务/测试调试、QEMU GDB附加配置
│   └── tasks.json               启动、测试、下载、导出和固件构建快捷任务
├── backend/                     数据读取、算法、会话、接口与持久化
├── web/                         浏览器前端，不需要npm构建
├── firmware/                    FreeRTOS固件、板级适配和第三方内核
├── scripts/                     可在终端运行的下载、比较、导出和验证工具
├── tests/                       单元、接口和浏览器自动化测试
├── docs/                        项目设计、数据依据、截图和验证记录
└── data/                        本机原始数据与运行产物，除.gitkeep外不提交Git
```

`environment.yml`用于常规安装；两个lock文件是特定环境的版本快照，其中Conda快照限定为`osx-arm64`，不能直接当作Linux或Windows的通用安装文件。

## 后端 backend/

每个`__init__.py`用于标记Python包，本身不启动服务。后端以`api/app.py`组装各模块。

```text
backend/
├── __init__.py                  后端应用包标记
├── api/                         HTTP与WebSocket接口
│   ├── __init__.py              接口包标记
│   └── app.py                   应用生命周期、路由、幂等写入、后台任务和静态前端
├── domain/                      输入参数与状态约定
│   ├── __init__.py              领域模型包标记
│   └── models.py                默认设置、会话配置、控制命令、备注与比较参数校验
├── sources/                     只读公开记录数据源
│   ├── __init__.py              数据源包标记
│   └── public_data.py           文件哈希校验、原始时序对齐、阶段定位和策略读取
├── algorithms/                  信号与采集策略算法
│   ├── __init__.py              算法包标记
│   ├── processor.py             流式PPG滤波、峰间期、质量判定和固定窗口脉率
│   └── policies.py              三种采集策略、周期预算和恢复变化量计算
├── engines/                     处理会话与独立回放
│   ├── __init__.py              执行器包标记
│   ├── session.py               Python会话状态、记录时钟、自动恢复起点与结果保存
│   ├── firmware_port.py         QEMU进程控制、带CRC的观测传输和固件结果归档
│   └── replay.py                历史记录播放、暂停、定位和按游标重新计算
├── storage/                     SQLite与本地文件存储
│   ├── __init__.py              存储包标记
│   └── store.py                 索引、设置、事务、幂等命令、CSV/JSONL及文件清单
└── workers/                     后台计算任务
    ├── __init__.py              后台任务包标记
    └── compare.py               同一真实输入的三策略配对比较和汇总指标
```

`public_data.py` 读取原始 64 Hz BVP，将 32 Hz 加速度换算为 g，计算动态运动量并以前值保持方式对齐。`HR.csv` 仅用于显示对照。`session.py` 调用 `Processor` 完成主机端信号处理；`firmware_port.py` 负责与 QEMU 固件通信并归档固件结果；`replay.py` 不改写原记录。

## 浏览器前端 web/

```text
web/
├── index.html                   页面入口、导航和应用挂载区域
├── js/                          浏览器交互与绘图
│   ├── api.js                   HTTP请求、幂等键、错误转换和WebSocket重连
│   ├── app.js                   8个页面、表单、弹窗、会话控制与页面状态
│   └── charts.js                Canvas脉搏波、运动量和腕端显示绘制
└── styles/
    └── app.css                  桌面与手机布局、控件、状态色和图表样式
```

8个页面是工作台、恢复过程、记录库、记录回放、策略比较、数据与片段、系统状态和设置。FastAPI直接提供这些静态文件，因此前后端共用一个启动命令和端口。

## 终端工具 scripts/

在项目根目录用`python -m scripts.模块名`运行。

| 文件 | 功能 |
| --- | --- |
| `download_dataset.py` | 下载指定版本的真实公开记录及许可、来源信息，并核对官方SHA256 |
| `quick_compare.py` | 对指定记录和相位执行三种采集策略，保存结果JSON和指标CSV |
| `export_session.py` | 调用正在运行的后端接口，将指定会话的完整ZIP归档下载到本地并显示路径 |
| `run_qemu.py` | 准备真实观测文件并独立启动QEMU，也支持等待GDB连接 |
| `verify_qemu.py` | 完整运行真实QEMU，将固件结果与Python独立重算对照并保存验证证据 |

## 固件 firmware/

```text
firmware/
├── Makefile                     选择qemu/stm32目标，编译、运行、调试或清理
├── common/                      两种目标共用的应用代码与接口
│   ├── FreeRTOSConfig.h         内核时钟、调度、静态内存和功能开关
│   ├── bsp.h                    板级接口：时钟、通信、观测输入和输出等
│   ├── main.c                   创建六个静态任务，管理采集、会话、反馈和诊断
│   ├── dsp.h                    DSP样本、窗口结果及处理函数声明
│   ├── dsp.c                    C实现的PPG滤波、峰检测、质量检查与脉率估计
│   ├── input.h                  真实观测文件协议、配置与读取接口声明
│   └── input.c                  二进制输入解析、CRC校验、样本读取和固件结果写出
├── qemu/                        QEMU Cortex-M3目标的板级实现
│   ├── startup.c                向量表、复位初始化与内核异常入口
│   ├── linker.ld                代码、数据、栈的存储布局
│   └── bsp.c                    仿真时钟、UART控制和未连接物理传感器的接口状态
├── stm32/                       STM32L476RG目标的板级实现
│   ├── startup.c                Cortex-M4复位初始化与中断向量表
│   ├── linker.ld                STM32 Flash/RAM段布局
│   └── bsp.c                    STM32基础时钟/通信适配；传感器尚未接入
├── vendor/                      保留上游许可的第三方内核
│   ├── UPSTREAM.md              上游仓库、版本、提交号和保留端口说明
│   ├── manifest.json            第三方组件的结构化版本与许可记录
│   └── FreeRTOS-Kernel/         官方FreeRTOS V11.2.0源码，详见下一节
└── build/                       本机编译后生成，不提交Git
    ├── qemu/
    │   ├── repulse.elf          可运行、调试的QEMU固件
    │   └── repulse.map          QEMU链接与内存占用映射
    └── stm32/
        ├── repulse.elf          STM32交叉编译产物，未代表实机已验证
        └── repulse.map          STM32链接与内存占用映射
```

在 QEMU 固件仿真路径中，加速度预处理由主机完成；PPG 滤波、峰检测、质量判定和窗口计算由固件任务完成。STM32 目标目前仅通过交叉编译，传感器和板级功能尚未验证。

### 第三方FreeRTOS文件

下列文件来自上游，保留其原有注释与许可证，不加入项目中文注释。当前Makefile直接使用`tasks.c`、`queue.c`、`list.c`及对应CPU端口；目录中的其他功能供内核扩展使用，并非都已启用。

```text
firmware/vendor/FreeRTOS-Kernel/
├── tasks.c                      任务创建、调度和任务状态管理
├── queue.c                      队列及信号量的底层实现
├── list.c                       调度器使用的链表操作
├── croutine.c                   协程功能实现
├── event_groups.c               事件组实现
├── stream_buffer.c              流缓冲与消息缓冲实现
├── timers.c                     软件定时器和守护任务实现
├── include/                     内核对应用和移植层提供的头文件
│   ├── FreeRTOS.h               内核公共配置、类型与宏
│   ├── task.h                   任务管理API
│   ├── queue.h                  队列API
│   ├── semphr.h                 信号量与互斥量API
│   ├── list.h                   链表类型和操作接口
│   ├── croutine.h               协程API
│   ├── event_groups.h           事件组API
│   ├── stream_buffer.h          流缓冲API
│   ├── message_buffer.h         消息缓冲API
│   ├── timers.h                 软件定时器API
│   ├── atomic.h                 原子操作辅助接口
│   ├── portable.h               编译器与CPU移植层接口
│   ├── projdefs.h               通用返回值、时间转换和工程定义
│   ├── StackMacros.h            旧命名的栈检查兼容头文件
│   ├── stack_macros.h           栈溢出检查宏
│   ├── deprecated_definitions.h 旧版本配置名称兼容定义
│   ├── mpu_prototypes.h         MPU包装函数声明
│   ├── mpu_syscall_numbers.h    MPU系统调用编号
│   ├── mpu_wrappers.h           MPU接口包装与重定向
│   ├── newlib-freertos.h        Newlib C运行库集成支持
│   ├── picolibc-freertos.h      Picolibc C运行库集成支持
│   ├── stdint.readme           标准整数类型使用说明
│   └── CMakeLists.txt           上游头文件的CMake构建定义
├── portable/GCC/                GCC工具链的CPU移植代码
│   ├── ARM_CM3/
│   │   ├── port.c              Cortex-M3上下文切换、时钟与调度入口
│   │   └── portmacro.h         Cortex-M3端口类型、临界区与汇编宏
│   └── ARM_CM4F/
│       ├── port.c              含浮点支持的Cortex-M4上下文切换和调度入口
│       └── portmacro.h         Cortex-M4F端口类型、临界区与汇编宏
├── README.md                    上游项目介绍
├── LICENSE.md                   MIT许可证
├── History.txt                  上游版本变更历史
├── MISRA.md                     上游MISRA规则与偏离说明
├── CMakeLists.txt               上游内核的CMake构建定义
├── manifest.yml                 上游组件清单
├── sbom.spdx                    软件物料与许可证清单
├── cspell.config.yaml           上游拼写检查规则
├── .git-blame-ignore-revs        Git历史中可忽略的大规模格式修改
├── .gitattributes               上游Git文本与差异处理属性
├── .gitmodules                  上游子模块配置记录
├── GitHub-FreeRTOS-Kernel-Home.url 上游GitHub入口快捷方式
└── Quick_Start_Guide.url         上游快速入门文档快捷方式
```

## 自动化测试 tests/

```text
tests/
├── unit/                        不依赖网页操作的模块与固件验证
│   ├── test_public_data.py      文件哈希、真实样本、原始时间和加速度对齐
│   ├── test_signal.py           滤波、信号质量、缺口、预热和分块一致性
│   ├── test_policies.py         三策略开启区间、周期预算和恢复变化量规则
│   ├── test_engine_contract.py  会话起点、窗口边界、归档与回放约定
│   └── test_firmware_protocol.py 二进制协议、CRC、C/Python对照及真实QEMU流程
├── api/
│   └── test_app.py              REST/WebSocket、幂等、存储、导出与比较接口
└── e2e/
    └── browser_test.py          Chromium中的页面、窄屏、完整流程与交互测试
```

测试会使用隔离的运行目录。公开原始数据必须预先下载；固件测试需要对应编译工具和QEMU。测试运行不会自动下载数据或改写原始CSV。

## 项目文档 docs/

```text
docs/
├── PROJECT_STRUCTURE.md         本文件：目录、文件职责和阅读顺序
├── SYSTEM_DESIGN.md             本机内部文档：系统结构、处理链路和模块职责
├── ALGORITHM_CONTRACT.md        本机内部文档：采样单位、固定窗口和质量规则
├── DATASET.md                   真实数据来源、许可、事件依据和处理方法
├── FIRMWARE.md                  FreeRTOS任务、通信协议、构建、运行与调试
├── VERIFICATION.md              已执行测试、结果证据及尚未验证的范围
├── dataset-protocol-aerobic-v1.png 作者协议图，用于核对原记录阶段事件
├── dataset-protocol-provenance.json 协议图的来源、Notebook与附件哈希
├── demo/
│   └── repulse-demo.mp4         真实系统主要功能的录屏演示，约42秒
├── screenshots/                当前真实数据版本的运行截图
│   ├── 01-workbench.png         工作台
│   ├── 02-monitor.png           恢复过程
│   ├── 03-real-data.png         数据与片段
│   ├── 09-mobile.png            手机宽度下的工作台
│   └── 10-mobile-monitor.png    手机宽度下的恢复过程
├── ui/                         早期页面参考图，当前实际界面以screenshots为准
│   ├── home.png                 工作台参考图
│   ├── monitor.png              恢复过程参考图
│   ├── sessions.png             记录库参考图
│   ├── replay.png               回放参考图
│   ├── compare.png              策略比较参考图
│   ├── lab.png                  数据/实验页面参考图
│   ├── system.png               系统状态参考图
│   └── settings.png             设置参考图
└── verification/               可追溯的测试与运行证据
    ├── all-tests.xml            完整测试套件的JUnit报告
    ├── firmware-tests.xml       固件专项测试的JUnit报告
    ├── firmware-builds.json     编译器、目标、内存占用与ELF哈希
    ├── qemu-real-data.json      真实QEMU完整片段、CRC、任务与离线对照结果
    └── real-comparison.json     真实片段的三策略、四相位比较结果
```

`SYSTEM_DESIGN.md` 与 `ALGORITHM_CONTRACT.md` 可保存在开发者本机，不包含在仓库当前版本中；此前的 Git 提交可能仍含历史版本。数据来源、安装、固件运行和验证说明随仓库提供。

## 本地数据与运行产物 data/

下列目录按下载和运行过程生成，具体记录ID和导出ID每次不同。除`.gitkeep`外均被Git忽略。`REPULSE_DATA_DIR`可以更改运行记录根目录，`REPULSE_DATASETS_DIR`可以更改公开数据根目录。

```text
data/
├── .gitkeep                     保留空数据目录的占位文件
├── .service.lock                单实例文件锁的载体；文件存在不代表服务正在运行
├── app.sqlite                   会话索引、备注、设置、幂等命令和比较任务
├── app.sqlite-wal / -shm        SQLite运行期间可能生成的事务与共享内存文件
├── public/                      下载到本机的公开原始数据
│   ├── wearable-device-dataset/1.0.1/
│   │   ├── AEROBIC/             有氧骑行原始记录
│   │   │   ├── S01/             第一条记录
│   │   │   ├── S02/             默认展示记录
│   │   │   └── S04/             第三条记录；每个Sxx目录均含下方说明的4个CSV
│   │   ├── metadata/            随下载保留的来源说明
│   │   │   ├── README.txt       数据提供方说明
│   │   │   ├── LICENSE.txt      数据使用与署名许可
│   │   │   ├── data_constraints.txt 原始记录的约束说明
│   │   │   └── SHA256SUMS.txt    官方文件校验表
│   │   └── manifest.json        本次下载文件、来源及校验信息
│   └── diagnostics/             可选的片段质量排查产物，不参与运行
│       ├── s02-baseline.png      起点窗口质量排查图
│       ├── s02-baseline.svg      同图的矢量版本
│       └── s02-baseline.json     同次排查的数值记录
├── sessions/<session_id>/       每次处理的独立归档目录
│   ├── meta.json                创建时的来源与配置快照
│   ├── ppg.csv                  本次实际采集的PPG、滤波值、时间与参考字段
│   ├── accel.csv                动态运动量和XYZ加速度，单位g
│   ├── events.jsonl             开始、阶段、窗口和结束等事件，每行一个JSON
│   ├── estimates.jsonl          处理期间生成的脉率与质量估计
│   ├── result.json              完成、停止或中断后的最终结果
│   └── manifest.json            归档文件的大小和SHA256清单
├── comparisons/                 后台或终端生成的策略比较结果
│   ├── <job_id>/
│   │   ├── config.json          比较输入和冻结的参数
│   │   └── progress.json        可恢复查看的任务进度和结果
│   └── cli-<dataset_id>/
│       ├── results.json         quick_compare脚本的详细结果
│       └── metrics.csv          quick_compare脚本的汇总指标
├── analysis/<replay_id>/         回放建立时生成的配置目录
│   └── config.json              原记录ID与回放配置，不复制或重写原始样本
├── transports/<session_id>/     QEMU运行期间的临时通道，退出后清理
│   ├── observations.bin         主机准备的真实观测和输入CRC
│   └── processed.bin            固件实际输出的波形及校验数据
├── exports/<export_id>/         用户请求导出时生成
│   ├── summary.json             会话摘要导出
│   ├── results.csv              会话结果表导出
│   ├── raw-record.zip           会话原始归档与备注打包
│   ├── comparison.json          比较任务详细导出
│   └── comparison.csv           比较任务表格导出
└── verification/                本地临时验证目录，当前应用无读取依赖
```

原始记录每个`Sxx/`中的文件：

| 文件 | 实际内容 |
| --- | --- |
| `BVP.csv` | 原始64 Hz脉搏波，保留设备相对单位与正负值 |
| `ACC.csv` | 原始32 Hz三轴加速度，整数单位为1/64 g |
| `HR.csv` | 设备由同一BVP计算的脉率，仅作参考，不是独立心电金标准 |
| `tags.csv` | 同次记录的原始事件标记，用于固定主动恢复起点 |

一个导出目录只包含本次选择的格式，并非总会生成上面列出的全部文件。`export_session.py`还会将ZIP下载为`data/exports/<session_id>.zip`。原始公开CSV和会话输出CSV用途不同：前者是数据源，后者保留本次采集策略产生的缺测与处理结果。

## 缓存与空目录

- `.git/`是Git自身的版本与索引数据，不属于应用代码。
- `__pycache__/`、`.pytest_cache/`、`.ruff_cache/`是Python、pytest和Ruff自动生成的缓存，不提交Git。
- 本机可能保留空的`web/js/components/`、`web/js/pages/`、`web/js/store/`和`scenarios/`。它们是早期目录占位，当前没有被导入或使用；Git不保存空目录，所以新克隆中可能不存在。
- 旧运行目录中可能保留早期演示记录；当前真实数据入口不会将其作为公开记录读取。

## 建议阅读顺序

1. `README.md`：了解如何下载数据、启动和使用。
2. `run.py` → `backend/api/app.py`：找到应用入口及接口。
3. `backend/sources/public_data.py` → `backend/algorithms/processor.py` → `backend/engines/session.py`：理解真实样本如何变成窗口结果。
4. `web/js/app.js` → `web/js/api.js`、`web/js/charts.js`：理解页面操作、通信和绘图。
5. `docs/FIRMWARE.md` → `firmware/common/main.c`、`dsp.c`：理解FreeRTOS版本。
6. `tests/`与`docs/VERIFICATION.md`：核对已验证的行为和限制。
