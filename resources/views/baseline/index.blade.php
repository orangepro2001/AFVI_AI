@php
    use App\Support\PublicPath;
    $fmt = fn ($value) => $value === null ? '—' : rtrim(rtrim(number_format((float) $value, 4, '.', ''), '0'), '.');
    $link = fn (array $changes) => PublicPath::url('baseline').'?'.http_build_query(array_filter(array_merge(['vision' => $vision['name'] ?? null, 'light' => $light, 'q' => $query, 'view' => $viewMode, 'tag' => $tag], $changes), fn ($value) => $value !== null && $value !== ''));
    $share = (float) ($index['consensus_share'] ?? 0.6);
@endphp
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light">
    <title>AFVI AI | 参数基准</title>
    @include('partials.styles')
</head>
<body>
@include('partials.header', ['active' => 'baseline', 'meta' => isset($index['generated_at']) ? '规格读取 '.substr((string) $index['generated_at'], 0, 16) : '尚无基准'])
<main>
    <div class="title-row">
        <div><h1>参数基准</h1><p class="subtitle">同一台 Vision 上所有生产型号的 InspectionSpec 怎么设：每个节点、参数、通道有多少型号在用，最常见的值是多少。</p></div>
        <span class="scope-tag">READ ONLY · {{ $vision['name'] ?? '' }} {{ $vision['side'] ?? '' }} · {{ $vision['models'] ?? 0 }} 个型号</span>
    </div>

    <div class="notice" role="note">
        <span class="notice-icon">i</span>
        <div><strong>怎么看：</strong>"多数值"是启用该节点、且该通道参与检查（值不为 0）的型号里最常见的值，后面是占比；占比 ≥ {{ (int) round($share * 100) }}% 视为大家有共识。"不用此通道"是启用节点但该通道填 0 的型号比例。复制件、备份、TEST 型号不计入。点型号名可以看这个型号哪里和多数人不一样。</div>
    </div>

    @if (! $vision)
        <section class="panel"><div class="empty">还没有参数基准。在服务器上运行 afvi_ai.spec_baseline 生成。</div></section>
    @else
        <section class="panel">
            <div class="filters">
                <span class="muted">Vision：</span>
                @foreach ($index['visions'] as $candidate)
                    <a href="{{ $link(['vision' => $candidate['name'], 'page' => null]) }}" class="{{ $candidate['name'] === $vision['name'] ? 'pill good' : '' }}">{{ $candidate['name'] }} {{ $candidate['side'] }}</a>
                @endforeach
                <span class="muted">· 光源：</span>
                @foreach ($vision['lights'] as $candidate)
                    <a href="{{ $link(['light' => $candidate, 'page' => null]) }}" class="{{ (string) $candidate === (string) $light ? 'pill good' : '' }}">LIGHT{{ $candidate }}</a>
                @endforeach
                <span class="muted">· 显示：</span>
                <a href="{{ $link(['view' => 'all', 'page' => null]) }}" class="{{ $viewMode === 'all' ? 'pill good' : '' }}">全部</a>
                <a href="{{ $link(['view' => 'strong', 'page' => null]) }}" class="{{ $viewMode === 'strong' ? 'pill good' : '' }}">有共识</a>
                <a href="{{ $link(['view' => 'split', 'page' => null]) }}" class="{{ $viewMode === 'split' ? 'pill good' : '' }}">意见分散</a>
                <form method="get" action="{{ PublicPath::url('baseline') }}" style="display:flex; gap:6px; margin-left:auto">
                    <input type="hidden" name="vision" value="{{ $vision['name'] }}"><input type="hidden" name="light" value="{{ $light }}"><input type="hidden" name="view" value="{{ $viewMode }}">
                    <input type="search" name="q" value="{{ $query }}" placeholder="参数键 / 名称 / 节点" style="padding:6px 8px; border:1px solid var(--line)">
                    <button class="button" type="submit" style="min-height:30px">搜索</button>
                </form>
            </div>
            <div class="filters">
                <span class="muted">型号对照：</span>
                <form method="get" action="{{ PublicPath::url('baseline/model') }}" style="display:flex; gap:6px">
                    <input type="hidden" name="vision" value="{{ $vision['name'] }}"><input type="hidden" name="light" value="{{ $light }}">
                    <select name="model" style="padding:6px 8px; border:1px solid var(--line)">
                        @foreach ($models as $candidate)<option value="{{ $candidate }}">{{ $candidate }}</option>@endforeach
                    </select>
                    <button class="button" type="submit" style="min-height:30px">看这个型号和多数人的差别</button>
                </form>
                <span class="muted small">共 {{ number_format($total) }} 行 · 第 {{ $page }} 页</span>
            </div>
            @if (count($rows) === 0)
                <div class="empty">没有符合条件的参数。</div>
            @else
                <div class="table-wrap"><table>
                    <thead><tr><th>节点</th><th>参数</th><th>通道</th><th>在用型号</th><th>多数值</th><th>中位 / 10–90%</th><th>范围</th><th>常见值（型号数）</th><th>不用此通道</th><th>DEFAULT_SPEC</th></tr></thead>
                    <tbody>
                    @foreach ($rows as $row)
                        @php $stats = $row['stats']; $strong = $stats && $row['active'] >= ($index['min_consensus_models'] ?? 5) && $stats['mode_share'] >= $share; @endphp
                        <tr>
                            <td class="small"><span class="mono">{{ $row['node'] }}</span><br>{{ $row['node_name'] }}{{ $row['in_tree'] ? '' : '（不在节点树）' }}</td>
                            <td class="small"><span class="mono">{{ $row['key'] }}</span> {{ $row['name_ko'] ?: $row['name_en'] }}<br><span class="muted">{{ $row['name_en'] }} · {{ $row['tag'] }}</span></td>
                            <td class="mono">{{ $row['channel'] ?: '—' }}</td>
                            <td class="mono">{{ $row['active'] }} / {{ $row['models'] }}</td>
                            <td class="mono"><strong>{{ $fmt($stats['mode'] ?? null) }}</strong> <span class="pill {{ $strong ? 'strong' : 'weak' }}">{{ $stats ? (int) round($stats['mode_share'] * 100).'%' : '—' }}</span></td>
                            <td class="mono">{{ $fmt($stats['median'] ?? null) }} / {{ $fmt($stats['p10'] ?? null) }}–{{ $fmt($stats['p90'] ?? null) }}</td>
                            <td class="mono">{{ $fmt($stats['min'] ?? null) }}–{{ $fmt($stats['max'] ?? null) }}</td>
                            <td class="mono small">{{ implode(' · ', array_map(fn ($pair) => $fmt($pair[0]).'('.$pair[1].')', $stats['top_values'] ?? [])) }}</td>
                            <td class="mono">{{ $row['inactive_share'] === null ? '—' : (int) round($row['inactive_share'] * 100).'%' }}</td>
                            <td class="mono">{{ $fmt($row['default']) }}</td>
                        </tr>
                    @endforeach
                    </tbody>
                </table></div>
            @endif
            <div class="pager">
                <span>@if ($page > 1)<a href="{{ $link(['page' => $page - 1]) }}">← 上一页</a>@endif</span>
                <span></span>
                <span>@if ($hasNext)<a href="{{ $link(['page' => $page + 1]) }}">下一页 →</a>@endif</span>
            </div>
        </section>
    @endif
</main>
</body>
</html>
