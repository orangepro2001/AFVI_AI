@php
    use App\Support\PublicPath;
    $heldOut = is_array($report['held_out'] ?? null) ? $report['held_out'] : [];
    $modelInfo = is_array($report['model'] ?? null) ? $report['model'] : [];
    $auc = data_get($heldOut, 'metrics.roc_auc');
    $aucLow = data_get($heldOut, 'bootstrap.intervals.roc_auc.low');
    $aucHigh = data_get($heldOut, 'bootstrap.intervals.roc_auc.high');
    $miss = data_get($heldOut, 'at_threshold.keyed_miss_rate');
    $alarm = data_get($heldOut, 'at_threshold.good_false_alarm_rate');
    $percent = fn ($value) => is_numeric($value) ? number_format((float) $value * 100, 1).'%' : '—';
@endphp
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light">
    <title>AFVI AI | 图片判断</title>
    @include('partials.styles')
</head>
<body>
@include('partials.header', ['active' => 'vision', 'meta' => $worker ? '推理服务 '.($worker['device'] ?? '') : '推理服务未启动'])
<main>
    <div class="title-row">
        <div><h1>缺陷图片判断</h1><p class="subtitle">上传一张 AFVI 缺陷小图（p&lt;n&gt;.bmp 或转换后的 JPG），判断复核员会不会把它打成不良。</p></div>
        <span class="scope-tag">IMAGE MODEL · {{ strtoupper((string) ($modelInfo['architecture'] ?? 'not trained')) }}</span>
    </div>

    <div class="notice" role="note">
        <span class="notice-icon">!</span>
        <div><strong>结果含义：</strong>模型学的是历史 VRS 复核结果——"不良" = 复核员给这类点打了不良码，"良品" = 复核过但没打码。它只看图片本身，不知道光源、检查算法和规格；B 系列（6 位 VRS 码）的码含义尚未确认，结论要打折扣。请把它当作复核辅助，不要直接当作判定。</div>
    </div>

    <div class="panels">
        <section class="panel">
            <div class="panel-head"><h2>上传图片</h2><span class="panel-kicker">BMP / JPG / PNG · 最大 2 MB</span></div>
            <div class="panel-body">
                <form class="upload" method="post" action="{{ PublicPath::url('vision') }}" enctype="multipart/form-data">
                    <input type="file" name="image" accept=".bmp,.jpg,.jpeg,.png,image/bmp,image/jpeg,image/png" required>
                    <button class="button" type="submit">判断</button>
                </form>
                @if ($errors->any())
                    <p class="small" style="color:var(--red)">{{ $errors->first() }}</p>
                @endif
                @if ($error)
                    <p class="small" style="color:var(--red)">{{ $error }}</p>
                @endif
                @if ($result)
                    <div style="display:flex; gap:18px; align-items:flex-start; margin-top:16px; flex-wrap:wrap">
                        @if ($preview)<img class="preview" src="{{ $preview }}" alt="上传的图片">@endif
                        <div>
                            <div class="verdict {{ $result['verdict'] === 'defect' ? 'defect' : 'good' }}">{{ $result['verdict'] === 'defect' ? '倾向不良' : '倾向良品' }}</div>
                            <div class="data-line"><span>不良分数</span><strong class="mono">{{ number_format((float) $result['score'], 3) }}</strong></div>
                            <div class="data-line"><span>判定阈值</span><strong class="mono">{{ number_format((float) $result['threshold'], 3) }}</strong></div>
                            <div class="data-line"><span>文件</span><strong class="mono">{{ $filename ?? '' }}</strong></div>
                            <p class="small muted">分数 ≥ 阈值判为"倾向不良"。阈值在验证 LOT 上取"漏判率 = 误报率"的点。</p>
                        </div>
                    </div>
                @endif
            </div>
        </section>
        <section class="panel">
            <div class="panel-head"><h2>模型表现</h2><span class="pill {{ is_numeric($auc) ? 'good' : 'warn' }}">{{ is_numeric($auc) ? '已评估' : '尚未训练' }}</span></div>
            <div class="panel-body">
                @if (! $report)
                    <div class="empty">图像模型还没有训练完成。</div>
                @else
                    <div class="data-line"><span>留出 LOT ROC AUC</span><strong class="mono">{{ is_numeric($auc) ? number_format((float) $auc, 3) : '—' }}{{ is_numeric($aucLow) && is_numeric($aucHigh) ? ' ('.number_format((float) $aucLow, 3).'–'.number_format((float) $aucHigh, 3).')' : '' }}</strong></div>
                    <div class="data-line"><span>阈值下漏判的打码点（留出 LOT 平均）</span><strong class="mono">{{ $percent($miss) }}</strong></div>
                    <div class="data-line"><span>阈值下误报的良品（留出 LOT 平均）</span><strong class="mono">{{ $percent($alarm) }}</strong></div>
                    <div class="data-line"><span>留出 LOT / 图片</span><strong class="mono">{{ data_get($heldOut, 'lots', '—') }} / {{ number_format((int) data_get($heldOut, 'images', 0)) }}</strong></div>
                    <div class="data-line"><span>训练图片</span><strong class="mono">{{ number_format((int) data_get($modelInfo, 'dataset.images', 0)) }}</strong></div>
                    @if (is_array(data_get($heldOut, 'by_family')))
                        <p class="footnote">分型号 ROC AUC：
                            @foreach (data_get($heldOut, 'by_family') as $family)
                                {{ $family['family'] ?? '' }} {{ is_numeric($family['roc_auc'] ?? null) ? number_format((float) $family['roc_auc'], 2) : '—' }}@if (! $loop->last) · @endif
                            @endforeach
                        </p>
                    @endif
                @endif
            </div>
        </section>
    </div>
</main>
</body>
</html>
