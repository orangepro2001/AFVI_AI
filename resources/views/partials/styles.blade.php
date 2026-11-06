<style>
        :root {
            color-scheme: light;
            --ink: #17232a;
            --muted: #65737a;
            --line: #d8e0e1;
            --paper: #f4f7f6;
            --white: #fff;
            --teal: #087f78;
            --teal-soft: #e1f2ef;
            --amber: #a96600;
            --amber-soft: #fff1d2;
            --red: #b84039;
            --red-soft: #fae6e3;
            --blue: #315f83;
            --blue-soft: #e7f0f7;
            --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
            --sans: Inter, "Noto Sans CJK SC", "Microsoft YaHei", sans-serif;
        }
        * { box-sizing: border-box; }
        body { margin: 0; background: var(--paper); color: var(--ink); font: 14px/1.5 var(--sans); }
        header { height: 58px; display:flex; align-items:center; justify-content:space-between; padding:0 28px; background:#fff; border-bottom:1px solid var(--line); }
        .brand { display:flex; align-items:center; gap:12px; font-weight:700; letter-spacing:0; }
        .brand-mark { width:28px; height:28px; display:grid; place-items:center; color:#fff; background:var(--teal); font:700 12px var(--mono); }
        .top-meta { display:flex; align-items:center; gap:18px; color:var(--muted); font-size:12px; }
        .state { display:inline-flex; align-items:center; gap:7px; font-weight:650; color:var(--teal); }
        .state::before { content:""; width:7px; height:7px; border-radius:50%; background:currentColor; }
        main { width:min(1460px, 100%); margin:0 auto; padding:24px 28px 48px; }
        .title-row { display:flex; align-items:flex-end; justify-content:space-between; gap:20px; margin:0 0 20px; }
        h1 { margin:0; font-size:23px; line-height:1.2; letter-spacing:0; }
        .subtitle { margin:6px 0 0; color:var(--muted); }
        .scope-tag { border:1px solid #a7d3cd; color:#076b65; background:var(--teal-soft); padding:6px 10px; font-size:12px; font-weight:650; white-space:nowrap; }
        .notice { display:flex; gap:10px; align-items:flex-start; border:1px solid #e7c77f; background:#fff8e8; padding:11px 14px; margin-bottom:16px; color:#6c4a0b; }
        .notice strong { color:#553801; }
        .notice-icon { font:700 14px var(--mono); }
        .stats { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); border:1px solid var(--line); background:var(--white); }
        .stat { min-width:0; padding:15px 17px; border-right:1px solid var(--line); }
        .stat:last-child { border-right:0; }
        .stat-label { color:var(--muted); font-size:12px; }
        .stat-value { margin-top:4px; font:650 24px/1.15 var(--mono); font-variant-numeric:tabular-nums; }
        .stat-note { margin-top:4px; color:var(--muted); font-size:11px; }
        .panels { display:grid; grid-template-columns:minmax(0,1.1fr) minmax(340px,.9fr); gap:16px; margin-top:16px; }
        .panel { min-width:0; border:1px solid var(--line); background:var(--white); }
        .panel-head { display:flex; align-items:center; justify-content:space-between; gap:16px; min-height:48px; padding:10px 15px; border-bottom:1px solid var(--line); }
        h2 { margin:0; font-size:14px; line-height:1.3; }
        .panel-kicker { color:var(--muted); font:11px var(--mono); }
        .panel-body { padding:15px; }
        .label-bar { display:flex; height:12px; overflow:hidden; background:#edf1f0; }
        .label-positive { background:var(--red); }
        .label-assumed { background:#e4ad46; }
        .label-unobserved { background:#bec9cb; }
        .legend { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-top:13px; }
        .legend-item { display:flex; gap:8px; align-items:flex-start; color:var(--muted); font-size:11px; }
        .swatch { flex:none; width:9px; height:9px; margin-top:4px; background:var(--red); }
        .swatch.assumed { background:#e4ad46; }
        .swatch.unobserved { background:#bec9cb; }
        .legend-item b { display:block; color:var(--ink); font:600 14px var(--mono); }
        .data-line { display:flex; justify-content:space-between; gap:12px; padding:9px 0; border-bottom:1px solid #edf0f0; }
        .data-line:last-child { border-bottom:0; }
        .data-line span:first-child { color:var(--muted); }
        .data-line strong { text-align:right; overflow-wrap:anywhere; font-weight:600; }
        .pill { display:inline-flex; align-items:center; min-height:22px; border:1px solid var(--line); padding:2px 7px; font-size:11px; font-weight:650; white-space:nowrap; }
        .pill.good { border-color:#a7d3cd; color:#076b65; background:var(--teal-soft); }
        .pill.warn { border-color:#e7c77f; color:#805400; background:var(--amber-soft); }
        .pill.bad { border-color:#e5b7b1; color:#9d302a; background:var(--red-soft); }
        .pill.neutral { border-color:#bfd0dc; color:#315f83; background:var(--blue-soft); }
        .metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; margin-top:14px; }
        .metric { padding:10px; background:#f4f7f6; }
        .metric-label { color:var(--muted); font-size:11px; }
        .metric-value { margin-top:3px; font:600 17px var(--mono); }
        .muted { color:var(--muted); }
        .small { font-size:11px; }
        .table-wrap { overflow-x:auto; }
        table { width:100%; border-collapse:collapse; text-align:left; }
        th { padding:9px 12px; background:#f4f7f6; color:var(--muted); font-size:11px; font-weight:650; white-space:nowrap; }
        td { padding:10px 12px; border-top:1px solid #e8eded; vertical-align:top; }
        tbody tr:hover { background:#f8fbfa; }
        .mono { font-family:var(--mono); font-variant-numeric:tabular-nums; }
        .score { color:var(--red); font-weight:700; }
        .evidence { color:var(--muted); font-size:11px; max-width:420px; }
        .key { min-width:185px; font-size:11px; overflow-wrap:anywhere; }
        .empty { padding:24px 16px; color:var(--muted); text-align:center; }
        .footnote { margin:15px 0 0; color:var(--muted); font-size:11px; }
        @media (max-width:900px) { main{padding:20px 16px 36px} header{padding:0 16px} .stats{grid-template-columns:repeat(3,1fr)} .stat:nth-child(3){border-right:0} .stat:nth-child(n+4){border-top:1px solid var(--line)} .panels{grid-template-columns:1fr} }
        @media (max-width:560px) { header{height:52px} .top-meta .refresh{display:none} main{padding:17px 12px 28px} .title-row{align-items:flex-start;flex-direction:column;gap:10px} h1{font-size:20px} .stats{grid-template-columns:repeat(2,minmax(0,1fr))} .stat{padding:12px} .stat:nth-child(2n){border-right:0} .stat:nth-child(3){border-right:1px solid var(--line)} .stat:nth-child(n+3){border-top:1px solid var(--line)} .stat-value{font-size:19px} .legend{grid-template-columns:1fr} .metrics{grid-template-columns:repeat(2,minmax(0,1fr))} .notice{font-size:12px} }
        nav.top-nav { display:flex; gap:4px; }
        nav.top-nav a { color:var(--muted); text-decoration:none; padding:6px 10px; font-size:13px; font-weight:600; border-bottom:2px solid transparent; }
        nav.top-nav a:hover { color:var(--ink); }
        nav.top-nav a.active { color:var(--teal); border-bottom-color:var(--teal); }
        a { color:var(--blue); }
        .pill.strong { border-color:#a7d3cd; color:#076b65; background:var(--teal-soft); }
        .pill.moderate { border-color:#bfd0dc; color:#315f83; background:var(--blue-soft); }
        .pill.weak, .pill.unverified { border-color:#e7c77f; color:#805400; background:var(--amber-soft); }
        .filters { display:flex; flex-wrap:wrap; gap:10px; align-items:center; padding:12px 15px; border-bottom:1px solid var(--line); }
        .filters a, .filters span { font-size:12px; }
        .pager { display:flex; justify-content:space-between; padding:12px 15px; border-top:1px solid var(--line); font-size:12px; }
        .upload { display:grid; gap:12px; }
        .upload input[type=file] { padding:10px; border:1px dashed var(--line); background:#fafcfb; }
        .button { display:inline-flex; align-items:center; justify-content:center; min-height:36px; padding:0 16px; border:0; background:var(--teal); color:#fff; font:600 13px var(--sans); cursor:pointer; text-decoration:none; }
        .verdict { font:700 26px/1.2 var(--sans); }
        .verdict.defect { color:var(--red); }
        .verdict.good { color:var(--teal); }
        .preview { max-width:100%; width:260px; image-rendering:pixelated; border:1px solid var(--line); }
        @media (max-width:760px) { header{height:auto; flex-wrap:wrap; gap:6px; padding:8px 16px} nav.top-nav{order:3; width:100%; overflow-x:auto} }
</style>
