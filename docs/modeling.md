# 建模阶段说明

## 当前产物

首版不是一条参数曲线，也不是自动调机。它从 PxSpecTool 解析出的结构化特征中学习一个低容量、强正则化的逻辑回归函数，对已经检出的 merge point 计算历史 VRS 不良风险分，并按分数排序供人工复核。当前只使用 log1p(area_total)、log1p(length_max) 和 log1p(width_max) 三个特征；图片只通过已有 manifest 建立引用，不读取图像像素。

形式上，模型先用训练 LOT 的统计量标准化特征，再计算 score = sigmoid(intercept + coefficients · features)。这些系数描述历史点级标签的排序关系，不是设备参数的最优值。VRS 中非零 ivs_1 是正例；0/空值仅作为低可信假设良品，并做权重敏感性检查；无 VRS rewrite 和标签冲突不参与拟合。

## 小样本保护

每个 LOT 在特征缩放和拟合中总权重相同，避免点数特别多的一个批次支配模型。评估按 LOT 与时间隔离，主要指标让每个含正例或假设良品的 LOT 对相应类别具有相同权重，并按 LOT 做 bootstrap。要报告留出指标，训练和测试必须各有至少 5 个 LOT、每类至少 20 个点，且正例和假设良品各自至少分布在 3 个独立 LOT。

达不到门槛时，系统仍可生成探索性排序，但会标为 exploratory_only，不显示泛化指标。达到门槛也只说明对独立历史 LOT 的 VRS 判定有评估结果，不代表生产召回率，不代表模型能发现 AFVI 完全漏检的缺陷，也不构成参数优化结论。

## 现有样本状态

当前已验证的真实导出包含 26,757 个点，但都属于同一个 LOT。它可用于检查解析、标签、manifest join 和探索性拟合；由于只有一个独立 LOT，不能用于跨 LOT 验证。随着数据积累，先看独立 LOT 数及各 LOT 的标签分布，再看点数。

## 训练命令

下面的示例在 AI 服务器读取现有解析导出和 JPG manifest。它不会遍历 BMP，不读取 JPG 内容，也不会写入 ResultData、设备参数或正式数据库。报告输出到仓库之外：

    AFVI_DATA_HOME=/home/tony/afvi-ai-data
    PYTHONPATH=python python3 -m afvi_ai.train \
      --input /tmp/afvi-real-group-v2.jsonl \
      --output "$AFVI_DATA_HOME/reports/latest.json" \
      --cutoff 2026-10-01T00:00:00+09:00 \
      --manifest /var/lib/pximages/192.168.1.203/manifest.jsonl \
      --jpg-root /var/lib/pximages \
      --image-label 192.168.1.203

这里使用的单 LOT 示例只生成探索性报告。正式跨 LOT 评估要等到训练与留出时间段中都有足够独立 LOT，并满足上述每类覆盖门槛。参数回放仍需先证明固定帧、规格和参数可以复现设备输出。
