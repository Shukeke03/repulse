# 数据集说明

RePulse 从本地文件读取公开运动记录。数据未下载、文件缺失或校验失败时，数据源不可用。

## 来源与文件

- 数据集：[Wearable Device Dataset from Induced Stress and Structured Exercise Sessions，v1.0.1](https://physionet.org/content/wearable-device-dataset/1.0.1/)，Hongn 等。
- DOI：[10.13026/he0v-tf17](https://doi.org/10.13026/he0v-tf17)
- 论文：[Wearable Physiological Signals under Acute Stress and Exercise Conditions](https://www.nature.com/articles/s41597-025-04845-9)
- 许可：Open Data Commons Attribution License v1.0，原始 `LICENSE.txt` 随数据保存。

项目使用有氧骑行 S01、S02、S04 的 BVP、ACC、HR 和 tags 文件，CSV 合计约 6.1 MB：

```text
data/public/wearable-device-dataset/1.0.1/
  AEROBIC/S01/{BVP,ACC,HR,tags}.csv
  AEROBIC/S02/{BVP,ACC,HR,tags}.csv
  AEROBIC/S04/{BVP,ACC,HR,tags}.csv
  metadata/{README,LICENSE,data_constraints,SHA256SUMS}.txt
  manifest.json
```

首次下载：

```sh
conda activate repulse
python -m scripts.download_dataset
```

脚本优先从 PhysioNet S3 镜像下载，失败后使用 PhysioNet 原始地址。CSV 按代码中固定的 SHA-256 校验值验证；校验值对应数据集的官方 `SHA256SUMS.txt`。运行时读取已下载的文件，不自动更新数据。只下载默认记录可使用 `python -m scripts.download_dataset --subjects S02`。

`data/` 被 Git 忽略，原始数据和处理记录均保存在本机。可通过 `REPULSE_DATASETS_DIR` 指定数据根目录。

## 恢复起点与时间窗口

S01、S02、S04 使用作者的有氧第一版协议，每条记录有 12 个事件标记。协议图如下：

![作者有氧第一版协议与事件编号](dataset-protocol-aerobic-v1.png)

- 第 11 个标记（从 0 计数为 10）：110 rpm 负荷结束，开始低阻力踩踏恢复。
- 第 12 个标记：低阻力恢复结束，进入静止休息。

图像来源及 Notebook SHA-256 见 [dataset-protocol-provenance.json](dataset-protocol-provenance.json)。系统以第 11 个标记为主动恢复起点。

每次处理截取起点前 10 秒至起点后 124 秒，共 134 秒、8576 个 BVP 样本。片段时间从 0 开始，恢复起点位于第 10 秒。三个 8 秒窗口相对起点分别为 `[-4, 4)`、`[56, 64)` 和 `[116, 124)` 秒。播放速度和回放定位不改变这些时间边界。

数据提供方对原始日期做了平移；同次记录内的相对时间用于对齐。界面的记录创建时间是本次处理的时间。

## 采样率与量纲

| 文件 | 原始数据 | 项目中的处理 |
| --- | --- | --- |
| `BVP.csv` | 64 Hz，带正负值的设备相对单位（a.u.） | 保留原始采样率和数值，供脉率算法处理 |
| `ACC.csv` | 32 Hz 三轴加速度，整数单位为 1/64 g | 换算为 g，逐轴减去时间常数为 1 秒的因果指数移动均值，再取动态向量的模；以前值保持对齐到 BVP 时间轴 |
| `HR.csv` | 1 Hz，设备由同一 BVP 计算的 10 秒平均脉率 | 仅用于显示对照，不输入脉率算法；不是独立的心电参考 |
| `tags.csv` | 同次记录的事件标记 | 确定主动恢复起点 |

处理记录的 CSV 同时保存原始平移时间戳及以 g 表示的 XYZ 加速度。32 Hz 的加速度以前值保持方式对应到 64 Hz 样本行。

采集策略在 BVP 进入 DSP 之前确定样本是否可用；关闭期间记录为缺测，重新开启后预热 2 秒。策略比较使用同一原始片段及输入标识。

## 信号质量

连续读取默认记录 S02 时，60 秒和 120 秒窗口有有效脉率估计；起点窗口的峰间期和自相关一致性未通过质量检查，因此 `ΔPR60` 和 `ΔPR120` 均缺测。S01 和 S04 的部分窗口也存在信号质量问题。

质量规则包括 0.5–5 Hz 因果 Butterworth 带通、8 秒分析窗口、2 秒预热、峰间期中位数和自相关一致性检查。阈值属于工程参数，尚未经独立心电参考标定；无效结果以 `null` 及原因码表示。

## 验证

```sh
python -m pytest tests/unit/test_public_data.py tests/unit/test_signal.py tests/unit/test_policies.py
```

测试使用本地已下载记录，覆盖文件哈希、逐点采样值、64 Hz 时序、阶段标记、分块滤波、缺口与预热传播，以及设备 HR 字段对算法输出的隔离。
