# 实现与验收记录

项目：RePulse 复脉；日期：2026年10月25日；平台：macOS Apple Silicon。

本文件区分已执行测试和仍需扩大验证的规格，不把页面存在、代码存在或STM32编译成功当作所有设计要求都已验收。

## 已执行结果

| 验证 | 结果 | 证据 |
| --- | --- | --- |
| 单元、接口、固件协议、浏览器合并测试 | **80 passed**，24.09秒，0失败、0跳过 | [test-results.xml](test-results.xml) |
| Python DSP、策略、域契约 | 固定半开窗口、重启预热、缺口/超时传播、流式分块一致、标记唯一、回放不改原记录 | `tests/unit/test_signal.py`、`test_policies.py`、`test_engine_contract.py` |
| 接口回归 | 15项通过，含持久幂等键、重启中断、批量删除保护、目录移动恢复、导出缺文件、比较取消、非法故障参数 | `tests/api/test_app.py` |
| 真实Chromium | 4项通过，覆盖8页、360px布局、Python完整交互、设置与比较、实际QEMU六任务 | `tests/e2e/browser_test.py` |
| 真实QEMU短流程 | 固定参数变更明确拒绝、内核握手、六静态任务、准备、标记、取消、CSV及manifest保存；命令确认约0.3秒 | `tests/unit/test_firmware_protocol.py` |
| 同观测C/Python算法对照 | 正常、缺口、运动干扰、脱落和超时5种场景；窗口有效性一致，有效BPM差≤2 | `tests/unit/test_firmware_protocol.py` |
| 快速策略比较 | 36次运行、12组有效配对、0组排除，输入哈希一致，周期预算量化差≤0.06秒 | [python-quick-compare.json](verification/python-quick-compare.json) |
| 静态检查 | Ruff检查通过，28个Python文件格式检查通过 | `ruff check backend tests scripts`、`ruff format --check backend tests scripts run.py` |
| 构建 | Cortex-M3 QEMU与STM32L476RG两个独立ELF均编译成功 | `firmware/build/{qemu,stm32}/repulse.elf`、对应map |

合并复现命令：

```sh
conda activate repulse
python -m pytest tests/unit tests/api tests/e2e/browser_test.py --junitxml=docs/test-results.xml
```

QEMU完整124秒恢复过程已通过，默认时钟本次耗时204.07秒（主机调度会影响仿真推进速度）。三个窗口固件输出150、94、78 BPM，独立Python离线处理同观测数据为150、93.75、77.92 BPM，均在2 BPM容差内；无原始队列或遥测丢块。证据见 [qemu-full-cycle.json](verification/qemu-full-cycle.json)。脚本使用实际收到的原始观测作独立参考，未替换固件在线结果。

## A编号覆盖范围

| 编号 | 本次证据与边界 |
| --- | --- |
| A01 A02 A03 A04 | Python完整状态与实际落盘测试通过；提前标记保留真实t0；三个窗口不后移 |
| A05 A06 | 60秒缺口只影响对应变化量；OFF立即使当前值无效，数据年龄由实际观测支持计算 |
| A07 | 浏览器冻结/恢复、切页、故障页返回与后台采集流程通过；冻结状态跨多次导航可继续扩大测试 |
| A08 | 实现重连退避、bootstrap、序列过滤与刷新恢复；尚未对每种网络中断时序做穷举注入 |
| A09 A10 | 取消保存部分结果；正常退出及重启标记中断；不伪装继续采样 |
| A11 | 搜索、标签、批量删除受保护整体拒绝、移动目录中断恢复通过；大规模分页/筛选组合尚需扩测 |
| A12 | 暂停、步进、定位和从准备样本重建通过，原文件SHA256不变；当前使用从首样本重建，未实现状态检查点加速 |
| A13 | 比较后台执行、完整配对与安全取消保留进度通过；未完成1800次完整规模长测 |
| A14 | 活动Python会话故障计划、开始、停止、撤销与事件保存通过；尚未实现对历史记录另建故障派生试验及信号预览编辑器 |
| A15 | 摘要JSON、结果CSV、原始ZIP、界面PNG通过；缺失原始文件明确拒绝完整包并允许摘要 |
| A16 | 显示时长即时生效与保存恢复、配置版本冲突和会话配置冻结通过；首版主题固定浅色 |
| A17 | Python与真实QEMU按能力选择通过，活动会话唯一；QEMU周期策略与动态故障场景明确禁用，DEVICE未接硬件 |
| A18 | 8页360px无整页横向溢出；主要按钮与表单可键盘操作；完整无障碍审计尚未执行 |
| A19 | 三种策略共用不可变输入哈希，实际OFF在滤波之前；周期采样量化差单独记录 |
| A20 | 8页空状态、页面载入与真实交互已测；未对所有页面逐一执行存储损坏、网络失败与资源耗尽组合 |

## 明确尚未完成的验证

- STM32传感器I2C/FIFO、显示、按键、接线、佩戴与电气测试；BSP目前明确返回传感器不可用。
- QEMU全部故障原因逐项等价及周期策略接入。现有C/Python对照是限定输入与2 BPM容差验证。
- 服务异常断电、磁盘满、持续存储背压、批量大文件、跨系统迁移和所有崩溃时间点的压力验证。
- 真实人体运动恢复参考对照与接触阈值标定。

这些范围没有填写为“通过”。详细设计依据保留于 [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md)，后续可按上述边界继续补齐。

## 截图

- [工作台](screenshots/01-workbench.png)
- [运行中的监护](screenshots/02-monitor.png)
- [360px工作台](screenshots/09-mobile.png)
- [360px监护](screenshots/10-mobile-monitor.png)
