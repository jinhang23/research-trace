"""Dependency-free Research Trace web client embedded by the service."""

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

.raw-card { padding: 14px 20px; }
.raw-list {
  margin-top: 8px;
  padding-top: 5px;
  border-top: 1px solid var(--line);
}
.raw-row {
  padding: 13px 0;
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
.graph-viewport { min-width: 100%; }
.graph-canvas { position: relative; }
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
.graph-node {
  position: absolute;
  display: flex;
  width: 184px;
  height: 88px;
  flex-direction: column;
  align-items: stretch;
  justify-content: flex-start;
  gap: 4px;
  padding: 9px 10px;
  overflow: hidden;
  border: 1px solid #cbd5d1;
  border-radius: 9px;
  color: var(--ink);
  background: rgba(255, 255, 255, .94);
  box-shadow: 0 2px 7px rgba(35, 58, 52, .045);
  text-align: left;
}
.graph-node:hover { border-color: #8fa69f; background: #fff; }
.graph-node.selected {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px rgba(23, 107, 92, .14);
}
.graph-node.needs-review { border-left: 3px solid #a94d55; }
.graph-node-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: var(--muted);
  font: 9px/1.25 ui-monospace, SFMono-Regular, Consolas, monospace;
}
.graph-node strong {
  display: -webkit-box;
  overflow: hidden;
  font-size: 12px;
  font-weight: 630;
  line-height: 1.35;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.graph-node-state {
  margin-top: auto;
  overflow: hidden;
  color: var(--muted);
  font-size: 9px;
  line-height: 1.2;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.graph-node.confirmed .graph-node-state { color: var(--accent-strong); }
.graph-node.corrected .graph-node-state,
.graph-node.needs-review .graph-node-state { color: #8e3f47; }
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
.graph-node {
  border-color: #d7d1c8;
  border-radius: 5px;
  background: #fff;
  box-shadow: none;
}
.graph-node:hover { border-color: #a9a096; }
.graph-node.selected { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
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

.flow-node { border-left: 3px solid var(--accent); }
.flow-node .graph-node-state { color: var(--accent-strong); }
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
      <button class="account-btn" id="healthBtn" type="button" aria-haspopup="dialog" aria-label="采集、Recorder 与备份状态">
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
/* md.js — 极简 markdown 渲染器（零依赖，不走 CDN，file:// 下可用）。
 *
 * 覆盖科研笔记实际会用到的语法：标题、围栏代码、行内代码、列表（任意层嵌套、
 * 有序/无序/任务、项内可放代码块和引用）、引用、表格（含列对齐 + 数值列自动
 * 右对齐；`\|` 按 CommonMark 当字面竖线）、分隔线、粗体斜体删除线（* 和 _ 两套）、
 * 链接与图片（含 <尖括号> 目标和括号成对的裸路径）、裸链接自动识别、
 * 数学公式原样保留、[[007]] 内部跳转。
 *
 * 有意不做：引用式链接 [a][ref]、脚注、setext 标题、四空格缩进代码块。
 * 这些在实验记录里几乎不出现，做进来只会让下面这堆正则更难被人看懂。
 *
 * 安全：先整体转义 HTML，之后才插入自己生成的标签。正文里写不进裸 HTML。
 * 正文是人和 agent 通过 API 写进来的不可信输入，所以链接目标还要过 safeHref。
 */
(function (global) {
  "use strict";

  var ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return ESC[c]; }); }

  /* ------------------------------------------------------------ 链接目标消毒 */

  var NAMED_ENT = {
    amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", colon: ":",
    tab: "\t", newline: "\n", sol: "/", nbsp: " ", semi: ";"
  };
  function fromCp(n) { return (n >= 0 && n <= 0x10ffff) ? String.fromCodePoint(n) : ""; }

  /* 只用来生成「拿去比对黑名单」的探针，不用来生成真正输出的 href。
     浏览器会在属性值上解码实体，所以 &#106;avascript: / java&Tab;script: 这类写法
     到了浏览器手里就是 javascript:；黑名单必须看解码之后的样子。
     反复解码几轮是为了挡 &amp;#106; 这种套娃——宁可误判成 # 也不要漏。 */
  function decodeEntities(s) {
    for (var round = 0; round < 3; round++) {
      var next = s
        .replace(/&#x([0-9a-f]+);?/gi, function (_, hex) { return fromCp(parseInt(hex, 16)); })
        .replace(/&#(\d+);?/g, function (_, dec) { return fromCp(parseInt(dec, 10)); })
        .replace(/&([a-z][a-z0-9]*);?/gi, function (m, name) {
          var k = name.toLowerCase();
          return Object.prototype.hasOwnProperty.call(NAMED_ENT, k) ? NAMED_ENT[k] : m;
        });
      if (next === s) break;
      s = next;
    }
    return s;
  }

  var BAD_SCHEME = /^(?:javascript|data|vbscript|livescript|mocha):/i;

  /* WHATWG URL 解析在认协议之前，会先剥掉首尾的 C0 控制字符与空格、
     并删掉中间所有的 tab/换行。所以 "\x01javascript:alert(1)" 在浏览器眼里
     就是一个 javascript: URL —— 只用 trim() 加一个 \s 前缀的正则判断，会整条漏过去。
     这里的做法是：先按同一套规则把控制字符清干净，再判断，并且**返回清干净的那份**
     （返回原串等于把控制字符留给浏览器自己去剥，等于没修）。 */
  function safeHref(h, resolve) {
    var s = String(h == null ? "" : h).replace(/[\u0000-\u001f\u007f]/g, "").trim();
    var probe = decodeEntities(s).replace(/[\u0000-\u0020\u007f]/g, "");
    if (BAD_SCHEME.test(probe)) return "#";
    if (/^([a-z][a-z0-9+.-]*:|\/\/|\/|#)/i.test(s)) return s;   // 绝对 / 协议 / 锚点：原样
    return resolve ? resolve(s) : s;                             // 相对路径：交给调用方重写
  }

  function imgTag(alt, src, resolve, cls, title) {
    return '<img class="' + (cls || "") + '" alt="' + alt + '" src="' + safeHref(src, resolve) + '"'
      + (title ? ' title="' + title + '"' : "") + ' loading="lazy">';
  }

  /* 目标地址两种写法：CommonMark 的 <...> 尖括号形式（里面允许空格），
     以及圆括号成对的裸路径。`loss curve (run 42).png` 这种附件名在实验记录里太常见，
     只认「不含空格和括号」的老写法会让这类图/附件在网页上根本不出现。
     标题（图注）里的引号在整体转义之后已经是 &quot; / &#39;，
     正则必须按转义后的样子写——按 `"` 写会导致整个图片语法匹配不上。
     标题允许跨行：FORMAT.md §5 自己给的示例就是把长图注折成两行写的。 */
  var DEST = "(?:&lt;([^\\n]*?)&gt;|((?:[^()\\s]|\\((?:[^()\\s]|\\([^()\\s]*\\))*\\))+))";
  var TITLE = "(?:\\s+(?:&quot;([\\s\\S]*?)&quot;|&#39;([\\s\\S]*?)&#39;))?";
  var IMG_SRC = "!\\[([^\\]]*)\\]\\(\\s*" + DEST + TITLE + "\\s*\\)";
  var LINK_SRC = "\\[([^\\]]+)\\]\\(\\s*" + DEST + TITLE + "\\s*\\)";
  var RE_IMG = new RegExp(IMG_SRC, "g");
  var RE_LINK = new RegExp(LINK_SRC, "g");
  var RE_LONE_IMG = new RegExp("^" + IMG_SRC + "$");
  // 捕获组：1=alt/文字 2=<尖括号>目标 3=裸目标 4="标题" 5='标题'
  function destOf(m) { return m[2] !== undefined ? m[2] : (m[3] || ""); }
  function titleOf(m) { return m[4] !== undefined ? m[4] : (m[5] !== undefined ? m[5] : ""); }

  /* ---------------------------------------------------------------- 行内 */

  var SENT = "\u0000";   // render() 已把正文里的控制字符清掉，所以它一定不会和正文撞

  // CommonMark 的反斜杠转义只对 ASCII 标点生效。这条限制很重要：
  // 实验记录里全是 C:\Users\... 这类 Windows 路径，若无差别地吃掉反斜杠，
  // 路径会当场变形；按标准只在标点前生效，路径原样保留。
  var ESCAPABLE = "!\"#$%&'()*+,\\-./:;<=>?@\\[\\\\\\]^_`{|}~";

  function inline(text, resolve, brk) {
    var holes = [];
    function hole(html) { holes.push(html); return SENT + (holes.length - 1) + SENT; }

    // 行内代码。先抠出来，后面的强调/链接规则就碰不到它了。
    text = text.replace(/`([^`]+)`/g, function (_, c) { return hole("<code>" + c + "</code>"); });

    // 数学公式。不引 KaTeX（零依赖是硬约束），但**必须原样留住**：
    // 公式一旦被 * _ \ 那几条规则啃过，人和 LLM 都再也读不回原式。
    // 所以整段抠成不可变文本、连 $ 一起保留，将来接排版器时纯属加成。
    text = text.replace(/\$\$([^\n]+?)\$\$/g, function (m) { return hole('<span class="math">' + m + "</span>"); });
    // 单个 $ 的判定按 pandoc 那套：$ 后不能是空白、$ 前不能是空白、收尾的 $ 后面不能跟数字。
    // 这条「后面不能跟数字」正是用来放过「花了 $5 又花了 $3」这种货币写法的。
    text = text.replace(/(^|[^\\$])\$(?!\s)((?:[^$\n])*?[^\s\\])\$(?!\d)/g,
      function (_, pre, body) { return pre + hole('<span class="math">$' + body + "$</span>"); });

    // 反斜杠转义：\| \* \_ \[ … 一律变回字面字符，且不再参与后面的语法。
    text = text.replace(new RegExp("\\\\([" + ESCAPABLE + "])", "g"), function (_, c) { return hole(c); });

    // 裸链接。必须在 [text](url) 之前处理，且前导字符里排除 ( 和 [，
    // 否则 [标题](http://x) 里的 url 会被再包一层。
    // 括号按成对计入：维基百科那种 .../Foo_(bar) 链接不这样做会被截成坏链接。
    text = text.replace(/(^|[\s、，。；：])(https?:\/\/(?:[^\s<>"'()（）、，。；：]|\((?:[^\s()]|\([^\s()]*\))*\))+)/g,
      function (_, pre, url) { return pre + "[" + url + "](" + url + ")"; });

    text = text.replace(RE_IMG, function () {
      var m = arguments;
      return hole(imgTag(m[1], destOf(m), resolve, "inline-img zoomable", titleOf(m)));
    });
    // 链接：开合标签各自入洞，中间的文字留在外面继续吃强调规则。
    // （旧实现是整条 <a …> 生成完再跑强调替换，于是 [x](https://a/**b**/c) 的
    //  href 里会被塞进 <strong>，链接指向一个不存在的地址。）
    text = text.replace(RE_LINK, function () {
      var m = arguments;
      var href = safeHref(destOf(m), resolve);
      var ext = /^[a-z][a-z0-9+.-]*:/i.test(href) ? ' target="_blank" rel="noopener noreferrer"' : "";
      var title = titleOf(m);
      return hole('<a href="' + href + '"' + ext + (title ? ' title="' + title + '"' : "") + ">")
        + m[1] + hole("</a>");
    });
    // <https://…> 自动链接。放在上面两条之后，免得抢走 ![](<a b.png>) 里的尖括号目标。
    text = text.replace(/&lt;(https?:\/\/[^\s&]+)&gt;/g, function (_, url) {
      var href = safeHref(url, resolve);
      return hole('<a href="' + href + '" target="_blank" rel="noopener noreferrer">' + url + "</a>");
    });
    // [[007]] —— 森林结构下表达"另见某条支"的软链接；反向链接由后端算出
    text = text.replace(/\[\[\s*(\d+[a-z]*)\s*\]\]/g, function (_, id) {
      return hole('<a class="wikilink" href="#' + id + '" data-goto="' + id + '">' + id + "</a>");
    });

    text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/(^|[^\w_])__([^_]+?)__(?![\w])/g, "$1<strong>$2</strong>");
    text = text.replace(/(^|[^*\w])\*([^*\n]+)\*(?![*\w])/g, "$1<em>$2</em>");
    // 下划线强调的前后都要求不是词内字符，否则 some_var_name 会被劈成斜体
    text = text.replace(/(^|[^\w_])_([^_\n]+?)_(?![\w])/g, "$1<em>$2</em>");
    text = text.replace(/~~([^~]+)~~/g, "<del>$1</del>");

    if (brk) text = text.replace(/\n/g, "<br>");

    // 洞里可能还嵌着洞（比如 ![`a`](x) 的 alt），所以要循环填到没有为止。
    var guard = 8;
    while (text.indexOf(SENT) >= 0 && guard-- > 0) {
      text = text.replace(new RegExp(SENT + "(\\d+)" + SENT, "g"), function (_, i) {
        var v = holes[+i];
        return v === undefined ? "" : v;
      });
    }
    return text;
  }

  /* ---------------------------------------------------------------- 表格 */

  /* 按 CommonMark 切分表格行：`\|` 是**字面竖线**，不是分隔符。
     从 Excel / Google Sheets 粘表格时会自动产出这种转义（含管道命令、`低|中|高`
     这类取值都会），认不出来就会把一格劈成两格、把最右边的列整个挤掉。 */
  function cells(line) {
    var s = line.replace(/^\s*\|/, "");
    var out = [], cur = "";
    for (var i = 0; i < s.length; i++) {
      var ch = s.charAt(i);
      if (ch === "\\" && s.charAt(i + 1) === "|") { cur += "\\|"; i++; continue; }
      if (ch === "|") { out.push(cur); cur = ""; continue; }
      cur += ch;
    }
    out.push(cur);
    // 行尾那根收口的竖线会切出一个空格子，去掉它；以 `\|` 结尾时切不出空格子，不受影响
    if (out.length > 1 && /^\s*$/.test(out[out.length - 1])) out.pop();
    return out.map(function (c) { return c.trim(); });
  }

  /* 数值单元格：剥掉强调/空白之后是一个数，可带正负号、千分位、百分号、科学计数，
     并且允许「主值 ± 误差」这种写法。
     FORMAT.md §4 明写「有方差就写进去（0.943 ± 0.004）」，
     所以带 ± 的格子必须仍然算数值列——否则照着格式标准写，反而丢掉标准承诺的
     右对齐和底纹条，标准和实现就打架了。
     带单位的（40 s）故意**不**算数值：FORMAT.md 说这是对的，单位列当文字列读更清楚。 */
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

  /* 表格列：显式的 :--- / ---: / :---: 优先；没写就把"整列都是数字"的列右对齐。
     科研表格里的指标列右对齐之后小数点自然成列，好读得多。
     numeric[] 单独算一份（不受显式对齐影响），供底纹条使用。 */
  function columnInfo(sepLine, head, rows) {
    var explicit = cells(sepLine).map(function (c) {
      var l = /^:/.test(c), r = /:$/.test(c);
      return l && r ? "center" : r ? "right" : l ? "left" : "";
    });
    var align = [], numeric = [];
    head.forEach(function (_, i) {
      var seen = 0, ok = true;
      for (var k = 0; k < rows.length; k++) {
        var v = isNumeric(rows[k][i]);
        if (v === false) { ok = false; break; }
        if (v === true) seen++;
      }
      numeric[i] = ok && seen > 0;
      align[i] = explicit[i] || (numeric[i] ? "right" : "");
    });
    return { align: align, numeric: numeric };
  }

  /* ---------------------------------------------------------------- 块 */

  var BLOCK_START = /^(\s*(#{1,6}\s|&gt;|```|~~~|\||\$\$)|\s*([-*+]|\d+[.)])\s|\s*(-{3,}|\*{3,}|_{3,})\s*$)/;
  var LI_RE = /^(\s*)([-*+]|\d+[.)])(\s+)(.*)$/;

  function isBlank(s) { return /^\s*$/.test(s); }
  function indentOf(s) { return s.match(/^\s*/)[0].length; }

  /* 列表块：把每一项的行收集起来、剥掉这一项的缩进，然后**递归**丢回 renderLines。
     子列表、项内的围栏代码、项内的引用块因此全部自然支持，不必在这里为每种嵌套
     各写一遍分支。旧实现是手工展开了一层子列表，于是三层缩进被压平、
     有序子列表退化成 <ul> 丢掉编号、项内的代码块把列表劈成两段还泄漏出缩进。 */
  function listBlock(lines, i0, opts) {
    var m0 = lines[i0].match(LI_RE);
    var base = m0[1].length;
    var ordered = /\d/.test(m0[2]);
    var startNo = ordered ? parseInt(m0[2], 10) : 1;
    var i = i0, items = [], isTask = false;

    while (i < lines.length) {
      if (isBlank(lines[i])) {
        // 松散列表：项与项之间的空行只是段间距，不该把一个列表切成两个 <ul>
        var k = i;
        while (k < lines.length && isBlank(lines[k])) k++;
        var nx = k < lines.length ? lines[k].match(LI_RE) : null;
        if (!nx || nx[1].length > base || /\d/.test(nx[2]) !== ordered) break;
        i = k;
      }
      var m = lines[i].match(LI_RE);
      if (!m || m[1].length > base || /\d/.test(m[2]) !== ordered) break;

      var pad = m[1].length + m[2].length + m[3].length;   // 这一项的内容起始列
      var buf = [m[4]];
      i++;
      while (i < lines.length) {
        var ln = lines[i];
        if (isBlank(ln)) {
          var j = i;
          while (j < lines.length && isBlank(lines[j])) j++;
          if (j >= lines.length || indentOf(lines[j]) < base + 1) break;   // 空行后不再缩进 → 本项结束
          while (i < j) { buf.push(""); i++; }
          continue;
        }
        if (indentOf(ln) >= base + 1) { buf.push(ln.slice(Math.min(pad, indentOf(ln)))); i++; continue; }
        if (LI_RE.test(ln)) break;            // 同级或更浅的新项
        if (BLOCK_START.test(ln)) break;      // 另起一个块
        buf.push(ln.trim());                  // 懒续行
        i++;
      }

      var t = buf[0].match(/^\[([ xX])\]\s+(.*)$/);
      var checked = false;
      if (t) { isTask = true; checked = t[1] !== " "; buf[0] = t[2]; }
      var html = renderLines(buf, opts).replace(/^<p>([\s\S]*?)<\/p>/, "$1");   // 紧凑列表：首段不套 <p>
      if (t) {
        // <label> 只包住这一项自己的首段。项里还挂着子列表时不能一起包进去，
        // 否则子项的勾选框会落在父项的 label 内，点父项等于点子项。
        var cut = html.search(/\n(?=<)/);
        var lead = cut < 0 ? html : html.slice(0, cut);
        html = '<label class="task"><input type="checkbox" disabled'
          + (checked ? " checked" : "") + ">" + lead + "</label>" + (cut < 0 ? "" : html.slice(cut));
      }
      items.push(html);
    }

    var tag = ordered ? "ol" : "ul";
    var attr = (isTask ? ' class="tasks"' : "") + (ordered && startNo !== 1 ? ' start="' + startNo + '"' : "");
    return {
      next: i,
      html: "<" + tag + attr + ">"
        + items.map(function (h) { return "<li>" + h + "</li>"; }).join("")
        + "</" + tag + ">"
    };
  }

  /* 输入必须是**已经转义**的行数组。render() 负责转义，blockquote / 列表项递归时直接复用。 */
  function renderLines(lines, opts) {
    var resolve = opts && opts.resolve;
    var out = [];
    var i = 0;

    while (i < lines.length) {
      var line = lines[i];
      if (isBlank(line)) { i++; continue; }

      // 围栏代码：info string 可以带参数（```python title=x），闭合只认光秃秃的一行围栏。
      // 内容按围栏自身的缩进对齐剥掉，免得列表项里的代码块带着多余空格显示。
      var fence = line.match(/^(\s*)(`{3,}|~{3,})\s*(\S*)([^`]*)$/);
      if (fence) {
        var indent = fence[1].length, mark = fence[2][0], lang = fence[3], buf = [];
        var closer = new RegExp("^\\s*\\" + mark + "{3,}\\s*$");
        i++;
        while (i < lines.length && !closer.test(lines[i])) {
          buf.push(lines[i].slice(Math.min(indent, indentOf(lines[i]))));
          i++;
        }
        i++;
        out.push('<pre class="code"' + (lang ? ' data-lang="' + lang + '"' : "") + "><code>" + buf.join("\n") + "</code></pre>");
        continue;
      }

      // $$ 独占几行的行间公式：原样保留（含定界符），只用 <pre> 把换行留住。
      // 不解析、不转写——渲染器认不出的公式，至少要保证人和 LLM 读到的还是原式。
      if (/^\s*\$\$\s*$/.test(line)) {
        var mbuf = [];
        i++;
        while (i < lines.length && !/^\s*\$\$\s*$/.test(lines[i])) mbuf.push(lines[i++]);
        i++;
        out.push('<pre class="math math-block">$$\n' + mbuf.join("\n") + "\n$$</pre>");
        continue;
      }

      var h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) { var lv = h[1].length; out.push("<h" + lv + ">" + inline(h[2], resolve) + "</h" + lv + ">"); i++; continue; }

      if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { out.push("<hr>"); i++; continue; }

      if (/^\s*&gt;\s?/.test(line)) {
        var q = [];
        while (i < lines.length && /^\s*&gt;\s?/.test(lines[i])) q.push(lines[i++].replace(/^\s*&gt;\s?/, ""));
        out.push("<blockquote>" + renderLines(q, opts) + "</blockquote>");
        continue;
      }

      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
        var head = cells(line), sep = lines[i + 1];
        i += 2;
        var rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(cells(lines[i++]));
        var info = columnInfo(sep, head, rows);
        var at = function (k) { return info.align[k] ? ' class="ta-' + info.align[k] + '"' : ""; };
        // data-num 是给底纹条用的「主数值」（0.943 ± 0.004 → 0.943）。
        // 它是从单元格文本现算的派生量，不是新语法：去掉这层属性一个字都不丢。
        var num = function (k, v) {
          if (!info.numeric[k]) return "";
          var n = numericValue(v);
          return isFinite(n) ? ' data-num="' + n + '"' : "";
        };
        out.push(
          '<div class="tablewrap"><table><thead><tr>' +
          head.map(function (c, k) { return "<th" + at(k) + ">" + inline(c, resolve) + "</th>"; }).join("") +
          "</tr></thead><tbody>" +
          rows.map(function (r) {
            return "<tr>" + head.map(function (_, k) {
              var v = r[k] == null ? "" : r[k];
              return "<td" + at(k) + num(k, v) + ">" + inline(v, resolve) + "</td>";
            }).join("") + "</tr>";
          }).join("") +
          "</tbody></table></div>"
        );
        continue;
      }

      if (LI_RE.test(line)) {
        var lb = listBlock(lines, i, opts);
        out.push(lb.html);
        i = lb.next;
        continue;
      }

      // 独占一段的图片渲染成 figure：图注用 "标题" 里的文字，没有就用 alt。
      // 科研笔记里的图基本都需要一句说明，否则半年后看不出画的是什么。
      // 整段一起匹配（不是只看一行）是因为 FORMAT.md §5 的示例把长图注折行写了。
      if (/^\s*!\[/.test(line)) {
        var j2 = i, chunk = [];
        while (j2 < lines.length && !isBlank(lines[j2])) chunk.push(lines[j2++]);
        var lone = chunk.join("\n").trim().match(RE_LONE_IMG);
        if (lone) {
          var cap = titleOf(lone) || lone[1];
          out.push("<figure>" + imgTag(lone[1], destOf(lone), resolve, "zoomable")
            + (cap ? "<figcaption>" + inline(cap, resolve) + "</figcaption>" : "") + "</figure>");
          i = j2;
          continue;
        }
      }

      var para = [];
      while (i < lines.length && !isBlank(lines[i]) && !(para.length && BLOCK_START.test(lines[i]))) {
        para.push(lines[i++]);
      }
      out.push("<p>" + inline(para.join("\n"), resolve, true) + "</p>");
    }
    return out.join("\n");
  }

  function render(src, opts) {
    var s = String(src == null ? "" : src).replace(/\r\n?/g, "\n");
    // 控制字符一律先剥掉，两个理由：
    // (1) 行内代码/链接用 \u0000 当占位哨兵，正文里混进真的 \u0000 会串到别的 code span 上；
    // (2) 浏览器解析 URL 前会剥掉首尾的 C0 控制字符，"\x01javascript:" 在 href 里照样执行。
    // 它们本来也不是人能读的字符，去掉不损失任何正文信息（note.md 里的原字节不受影响）。
    s = s.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "");
    return renderLines(esc(s).split("\n"), opts || {});
  }

  global.md = {
    render: render,
    esc: esc,
    safeHref: safeHref,
    isNumeric: isNumeric,
    numericValue: numericValue
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
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
        ${artifact.uri ? ' · ' + esc(artifact.uri) : ''}
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
    `<option value="" ${selectedId ? '' : 'selected'}>新的起点（无 parent）</option>`,
    ...candidates.map(node =>
      `<option value="${esc(node.id)}" ${node.id === selectedId ? 'selected' : ''}>${esc(node.title)}</option>`
    )
  ].join('');
}

/* Deterministic tidy-tree layout for the explicit parent relation.
   Missing parents become roots; no edge is inferred from time or proximity. */
function layoutGraphNodes(inputNodes) {
  const nodes = [...inputNodes].sort(nodeOrder);
  const byId = new Map(nodes.map(node => [node.id, node]));
  const children = new Map(nodes.map(node => [node.id, []]));
  const roots = [];
  nodes.forEach(node => {
    if (node.parent_id && byId.has(node.parent_id)) children.get(node.parent_id).push(node);
    else roots.push(node);
  });
  children.forEach(items => items.sort(nodeOrder));
  roots.sort(nodeOrder);

  const positions = {};
  const visiting = new Set();
  const visited = new Set();
  let nextLeaf = 0;
  const place = (node, depth) => {
    if (visited.has(node.id)) return positions[node.id].column;
    if (visiting.has(node.id)) {
      const column = nextLeaf++;
      positions[node.id] = {column, depth};
      visited.add(node.id);
      return column;
    }
    visiting.add(node.id);
    const descendants = (children.get(node.id) || []).filter(child => !visiting.has(child.id));
    let column;
    if (!descendants.length) column = nextLeaf++;
    else {
      const childColumns = descendants.map(child => place(child, depth + 1));
      column = (childColumns[0] + childColumns[childColumns.length - 1]) / 2;
    }
    positions[node.id] = {column, depth};
    visiting.delete(node.id);
    visited.add(node.id);
    return column;
  };
  roots.forEach(root => place(root, 0));
  nodes.filter(node => !visited.has(node.id)).forEach(node => place(node, 0));

  const cardWidth = 184;
  const cardHeight = 88;
  const gapX = 26;
  const gapY = 54;
  const padding = 20;
  Object.values(positions).forEach(position => {
    position.left = padding + position.column * (cardWidth + gapX);
    position.top = padding + position.depth * (cardHeight + gapY);
  });
  const maxColumn = Math.max(0, ...Object.values(positions).map(position => position.column));
  const maxDepth = Math.max(0, ...Object.values(positions).map(position => position.depth));
  return {
    nodes,
    positions,
    cardWidth,
    cardHeight,
    width: Math.max(360, padding * 2 + cardWidth + maxColumn * (cardWidth + gapX)),
    height: Math.max(168, padding * 2 + cardHeight + maxDepth * (cardHeight + gapY))
  };
}

function graphSectionHtml(chapter, nodes, showChapter = false) {
  const ordered = [...nodes].sort(nodeOrder);
  if (!ordered.length) return `
    <section class="chapter-map ${showChapter ? 'grouped' : ''}">
      ${showChapter ? `<button class="chapter-map-title" type="button" data-focus-chapter="${esc(chapter.id)}">${esc(chapter.name)}<span>0 条</span></button>` : ''}
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
    return `<path d="M ${x1} ${y1} V ${middle} H ${x2} V ${y2}"></path>`;
  }).join('');
  const cards = ordered.map(node => {
    const position = layout.positions[node.id];
    const [reviewClass, reviewLabel] = nodeReview(node);
    const selected = S.selectedNodeId === node.id;
    const unresolved = (node.comments || []).some(comment => comment.kind === 'correction' && !comment.resolved_at);
    return `
      <button class="graph-node ${reviewClass} ${selected ? 'selected' : ''} ${unresolved ? 'needs-review' : ''}"
        type="button" data-select-node="${esc(node.id)}" aria-pressed="${selected}"
        aria-label="记录 ${ordinal.get(node.id)}：${esc(node.title)}，${esc(reviewLabel)}"
        title="${esc(node.id)}" style="left:${position.left}px;top:${position.top}px">
        <span class="graph-node-meta"><span>${ordinal.get(node.id)}</span><time>${fmt(node.occurred_at)}</time></span>
        <strong>${esc(node.title)}</strong>
        <span class="graph-node-state">${esc(reviewLabel)}${unresolved ? ' · 有待处理纠正' : ''}</span>
      </button>
    `;
  }).join('');
  return `
    <section class="chapter-map ${showChapter ? 'grouped' : ''}">
      ${showChapter ? `<button class="chapter-map-title" type="button" data-focus-chapter="${esc(chapter.id)}">${esc(chapter.name)}<span>${ordered.length} 条</span></button>` : ''}
      <div class="graph-viewport">
        <div class="graph-canvas" style="width:${layout.width}px;height:${layout.height}px">
          <svg class="graph-edges" viewBox="0 0 ${layout.width} ${layout.height}" width="${layout.width}" height="${layout.height}" aria-hidden="true">${edges}</svg>
          ${cards}
        </div>
      </div>
    </section>
  `;
}

function listSectionHtml(chapter, nodes, showChapter = false) {
  const ordered = [...nodes].sort(nodeOrder);
  const ordinal = new Map(ordered.map((node, index) => [node.id, String(index + 1).padStart(2, '0')]));
  return `
    <section class="chapter-map record-group ${showChapter ? 'grouped' : ''}">
      ${showChapter ? `<button class="chapter-map-title" type="button" data-focus-chapter="${esc(chapter.id)}">${esc(chapter.name)}<span>${ordered.length} 条</span></button>` : ''}
      ${ordered.length ? `<div class="record-list">${ordered.map((node, index) => {
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
      }).join('')}</div>` : '<div class="structure-empty">这个 Chapter 还没有记录。</div>'}
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
function layoutDataflowNodes(nodes, edges) {
  const byId = new Map(nodes.map(node => [node.id, node]));
  const incoming = new Map(nodes.map(node => [node.id, []]));
  edges.forEach(edge => {
    if (!byId.has(edge.from_node_id) || !byId.has(edge.to_node_id)) return;
    if (edge.from_node_id === edge.to_node_id) return;
    incoming.get(edge.to_node_id).push(edge.from_node_id);
  });
  const depth = new Map();
  const visiting = new Set();
  const depthOf = id => {
    if (depth.has(id)) return depth.get(id);
    if (visiting.has(id)) return 0;
    visiting.add(id);
    let value = 0;
    (incoming.get(id) || []).forEach(from => {
      value = Math.max(value, depthOf(from) + 1);
    });
    visiting.delete(id);
    depth.set(id, value);
    return value;
  };
  const ordered = [...nodes].sort(nodeOrder);
  ordered.forEach(node => depthOf(node.id));

  const cardWidth = 184;
  const cardHeight = 88;
  const gapX = 26;
  const gapY = 62;
  const padding = 20;
  const positions = {};
  const filled = new Map();
  let maxColumn = 0;
  let maxDepth = 0;
  ordered.forEach(node => {
    const row = depth.get(node.id) || 0;
    const column = filled.get(row) || 0;
    filled.set(row, column + 1);
    positions[node.id] = {
      depth: row,
      column,
      left: padding + column * (cardWidth + gapX),
      top: padding + row * (cardHeight + gapY)
    };
    maxColumn = Math.max(maxColumn, column);
    maxDepth = Math.max(maxDepth, row);
  });
  return {
    nodes: ordered,
    positions,
    cardWidth,
    cardHeight,
    width: Math.max(360, padding * 2 + cardWidth + maxColumn * (cardWidth + gapX)),
    height: Math.max(168, padding * 2 + cardHeight + maxDepth * (cardHeight + gapY))
  };
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
        title="${esc(node.id)}" style="left:${position.left}px;top:${position.top}px">
        <span class="graph-node-meta"><span>${ordinal.get(node.id)}</span><time>${fmt(node.occurred_at)}</time></span>
        <strong>${esc(node.title)}</strong>
        <span class="graph-node-state">${esc(chapter)}</span>
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

function rawRowHtml(item) {
  const body = item.kind === 'event'
    ? JSON.stringify(item.payload, null, 2)
    : String(item.preview || '');
  return `
    <div class="raw-row">
      <div><strong>${esc(item.kind === 'event' ? item.event_type : 'transcript')}</strong>
        <span class="meta">${fmt(item.at)} · session ${esc(item.session_id || '—')} · agent ${esc(item.agent_id || 'main')}</span>
      </div>
      <pre>${esc(body)}</pre>
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
   没有登记时退回项目最近的原始历史，而不是给一个死按钮。 */
async function showNodeRaw(nodeId) {
  const node = S.project.nodes.find(item => item.id === nodeId);
  const label = node ? node.title : nodeId;
  const sources = (node && node.source_event_ids) || [];
  setModal('原始历史 · ' + label, '<div class="meta">加载中…</div>');
  try {
    const value = await api('/api/projects/' + encodeURIComponent(S.project.id) + '/raw?limit=200');
    const matched = sources.length
      ? value.items.filter(item => sources.includes(item.id))
      : [];
    const items = matched.length ? matched : value.items;
    const note = !sources.length
      ? '这条记录没有登记来源 event，下面是项目最近的原始历史。'
      : (matched.length
        ? `这条记录登记了 ${sources.length} 条来源 event，其中 ${matched.length} 条已在中央。`
        : `这条记录登记的 ${sources.length} 条来源 event 还没有出现在中央，先显示项目最近的原始历史。`);
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
    button.onclick = () => {
      const nodeId = button.dataset.attachNode;
      setModal(
        '添加附件或外部产物',
        `
          <label for="fieldFile">小文件（可空，服务默认上限 10 MB）</label><input id="fieldFile" type="file">
          <label for="fieldArtifactName">显示名称</label><input id="fieldArtifactName">
          <div class="grid2">
            <div><label for="fieldDirection">角色</label><select id="fieldDirection"><option value="reference">reference</option><option value="input">input</option><option value="output">output</option></select></div>
            <div><label for="fieldMachine">机器（外部路径可填）</label><input id="fieldMachine"></div>
          </div>
          <label for="fieldExternal">外部路径（大数据/模型只登记位置）</label><input id="fieldExternal">
          <label for="fieldUri">URI（可选）</label><input id="fieldUri">
        `,
        async () => {
          const file = $('#fieldFile').files[0];
          const value = {
            project_id: S.project.id,
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
    };
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

/* §13 的容量告警。备份撞上 GitHub 的上限是渐进发生的：等到 push 被拒才知道，
   就已经有一轮备份没写进去了。backup.capacity 是 sync_git_backup 每轮算出来的
   （backup._capacity → server 的 backup_state → /api/health），这里是它唯一的
   落点——不渲染的话那次计算等于没做。 */
function backupCapacityLines(capacity) {
  if (!capacity) return [];
  const facts = [
    `导出 ${bytesLabel(capacity.export_bytes)}`,
    capacity.repository_bytes === null || capacity.repository_bytes === undefined
      ? '' : `仓库 ${bytesLabel(capacity.repository_bytes)}`,
    capacity.volumes ? `${esc(capacity.volumes)} 个分卷` : '',
    capacity.largest_file ? `最大文件 ${esc(capacity.largest_file)} ${bytesLabel(capacity.largest_file_bytes)}` : ''
  ].filter(Boolean).join(' · ');
  const critical = String(capacity.level || '') === 'critical';
  return [facts, ...(capacity.warnings || []).map(
    item => critical ? `<span class="danger">${esc(item)}</span>` : esc(item)
  )];
}

function backupHealthHtml(value) {
  const backup = value.backup || {};
  if (!backup.enabled) {
    return healthCardHtml('GitHub 每日备份', 'unknown', ['服务没有配置 --backup-repo，没有灾备副本。']);
  }
  /* 本地 commit 成功但 push 失败时远端会静静落后好几周，而 last_success_at
     照样在往前走。unpushed_commits 是唯一能把这件事说出来的字段。 */
  const behind = Number(backup.unpushed_commits || 0);
  const capacity = backup.capacity || null;
  const level = capacity ? String(capacity.level || 'ok') : 'ok';
  const missing = backup.missing_objects || [];
  const state = level === 'critical'
    ? 'critical'
    : ((backup.error || behind || level === 'warn' || missing.length)
      ? 'warn'
      : (backup.last_success_at ? 'ok' : 'unknown'));
  return healthCardHtml('GitHub 每日备份', state, [
    backup.running ? '正在导出…' : '',
    `最近尝试 ${fmt(backup.last_attempt_at) || '—'} · 最近成功 ${fmt(backup.last_success_at) || '—'}`,
    backup.changed === null || backup.changed === undefined ? '' : `上次导出${backup.changed ? '有变化并已 commit' : '内容未变化'}${backup.pushed ? ' · 已 push' : ''}`,
    behind ? `<span class="danger">远端落后 ${esc(behind)} 个 commit：上一轮 push 没成功，下一轮会补推。</span>` : '',
    ...backupCapacityLines(capacity),
    /* 大产物只备份引用元数据，但小附件的对象文件确实可能在数据卷上丢了。
       导出不因此中止，可是「备份里没有这些字节」必须有人看得见。 */
    missing.length
      ? `<span class="danger">${esc(missing.length)} 个附件对象在导出时已不存在，备份里没有它们的字节。</span>`
      : '',
    backup.error ? `<span class="danger">${esc(backup.error)}</span>` : ''
  ]);
}

async function showHealth() {
  setModal('采集与备份状态', '<div class="meta">加载中…</div>');
  try {
    const value = await api('/api/health');
    setModal('采集与备份状态', [
      outboxHealthHtml(value),
      recorderHealthHtml(value),
      backupHealthHtml(value),
      healthCardHtml('中央存储', (value.ok && !value.anonymous_read) ? 'ok' : 'warn', [
        `schema v${esc(value.schema_version ?? '—')} · 项目 ${esc((value.counts || {}).projects ?? '—')} 个 · 附件 ${esc((value.counts || {}).attachments ?? '—')} 个`,
        value.write_protected ? '写入需要设备凭证或登录。' : '写入未受保护（仅限本机开发）。',
        /* 未配 OAuth 时读取是完全公开的，包括原始 transcript 和附件下载。
           这件事只在服务端启动横幅里说过，用的人看不到。 */
        value.anonymous_read ? '<span class="danger">未配置 GitHub OAuth：任何能连到这个端口的人都能读取全部原始历史与附件。</span>' : '',
        value.purge_generation ? `已执行 ${esc(value.purge_generation)} 次紧急 purge · 最近一次 ${fmt((value.last_purge || {}).created_at) || '—'}` : ''
      ])
    ].join(''));
  } catch (error) {
    setModal('采集与备份状态', `<div class="danger">${esc(error.message)}</div>`);
  }
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
  node: '记录', comment: '评论', overview: 'Overview', event: '原始 event', transcript: 'transcript'
};

function searchHitHtml(hit) {
  const title = hit.title || hit.name || hit.event_type || hit.kind || hit.scope;
  const when = hit.occurred_at || hit.captured_at || hit.created_at || hit.updated_at;
  const isRaw = hit.scope === 'event' || hit.scope === 'transcript';
  // 命中评论时跳到它挂着的那条记录，而不是只把项目打开。
  const nodeId = hit.scope === 'node' ? hit.id : (hit.target_type === 'node' ? hit.target_id : '');
  return `
    <button class="hit" type="button" data-hit-project="${esc(hit.project_id || '')}"
      data-hit-node="${esc(nodeId || '')}" data-hit-raw="${isRaw ? '1' : ''}">
      <b>${esc(title)}</b>
      <div>${esc(String(hit.body || hit.overview || '').slice(0, 260))}</div>
      <small><span class="hit-project">${esc(projectName(hit.project_id))}</span> · ${esc(SEARCH_SCOPE_LABEL[hit.scope] || hit.scope)} · ${fmt(when)}</small>
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
    S.chapter = null;
    S.selectedNodeId = null;
  }
  renderSide();
  renderMain();
  if (button.dataset.hitRaw) {
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
