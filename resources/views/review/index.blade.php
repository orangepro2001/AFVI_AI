@php use App\Support\PublicPath; @endphp
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light">
    <title>AFVI AI | 复核清单</title>
    @include('partials.styles')
</head>
<body>
@include('partials.header', ['active' => 'review', 'meta' => isset($summary['generated_at']) ? '清单 '.substr((string) $summary['generated_at'], 0, 19) : '尚无清单'])
<main>
    <div class="title-row">
        <div><h1>复核清单</h1><p class="subtitle">每个 LOT 的已检出点按模型风险分从高到低排列，复核时从上往下看。</p></div>
        <span class="scope-tag">{{ strtoupper((string) ($model['feature_set'] ?? 'model')) }} · {{ $model['file'] ?? '' }}</span>
    </div>

    <div class="notice" role="note">
        <span class="notice-icon">!</span>
        <div><strong>怎么用：</strong>点开 LOT，从排名第 1 的点开始复核；分数只表示"复核员会打不良码"的可能性高低，不是判定结果，也不是设备参数。可信度来自该型号在留出 LOT 上的表现；"弱"或"未验证"的型号请照常全数复核。参与过模型训练的 LOT 排序偏乐观，仅供演示。</div>
    </div>

    <section class="panel">
        <div class="panel-head"><h2>LOT 列表</h2><span class="panel-kicker">{{ count($lots) }} 个 LOT · 新的在前</span></div>
        @if (count($lots) === 0)
            <div class="empty">还没有复核清单。在服务器上运行 afvi_ai.score 生成后会显示在这里。</div>
        @else
            <div class="table-wrap"><table>
                <thead><tr><th>LOT</th><th>型号</th><th>首次检查</th><th>检出点</th><th>VRS 打码点</th><th>模型可信度</th><th>说明</th></tr></thead>
                <tbody>
                @foreach ($lots as $lot)
                    <tr>
                        <td class="mono"><a href="{{ PublicPath::url('review/'.rawurlencode($lot['lot'])) }}">{{ $lot['lot'] }}</a></td>
                        <td class="mono">{{ implode(', ', $lot['families']) }}</td>
                        <td class="mono">{{ $lot['first_inspected_at'] ?? '—' }}</td>
                        <td class="mono">{{ number_format($lot['scored_points']) }}</td>
                        <td class="mono">{{ number_format($lot['vrs_keyed_points']) }}</td>
                        <td><span class="pill {{ $lot['trust']['level'] }}" title="{{ $lot['trust']['detail'] }}">{{ $lot['trust']['label'] }}</span></td>
                        <td class="small muted">{{ $lot['in_training'] ? '参与过训练（排序偏乐观）' : '未参与训练' }}</td>
                    </tr>
                @endforeach
                </tbody>
            </table></div>
        @endif
    </section>
</main>
</body>
</html>
