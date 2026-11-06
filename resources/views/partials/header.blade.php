@php use App\Support\PublicPath; @endphp
<header>
    <div class="brand"><span class="brand-mark">AI</span><span>AFVI <span class="muted">/</span> 检出点复核</span></div>
    <nav class="top-nav" aria-label="页面">
        <a href="{{ PublicPath::url() }}" class="{{ $active === 'dashboard' ? 'active' : '' }}">模型结果</a>
        <a href="{{ PublicPath::url('review') }}" class="{{ $active === 'review' ? 'active' : '' }}">复核清单</a>
        <a href="{{ PublicPath::url('vision') }}" class="{{ $active === 'vision' ? 'active' : '' }}">图片判断</a>
    </nav>
    <div class="top-meta"><span class="state">只读模式</span><span class="refresh">{{ $meta ?? '' }}</span></div>
</header>
