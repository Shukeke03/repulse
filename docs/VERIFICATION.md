# 真实数据版本验证记录

版本：0.2.0；日期：2026年10月27日；平台：macOS Apple Silicon，Miniforge Python 3.12。

## 已执行结果

| 验证 | 本次实际结果 | 证据 |
| --- | --- | --- |
| 单元、接口、固件与浏览器合并测试 | **72 passed**，110.85秒；0失败、0错误、0跳过 | [all-tests.xml](verification/all-tests.xml) |
| 原始数据与算法 | 官方SHA256、逐点BVP、原始时间戳、XYZ加速度、64 Hz时间轴、真实阶段事件、HR不参与估计、流式分块一致、质量与缺口传播 | `tests/unit/test_public_data.py`、`test_signal.py`、`test_policies.py` |
| 会话与回放 | 自动固定10秒起点、完整134秒处理、512样本窗口、关闭采集使当前值失效、重算不读取未来结果、不修改原记录 | `tests/unit/test_engine_contract.py`、`tests/api/test_app.py` |
| 接口与存储 | 持久幂等命令、重启中断、目录移动恢复、批量删除保护、原始CSV下载校验、JSON/CSV/ZIP导出、设置与比较取消 | `tests/api/test_app.py` |
| Chromium浏览器 | 4项通过；8页、360px布局、Python完整流程、FreeRTOS完整流程、六任务、冻结与切页、弹窗键盘、记录删除、备注、回放与导出 | `tests/e2e/browser_test.py` |
| 真实FreeRTOS | 13项通过；版本/CRC、固定设置、内核六任务、开始与取消、三种策略完整运行、同观测C/Python结果对照 | [firmware-tests.xml](verification/firmware-tests.xml) |
| 快速策略比较 | S02，4个相位×3种策略，12次运行、4组配对、0组排除；同组输入哈希一致 | [real-comparison.json](verification/real-comparison.json) |
| 静态检查 | Ruff检查通过，30个Python文件格式检查通过；前端JavaScript语法检查通过 | `ruff check backend tests scripts`、`ruff format --check backend tests scripts` |
| 固件构建 | QEMU Cortex-M3与STM32L476RG两个目标编译通过；当前ELF哈希已记录 | [firmware-builds.json](verification/firmware-builds.json) |

合并复现命令（先下载数据、构建QEMU固件并安装Chromium）：

```sh
conda activate repulse
python -m scripts.download_dataset
make -C firmware TARGET=qemu
export PLAYWRIGHT_BROWSERS_PATH="$(python -c 'import sys; print(sys.prefix)')/share/playwright-browsers"
python -m playwright install chromium
python -m pytest tests/unit tests/api tests/e2e/browser_test.py --junitxml=docs/verification/all-tests.xml
```

测试状态与运行记录使用隔离目录，不改写本地原始公开CSV。缺少公开数据或QEMU工具时，相应测试会明确跳过；上表本机执行没有跳过项。

## 完整真实片段

[QEMU完整流程证据](verification/qemu-real-data.json)对应S02公开有氧骑行记录：准备10秒、主动恢复124秒，共8576个64 Hz样本。10倍播放本次主机耗时17.436秒；输入和输出CRC通过，原始队列和遥测丢块均为0。该耗时受主机调度影响，不代表STM32实机性能。

| 固定窗口 | FreeRTOS在线结果 | 独立Python重算 | 判定 |
| --- | --- | --- | --- |
| 起点 | 缺测 | 缺测 | 间期与波形干扰，均拒绝估计 |
| 60秒 | 142.222 BPM | 142.22 BPM | 有效 |
| 120秒 | 130.169 BPM | 130.17 BPM | 有效 |

这组有效脉率差均小于0.01 BPM。固件使用float32，Python使用浮点科学计算；本次一致性证据不代表所有输入逐位相同。C质量检查遇到失败可提前返回，Python可记录多个原因，因此原因列表不保证逐项相同。起点缺测使ΔPR60、ΔPR120保持null。

QEMU读取同一真实BVP和运动量，在采集策略之后由C完成PPG处理与窗口计算。Python仅在验证脚本中独立重算作对照，没有将重算值代替固件输出。设备HR来自同一BVP，不是独立ECG参考。

## 策略比较的解释范围

快速比较使用S02的相位0、5、10、15秒。连续采集恢复段开启124秒，关键窗口优先策略开启70秒，周期策略平均开启约70.023秒；周期策略的最大预算量化差为0.0625秒，低于7/64秒的离散容差。

本次关键窗口优先策略保留了连续策略的60秒和120秒估计；周期策略在这4个相位没有得到完整可用的关键窗口。起点窗口在所有策略中均缺测。比较只说明这条真实记录及这些相位的工程行为，不能推广为人群效果。

误差列以相同片段的连续采集算法结果为工程对照；无可比窗口时为null，并保留可比次数。能耗是声明参数下的工程估算，未测量E4或STM32功耗。

## 验证边界

- 原始记录是公开人体实测；当前算法与工程质量门限尚无独立ECG准确度验证或临床阈值标定。
- 本次只下载三条有氧记录，原始CSV共6,116,010字节；包括来源、许可与清单的公开数据目录共6,386,413字节。
- STM32目标只完成交叉编译。传感器I2C/FIFO、显示、按键、佩戴和电气性能尚未上板验证，设备入口保持不可用。
- 尚未穷举网络中断、磁盘满、异常断电、存储损坏与长时间负载组合。回放从首样本重建，未实现检查点加速。
- 本次浏览器检查覆盖主要交互与窄屏布局，未完成独立无障碍认证。

## 界面截图

- [工作台](screenshots/01-workbench.png)
- [恢复过程](screenshots/02-monitor.png)
- [数据与片段](screenshots/03-real-data.png)
- [360px工作台](screenshots/09-mobile.png)
- [360px恢复过程](screenshots/10-mobile-monitor.png)
