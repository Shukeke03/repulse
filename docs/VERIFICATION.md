# 验证记录

以下结果记录于 2026 年 10 月 27 日，项目版本 0.2.0，验证平台为 macOS Apple Silicon、Miniforge Python 3.12。测试报告记录当次运行结果；后续修改应重新执行相应测试。

## 测试与构建

| 验证 | 当次结果 | 证据 |
| --- | --- | --- |
| 单元、接口、固件与浏览器合并测试 | **72 passed**，110.85 秒；0 失败、0 错误、0 跳过 | [all-tests.xml](verification/all-tests.xml) |
| 数据与算法 | 校验原始文件哈希、逐点 BVP、时间戳与 XYZ 加速度；检查固定起点、分块滤波、缺口和预热传播 | `tests/unit/test_public_data.py`、`test_signal.py`、`test_policies.py` |
| 会话与回放 | 检查 10 秒起点、134 秒处理时长、512 样本窗口、采集关闭、回放游标和原记录完整性 | `tests/unit/test_engine_contract.py`、`tests/api/test_app.py` |
| 接口与存储 | 检查幂等请求、重启恢复、删除保护、原始 CSV 下载校验、导出、设置与比较取消 | `tests/api/test_app.py` |
| Chromium | 4 项通过，覆盖 8 页、360 px 布局、两种执行路径的完整流程、回放、备注和导出 | `tests/e2e/browser_test.py` |
| 固件专项 | 13 项通过，覆盖输入协议、CRC、任务状态、取消、三策略及 C/Python 结果对照 | [firmware-tests.xml](verification/firmware-tests.xml) |
| 策略比较 | S02，4 个相位 × 3 种策略，12 次运行；4 组配对，0 组排除 | [real-comparison.json](verification/real-comparison.json) |
| 静态检查 | Ruff 检查和 30 个 Python 文件的格式检查通过；JavaScript 语法检查通过 | `ruff check backend tests scripts`、`ruff format --check backend tests scripts` |
| 固件构建 | QEMU Cortex-M3 和 STM32L476RG 目标编译通过，记录了当次 ELF 哈希 | [firmware-builds.json](verification/firmware-builds.json) |

复现合并测试时，将报告写入本地数据目录，避免覆盖已记录的历史结果：

```sh
conda activate repulse
python -m scripts.download_dataset
make -C firmware TARGET=qemu
export PLAYWRIGHT_BROWSERS_PATH="$(python -c 'import sys; print(sys.prefix)')/share/playwright-browsers"
python -m playwright install chromium
mkdir -p data/verification
python -m pytest tests/unit tests/api tests/e2e/browser_test.py --junitxml=data/verification/all-tests.xml
```

测试使用隔离的运行目录，不改写原始 CSV。浏览器测试需要本地公开数据，缺失时会失败；部分 QEMU 测试在工具或固件不可用时会跳过。上表所记录的运行没有跳过项。

## S02 完整片段

[QEMU 运行记录](verification/qemu-real-data.json)使用 S02 有氧骑行数据：起点前 10 秒和主动恢复 124 秒，共 8576 个 64 Hz 样本。播放倍率为 10 倍，当次主机耗时 17.436 秒。输入和输出的样本数及 CRC 校验通过；主机耗时受调度影响，不代表 STM32 实机性能。

| 固定窗口 | FreeRTOS 固件结果 | Python 离线重算 | 判定 |
| --- | --- | --- | --- |
| 起点 | 缺测 | 缺测 | 峰间期和波形周期一致性未通过检查 |
| 60 秒 | 142.222 BPM | 142.22 BPM | 有效 |
| 120 秒 | 130.169 BPM | 130.17 BPM | 有效 |

两个有效结果的差值均小于 0.01 BPM。固件使用 float32，Python 使用浮点数值计算；两实现的质量检查可能返回不同数量的原因码。由于起点缺测，`ΔPR60` 和 `ΔPR120` 均为 `null`。

主机为两种执行路径提供加速度预处理。QEMU 固件按策略处理 BVP 并输出窗口结果；验证脚本在固件处理结束后，用相同观测由 Python 离线重算窗口以比较实现一致性。设备 HR 由同一 BVP 计算，不是独立心电参考。

当次报告中的 `raw_drops` 为 0；该字段统计 `qRaw` 和 `qEvents` 的发送失败。`telemetry_drops` 在固件中固定为 0，并非实测遥测丢失计数。样本完整性主要通过序号、条数和 CRC 校验。

## 策略比较

当次快速比较使用 S02 的相位 0、5、10、15 秒。恢复段内连续读取 124 秒，关键窗口优先策略读取 70 秒，周期策略平均读取约 70.023 秒；周期策略的最大预算量化差为 0.0625 秒，小于 7/64 秒的离散容差。

关键窗口优先策略保留了连续读取的 60 秒和 120 秒估计；周期策略在这 4 个相位均未得到完整可用的关键窗口。所有策略的起点窗口均缺测。这些结果仅适用于该记录及相位设置。

差异指标以同一片段的连续读取结果为对照，无可比窗口时为 `null`，并记录可比次数。能耗值来自固定参数的工程模型，未测量 E4 或 STM32 功耗。

## 验证范围

- 原始文件来自公开人体采集。当前算法和质量阈值尚未进行独立心电准确度验证或临床标定。
- 当次下载的三条有氧记录的 CSV 共 6,116,010 字节；`data/public/` 当时的总快照为 6,386,413 字节，包含元数据、清单和诊断产物，并非单指版本目录。
- STM32 目标仅交叉编译；传感器 I2C/FIFO、显示、按键、接线、佩戴和电气性能尚未上板验证。
- 网络中断、磁盘满、异常断电、存储损坏和长期负载组合未穷举。回放从首样本重建，未实现检查点加速。
- 当次浏览器检查覆盖主要交互和窄屏布局，未进行独立无障碍认证。

## 界面截图

- [工作台](screenshots/01-workbench.png)
- [恢复过程](screenshots/02-monitor.png)
- [数据与片段](screenshots/03-real-data.png)
- [360 px 工作台](screenshots/09-mobile.png)
- [360 px 恢复过程](screenshots/10-mobile-monitor.png)
