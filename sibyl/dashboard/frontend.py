"""
Dashboard Frontend — serves the single-page React app.

The entire dashboard is a single HTML file with inlined React + Recharts.
No build step, no node_modules, no webpack.  Just CDN imports.

WHY INLINE?
    1. Docker image stays small (no node build stage).
    2. No CORS issues — same origin as the API.
    3. Easy to modify — just edit this file and restart.
    4. One container = agents + API + frontend.

LIBRARIES (loaded from CDN):
    - React 18 (via unpkg)
    - Recharts (via unpkg) — for the portfolio value chart
    - DM Sans + JetBrains Mono (Google Fonts)

VISUAL FRAMEWORK:
    Adapted from the Priscey hero-page design system:
    - Background: #0F0E1A (deep indigo-black)
    - Cards: #1A1930 with #3D3C6B borders
    - Holographic gradient accents (gold → rose → purple → blue)
    - DM Sans body, JetBrains Mono for data/metrics
    - Badge system with color-coded categories

SERVED AT: GET /
"""

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sibyl.ai — Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <script src="https://unpkg.com/react@18/umd/react.production.min.js" crossorigin></script>
    <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js" crossorigin></script>
    <script src="https://unpkg.com/recharts@2.12.7/umd/Recharts.js" crossorigin></script>
    <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
    <style>
        /* ═══ Sibyl.ai Design System (Priscey Hero Framework) ═══ */
        :root {
            --bg: #0F0E1A;
            --card: #1A1930;
            --surface: #252442;
            --border: #3D3C6B;
            --blue: #2563EB;
            --blue-light: #3B82F6;
            --green: #10B981;
            --green-dim: rgba(16,185,129,0.15);
            --amber: #F59E0B;
            --amber-dim: rgba(245,158,11,0.15);
            --red: #EF4444;
            --red-dim: rgba(239,68,68,0.15);
            --purple: #8B5CF6;
            --purple-dim: rgba(139,92,246,0.15);
            --text: #F8F9FB;
            --text-sec: #9F9FAB;
            --text-muted: #6E6D7B;
            --holo-gold: #C8A587;
            --holo-rose: #C29194;
            --holo-purple: #56549D;
            --holo-blue: #3B5E98;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'DM Sans', system-ui, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            -webkit-font-smoothing: antialiased;
        }

        /* ═══ Layout ═══ */
        .sibyl-root { max-width: 1440px; margin: 0 auto; padding: 0 24px; }

        /* ═══ Cards ═══ */
        .card {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 20px;
        }
        .card-surface {
            background: var(--surface);
            border-radius: 10px;
            padding: 12px 16px;
        }

        /* ═══ Holographic Title Gradient ═══ */
        .holo-text {
            background: linear-gradient(90deg, var(--holo-gold), var(--holo-rose), var(--holo-purple), var(--holo-blue));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        /* ═══ Typography ═══ */
        .mono { font-family: 'JetBrains Mono', monospace; }
        .label {
            font-family: 'JetBrains Mono', monospace;
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 2px;
            color: var(--text-muted);
        }
        .metric-value {
            font-family: 'JetBrains Mono', monospace;
            font-size: 24px;
            font-weight: 600;
            color: var(--text);
        }
        .metric-value-sm {
            font-family: 'JetBrains Mono', monospace;
            font-size: 16px;
            font-weight: 500;
        }

        /* ═══ Status Colors ═══ */
        .c-green { color: var(--green); }
        .c-red { color: var(--red); }
        .c-amber { color: var(--amber); }
        .c-blue { color: var(--blue-light); }
        .c-purple { color: var(--purple); }
        .c-muted { color: var(--text-muted); }
        .c-sec { color: var(--text-sec); }

        /* ═══ Badges ═══ */
        .badge {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 1px;
            padding: 3px 10px;
            border-radius: 12px;
        }
        .badge-sge { background: rgba(37,99,235,0.15); color: var(--blue-light); }
        .badge-ace { background: var(--purple-dim); color: var(--purple); }
        .badge-clear { background: var(--green-dim); color: var(--green); }
        .badge-warning { background: var(--amber-dim); color: var(--amber); }
        .badge-caution { background: rgba(249,115,22,0.15); color: #F97316; }
        .badge-critical { background: var(--red-dim); color: var(--red); }
        .badge-arb { background: rgba(6,182,212,0.15); color: #06B6D4; }
        .badge-sentiment { background: rgba(168,85,247,0.15); color: #A855F7; }
        .badge-signal { background: rgba(37,99,235,0.12); color: var(--blue-light); }

        /* ═══ Grids ═══ */
        .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
        .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
        .grid-2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
        .grid-2-1 { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; }
        .grid-1-2 { display: grid; grid-template-columns: 1fr 2fr; gap: 16px; }

        @media (max-width: 1200px) {
            .grid-4 { grid-template-columns: repeat(2, 1fr); }
            .grid-2-1, .grid-1-2 { grid-template-columns: 1fr; }
        }
        @media (max-width: 768px) {
            .grid-4, .grid-3, .grid-2 { grid-template-columns: 1fr; }
        }

        /* ═══ Animations ═══ */
        .pulse { animation: pulse 2s ease-in-out infinite; }
        @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.5; } }

        .holo-glow {
            position: relative;
        }
        .holo-glow::after {
            content: '';
            position: absolute;
            top: -1px; left: -1px; right: -1px; bottom: -1px;
            border-radius: 16px;
            background: linear-gradient(135deg, var(--holo-gold), var(--holo-rose), var(--holo-purple), var(--holo-blue));
            opacity: 0.15;
            z-index: -1;
            filter: blur(8px);
        }

        /* ═══ Scrollbar ═══ */
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: var(--bg); }
        ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

        /* ═══ Tables ═══ */
        .data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
        .data-table thead th {
            font-family: 'JetBrains Mono', monospace;
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            color: var(--text-muted);
            text-align: left;
            padding: 8px 12px;
            border-bottom: 1px solid var(--border);
        }
        .data-table tbody td {
            padding: 10px 12px;
            border-bottom: 1px solid rgba(61,60,107,0.3);
            color: var(--text-sec);
        }
        .data-table tbody tr:hover td { background: rgba(37,36,66,0.5); }
        .data-table .text-right { text-align: right; }
        .data-table .truncate { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

        /* ═══ Tabs ═══ */
        .tab-bar {
            display: flex;
            gap: 4px;
            padding: 4px;
            background: var(--surface);
            border-radius: 12px;
            margin-bottom: 16px;
        }
        .tab {
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1px;
            padding: 8px 16px;
            border-radius: 8px;
            border: none;
            background: transparent;
            color: var(--text-muted);
            cursor: pointer;
            transition: all 0.2s;
        }
        .tab:hover { color: var(--text-sec); }
        .tab.active { background: var(--card); color: var(--text); }

        /* ═══ Status Dot ═══ */
        .status-dot {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 6px;
        }
        .dot-green { background: var(--green); box-shadow: 0 0 6px var(--green); }
        .dot-red { background: var(--red); box-shadow: 0 0 6px var(--red); }
        .dot-amber { background: var(--amber); box-shadow: 0 0 6px var(--amber); }

        /* ═══ Progress Bar ═══ */
        .progress-bar {
            height: 4px;
            background: var(--surface);
            border-radius: 2px;
            overflow: hidden;
        }
        .progress-fill {
            height: 100%;
            border-radius: 2px;
            transition: width 0.5s ease;
        }

        /* ═══ Category Colors ═══ */
        .cat-politics { color: #F87171; }
        .cat-sports { color: #34D399; }
        .cat-culture { color: #A78BFA; }
        .cat-crypto { color: #FBBF24; }
        .cat-climate { color: #2DD4BF; }
        .cat-economics { color: #60A5FA; }
        .cat-mentions { color: #FB923C; }
        .cat-companies { color: #818CF8; }
        .cat-financials { color: #4ADE80; }
        .cat-tech { color: #22D3EE; }

        .section-gap { margin-bottom: 16px; }
    </style>
</head>
<body>
<div id="root"></div>
<script type="text/babel">
// ═══════════════════════════════════════════════════════════════════════
//  Sibyl.ai Dashboard — Holographic Design System
// ═══════════════════════════════════════════════════════════════════════

const { useState, useEffect, useCallback, useMemo } = React;
const { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
        CartesianGrid, PieChart, Pie, Cell, BarChart, Bar } = Recharts;

// ── API Layer ────────────────────────────────────────────────────────
const API = '/api';
const fetcher = async (path) => {
    try {
        const res = await fetch(`${API}${path}`);
        if (!res.ok) return null;
        return await res.json();
    } catch { return null; }
};

// ── Formatting ───────────────────────────────────────────────────────
const fmt = (n, d=2) => n != null ? Number(n).toFixed(d) : '—';
const fmtK = (n) => {
    if (n == null) return '—';
    const v = Number(n);
    if (Math.abs(v) >= 1000) return `$${(v/1000).toFixed(1)}k`;
    return `$${v.toFixed(2)}`;
};
const fmtPct = (n) => n != null ? `${(Number(n) * 100).toFixed(1)}%` : '—';
const fmtPnl = (n) => {
    if (n == null) return '—';
    const v = Number(n);
    return v >= 0 ? `+$${v.toFixed(2)}` : `-$${Math.abs(v).toFixed(2)}`;
};
const pnlClass = (n) => Number(n) >= 0 ? 'c-green' : 'c-red';
const timeSince = (ts) => {
    if (!ts) return '';
    const ms = Date.now() - new Date(ts).getTime();
    const m = Math.floor(ms/60000);
    if (m < 1) return 'just now';
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m/60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h/24)}d ago`;
};

// ── Category Helpers ─────────────────────────────────────────────────
const CATEGORIES = [
    'Politics','Sports','Culture','Crypto','Climate',
    'Economics','Mentions','Companies','Financials','Tech & Science'
];
const catClass = (cat) => {
    if (!cat) return '';
    const key = cat.toLowerCase().replace(/[^a-z]/g,'').slice(0,4);
    const map = {poli:'cat-politics',spor:'cat-sports',cult:'cat-culture',
        cryp:'cat-crypto',clim:'cat-climate',econ:'cat-economics',
        ment:'cat-mentions',comp:'cat-companies',fina:'cat-financials',
        tech:'cat-tech'};
    for (const [k,v] of Object.entries(map)) if (key.startsWith(k)) return v;
    return 'c-sec';
};
const CAT_COLORS = {
    Politics:'#F87171',Sports:'#34D399',Culture:'#A78BFA',Crypto:'#FBBF24',
    Climate:'#2DD4BF',Economics:'#60A5FA',Mentions:'#FB923C',
    Companies:'#818CF8',Financials:'#4ADE80','Tech & Science':'#22D3EE'
};

// ═══ Components ══════════════════════════════════════════════════════

// ── Badge ────────────────────────────────────────────────────────────
const Badge = ({ text, type }) => <span className={`badge badge-${type}`}>{text}</span>;
const EngineBadge = ({ engine }) => <Badge text={engine} type={engine?.toLowerCase() === 'ace' ? 'ace' : 'sge'} />;
const DrawdownBadge = ({ level }) => {
    const map = { CLEAR:'clear', WARNING:'warning', CAUTION:'caution', CRITICAL:'critical' };
    return <Badge text={level || 'CLEAR'} type={map[level] || 'clear'} />;
};
const SignalTypeBadge = ({ type }) => {
    if (!type) return null;
    const t = type.toUpperCase();
    if (t === 'ARBITRAGE') return <Badge text="ARB" type="arb" />;
    if (t === 'SENTIMENT') return <Badge text="SENT" type="sentiment" />;
    return <Badge text={t} type="signal" />;
};

// ── Metric Card ──────────────────────────────────────────────────────
const Metric = ({ label, value, sub, color, icon }) => (
    <div className="card">
        <div className="label" style={{marginBottom:6}}>{label}</div>
        <div className={`metric-value ${color || ''}`}>{value}</div>
        {sub && <div style={{fontSize:12,color:'var(--text-muted)',marginTop:4}}>{sub}</div>}
    </div>
);

// ── System Status Header ─────────────────────────────────────────────
const Header = ({ risk, lastUpdate, portfolio }) => {
    const level = risk?.drawdown_level || 'CLEAR';
    const dotClass = level === 'CLEAR' ? 'dot-green' : level === 'WARNING' ? 'dot-amber' : 'dot-red';
    const statusText = level === 'CLEAR' ? 'System Normal' : level;

    return (
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',padding:'20px 0 16px'}}>
            <div style={{display:'flex',alignItems:'center',gap:16}}>
                <div>
                    <h1 style={{fontSize:28,fontWeight:700,margin:0,lineHeight:1}}>
                        <span className="holo-text">Sibyl</span>
                        <span style={{color:'var(--text-muted)'}}>.ai</span>
                    </h1>
                    <div className="mono" style={{fontSize:10,color:'var(--text-muted)',letterSpacing:2,textTransform:'uppercase',marginTop:4}}>
                        Autonomous Prediction Markets
                    </div>
                </div>
            </div>
            <div style={{display:'flex',alignItems:'center',gap:24}}>
                <div style={{textAlign:'right'}}>
                    <div className="mono" style={{fontSize:11,color:'var(--text-muted)'}}>
                        v0.2.0 • {lastUpdate || '—'}
                    </div>
                    <div style={{fontSize:12,marginTop:2,display:'flex',alignItems:'center',justifyContent:'flex-end',gap:4}}>
                        <span className={`status-dot ${dotClass} ${level !== 'CLEAR' ? 'pulse' : ''}`}></span>
                        <span className={level === 'CLEAR' ? 'c-green' : level === 'WARNING' ? 'c-amber' : 'c-red'} style={{fontSize:12,fontWeight:600}}>
                            {statusText}
                        </span>
                    </div>
                </div>
            </div>
        </div>
    );
};

// ── Portfolio Value Chart ────────────────────────────────────────────
const PortfolioChart = ({ data }) => {
    if (!data || data.length === 0) {
        return (
            <div className="card" style={{textAlign:'center',padding:'48px 20px',color:'var(--text-muted)'}}>
                <div style={{fontSize:32,marginBottom:8}}>&#8203;</div>
                <div className="mono" style={{fontSize:12,letterSpacing:1}}>NO CHART DATA YET</div>
                <div style={{fontSize:13,marginTop:4}}>Positions must close to generate history</div>
            </div>
        );
    }
    return (
        <div className="card">
            <div className="label" style={{marginBottom:12}}>Portfolio Value</div>
            <ResponsiveContainer width="100%" height={280}>
                <AreaChart data={data}>
                    <defs>
                        <linearGradient id="holoGrad" x1="0" y1="0" x2="1" y2="1">
                            <stop offset="0%" stopColor="#C8A587" stopOpacity={0.3}/>
                            <stop offset="33%" stopColor="#C29194" stopOpacity={0.2}/>
                            <stop offset="66%" stopColor="#56549D" stopOpacity={0.15}/>
                            <stop offset="100%" stopColor="#3B5E98" stopOpacity={0.05}/>
                        </linearGradient>
                        <linearGradient id="strokeGrad" x1="0" y1="0" x2="1" y2="0">
                            <stop offset="0%" stopColor="#C8A587"/>
                            <stop offset="33%" stopColor="#C29194"/>
                            <stop offset="66%" stopColor="#56549D"/>
                            <stop offset="100%" stopColor="#3B5E98"/>
                        </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(61,60,107,0.3)" />
                    <XAxis dataKey="date" tick={{fill:'#6E6D7B',fontSize:10,fontFamily:'JetBrains Mono'}} axisLine={{stroke:'var(--border)'}} tickLine={false} />
                    <YAxis tick={{fill:'#6E6D7B',fontSize:10,fontFamily:'JetBrains Mono'}} axisLine={false} tickLine={false} domain={['auto','auto']} />
                    <Tooltip
                        contentStyle={{background:'#1A1930',border:'1px solid #3D3C6B',borderRadius:12,fontFamily:'JetBrains Mono',fontSize:12}}
                        labelStyle={{color:'#9F9FAB',marginBottom:4}}
                        formatter={(v) => [`$${Number(v).toFixed(2)}`, 'Value']}
                    />
                    <Area type="monotone" dataKey="value" stroke="url(#strokeGrad)" fill="url(#holoGrad)" strokeWidth={2.5} dot={false} />
                </AreaChart>
            </ResponsiveContainer>
        </div>
    );
};

// ── Engine Card ──────────────────────────────────────────────────────
const EngineCard = ({ name, data }) => {
    if (!data) return null;
    const cbOk = data.circuit_breaker === 'CLEAR';
    const exposure = data.total_capital > 0 ? (data.deployed_capital / data.total_capital) : 0;

    return (
        <div className="card">
            <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:12}}>
                <EngineBadge engine={name} />
                <Badge text={data.circuit_breaker} type={cbOk ? 'clear' : 'critical'} />
            </div>
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:8}}>
                <div>
                    <div className="label">Capital</div>
                    <div className="metric-value-sm">${fmt(data.total_capital)}</div>
                </div>
                <div>
                    <div className="label">Deployed</div>
                    <div className="metric-value-sm">${fmt(data.deployed_capital)}</div>
                </div>
                <div>
                    <div className="label">Daily P&L</div>
                    <div className={`metric-value-sm ${pnlClass(data.daily_pnl)}`}>{fmtPnl(data.daily_pnl)}</div>
                </div>
            </div>
            <div style={{marginTop:10}}>
                <div style={{display:'flex',justifyContent:'space-between',marginBottom:4}}>
                    <span className="label">Exposure</span>
                    <span className="mono" style={{fontSize:11,color:'var(--text-sec)'}}>{fmtPct(exposure)}</span>
                </div>
                <div className="progress-bar">
                    <div className="progress-fill" style={{
                        width: `${Math.min(exposure * 100, 100)}%`,
                        background: exposure > 0.7 ? 'var(--red)' : exposure > 0.4 ? 'var(--amber)' : 'var(--green)'
                    }}></div>
                </div>
            </div>
        </div>
    );
};

// ── Positions Table ──────────────────────────────────────────────────
const PositionsTable = ({ positions, title, showCategory }) => (
    <div className="card" style={{overflow:'hidden'}}>
        <div className="label" style={{marginBottom:12,padding:'0 4px'}}>{title}</div>
        {positions.length === 0
            ? <div style={{color:'var(--text-muted)',fontSize:13,padding:'16px 4px'}}>No positions</div>
            : <div style={{overflowX:'auto'}}>
                <table className="data-table">
                    <thead>
                        <tr>
                            <th>Market</th>
                            {showCategory && <th>Category</th>}
                            <th>Engine</th>
                            <th>Side</th>
                            <th className="text-right">Size</th>
                            <th className="text-right">Entry</th>
                            <th className="text-right">Current</th>
                            <th className="text-right">P&L</th>
                        </tr>
                    </thead>
                    <tbody>
                        {positions.map((p, i) => (
                            <tr key={p.id || i}>
                                <td className="truncate" title={p.title}>{p.title}</td>
                                {showCategory && <td><span className={catClass(p.category)} style={{fontSize:12}}>{p.category || '—'}</span></td>}
                                <td><EngineBadge engine={p.engine} /></td>
                                <td><span className="mono" style={{fontSize:12}}>{p.side}</span></td>
                                <td className="text-right mono" style={{fontSize:12}}>{p.size}</td>
                                <td className="text-right mono" style={{fontSize:12}}>${fmt(p.entry_price)}</td>
                                <td className="text-right mono" style={{fontSize:12}}>${fmt(p.current_price)}</td>
                                <td className={`text-right mono ${pnlClass(p.pnl)}`} style={{fontSize:12,fontWeight:600}}>{fmtPnl(p.pnl)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        }
    </div>
);

// ── Signal Feed ──────────────────────────────────────────────────────
const SignalsFeed = ({ signals }) => (
    <div className="card" style={{overflow:'hidden'}}>
        <div className="label" style={{marginBottom:12,padding:'0 4px'}}>Signal Feed</div>
        {signals.length === 0
            ? <div style={{color:'var(--text-muted)',fontSize:13,padding:'16px 4px'}}>No signals yet</div>
            : <div style={{maxHeight:380,overflowY:'auto'}}>
                {signals.slice(0, 20).map((s, i) => (
                    <div key={s.id || i} style={{
                        display:'flex',alignItems:'center',justifyContent:'space-between',
                        padding:'10px 8px',borderBottom:'1px solid rgba(61,60,107,0.3)'
                    }}>
                        <div style={{flex:1,minWidth:0}}>
                            <div style={{fontSize:13,color:'var(--text)',whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}} title={s.title}>
                                {s.title}
                            </div>
                            <div style={{display:'flex',gap:8,alignItems:'center',marginTop:3}}>
                                <SignalTypeBadge type={s.signal_type} />
                                <span style={{fontSize:11,color:'var(--text-muted)'}}>{timeSince(s.timestamp)}</span>
                            </div>
                        </div>
                        <div style={{display:'flex',alignItems:'center',gap:10,marginLeft:12,flexShrink:0}}>
                            <div style={{textAlign:'right'}}>
                                <div className="mono" style={{fontSize:12,color:'var(--text-sec)'}}>{fmtPct(s.confidence)}</div>
                                <div className="mono c-green" style={{fontSize:11}}>EV {fmtPct(s.ev_estimate)}</div>
                            </div>
                            {s.routed_to && <EngineBadge engine={s.routed_to} />}
                        </div>
                    </div>
                ))}
            </div>
        }
    </div>
);

// ── Risk Summary Panel ───────────────────────────────────────────────
const RiskPanel = ({ risk }) => {
    if (!risk) return null;
    return (
        <div className="card">
            <div className="label" style={{marginBottom:16}}>Risk Overview</div>
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16}}>
                <div>
                    <div style={{fontSize:11,color:'var(--text-muted)',marginBottom:4}}>Drawdown</div>
                    <div style={{display:'flex',alignItems:'center',gap:8}}>
                        <span className={`metric-value-sm ${risk.drawdown_level === 'CLEAR' ? '' : 'c-red'}`}>
                            {fmtPct(risk.drawdown_pct)}
                        </span>
                        <DrawdownBadge level={risk.drawdown_level} />
                    </div>
                </div>
                <div>
                    <div style={{fontSize:11,color:'var(--text-muted)',marginBottom:4}}>High-Water Mark</div>
                    <div className="metric-value-sm">${fmt(risk.high_water_mark)}</div>
                </div>
                <div>
                    <div style={{fontSize:11,color:'var(--text-muted)',marginBottom:4}}>Win Rate (7d)</div>
                    <div className={`metric-value-sm ${Number(risk.win_rate_7d) >= 0.55 ? 'c-green' : Number(risk.win_rate_7d) >= 0.45 ? 'c-amber' : 'c-red'}`}>
                        {fmtPct(risk.win_rate_7d)}
                    </div>
                </div>
                <div>
                    <div style={{fontSize:11,color:'var(--text-muted)',marginBottom:4}}>Sharpe (30d)</div>
                    <div className={`metric-value-sm ${Number(risk.sharpe_30d) >= 1 ? 'c-green' : Number(risk.sharpe_30d) >= 0 ? 'c-amber' : 'c-red'}`}>
                        {fmt(risk.sharpe_30d)}
                    </div>
                </div>
                <div>
                    <div style={{fontSize:11,color:'var(--text-muted)',marginBottom:4}}>Open Positions</div>
                    <div className="metric-value-sm">{risk.open_positions || 0}</div>
                </div>
                <div>
                    <div style={{fontSize:11,color:'var(--text-muted)',marginBottom:4}}>Total Exposure</div>
                    <div className="metric-value-sm">${fmt(risk.total_exposure)}</div>
                </div>
            </div>
        </div>
    );
};

// ── Category Allocation Mini-Chart ───────────────────────────────────
const CategoryAllocation = ({ positions }) => {
    const catData = useMemo(() => {
        const map = {};
        (positions || []).forEach(p => {
            const cat = p.category || 'Unknown';
            map[cat] = (map[cat] || 0) + Math.abs(Number(p.size || 0) * Number(p.entry_price || 0));
        });
        return Object.entries(map).map(([name, value]) => ({
            name, value: Math.round(value * 100) / 100,
            fill: CAT_COLORS[name] || '#6E6D7B'
        })).sort((a,b) => b.value - a.value);
    }, [positions]);

    if (catData.length === 0) {
        return (
            <div className="card">
                <div className="label" style={{marginBottom:12}}>Category Allocation</div>
                <div style={{color:'var(--text-muted)',fontSize:13}}>No open positions</div>
            </div>
        );
    }

    return (
        <div className="card">
            <div className="label" style={{marginBottom:12}}>Category Allocation</div>
            <ResponsiveContainer width="100%" height={180}>
                <PieChart>
                    <Pie data={catData} dataKey="value" nameKey="name" cx="50%" cy="50%"
                         innerRadius={45} outerRadius={75} paddingAngle={2} strokeWidth={0}>
                        {catData.map((entry, i) => <Cell key={i} fill={entry.fill} />)}
                    </Pie>
                    <Tooltip
                        contentStyle={{background:'#1A1930',border:'1px solid #3D3C6B',borderRadius:10,fontFamily:'JetBrains Mono',fontSize:11}}
                        formatter={(v) => [`$${Number(v).toFixed(2)}`, '']}
                    />
                </PieChart>
            </ResponsiveContainer>
            <div style={{display:'flex',flexWrap:'wrap',gap:'6px 12px',marginTop:4}}>
                {catData.map(c => (
                    <div key={c.name} style={{display:'flex',alignItems:'center',gap:4,fontSize:11}}>
                        <span style={{width:8,height:8,borderRadius:2,background:c.fill,display:'inline-block'}}></span>
                        <span style={{color:'var(--text-sec)'}}>{c.name}</span>
                    </div>
                ))}
            </div>
        </div>
    );
};

// ── Activity Log ─────────────────────────────────────────────────────
const ActivityLog = ({ positions, signals }) => {
    const events = useMemo(() => {
        const items = [];
        (positions || []).forEach(p => {
            items.push({
                ts: p.opened_at,
                type: 'OPEN',
                text: `Opened ${p.side} on ${p.title}`,
                detail: `${p.engine} • $${fmt(p.entry_price)} • ${p.size} contracts`,
                color: 'c-blue'
            });
        });
        // Recent closed positions
        return items.sort((a,b) => new Date(b.ts) - new Date(a.ts)).slice(0, 10);
    }, [positions]);

    return (
        <div className="card">
            <div className="label" style={{marginBottom:12}}>Recent Activity</div>
            {events.length === 0
                ? <div style={{color:'var(--text-muted)',fontSize:13}}>No activity yet</div>
                : <div style={{maxHeight:300,overflowY:'auto'}}>
                    {events.map((ev, i) => (
                        <div key={i} style={{padding:'8px 0',borderBottom:'1px solid rgba(61,60,107,0.2)',display:'flex',gap:10,alignItems:'flex-start'}}>
                            <div style={{fontSize:10,color:'var(--text-muted)',minWidth:50,paddingTop:2}} className="mono">
                                {timeSince(ev.ts)}
                            </div>
                            <div style={{flex:1}}>
                                <div style={{fontSize:13,color:'var(--text)'}}>{ev.text}</div>
                                <div style={{fontSize:11,color:'var(--text-muted)',marginTop:2}}>{ev.detail}</div>
                            </div>
                        </div>
                    ))}
                </div>
            }
        </div>
    );
};

// ═══ Main App ════════════════════════════════════════════════════════

const App = () => {
    const [portfolio, setPortfolio] = useState(null);
    const [positions, setPositions] = useState([]);
    const [history, setHistory] = useState([]);
    const [signals, setSignals] = useState([]);
    const [risk, setRisk] = useState(null);
    const [chart, setChart] = useState([]);
    const [lastUpdate, setLastUpdate] = useState(null);
    const [activeTab, setActiveTab] = useState('positions');

    const refresh = useCallback(async () => {
        const [p, pos, hist, sig, r, c] = await Promise.all([
            fetcher('/portfolio'),
            fetcher('/positions'),
            fetcher('/positions/history'),
            fetcher('/signals'),
            fetcher('/risk'),
            fetcher('/chart/portfolio'),
        ]);
        if (p) setPortfolio(p);
        if (pos) setPositions(pos);
        if (hist) setHistory(hist);
        if (sig) setSignals(sig);
        if (r) setRisk(r);
        if (c) setChart(c);
        setLastUpdate(new Date().toLocaleTimeString());
    }, []);

    useEffect(() => {
        refresh();
        const iv = setInterval(refresh, 10000);
        return () => clearInterval(iv);
    }, [refresh]);

    const dailyPnl = portfolio?.daily_pnl || 0;
    const totalBalance = portfolio?.total_balance || 0;

    return (
        <div className="sibyl-root" style={{paddingBottom:40}}>
            {/* ── Header ── */}
            <Header risk={risk} lastUpdate={lastUpdate} portfolio={portfolio} />

            {/* ── Top Metrics Row ── */}
            <div className="grid-4 section-gap">
                <div className="card holo-glow">
                    <div className="label" style={{marginBottom:6}}>Portfolio Value</div>
                    <div className="metric-value">${fmt(totalBalance)}</div>
                    <div style={{fontSize:11,color:'var(--text-muted)',marginTop:4}}>
                        Reserve: ${fmt(portfolio?.cash_reserve)}
                    </div>
                </div>
                <Metric
                    label="Daily P&L"
                    value={fmtPnl(dailyPnl)}
                    color={pnlClass(dailyPnl)}
                    sub={totalBalance > 0 ? `${((dailyPnl / totalBalance) * 100).toFixed(2)}% of portfolio` : ''}
                />
                <Metric
                    label="Win Rate (7d)"
                    value={fmtPct(risk?.win_rate_7d)}
                    sub={`Sharpe: ${fmt(risk?.sharpe_30d)}`}
                />
                <Metric
                    label="Drawdown"
                    value={fmtPct(risk?.drawdown_pct)}
                    color={risk?.drawdown_level === 'CLEAR' ? '' : 'c-red'}
                    sub={risk ? React.createElement(DrawdownBadge, {level: risk.drawdown_level}) : ''}
                />
            </div>

            {/* ── Engine Cards ── */}
            {portfolio?.engines && (
                <div className="grid-2 section-gap">
                    {Object.entries(portfolio.engines).map(([name, e]) => (
                        <EngineCard key={name} name={name} data={e} />
                    ))}
                </div>
            )}

            {/* ── Chart + Risk Panel ── */}
            <div className="grid-2-1 section-gap">
                <PortfolioChart data={chart} />
                <RiskPanel risk={risk} />
            </div>

            {/* ── Tab Navigation ── */}
            <div className="tab-bar">
                {[
                    {id:'positions', label:'Open Positions'},
                    {id:'signals', label:'Signal Feed'},
                    {id:'history', label:'History'},
                ].map(t => (
                    <button key={t.id} className={`tab ${activeTab === t.id ? 'active' : ''}`}
                            onClick={() => setActiveTab(t.id)}>
                        {t.label}
                        {t.id === 'positions' && positions.length > 0 &&
                            <span className="mono" style={{marginLeft:6,fontSize:10,opacity:0.6}}>{positions.length}</span>}
                        {t.id === 'signals' && signals.length > 0 &&
                            <span className="mono" style={{marginLeft:6,fontSize:10,opacity:0.6}}>{signals.length}</span>}
                    </button>
                ))}
            </div>

            {/* ── Tab Content ── */}
            <div className="grid-2-1 section-gap">
                <div>
                    {activeTab === 'positions' && <PositionsTable positions={positions} title="Open Positions" showCategory={true} />}
                    {activeTab === 'signals' && <SignalsFeed signals={signals} />}
                    {activeTab === 'history' && <PositionsTable positions={history} title="Recent Closed Positions" showCategory={false} />}
                </div>
                <div style={{display:'flex',flexDirection:'column',gap:16}}>
                    <CategoryAllocation positions={positions} />
                    <ActivityLog positions={positions} signals={signals} />
                </div>
            </div>

            {/* ── Footer ── */}
            <div style={{textAlign:'center',padding:'24px 0',borderTop:'1px solid var(--border)',marginTop:24}}>
                <span className="mono" style={{fontSize:10,color:'var(--text-muted)',letterSpacing:2,textTransform:'uppercase'}}>
                    Sibyl.ai v0.2.0 — Autonomous Prediction Market Investing
                </span>
            </div>
        </div>
    );
};

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);
</script>
</body>
</html>"""
