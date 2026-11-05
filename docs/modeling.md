# 建模阶段说明

## 当前产物

模型不是参数曲线，也不是自动调机。它从 PxSpecTool 解析出的结构化字段学习一个岭回归逻辑回归，对已经检出的 merge point 计算历史 VRS 不良风险分，并按分数排序供人工复核。图片只通过已有 manifest 建立引用，不读取图像像素。

特征集事先固定为两组：size3 = log1p(area_total、length_max、width_max)；structured = size3 + log1p(area_max) + is_dummy + side、def_side、light_id、channel_idx、sr_metal 的 one-hot（类别取值只从训练行学习）。坐标和 pc_idx 不进入模型。使用 --tune 时，只在训练 LOT 内做 5 折 LOT 分组交叉验证，在 {size3, structured} × L2 {2.0, 0.1, 0.01, 0.001} 中按平均折 ROC-AUC 选择，再在全部训练 LOT 上重拟合，对留出 LOT 只评一次。求解器为带回溯的 Newton 法。

形式上，模型先用训练 LOT 的统计量标准化特征，再计算 score = sigmoid(intercept + coefficients · features)。这些系数描述历史点级标签的排序关系，不是设备参数的最优值。VRS 中非零 ivs_1 是正例；0/空值仅作为低可信假设良品，并做权重敏感性检查；无 VRS rewrite 和标签冲突不参与拟合。报告同时给出固定参照 log1p(area_total) 的留出 AUC、模型与参照的配对 LOT bootstrap 差值区间，以及按机台和型号的留出表现。

## 小样本保护

每个 LOT 在特征缩放和拟合中总权重相同，避免点数特别多的一个批次支配模型。评估按 LOT 与时间隔离，主要指标让每个含正例或假设良品的 LOT 对相应类别具有相同权重，并按 LOT 做 bootstrap。要报告留出指标，训练和测试必须各有至少 5 个 LOT、每类至少 20 个点，且正例和假设良品各自至少分布在 3 个独立 LOT。

达不到门槛时，系统仍可生成探索性排序，但会标为 exploratory_only，不显示泛化指标。达到门槛也只说明对独立历史 LOT 的 VRS 判定有评估结果，不代表生产召回率，不代表模型能发现 AFVI 完全漏检的缺陷，也不构成参数优化结论。

## 现有样本状态（2026-11-06）

ResultData/Data/{AFVI13,AFVI14} 下按 model/LOT/group 有限层级盘点出 228 个生产组（排除名称含 TEST、DRY-RUN 的型号或 LOT），其中 185 组成功只读导出，共 11,878,219 个点、74 个 LOT，62 个 LOT 有 VRS 标签，59 个含正例。缺 Bundle001.ini 的组被跳过。

按 2026-08-15T00:00:00+09:00 切分（另随机留出 20% 早期 LOT）：34 个训练 LOT、28 个留出 LOT。第一次（冻结 size3、L2 2.0）留出 AUC 0.518，不合格。第二次（训练 LOT 交叉验证选出 structured、L2 0.001）留出 AUC 0.655（LOT bootstrap 95% 0.565–0.711），参照 0.560，配对差值 −0.005 至 +0.162。第二次是同一留出集的第二次使用。

## 运行步骤

以下命令在 AI 服务器运行，输入均为只读挂载，输出都在仓库之外：

    AFVI_DATA_HOME=/home/tony/afvi-ai-data
    RESULT_DATA=/srv/pxshares/192.168.1.203/pixel/ResultData/Data

    # 1. 有限层级盘点（不进入 strip 目录内部）
    PYTHONPATH=python python3 -m afvi_ai.inventory \
      --data-root "$RESULT_DATA" --output "$AFVI_DATA_HOME/inventory/groups.jsonl"

    # 2. 逐组导出（可续跑：已存在的非空文件会跳过）
    go build -mod=mod -o "$AFVI_DATA_HOME/bin/afvi-export" ./cmd/afvi-export
    python3 -c 'import json,sys
    for l in open(sys.argv[1]):
        r = json.loads(l); print(r["machine"], r["group_rel"], r["group_dir"], sep="\t")' \
      "$AFVI_DATA_HOME/inventory/groups.jsonl" |
    while IFS="$(printf '\t')" read -r machine rel dir; do
      out="$AFVI_DATA_HOME/exports/$machine/$rel.jsonl"
      [ -s "$out" ] && continue
      mkdir -p "$(dirname "$out")"
      "$AFVI_DATA_HOME/bin/afvi-export" -machine "$machine" -group-rel "$rel" -group-dir "$dir" > "$out.part" \
        && mv "$out.part" "$out" || { rm -f "$out.part"; echo "FAIL $machine $rel" >&2; }
    done

    # 3. 只看标签与时间的覆盖表，先定 cutoff，再训练
    PYTHONPATH=python python3 -m afvi_ai.coverage --input "$AFVI_DATA_HOME/exports" \
      --output "$AFVI_DATA_HOME/inventory/coverage.json" --cutoff 2026-08-15T00:00:00+09:00

    # 4. 训练与评估（每个 LOT×标签状态抽样 1,500 行并按全量回权）
    PYTHONPATH=python python3 -m afvi_ai.train --input "$AFVI_DATA_HOME/exports" \
      --output "$AFVI_DATA_HOME/reports/latest.json" --cutoff 2026-08-15T00:00:00+09:00 \
      --max-rows-per-lot-state 1500 --max-rows 400000 --seed 0 --tune --tuning-workers 8 \
      --holdout-note "说明该留出集此前被评估过几次"

## 冻结模型与新 LOT 确认

第 4 步加 --final-model "$AFVI_DATA_HOME/models/<name>.json" 时，会用选定设置在全部有标签 LOT 上重拟合并保存模型 JSON（当前为 models/afvi-risk-v3-20261106.json，62 个 LOT，structured、L2 0.001）。新数据到来后，只对新增组做第 1、2 步，然后：

    PYTHONPATH=python python3 -m afvi_ai.confirm \
      --model "$AFVI_DATA_HOME/models/afvi-risk-v3-20261106.json" \
      --input "$AFVI_DATA_HOME/exports-new" --output "$AFVI_DATA_HOME/reports/confirm-<date>.json"

确认不重拟合；模型训练集中出现过的 LOT 会被剔除。新 LOT 需满足与留出集相同的门槛（至少 5 个 LOT，两类各至少 3 个 LOT、20 行），合格判定沿用事先登记的标准：LOT bootstrap 下界 > 0.5，且点估计高于 log1p(area_total) 参照。

## 复核清单

    PYTHONPATH=python python3 -m afvi_ai.score \
      --model "$AFVI_DATA_HOME/models/afvi-risk-v3-20261106.json" \
      --input "$AFVI_DATA_HOME/exports-new" --output-dir "$AFVI_DATA_HOME/review/<date>" \
      --share-root /srv/pxshares/192.168.1.203/pixel --check-images 500

每个 LOT 输出一份 CSV（风险从高到低）：排名、分数、所在前百分比、strip/side/unit/merge 定位、ResultData 中的缺陷图相对路径、前 N 个点的图是否实际保存（只 stat，不读像素）、已有 VRS 码，以及该型号族在留出评估中的 AUC 和前 30% 覆盖率。summary.json 标出训练中没出现过或没有留出证据的型号族。分数是复核优先级，不是判定结果或设备参数。

留出 LOT 上按每个 LOT 内分数从高到低复核时，找到的 VRS 打码点比例（LOT 平均）：前 10% 27.5%，前 20% 43.1%，前 30% 57.5%，前 50% 74.8%；仅按面积排序为 13.3%/25.9%/38.8%/61.8%，随机为 10%/20%/30%/50%。前 30% 在 7M99-535/586/593、24FBO060-19、24MSP037-15、25FBO015-15 上为 77%–88%，在 B 系（4SP0223G01、6ST2001Q01、6AN0921I01）上为 25%–44%。

## 标签语义

ivs_1 是复核站人工键入码（见 PxSpecTool docs/result-data/50-ivs-keyin.md），"0" 表示未键入。ResultData/DLG 列出的是送复核的条带，不是深度学习抽样；在已复核条带内，列表内外的键入率相近（3.8% 与 4.4%）。码表按型号族配置：7M99 系为字母码，FBO/MSP 系为 3–4 位数字，B 系（4SP0223G01、3HN1008O01、6AN0921I01、6ST2001Q01）为 6 位数字（450xxx/850xxx/880xxx/150xxx）。码含义不在 ResultData 中；各族码频次表在 $AFVI_DATA_HOME/inventory/vrs-codes-by-family.csv，留有含义和是否真实不良两列待品质部门填写。B 系在训练 LOT 交叉验证中同样表现弱，标签定义是首要怀疑对象。

本次实际导出目录是 $AFVI_DATA_HOME/exports-20261106-all，训练输入为只含生产组的符号链接目录 exports-20261106-production。达到门槛只说明对独立历史 LOT 的 VRS 判定有评估结果，不代表生产召回率，也不构成参数优化结论。参数回放仍需先证明固定帧、规格和参数可以复现设备输出。
