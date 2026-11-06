@php
    use App\Support\PublicPath;
    $base = 'review/'.rawurlencode($lot);
    $query = fn (array $changes) => '?'.http_build_query(array_filter(array_merge(['side' => $side, 'hide_dummy' => $hideDummy ? 1 : null, 'page' => $page], $changes), fn ($value) => $value !== null && $value !== ''));
@endphp
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light">
    <title>AFVI AI | {{ $lot }}</title>
    @include('partials.styles')
</head>
<body>
@include('partials.header', ['active' => 'review', 'meta' => 'LOT '.$lot])
<main>
    <div class="title-row">
        <div><h1>{{ $lot }}</h1><p class="subtitle">{{ implode(', ', (array) ($entry['families'] ?? [])) }} · {{ number_format((int) ($entry['scored_points'] ?? 0)) }} 个检出点 · VRS 打码 {{ number_format((int) ($entry['vrs_keyed_points'] ?? 0)) }}</p></div>
        <div style="display:flex; gap:8px; align-items:center">
            <span class="pill {{ $trust['level'] }}" title="{{ $trust['detail'] }}">模型可信度 {{ $trust['label'] }}</span>
            <a class="button" href="{{ PublicPath::url($base.'/csv') }}">下载完整 CSV</a>
        </div>
    </div>

    @if (in_array($trust['level'], ['weak', 'unverified'], true))
        <div class="notice" role="note"><span class="notice-icon">!</span><div><strong>该型号模型可信度{{ $trust['label'] }}：</strong>{{ $trust['detail'] }}。排序仅供参考，请照常全数复核。</div></div>
    @endif
    @if ($entry['lot_in_model_training'] ?? false)
        <div class="notice" role="note"><span class="notice-icon">i</span><div>这个 LOT 参与过模型训练，排序效果会比新 LOT 好，仅用于演示清单格式。</div></div>
    @endif

    <section class="panel">
        <div class="filters">
            <span class="muted">面：</span>
            <a href="{{ PublicPath::url($base).$query(['side' => null, 'page' => 1]) }}">全部</a>
            <a href="{{ PublicPath::url($base).$query(['side' => 'FC', 'page' => 1]) }}">FC</a>
            <a href="{{ PublicPath::url($base).$query(['side' => 'BC', 'page' => 1]) }}">BC</a>
            <span class="muted">·</span>
            <a href="{{ PublicPath::url($base).$query(['hide_dummy' => $hideDummy ? null : 1, 'page' => 1]) }}">{{ $hideDummy ? '显示 Dummy 点' : '隐藏 Dummy 点' }}</a>
            <span class="muted small">当前：{{ $side ?? '全部面' }}{{ $hideDummy ? ' · 不含 Dummy' : '' }} · 第 {{ $page }} 页（每页 200）</span>
        </div>
        @if (count($rows) === 0)
            <div class="empty">没有符合条件的点。</div>
        @else
            <div class="table-wrap"><table>
                <thead><tr><th>排名</th><th>风险分</th><th>前 %</th><th>面</th><th>Strip</th><th>Unit</th><th>Merge</th><th>面积</th><th>光源</th><th>区域</th><th>Dummy</th><th>已有 VRS 码</th><th>缺陷图（ResultData 相对路径）</th></tr></thead>
                <tbody>
                @foreach ($rows as $row)
                    <tr>
                        <td class="mono">{{ $row['rank'] }}</td>
                        <td class="mono score">{{ number_format((float) $row['score'], 3) }}</td>
                        <td class="mono">{{ $row['top_percent'] }}</td>
                        <td class="mono">{{ $row['side'] }}</td>
                        <td class="mono">{{ $row['strip'] }}</td>
                        <td class="mono">{{ $row['unit_idx'] }}</td>
                        <td class="mono">{{ $row['img_merge_idx'] }}</td>
                        <td class="mono">{{ $row['area_total'] }}</td>
                        <td class="mono">LIGHT{{ $row['light_id'] }}</td>
                        <td>{{ $row['sr_metal'] === '2' ? 'Metal' : ($row['sr_metal'] === '1' ? 'SR' : $row['sr_metal']) }}</td>
                        <td>{{ $row['is_dummy'] === 'True' ? '是' : '' }}</td>
                        <td class="mono">{{ $row['vrs_code'] !== '' && $row['vrs_code'] !== '0' ? $row['vrs_code'] : '' }}</td>
                        <td class="key mono">{{ $row['result_data_image'] }}</td>
                    </tr>
                @endforeach
                </tbody>
            </table></div>
        @endif
        <div class="pager">
            <span>@if ($page > 1)<a href="{{ PublicPath::url($base).$query(['page' => $page - 1]) }}">← 上一页</a>@endif</span>
            <span><a href="{{ PublicPath::url('review') }}">返回 LOT 列表</a></span>
            <span>@if ($hasNext)<a href="{{ PublicPath::url($base).$query(['page' => $page + 1]) }}">下一页 →</a>@endif</span>
        </div>
    </section>
    <p class="footnote">模型：{{ $model['version'] ?? '' }} {{ $model['feature_set'] ?? '' }} · 分数只用于复核排序。"已有 VRS 码"是历史复核结果，新 LOT 复核前为空。</p>
</main>
</body>
</html>
