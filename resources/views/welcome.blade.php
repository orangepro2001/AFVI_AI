<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light">
    <title>AFVI AI | 检出点复核</title>
    @include('partials.styles')
</head>
<body>
@include('partials.header', ['active' => 'dashboard', 'meta' => $report ? '报告 '.($report['generated_at'] ?? '时间未知') : '等待建模报告'])
<main>
    <div class="title-row">
        <div><h1>缺陷点风险排序</h1><p class="subtitle">按历史 VRS 结果排序已检出的 Defect Point，供人工复核。</p></div>
        <span class="scope-tag">HISTORICAL VRS · READ ONLY</span>
    </div>

    <div class="notice" role="note">
        <span class="notice-icon">!</span>
        <div><strong>结果适用范围：</strong>此处只衡量对历史 VRS 判定的拟合与泛化。无 VRS rewrite 的点不作为良品；“0”标签属于低可信假设良品。模型无法评估 AFVI 完全漏检的缺陷，也不会给出或写入设备参数。</div>
    </div>

    @if (! $report)
        <section class="panel" aria-live="polite">
            <div class="panel-head"><h2>建模报告</h2><span class="pill {{ $reportError === 'invalid' ? 'bad' : 'warn' }}">{{ $reportError === 'invalid' ? '报告格式无效' : ($reportError === 'unavailable' ? '报告不可读取' : '尚无报告') }}</span></div>
            <div class="empty">{{ $reportError === 'invalid' ? 'latest.json 无法解析或 schema_version 不受支持。' : ($reportError === 'unavailable' ? '报告路径不可读取，请检查 AFVI_DATA_HOME 配置。' : '训练评估完成后，报告将显示在此页面。') }}</div>
        </section>
    @else
        @php
            $coverage = $report['coverage'];
            $evaluation = $report['evaluation'];
            $split = $evaluation['split'];
            $total = max(1, $coverage['observed'] + $coverage['unlabeled']);
            $positiveWidth = min(100, $coverage['positive'] / $total * 100);
            $assumedWidth = min(100 - $positiveWidth, $coverage['assumed_good'] / $total * 100);
            $unlabeledWidth = max(0, 100 - $positiveWidth - $assumedWidth);
            $metrics = $evaluation['metrics'];
            $metricValues = is_array($metrics) ? $metrics : [];
            $auc = $metricValues['roc_auc'] ?? $metricValues['roc_auc_score'] ?? null;
            $ap = $metricValues['average_precision'] ?? $metricValues['average_precision_score'] ?? null;
            $aucLow = data_get($evaluation, 'bootstrap.intervals.roc_auc.low');
            $aucHigh = data_get($evaluation, 'bootstrap.intervals.roc_auc.high');
            $apLow = data_get($evaluation, 'bootstrap.intervals.average_precision.low');
            $apHigh = data_get($evaluation, 'bootstrap.intervals.average_precision.high');
            $status = $evaluation['status'] ?? 'unknown';
            $referenceAuc = data_get($evaluation, 'reference.metrics.roc_auc');
            $differenceLow = data_get($evaluation, 'reference.paired_bootstrap.roc_auc_difference.low');
            $differenceHigh = data_get($evaluation, 'reference.paired_bootstrap.roc_auc_difference.high');
            $formatAuc = fn ($value) => is_numeric($value) ? number_format((float) $value, 3) : '—';
            $statusClass = in_array($status, ['evaluated', 'validated', 'supported'], true) ? 'good' : (in_array($status, ['insufficient_support', 'exploratory_only'], true) ? 'warn' : 'neutral');
        @endphp
        <section class="stats" aria-label="数据概况">
            <div class="stat"><div class="stat-label">合并缺陷点</div><div class="stat-value">{{ number_format($coverage['samples']) }}</div><div class="stat-note">来自解析后的结果组</div></div>
            <div class="stat"><div class="stat-label">独立 LOT</div><div class="stat-value">{{ number_format($coverage['lots']) }}</div><div class="stat-note">验证按 LOT 隔离</div></div>
            <div class="stat"><div class="stat-label">VRS 已观测</div><div class="stat-value">{{ number_format($coverage['observed']) }}</div><div class="stat-note">正例与假设良品合计</div></div>
            <div class="stat"><div class="stat-label">标注正例</div><div class="stat-value">{{ number_format($coverage['positive']) }}</div><div class="stat-note">原始非零 VRS 码</div></div>
            <div class="stat"><div class="stat-label">无标签点</div><div class="stat-value">{{ number_format($coverage['unlabeled']) }}</div><div class="stat-note">不参与监督训练</div></div>
        </section>

        <div class="panels">
            <section class="panel">
                <div class="panel-head"><h2>标签与图像覆盖</h2><span class="panel-kicker">{{ $report['data_source'] ?? 'PxSpecTool / VRS' }}</span></div>
                <div class="panel-body">
                    <div class="label-bar" role="img" aria-label="正例 {{ $coverage['positive'] }}，低可信假设良品 {{ $coverage['assumed_good'] }}，未观测 {{ $coverage['unlabeled'] }}">
                        <span class="label-positive" style="width:{{ $positiveWidth }}%"></span><span class="label-assumed" style="width:{{ $assumedWidth }}%"></span><span class="label-unobserved" style="width:{{ $unlabeledWidth }}%"></span>
                    </div>
                    <div class="legend">
                        <div class="legend-item"><span class="swatch"></span><span>VRS 非零码<b>{{ number_format($coverage['positive']) }}</b></span></div>
                        <div class="legend-item"><span class="swatch assumed"></span><span>“0”假设良品 · 低可信<b>{{ number_format($coverage['assumed_good']) }}</b></span></div>
                        <div class="legend-item"><span class="swatch unobserved"></span><span>没有 VRS rewrite<b>{{ number_format($coverage['unlabeled']) }}</b></span></div>
                    </div>
                    <div style="margin-top:14px">
                        <div class="data-line"><span>标签冲突点</span><strong>{{ number_format($coverage['conflicting']) }}</strong></div>
                        <div class="data-line"><span>JPG manifest 已映射</span><strong>{{ number_format($coverage['images']['mapped'] + $coverage['images']['available']) }}</strong></div>
                        <div class="data-line"><span>缺少 manifest 项</span><strong>{{ number_format($coverage['images']['missing_manifest_entry']) }}</strong></div>
                        <div class="data-line"><span>检查时间</span><strong class="mono">{{ $report['generated_at'] ?? '未知' }}</strong></div>
                    </div>
                </div>
            </section>
            <section class="panel">
                <div class="panel-head"><h2>LOT / 时间隔离评估</h2><span class="pill {{ $statusClass }}">{{ $status }}</span></div>
                <div class="panel-body">
                    <div class="data-line"><span>训练样本 / LOT</span><strong>{{ number_format($split['train_rows']) }} / {{ count($split['train_lots']) }}</strong></div>
                    <div class="data-line"><span>测试样本 / LOT</span><strong>{{ number_format($split['test_rows']) }} / {{ count($split['test_lots']) }}</strong></div>
                    <div class="data-line"><span>时间切分点</span><strong class="mono">{{ $split['cutoff'] ?? '未设置' }}</strong></div>
                    @if (is_array($split['train_class_lots']) || is_array($split['test_class_lots']))
                        <div class="data-line"><span>正例所属训练 / 测试 LOT</span><strong>{{ $split['train_class_lots']['positive'] ?? '—' }} / {{ $split['test_class_lots']['positive'] ?? '—' }}</strong></div>
                        <div class="data-line"><span>假设良品所属训练 / 测试 LOT</span><strong>{{ $split['train_class_lots']['assumed_good'] ?? '—' }} / {{ $split['test_class_lots']['assumed_good'] ?? '—' }}</strong></div>
                    @endif
                    <p class="small muted">{{ $evaluation['reason'] ?? '报告未提供评估说明。' }}</p>
                    @if (is_string($evaluation['holdout_history'] ?? null))
                        <p class="small muted">留出集使用记录：{{ $evaluation['holdout_history'] }}</p>
                    @endif
                    @if (is_array($metrics))
                        <div class="metrics">
                            <div class="metric"><div class="metric-label">ROC AUC</div><div class="metric-value">{{ is_numeric($auc) ? number_format((float) $auc, 3) : '—' }}</div></div>
                            <div class="metric"><div class="metric-label">Average precision</div><div class="metric-value">{{ is_numeric($ap) ? number_format((float) $ap, 3) : '—' }}</div></div>
                            <div class="metric"><div class="metric-label">ROC AUC · 95% LOT 区间</div><div class="metric-value" style="font-size:13px">{{ is_numeric($aucLow) && is_numeric($aucHigh) ? number_format((float) $aucLow, 3).' - '.number_format((float) $aucHigh, 3) : '—' }}</div></div>
                            <div class="metric"><div class="metric-label">AP · 95% LOT 区间</div><div class="metric-value" style="font-size:13px">{{ is_numeric($apLow) && is_numeric($apHigh) ? number_format((float) $apLow, 3).' - '.number_format((float) $apHigh, 3) : '—' }}</div></div>
                            @if (is_array($evaluation['reference']))
                                <div class="metric"><div class="metric-label">参照 AUC · log1p(area_total)</div><div class="metric-value">{{ $formatAuc($referenceAuc) }}</div></div>
                                <div class="metric"><div class="metric-label">模型 − 参照 AUC · 95% 配对区间</div><div class="metric-value" style="font-size:13px">{{ is_numeric($differenceLow) && is_numeric($differenceHigh) ? number_format((float) $differenceLow, 3).' - '.number_format((float) $differenceHigh, 3) : '—' }}</div></div>
                            @endif
                        </div>
                    @else
                        <div class="empty" style="padding:14px 0 2px">独立 LOT 或类别支持不足，暂不显示泛化指标。</div>
                    @endif
                    @if (! empty($report['model']['feature_allowlist']))
                        <p class="footnote">特征：{{ implode(' · ', array_map('strval', $report['model']['feature_allowlist'])) }}</p>
                        @if (is_scalar($report['model']['feature_set'] ?? null))
                            <p class="footnote">特征集 {{ $report['model']['feature_set'] }} · L2 {{ is_numeric($report['model']['l2_strength'] ?? null) ? $report['model']['l2_strength'] : '—' }}{{ is_array(data_get($report, 'model.selection.chosen')) ? ' · 由训练 LOT 分组交叉验证选定' : '' }}</p>
                        @endif
                    @endif
                </div>
            </section>
        </div>

        @php
            $formatShare = fn ($value) => is_numeric($value) ? number_format((float) $value * 100, 1).'%' : '—';
        @endphp
        @if (! empty($evaluation['review_curve']))
            <section class="panel" style="margin-top:16px">
                <div class="panel-head"><h2>按风险分复核的覆盖率</h2><span class="panel-kicker">留出 LOT · 每个 LOT 内按分数从高到低复核</span></div>
                <div class="panel-body">
                    <div class="data-line"><span>复核前</span><strong>找到的 VRS 打码点：模型 / 仅面积 / 随机</strong></div>
                    @foreach ($evaluation['review_curve'] as $point)
                        <div class="data-line"><span class="mono">{{ number_format($point['fraction'] * 100, 0) }}%</span><strong>{{ $formatShare($point['model']) }} / {{ $formatShare($point['reference']) }} / {{ $formatShare($point['fraction']) }}</strong></div>
                    @endforeach
                </div>
            </section>
        @endif

        @if (! empty($evaluation['breakdown']['model']))
            <section class="panel" style="margin-top:16px">
                <div class="panel-head"><h2>留出 LOT 分型号表现</h2><span class="panel-kicker">ROC AUC 模型 / 参照 · 复核前 30% 找到的打码点</span></div>
                <div class="panel-body">
                    @foreach ($evaluation['breakdown']['model'] as $entry)
                        <div class="data-line"><span class="mono">{{ $entry['name'] }} · {{ number_format($entry['rows']) }} 点 / {{ $entry['lots'] }} LOT</span><strong>{{ $formatAuc($entry['model_roc_auc']) }} / {{ $formatAuc($entry['reference_roc_auc']) }} · {{ $formatShare($entry['model_recall_at_30']) }}</strong></div>
                    @endforeach
                </div>
            </section>
        @endif

        <section class="panel" style="margin-top:16px">
            <div class="panel-head"><h2>待复核点</h2><span class="panel-kicker">按风险分排序 · {{ count($report['samples']) }} 条</span></div>
            @if (count($report['samples']) === 0)
                <div class="empty">当前报告没有可展示的点级评分。</div>
            @else
                <div class="table-wrap"><table>
                    <thead><tr><th>风险</th><th>评分角色</th><th>自然键</th><th>LOT</th><th>VRS</th><th>可信度</th><th>依据</th></tr></thead>
                    <tbody>
                    @foreach ($report['samples'] as $sample)
                        @php $key = $sample['key']; @endphp
                        <tr>
                            <td class="mono score">{{ number_format((float) $sample['score'], 3) }}</td>
                            <td><span class="pill {{ $sample['prediction_role'] === 'held_out' ? 'good' : ($sample['prediction_role'] === 'exploratory_in_sample' ? 'warn' : 'neutral') }}" title="{{ $sample['prediction_role'] === 'held_out' ? '此 LOT 未参与模型拟合，用于历史留出评估。' : ($sample['prediction_role'] === 'exploratory_in_sample' ? '此分数来自参与拟合的数据，不代表独立泛化能力。' : '报告未提供可识别的评分角色。') }}">{{ $sample['prediction_role'] === 'held_out' ? '留出 LOT' : ($sample['prediction_role'] === 'exploratory_in_sample' ? '探索性样本内' : '角色未知') }}</span></td>
                            <td class="key mono">{{ implode(' / ', array_filter([$key['machine'] ?? null, $key['result_group'] ?? null, $key['strip'] ?? null, $key['side'] ?? null, isset($key['unit_idx']) ? 'U'.$key['unit_idx'] : null, isset($key['img_merge_idx']) ? 'M'.$key['img_merge_idx'] : null], fn ($value) => $value !== null && $value !== '')) }}</td>
                            <td class="mono">{{ $sample['lot'] ?? '—' }}</td>
                            <td class="mono">{{ $sample['ivs_1'] ?? '—' }}</td>
                            <td><span class="pill {{ ($sample['label_confidence'] ?? '') === 'high' ? 'good' : 'warn' }}">{{ $sample['label_confidence'] ?? 'unknown' }}</span></td>
                            <td class="evidence">{{ is_array($sample['evidence']) ? implode(' · ', array_map('strval', $sample['evidence'])) : (string) $sample['evidence'] }}</td>
                        </tr>
                    @endforeach
                    </tbody>
                </table></div>
            @endif
        </section>
        <p class="footnote">模型：{{ $report['model']['name'] ?? '未命名' }} {{ $report['model']['version'] ?? '' }} · 排序分仅供人工复核，不是设备参数建议。</p>
    @endif
</main>
</body>
</html>
