@php
    use App\Support\PublicPath;
    $fmt = fn ($value) => $value === null ? '—' : rtrim(rtrim(number_format((float) $value, 4, '.', ''), '0'), '.');
    $here = fn (array $changes) => PublicPath::url('baseline/model').'?'.http_build_query(array_filter(array_merge(['vision' => $vision['name'], 'model' => $model, 'light' => $light, 'all' => $onlyDifferences ? null : '1'], $changes), fn ($value) => $value !== null && $value !== ''));
    $channelName = ['1' => 'R', '2' => 'G', '3' => 'B'];
    $percent = fn ($value) => is_numeric($value) ? number_format((float) $value * 100, 1).'%' : '—';
@endphp
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light">
    <title>AFVI AI | {{ $model }} 参数对照</title>
    @include('partials.styles')
</head>
<body>
@include('partials.header', ['active' => 'baseline', 'meta' => $vision['name'].' '.$model])
<main>
    <div class="title-row">
        <div><h1>{{ $model }}</h1><p class="subtitle">{{ $vision['name'] }} {{ $vision['side'] }} · LIGHT{{ $light }} · 和这台 Vision 上 {{ $vision['models'] }} 个生产型号的多数值对照</p></div>
        <div style="display:flex; gap:8px; align-items:center">
            <a class="button" href="{{ $paraLink }}">在 /afvi/para 打开</a>
            <a href="{{ PublicPath::url('baseline').'?'.http_build_query(['vision' => $vision['name'], 'light' => $light]) }}">返回参数基准</a>
        </div>
    </div>

    <section class="panel">
        <div class="filters">
            <span class="muted">光源：</span>
            @foreach ($vision['lights'] as $candidate)
                <a href="{{ $here(['light' => $candidate]) }}" class="{{ (string) $candidate === (string) $light ? 'pill good' : '' }}">LIGHT{{ $candidate }}</a>
            @endforeach
            <span class="muted">·</span>
            <a href="{{ $here(['all' => $onlyDifferences ? '1' : null]) }}">{{ $onlyDifferences ? '显示全部参数' : '只看和多数人不一样的' }}</a>
            <span class="muted small">和多数人不一样：{{ $differences }} 处（多数值占比 ≥ {{ (int) round(($index['consensus_share'] ?? 0.6) * 100) }}%、在用型号 ≥ {{ $index['min_consensus_models'] ?? 5 }} 的参数）</span>
        </div>
        @if (count($rows) === 0)
            <div class="empty">{{ $onlyDifferences ? '这个光源下没有和多数人明显不一样的参数。' : '这个型号在该光源下没有参数。' }}</div>
        @else
            <div class="table-wrap"><table>
                <thead><tr><th>节点</th><th>参数</th><th>通道</th><th>本型号</th><th>多数值</th><th>常见值（型号数）</th><th>在用型号</th></tr></thead>
                <tbody>
                @foreach ($rows as $item)
                    @php $row = $item['row']; $stats = $row['stats']; @endphp
                    <tr>
                        <td class="small"><span class="mono">{{ $row['node'] }}</span><br>{{ $row['node_name'] }}</td>
                        <td class="small"><span class="mono">{{ $row['key'] }}</span> {{ $row['name_ko'] ?: $row['name_en'] }}<br><span class="muted">{{ $row['name_en'] }}</span></td>
                        <td class="mono">{{ $row['channel'] ?: '—' }}</td>
                        <td class="mono"><strong style="color:{{ $item['differs'] ? 'var(--red)' : 'inherit' }}">{{ $fmt($item['value']) }}</strong>{{ $item['enabled'] ? '' : '（节点关闭）' }}</td>
                        <td class="mono">{{ $fmt($stats['mode'] ?? null) }} <span class="pill {{ $item['strong'] ? 'strong' : 'weak' }}">{{ $stats ? (int) round($stats['mode_share'] * 100).'%' : '—' }}</span></td>
                        <td class="mono small">{{ implode(' · ', array_map(fn ($pair) => $fmt($pair[0]).'('.$pair[1].')', $stats['top_values'] ?? [])) }}</td>
                        <td class="mono">{{ $row['active'] }} / {{ $row['models'] }}</td>
                    </tr>
                @endforeach
                </tbody>
            </table></div>
        @endif
    </section>

    <section class="panel" style="margin-top:16px">
        <div class="panel-head"><h2>AI 从检出结果里学到的（{{ $family }} · {{ $sideCode }} · LIGHT{{ $light }}）</h2><span class="panel-kicker">与规格项的对应未验证</span></div>
        <div class="panel-body">
            <p class="small muted">这里的"实际下限"是结果数据里各 LOT 观测到的最小缺陷面积（area_max，pixel），代表当时真正生效的下限；它和上面哪个节点、哪个参数对应，目前还没解开（检查代码 → CNODE 未闭合）。面的对应按 FC→TOP、BC→BOTTOM 推定；SR/Metal 的编码也有矛盾，这里只写原值。</p>
            @if (count($evidence) === 0)
                <div class="empty" style="padding:10px 0">这个型号在该面、该光源下没有足够的 LOT 证据。</div>
            @else
                @foreach ($evidence as $entry)
                    <div class="data-line">
                        <span class="mono">{{ $channelName[$entry['channel']] ?? $entry['channel'] }} 通道 · sr_metal={{ $entry['sr_metal'] }}{{ ($entry['code_main'] ?? '') !== '' ? ' · 检查代码 '.$entry['code_main'] : '' }}{{ ($entry['code_meaning_unverified'] ?? false) ? ' · VRS 码含义未确认' : '' }}</span>
                        <strong class="small" style="text-align:left">
                            @if ($entry['kind'] === 'limit_change')
                                实际下限：
                                @foreach ($entry['regimes'] as $regime){{ $regime['limit'] }}（{{ $regime['lots'] }} LOT，{{ $regime['first_date'] }}~{{ $regime['last_date'] }}）@if (! $loop->last) · @endif @endforeach
                                <br>
                                @foreach ($entry['changes'] as $change)
                                    低下限 LOT 里 {{ $change['low_limit'] }}–{{ $change['high_limit'] }} 之间：占点 {{ $percent($change['point_share_below']) }}，打码点 {{ $percent($change['keyed_share_below']) }}，打码率 {{ $percent($change['keyed_rate_below']) }}（以上 {{ $percent($change['keyed_rate_above']) }}）@if (! $loop->last)<br>@endif
                                @endforeach
                            @else
                                草案：{{ ['candidate' => '候选', 'do_not_apply_heldout_exceeds_budget' => '留出 LOT 超预算，不建议', 'unverified_no_heldout' => '没有留出 LOT，未验证'][$entry['recommendation']] ?? $entry['recommendation'] }}
                                · 现观测下限 {{ $entry['observed_min_area'] }}{{ ($entry['recommended_min_area'] ?? '') !== '' ? ' → 建议 '.$entry['recommended_min_area'] : '' }}
                            @endif
                        </strong>
                    </div>
                @endforeach
            @endif
        </div>
    </section>
</main>
</body>
</html>
