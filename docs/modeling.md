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

## 参数基准草案（不是设备规格文件）

    PYTHONPATH=python python3 -m afvi_ai.propose --input "$AFVI_DATA_HOME/exports-20261106-production" \
      --split-report "$AFVI_DATA_HOME/reports/multilot-tuned-v2-20261106.json" \
      --output "$AFVI_DATA_HOME/proposals/afvi-min-defect-area-draft-<date>.xml"

按型号族 × 面（FC/BC）× light × channel × sr_metal，用全部点（不抽样）统计 area_max（merge 内最大 blob 面积，pixel）。在训练 LOT 上找使 LOT 平均 VRS 打码点损失不超过 0.5% / 1% 的最大下限，再在留出 LOT 上测同一下限。只评估"提高下限"。6 位数字码的型号族自动跳过。recommendation 为 candidate 时才表示留出 LOT 也在预算内。对应的规格项候选为 SpecParameter 1024/1025 Min Defect Size(Pixel)、1002/1011 Bright/Dark Defect Size(Pixel)，对应关系未验证；实际 InspectionSpec.xml 不在 .203 共享上。

2026-11-06 草案：225 个条件中 6 个 candidate（全部在 7M99-586/593），8 个留出超预算不建议，7 个无留出 LOT（7M99-460）未验证，其余维持现状。candidate 的复核点减少只有 0%–5.6%，说明面积下限不是减少误报的主要手段。多个条件中留出 LOT 的最小面积与训练 LOT 不同（如 7M99-586 FC L1 C2 SR2 从 30 变为 5，7M99-593 BC L0 C2 SR2 从 20 变为 120），说明设备下限在 LOT 之间被调整过。

## 检查代码与成员点（exports v2）

导出器现在为每个 merge point 输出代表点的 code_main/code_sub（哪个检查算法/节点检出）、最小成员面积 area_min，以及 members（每个成员 P 的面积、长宽、light、channel、sr_metal、code）。v2 导出在 $AFVI_DATA_HOME/exports-20261106-v2（生产组链接目录 exports-20261106-v2-production），185 组、11,878,219 行，与 v1 完全一致。light = LIGHT<n>，channel 1/2/3 = R/G/B，sr_metal 1 = SR(NonMetal)、2 = Metal。

## 下限变更证据（自然实验）

    PYTHONPATH=python python3 -m afvi_ai.limits --input "$AFVI_DATA_HOME/exports-20261106-v2-production" --by-code \
      --output "$AFVI_DATA_HOME/proposals/afvi-limit-change-evidence-bycode-<date>.xml"

同一条件下各 LOT 观测到的最小 area_max 近似该 LOT 实际生效的下限（只取点数 ≥200、且最小值附近有截断堆积的 LOT）。下限相近的 LOT 归为一个区间；在低下限 LOT 中统计落在高下限以下的点和 VRS 打码点，从而同时得到"提高"和"降低"下限的证据。2026-11-06：不分代码 21 个条件、按代码 30 个条件有下限变化。例：7M99-593 FC LIGHT0 G Metal 40→20，低区间占点 66%、打码点 42%、打码率 9.5%（降低有价值）；7M99-586 BC LIGHT2 B SR 30→40，低区间占点 16%、打码点 5.5%（提高代价小）；7M99-593 BC LIGHT0 G Metal 120 与 20 之间占点 88%、打码点 60%（需品质判断）。

## 位置热点

    PYTHONPATH=python python3 -m afvi_ai.hotspots --input "$AFVI_DATA_HOME/exports-20261106-v2-production" \
      --output "$AFVI_DATA_HOME/proposals/afvi-hotspots-<date>.xml"

把 strip 坐标按 100px 分箱，按数据估计每个 unit 的范围，映射到 unit 内 10×10 相对网格。Dummy 区几乎所有型号打码率 0.0–0.2%，而占点可达 9–24%（23MSP217-49 FC 23.7%、25FBO015-15 FC 14.6%、24FBO060-19 FC 12.3%）。unit 内固定位置的误报集中：25FBO015-15 BC (9,9) 占 29.5%、打码率 0.05%；7M99-593 BC (4,6) 占 15.5%、0.10%；7M99-535 BC (7,0) 占 9.9%、0.02%。这些是 Adjust Mask / Remove Edge 类参数的复核候选。

## v4 候选模型

在 34 个训练 LOT 上 5 折分组交叉验证：size3 0.660，structured 0.736（L2 0.001）/0.747（L2 0.0001），structured_code 0.777/0.779。v4 = structured_code、L2 0.0001，事先由交叉验证选定：

    PYTHONPATH=python python3 -m afvi_ai.train --input "$AFVI_DATA_HOME/exports-20261106-v2-production" \
      --output "$AFVI_DATA_HOME/reports/multilot-v4-<date>.json" --cutoff 2026-08-15T00:00:00+09:00 \
      --max-rows-per-lot-state 1500 --max-rows 400000 --seed 0 --feature-set structured_code --l2 0.0001 \
      --final-model "$AFVI_DATA_HOME/models/afvi-risk-v4-<date>.json"

28 个留出 LOT 对 v4 是第三次使用，只作参考；决定性检验是对 2026-11-06 以后的新 LOT 用 afvi_ai.confirm 同时检验 v3 和 v4。

## 网站入口

https://tony-ai-pc-1.tail9f7f1f.ts.net/afvi_ai （tailscale serve 路径 /afvi_ai → 127.0.0.1:18000，路由同时注册在 / 和 /afvi_ai 下）：

- 模型结果（/）：结构化模型评估报告 reports/latest.json。
- 复核清单（/review）：读取 reports/review/current（指向 v4-20261106）的 summary.json 和每个 LOT 的 CSV；按风险分排序，可按面筛选、隐藏 Dummy、分页、下载完整 CSV；按型号的留出 AUC 标注可信度（≥0.75 强，0.65–0.75 中，<0.65 弱，无证据为未验证）。
- 图片判断（/vision）：上传缺陷小图（BMP/JPG/PNG，≤2 MB），Laravel 把图片写入 storage/app/vision/inbox（宿主机 /home/tony/afvi-ai-data/laravel-runtime/app/vision），systemd 用户服务 afvi-vision-worker 用图像模型打分后写回 outbox。没有新开网络端口。预览容器不保存会话，所以上传路由不校验 CSRF 令牌，改为校验 Origin/Referer 必须同源。

重新生成复核清单（按 LOT 逐个处理，内存有上限）：

    systemd-run --user --scope -p MemoryMax=4G -p MemorySwapMax=0 env PYTHONPATH=python \
      python3 -m afvi_ai.score --model "$AFVI_DATA_HOME/models/afvi-risk-v4-20261106.json" \
      --input "$AFVI_DATA_HOME/exports-20261106-v2-production" --output-dir "$AFVI_DATA_HOME/reports/review/<run>"
    ln -sfn <run> "$AFVI_DATA_HOME/reports/review/current"

## 参数基准（其他人怎么放参数）

    systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 env PYTHONPATH=python python3 -m afvi_ai.spec_baseline \
      --inventory FM1=/srv/pxshares/192.168.1.61/PxInventory:TOP --inventory FM2=/srv/pxshares/192.168.1.62/PxInventory:TOP \
      --inventory BM=/srv/pxshares/192.168.1.63/PxInventory:BOTTOM --output-dir "$AFVI_DATA_HOME/reports/baseline" \
      --limit-evidence "$AFVI_DATA_HOME/proposals/afvi-limit-change-evidence-bycode-20261106.json" \
      --proposals "$AFVI_DATA_HOME/proposals/afvi-min-defect-area-bycode-draft-20261106.xml"

只读三台 Vision 的 INSPECT_SPEC（排除复制件、备份、TEST、带空格/括号/韩文的文件夹和 DEFAULT_SPEC）。每个 (LIGHT, 节点, MASTER/SUBMASTER/INSPECTION, 参数键, 通道) 统计：有该行的型号数、节点启用数、通道参与数（INSPECTION 通道值 0 = 该通道不参与）、参与型号中的多数值与占比、中位、10–90%、范围、常见值、DEFAULT_SPEC/Default 的值；并列出每个型号与"强共识"（多数值占比 ≥60%、参与型号 ≥5）不同的地方。网站 /baseline 显示基准，/baseline/model 显示某型号与多数值的对照，并附上该型号族在同一面、同一光源下 AI 从结果数据观察到的实际下限（对应到哪个规格项尚未验证）。

2026-11-07：FM1 69 个、FM2 41 个、BM 60 个生产型号，读取无错误。DMG 节点（CNODE 12，机台内置的外观 AI 判定，不靠参数；1024 只出现在这里）按噪声处理，不计入。参与型号 ≥5 的行里，强共识占 FM1 85%、FM2 89%、BM 98%；意见分散的主要是对位偏移 901/902 和 Chain Search Range。DEFAULT_SPEC 与强多数值不同的行：FM1 25、FM2 26、BM 15，例如 FM2 L-Pad 1015 Dark Defect Offset2 Size 多数 200/150 而 DEFAULT 为 15，503 Remove SR Edge 多数 6 而 DEFAULT 为 10。与多数差别最多的型号：FM1 24msp185-03-00（50 处）、SPFCE300N4.KMA0-00、7M99-631-00、jacson-00、danny-00。

## 图像模型

PyTorch 装在 $AFVI_DATA_HOME/venv-vision（Python 3.12，torch 2.14.1+cu126，GTX 1080 Ti 可用）。经过 VRS 复核的点几乎都保存了缺陷图（约 16.8 万打码、约 500 万未打码）。

    # 1. 采样并缩成 192×192 JPEG（只读 .203 共享，限速 50 MiB/s）
    systemd-run --user --scope -p MemoryMax=4G -p MemorySwapMax=0 env PYTHONPATH=python \
      $AFVI_DATA_HOME/venv-vision/bin/python -m afvi_ai.vision.dataset \
      --input "$AFVI_DATA_HOME/exports-20261106-v2-production" \
      --split-report "$AFVI_DATA_HOME/reports/multilot-tuned-v2-20261106.json" \
      --share-root /srv/pxshares/192.168.1.203/pixel --output-dir "$AFVI_DATA_HOME/vision/dataset-<date>" \
      --per-lot-class 900 --workers 4 --max-mbps 50
    # 2. 训练（ResNet18 ImageNet 预训练微调；训练 LOT 中留出验证 LOT 做早停和阈值；留出 LOT 只评一次）
    systemd-run --user --scope -p MemoryMax=8G -p MemorySwapMax=0 env PYTHONPATH=python \
      $AFVI_DATA_HOME/venv-vision/bin/python -m afvi_ai.vision.train_image \
      --dataset "$AFVI_DATA_HOME/vision/dataset-<date>" --output-dir "$AFVI_DATA_HOME/models/vision-<date>" \
      --report "$AFVI_DATA_HOME/reports/vision/vision-<date>.json"
    # 3. 启用：models/vision-current → vision-<date>，reports/vision/latest.json → 该报告
    systemctl --user restart afvi-vision-worker

2026-11-06 数据集：100,833 张（62 个 LOT；训练 58,477 张，留出 42,356 张；缺图 99 张）。所有重任务都用 systemd-run 设内存上限并禁用 swap。

## 标签语义

ivs_1 是复核站人工键入码（见 PxSpecTool docs/result-data/50-ivs-keyin.md），"0" 表示未键入。ResultData/DLG 列出的是送复核的条带，不是深度学习抽样；在已复核条带内，列表内外的键入率相近（3.8% 与 4.4%）。码表按型号族配置：7M99 系为字母码，FBO/MSP 系为 3–4 位数字，B 系（4SP0223G01、3HN1008O01、6AN0921I01、6ST2001Q01）为 6 位数字（450xxx/850xxx/880xxx/150xxx）。码含义不在 ResultData 中；各族码频次表在 $AFVI_DATA_HOME/inventory/vrs-codes-by-family.csv，留有含义和是否真实不良两列待品质部门填写。B 系在训练 LOT 交叉验证中同样表现弱，标签定义是首要怀疑对象。

本次实际导出目录是 $AFVI_DATA_HOME/exports-20261106-all，训练输入为只含生产组的符号链接目录 exports-20261106-production。达到门槛只说明对独立历史 LOT 的 VRS 判定有评估结果，不代表生产召回率，也不构成参数优化结论。参数回放仍需先证明固定帧、规格和参数可以复现设备输出。
