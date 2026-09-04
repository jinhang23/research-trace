"""Research Trace reader with vendored markdown-it and Dagre components."""

#: 页面里所有指回本服务的地址都从这个占位符派生。挂在域名根时它渲染成空串，
#: 于是 BASE + '/api/x' 还是 '/api/x' —— 根部署的行为一个字节都没变。
BASE_PLACEHOLDER = "__TRACE_BASE__"

INDEX_HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="theme-color" content="#fbfaf8">
<title>Research Trace</title>
<style>
:root {
  --bg: #edf3f4;
  --bg-deep: #e6eeef;
  --glass: rgba(255, 255, 255, .72);
  --glass-strong: rgba(255, 255, 255, .9);
  --glass-muted: rgba(247, 250, 250, .68);
  --ink: #142421;
  --ink-soft: #344944;
  --muted: #667a75;
  --line: rgba(54, 83, 76, .13);
  --line-light: rgba(255, 255, 255, .82);
  --accent: #147765;
  --accent-strong: #0d5d50;
  --accent-soft: rgba(20, 119, 101, .1);
  /* v1 的状态三色。每种同时给颜色和线型，所以不靠颜色也读得出来。 */
  --st-done: #2f7d4f;
  --st-wip: #ab7716;
  --st-note: #a8453a;
  --accent-softer: rgba(20, 119, 101, .055);
  --blue-soft: rgba(85, 120, 210, .12);
  --violet-soft: rgba(132, 102, 190, .1);
  --warn: #9a5a18;
  --warn-soft: rgba(154, 90, 24, .1);
  --danger: #a23f4c;
  --danger-soft: rgba(162, 63, 76, .09);
  --success: #147765;
  --shadow-sm: 0 7px 24px rgba(30, 55, 49, .07);
  --shadow-md: 0 18px 48px rgba(30, 55, 49, .1);
  --shadow-lg: 0 30px 80px rgba(25, 47, 42, .16);
  --radius-sm: 12px;
  --radius-md: 18px;
  --radius-lg: 26px;
  --header-offset: 96px;
}

* { box-sizing: border-box; }
html { min-width: 320px; scroll-padding-top: var(--header-offset); }
body {
  min-height: 100vh;
  margin: 0;
  overflow-x: hidden;
  color: var(--ink);
  background:
    radial-gradient(circle at 8% -4%, rgba(105, 207, 174, .26), transparent 33rem),
    radial-gradient(circle at 96% 2%, rgba(116, 145, 224, .2), transparent 34rem),
    radial-gradient(circle at 68% 96%, rgba(170, 132, 213, .12), transparent 32rem),
    linear-gradient(145deg, #f2f7f6 0%, var(--bg) 46%, var(--bg-deep) 100%);
  background-attachment: fixed;
  font: 15px/1.6 "Plus Jakarta Sans", Inter, ui-sans-serif, system-ui, -apple-system,
    BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  -webkit-font-smoothing: antialiased;
}

body::before,
body::after {
  position: fixed;
  z-index: -1;
  width: 24rem;
  height: 24rem;
  border-radius: 999px;
  content: "";
  pointer-events: none;
  filter: blur(12px);
}
body::before {
  top: 18%;
  left: -16rem;
  background: rgba(79, 187, 160, .12);
}
body::after {
  right: -15rem;
  bottom: 4%;
  background: rgba(98, 124, 204, .11);
}

button,
input,
textarea,
select { font: inherit; }
button,
summary,
a {
  -webkit-tap-highlight-color: transparent;
  touch-action: manipulation;
}
button { cursor: pointer; }
a { color: var(--accent-strong); text-underline-offset: 3px; }
a:hover { color: var(--accent); }
::selection { color: var(--ink); background: rgba(89, 201, 168, .3); }
[hidden] { display: none !important; }

:focus-visible {
  outline: 3px solid rgba(20, 119, 101, .35);
  outline-offset: 3px;
}

.skip-link {
  position: fixed;
  z-index: 100;
  top: 8px;
  left: 12px;
  padding: 10px 14px;
  border-radius: 10px;
  color: #fff;
  background: var(--accent-strong);
  transform: translateY(-140%);
}
.skip-link:focus { transform: translateY(0); }
.sr-only {
  position: absolute !important;
  width: 1px !important;
  height: 1px !important;
  padding: 0 !important;
  margin: -1px !important;
  overflow: hidden !important;
  clip: rect(0, 0, 0, 0) !important;
  white-space: nowrap !important;
  border: 0 !important;
}

.icon {
  width: 18px;
  height: 18px;
  flex: 0 0 auto;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.top-shell {
  position: sticky;
  z-index: 50;
  top: 0;
  padding: 14px clamp(14px, 2.4vw, 32px) 0;
}
.top {
  display: grid;
  grid-template-columns: minmax(196px, auto) minmax(240px, 680px) minmax(120px, auto);
  align-items: center;
  gap: 18px;
  width: min(1500px, 100%);
  min-height: 66px;
  margin: 0 auto;
  padding: 9px 11px 9px 16px;
  border: 1px solid var(--line-light);
  border-radius: 19px;
  background: rgba(255, 255, 255, .74);
  box-shadow: 0 12px 36px rgba(31, 57, 51, .09);
  backdrop-filter: blur(20px) saturate(145%);
  -webkit-backdrop-filter: blur(20px) saturate(145%);
}
.brand {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 11px;
  color: var(--ink);
  text-decoration: none;
}
.brand-mark {
  display: grid;
  width: 40px;
  height: 40px;
  flex: 0 0 auto;
  place-items: center;
  border: 1px solid rgba(255, 255, 255, .86);
  border-radius: 13px;
  color: #fff;
  background: linear-gradient(145deg, #20947e, #0e6255);
  box-shadow: 0 8px 18px rgba(14, 98, 85, .24), inset 0 1px rgba(255, 255, 255, .28);
}
.brand-mark .icon { width: 22px; height: 22px; stroke-width: 1.9; }
.brand-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  line-height: 1.2;
}
.brand-copy strong { font-size: 15px; letter-spacing: -.01em; }
.brand-copy small {
  margin-top: 3px;
  color: var(--muted);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .13em;
  text-transform: uppercase;
}

.search-shell {
  position: relative;
  display: flex;
  align-items: center;
  min-width: 0;
}
.search-shell > .icon {
  position: absolute;
  z-index: 1;
  left: 15px;
  color: var(--muted);
  pointer-events: none;
}
.search-shell input {
  width: 100%;
  min-height: 46px;
  padding: 10px 44px;
  border: 1px solid rgba(54, 83, 76, .1);
  border-radius: 14px;
  outline: 0;
  color: var(--ink);
  background: rgba(241, 246, 245, .72);
  box-shadow: inset 0 1px 2px rgba(30, 55, 49, .035);
  transition: border-color .2s ease, background .2s ease, box-shadow .2s ease;
}
.search-shell input::placeholder { color: #74847f; }
.search-shell input:hover { background: rgba(247, 250, 250, .9); }
.search-shell input:focus {
  border-color: rgba(20, 119, 101, .35);
  background: rgba(255, 255, 255, .94);
  box-shadow: 0 0 0 4px rgba(20, 119, 101, .08);
}
.search-key {
  position: absolute;
  right: 11px;
  display: inline-grid;
  min-width: 25px;
  height: 24px;
  place-items: center;
  border: 1px solid var(--line);
  border-radius: 7px;
  color: var(--muted);
  background: rgba(255, 255, 255, .68);
  font-size: 11px;
  pointer-events: none;
}
.search-results {
  position: absolute;
  z-index: 60;
  top: calc(100% + 10px);
  right: 0;
  left: 0;
  max-height: min(65vh, 560px);
  overflow: auto;
  border: 1px solid var(--line-light);
  border-radius: 17px;
  background: rgba(255, 255, 255, .94);
  box-shadow: var(--shadow-lg);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
}
.hit {
  padding: 14px 16px;
  border-bottom: 1px solid var(--line);
}
.hit:last-child { border-bottom: 0; }
.hit b { display: block; margin-bottom: 3px; color: var(--ink); }
.hit div {
  display: -webkit-box;
  overflow: hidden;
  color: var(--ink-soft);
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.hit small { display: block; margin-top: 6px; color: var(--muted); }
/* 搜索命中是可点击的：跨项目搜到的记录必须能直接跳过去，否则数据在但够不着。 */
button.hit {
  display: block;
  width: 100%;
  border: 0;
  border-bottom: 1px solid var(--line);
  border-radius: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  font: inherit;
  cursor: pointer;
}
button.hit:hover,
button.hit:focus-visible { background: rgba(238, 243, 242, .72); }
.hit-project { color: var(--accent-strong); font-weight: 600; }

.top-actions {
  display: flex;
  align-items: center;
  justify-self: end;
  gap: 8px;
}
.account-btn {
  display: inline-flex;
  min-height: 46px;
  align-items: center;
  justify-content: center;
  justify-self: end;
  gap: 8px;
  padding: 9px 14px;
  border: 1px solid rgba(54, 83, 76, .12);
  border-radius: 14px;
  color: var(--ink-soft);
  background: rgba(255, 255, 255, .66);
  transition: color .2s ease, border-color .2s ease, background .2s ease, box-shadow .2s ease;
}
.account-btn:hover {
  border-color: rgba(20, 119, 101, .2);
  color: var(--accent-strong);
  background: rgba(255, 255, 255, .96);
  box-shadow: var(--shadow-sm);
}

.layout {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: 24px;
  width: min(1500px, calc(100% - clamp(28px, 4.8vw, 64px)));
  min-height: calc(100vh - 96px);
  margin: 0 auto;
}
.sidebar {
  position: sticky;
  top: var(--header-offset);
  align-self: start;
  max-height: calc(100vh - 112px);
  margin-top: 18px;
  padding: 17px;
  overflow: auto;
  border: 1px solid var(--line-light);
  border-radius: 22px;
  background: rgba(255, 255, 255, .58);
  box-shadow: var(--shadow-sm);
  backdrop-filter: blur(18px) saturate(135%);
  -webkit-backdrop-filter: blur(18px) saturate(135%);
}
.sidebar-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 2px 4px 15px;
  border-bottom: 1px solid var(--line);
}
.sidebar-head strong { font-size: 15px; letter-spacing: -.01em; }
.sidebar-head span { color: var(--muted); font-size: 12px; }
.nav-section { padding-top: 17px; }
.section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 20px;
  margin: 0 7px 8px;
  color: var(--muted);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .14em;
  text-transform: uppercase;
}
.section-title .section-count {
  font-size: 10px;
  letter-spacing: 0;
}
.nav-list { min-width: 0; }
.project,
.chapter {
  display: grid;
  width: 100%;
  min-height: 52px;
  grid-template-columns: 34px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  margin: 3px 0;
  padding: 7px 9px;
  border: 1px solid transparent;
  border-radius: 13px;
  color: var(--ink-soft);
  background: transparent;
  text-align: left;
  transition: color .18s ease, border-color .18s ease, background .18s ease, box-shadow .18s ease;
}
.project:hover,
.chapter:hover {
  color: var(--ink);
  background: rgba(255, 255, 255, .66);
}
.project.on,
.chapter.on {
  border-color: rgba(255, 255, 255, .9);
  color: var(--accent-strong);
  background: rgba(255, 255, 255, .88);
  box-shadow: 0 7px 20px rgba(31, 66, 57, .08);
}
.nav-icon {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border: 1px solid rgba(20, 119, 101, .08);
  border-radius: 10px;
  color: var(--accent);
  background: var(--accent-softer);
}
.chapter .nav-icon {
  color: #6674a6;
  background: rgba(95, 111, 172, .07);
  border-color: rgba(95, 111, 172, .08);
}
.project.on .nav-icon,
.chapter.on .nav-icon { background: var(--accent-soft); }
.nav-copy { min-width: 0; }
.nav-copy strong {
  display: block;
  overflow: hidden;
  font-size: 13px;
  font-weight: 650;
  line-height: 1.3;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.nav-copy small {
  display: block;
  overflow: hidden;
  margin-top: 2px;
  color: var(--muted);
  font-size: 10px;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.count {
  display: inline-grid;
  min-width: 24px;
  height: 24px;
  place-items: center;
  padding: 0 6px;
  border-radius: 8px;
  color: var(--muted);
  background: rgba(102, 122, 117, .08);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.side-add {
  display: flex;
  width: 100%;
  min-height: 46px;
  align-items: center;
  justify-content: center;
  gap: 7px;
  margin: 9px 0 0;
  border: 1px dashed rgba(20, 119, 101, .28);
  border-radius: 13px;
  color: var(--accent-strong);
  background: rgba(255, 255, 255, .28);
  transition: border-color .2s ease, background .2s ease;
}
.side-add:hover {
  border-color: rgba(20, 119, 101, .48);
  background: rgba(255, 255, 255, .72);
}

main {
  width: 100%;
  min-width: 0;
  max-width: 1160px;
  margin-right: auto;
  padding: 28px 4px 88px 0;
}
.main-stack { display: grid; gap: 18px; }
.glass,
.card,
.section-card,
.node-card {
  border: 1px solid var(--line-light);
  background: var(--glass);
  box-shadow: var(--shadow-sm);
  backdrop-filter: blur(18px) saturate(135%);
  -webkit-backdrop-filter: blur(18px) saturate(135%);
}
.project-hero {
  position: relative;
  min-height: 188px;
  padding: clamp(24px, 4vw, 38px);
  overflow: hidden;
  border: 1px solid var(--line-light);
  border-radius: var(--radius-lg);
  background:
    linear-gradient(118deg, rgba(255, 255, 255, .88), rgba(255, 255, 255, .55)),
    linear-gradient(135deg, var(--accent-soft), var(--blue-soft));
  box-shadow: var(--shadow-md);
  backdrop-filter: blur(20px) saturate(145%);
  -webkit-backdrop-filter: blur(20px) saturate(145%);
}
.project-hero::after {
  position: absolute;
  top: -8rem;
  right: -5rem;
  width: 21rem;
  height: 21rem;
  border-radius: 50%;
  background:
    radial-gradient(circle at 38% 38%, rgba(255, 255, 255, .72), transparent 18%),
    linear-gradient(145deg, rgba(57, 170, 143, .24), rgba(92, 117, 199, .19));
  content: "";
  filter: blur(1px);
  opacity: .72;
  pointer-events: none;
}
.project-hero > * { position: relative; z-index: 1; }
.eyebrow {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 10px;
  color: var(--accent-strong);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .15em;
  text-transform: uppercase;
}
.status-dot {
  width: 8px;
  height: 8px;
  border: 2px solid rgba(255, 255, 255, .8);
  border-radius: 999px;
  background: #30a68d;
  box-shadow: 0 0 0 4px rgba(48, 166, 141, .1);
}
.project-hero h1,
.welcome-card h1 {
  max-width: 780px;
  margin: 0;
  color: #10231f;
  font-size: clamp(31px, 5vw, 52px);
  font-weight: 720;
  line-height: 1.08;
  letter-spacing: -.045em;
  overflow-wrap: anywhere;
}
.project-subtitle {
  max-width: 720px;
  margin: 13px 0 0;
  color: var(--ink-soft);
  font-size: 15px;
}
.workspace-key {
  display: inline-flex;
  max-width: min(620px, 100%);
  align-items: center;
  gap: 7px;
  margin-top: 21px;
  padding: 7px 10px;
  overflow-wrap: anywhere;
  border: 1px solid rgba(54, 83, 76, .09);
  border-radius: 10px;
  color: var(--muted);
  background: rgba(255, 255, 255, .42);
  font: 11px/1.45 ui-monospace, SFMono-Regular, Consolas, monospace;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.metric {
  min-width: 0;
  padding: 18px 19px;
  border: 1px solid var(--line-light);
  border-radius: var(--radius-md);
  background: rgba(255, 255, 255, .62);
  box-shadow: var(--shadow-sm);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
}
.metric-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.metric-icon {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: 11px;
  color: var(--accent);
  background: var(--accent-soft);
}
.metric:nth-child(2) .metric-icon { color: #5f6fa5; background: rgba(95, 111, 165, .1); }
.metric:nth-child(3) .metric-icon { color: #8b5f9d; background: rgba(139, 95, 157, .1); }
.metric:nth-child(4) .metric-icon { color: #98702c; background: rgba(152, 112, 44, .1); }
.metric-value {
  margin-top: 13px;
  color: var(--ink);
  font-size: 25px;
  font-weight: 720;
  line-height: 1;
  letter-spacing: -.03em;
  font-variant-numeric: tabular-nums;
}
.metric-label { margin-top: 6px; color: var(--muted); font-size: 11px; }

.section-card,
.card {
  margin: 0;
  padding: clamp(19px, 3vw, 27px);
  border-radius: var(--radius-md);
}
.section-card.feature {
  background:
    linear-gradient(145deg, rgba(255, 255, 255, .84), rgba(250, 253, 252, .62)),
    var(--glass);
}
.section-head,
.chapter-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}
.section-heading {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 12px;
}
.section-icon {
  display: grid;
  width: 40px;
  height: 40px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 13px;
  color: var(--accent);
  background: var(--accent-soft);
}
.section-heading .eyebrow { margin-bottom: 3px; }
.section-card h2,
.card h2,
.section-card h3 {
  margin: 0;
  color: var(--ink);
  font-weight: 690;
  line-height: 1.25;
  letter-spacing: -.025em;
}
.section-card h2,
.card h2 { font-size: 20px; }
.section-card h3 { font-size: 17px; }
.body {
  max-width: 76ch;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: var(--ink-soft);
}
.section-card > .body { margin-top: 20px; }

/* --- 渲染后的 markdown ---------------------------------------------------
   .body 默认是 pre-wrap（纯文本靠它保住换行）；渲染之后换行由块级元素负责，
   再叠一层 pre-wrap 会把标签之间的缩进也画出来，所以这里必须关掉。 */
.md { white-space: normal; }
.md > :first-child { margin-top: 0; }
.md > :last-child { margin-bottom: 0; }
.md p { margin: 0 0 12px; }
.md h1, .md h2, .md h3, .md h4, .md h5, .md h6 {
  margin: 22px 0 10px; line-height: 1.3; color: var(--ink); font-weight: 640;
}
.md h1 { font-size: 20px; }
.md h2 { font-size: 17px; }
.md h3 { font-size: 15px; }
.md h4, .md h5, .md h6 { font-size: 14px; }
.md ul, .md ol { margin: 0 0 12px; padding-left: 22px; }
.md li { margin: 4px 0; }
.md ul.tasks { list-style: none; padding-left: 4px; }
.md .task { display: inline-flex; align-items: baseline; gap: 8px; }
.md blockquote {
  margin: 0 0 12px; padding: 2px 0 2px 14px;
  border-left: 3px solid var(--line); color: var(--muted);
}
.md code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .92em;
  background: var(--accent-soft); border-radius: 4px; padding: 1px 5px;
}
.md pre.code {
  margin: 0 0 12px; padding: 12px 14px; border: 1px solid var(--line);
  border-radius: 8px; overflow-x: auto; line-height: 1.5;
}
.md pre.code code { background: none; padding: 0; }
.md hr { margin: 20px 0; border: 0; border-top: 1px solid var(--line); }
.md a { color: var(--accent); }
.md .wikilink { font-variant-numeric: tabular-nums; }
.md figure { margin: 0 0 14px; }
.md figure img { max-width: 100%; height: auto; border-radius: 8px; }
.md figcaption { margin-top: 6px; color: var(--muted); font-size: 12px; }
.md img { max-width: 100%; height: auto; }
/* 表格可能比容器宽 —— 让它在自己的框里横向滚，别把整页撑出横向滚动条。 */
.md .tablewrap { margin: 0 0 14px; overflow-x: auto; }
.md table { border-collapse: collapse; font-size: 13px; min-width: 100%; }
.md th, .md td {
  border-bottom: 1px solid var(--line); padding: 7px 12px; text-align: left;
  white-space: nowrap;
}
.md thead th { color: var(--muted); font-weight: 600; }
.md .ta-right { text-align: right; }
.md .ta-center { text-align: center; }
/* 整列都是数字时右对齐，读数容易对位 —— data-num 是渲染器算好的主数值。 */
.md td[data-num] { font-variant-numeric: tabular-nums; }

.empty-copy { color: var(--muted); font-style: italic; }
.meta {
  color: var(--muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}
.version-badge,
.pill,
.scope-badge {
  display: inline-flex;
  min-height: 24px;
  align-items: center;
  padding: 3px 8px;
  border-radius: 999px;
  color: var(--accent-strong);
  background: var(--accent-soft);
  font-size: 10px;
  font-weight: 700;
  line-height: 1.25;
}
.pill {
  margin: 8px 5px 0 0;
  font-weight: 600;
}
.pill.muted { color: var(--muted); background: rgba(102, 122, 117, .09); }
.pill.corrected { color: var(--danger); background: var(--danger-soft); }
.pill.confirmed { color: var(--success); background: var(--accent-soft); }

.toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.btn {
  display: inline-flex;
  min-height: 44px;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 9px 13px;
  border: 1px solid var(--line);
  border-radius: 12px;
  color: var(--ink-soft);
  background: rgba(255, 255, 255, .68);
  text-decoration: none;
  transition: color .18s ease, border-color .18s ease, background .18s ease, box-shadow .18s ease;
}
.btn:hover {
  border-color: rgba(20, 119, 101, .22);
  color: var(--accent-strong);
  background: rgba(255, 255, 255, .96);
  box-shadow: 0 6px 18px rgba(30, 55, 49, .07);
}
.btn.primary {
  border-color: transparent;
  color: #fff;
  background: linear-gradient(145deg, #1a8a74, #0f6758);
  box-shadow: 0 9px 20px rgba(15, 103, 88, .18);
}
.btn.primary:hover {
  color: #fff;
  background: linear-gradient(145deg, #18806d, #0d5b4e);
  box-shadow: 0 11px 24px rgba(15, 103, 88, .24);
}
.btn.warn { color: var(--warn); }
.btn.warn:hover { border-color: rgba(154, 90, 24, .2); background: var(--warn-soft); }
.btn:disabled { cursor: wait; opacity: .62; box-shadow: none; }
.btn:active,
.project:active,
.chapter:active,
.chapter-tile:active,
.project-tile:active { background: rgba(229, 242, 238, .94); }

.comments {
  margin-top: 20px;
  padding-top: 15px;
  border-top: 1px solid var(--line);
}
.comments > summary,
.raw-card > summary {
  display: flex;
  min-height: 44px;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin: -8px -8px 0;
  padding: 8px;
  border-radius: 11px;
  color: var(--ink-soft);
  cursor: pointer;
  list-style: none;
}
.comments > summary::-webkit-details-marker,
.raw-card > summary::-webkit-details-marker { display: none; }
.comments > summary:hover,
.raw-card > summary:hover { background: rgba(255, 255, 255, .55); }
.summary-label { display: inline-flex; align-items: center; gap: 8px; font-weight: 650; }
.comment-count { color: var(--muted); font-size: 11px; font-weight: 500; }
.chevron { transition: transform .2s ease; }
details[open] > summary .chevron { transform: rotate(180deg); }
.comments-content { padding-top: 8px; }
.comment {
  margin: 9px 0;
  padding: 11px 13px;
  border: 1px solid rgba(102, 122, 117, .09);
  border-left: 3px solid #a8b7b2;
  border-radius: 4px 12px 12px 4px;
  color: var(--ink-soft);
  background: rgba(249, 251, 250, .72);
}
.comment.correction { border-left-color: var(--danger); background: var(--danger-soft); }
.comment.confirmation { border-left-color: var(--success); background: var(--accent-softer); }
.comment .who { margin-top: 5px; color: var(--muted); font-size: 10px; }
.comment.resolved { opacity: .58; }
.comment-form {
  display: grid;
  grid-template-columns: 132px minmax(0, 1fr) auto;
  gap: 8px;
  margin-top: 11px;
}
.comment-form select,
.comment-form input {
  width: 100%;
  min-width: 0;
  min-height: 44px;
  padding: 9px 11px;
  border: 1px solid var(--line);
  border-radius: 11px;
  outline: 0;
  color: var(--ink);
  background: rgba(255, 255, 255, .82);
}
.comment-form select:focus,
.comment-form input:focus {
  border-color: rgba(20, 119, 101, .38);
  box-shadow: 0 0 0 4px rgba(20, 119, 101, .08);
}

.chapter-grid,
.project-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 18px;
}
.chapter-tile,
.project-tile {
  position: relative;
  display: flex;
  min-width: 0;
  min-height: 142px;
  flex-direction: column;
  align-items: flex-start;
  padding: 18px;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 16px;
  color: var(--ink);
  background: rgba(255, 255, 255, .52);
  text-align: left;
  transition: border-color .2s ease, background .2s ease, box-shadow .2s ease;
}
.chapter-tile:hover,
.project-tile:hover {
  border-color: rgba(20, 119, 101, .2);
  background: rgba(255, 255, 255, .88);
  box-shadow: var(--shadow-sm);
}
.tile-top {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.tile-icon {
  display: grid;
  width: 38px;
  height: 38px;
  place-items: center;
  border-radius: 12px;
  color: var(--accent);
  background: var(--accent-soft);
}
.tile-arrow { color: var(--muted); transition: color .2s ease, transform .2s ease; }
.chapter-tile:hover .tile-arrow,
.project-tile:hover .tile-arrow { color: var(--accent); transform: translateX(2px); }
.chapter-tile strong,
.project-tile strong {
  display: block;
  margin-top: 15px;
  font-size: 15px;
  line-height: 1.35;
  overflow-wrap: anywhere;
}
.tile-summary {
  display: -webkit-box;
  overflow: hidden;
  margin-top: 6px;
  color: var(--muted);
  font-size: 12px;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.timeline {
  position: relative;
  display: grid;
  gap: 13px;
  margin-left: 3px;
}
.timeline::before {
  position: absolute;
  top: 23px;
  bottom: 24px;
  left: 21px;
  width: 1px;
  background: linear-gradient(var(--accent), rgba(20, 119, 101, .08));
  content: "";
}
.node {
  position: relative;
  min-width: 0;
  padding-left: 58px;
}
.node-marker {
  position: absolute;
  z-index: 1;
  top: 17px;
  left: 0;
  display: grid;
  width: 43px;
  height: 34px;
  place-items: center;
  border: 4px solid rgba(237, 243, 244, .92);
  border-radius: 11px;
  color: #fff;
  background: linear-gradient(145deg, #2a9a83, #126b5b);
  box-shadow: 0 6px 13px rgba(18, 107, 91, .18);
  font-size: 9px;
  font-weight: 800;
  letter-spacing: .04em;
  font-variant-numeric: tabular-nums;
}
.node-card {
  min-width: 0;
  padding: 20px 21px;
  border-radius: var(--radius-md);
}
.node-card h3 {
  margin: 4px 0 0;
  font-size: 17px;
  overflow-wrap: anywhere;
}
.node-card .body { margin-top: 14px; }
.node-actions { justify-content: flex-end; }
.node-actions .btn { min-height: 40px; padding: 7px 10px; font-size: 12px; }

.code {
  margin-top: 14px;
  overflow: hidden;
  border: 1px solid rgba(35, 64, 57, .14);
  border-radius: 13px;
  background: rgba(255, 255, 255, .58);
}
.code-head {
  padding: 9px 12px;
  overflow-wrap: anywhere;
  color: #4f615c;
  background: rgba(235, 241, 239, .88);
  font: 11px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace;
}
.code pre {
  max-height: 420px;
  margin: 0;
  padding: 14px;
  overflow: auto;
  color: #e7f2ef;
  background: #162521;
  font: 12px/1.6 ui-monospace, SFMono-Regular, Consolas, monospace;
  tab-size: 2;
}
.code-annotation { padding: 10px 12px; color: var(--ink-soft); font-size: 12px; }
.code-evidence { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--line); min-width: 0; }
.code-evidence pre { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; line-height: 1.6; }
.code-evidence summary { cursor: pointer; }
.artifact {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  margin: 8px 0 0;
  padding: 9px 11px;
  overflow-wrap: anywhere;
  border: 1px solid rgba(102, 122, 117, .08);
  border-radius: 11px;
  color: var(--ink-soft);
  background: rgba(241, 246, 244, .7);
  font-size: 12px;
}
.artifact .icon { width: 16px; height: 16px; margin-top: 2px; color: var(--accent); }

/* 原始历史的时间线。左侧一道细线 + 每条一个色点：事件类型是这里唯一稳定的结构，
   用颜色编码它，人扫一眼就能分出「我说的话 / 跑的命令 / 命令的输出 / 一轮的回答」。 */
.raw-row { position: relative; }
.raw-row::before {                      /* 贯穿的时间线 */
  content: "";
  position: absolute;
  left: 4px;
  top: 0;
  bottom: 0;
  width: 1px;
  background: var(--line);
}
.raw-row:first-child::before { top: 18px; }
.raw-row:last-child::before { bottom: auto; height: 18px; }
.raw-row::after {                       /* 类型色点 */
  content: "";
  position: absolute;
  left: 0;
  top: 15px;
  width: 9px;
  height: 9px;
  border: 1.6px solid var(--muted);
  border-radius: 50%;
  background: var(--panel, #fff);
}
.raw-row.tone-prompt::after { border-color: var(--accent); background: var(--accent); }
.raw-row.tone-turn::after { border-color: var(--accent); }
.raw-row.tone-call::after { border-color: #7d8a86; }
.raw-row.tone-result::after { border-color: #7d8a86; background: #7d8a86; }
.raw-row.tone-agent::after { border-radius: 2px; }
.raw-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 9px;
}
.raw-label { color: var(--ink); font-size: 13px; font-weight: 620; }
.raw-when, .raw-who, .raw-dur {
  color: var(--muted);
  font: 10.5px/1.4 ui-monospace, SFMono-Regular, Consolas, monospace;
}
.raw-who { margin-left: auto; }
.raw-dur { color: var(--accent-strong); }
.raw-brief {
  margin: 5px 0 0;
  max-width: 78ch;
  color: var(--ink-soft, #344944);
  font-size: 13px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
/* 你自己说的话和这一轮的回答是这里最该被读到的两类，给它们正常的字重。 */
.raw-row.tone-prompt .raw-brief { color: var(--ink); }
.raw-row.tone-call .raw-brief,
.raw-row.tone-result .raw-brief {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 11.5px;
}
/* 对话原文里的每一轮。角色标签用固定宽度，多行时左边缘对齐，扫起来像剧本。 */
.raw-turns { margin-top: 6px; display: grid; gap: 4px; }
.raw-turn {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  gap: 8px;
  margin: 0;
  max-width: 78ch;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--ink-soft, #344944);
  overflow-wrap: anywhere;
}
.raw-turn .who {
  color: var(--muted);
  font: 10.5px/1.75 ui-monospace, SFMono-Regular, Consolas, monospace;
  text-align: right;
}
.raw-turn.side { opacity: .72; }        /* 子 agent 的回合压低一档，主线才读得出来 */

.raw-full { margin-top: 6px; }
.raw-full > summary {
  display: inline-block;
  color: var(--muted);
  font-size: 11px;
  cursor: pointer;
  list-style: none;
}
.raw-full > summary::-webkit-details-marker { display: none; }
.raw-full > summary::before { content: "▸ "; }
.raw-full[open] > summary::before { content: "▾ "; }
.raw-full > summary:hover { color: var(--accent-strong); }

.raw-card { padding: 14px 20px; }
.raw-list {
  margin-top: 8px;
  padding-top: 5px;
  border-top: 1px solid var(--line);
}
.raw-row {
  /* 左内边距要和时间线的圆点一起改：padding 简写会把上面那条 padding-left 重置掉，
     圆点就压在文字上了。所以只在这一处定义内边距。 */
  padding: 13px 0 13px 20px;
  border-bottom: 1px solid var(--line);
}
.raw-row:last-child { border-bottom: 0; }
.raw-row pre {
  max-height: 190px;
  margin: 7px 0 0;
  padding: 10px;
  overflow: auto;
  border-radius: 10px;
  color: #41544f;
  background: rgba(238, 243, 242, .74);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: 11px/1.55 ui-monospace, SFMono-Regular, Consolas, monospace;
}

.welcome-card {
  position: relative;
  min-height: 320px;
  padding: clamp(28px, 6vw, 58px);
  overflow: hidden;
  border: 1px solid var(--line-light);
  border-radius: var(--radius-lg);
  background:
    linear-gradient(120deg, rgba(255, 255, 255, .9), rgba(255, 255, 255, .55)),
    linear-gradient(135deg, rgba(33, 150, 126, .14), rgba(106, 126, 201, .14));
  box-shadow: var(--shadow-md);
  backdrop-filter: blur(22px) saturate(145%);
  -webkit-backdrop-filter: blur(22px) saturate(145%);
}
.welcome-card::after {
  position: absolute;
  top: -5rem;
  right: -4rem;
  width: 24rem;
  height: 24rem;
  border: 1px solid rgba(255, 255, 255, .65);
  border-radius: 45% 55% 64% 36%;
  background: linear-gradient(145deg, rgba(34, 158, 132, .2), rgba(102, 122, 197, .18));
  box-shadow: inset 0 0 60px rgba(255, 255, 255, .32);
  content: "";
  transform: rotate(18deg);
}
.welcome-card > * { position: relative; z-index: 1; }
.welcome-card p {
  max-width: 610px;
  margin: 17px 0 0;
  color: var(--ink-soft);
  font-size: 16px;
}
.welcome-card .toolbar { margin-top: 25px; }
.home-section { margin-top: 18px; }
.empty {
  display: grid;
  min-height: 250px;
  place-items: center;
  padding: 38px 20px;
  border: 1px dashed rgba(54, 83, 76, .18);
  border-radius: var(--radius-md);
  color: var(--muted);
  background: rgba(255, 255, 255, .34);
  text-align: center;
}
.empty-inner { max-width: 560px; }
.empty h2 { margin: 0; color: var(--ink); font-size: 25px; letter-spacing: -.03em; }
.empty p { margin: 10px 0 0; }
.empty .btn { margin-top: 20px; }
.danger { color: var(--danger) !important; }

dialog {
  width: min(680px, calc(100% - 28px));
  max-width: 680px;
  max-height: min(86vh, 820px);
  padding: 0;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, .84);
  border-radius: 23px;
  color: var(--ink);
  background: rgba(249, 252, 251, .94);
  box-shadow: var(--shadow-lg);
  backdrop-filter: blur(26px) saturate(145%);
  -webkit-backdrop-filter: blur(26px) saturate(145%);
}
dialog::backdrop {
  background: rgba(21, 38, 34, .38);
  backdrop-filter: blur(5px);
  -webkit-backdrop-filter: blur(5px);
}
.editor {
  display: grid;
  max-height: min(86vh, 820px);
  grid-template-rows: auto minmax(0, 1fr) auto;
}
.dialog-head {
  padding: 22px 24px 15px;
  border-bottom: 1px solid var(--line);
}
.dialog-title {
  margin: 0;
  font-size: 21px;
  font-weight: 700;
  letter-spacing: -.025em;
}
.modal-body { padding: 5px 24px 22px; overflow: auto; }
.dialog-actions {
  justify-content: flex-end;
  padding: 14px 24px 20px;
  border-top: 1px solid var(--line);
  background: rgba(247, 250, 249, .74);
}
.modal-status {
  min-height: 19px;
  padding: 0 24px;
  color: var(--danger);
  font-size: 12px;
}
.editor label,
.field-label {
  display: block;
  margin: 15px 0 6px;
  color: var(--ink-soft);
  font-size: 12px;
  font-weight: 650;
}
.editor input:not([type="checkbox"]),
.editor textarea,
.editor select {
  width: 100%;
  min-height: 44px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 12px;
  outline: 0;
  color: var(--ink);
  background: rgba(255, 255, 255, .86);
}
.editor textarea { min-height: 170px; resize: vertical; }
.editor input:focus,
.editor textarea:focus,
.editor select:focus {
  border-color: rgba(20, 119, 101, .38);
  box-shadow: 0 0 0 4px rgba(20, 119, 101, .08);
}
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.management-card {
  margin: 11px 0;
  padding: 15px;
  border: 1px solid var(--line);
  border-radius: 15px;
  background: rgba(255, 255, 255, .62);
}
.management-card:first-child { margin-top: 14px; }
.management-card strong { overflow-wrap: anywhere; }
.management-card .toolbar { margin-top: 11px; }
.management-card .field-inline { min-width: 130px; flex: 1 1 150px; }
.management-card .field-inline .field-label { margin-top: 0; }
.management-card pre {
  max-height: 220px;
  margin: 9px 0 0;
  padding: 10px;
  overflow: auto;
  border-radius: 10px;
  color: #41544f;
  background: rgba(238, 243, 242, .74);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: 11px/1.55 ui-monospace, SFMono-Regular, Consolas, monospace;
}
.check-row {
  display: inline-flex !important;
  min-height: 44px;
  align-items: center;
  gap: 7px;
  margin: 0 !important;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 11px;
  background: rgba(255, 255, 255, .62);
}
.account-summary {
  display: flex;
  align-items: center;
  gap: 13px;
  padding: 16px 0 6px;
}
.account-avatar {
  display: grid;
  width: 48px;
  height: 48px;
  place-items: center;
  border-radius: 15px;
  color: #fff;
  background: linear-gradient(145deg, #258d78, #105f52);
  font-weight: 750;
}
.account-summary strong { display: block; }
.account-summary .meta { margin-top: 3px; }
.login-icon { margin: 0 auto 16px; }

.toast-region {
  position: fixed;
  z-index: 90;
  right: 18px;
  bottom: 18px;
  display: grid;
  width: min(390px, calc(100% - 36px));
  gap: 8px;
  pointer-events: none;
}
.toast {
  padding: 13px 15px;
  border: 1px solid var(--line-light);
  border-radius: 14px;
  color: var(--ink);
  background: rgba(255, 255, 255, .94);
  box-shadow: var(--shadow-md);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  animation: toast-in .22s ease both;
}
.toast.error { color: var(--danger); }
@keyframes toast-in {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}

@media (max-width: 1120px) {
  .layout { grid-template-columns: 250px minmax(0, 1fr); gap: 18px; }
  .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 880px) {
  :root { --header-offset: 92px; }
  .layout { grid-template-columns: 1fr; }
  .sidebar {
    position: static;
    display: grid;
    max-height: none;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 0 18px;
  }
  .sidebar-head { grid-column: 1 / -1; }
  .nav-section { min-width: 0; }
  main { max-width: none; padding-top: 4px; }
  .project-hero { min-height: 170px; }
}

@media (max-width: 680px) {
  :root { --header-offset: 154px; }
  .top-shell { padding: 9px 9px 0; }
  .top {
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 9px;
    padding: 9px;
    border-radius: 17px;
  }
  .brand-copy small { display: none; }
  .search-shell { grid-column: 1 / -1; grid-row: 2; }
  .account-btn span { display: none; }
  .account-btn { width: 46px; padding: 0; }
  .layout { width: calc(100% - 18px); }
  .sidebar {
    grid-template-columns: 1fr;
    margin-top: 10px;
    padding: 14px;
    border-radius: 18px;
  }
  .nav-list {
    display: flex;
    gap: 7px;
    padding: 1px 1px 4px;
    overflow-x: auto;
    overscroll-behavior-inline: contain;
  }
  .project,
  .chapter { min-width: 196px; margin: 0; }
  main { padding: 2px 0 65px; }
  .project-hero,
  .welcome-card { padding: 25px 21px; border-radius: 21px; }
  .project-hero h1,
  .welcome-card h1 { font-size: clamp(29px, 10vw, 42px); }
  .metric-grid,
  .chapter-grid,
  .project-grid,
  .grid2 { grid-template-columns: 1fr; }
  .metric-grid { gap: 9px; }
  .metric { display: grid; grid-template-columns: 42px auto; column-gap: 10px; padding: 14px; }
  .metric-top { grid-row: 1 / 3; }
  .metric-value { margin-top: 1px; font-size: 21px; }
  .metric-label { margin-top: 1px; }
  .section-head,
  .chapter-head { align-items: stretch; flex-direction: column; }
  .section-head > .toolbar,
  .chapter-head > .toolbar { justify-content: flex-start; }
  .comment-form { grid-template-columns: 1fr; }
  .node { padding-left: 49px; }
  .node-marker { width: 37px; }
  .timeline::before { left: 18px; }
  .node-card { padding: 18px 16px; }
  .node-actions { justify-content: flex-start; }
  .dialog-head { padding: 19px 19px 13px; }
  .modal-body { padding: 4px 19px 18px; }
  .dialog-actions { padding: 12px 19px 17px; }
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    scroll-behavior: auto !important;
    animation-duration: .01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: .01ms !important;
  }
}

/* Quiet reading layout: glass is reserved for navigation and dialogs. */
:root {
  --bg: #f6f8f7;
  --bg-deep: #f0f3f2;
  --glass: rgba(255, 255, 255, .8);
  --ink: #17231f;
  --ink-soft: #3f514b;
  --muted: #71807b;
  --line: rgba(38, 62, 55, .12);
  --line-light: rgba(255, 255, 255, .88);
  --accent: #176b5c;
  --accent-strong: #0e594c;
  --accent-soft: rgba(23, 107, 92, .08);
  --shadow-sm: 0 5px 20px rgba(30, 55, 49, .055);
  --header-offset: 82px;
}
body {
  color: var(--ink);
  background: linear-gradient(180deg, #f8faf9 0%, var(--bg) 42%, var(--bg-deep) 100%);
  background-attachment: fixed;
  font-size: 16px;
  line-height: 1.68;
}
body::before,
body::after { display: none; }

.top-shell { padding: 10px clamp(12px, 2vw, 24px) 0; }
.top {
  grid-template-columns: minmax(170px, auto) minmax(240px, 620px) minmax(108px, auto);
  min-height: 58px;
  gap: 14px;
  padding: 7px 9px 7px 12px;
  border-radius: 14px;
  background: rgba(255, 255, 255, .82);
  box-shadow: 0 6px 22px rgba(31, 57, 51, .065);
  backdrop-filter: blur(16px) saturate(125%);
  -webkit-backdrop-filter: blur(16px) saturate(125%);
}
.brand { gap: 9px; }
.brand-mark {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  box-shadow: none;
}
.brand-mark .icon { width: 18px; height: 18px; }
.brand-copy strong { font-size: 14px; }
.brand-copy small { display: none; }
.search-shell input {
  min-height: 44px;
  border-radius: 11px;
  background: rgba(242, 246, 244, .72);
}
.account-btn {
  min-height: 44px;
  padding: 8px 12px;
  border: 0;
  border-radius: 11px;
  background: transparent;
}

.layout {
  grid-template-columns: 220px minmax(0, 1fr);
  gap: clamp(28px, 4vw, 52px);
  width: min(1260px, calc(100% - clamp(28px, 4vw, 56px)));
}
.sidebar {
  top: var(--header-offset);
  max-height: calc(100vh - 98px);
  margin-top: 20px;
  padding: 13px;
  border-radius: 15px;
  background: rgba(255, 255, 255, .62);
  box-shadow: none;
  backdrop-filter: blur(14px) saturate(115%);
  -webkit-backdrop-filter: blur(14px) saturate(115%);
}
.sidebar-head { display: none; }
.nav-section { padding-top: 2px; }
#chapterArea { margin-top: 20px; }
.section-title {
  margin: 0 8px 5px;
  font-size: 10px;
  letter-spacing: .12em;
}
.project,
.chapter {
  min-height: 44px;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  margin: 1px 0;
  padding: 7px 9px;
  border-radius: 9px;
  font-size: 13px;
}
.project:hover,
.chapter:hover { background: rgba(255, 255, 255, .62); }
.project.on,
.chapter.on {
  border-color: transparent;
  background: var(--accent-soft);
  box-shadow: none;
}
.nav-icon,
.nav-copy small { display: none; }
.nav-copy strong { font-size: 13px; font-weight: 580; }
.count {
  min-width: 18px;
  height: auto;
  padding: 0;
  color: #899590;
  background: transparent;
  font-size: 10px;
}
.side-add {
  min-height: 44px;
  justify-content: flex-start;
  margin: 2px 0 0;
  padding: 7px 9px;
  border: 0;
  border-radius: 9px;
  color: var(--muted);
  background: transparent;
  font-size: 12px;
}
.side-add .icon { width: 15px; height: 15px; }
.side-add:hover { border-color: transparent; color: var(--accent); background: var(--accent-soft); }

main {
  max-width: 900px;
  padding: 42px 0 90px;
}
.main-stack { gap: 0; }
.page-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  padding: 4px 0 27px;
  border-bottom: 1px solid var(--line);
}
.page-head h1 {
  margin: 0;
  font-size: clamp(27px, 4vw, 35px);
  font-weight: 680;
  line-height: 1.18;
  letter-spacing: -.035em;
  overflow-wrap: anywhere;
}
.page-head p {
  max-width: 680px;
  margin: 9px 0 0;
  color: var(--muted);
  font-size: 13px;
}
.page-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px 13px;
  margin-top: 10px;
  color: var(--muted);
  font-size: 12px;
}
.workspace-key {
  max-width: 100%;
  margin: 0;
  padding: 0;
  border: 0;
  color: var(--muted);
  background: transparent;
  font-size: 10px;
}
.workspace-key .icon { width: 13px; height: 13px; }

.project-list-section { padding-top: 27px; }
.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 6px;
}
.list-head h2 { margin: 0; font-size: 15px; font-weight: 650; }
.simple-projects { border-top: 1px solid var(--line); }
.project-row {
  display: grid;
  width: 100%;
  min-height: 66px;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 18px;
  padding: 11px 3px;
  border: 0;
  border-bottom: 1px solid var(--line);
  color: var(--ink);
  background: transparent;
  text-align: left;
}
.project-row:hover { color: var(--accent-strong); background: rgba(255, 255, 255, .32); }
.project-row strong { display: block; font-size: 14px; font-weight: 600; }
.project-row small { display: block; margin-top: 2px; color: var(--muted); font-size: 11px; }
.project-row .icon { color: var(--muted); }

.glass,
.card,
.section-card,
.node-card {
  border: 0;
  background: transparent;
  box-shadow: none;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}
.section-card,
.card {
  padding: 28px 0;
  border-bottom: 1px solid var(--line);
  border-radius: 0;
}
.section-card.feature { background: transparent; }
.section-head,
.chapter-head { align-items: flex-start; }
.section-heading { display: block; }
.section-icon,
.section-heading .eyebrow { display: none; }
.section-card h2,
.card h2 { font-size: 18px; font-weight: 650; }
.section-card h3 { font-size: 16px; }
.section-card > .body { margin-top: 15px; }
.body { max-width: 72ch; color: var(--ink-soft); }
.version-badge {
  min-height: 0;
  padding: 0;
  color: var(--muted);
  background: transparent;
  font-weight: 500;
}
.section-card .btn:not(.primary),
.node-card .btn {
  border-color: transparent;
  color: var(--muted);
  background: transparent;
  box-shadow: none;
}
.section-card .btn:not(.primary):hover,
.node-card .btn:hover { color: var(--accent-strong); background: var(--accent-soft); }
.chapter-hint {
  padding: 22px 0 28px;
  border-bottom: 1px solid var(--line);
  color: var(--muted);
  font-size: 13px;
}

.timeline {
  gap: 0;
  margin: 0;
  padding-top: 4px;
}
.timeline::before {
  top: 33px;
  bottom: 32px;
  left: 4px;
  background: var(--line);
}
.node { padding-left: 26px; }
.node::before {
  position: absolute;
  z-index: 1;
  top: 31px;
  left: 0;
  width: 9px;
  height: 9px;
  border: 2px solid var(--bg);
  border-radius: 50%;
  background: #7b8e88;
  box-shadow: 0 0 0 1px rgba(38, 62, 55, .18);
  content: "";
}
.node-marker { display: none; }
.node-card {
  padding: 25px 0 27px;
  border-bottom: 1px solid var(--line);
  border-radius: 0;
}
.node-card h3 { margin-top: 3px; font-size: 16px; font-weight: 640; }
.node-card .body { margin-top: 12px; }
.node-actions {
  opacity: 0;
  transition: opacity .18s ease;
}
.node:hover .node-actions,
.node:focus-within .node-actions { opacity: 1; }
.node-actions .btn { min-height: 36px; padding: 5px 8px; }
.pill {
  min-height: 21px;
  margin-top: 7px;
  padding: 2px 7px;
  border: 1px solid var(--line);
  color: var(--muted);
  background: transparent;
  font-size: 9px;
}
.pill.muted,
.pill.corrected,
.pill.confirmed { background: transparent; }

.comments { margin-top: 16px; padding-top: 8px; }
.comments > summary,
.raw-card > summary {
  min-height: 40px;
  margin: -4px 0 0;
  padding: 4px 0;
  border-radius: 7px;
  font-size: 12px;
}
.summary-label { gap: 6px; font-weight: 560; }
.summary-label .icon { display: none; }
.comment-count { font-size: 10px; }
.comments-content { padding-top: 4px; }
.comment { margin: 7px 0; padding: 9px 11px; font-size: 13px; }
.comment-compose { margin-top: 8px; }
.comment-compose > summary {
  display: inline-flex;
  min-height: 40px;
  align-items: center;
  color: var(--muted);
  cursor: pointer;
  font-size: 11px;
  list-style: none;
}
.comment-compose > summary::-webkit-details-marker { display: none; }
.comment-compose[open] > summary { color: var(--accent); }
.comment-form { margin-top: 4px; }

.raw-card { padding: 18px 0; border-top: 0; }
.raw-list { margin-top: 4px; }
.empty {
  min-height: 170px;
  padding: 26px 16px;
  border: 0;
  border-radius: 0;
  background: transparent;
}
.empty h2 { font-size: 20px; }

@media (max-width: 880px) {
  :root { --header-offset: 80px; }
  .layout { grid-template-columns: 1fr; gap: 0; width: min(900px, calc(100% - 28px)); }
  .sidebar {
    position: static;
    display: block;
    max-height: none;
    margin-top: 12px;
    padding: 10px;
  }
  .nav-section { min-width: 0; }
  #chapterArea { margin-top: 11px; }
  .nav-list { display: flex; gap: 4px; overflow-x: auto; padding-bottom: 2px; }
  .nav-list { scrollbar-width: none; }
  .nav-list::-webkit-scrollbar { display: none; }
  .project,
  .chapter { min-width: 168px; margin: 0; }
  .side-add { width: auto; }
  main { max-width: none; padding-top: 30px; }
}

@media (max-width: 680px) {
  :root { --header-offset: 142px; }
  .top-shell { padding: 7px 7px 0; }
  .top {
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 7px;
    padding: 7px;
    border-radius: 12px;
  }
  .search-shell {
    width: 100%;
    min-width: 0;
    grid-column: 1 / -1;
    grid-row: 2;
  }
  .account-btn { width: 44px; padding: 0; }
  .account-btn span { display: none; }
  .layout { width: calc(100% - 18px); }
  .sidebar { margin-top: 8px; }
  .page-head { align-items: stretch; flex-direction: column; gap: 14px; padding-bottom: 22px; }
  .page-head h1 { font-size: 28px; }
  main { padding-top: 24px; }
  .section-card,
  .card { padding: 23px 0; }
  .section-head,
  .chapter-head { flex-direction: column; gap: 10px; }
  .section-head > .toolbar,
  .chapter-head > .toolbar { justify-content: flex-start; }
  .node { padding-left: 20px; }
  .timeline::before { left: 3px; }
  .node::before { left: -1px; }
  .node-card { padding: 22px 0 24px; }
  .node-actions { opacity: 1; }
}

/* Project workspace: the structure stays visible while one record is read. */
body.workspace-active .layout {
  width: min(1480px, calc(100% - clamp(24px, 3vw, 44px)));
  grid-template-columns: 210px minmax(0, 1fr);
  gap: clamp(18px, 2.2vw, 30px);
}
main.workspace-mode {
  width: 100%;
  max-width: none;
  height: calc(100vh - 88px);
  min-height: 620px;
  padding: 20px 0 24px;
  overflow: hidden;
}
.workspace-page {
  display: grid;
  height: 100%;
  min-height: 0;
  grid-template-rows: auto minmax(0, 1fr);
}
.workspace-page > .page-head {
  align-items: flex-start;
  padding: 0 0 16px;
  border-bottom: 0;
}
.workspace-page > .page-head h1 { font-size: clamp(24px, 3vw, 30px); }
.workspace-page > .page-head .page-meta { margin-top: 6px; }
.workspace-body {
  display: grid;
  min-width: 0;
  min-height: 0;
  grid-template-columns: minmax(390px, .92fr) minmax(390px, 1.08fr);
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: rgba(255, 255, 255, .42);
}
.structure-pane,
.record-pane { min-width: 0; min-height: 0; }
.structure-pane {
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--line);
  background: rgba(244, 247, 246, .7);
}
.pane-head {
  display: flex;
  min-height: 70px;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 13px 15px;
  border-bottom: 1px solid var(--line);
  background: rgba(255, 255, 255, .58);
}
.pane-kicker {
  display: block;
  margin-bottom: 2px;
  color: var(--muted);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: .14em;
}
.pane-head h2 { margin: 0; font-size: 15px; font-weight: 650; }
.view-switch {
  display: inline-flex;
  flex: 0 0 auto;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(255, 255, 255, .66);
}
.view-switch button {
  min-height: 36px;
  padding: 5px 10px;
  border: 0;
  border-radius: 0;
  color: var(--muted);
  background: transparent;
  font-size: 11px;
}
.view-switch button + button { border-left: 1px solid var(--line); }
.view-switch button[aria-pressed="true"] {
  color: #fff;
  background: var(--accent);
}
.structure-subhead {
  display: flex;
  min-height: 39px;
  align-items: center;
  gap: 7px 13px;
  padding: 6px 15px;
  border-bottom: 1px solid var(--line);
  color: var(--muted);
  font-size: 10px;
}
.inline-add {
  display: inline-flex;
  min-height: 30px;
  align-items: center;
  gap: 5px;
  margin-left: auto;
  padding: 3px 8px;
  border: 0;
  color: var(--accent-strong);
  background: transparent;
  font-size: 11px;
}
.inline-add:hover { background: var(--accent-soft); }
.inline-add .icon { width: 14px; height: 14px; }
.structure-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  overscroll-behavior: contain;
}
.chapter-map {
  width: max-content;
  min-width: 100%;
}
/* 结构图必须**收在栏里**，否则 width:max-content 会让整段跟着画布一起变宽，
   .graph-viewport 拿到 1240px 的可视宽、自己不再滚，右边的分支就被外层直接切掉，
   而且缩放算出来永远是 100%（它以为自己装得下）。列表那边仍要 max-content。 */
.chapter-map.graph-map { width: auto; min-width: 0; }
.chapter-map.grouped + .chapter-map { border-top: 1px solid var(--line); }
.chapter-map-title {
  position: sticky;
  left: 0;
  z-index: 2;
  display: flex;
  width: min(100%, 100vw);
  min-width: 360px;
  min-height: 42px;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 16px;
  border: 0;
  border-bottom: 1px solid var(--line);
  border-radius: 0;
  color: var(--ink);
  background: rgba(250, 252, 251, .94);
  text-align: left;
  font-size: 12px;
  font-weight: 620;
}
.chapter-map-title:hover { color: var(--accent-strong); background: #fff; }
.chapter-map-title span { color: var(--muted); font-size: 10px; font-weight: 500; }
/* 列表的装订线。行高必须和 railSvg 拿到的那个数一致，否则点会对不上行。 */
.record-rail { display: flex; align-items: flex-start; gap: 2px; }
.record-rail-gutter { flex: 0 0 auto; padding-top: 0; }
.record-rail .record-list { flex: 1; min-width: 0; }
.record-rail .record-row { min-height: 58px; height: 58px; box-sizing: border-box; }
.rail { display: block; overflow: visible; }
.rail .rail-edge { fill: none; stroke: #9eafa9; stroke-width: 1.5; }
.rail .rail-dot { fill: #fff; stroke: #7f948e; stroke-width: 1.6; }
.rail .rail-dot.confirmed { fill: var(--accent); stroke: var(--accent); }
.rail .rail-dot.needs-review,
.rail .rail-dot.corrected { fill: #fff; stroke: #a94d55; stroke-width: 2; }

/* 画布常常比栏宽（分叉越多越宽），不给滚动就等于把右边的分支藏起来。
   缩放沿用 v1 的分工：外面这层承担缩放**后**的尺寸（决定滚动范围），
   .graph-canvas 保持自然尺寸再整体 scale——两者分开，缩放才不会把滚动条算错。 */
.graph-viewport { overflow: auto; max-height: min(72vh, 760px); }
.graph-zoomwrap { position: relative; }
.graph-canvas { position: relative; transform-origin: 0 0; }
.graph-zoom {
  display: flex;
  gap: 3px;
  align-items: center;
  margin-left: auto;
  color: var(--muted);
  font: 10.5px/1 ui-monospace, SFMono-Regular, Consolas, monospace;
}
.graph-zoom button {
  min-width: 22px;
  padding: 3px 5px;
  border: 1px solid var(--line);
  border-radius: 4px;
  color: inherit;
  background: #fff;
  font: inherit;
}
.graph-zoom button:hover { border-color: var(--accent); color: var(--accent-strong); }
.graph-zoom output { min-width: 34px; text-align: right; }
.graph-edges {
  position: absolute;
  inset: 0;
  overflow: visible;
  pointer-events: none;
}
.graph-edges path {
  fill: none;
  stroke: #9eafa9;
  stroke-width: 1.35;
  vector-effect: non-scaling-stroke;
}
/* 结构图 = v1 的卡片图：SVG 画边，HTML 卡片叠在上面。卡片用 HTML 而不是
   foreignObject，截断、hover、焦点态才都能用原生 CSS 写。 */
.graph-node {
  position: absolute;
  display: flex;
  flex-direction: column;
  gap: 3px;
  box-sizing: border-box;
  overflow: hidden;
  padding: 6px 9px;
  border: 1.5px solid var(--line);
  border-radius: 6px;
  color: var(--ink);
  background: #fff;
  text-align: left;
}
.graph-node:hover { box-shadow: 0 3px 12px rgba(35, 58, 52, .14); }
.graph-node.selected { box-shadow: 0 0 0 2.5px var(--accent), 0 4px 14px rgba(35, 58, 52, .16); }
.graph-node-meta {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--muted);
  font: 10.5px/1.6 ui-monospace, SFMono-Regular, Consolas, monospace;
  white-space: nowrap;
}
.graph-node-meta time { margin-left: auto; }
.graph-idx { font-weight: 700; color: var(--ink); letter-spacing: .02em; }
.graph-node-title {
  display: -webkit-box;
  overflow: hidden;
  font-size: 12.5px;
  line-height: 1.42;
  -webkit-box-orient: vertical;
  /* 卡片里放不下整条标题（中位 50 字），这里只给开头两行认人，
     完整的一条在右边的详情里，鼠标停上去也有。图管形状，不管全文。 */
  -webkit-line-clamp: 2;
}

/* v1 的状态语言：颜色和线型**同时**给，所以黑白打印或看不清颜色时也读得出来。
   已确认 = 绿实线，未确认 = 琥珀虚线，已纠正/有待处理 = 红点线。 */
.graph-node.confirmed { border-style: solid; border-color: var(--st-done); }
.graph-node.unreviewed { border-style: dashed; border-color: var(--st-wip); }
.graph-node.corrected,
.graph-node.needs-review { border-style: dotted; border-color: var(--st-note); background: var(--bg); }
.graph-node.confirmed .graph-node-state { color: var(--st-done); }
.graph-node.unreviewed .graph-node-state { color: var(--st-wip); }
.graph-node.corrected .graph-node-state,
.graph-node.needs-review .graph-node-state { color: var(--st-note); }
.graph-edges path.tree-edge { stroke-width: 1.7; }
.graph-edges path.tree-edge.confirmed { stroke: var(--st-done); }
.graph-edges path.tree-edge.unreviewed { stroke: var(--st-wip); stroke-dasharray: 5 3.5; }
.graph-edges path.tree-edge.corrected,
.graph-edges path.tree-edge.needs-review { stroke: var(--st-note); stroke-dasharray: 1.5 3.5; stroke-linecap: round; }
.structure-empty {
  width: min(100%, 520px);
  min-width: 340px;
  padding: 28px 16px;
  color: var(--muted);
  font-size: 12px;
}
.record-group { padding-bottom: 10px; }
.record-list { min-width: 360px; }
.record-row {
  display: grid;
  width: 100%;
  min-height: 58px;
  grid-template-columns: 34px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  padding: 8px 13px;
  border: 0;
  border-bottom: 1px solid var(--line);
  border-radius: 0;
  color: var(--ink);
  background: transparent;
  text-align: left;
}
.record-row:hover { background: rgba(255, 255, 255, .72); }
.record-row.selected { background: var(--accent-soft); box-shadow: inset 3px 0 0 var(--accent); }
.record-index { color: var(--muted); font: 10px ui-monospace, SFMono-Regular, Consolas, monospace; }
.record-copy { min-width: 0; }
.record-copy strong { display: block; overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.record-copy small { display: block; overflow: hidden; margin-top: 2px; color: var(--muted); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.record-state { color: var(--muted); font-size: 9px; white-space: nowrap; }
.record-row.confirmed .record-state { color: var(--accent-strong); }
.record-row.corrected .record-state { color: #8e3f47; }
.record-pane {
  overflow: auto;
  padding: 8px clamp(20px, 3vw, 34px) 70px;
  background: rgba(255, 255, 255, .76);
}
.record-pane .section-card,
.record-pane .card { padding: 24px 0; }
.record-pane .section-card:first-child,
.record-pane .card:first-child { padding-top: 24px; }
.detail-context {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 13px 0 4px;
  color: var(--muted);
  font-size: 10px;
}
.detail-context button {
  min-height: 30px;
  padding: 3px 6px;
  border: 0;
  color: var(--accent-strong);
  background: transparent;
  font-size: 10px;
}
.detail-context button:hover { background: var(--accent-soft); }
.record-detail .node { padding-left: 0; }
.record-detail .node::before { display: none; }
.record-detail .node-card {
  padding: 18px 0 26px;
  border-bottom: 0;
}
.record-detail .node-card h3 { font-size: 20px; line-height: 1.35; }
.record-detail .node-actions { opacity: 1; }
.record-detail .body { max-width: 68ch; font-size: 14px; }

@media (max-width: 1040px) {
  body.workspace-active .layout { grid-template-columns: 190px minmax(0, 1fr); gap: 16px; }
  .workspace-body { grid-template-columns: minmax(340px, .88fr) minmax(360px, 1.12fr); }
}

@media (max-width: 880px) {
  body.workspace-active .layout { grid-template-columns: 1fr; width: min(960px, calc(100% - 24px)); }
  main.workspace-mode { height: calc(100vh - 215px); min-height: 600px; padding-top: 16px; }
}

@media (max-width: 720px) {
  main.workspace-mode { height: auto; min-height: 0; overflow: visible; padding-bottom: 54px; }
  .workspace-page { height: auto; }
  .workspace-body { grid-template-columns: 1fr; overflow: visible; }
  .structure-pane { height: min(56vh, 520px); min-height: 390px; border-right: 0; border-bottom: 1px solid var(--line); }
  .record-pane { min-height: 430px; overflow: visible; padding: 7px 17px 55px; }
  .workspace-page > .page-head { padding-bottom: 13px; }
  .pane-head { align-items: flex-start; flex-direction: column; }
  .view-switch { width: 100%; }
  .view-switch button { flex: 1; min-height: 42px; }
  .structure-subhead { flex-wrap: wrap; }
  .inline-add { min-height: 36px; }
}

/* Classic trace theme: one quiet toolbar, one structure pane, one reading pane.
   Secondary capabilities remain available, but they do not become extra cards. */
:root {
  --bg: #fbfaf8;
  --bg-deep: #f6f3ee;
  --glass: #fff;
  --glass-strong: #fff;
  --glass-muted: #faf8f4;
  --ink: #24211d;
  --ink-soft: #49443d;
  --muted: #817a70;
  --line: #e4dfd7;
  --line-light: #eeeae4;
  --accent: #2d62a8;
  --accent-strong: #1f4f8f;
  --accent-soft: rgba(45, 98, 168, .09);
  --accent-softer: rgba(45, 98, 168, .045);
  --success: #2f7d4f;
  --shadow-sm: none;
  --shadow-md: none;
  --shadow-lg: none;
  --radius-sm: 5px;
  --radius-md: 7px;
  --radius-lg: 9px;
  --header-offset: 51px;
}
body {
  color: var(--ink);
  background: var(--bg);
  font-size: 14px;
  line-height: 1.62;
}
.top-shell {
  padding: 0;
  border-bottom: 1px solid var(--line);
  background: var(--glass-strong);
}
.top {
  width: 100%;
  min-height: 50px;
  grid-template-columns: minmax(160px, auto) minmax(260px, 600px) minmax(104px, auto);
  gap: 14px;
  padding: 6px 14px;
  border: 0;
  border-radius: 0;
  background: #fff;
  box-shadow: none;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}
.brand { gap: 8px; }
.brand-mark {
  width: 30px;
  height: 30px;
  border: 0;
  border-radius: 5px;
  background: var(--accent);
  box-shadow: none;
}
.brand-mark .icon { width: 17px; height: 17px; }
.brand-copy strong { font-size: 13px; }
.brand-copy small { display: none; }
.search-shell input {
  min-height: 36px;
  border: 1px solid var(--line);
  border-radius: 5px;
  background: var(--bg);
}
.search-shell input:hover,
.search-shell input:focus { background: #fff; }
.account-btn {
  min-height: 36px;
  padding: 5px 9px;
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
}
.account-btn:hover { border-color: var(--line); background: var(--bg); }

/* The old sidebar repeated the project list and created a third reading column.
   Project switching lives on the home page; chapter scope lives above the graph. */
.layout,
body.workspace-active .layout {
  display: block;
  width: 100%;
  max-width: none;
}
.sidebar { display: none; }
main {
  width: min(960px, calc(100% - 36px));
  max-width: none;
  margin: 0 auto;
  padding: 34px 0 72px;
}
.page-head h1 { font-size: clamp(25px, 3vw, 32px); }
.primary { box-shadow: none; }

main.workspace-mode {
  width: 100%;
  height: calc(100vh - 51px);
  min-height: 560px;
  margin: 0;
  padding: 0;
}
.workspace-page { grid-template-rows: auto minmax(0, 1fr); }
.workspace-page > .page-head {
  min-height: 58px;
  align-items: center;
  padding: 7px 16px;
  border-bottom: 1px solid var(--line);
  background: #fff;
}
.workspace-page > .page-head h1 {
  font-size: 18px;
  font-weight: 630;
  letter-spacing: -.015em;
}
.workspace-page > .page-head .page-meta { margin-top: 2px; }
.workspace-key { font-size: 9px; }
.workspace-body {
  grid-template-columns: minmax(380px, 46%) minmax(390px, 54%);
  border: 0;
  border-radius: 0;
  background: #fff;
}
.structure-pane {
  border-right: 1px solid var(--line);
  background: var(--bg);
}
.pane-head {
  min-height: 48px;
  padding: 6px 11px;
  background: #fff;
}
.pane-heading {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
}
.pane-kicker {
  margin: 0;
  font-size: 9px;
  letter-spacing: .1em;
}
.scope-select {
  min-width: 120px;
  max-width: 220px;
  height: 31px;
  padding: 3px 27px 3px 7px;
  border: 1px solid var(--line);
  border-radius: 5px;
  color: var(--ink);
  background: var(--bg);
  font-size: 12px;
}
.scope-select:focus { background: #fff; }
.view-switch { border-radius: 5px; background: #fff; }
.view-switch button { min-height: 30px; padding: 3px 8px; }
.view-switch button[aria-pressed="true"] { color: var(--accent-strong); background: var(--accent-soft); }
.structure-subhead {
  min-height: 31px;
  padding: 4px 11px;
  background: var(--bg);
}
.structure-subhead span:nth-child(2) { opacity: .78; }
.inline-add { min-height: 25px; }
.chapter-map-title {
  min-height: 34px;
  padding: 5px 12px;
  background: rgba(255, 255, 255, .94);
}
/* 这里以前统一压过 .graph-node 的 border-color，会把上面按 status 分出来的
   三种颜色全抹成同一个灰 —— 边框归 status，这一档只管没有 status 的数据流卡片。 */
.flow-node { border-color: #d7d1c8; border-radius: 5px; }
.graph-edges path { stroke: #aaa298; }
.record-row:hover { background: #fff; }
.record-pane {
  padding: 5px clamp(22px, 3vw, 38px) 60px;
  background: #fff;
}
.record-pane .section-card,
.record-pane .card { padding: 20px 0; }
.record-pane .section-card:first-child,
.record-pane .card:first-child { padding-top: 20px; }
.record-detail .node-card h3 { font-size: 19px; }
.comments > summary,
.raw-card > summary { color: var(--muted); }

/* 数据流用的是同一个 .graph-node，所以样式跟着一起走点式；
   它的点填成实心，和结构图区分开——那张图里实心表示「已确认」，这里表示「在数据流上」。 */
.flow-node { border-left: 3px solid var(--accent); }
.flow-node strong {
  display: -webkit-box;
  overflow: hidden;
  font-size: 12.5px;
  font-weight: 600;
  line-height: 1.45;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 4;
}
.flow-node .graph-idx {
  align-self: flex-start;
  padding: 0 5px;
  border: 1px solid var(--line);
  border-radius: 999px;
  font: 9.5px/1.7 ui-monospace, SFMono-Regular, Consolas, monospace;
}
.flow-node .graph-idx { border-color: var(--accent); color: var(--accent-strong); }
.graph-edges path.flow-edge {
  stroke: var(--accent);
  stroke-width: 1.2;
  marker-end: url(#flowArrow);
}
/* `.graph-edges path` 把所有 path 的 fill 关掉了，箭头本身是一个 path，
   不单独放开就是一个看不见的箭头。 */
.graph-edges marker path { fill: var(--accent); stroke: none; }
/* 环（A 的产物被 B 消费、B 的产物又被 A 消费）在派生视图里是允许出现的：
   存储层只做一次键 join，不按时间过滤方向。回边画成虚线，免得读者以为
   自己看的是一条普通的前向依赖。 */
.graph-edges path.flow-edge.back { stroke-dasharray: 4 3; opacity: .75; }
.flow-edge-label {
  fill: var(--muted);
  font: 9px ui-monospace, SFMono-Regular, Consolas, monospace;
  paint-order: stroke;
  stroke: rgba(250, 252, 251, .96);
  stroke-width: 3px;
  stroke-linejoin: round;
}
/* 只让画布自己横向滚。说明文字和依据清单留在面板宽度里：否则读者要先往右滚
   看图、再滚回来读「这条边凭什么连的」，而后者正是判断图可不可信的东西。 */
.flow-map { width: 100%; }
.flow-map .graph-viewport {
  min-width: 0;
  overflow-x: auto;
  overscroll-behavior-x: contain;
}
.flow-note,
.flow-evidence { max-width: 720px; }
.flow-note {
  padding: 10px 16px 0;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.6;
}
.flow-evidence {
  margin: 0;
  padding: 10px 16px 18px;
  list-style: none;
}
.flow-evidence > li {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 10px;
  padding: 5px 0;
  border-top: 1px solid var(--line);
  font-size: 11px;
}
.flow-evidence-pair { color: var(--ink); font: 10px ui-monospace, SFMono-Regular, Consolas, monospace; }
.flow-evidence code {
  overflow-wrap: anywhere;
  color: var(--muted);
  font: 10px ui-monospace, SFMono-Regular, Consolas, monospace;
}

@media (max-width: 720px) {
  :root { --header-offset: 101px; }
  .top { grid-template-columns: minmax(0, 1fr) auto; padding: 6px 8px; }
  .search-shell { grid-column: 1 / -1; grid-row: 2; }
  main { width: calc(100% - 22px); padding-top: 24px; }
  main.workspace-mode { width: 100%; height: auto; padding: 0 0 44px; }
  .workspace-page > .page-head { padding: 8px 11px; }
  .workspace-body { grid-template-columns: 1fr; }
  .pane-head { align-items: center; flex-direction: row; }
  .pane-heading { flex: 1; }
  .pane-kicker { display: none; }
  .scope-select { width: 100%; max-width: none; }
  .view-switch { width: auto; }
  .view-switch button { min-height: 34px; }
  .structure-pane { min-height: 360px; border-right: 0; border-bottom: 1px solid var(--line); }
}
</style>
</head>
<body>
<a class="skip-link" href="#main">跳到主要内容</a>
<header class="top-shell">
  <div class="top">
    <a class="brand" href="__TRACE_BASE__/" aria-label="Research Trace 首页">
      <span class="brand-mark" aria-hidden="true">
        <svg class="icon" viewBox="0 0 24 24">
          <circle cx="6" cy="6" r="2.2"></circle>
          <circle cx="18" cy="7" r="2.2"></circle>
          <circle cx="9" cy="18" r="2.2"></circle>
          <path d="M8 7.2l7.8-.2M7.2 8l1.2 7.7M16.6 8.8l-5.8 7.4"></path>
        </svg>
      </span>
      <span class="brand-copy"><strong>Research Trace</strong><small>Research workspace</small></span>
    </a>
    <div class="search-shell">
      <svg class="icon" viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="11" cy="11" r="7"></circle>
        <path d="m20 20-4-4"></path>
      </svg>
      <label class="sr-only" for="search">搜索研究记录与原始历史</label>
      <input id="search" type="search" autocomplete="off" placeholder="搜索记录、结论与原始历史…" aria-controls="searchResults" aria-expanded="false">
      <span class="search-key" aria-hidden="true">/</span>
      <div id="searchResults" class="search-results" role="region" aria-label="搜索结果" hidden></div>
    </div>
    <div class="top-actions">
      <button class="account-btn" id="healthBtn" type="button" aria-haspopup="dialog" aria-label="采集、Recorder 与集成状态">
        <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h4l2-5 3 10 2.5-5H21"></path></svg>
        <span>状态</span>
      </button>
      <button class="account-btn" id="tokenBtn" type="button" aria-haspopup="dialog" aria-label="账户与连接设置">
        <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4"></circle><path d="M4.5 20a7.5 7.5 0 0 1 15 0"></path></svg>
        <span>连接设置</span>
      </button>
    </div>
  </div>
</header>

<div class="layout">
  <aside class="sidebar" id="sidebar" aria-label="项目和 Chapter 导航">
    <div class="sidebar-head"><strong>研究空间</strong><span id="workspaceCount">0 个项目</span></div>
    <section class="nav-section" aria-labelledby="projectsLabel">
      <div class="section-title" id="projectsLabel">Projects <span class="section-count" id="projectCount">0</span></div>
      <div class="nav-list" id="projects"></div>
      <button class="side-add" id="addProject" type="button">
        <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"></path></svg>
        新建项目
      </button>
    </section>
    <section class="nav-section" id="chapterArea" aria-labelledby="chaptersLabel" hidden>
      <div class="section-title" id="chaptersLabel">Chapters <span class="section-count" id="chapterCount">0</span></div>
      <div class="nav-list" id="chapters"></div>
      <button class="side-add" id="addChapter" type="button">
        <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"></path></svg>
        新建 Chapter
      </button>
    </section>
  </aside>
  <main id="main" tabindex="-1" aria-live="polite">
    <div class="empty"><div class="empty-inner">正在载入研究空间…</div></div>
  </main>
</div>

<dialog id="modal" aria-labelledby="modalTitle">
  <form method="dialog" class="editor">
    <div class="dialog-head"><h2 class="dialog-title" id="modalTitle"></h2></div>
    <div class="modal-body" id="modalBody"></div>
    <div class="modal-status" id="modalStatus" role="alert" aria-live="assertive"></div>
    <div class="toolbar dialog-actions">
      <button class="btn" id="modalCancel" value="cancel">取消</button>
      <button class="btn primary" id="modalSave" value="default">保存</button>
    </div>
  </form>
</dialog>
<div class="toast-region" id="toastRegion" aria-live="polite" aria-atomic="true"></div>

<script src="__TRACE_BASE__/assets/markdown-it.min.js"></script>
<script src="__TRACE_BASE__/assets/dagre.min.js"></script>
<script>
/* 本地偏好只有一个 key 前缀。曾经这里还兜底读一次带旧后缀的 key，免得升级把
   开发者存的写入 token 弄丢；§16 已经拍板「现在没有任何已发凭证」，兼容读因此
   只剩下一个违反命名要求的字符串，删掉的代价最多是本机重贴一次 token。 */
function stored(name) {
  return localStorage.getItem('trace.' + name) || '';
}

const S = {
  projects: [],
  project: null,
  chapter: null,
  /* §8 的可选派生视图。它跟着当前项目走，所以和 S.project 一起换，
     不能留着上一个项目的图。 */
  dataflow: null,
  selectedNodeId: null,
  graphZoom: null,   // null = 按栏宽自动适配；人调过之后记住那个数
  workView: stored('workView') || 'graph',
  token: stored('token'),
  actor: stored('actor') || 'human',
  authEnabled: false,
  user: null,
  csrf: ''
};

const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
})[character]);

const ICONS = {
  folder: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 7.5h6l2-2h9v13h-17z"></path></svg>',
  chapter: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 4h12v16H6zM9 8h6M9 12h6M9 16h4"></path></svg>',
  inbox: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16l-1.5 14h-13zM4.8 14h4l1.5 2h3.4l1.5-2h4"></path></svg>',
  plus: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"></path></svg>',
  edit: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m4 20 4.5-1 10-10-3.5-3.5-10 10zM13.8 6.7l3.5 3.5"></path></svg>',
  paperclip: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 12 5-5a3 3 0 0 1 4.2 4.2l-7 7a5 5 0 0 1-7-7l7.3-7.3"></path></svg>',
  message: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h14v11H9l-4 3z"></path></svg>',
  history: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12a8 8 0 1 0 2.3-5.7L4 8.5M4 4v4.5h4.5M12 8v5l3 2"></path></svg>',
  spark: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 1.4 5.6L19 10l-5.6 1.4L12 17l-1.4-5.6L5 10l5.6-1.4zM18 16l.7 2.3L21 19l-2.3.7L18 22l-.7-2.3L15 19l2.3-.7z"></path></svg>',
  database: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><ellipse cx="12" cy="5.5" rx="7.5" ry="3"></ellipse><path d="M4.5 5.5v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-6M4.5 11.5v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-6"></path></svg>',
  arrow: '<svg class="icon tile-arrow" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M14 7l5 5-5 5"></path></svg>',
  chevron: '<svg class="icon chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m7 9 5 5 5-5"></path></svg>',
  file: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h8l4 4v14H6zM14 3v5h5"></path></svg>',
  user: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4"></circle><path d="M4.5 20a7.5 7.5 0 0 1 15 0"></path></svg>',
  device: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="2.5" width="12" height="19" rx="2"></rect><path d="M10 18h4"></path></svg>',
  team: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="8" r="3"></circle><circle cx="17" cy="9" r="2.5"></circle><path d="M3.5 19a5.5 5.5 0 0 1 11 0M14 14.5a4.5 4.5 0 0 1 6.5 4"></path></svg>',
  logout: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M10 5H5v14h5M14 8l4 4-4 4M8 12h10"></path></svg>'
};
const icon = name => ICONS[name] || '';

/* 服务可以挂在一个路径前缀下（trace-server --base-path）。页面里每一个指回本服务的
   地址都要带上它；根部署时它是空串，拼接结果与从前完全一致。 */
const BASE = '__TRACE_BASE__';

/* === markdown renderer (begin) === */
// markdown-it owns Markdown parsing; these rules preserve research-note affordances.
(function(global) {
  const parser = global.markdownit({html:false, linkify:true, breaks:true});
  const esc = parser.utils.escapeHtml;
  function safeHref(value, resolve) {
    let text = String(value == null ? '' : value).replace(/[\u0000-\u001f\u007f]/g, '').trim();
    let probe = text;
    for (let i=0; i<4; i++) probe = parser.utils.unescapeAll(probe);
    try { probe=decodeURIComponent(probe); } catch (_) {}
    probe = probe.replace(/[\u0000-\u0020\u007f]/g, '');
    if (/^(javascript|vbscript|data|livescript|mocha):/i.test(probe)) return '#';
    if (!/^([a-z][a-z0-9+.-]*:|\/\/|\/|#)/i.test(text) && resolve) {
      return safeHref(resolve(text));
    }
    return text;
  }
  // Invalid destinations still render as inert links instead of active schemes.
  parser.validateLink = () => true;
  parser.renderer.rules.link_open = (tokens, idx, options, env, self) => {
    tokens[idx].attrSet('href', safeHref(tokens[idx].attrGet('href'), env.resolve));
    tokens[idx].attrSet('rel', 'noopener noreferrer');
    if(tokens[idx].attrGet('href')!=='#') tokens[idx].attrSet('target','_blank');
    return self.renderToken(tokens, idx, options);
  };
  const image = parser.renderer.rules.image;
  parser.renderer.rules.image = (tokens, idx, options, env, self) => {
    const token=tokens[idx];
    token.attrSet('src', safeHref(token.attrGet('src'), env.resolve));
    token.attrSet('loading', 'lazy'); token.attrSet('class', token.meta?.figure ? 'zoomable' : 'zoomable inline-img');
    return image(tokens, idx, options, env, self) + (token.meta && token.meta.caption
      ? '<figcaption>'+parser.renderInline(token.meta.caption, env)+'</figcaption>' : '');
  };
  parser.renderer.rules.s_open=()=>'<del>';
  parser.renderer.rules.s_close=()=>'</del>';
  parser.renderer.rules.fence=(tokens,idx)=>{
    const token=tokens[idx], language=token.info.trim().split(/\s+/)[0];
    return '<pre class="code"'+(language?' data-lang="'+esc(language)+'"':'')+'><code>'+esc(token.content.replace(/\n$/,''))+'</code></pre>\n';
  };
  parser.renderer.rules.table_open=()=>'<div class="tablewrap"><table>\n';
  parser.renderer.rules.table_close=()=>'</table></div>\n';
  parser.inline.ruler.before('link', 'research_reference', (state, silent) => {
    const match=/^\[\[([\w-]+)\]\]/.exec(state.src.slice(state.pos));
    if (!match) return false;
    if (!silent) {
      const open=state.push('link_open','a',1); open.attrSet('href','#');
      open.attrSet('data-goto',match[1]); open.attrSet('class','wikilink');
      const text=state.push('text','',0); text.content=match[1];
      state.push('link_close','a',-1);
    }
    state.pos+=match[0].length; return true;
  });
  var NUM = "[+\\-\u2212\u00b1\u2213]?(?:\\d[\\d,]*(?:\\.\\d+)?|\\.\\d+)(?:[eE][+\\-\u2212]?\\d+)?%?";
  var PM = "(?:\u00b1|\u2213|\\+\\/-|\\+-)";                       // ± ∓ +/- +-
  var NUMERIC = new RegExp("^" + NUM + "(?:" + PM + NUM + ")?$");
  var NUM_HEAD = new RegExp("^" + NUM);

  function bare(c) { return String(c == null ? "" : c).replace(/[*_`\s]|&nbsp;/g, ""); }

  /* true=是数值，false=不是，null=空位（`-` `—` `/` 这类占位，不参与整列判断） */
  function isNumeric(c) {
    var v = bare(c);
    if (!v || v === "-" || v === "\u2014" || v === "\u2013" || v === "/") return null;
    return NUMERIC.test(v);
  }

  /* 取出「主数值」——底纹条按它算长度，误差项不参与。
     纯前缀的 ± （`±0.5`）按量值取正：那种写法表达的是幅度，不是负数。 */
  function numericValue(c) {
    if (isNumeric(c) !== true) return NaN;
    var m = bare(c).match(NUM_HEAD);
    if (!m) return NaN;
    var v = m[0].replace(/,/g, "").replace(/%$/, "")
      .replace(/^[\u00b1\u2213]/, "").replace(/\u2212/g, "-");
    return parseFloat(v);
  }


  parser.core.ruler.after('inline','research_presentation', state => {
    const tokens=state.tokens;
    let cells=[], column=0, inTable=false;
    const finishTable=()=>{
      const columns=new Map();
      cells.filter(c=>c.tag==='td').forEach(c=>{
        const values=columns.get(c.column)||[]; values.push(isNumeric(c.content)); columns.set(c.column,values);
      });
      cells.forEach(c=>{
        const values=(columns.get(c.column)||[]).filter(v=>v!==null);
        if (values.length && values.every(v=>v)) {
          if (!c.token.attrGet('style')) c.token.attrSet('style','text-align:right');
          const n=numericValue(c.content);
          if(c.tag==='td' && Number.isFinite(n)) c.token.attrSet('data-num',String(n));
        }
        const align=/text-align:(left|center|right)/.exec(c.token.attrGet('style')||'');
        if(align) {
          c.token.attrs=c.token.attrs.filter(([name])=>name!=='style');
          c.token.attrJoin('class','ta-'+align[1]);
        }
      }); cells=[];
    };
    for(let i=0;i<tokens.length;i++) {
      const t=tokens[i];
      if(t.type==='table_open') inTable=true;
      if(t.type==='table_close') {finishTable(); inTable=false;}
      if(t.type==='tr_open') column=0;
      if(inTable && (t.type==='td_open'||t.type==='th_open')) {
        cells.push({token:t,tag:t.tag,column:column++,content:(tokens[i+1]||{}).content||''});
      }
      if(t.type!=='inline') continue;
      const children=t.children||[];
      if(children.length===1 && children[0].type==='image' && tokens[i-1]?.type==='paragraph_open') {
        tokens[i-1].tag='figure'; tokens[i+1].tag='figure';
        children[0].meta={figure:true,caption:children[0].attrGet('title')||children[0].content};
      }
      if(tokens[i-1]?.type==='paragraph_open' && tokens[i-2]?.type==='list_item_open' && children[0]?.type==='text') {
        const match=/^\[([ xX])\]\s+/.exec(children[0].content);
        if(match) {
          children[0].content=children[0].content.slice(match[0].length);
          const check=new state.Token('html_inline','',0);
          check.content='<label class="task"><input type="checkbox" disabled'+(match[1].toLowerCase()==='x'?' checked':'')+'>';
          children.unshift(check);
          const close=new state.Token('html_inline','',0); close.content='</label>'; children.push(close);
          for(let j=i-3;j>=0;j--) {
            if(tokens[j].nesting===1 && /^(bullet|ordered)_list_open$/.test(tokens[j].type)) {
              tokens[j].attrSet('class','tasks'); break;
            }
          }
        }
      }
    }
  });
  global.md={render:(src,opts)=>parser.render(String(src||'').replace(/^\uFEFF/,'').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g,''),opts||{}),
             safeHref,esc,isNumeric,numericValue};
})(globalThis);
/* === markdown renderer (end) === */


function headers(write = false) {
  const result = {'Content-Type': 'application/json', 'X-Trace-Actor': S.actor};
  if (!S.authEnabled && S.token) result.Authorization = 'Bearer ' + S.token;
  if (write && S.csrf) result['X-CSRF-Token'] = S.csrf;
  return result;
}

async function api(path, options = {}) {
  const response = await fetch(BASE + path, {
    ...options,
    headers: {
      ...headers(Boolean(options.method && options.method !== 'GET')),
      ...(options.headers || {})
    }
  });
  const raw = await response.text();
  let value = {};
  if (raw) {
    try { value = JSON.parse(raw); }
    catch { value = {error: raw}; }
  }
  if (response.status === 401 && S.authEnabled) {
    location.href = BASE + '/auth/github/login?return_to=' +
      encodeURIComponent(location.pathname + location.search);
    throw Error('登录已过期，正在重新登录');
  }
  if (!response.ok) throw Error(value.error || value.detail || response.statusText);
  return value;
}

function canWrite() {
  return !S.authEnabled || Boolean(S.user && ['member', 'admin'].includes(S.user.role));
}

/* fmt 的返回值在每一个调用点都是未转义地插进 innerHTML 的。occurred_at / created_at
   这些时间戳是 Recorder 或任何持凭证的机器能写的自由字符串，解析失败时把原文原样
   交回去，等于让团队里每个打开这个项目的人在自己的会话下执行它。
   所以这个函数的每一条返回路径都必须已经转义——包括异常分支。 */
function fmt(value) {
  if (!value) return '';
  const text = String(value);
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return esc(text);
  try {
    return esc(new Intl.DateTimeFormat('zh-CN', {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit'
    }).format(date));
  } catch {
    return esc(text);
  }
}

function file64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1]);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function notify(message, tone = 'error') {
  const toast = document.createElement('div');
  toast.className = 'toast ' + tone;
  toast.textContent = message;
  $('#toastRegion').appendChild(toast);
  setTimeout(() => toast.remove(), 4200);
}

async function withBusy(button, action, busyLabel = '处理中…') {
  const original = button.innerHTML;
  button.disabled = true;
  button.textContent = busyLabel;
  try { return await action(); }
  finally {
    button.disabled = false;
    button.innerHTML = original;
  }
}

function setAccountLabel(label) {
  const button = $('#tokenBtn');
  button.innerHTML = icon('user') + `<span>${esc(label)}</span>`;
  button.setAttribute('aria-label', label);
}

function setModal(title, html, onSave = null) {
  const modal = $('#modal');
  const save = $('#modalSave');
  const cancel = $('#modalCancel');
  $('#modalTitle').textContent = title;
  $('#modalBody').innerHTML = html;
  $('#modalStatus').textContent = '';
  save.hidden = typeof onSave !== 'function';
  cancel.textContent = save.hidden ? '关闭' : '取消';
  save.disabled = false;
  save.textContent = '保存';
  save.onclick = typeof onSave === 'function' ? async event => {
    event.preventDefault();
    $('#modalStatus').textContent = '';
    await withBusy(save, async () => {
      try {
        await onSave();
        modal.close();
      } catch (error) {
        $('#modalStatus').textContent = error.message;
      }
    }, '保存中…');
  } : null;
  if (!modal.open) modal.showModal();
  requestAnimationFrame(() => {
    const target = $('#modalBody').querySelector('input:not([type="hidden"]), textarea, select, button');
    if (target) target.focus();
    else cancel.focus();
  });
}

async function loadProjects() {
  const value = await api('/api/projects');
  S.projects = value.projects;
  if (S.project && !S.projects.some(project => project.id === S.project.id)) {
    S.project = null;
    S.chapter = null;
    S.dataflow = null;
  }
  renderSide();
  if (!S.project) renderMain();
}

/* 数据流单独一条请求，而且失败就当作「这个项目没有 artifact 关系」。
   §8 说没有 artifact 关系的项目仍可完整使用，所以一个可选派生视图取不到
   （旧服务端没有这个端点、或者查询超时）绝不能让整个项目页打不开。 */
async function loadDataflow(projectId) {
  try {
    return await api('/api/projects/' + encodeURIComponent(projectId) + '/dataflow?limit=400');
  } catch {
    return null;
  }
}

async function openProject(id) {
  S.project = await api('/api/projects/' + encodeURIComponent(id));
  S.dataflow = await loadDataflow(id);
  S.chapter = null;
  S.selectedNodeId = null;
  renderSide();
  renderMain();
}

async function refreshProject(chapterId = S.chapter && S.chapter.id) {
  if (!S.project) return;
  S.project = await api('/api/projects/' + encodeURIComponent(S.project.id));
  S.dataflow = await loadDataflow(S.project.id);
  S.chapter = chapterId
    ? S.project.chapters.find(chapter => chapter.id === chapterId) || null
    : null;
  if (S.selectedNodeId) {
    const selected = S.project.nodes.find(node => node.id === S.selectedNodeId);
    if (!selected || (S.chapter && selected.chapter_id !== S.chapter.id)) {
      S.selectedNodeId = null;
    }
  }
  renderSide();
  renderMain();
}

function renderSide() {
  $('#workspaceCount').textContent = `${S.projects.length} 个项目`;
  $('#projectCount').textContent = String(S.projects.length);
  $('#projects').innerHTML = S.projects.map(project => `
    <button class="project ${S.project && S.project.id === project.id ? 'on' : ''}"
      type="button" data-project="${esc(project.id)}" aria-pressed="${S.project && S.project.id === project.id}">
      <span class="nav-copy"><strong>${esc(project.name)}</strong></span>
      <span class="count">${project.node_count || 0}</span>
    </button>
  `).join('');
  $('#addProject').hidden = !canWrite();
  $('#chapterArea').hidden = !S.project;
  if (!S.project) return;
  $('#chapterCount').textContent = String(S.project.chapters.length);
  $('#chapters').innerHTML = S.project.chapters.map(chapter => {
    const nodeCount = S.project.nodes.filter(node => node.chapter_id === chapter.id).length;
    return `
      <button class="chapter ${S.chapter && S.chapter.id === chapter.id ? 'on' : ''}"
        type="button" data-chapter="${esc(chapter.id)}" aria-pressed="${S.chapter && S.chapter.id === chapter.id}">
        <span class="nav-copy"><strong>${esc(chapter.name)}</strong></span>
        <span class="count">${nodeCount}</span>
      </button>
    `;
  }).join('');
  $('#addChapter').hidden = !canWrite();
}

function homeHtml() {
  const projectRows = S.projects.map(project => `
    <button class="project-row" type="button" data-open-project="${esc(project.id)}">
      <span><strong>${esc(project.name)}</strong><small>${project.node_count || 0} 条记录 · ${project.chapter_count || 0} 个 Chapters</small></span>
      ${icon('arrow')}
    </button>
  `).join('');
  return `
    <div class="main-stack">
      <header class="page-head">
        <div><h1>Research Trace</h1><p>项目认识保持简洁可读，完整的 Session 与 Agent 历史留在底层随时可查。</p></div>
        ${canWrite() ? `<button class="btn primary" id="homeAddProject" type="button">${icon('plus')}新建项目</button>` : ''}
      </header>
      <section class="project-list-section">
        <div class="list-head"><h2>项目</h2><span class="meta">${S.projects.length} 个</span></div>
        ${projectRows ? `<div class="simple-projects">${projectRows}</div>` : `
          <div class="empty"><div class="empty-inner"><h2>这里还没有项目</h2><p>创建第一个项目后，Recorder 会把有价值的研究过程放到合适的 Chapter。</p></div></div>
        `}
      </section>
    </div>
  `;
}

function commentHtml(comments, targetType, targetId) {
  const unresolved = comments.filter(comment => comment.kind === 'correction' && !comment.resolved_at).length;
  const entries = comments.map(comment => `
    <div class="comment ${esc(comment.kind)} ${comment.resolved_at ? 'resolved' : ''}">
      <div class="md">${md.render(comment.body || '')}</div>
      <div class="who">${esc(comment.kind)} · ${esc(comment.author_id || comment.author_type)} · ${fmt(comment.created_at)}${comment.resolved_at ? ` · 已处理（${esc(comment.resolved_by || '')}）` : ''}${
        !comment.resolved_at && comment.acknowledged_at
          ? ` · Recorder 已读入（${esc(comment.acknowledged_by || '')}），仍待你确认`
          : ''
      }</div>
      ${/* 了结一条纠正只有人能做：服务端对机器凭证返回 403。以前界面上根本没有这个按钮，
            所以唯一会写 resolved_at 的反而是 Recorder。 */''}
      ${comment.kind === 'correction' && !comment.resolved_at && canWrite()
        ? `<button class="btn" type="button" data-resolve-comment="${esc(comment.id)}">标记为已处理</button>`
        : ''}
    </div>
  `).join('');
  return `
    <details class="comments" ${unresolved ? 'open' : ''}>
      <summary>
        <span class="summary-label">评论与纠正</span>
        <span class="comment-count">${comments.length} 条${unresolved ? ` · ${unresolved} 条待处理` : ''} ${icon('chevron')}</span>
      </summary>
      <div class="comments-content">
        ${entries}
        ${canWrite() ? `
          <details class="comment-compose">
            <summary>添加反馈</summary>
            <div class="comment-form">
              <label class="sr-only" for="commentKind-${esc(targetType)}-${esc(targetId)}">反馈类型</label>
              <select id="commentKind-${esc(targetType)}-${esc(targetId)}" data-comment-kind>
                <option value="comment">评论</option>
                <option value="correction">纠正</option>
                <option value="confirmation">确认</option>
              </select>
              <label class="sr-only" for="commentBody-${esc(targetType)}-${esc(targetId)}">反馈内容</label>
              <input id="commentBody-${esc(targetType)}-${esc(targetId)}" data-comment-body placeholder="直接评论或纠正上面的内容">
              <button class="btn" type="button" data-add-comment data-type="${targetType}" data-id="${esc(targetId)}">添加</button>
            </div>
          </details>
        ` : ''}
      </div>
    </details>
  `;
}

function overviewHtml() {
  const project = S.project;
  const comments = project.comments.filter(comment => comment.target_type === 'overview');
  return `
    <section class="section-card feature">
      <div class="section-head">
        <div class="section-heading"><h2>Overview</h2></div>
        <div class="toolbar">
          <span class="version-badge">v${project.overview_version}</span>
          <button class="btn" type="button" data-history-type="overview" data-history-id="${esc(project.id)}"
            data-history-label="Overview">${icon('history')}修订历史</button>
          ${canWrite() ? `<button class="btn" id="editOverview" type="button">${icon('edit')}编辑</button>` : ''}
        </div>
      </div>
      <div class="body ${project.overview ? 'md' : 'empty-copy'}">${project.overview ? md.render(project.overview) : '尚未形成项目 Overview。'}</div>
      ${commentHtml(comments, 'overview', project.id)}
      ${runsHtml(project.recent_runs || [])}
    </section>
  `;
}

function summaryHtml(chapter) {
  const comments = S.project.comments.filter(
    comment => comment.target_type === 'chapter' && comment.target_id === chapter.id
  );
  return `
    <section class="section-card">
      <div class="section-head">
        <div class="section-heading"><h2>${esc(chapter.name)}</h2></div>
        <div class="toolbar">
          <span class="version-badge">摘要 v${chapter.summary_version}</span>
          <button class="btn" type="button" data-history-type="chapter" data-history-id="${esc(chapter.id)}"
            data-history-label="${esc(chapter.name)}">${icon('history')}修订历史</button>
          ${canWrite() ? `
            <button class="btn" id="editSummary" type="button">${icon('edit')}编辑</button>
          ` : ''}
        </div>
      </div>
      <div class="body ${chapter.summary ? 'md' : 'empty-copy'}">${chapter.summary ? md.render(chapter.summary) : '这一章还没有当前摘要。'}</div>
      ${commentHtml(comments, 'chapter', chapter.id)}
    </section>
  `;
}

function chapterPickerHtml() {
  return '<div class="chapter-hint">从左侧选择一个 Chapter，查看章内记录。</div>';
}

function externalLink(uri,label) {
  try {
    const url=new URL(uri);
    if(!['http:','https:'].includes(url.protocol) || url.username || url.password) return esc(uri);
    return `<a href="${esc(url.href)}" target="_blank" rel="noopener noreferrer">${esc(label||uri)}</a>`;
  } catch {return esc(uri);}
}
function evidenceDownload(id,label) {
  return id ? `<a href="${BASE}/api/attachments/${encodeURIComponent(id)}/content">${esc(label)}</a>` : '';
}
function codeSnapshotHtml(code) {
  return `<div class="meta">代码 <code>${esc(code.commit_hash||'待核实')}</code>
    ${evidenceDownload(code.archive_attachment_id,'下载代码')}
    ${code.entire ? ` · Entire ${esc(code.entire.checkpoint_id)}${code.entire.matches_snapshot===false ? '（基础提交会话）' : ''}` : ''}
    ${evidenceDownload(code.entire_attachment_id,'会话来源')}
    ${!code.archive_attachment_id ? '<span> · 代码文件等待上传</span>' : ''}</div>`;
}
function runsHtml(runs, standalone=[]) {
  if(!runs.length && !standalone.length) return '';
  const states={PREPARED:'已准备',SUBMITTING:'正在提交',SUBMISSION_UNKNOWN:'提交结果待核实',SUBMISSION_FAILED:'提交失败',
    QUEUED:'排队中',PENDING:'排队中',RUNNING:'运行中',COMPLETED:'已完成',FAILED:'失败',CANCELLED:'已取消',TIMEOUT:'超时',OUT_OF_MEMORY:'内存不足'};
  return `<details class="comments"><summary>运行与代码证据 · ${runs.length} 次运行${standalone.length ? ' · '+standalone.length+' 个代码版本' : ''}</summary>
    ${runs.map(run=>`<section class="code-evidence"><strong>${esc(run.name||run.id)}</strong> · ${esc(states[run.state]||run.state)}
      ${run.job_id ? ' · SLURM '+esc(run.job_id) : ''}
      ${run.replay_of ? ' · 复现自 '+esc(run.replay_of.slice(0,12)) : ''}
      ${run.wandb_url ? ' · '+externalLink(run.wandb_url,'查看 W&B 曲线') : ''}
      ${codeSnapshotHtml(run.snapshot||{})}
      <pre>${esc((run.command||[]).join(' '))}</pre>
      <div class="meta">${run.exit_code!==undefined ? '退出码 '+esc(run.exit_code)+' · ' : ''}
        ${evidenceDownload(run.stdout_attachment_id,run.stdout_truncated?'标准输出（末尾 1 MiB）':'标准输出')}
        ${evidenceDownload(run.stderr_attachment_id,run.stderr_truncated?'错误输出（末尾 1 MiB）':'错误输出')}</div>
      ${run.error ? `<div class="danger">${esc(run.error)}</div>` : ''}
      <details><summary>配置、环境与数据引用</summary><pre>${esc(JSON.stringify({command:run.command,environment:run.environment,data:run.data,source_directory:run.source_dir,output_directory:run.stdout},null,2))}</pre></details>
    </section>`).join('')}
    ${standalone.map(codeSnapshotHtml).join('')}</details>`;
}

function nodeHtml(node) {
  const comments = node.comments || [];
  const [reviewClass, reviewLabel] = nodeReview(node);
  const directionLabels = {input: '输入', output: '输出', reference: '参考'};
  const codes = (node.code_evidence || []).map(evidence => `
    <div class="code">
      <div class="code-head">${esc(evidence.file_path)}${evidence.symbol ? ' · ' + esc(evidence.symbol) : ''}${evidence.commit_hash ? ' @ ' + esc(evidence.commit_hash.slice(0, 10)) : ''} · ${esc(evidence.attribution)}</div>
      ${evidence.snippet ? `<pre>${esc(evidence.snippet)}</pre>` : ''}
      ${evidence.diff ? `<pre>${esc(evidence.diff)}</pre>` : ''}
      ${evidence.annotation ? `<div class="code-annotation">${esc(evidence.annotation)}</div>` : ''}
    </div>
  `).join('');
  const artifacts = (node.attachments || []).map(artifact => `
    <div class="artifact">
      ${icon('file')}
      <div><strong>${esc(directionLabels[artifact.direction] || artifact.direction)}</strong> ·
        ${artifact.object_path ? `<a href="${BASE}/api/attachments/${encodeURIComponent(artifact.id)}/content">${esc(artifact.name)}</a>` : esc(artifact.name)}
        ${artifact.external_path ? ' · ' + esc(artifact.machine || '') + ':' + esc(artifact.external_path) : ''}
        ${artifact.uri ? ' · ' + externalLink(artifact.uri, '打开来源') : ''}
        ${artifact.metadata && artifact.metadata.provider === 'mlflow' ? `<div class="meta">MLflow ${esc(artifact.metadata.kind)} · ${esc(artifact.metadata.external_id)} · 已保存证据快照</div>` : ''}
      </div>
    </div>
  `).join('');
  return `
    <article class="node">
      <div class="node-card">
        <div class="meta"><time datetime="${esc(node.occurred_at)}">${fmt(node.occurred_at)}</time>${node.parent_id ? ' · 延续 ' + esc(node.parent_id) : ''}</div>
        <div class="chapter-head">
          <h3>${esc(node.title)}</h3>
          <div class="toolbar node-actions">
            <button class="btn" type="button" data-history-type="node" data-history-id="${esc(node.id)}"
              data-history-label="${esc(node.title)}">${icon('history')}修订历史</button>
            <button class="btn" type="button" data-raw-node="${esc(node.id)}">${icon('database')}原始历史</button>
            ${canWrite() ? `
              <button class="btn" type="button" data-edit-node="${esc(node.id)}">${icon('edit')}编辑</button>
              <button class="btn" type="button" data-attach-node="${esc(node.id)}">${icon('paperclip')}附件 / 产物</button>
            ` : ''}
          </div>
        </div>
        <div>
          ${(node.labels || []).map(label => `<span class="pill">${esc(label)}</span>`).join('')}
          <span class="pill muted ${reviewClass}">${esc(reviewLabel)}</span>
        </div>
        <div class="body md">${md.render(node.body || '')}</div>
        ${runsHtml(node.runs || [], node.code_snapshots || [])}
        ${codes}
        ${artifacts}
        ${commentHtml(comments, 'node', node.id)}
      </div>
    </article>
  `;
}

function nodeOrder(left, right) {
  const byTime = String(left.occurred_at || '').localeCompare(String(right.occurred_at || ''));
  return byTime || String(left.id).localeCompare(String(right.id));
}

function nodeReview(node) {
  const labels = {
    confirmed: ['confirmed', '已确认'],
    corrected: ['corrected', '已纠正'],
    unreviewed: ['unreviewed', '未确认']
  };
  return labels[node.review_state] || ['unreviewed', node.review_state || '未确认'];
}

function parentOptionsHtml(chapterId, selectedId = '', excludeId = '') {
  const candidates = S.project.nodes
    .filter(node => node.chapter_id === chapterId && node.id !== excludeId)
    .sort(nodeOrder);
  return [
    `<option value="" ${selectedId ? '' : 'selected'}>独立探索或前序关系待核实</option>`,
    ...candidates.map(node =>
      `<option value="${esc(node.id)}" ${node.id === selectedId ? 'selected' : ''}>${esc(node.title)}</option>`
    )
  ].join('');
}

/* Dagre owns layered graph placement. Only explicitly supported parent/artifact edges are drawn. */
const TREE_NODE_W = 176;
/* v1 写的是 58，但 58 装不下「一行元信息 + 两行标题」：6+17+3+35.5+6 = 67.5，
   差的那 10px 会被卡片的 overflow:hidden 从第二行中间切开——半个字比没有字更难读。
   宽度照搬 v1，高度按内容实测取整。 */
const TREE_NODE_H = 68;
const TREE_H_GAP = 20;    // 同层相邻
const TREE_V_GAP = 38;    // 层与层
const TREE_SIBLING_GAP = 56;  // 两棵树之间
const TREE_PAD = 24;

function dagreLayout(inputNodes, edges, cardWidth, cardHeight, gapX, gapY, padding) {
  const nodes=[...inputNodes].sort(nodeOrder);
  const graph=new dagre.graphlib.Graph().setGraph({rankdir:'TB',nodesep:gapX,ranksep:gapY,marginx:padding,marginy:padding});
  graph.setDefaultEdgeLabel(()=>({}));
  nodes.forEach(node=>graph.setNode(node.id,{width:cardWidth,height:cardHeight}));
  edges.forEach(([from,to])=>{if(from!==to && graph.hasNode(from) && graph.hasNode(to)) graph.setEdge(from,to);});
  dagre.layout(graph);
  const ranks=[...new Set(nodes.map(node=>graph.node(node.id).y))].sort((a,b)=>a-b);
  const positions={};
  nodes.forEach(node=>{const p=graph.node(node.id); positions[node.id]={left:p.x-cardWidth/2,top:p.y-cardHeight/2,depth:ranks.indexOf(p.y)};});
  return {nodes,positions,cardWidth,cardHeight,width:Math.max(cardWidth+2*padding,graph.graph().width||0),height:Math.max(cardHeight+2*padding,graph.graph().height||0)};
}
function layoutGraphNodes(nodes) {
  return dagreLayout(nodes,nodes.filter(n=>n.parent_id).map(n=>[n.parent_id,n.id]),TREE_NODE_W,TREE_NODE_H,TREE_H_GAP,TREE_V_GAP,TREE_PAD);
}

/* 一条记录在图上用哪套线型。有没解决的纠正压过一切：那是「这里还有事没完」，
   比「已确认 / 未确认」更该先被看见。 */
function graphState(node) {
  const unresolved = (node.comments || []).some(comment => comment.kind === 'correction' && !comment.resolved_at);
  return unresolved ? 'needs-review' : nodeReview(node)[0];
}

function graphSectionHtml(chapter, nodes, showChapter = false) {
  const ordered = [...nodes].sort(nodeOrder);
  const head = showChapter
    ? `<button class="chapter-map-title" type="button" data-focus-chapter="${esc(chapter.id)}">${esc(chapter.name)}<span>${ordered.length} 条</span></button>`
    : '';
  if (!ordered.length) return `
    <section class="chapter-map graph-map ${showChapter ? 'grouped' : ''}">
      ${head}
      <div class="structure-empty">这个 Chapter 还没有记录。</div>
    </section>
  `;
  const layout = layoutGraphNodes(ordered);
  const ordinal = new Map(ordered.map((node, index) => [node.id, String(index + 1).padStart(2, '0')]));
  const edges = ordered.map(node => {
    const child = layout.positions[node.id];
    const parent = node.parent_id && layout.positions[node.parent_id];
    if (!parent) return '';
    const x1 = parent.left + layout.cardWidth / 2;
    const y1 = parent.top + layout.cardHeight;
    const x2 = child.left + layout.cardWidth / 2;
    const y2 = child.top;
    const middle = y1 + (y2 - y1) / 2;
    /* 线型跟着**子**节点走：一条边说的是「这一步接着那一步」，
       而可信不可信是这一步自己的事，不是那条关系的事。 */
    return `<path class="tree-edge ${graphState(node)}" d="M ${x1} ${y1} V ${middle} H ${x2} V ${y2}"/>`;
  }).join('');
  const cards = ordered.map(node => {
    const position = layout.positions[node.id];
    const [reviewClass, reviewLabel] = nodeReview(node);
    const selected = S.selectedNodeId === node.id;
    const state = graphState(node);
    return `
      <button class="graph-node ${state} ${selected ? 'selected' : ''}" type="button"
        data-select-node="${esc(node.id)}" aria-pressed="${selected}"
        title="${esc(node.title)}"
        aria-label="记录 ${ordinal.get(node.id)}：${esc(node.title)}，${esc(reviewLabel)}${state === 'needs-review' ? '，有待处理的纠正' : ''}"
        style="left:${position.left}px;top:${position.top}px;width:${layout.cardWidth}px;height:${layout.cardHeight}px">
        <span class="graph-node-meta"><span class="graph-idx">${ordinal.get(node.id)}</span><span class="graph-node-state">${esc(reviewLabel)}</span><time>${fmt(node.occurred_at)}</time></span>
        <span class="graph-node-title">${esc(node.title)}</span>
      </button>
    `;
  }).join('');
  return `
    <section class="chapter-map graph-map ${showChapter ? 'grouped' : ''}">
      ${head}
      <div class="graph-viewport">
        <div class="graph-zoomwrap">
          <div class="graph-canvas" style="width:${layout.width}px;height:${layout.height}px">
            <svg class="graph-edges" viewBox="0 0 ${layout.width} ${layout.height}" width="${layout.width}" height="${layout.height}" aria-hidden="true">${edges}</svg>
            ${cards}
          </div>
        </div>
      </div>
    </section>
  `;
}

/* 列表装订线的行高必须和 .record-row 的实际行高一致，否则点会一行行地错开。
   两边引用同一个常量，别让 CSS 和 JS 各写一个数。 */
const RAIL_ROW_HEIGHT = 58;

/* === rail lanes (begin) === */
function railLanes(ordered) {
  const index = new Map(ordered.map((node, i) => [node.id, i]));
  const pending = new Map(ordered.map(node => [node.id, 0]));
  ordered.forEach(node => {
    if (node.parent_id && pending.has(node.parent_id)) {
      pending.set(node.parent_id, pending.get(node.parent_id) + 1);
    }
  });

  const occupant = [];      // 每条车道当前的前沿节点
  const debt = [];          // 这条车道上还有几个节点等着长孩子
  const lane = new Map();
  const edges = [];

  ordered.forEach((node, row) => {
    const parentId = node.parent_id && index.has(node.parent_id) ? node.parent_id : null;
    // 父节点必须**已经排在前面**才算数：排序键是 (occurred_at, id)，同一毫秒内由随机 id
    // 决定先后，所以子节点完全可能排在父节点前面。那种情况下这一行按新起点画，不画边 ——
    // 一条指向「还没出现的行」的线只会更难懂。
    const parent = parentId !== null && index.get(parentId) < row && lane.has(parentId)
      ? parentId : null;

    let column = -1;
    if (parent !== null) {
      const parentLane = lane.get(parent);
      // 父节点还占着自己那条车道时，第一个孩子直接续上去；后面的孩子另开一条。
      if (occupant[parentLane] === parent) column = parentLane;
    }
    if (column < 0) {
      column = occupant.findIndex(item => item === null || item === undefined);
      if (column < 0) column = occupant.length;
    }

    lane.set(node.id, column);
    occupant[column] = node.id;
    debt[column] = debt[column] || 0;
    if (pending.get(node.id) > 0) debt[column] += 1;
    if (parent !== null) {
      edges.push({from: index.get(parent), to: row, fromLane: lane.get(parent), toLane: column});
      pending.set(parent, pending.get(parent) - 1);
      if (pending.get(parent) === 0) debt[lane.get(parent)] -= 1;
    }

    // 一条车道只有在「上面再没有节点等着长孩子」时才能让出去。只看当前这个节点是不是
    // 叶子是不够的：父节点还有别的孩子没排上来时，那个孩子要从这条车道分出去，
    // 中间这段是被占着的。放早了，后来的边会直接穿过已经画好的点，看起来像一条直链。
    occupant.forEach((_, i) => { if (!debt[i]) occupant[i] = null; });
  });

  return {lane, edges, width: Math.max(1, occupant.length)};
}
/* === rail lanes (end) === */

function railSvg(ordered, rowHeight) {
  const {lane, edges, width} = railLanes(ordered);
  const laneWidth = 13;
  const svgWidth = width * laneWidth + 6;
  const x = column => 6 + column * laneWidth;
  const y = row => row * rowHeight + rowHeight / 2;
  const parts = edges.map(edge => {
    const x1 = x(edge.fromLane), x2 = x(edge.toLane);
    const y1 = y(edge.from), y2 = y(edge.to);
    if (x1 === x2) return `<path class="rail-edge" d="M${x1} ${y1}V${y2}"/>`;
    // 换道时走一个圆角肘弯，和 git graph 里并线的形状一致，不用另外教人认。
    const radius = Math.min(laneWidth, rowHeight / 2);
    const dir = x2 > x1 ? 1 : -1;
    return `<path class="rail-edge" d="M${x1} ${y1}V${y2 - radius}Q${x1} ${y2} ${x1 + dir * radius} ${y2}H${x2}"/>`;
  });
  const dots = ordered.map((node, row) => {
    const [reviewClass] = nodeReview(node);
    return `<circle class="rail-dot ${reviewClass}" cx="${x(lane.get(node.id))}" cy="${y(row)}" r="3.4"/>`;
  });
  return `<svg class="rail" width="${svgWidth}" height="${ordered.length * rowHeight}"
    viewBox="0 0 ${svgWidth} ${ordered.length * rowHeight}" aria-hidden="true">${parts.join('')}${dots.join('')}</svg>`;
}

function listSectionHtml(chapter, nodes, showChapter = false) {
  const ordered = [...nodes].sort(nodeOrder);
  const ordinal = new Map(ordered.map((node, index) => [node.id, String(index + 1).padStart(2, '0')]));
  return `
    <section class="chapter-map record-group ${showChapter ? 'grouped' : ''}">
      ${showChapter ? `<button class="chapter-map-title" type="button" data-focus-chapter="${esc(chapter.id)}">${esc(chapter.name)}<span>${ordered.length} 条</span></button>` : ''}
      ${ordered.length ? `<div class="record-rail"><div class="record-rail-gutter">${railSvg(ordered, RAIL_ROW_HEIGHT)}</div><div class="record-list">${ordered.map((node, index) => {
        const [reviewClass, reviewLabel] = nodeReview(node);
        const selected = S.selectedNodeId === node.id;
        return `
          <button class="record-row ${reviewClass} ${selected ? 'selected' : ''}" type="button"
            data-select-node="${esc(node.id)}" aria-pressed="${selected}">
            <span class="record-index">${String(index + 1).padStart(2, '0')}</span>
            <span class="record-copy"><strong>${esc(node.title)}</strong><small>${node.parent_id && ordinal.has(node.parent_id) ? '延续记录 ' + ordinal.get(node.parent_id) : '新的起点'} · ${fmt(node.occurred_at)}</small></span>
            <span class="record-state">${esc(reviewLabel)}</span>
          </button>
        `;
      }).join('')}</div></div>` : '<div class="structure-empty">这个 Chapter 还没有记录。</div>'}
    </section>
  `;
}

/* §10：数据流只在存在明确 artifact 关系时显示。一个恒空的面板是纯噪声，
   所以「有没有这个视图」由有没有边决定，而不是由服务端答没答应。
   注意判据是 edges 而不是 nodes：存储层只把参与了边的 Node 放进 nodes[]，
   两者本该同进同退，但空图必须是「整块不出现」这一条不依赖那个巧合。 */
function dataflowAvailable() {
  return Boolean(S.dataflow && (S.dataflow.edges || []).length);
}

function effectiveWorkView() {
  /* workView 存在 localStorage 里，会跨项目带过来。上一个项目选了数据流、
     下一个项目没有 artifact 关系时必须退回结构图，否则就是一块空白。 */
  if (S.workView === 'dataflow') return dataflowAvailable() ? 'dataflow' : 'graph';
  return S.workView === 'list' ? 'list' : 'graph';
}

/* 返回值已经转义。这里和 fmt 是同一课：key_kind 来自数据库，未知取值会被原样
   带出来，而每个调用点都是插进 innerHTML 的。 */
function dataflowKeyLabel(kind) {
  const labels = {
    sha256: '同一份内容（sha256）',
    uri: '同一个位置（uri）',
    path: '同一个位置（机器 + 绝对路径）'
  };
  return esc(labels[kind] || kind || '未知依据');
}

/* 数据流的分层布局。和 layoutGraphNodes 画的**不是**一回事：那边的边是 Node 上
   明确写下的 parent，只在 Chapter 内部；这边的边来自两个 Node 登记了同一个
   artifact 键，因此可以跨 Chapter（消融吃主实验的产物）。跨的是 Node 之间的
   产物关系，Chapter 本身依旧互不相连——所以这个视图里没有任何 Chapter 容器，
   Chapter 只作为节点卡片上的一行标签出现。
   存储层允许出现环（它只做一次键 join，不按时间过滤方向），深度计算因此必须
   自带环保护：一条 A→B→A 不能让页面转不出来。 */
/* 数据流和结构图共用 .graph-node 的**外观**，但尺寸各算各的：结构图的卡片要密（一屏
   装下整棵树），数据流的卡片只有几个、要看得清产物名字。所以两边都把宽高**内联**写在
   卡片上，由各自的布局函数说了算 —— 让 CSS 定死一个尺寸，另一张图的边就会落在卡片外面。 */
function layoutDataflowNodes(nodes,edges) {
  return dagreLayout(nodes,edges.map(e=>[e.from_node_id,e.to_node_id]),240,112,26,62,20);
}

function dataflowSectionHtml() {
  if (!dataflowAvailable()) return '';
  const flow = S.dataflow;
  const nodes = flow.nodes || [];
  const layout = layoutDataflowNodes(nodes, flow.edges || []);
  const known = new Set(nodes.map(node => node.id));
  const edges = (flow.edges || []).filter(edge => known.has(edge.from_node_id) && known.has(edge.to_node_id));
  const ordinal = new Map(layout.nodes.map((node, index) => [node.id, String(index + 1).padStart(2, '0')]));
  const chapters = new Map((S.project.chapters || []).map(chapter => [chapter.id, chapter.name]));

  /* 相邻两层之间的边，横向拐点落在两层之间的空隙里，永远不会压到任何卡片。
     跨层的边（包括环上的回边）不行：同一列上它就是一条直接从中间那些节点身上
     碾过去的竖线。所以跨层的边一律绕到画布右侧，每条占一条自己的通道。 */
  const laneGap = 18;
  const spanning = edges.filter(edge =>
    layout.positions[edge.to_node_id].depth !== layout.positions[edge.from_node_id].depth + 1
  );
  const laneOf = new Map(spanning.map((edge, index) => [edge, layout.width + 2 + index * laneGap]));
  const canvasWidth = spanning.length ? layout.width + spanning.length * laneGap + 10 : layout.width;
  const outgoing = new Map();

  const paths = edges.map(edge => {
    const from = layout.positions[edge.from_node_id];
    const to = layout.positions[edge.to_node_id];
    const x1 = from.left + layout.cardWidth / 2;
    const y1 = from.top + layout.cardHeight;
    const x2 = to.left + layout.cardWidth / 2;
    const y2 = to.top;
    const lane = laneOf.get(edge);
    const back = to.depth <= from.depth;
    const d = lane === undefined
      ? `M ${x1} ${y1} V ${y1 + (y2 - y1) / 2} H ${x2} V ${y2}`
      : `M ${x1} ${y1} V ${y1 + 16} H ${lane} V ${y2 - 16} H ${x2} V ${y2}`;
    /* 每条边都要说清凭什么连的，否则读者没法判断这张图可不可信——§8 的全部立场
       就是「只画登记过的，不猜」。短标签贴在产物**离开生产者**的那一头（同一个
       生产者的多条边依次往下排，不会互相盖住），完整的键在下面的依据清单里。 */
    const stack = outgoing.get(edge.from_node_id) || 0;
    outgoing.set(edge.from_node_id, stack + 1);
    const shortKey = String(edge.key || '');
    const label = `${edge.key_kind || '?'} ${shortKey.length > 14 ? shortKey.slice(0, 12) + '…' : shortKey}`;
    return `
        <path class="flow-edge ${back ? 'back' : ''}" d="${d}"></path>
        <text class="flow-edge-label" x="${x1}" y="${y1 + 13 + stack * 11}" text-anchor="middle">${esc(label)}</text>
    `;
  }).join('');

  const cards = layout.nodes.map(node => {
    const position = layout.positions[node.id];
    const selected = S.selectedNodeId === node.id;
    const chapter = chapters.get(node.chapter_id) || '未知 Chapter';
    return `
      <button class="graph-node flow-node ${selected ? 'selected' : ''}" type="button"
        data-select-node="${esc(node.id)}" aria-pressed="${selected}"
        aria-label="数据流节点 ${ordinal.get(node.id)}：${esc(node.title)}，属于 ${esc(chapter)}"
        data-title="${esc(node.title)}"
        title="${esc(node.title)}"
        style="left:${position.left}px;top:${position.top}px;width:${layout.cardWidth}px;height:${layout.cardHeight}px">
        <span class="graph-idx">${ordinal.get(node.id)}</span>
        <strong>${esc(node.title)}</strong>
      </button>
    `;
  }).join('');

  const evidence = edges.map(edge => `
      <li>
        <span class="flow-evidence-pair">${ordinal.get(edge.from_node_id)} → ${ordinal.get(edge.to_node_id)}</span>
        <span>${dataflowKeyLabel(edge.key_kind)}</span>
        <code>${esc(edge.key)}</code>
        ${edge.name ? `<span class="meta">${esc(edge.name)}</span>` : ''}
      </li>
  `).join('');

  const stats = flow.stats || {};
  /* 位置相同不等于内容相同：重跑一次覆盖掉 latest.ckpt 会给出同一个路径键但不同的
     字节。把 sha256 和 uri/path 混成一个匿名的「相同」就是在藏这件事。 */
  const located = edges.some(edge => edge.key_kind !== 'sha256');
  const notes = [
    '边只来自 Node 上明确登记的 input / output artifact 键，不从自然语言推断生产者和消费者。',
    '边可以跨 Chapter；Chapter 之间仍然没有任何时间、父子或 pipeline 顺序。',
    located ? '按位置（uri / 机器+路径）连的边只说明两次登记指向同一个位置，后一次运行可能已经覆盖了内容。' : '',
    stats.truncated ? `关系太多，这里只画了前 ${esc(edges.length)} 条（共 ${esc(stats.edges ?? '—')} 条）。` : '',
    Number(stats.unkeyed || 0) > 0
      ? `另有 ${esc(stats.unkeyed)} 个登记的产物没有可比对的键（缺 sha256、缺带 scheme 的绝对 uri、或缺机器 + 绝对路径），它们不出现在这张图里。`
      : '',
    Number(stats.unlabeled_direction || 0) > 0
      ? `另有 ${esc(stats.unlabeled_direction)} 个产物的 direction 仍是默认的 reference：登记它的人没有声明流向，因此两端都不参与连边。`
      : ''
  ].filter(Boolean);

  return `
    <section class="chapter-map flow-map">
      <div class="graph-viewport">
        <div class="graph-canvas" style="width:${canvasWidth}px;height:${layout.height}px">
          <svg class="graph-edges" viewBox="0 0 ${canvasWidth} ${layout.height}" width="${canvasWidth}" height="${layout.height}" aria-hidden="true">
            <defs>
              <marker id="flowArrow" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto">
                <path d="M0 0 L6 3 L0 6 z" fill="currentColor" stroke="none"></path>
              </marker>
            </defs>
            ${paths}
          </svg>
          ${cards}
        </div>
      </div>
      <div class="flow-note">${notes.join('<br>')}</div>
      <ul class="flow-evidence" aria-label="每条边的连接依据">${evidence}</ul>
    </section>
  `;
}

/* 缩放。没有它，一棵 14 层的树在 900px 的视口里要滚三屏才看得完，
   而「一眼看出形状」正是这张图存在的全部理由——滚着看等于回到列表。
   S.graphZoom 为 null 时按栏宽自动适配；人一旦手动调过就记住他的选择。 */
const GRAPH_ZOOM_MIN = 0.35;
const GRAPH_ZOOM_MAX = 2;

/* 自动档是按栏宽算的，窗口一变就得重算；只挂一次，别每次渲染都再挂一个。 */
let graphResizeBound = false;
function bindGraphResize() {
  if (graphResizeBound) return;
  graphResizeBound = true;
  window.addEventListener('resize', () => applyGraphZoom());
}

/* 当前倍数以画布上真正生效的那个为准：自动档下 S.graphZoom 还是 null，
   而人按「放大」想要的是「比现在大一点」，不是「比 1 大一点」。 */
function currentGraphZoom() {
  const canvas = document.querySelector('.graph-canvas');
  const scale = canvas && canvas.style.transform.match(/scale\(([\d.]+)\)/);
  return scale ? parseFloat(scale[1]) : 1;
}

function applyGraphZoom() {
  document.querySelectorAll('.graph-zoomwrap').forEach(wrap => {
    const canvas = wrap.querySelector('.graph-canvas');
    if (!canvas) return;
    const width = parseFloat(canvas.style.width) || 1;
    const height = parseFloat(canvas.style.height) || 1;
    const room = wrap.parentElement.clientWidth || width;
    // 自动档只缩不放：一棵窄树没必要被拉大到糊掉。
    const auto = Math.max(GRAPH_ZOOM_MIN, Math.min(1, room / width));
    const zoom = S.graphZoom === null ? auto : S.graphZoom;
    canvas.style.transform = zoom === 1 ? '' : `scale(${zoom})`;
    wrap.style.width = Math.ceil(width * zoom) + 'px';
    wrap.style.height = Math.ceil(height * zoom) + 'px';
    const label = document.querySelector('[data-graph-zoom-value]');
    if (label) label.textContent = Math.round(zoom * 100) + '%';
  });
}

function structureContentHtml() {
  if (effectiveWorkView() === 'dataflow') return dataflowSectionHtml();
  const populated = S.project.chapters.filter(chapter =>
    S.project.nodes.some(node => node.chapter_id === chapter.id)
  );
  const chapters = S.chapter ? [S.chapter] : (populated.length ? populated : S.project.chapters);
  const showChapter = !S.chapter;
  const renderer = effectiveWorkView() === 'list' ? listSectionHtml : graphSectionHtml;
  return chapters.map(chapter => renderer(
    chapter,
    S.project.nodes.filter(node => node.chapter_id === chapter.id),
    showChapter
  )).join('') || '<div class="structure-empty">这个项目还没有 Chapter。</div>';
}

function detailContentHtml() {
  const selected = S.selectedNodeId && S.project.nodes.find(node => node.id === S.selectedNodeId);
  if (selected) {
    const chapter = S.project.chapters.find(item => item.id === selected.chapter_id);
    return `
      <div class="detail-context">
        <button type="button" data-clear-node>${S.chapter ? 'Chapter 摘要' : '项目 Overview'}</button>
        <span>/</span><span>${esc(chapter ? chapter.name : 'Unknown Chapter')}</span>
      </div>
      <div class="record-detail">${nodeHtml(selected)}</div>
      ${rawHistoryHtml()}
    `;
  }
  /* 原始 timeline 在每种详情下都必须留一个入口。以前它只挂在"项目 Overview"那一支，
     选中 Chapter 或某条记录之后整段原始历史就再也点不到了。 */
  if (S.chapter) return summaryHtml(S.chapter) + rawHistoryHtml();
  return overviewHtml() + rawHistoryHtml();
}

function workspaceHtml() {
  const nodeCount = S.chapter
    ? S.project.nodes.filter(node => node.chapter_id === S.chapter.id).length
    : S.project.nodes.length;
  const view = effectiveWorkView();
  const flow = S.dataflow || {};
  const flowStats = flow.stats || {};
  const viewHint = {
    graph: '连线仅表示明确的 parent 关系',
    list: '按发生时间排列',
    dataflow: '连线只来自登记过的 artifact 键，可跨 Chapter；Chapter 之间仍无顺序'
  }[view];
  /* 图是空的可以是「这个项目没有产物」（§8 说这完全正常），也可以是一个可修的缺口。
     缺口有两种，必须分别说出来，否则它们和「没有产物」长得一模一样：
       * 给了方向但没给可比对的键；
       * 键给对了，但 direction 停在默认值 reference —— reference 两边都不参与
         join，所以图照样是空的。这一种更常见：键要主动写错，方向只要不写就错。 */
  const gaps = view !== 'dataflow' && !dataflowAvailable() ? [
    Number(flowStats.unkeyed || 0) > 0
      ? `${esc(flowStats.unkeyed)} 个产物登记时没有可比对的键，数据流连不出边` : '',
    Number(flowStats.unlabeled_direction || 0) > 0
      ? `${esc(flowStats.unlabeled_direction)} 个产物的 direction 仍是默认的 reference，不声明流向就不参与数据流` : ''
  ].filter(Boolean) : [];
  const unkeyedHint = gaps.map(item => `<span>${item}</span>`).join('');
  const chapterOptions = [
    `<option value="" ${S.chapter ? '' : 'selected'}>全部章节（无先后）</option>`,
    ...S.project.chapters.map(chapter =>
      `<option value="${esc(chapter.id)}" ${S.chapter && S.chapter.id === chapter.id ? 'selected' : ''}>${esc(chapter.name)}</option>`
    )
  ].join('');
  return `
    <div class="workspace-page">
      ${projectHeaderHtml()}
      <div class="workspace-body">
        <section class="structure-pane" aria-labelledby="structureTitle">
          <header class="pane-head">
            <div class="pane-heading">
              <span class="pane-kicker" id="structureTitle">结构</span>
              <label class="sr-only" for="chapterScope">查看范围</label>
              <select class="scope-select" id="chapterScope" aria-label="查看范围">${chapterOptions}</select>
            </div>
            <div class="view-switch" role="group" aria-label="结构呈现方式">
              <button type="button" data-work-view="graph" aria-pressed="${view === 'graph'}">结构图</button>
              <button type="button" data-work-view="list" aria-pressed="${view === 'list'}">记录列表</button>
              ${dataflowAvailable() ? `<button type="button" data-work-view="dataflow" aria-pressed="${view === 'dataflow'}">数据流</button>` : ''}
            </div>
          </header>
          <div class="structure-subhead">
            <span>${view === 'dataflow' ? `${esc((flow.edges || []).length)} 条产物关系` : `${nodeCount} 条记录`}</span>
            <span>${viewHint}</span>
            ${unkeyedHint}
            ${S.chapter && canWrite() ? `<button class="inline-add" id="addNode" type="button">${icon('plus')}添加记录</button>` : ''}
            ${view === 'graph' && nodeCount ? `
              <span class="graph-zoom" role="group" aria-label="结构图缩放">
                <button type="button" data-graph-zoom="out" aria-label="缩小">−</button>
                <output data-graph-zoom-value aria-live="off">100%</output>
                <button type="button" data-graph-zoom="in" aria-label="放大">+</button>
                <button type="button" data-graph-zoom="fit" aria-label="适应栏宽">适宽</button>
              </span>` : ''}
          </div>
          <div class="structure-scroll" id="structureScroll">${structureContentHtml()}</div>
        </section>
        <section class="record-pane" id="recordPane" tabindex="-1" aria-label="记录详情">
          ${detailContentHtml()}
        </section>
      </div>
    </div>
  `;
}

function rawHistoryHtml() {
  return `
    <details class="card raw-card" id="rawHistory">
      <summary>
        <span class="summary-label">原始 Session / Agent 历史</span>
        <span class="comment-count">按需加载 ${icon('chevron')}</span>
      </summary>
      <div class="raw-list" id="rawItems">
        <div class="meta">展开后读取最近记录；完整内容仍可全文搜索。</div>
      </div>
    </details>
  `;
}

/* 原始历史此前是把 payload 直接 JSON.stringify 倒进 <pre>。那等于把「有没有存下来」
   和「读不读得懂」混为一谈：东西确实都在，但没人翻得动。

   每种事件其实都有一个真正承载信息的字段（你说的话、跑的命令、命令的输出、这一轮的
   回答）。把那个字段挑出来当摘要，原始 JSON 收进 <details> —— 需要逐字核对时它一直在，
   只是不再挡在阅读的路上。 */
function briefText(value, limit = 220) {
  const text = String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
  return text.length > limit ? text.slice(0, limit) + '…' : text;
}

function toolBrief(input) {
  if (!input || typeof input !== 'object') return briefText(input);
  // 命令和路径是「这一步到底做了什么」的答案；其余字段是参数，收进 details 就行。
  for (const key of ['command', 'file_path', 'path', 'pattern', 'query', 'description', 'prompt']) {
    if (input[key]) return briefText(input[key]);
  }
  return briefText(JSON.stringify(input));
}

function resultBrief(response) {
  if (response == null) return '';
  if (typeof response !== 'object') return briefText(response);
  if (response.interrupted) return '被中断';
  const out = String(response.stdout || '').trim();
  const err = String(response.stderr || '').trim();
  if (out) return briefText(out);
  if (err) return briefText(err);
  return briefText(JSON.stringify(response));
}

function durationLabel(ms) {
  const value = Number(ms);
  if (!isFinite(value) || value <= 0) return '';
  return value >= 1000 ? (value / 1000).toFixed(1) + ' s' : Math.round(value) + ' ms';
}

function rawSummary(item) {
  if (item.kind !== 'event') {
    // turns 由服务端解析好（preview 是按字符硬截断的，客户端解析不出完整 JSON 行）
    const turns = Array.isArray(item.turns) ? item.turns : [];
    // 解析不出对话时不要退回原始 JSON —— 那正是这次要修掉的东西。说明情况就好，
    // 逐字核对的入口在下面的「原始记录」里一直开着。
    return {tone: 'transcript', label: '对话原文', text: '', turns,
            meta: turns.length ? ''
                : (item.plumbing ? 'Research Trace 自己的调度记录，已隐去' : '这一段没有可读的对话')};
  }
  const payload = item.payload || {};
  const tool = payload.tool_name || '工具';
  switch (item.event_type) {
    case 'UserPromptSubmit':
      return {tone: 'prompt', label: '你说', text: briefText(payload.prompt), meta: ''};
    case 'PreToolUse':
      return {tone: 'call', label: tool, text: toolBrief(payload.tool_input), meta: ''};
    case 'PostToolUse':
      return {tone: 'result', label: tool + ' 的输出', text: resultBrief(payload.tool_response),
              meta: durationLabel(payload.duration_ms)};
    case 'Stop':
      return {tone: 'turn', label: '一轮结束', text: briefText(payload.last_assistant_message), meta: ''};
    case 'SubagentStop':
      return {tone: 'agent', label: '子任务结束',
              text: briefText(payload.agent_type || payload.agent_id || ''), meta: ''};
    case 'SessionStart':
      return {tone: 'turn', label: '会话开始', text: briefText(payload.source || ''), meta: ''};
    default:
      return {tone: 'other', label: item.event_type, text: briefText(JSON.stringify(payload)), meta: ''};
  }
}

function rawRowHtml(item) {
  const brief = rawSummary(item);
  const full = item.kind === 'event'
    ? JSON.stringify(item.payload, null, 2)
    : String(item.preview || '');
  const agent = item.agent_id ? `agent ${item.agent_id}` : '主会话';
  return `
    <div class="raw-row tone-${brief.tone}">
      <div class="raw-head">
        <span class="raw-label">${esc(brief.label)}</span>
        <span class="raw-when">${fmt(item.at)}</span>
        ${brief.meta ? `<span class="raw-dur">${esc(brief.meta)}</span>` : ''}
        <span class="raw-who">${esc(agent)}</span>
      </div>
      ${brief.turns && brief.turns.length ? `<div class="raw-turns">${brief.turns.map(turn => `
        <p class="raw-turn${turn.sidechain ? ' side' : ''}"><span class="who">${esc(turn.who)}</span>${esc(turn.text)}</p>
      `).join('')}</div>` : ''}
      ${brief.text ? `<p class="raw-brief">${esc(brief.text)}</p>` : ''}
      ${!brief.text && !(brief.turns && brief.turns.length) ? `<p class="raw-brief empty-copy">${esc(brief.meta || '（无可读正文）')}</p>` : ''}
      <details class="raw-full"><summary>原始记录</summary><pre>${esc(full)}</pre></details>
    </div>
  `;
}

async function loadRaw() {
  const box = $('#rawItems');
  box.innerHTML = '<div class="meta">加载中…</div>';
  try {
    const value = await api('/api/projects/' + encodeURIComponent(S.project.id) + '/raw?limit=60');
    box.innerHTML = value.items.map(rawRowHtml).join('')
      || '<div class="meta">还没有已上传的原始历史。</div>';
  } catch (error) {
    box.innerHTML = `<div class="danger">${esc(error.message)}</div>`;
  }
}

/* 从语义记录跳到它的来源原始历史。Node 上登记的 source_event_ids 就是这条边，
   没有登记或来源缺失时明确显示缺口，不用最近的事件冒充来源。 */
async function showNodeRaw(nodeId) {
  const node = S.project.nodes.find(item => item.id === nodeId);
  const label = node ? node.title : nodeId;
  const sources = (node && node.source_event_ids) || [];
  setModal('原始历史 · ' + label, '<div class="meta">加载中…</div>');
  try {
    const value = await api('/api/nodes/' + encodeURIComponent(nodeId) + '/sources');
    const items = value.items;
    const note = !sources.length ? '这条记录尚未登记来源；前序关系和依据可稍后补充。'
      : `登记了 ${sources.length} 条来源，已找到 ${items.length} 条${value.missing_event_ids.length ? '；其余来源待核实' : ''}。`;
    setModal('原始历史 · ' + label, `
      <div class="meta">${esc(note)}</div>
      <div class="raw-list">${items.map(rawRowHtml).join('') || '<div class="meta">还没有已上传的原始历史。</div>'}</div>
    `);
  } catch (error) {
    setModal('原始历史 · ' + label, `<div class="danger">${esc(error.message)}</div>`);
  }
}

/* §3.4：被纠正的原文必须保留。/api/revisions 一直有数据，界面上却没有落点。 */
async function showRevisions(targetType, targetId, label) {
  setModal('修订历史 · ' + label, '<div class="meta">加载中…</div>');
  try {
    const value = await api(
      '/api/revisions/' + encodeURIComponent(targetType) + '/' + encodeURIComponent(targetId)
    );
    const rows = (value.revisions || []).map(revision => {
      const snapshot = revision.snapshot && typeof revision.snapshot === 'object' ? revision.snapshot : {};
      const text = snapshot.body ?? snapshot.summary ?? snapshot.overview ?? JSON.stringify(snapshot, null, 2);
      const sources = revision.source_event_ids || [];
      return `
        <div class="management-card">
          <strong>v${esc(revision.version)}${revision.milestone ? ' · milestone' : ''}</strong>
          <div class="meta">${esc(revision.actor_type || 'unknown')}${revision.actor_id ? ' · ' + esc(revision.actor_id) : ''} · ${fmt(revision.created_at)}</div>
          ${snapshot.title ? `<div class="body">${esc(snapshot.title)}</div>` : ''}
          <pre>${esc(String(text ?? ''))}</pre>
          ${sources.length ? `<div class="meta">来源 event：${esc(sources.join(', '))}</div>` : ''}
        </div>
      `;
    }).join('');
    setModal(
      '修订历史 · ' + label,
      rows || '<div class="empty"><div class="empty-inner">还没有更早的版本。</div></div>'
    );
  } catch (error) {
    setModal('修订历史 · ' + label, `<div class="danger">${esc(error.message)}</div>`);
  }
}

function projectHeaderHtml() {
  const project = S.project;
  const rawKey = (project.workspace_keys || [])[0];
  const key = rawKey && typeof rawKey === 'object' ? rawKey.workspace_key : rawKey;
  return `
    <header class="page-head">
      <div>
        <h1>${esc(project.name)}</h1>
        <div class="page-meta">
          <span>${project.nodes.length} 条记录</span>
          <span>${project.chapters.length} 个章节</span>
          ${S.chapter ? `<span>当前：${esc(S.chapter.name)}</span>` : ''}
          ${key ? `<span class="workspace-key">${icon('database')}${esc(key)}</span>` : ''}
        </div>
      </div>
    </header>
  `;
}

function renderMain() {
  document.title = S.project ? S.project.name + ' · Research Trace' : 'Research Trace';
  document.body.classList.toggle('workspace-active', Boolean(S.project));
  $('#main').classList.toggle('workspace-mode', Boolean(S.project));
  if (!S.project) {
    $('#main').innerHTML = homeHtml();
    const add = $('#homeAddProject');
    if (add) add.onclick = showNewProject;
    document.querySelectorAll('[data-open-project]').forEach(button => {
      button.onclick = () => openProject(button.dataset.openProject).catch(error => notify(error.message));
    });
    return;
  }
  $('#main').innerHTML = workspaceHtml();
  bindWorkspace();
  bindMain();
  bindNodeActions();
}

function renderRecordPane(focus = false) {
  const pane = $('#recordPane');
  if (!pane) return;
  pane.innerHTML = detailContentHtml();
  document.querySelectorAll('[data-select-node]').forEach(button => {
    const selected = button.dataset.selectNode === S.selectedNodeId;
    button.classList.toggle('selected', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  bindMain();
  bindNodeActions();
  const clear = $('[data-clear-node]');
  if (clear) clear.onclick = () => {
    S.selectedNodeId = null;
    renderRecordPane(true);
  };
  if (focus) pane.focus({preventScroll: true});
  if (focus && matchMedia('(max-width: 720px)').matches) {
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    pane.scrollIntoView({behavior: reduced ? 'auto' : 'smooth', block: 'start'});
  }
}

function bindWorkspace() {
  const chapterScope = $('#chapterScope');
  if (chapterScope) chapterScope.onchange = () => {
    S.chapter = S.project.chapters.find(chapter => chapter.id === chapterScope.value) || null;
    S.selectedNodeId = null;
    renderSide();
    renderMain();
  };
  document.querySelectorAll('[data-work-view]').forEach(button => {
    button.onclick = () => {
      S.workView = button.dataset.workView;
      localStorage.setItem('trace.workView', S.workView);
      renderMain();
    };
  });
  document.querySelectorAll('[data-select-node]').forEach(button => {
    button.onclick = () => {
      S.selectedNodeId = button.dataset.selectNode;
      renderRecordPane(true);
    };
  });
  document.querySelectorAll('[data-graph-zoom]').forEach(button => {
    button.onclick = () => {
      const kind = button.dataset.graphZoom;
      if (kind === 'fit') S.graphZoom = null;
      else S.graphZoom = Math.max(GRAPH_ZOOM_MIN,
        Math.min(GRAPH_ZOOM_MAX, kind === 'in' ? currentGraphZoom() * 1.25 : currentGraphZoom() / 1.25));
      applyGraphZoom();
    };
  });
  document.querySelectorAll('.graph-viewport').forEach(viewport => {
    // 只认 ctrl/⌘ + 滚轮：普通滚轮还得留给「往下读」。
    viewport.onwheel = event => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      S.graphZoom = Math.max(GRAPH_ZOOM_MIN,
        Math.min(GRAPH_ZOOM_MAX, currentGraphZoom() * (event.deltaY < 0 ? 1.1 : 1 / 1.1)));
      applyGraphZoom();
    };
  });
  applyGraphZoom();
  bindGraphResize();
  document.querySelectorAll('[data-focus-chapter]').forEach(button => {
    button.onclick = () => {
      S.chapter = S.project.chapters.find(chapter => chapter.id === button.dataset.focusChapter) || null;
      S.selectedNodeId = null;
      renderSide();
      renderMain();
      $('#structureTitle').focus?.({preventScroll: true});
    };
  });
  const clear = $('[data-clear-node]');
  if (clear) clear.onclick = () => {
    S.selectedNodeId = null;
    renderRecordPane(true);
  };
}

function bindMain() {
  const raw = $('#rawHistory');
  if (raw) raw.ontoggle = () => {
    if (raw.open && !raw.dataset.loaded) {
      raw.dataset.loaded = '1';
      loadRaw();
    }
  };
  document.querySelectorAll('[data-select-chapter]').forEach(button => {
    button.onclick = () => {
      S.chapter = S.project.chapters.find(chapter => chapter.id === button.dataset.selectChapter);
      renderSide();
      renderMain();
      $('#main').focus({preventScroll: true});
    };
  });
  document.querySelectorAll('[data-history-type]').forEach(button => {
    button.onclick = () => showRevisions(
      button.dataset.historyType, button.dataset.historyId, button.dataset.historyLabel || ''
    );
  });
  const editOverview = $('#editOverview');
  if (editOverview) editOverview.onclick = () => setModal(
    '编辑 Overview',
    `<label for="fieldBody">项目当前认识</label><textarea id="fieldBody">${esc(S.project.overview)}</textarea>`,
    async () => {
      await api('/api/curate', {
        method: 'POST',
        body: JSON.stringify({
          project_id: S.project.id,
          target_type: 'overview',
          body: $('#fieldBody').value,
          expect_version: S.project.overview_version
        })
      });
      await refreshProject();
    }
  );
  const editSummary = $('#editSummary');
  if (editSummary) editSummary.onclick = () => setModal(
    '编辑 Chapter 摘要',
    `<label for="fieldBody">当前摘要</label><textarea id="fieldBody">${esc(S.chapter.summary)}</textarea>`,
    async () => {
      await api('/api/curate', {
        method: 'POST',
        body: JSON.stringify({
          project_id: S.project.id,
          target_type: 'chapter',
          target_id: S.chapter.id,
          body: $('#fieldBody').value,
          expect_version: S.chapter.summary_version
        })
      });
      await refreshProject();
    }
  );
  const addNode = $('#addNode');
  if (addNode) addNode.onclick = () => setModal(
    '添加通用记录',
    `
      <label for="fieldTitle">标题</label><input id="fieldTitle">
      <label for="fieldBody">正文</label><textarea id="fieldBody"></textarea>
      <div class="grid2">
        <div><label for="fieldTime">发生时间（可空）</label><input id="fieldTime" type="datetime-local"></div>
        <div><label for="fieldLabels">Labels（逗号分隔，可空）</label><input id="fieldLabels"></div>
      </div>
      <label for="fieldParent">延续自（确实是延续时才选择）</label>
      <select id="fieldParent">${parentOptionsHtml(S.chapter.id)}</select>
    `,
    async () => {
      await api('/api/record', {
        method: 'POST',
        body: JSON.stringify({
          project_id: S.project.id,
          chapter_id: S.chapter.id,
          idempotency_key: 'human-' + crypto.randomUUID(),
          title: $('#fieldTitle').value,
          body: $('#fieldBody').value,
          occurred_at: $('#fieldTime').value
            ? new Date($('#fieldTime').value).toISOString()
            : undefined,
          labels: $('#fieldLabels').value.split(',').map(value => value.trim()).filter(Boolean),
          parent_id: $('#fieldParent').value || undefined
        })
      });
      await refreshProject();
    }
  );
  document.querySelectorAll('[data-add-comment]').forEach(button => {
    button.onclick = async () => {
      const box = button.closest('.comment-form');
      const body = box.querySelector('[data-comment-body]').value.trim();
      if (!body) {
        notify('请先填写评论或纠正内容');
        return;
      }
      await withBusy(button, async () => {
        try {
          await api('/api/comments', {
            method: 'POST',
            body: JSON.stringify({
              project_id: S.project.id,
              target_type: button.dataset.type,
              target_id: button.dataset.id,
              kind: box.querySelector('[data-comment-kind]').value,
              body
            })
          });
          await refreshProject();
        } catch (error) {
          notify(error.message);
        }
      }, '添加中…');
    };
  });
  document.querySelectorAll('[data-resolve-comment]').forEach(button => {
    button.onclick = () => withBusy(button, async () => {
      try {
        await api('/api/comments/' + encodeURIComponent(button.dataset.resolveComment) +
                  '/resolve', {method: 'POST'});
        await refreshProject();
      } catch (error) {
        notify(error.message);
      }
    }, '处理中…');
  });
}

function bindNodeActions() {
  document.querySelectorAll('[data-raw-node]').forEach(button => {
    button.onclick = () => showNodeRaw(button.dataset.rawNode);
  });
  document.querySelectorAll('[data-edit-node]').forEach(button => {
    button.onclick = () => {
      const node = S.project.nodes.find(item => item.id === button.dataset.editNode);
      const chapterOptions = S.project.chapters.map(chapter =>
        `<option value="${esc(chapter.id)}" ${chapter.id === node.chapter_id ? 'selected' : ''}>${esc(chapter.name)}</option>`
      ).join('');
      setModal(
        '编辑记录',
        `
          <label for="fieldTitle">标题</label><input id="fieldTitle" value="${esc(node.title)}">
          <label for="fieldBody">正文</label><textarea id="fieldBody">${esc(node.body)}</textarea>
          <div class="grid2">
            <div><label for="fieldChapter">Chapter</label><select id="fieldChapter">${chapterOptions}</select></div>
            <div><label for="fieldReview">确认状态</label><select id="fieldReview">
              <option value="unreviewed" ${node.review_state === 'unreviewed' ? 'selected' : ''}>未确认</option>
              <option value="confirmed" ${node.review_state === 'confirmed' ? 'selected' : ''}>已确认</option>
              <option value="corrected" ${node.review_state === 'corrected' ? 'selected' : ''}>已纠正</option>
            </select></div>
          </div>
          <label for="fieldLabels">Labels（逗号分隔）</label><input id="fieldLabels" value="${esc((node.labels || []).join(', '))}">
          <label for="fieldParent">延续自</label><select id="fieldParent">${parentOptionsHtml(node.chapter_id, node.parent_id || '', node.id)}</select>
        `,
        async () => {
          const destinationChapterId = $('#fieldChapter').value;
          await api('/api/nodes/' + encodeURIComponent(node.id), {
            method: 'PATCH',
            body: JSON.stringify({
              expect_version: node.version,
              patch: {
                title: $('#fieldTitle').value,
                body: $('#fieldBody').value,
                labels: $('#fieldLabels').value.split(',').map(value => value.trim()).filter(Boolean),
                chapter_id: $('#fieldChapter').value,
                parent_id: $('#fieldParent').value || null,
                review_state: $('#fieldReview').value
              }
            })
          });
          await refreshProject(destinationChapterId);
        }
      );
      $('#fieldChapter').onchange = () => {
        $('#fieldParent').innerHTML = parentOptionsHtml($('#fieldChapter').value, '', node.id);
      };
    };
  });
  document.querySelectorAll('[data-attach-node]').forEach(button => {
    button.onclick = () => withBusy(button, async () => {
      const nodeId = button.dataset.attachNode;
      const projectId = S.project.id;
      let mlflowEnabled = false;
      try {
        const integrations = await api('/api/integrations');
        mlflowEnabled = !!((integrations.capabilities || {})[projectId] || {}).mlflow;
      } catch (_) { /* Ordinary attachments work even if status is unavailable. */ }
      setModal(
        '添加附件或外部产物',
        `
          <label for="fieldSource">来源</label><select id="fieldSource"><option value="file">文件或外部位置</option>${mlflowEnabled ? '<option value="mlflow">MLflow 实验证据</option>' : ''}</select>
          <div id="mlflowFields" hidden>
            <p class="meta">把已绑定实验中的 run 或 trace 保存为证据快照，关联到当前记录。</p>
            <label for="fieldMlflowKind">证据类型</label><select id="fieldMlflowKind"><option value="run">Run（参数与指标）</option><option value="trace">Trace（会话与工具调用）</option></select>
            <label for="fieldMlflowId">Run ID / Trace ID</label><input id="fieldMlflowId" autocomplete="off">
          </div>
          <div id="fileFields">
          <label for="fieldFile">小文件（可空，服务默认上限 10 MB）</label><input id="fieldFile" type="file">
          <label for="fieldArtifactName">显示名称</label><input id="fieldArtifactName">
          <div class="grid2">
            <div><label for="fieldDirection">角色</label><select id="fieldDirection"><option value="reference">reference</option><option value="input">input</option><option value="output">output</option></select></div>
            <div><label for="fieldMachine">机器（外部路径可填）</label><input id="fieldMachine"></div>
          </div>
          <label for="fieldExternal">外部路径（大数据/模型只登记位置）</label><input id="fieldExternal">
          <label for="fieldUri">URI（可选）</label><input id="fieldUri">
          </div>
        `,
        async () => {
          if ($('#fieldSource').value === 'mlflow') {
            const externalId = $('#fieldMlflowId').value.trim();
            if (!externalId) throw new Error('请填写 Run ID 或 Trace ID');
            const result = await api('/api/attach', {method: 'POST', body: JSON.stringify({
              project_id: projectId, target_type: 'node', target_id: nodeId,
              integration: 'mlflow', external_kind: $('#fieldMlflowKind').value, external_id: externalId
            })});
            await refreshProject();
            notify(result.duplicate ? '这份证据已保存，已关联到当前记录' : '证据快照已保存');
            return;
          }
          const file = $('#fieldFile').files[0];
          const value = {
            project_id: projectId,
            target_type: 'node',
            target_id: nodeId,
            name: $('#fieldArtifactName').value || (file && file.name) || 'artifact',
            direction: $('#fieldDirection').value,
            machine: $('#fieldMachine').value || undefined,
            external_path: $('#fieldExternal').value || undefined,
            uri: $('#fieldUri').value || undefined
          };
          if (file) {
            value.data_base64 = await file64(file);
            value.mime_type = file.type || undefined;
            value.size = file.size;
          }
          await api('/api/attach', {method: 'POST', body: JSON.stringify(value)});
          await refreshProject();
        }
      );
      $('#fieldSource').onchange = () => {
        const isMlflow = $('#fieldSource').value === 'mlflow';
        $('#mlflowFields').hidden = !isMlflow;
        $('#fileFields').hidden = isMlflow;
      };
    });
  });
}

function showNewProject() {
  setModal(
    '新建项目',
    `
      <label for="fieldName">项目名称</label><input id="fieldName" autocomplete="off">
      <label for="fieldKey">Workspace key（可空，建议填 Git remote）</label><input id="fieldKey" autocomplete="off">
    `,
    async () => {
      const project = await api('/api/projects', {
        method: 'POST',
        body: JSON.stringify({
          name: $('#fieldName').value,
          workspace_keys: $('#fieldKey').value ? [$('#fieldKey').value] : []
        })
      });
      await loadProjects();
      await openProject(project.id);
    }
  );
}

function showNewChapter() {
  setModal(
    '新建 Chapter',
    `
      <label for="fieldName">Chapter 名称</label><input id="fieldName" autocomplete="off">
      <label for="fieldBody">当前摘要（可空）</label><textarea id="fieldBody"></textarea>
    `,
    async () => {
      const chapter = await api(
        '/api/projects/' + encodeURIComponent(S.project.id) + '/chapters',
        {
          method: 'POST',
          body: JSON.stringify({name: $('#fieldName').value, summary: $('#fieldBody').value})
        }
      );
      await refreshProject(chapter.id);
    }
  );
}

/* §10 要求的健康状态。这是用户判断"我这台机器的东西到底传上去没有"的唯一入口：
   投递器把本机 outbox 报上来之前，中央能证明的只有"最近一次被确认存下的 batch"，
   所以未上报要显式说出来，不能画一个绿灯糊弄过去。 */
const HEALTH_STATE_PILL = {ok: 'confirmed', warn: 'corrected', critical: 'corrected', unknown: 'muted'};
const HEALTH_STATE_LABEL = {ok: '正常', warn: '需要注意', critical: '严重', unknown: '未上报'};

function healthCardHtml(title, state, lines) {
  return `
    <div class="management-card">
      <strong>${esc(title)}</strong>
      <span class="pill ${HEALTH_STATE_PILL[state] || 'muted'}">${esc(HEALTH_STATE_LABEL[state] || state)}</span>
      <div class="meta">${lines.filter(Boolean).join('<br>')}</div>
    </div>
  `;
}

function outboxHealthHtml(value) {
  const outbox = value.outbox;
  const machines = (outbox && outbox.machines) || (Array.isArray(outbox) ? outbox : null);
  if (!machines || !machines.length) {
    return healthCardHtml('本机 outbox 投递', 'unknown', [
      '还没有投递器上报 outbox 状态。',
      'hook 只写 pending/，投递成功才搬进 sent/；中央这边只能看到已确认的 batch。'
    ]);
  }
  const stuck = machines.some(machine => Number(machine.pending || 0) > 0 || machine.last_error);
  return healthCardHtml('本机 outbox 投递', stuck ? 'warn' : 'ok', machines.map(machine => [
    `<strong>${esc(machine.machine || machine.host || '未知机器')}</strong>`,
    `pending ${esc(machine.pending ?? '—')} · sent ${esc(machine.sent ?? '—')}`,
    machine.oldest_pending_at ? `最早未投递 ${fmt(machine.oldest_pending_at)}` : '',
    machine.last_delivered_at ? `最近投递 ${fmt(machine.last_delivered_at)}` : '',
    machine.last_error ? `<span class="danger">${esc(machine.last_error)}</span>` : ''
  ].filter(Boolean).join(' · ')));
}

function recorderHealthHtml(value) {
  const counts = value.counts || {};
  const batch = value.last_batch;
  const recorder = value.recorder;
  const lines = [
    `原始 event ${esc(counts.events ?? '—')} 条 · transcript ${esc(counts.transcript_chunks ?? '—')} 段 · 语义 Node ${esc(counts.nodes ?? '—')} 条`,
    batch
      ? `最近一次被中央确认的 batch：${esc(batch.batch_id || '')} · ${esc(batch.event_count ?? 0)} 条 event · ${fmt(batch.created_at)}`
      : '中央还没有确认过任何 batch。'
  ];
  if (!recorder) {
    lines.push('Recorder 未处理游标尚未上报；一批 batch 不产生 Node 本身是正常的。');
    return healthCardHtml('Recorder', 'unknown', lines);
  }
  lines.push(`未处理 batch ${esc(recorder.pending_batches ?? '—')} · 最近处理 ${fmt(recorder.last_processed_at)}`);
  if (recorder.last_error) lines.push(`<span class="danger">${esc(recorder.last_error)}</span>`);
  return healthCardHtml('Recorder', recorder.last_error || Number(recorder.pending_batches || 0) > 0 ? 'warn' : 'ok', lines);
}

/* 只由 Number 造字符串，任何一条路径都不会把入参原样交回去（fmt 那次存储型 XSS
   就是从「解析失败原样返回」来的）。 */
function bytesLabel(value) {
  const size = Number(value);
  if (!Number.isFinite(size) || size < 0) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let index = 0;
  let scaled = size;
  while (scaled >= 1024 && index < units.length - 1) {
    scaled /= 1024;
    index += 1;
  }
  return (index ? scaled.toFixed(1) : String(Math.round(scaled))) + ' ' + units[index];
}

async function showHealth() {
  setModal('采集与集成状态', '<div class="meta">加载中…</div>');
  try {
    const value = await api('/api/health');
    setModal('采集与集成状态', [
      outboxHealthHtml(value),
      recorderHealthHtml(value),
      integrationHealthHtml(value.integrations),
      healthCardHtml('中央存储', (value.ok && !value.anonymous_read) ? 'ok' : 'warn', [
        `schema v${esc(value.schema_version ?? '—')} · 项目 ${esc((value.counts || {}).projects ?? '—')} 个 · 附件 ${esc((value.counts || {}).attachments ?? '—')} 个`,
        value.write_protected ? '写入需要设备凭证或登录。' : '写入未受保护（仅限本机开发）。',
        /* 未配 OAuth 时读取是完全公开的，包括原始 transcript 和附件下载。
           这件事只在服务端启动横幅里说过，用的人看不到。 */
        value.anonymous_read ? '<span class="danger">未配置 GitHub OAuth：任何能连到这个端口的人都能读取全部原始历史与附件。</span>' : '',
        value.purge_generation ? `已执行 ${esc(value.purge_generation)} 次紧急 purge · 最近一次 ${fmt((value.last_purge || {}).created_at) || '—'}` : ''
      ])
    ].join(''));
    const sync = $('#syncKnowledge');
    if (sync) sync.onclick = () => withBusy(sync, async () => {
      try {
        await api('/api/integrations/sync', {method: 'POST', body: '{}'});
        await showHealth();
      } catch (error) { notify(error.message); }
    }, '同步中…');
  } catch (error) {
    setModal('采集与集成状态', `<div class="danger">${esc(error.message)}</div>`);
  }
}

function integrationHealthHtml(value) {
  if (!value) return '';
  const memory = value.basic_memory || {}, mlflow = value.mlflow || {};
  const states = {disabled: '未启用', pending: '等待同步', syncing: '同步中', ready: '可用', configured: '已配置', error: '暂不可用'};
  return healthCardHtml('知识检索与实验证据', memory.state === 'error' || mlflow.state === 'error' ? 'warn' : 'ok', [
    `Basic Memory：${esc(states[memory.state] || memory.state)} · 已同步 ${esc(value.indexed_notes || 0)} 份内容`,
    memory.last_success_at ? `最近同步：${fmt(memory.last_success_at)}。向量生成由知识服务继续处理。` : '',
    memory.state === 'error' ? '知识同步将自动重试，当前仍可使用本地关键词检索。' : '',
    `MLflow：${esc(states[mlflow.state] || mlflow.state)}。已绑定项目可在记录的“附件 / 产物”中导入证据。`,
    memory.enabled && canWrite() ? '<button class="btn" type="button" id="syncKnowledge">立即同步知识</button>' : ''
  ]);
}

async function showUsers() {
  const value = await api('/api/admin/users');
  setModal(
    '团队用户',
    value.users.map(user => `
      <div class="management-card">
        <strong>${esc(user.login)}</strong>
        <div class="meta">${esc(user.display_name || '')} · GitHub #${esc(user.github_id)}</div>
        <div class="toolbar">
          <div class="field-inline"><label class="field-label" for="role-${esc(user.id)}">角色</label>
            <select id="role-${esc(user.id)}" data-role="${esc(user.id)}">
              <option value="reader" ${user.role === 'reader' ? 'selected' : ''}>reader</option>
              <option value="member" ${user.role === 'member' ? 'selected' : ''}>member</option>
              <option value="admin" ${user.role === 'admin' ? 'selected' : ''}>admin</option>
            </select>
          </div>
          <label class="check-row"><input type="checkbox" data-disabled="${esc(user.id)}" ${user.disabled ? 'checked' : ''}> 禁用</label>
          <button class="btn" type="button" data-save-user="${esc(user.id)}">保存</button>
        </div>
      </div>
    `).join('') || '<div class="empty"><div class="empty-inner">还没有用户</div></div>'
  );
  document.querySelectorAll('[data-save-user]').forEach(button => {
    button.onclick = () => withBusy(button, async () => {
      const id = button.dataset.saveUser;
      try {
        await api('/api/admin/users/' + encodeURIComponent(id), {
          method: 'PATCH',
          body: JSON.stringify({
            role: document.querySelector(`[data-role="${id}"]`).value,
            disabled: document.querySelector(`[data-disabled="${id}"]`).checked
          })
        });
        await showUsers();
      } catch (error) {
        notify(error.message);
      }
    });
  });
}

/* 凭证到期是静默的：那台机器上的 hook 与 MCP 会突然开始 401，而人只会看到
   "传不上去"。所以到期日必须摆在设备面板上，并且提前提醒。 */
function deviceExpiringSoon(device, days = 14) {
  const at = new Date(device.expires_at);
  if (Number.isNaN(at.getTime())) return false;
  return at.getTime() - Date.now() < days * 86400000;
}

async function showDevices() {
  const value = await api('/api/auth/devices');
  setModal(
    '已登录设备',
    value.devices.map(device => `
      <div class="management-card">
        <strong>${esc(device.name)}</strong>
        <div class="meta">${device.revoked_at ? '已撤销' : '有效'} · 创建 ${fmt(device.created_at)} · 最近使用 ${fmt(device.last_used_at) || '尚未使用'}</div>
        ${device.revoked_at || !device.expires_at ? '' : `<div class="meta">有效期至 ${fmt(device.expires_at)}${deviceExpiringSoon(device) ? '<span class="danger"> · 即将过期，在那台机器上运行 <code>trace-login --renew</code></span>' : ' · 到期后自动失效，用 <code>trace-login --renew</code> 续期'}</div>`}
        ${device.revoked_at ? '' : `<button class="btn warn" type="button" data-revoke-device="${esc(device.id)}">${icon('logout')}撤销设备</button>`}
      </div>
    `).join('') || '<div class="empty"><div class="empty-inner">还没有通过账号绑定的设备。</div></div>'
  );
  document.querySelectorAll('[data-revoke-device]').forEach(button => {
    button.onclick = async () => {
      if (!confirm('撤销这台设备？它的 Hook/MCP 将立即失效。')) return;
      await withBusy(button, async () => {
        try {
          await api('/api/auth/devices/' + encodeURIComponent(button.dataset.revokeDevice), {
            method: 'DELETE'
          });
          await showDevices();
        } catch (error) {
          notify(error.message);
        }
      }, '撤销中…');
    };
  });
}

function showAccount() {
  if (!S.authEnabled) {
    setModal(
      '连接设置',
      `
        <label for="fieldToken">旧版写入 Token</label><input id="fieldToken" type="password" value="${esc(S.token)}" autocomplete="off">
        <label for="fieldActor">你的显示名称</label><input id="fieldActor" value="${esc(S.actor)}" autocomplete="off">
        <p class="meta">推荐启用 GitHub OAuth 并使用 trace-login；旧 Token 只保存在当前浏览器 localStorage。</p>
      `,
      async () => {
        S.token = $('#fieldToken').value;
        S.actor = $('#fieldActor').value || 'human';
        localStorage.setItem('trace.token', S.token);
        localStorage.setItem('trace.actor', S.actor);
      }
    );
    return;
  }
  const name = S.user.display_name || S.user.login;
  setModal(
    '账户',
    `
      <div class="account-summary">
        <div class="account-avatar" aria-hidden="true">${esc(name.slice(0, 1).toUpperCase())}</div>
        <div><strong>${esc(name)}</strong><div class="meta">@${esc(S.user.login)} · ${esc(S.user.role)}</div></div>
      </div>
      <div class="toolbar">
        <button class="btn" type="button" id="manageDevices">${icon('device')}管理已登录设备</button>
        ${S.user.role === 'admin' ? `<button class="btn" type="button" id="manageUsers">${icon('team')}管理团队用户</button>` : ''}
        <button class="btn warn" type="button" id="logoutBtn">${icon('logout')}退出登录</button>
      </div>
    `
  );
  $('#manageDevices').onclick = () => showDevices().catch(error => notify(error.message));
  const manageUsers = $('#manageUsers');
  if (manageUsers) manageUsers.onclick = () => showUsers().catch(error => notify(error.message));
  $('#logoutBtn').onclick = async () => {
    try {
      await api('/api/auth/logout', {method: 'POST'});
      location.reload();
    } catch (error) {
      notify(error.message);
    }
  };
}

$('#projects').onclick = event => {
  const button = event.target.closest('[data-project]');
  if (button) openProject(button.dataset.project).catch(error => notify(error.message));
};
$('#chapters').onclick = event => {
  const button = event.target.closest('[data-chapter]');
  if (!button) return;
  S.chapter = S.project.chapters.find(chapter => chapter.id === button.dataset.chapter);
  S.selectedNodeId = null;
  renderSide();
  renderMain();
};
$('#addProject').onclick = showNewProject;
$('#addChapter').onclick = showNewChapter;
$('#tokenBtn').onclick = showAccount;
$('#healthBtn').onclick = () => showHealth();

/* 跨项目搜索必须说清"这条命中属于哪个项目"，并且点得进去。
   只显示 scope 和时间时，搜到别的项目的记录等于知道它存在却打不开。 */
function projectName(id) {
  const project = S.projects.find(item => item.id === id);
  return project ? project.name : '未知项目';
}

const SEARCH_SCOPE_LABEL = {
  node: '记录', chapter: '研究线', comment: '评论', overview: 'Overview', event: '原始 event', transcript: 'transcript'
};

function searchHitHtml(hit) {
  const title = hit.title || hit.name || hit.event_type || hit.kind || hit.scope;
  const when = hit.occurred_at || hit.captured_at || hit.created_at || hit.updated_at;
  const isRaw = hit.scope === 'event' || hit.scope === 'transcript';
  // 命中评论时跳到它挂着的那条记录，而不是只把项目打开。
  const nodeId = hit.scope === 'node' ? hit.id : (hit.target_type === 'node' ? hit.target_id : '');
  return `
    <button class="hit" type="button" data-hit-project="${esc(hit.project_id || '')}"
      data-hit-node="${esc(nodeId || '')}" data-hit-chapter="${esc(hit.scope === 'chapter' ? hit.id : (hit.chapter_id || ''))}" data-hit-raw="${isRaw ? '1' : ''}">
      <b>${esc(title)}</b>
      <div>${esc(String(hit.body || hit.overview || '').slice(0, 260))}</div>
      <small><span class="hit-project">${esc(projectName(hit.project_id))}</span> · ${esc(SEARCH_SCOPE_LABEL[hit.scope] || hit.scope)} · ${fmt(when)}</small>
      ${hit.index_stale ? '<small>索引待更新 · 此处显示最新记录</small>' : ''}
    </button>
  `;
}

async function openHit(button) {
  const projectId = button.dataset.hitProject;
  if (!projectId) {
    notify('这条命中没有所属项目，无法跳转');
    return;
  }
  hideSearch();
  if (!S.project || S.project.id !== projectId) await openProject(projectId);
  const node = button.dataset.hitNode
    ? S.project.nodes.find(item => item.id === button.dataset.hitNode)
    : null;
  if (node) {
    S.chapter = S.project.chapters.find(chapter => chapter.id === node.chapter_id) || null;
    S.selectedNodeId = node.id;
  } else {
    S.chapter = S.project.chapters.find(chapter => chapter.id === button.dataset.hitChapter) || null;
    S.selectedNodeId = null;
  }
  renderSide();
  renderMain();
  if (button.dataset.hitRaw === '1') {
    const raw = $('#rawHistory');
    if (raw) {
      raw.open = true;
      raw.scrollIntoView({block: 'nearest'});
    }
  }
}

function bindSearchHits() {
  $('#searchResults').querySelectorAll('[data-hit-project]').forEach(button => {
    button.onclick = () => openHit(button).catch(error => notify(error.message));
  });
}

let searchTimer;
function hideSearch() {
  $('#searchResults').hidden = true;
  $('#search').setAttribute('aria-expanded', 'false');
}
$('#search').oninput = event => {
  clearTimeout(searchTimer);
  const query = event.target.value.trim();
  if (!query) {
    hideSearch();
    return;
  }
  searchTimer = setTimeout(async () => {
    try {
      const value = await api('/api/search?q=' + encodeURIComponent(query) + '&scope=all&limit=30');
      if ($('#search').value.trim() !== query) return;
      $('#searchResults').innerHTML = (value.hits.map(searchHitHtml).join('')
        || '<div class="hit"><b>没有结果</b><div>换一个更具体的关键词试试。</div></div>')
        + searchTruncationHtml(value);
      bindSearchHits();
      $('#searchResults').hidden = false;
      $('#search').setAttribute('aria-expanded', 'true');
    } catch (error) {
      $('#searchResults').innerHTML = `<div class="hit danger">${esc(error.message)}</div>`;
      $('#searchResults').hidden = false;
      $('#search').setAttribute('aria-expanded', 'true');
    }
  }, 250);
};
/* 存储层给语义层留了保底名额并算出了截断信息。不说出来的话，用户看到 30 条
   就以为只有 30 条——而被挤掉的往往正是他要找的那条语义记录。 */
function searchTruncationHtml(value) {
  if (value && value.retrieval && value.retrieval.fallback) return '<div class="hit meta">知识检索暂不可用，当前显示本地关键词结果。</div>';
  if (value && value.retrieval && value.retrieval.backend === 'basic_memory+local') {
    const more = value.truncated || value.retrieval.candidate_omitted;
    return `<div class="hit meta">知识检索与关键词结果${more ? ' · 部分结果未显示，请缩小搜索范围' : ''}</div>`;
  }
  if (!value || !value.truncated) return '';
  const omitted = value.omitted || {};
  const parts = Object.keys(omitted)
    .filter(key => omitted[key])
    .map(key => `${esc(SEARCH_SCOPE_LABEL[key] || key)} ${esc(omitted[key])} 条`);
  if (!parts.length) return '';
  return `<div class="hit meta">还有 ${parts.join('、')}未显示。改用 scope=semantic 或换更具体的关键词。</div>`;
}

document.addEventListener('click', event => {
  if (!event.target.closest('.search-shell')) hideSearch();
});
document.addEventListener('keydown', event => {
  if (event.key === '/' && !event.ctrlKey && !event.metaKey &&
      !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {
    event.preventDefault();
    $('#search').focus();
  }
  if (event.key === 'Escape' && !$('#searchResults').hidden) hideSearch();
});

async function bootstrap() {
  const config = await api('/api/auth/config');
  S.authEnabled = config.enabled;
  if (S.authEnabled) {
    const response = await fetch(BASE + '/api/auth/me', {headers: {Accept: 'application/json'}});
    if (!response.ok) {
      setAccountLabel('GitHub 登录');
      $('#tokenBtn').onclick = () => {
        location.href = BASE + '/auth/github/login?return_to='
          + encodeURIComponent(BASE + '/');
      };
      $('#sidebar').hidden = true;
      $('.layout').style.gridTemplateColumns = '1fr';
      $('#main').style.margin = 'auto';
      $('#main').innerHTML = `
        <div class="empty">
          <div class="empty-inner">
            <span class="section-icon login-icon">${icon('user')}</span>
            <h2>登录 Research Trace</h2>
            <p>使用获准的 GitHub 账户访问团队研究记录。</p>
            <a class="btn primary" href="${BASE}/auth/github/login?return_to=${encodeURIComponent(BASE + '/')}">使用 GitHub 登录</a>
          </div>
        </div>
      `;
      return;
    }
    const value = await response.json();
    S.user = value.user;
    S.csrf = value.csrf_token;
    S.actor = S.user.login;
    setAccountLabel('@' + S.user.login);
  }
  await loadProjects();
}

bootstrap().catch(error => {
  $('#main').innerHTML = `<div class="empty danger"><div class="empty-inner">${esc(error.message)}</div></div>`;
});
</script>
</body>
</html>'''


def render_index(base_path: str = "") -> str:
    """按挂载前缀渲染首页。base_path 为空即根部署，渲染结果与模板逐字节相同。"""
    return INDEX_HTML.replace(BASE_PLACEHOLDER, base_path or "")
