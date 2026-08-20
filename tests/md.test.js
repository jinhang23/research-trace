/* markdown 渲染器的断言。跑：node --test tests/md.test.js
 *
 * 渲染器嵌在 research_trace/webapp.py 的页面脚本里（整个前端是一个自包含的字符串，
 * 不走 CDN），所以这里按标记把它抠出来 eval。抠不到就直接报错 —— 标记被改掉时
 * 必须是「测试挂了」，而不是「测试悄悄测了个空气」。
 *
 * 这是整个系统里最容易出错的一块（正则一层叠一层），而且正文是人和 agent 通过 API
 * 写进来的**不可信输入**，所以安全断言排在最前面。
 */
const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const SOURCE = fs.readFileSync(
  path.join(__dirname, "..", "research_trace", "webapp.py"), "utf8");
const BEGIN = "/* === markdown renderer (begin) === */";
const END = "/* === markdown renderer (end) === */";
const from = SOURCE.indexOf(BEGIN);
const to = SOURCE.indexOf(END);
if (from < 0 || to < 0) {
  throw new Error("在 webapp.py 里找不到渲染器的起止标记；抠不出来就没法测");
}
(0, eval)(SOURCE.slice(from + BEGIN.length, to));

const { render, safeHref, numericValue } = globalThis.md;

const R = (s, opts) => render(s, opts || {});
/* 洞察面板（app.js renderInsights）传的 resolve 是恒等函数，相对路径原样落进 href。
   安全断言必须按这个最坏情况来测，用带重写的 resolve 会把问题遮住。 */
const RAW = { resolve: (h) => h };

/* ---------------------------------------------------------------- 安全 */

test("正文里写不进裸 HTML —— 尖括号全被转义成文本", () => {
  const h = R('<script>alert(1)</script><img src=x onerror=alert(1)>');
  assert.ok(!/<script/i.test(h), h);
  assert.ok(!/<img[^>]*onerror/i.test(h), h);
  assert.ok(h.includes("&lt;script&gt;"));
  assert.equal(h.match(/<[a-z]/gi).length, 1, "只应当有我们自己生成的那个 <p>");
});

test("javascript: 链接被打断", () => {
  assert.ok(R("[点我](javascript:alert(1))").includes('href="#"'));
  assert.ok(R('![x](javascript:alert(1))').includes('src="#"'));
});

/* ---------------------------------------------------------------- 表格 */

test("表格渲染出完整的行列", () => {
  const h = R(["| 模型 | RMSE |", "|---|---|", "| 基线 | 1.42 |", "| 新 | 1.24 |"].join("\n"));
  assert.equal((h.match(/<tr>/g) || []).length, 3);
  assert.equal((h.match(/<td/g) || []).length, 4);
  assert.ok(h.includes("<th") && h.includes("模型"));
});

test("显式列对齐生效", () => {
  const h = R(["| a | b | c |", "|:---|:---:|---:|", "| 1 | 2 | 3 |"].join("\n"));
  assert.ok(h.includes('<th class="ta-left">'), h);
  assert.ok(h.includes('<th class="ta-center">'));
  assert.ok(h.includes('<th class="ta-right">'));
});

test("整列都是数字时自动右对齐，文字列不受影响", () => {
  const h = R(["| 模型 | 准确率 |", "|---|---|", "| 基线 | 0.897 |", "| 新 | **0.951** |"].join("\n"));
  const ths = h.match(/<th(?=[\s>])[^>]*>/g);
  assert.equal(ths[0], "<th>", "文字列不该被右对齐");
  assert.equal(ths[1], '<th class="ta-right">', "数字列（含 **加粗**）应当右对齐");
});

test("数字列里的占位横线不破坏自动对齐", () => {
  const h = R(["| x | v |", "|---|---|", "| a | 1.2 |", "| b | — |"].join("\n"));
  assert.ok(h.includes('<th class="ta-right">'));
});

test("混了文字的列不会被误判成数字列", () => {
  const h = R(["| x | v |", "|---|---|", "| a | 1.2 |", "| b | 待定 |"].join("\n"));
  assert.equal(h.match(/<th(?=[\s>])[^>]*>/g)[1], "<th>");
});

test("缺列的行补成空单元格而不是塌掉", () => {
  const h = R(["| a | b | c |", "|---|---|---|", "| 1 |"].join("\n"));
  assert.equal((h.match(/<td/g) || []).length, 3);
});

/* ---------------------------------------------------------------- 图片 */

test("独占一段的图片渲染成 figure，标题当图注", () => {
  const h = R('![loss 曲线](loss.png "训练 30 轮，第 12 轮开始过拟合")');
  assert.ok(h.includes("<figure>"), h);
  assert.ok(h.includes("<figcaption>训练 30 轮，第 12 轮开始过拟合</figcaption>"));
  assert.ok(h.includes('class="zoomable"'));
});

test("没有标题时用 alt 当图注", () => {
  assert.ok(R("![loss 曲线](loss.png)").includes("<figcaption>loss 曲线</figcaption>"));
});

test("alt 和标题都为空时不渲染图注", () => {
  const h = R("![](loss.png)");
  assert.ok(h.includes("<figure>") && !h.includes("<figcaption>"));
});

test("句子中间的图片保持行内，不变成 figure", () => {
  const h = R("如图 ![x](a.png) 所示");
  assert.ok(!h.includes("<figure>"), h);
  assert.ok(h.includes("inline-img"));
});

test("相对路径交给 resolve 重写，绝对地址原样保留", () => {
  const h = R("![](fig.png)\n\n[外链](https://example.com)", { resolve: (p) => "/files/007/" + p });
  assert.ok(h.includes('src="/files/007/fig.png"'), h);
  assert.ok(h.includes('href="https://example.com"'));
  assert.ok(h.includes('target="_blank"'));
});

/* ---------------------------------------------------------------- 其它块 */

test("围栏代码原样保留内容，并带上语言标记", () => {
  const h = R("```python\nfor i in range(3):\n    print(i * 2)\n```");
  assert.ok(h.includes('data-lang="python"'));
  assert.ok(h.includes("print(i * 2)"));
  assert.ok(!h.includes("<em>"), "代码里的星号不该被当成强调");
});

test("行内代码里的星号不被当成强调", () => {
  assert.ok(R("用 `a * b` 相乘").includes("<code>a * b</code>"));
});

test("任务列表渲染成勾选框", () => {
  const h = R("- [ ] 去重后重训\n- [x] 聚类跑完");
  assert.ok(h.includes('class="tasks"'));
  assert.equal((h.match(/<input type="checkbox" disabled/g) || []).length, 2);
  assert.equal((h.match(/checked/g) || []).length, 1);
});

test("裸链接自动识别", () => {
  assert.ok(R("见 https://arxiv.org/abs/1234.5678 第 3 节").includes('href="https://arxiv.org/abs/1234.5678"'));
});

test("裸链接不会破坏已有的 [文字](链接)", () => {
  const h = R("[这篇论文](https://arxiv.org/abs/1)");
  assert.equal((h.match(/<a /g) || []).length, 1, h);
});

test("wikilink 变成可跳转的内部链接", () => {
  const h = R("综合了 [[003b]] 的结论");
  assert.ok(h.includes('data-goto="003b"'));
});

test("引用块递归渲染内部结构", () => {
  const h = R("> ## 小标题\n> - 一项");
  assert.ok(h.includes("<blockquote>") && h.includes("<h2>") && h.includes("<li>"));
});

test("嵌套列表保留层级", () => {
  const h = R("- 顶层\n  - 子项\n- 另一个顶层");
  assert.ok(h.includes("<ul><li>"), h);
  assert.equal((h.match(/<ul/g) || []).length, 2);
});

test("五个小节的标题都渲染成 h2", () => {
  const h = R(["## 为什么", "a", "## 做了什么", "b", "## 结果", "c", "## 结论", "d", "## 下一步", "e"].join("\n"));
  assert.equal((h.match(/<h2>/g) || []).length, 5);
});

test("空正文不炸", () => {
  assert.equal(R(""), "");
  assert.equal(R(null), "");
});

test("CRLF 与 BOM 不影响解析", () => {
  assert.ok(R("## 标题\r\n\r\n正文").includes("<h2>标题</h2>"));
});

test("同样的输入产出同样的输出", () => {
  const src = "## 结果\n\n| a | b |\n|---|--:|\n| x | 1.2 |\n\n![图](a.png \"说明\")";
  assert.equal(R(src), R(src));
});

/* ======================================================= 附件名里的空格与括号
   trace_mcp._md_ref 对含空格/括号的路径会生成 CommonMark 的 <...> 形式，
   网页拖拽上传则直接插入裸路径。两种都必须能渲染出图，否则
   `loss curve (run 42).png` 这类再普通不过的文件名在正文里就只剩一串源码。 */

test("文件名带空格时 <尖括号> 形式的图片仍渲染成 img 而不是裸源码", () => {
  const h = R('![](<loss curve.png> "第 12 轮后验证集回升")', { resolve: (p) => "/files/3/" + p });
  assert.ok(h.includes('src="/files/3/loss curve.png"'), h);
  assert.ok(!h.includes("&lt;"), "尖括号是语法，不该原样显示出来");
  assert.ok(h.includes("<figcaption>第 12 轮后验证集回升</figcaption>"));
});

test("文件名带空格时 <尖括号> 形式的普通附件链接也认得", () => {
  const h = R("见 [数据](<data set.csv>)", { resolve: (p) => "/files/3/" + p });
  assert.ok(h.includes('href="/files/3/data set.csv"'), h);
});

test("文件名里的成对括号不把 src 截断", () => {
  const h = R("如图 ![x](fig(1).png) 所示");
  assert.ok(h.includes('src="fig(1).png"'), h);
  assert.ok(!h.includes(".png)"), "不该有残留的裸文本 .png)");
});

test("空格加括号的文件名（loss curve (run 42).png）完整保留", () => {
  const h = R("![](<loss curve (run 42).png>)", RAW);
  assert.ok(h.includes('src="loss curve (run 42).png"'), h);
});

test("图注的 HTML 转义没被 <尖括号> 支持弄坏", () => {
  // 标题里的引号在整体转义后是 &quot;，正则若按 " 写会整条匹配不上
  assert.ok(R('![a](b.png "说明")').includes("<figcaption>说明</figcaption>"));
  assert.ok(R("![a](b.png '说明')").includes("<figcaption>说明</figcaption>"));
});

test("FORMAT.md 那样折行写的长图注仍然进 figcaption", () => {
  const h = R('![](loss.png "第 12 轮之后验证集回升，\n说明再往后就是纯过拟合。")');
  assert.ok(h.includes("<figure>"), h);
  assert.ok(h.includes("纯过拟合"), h);
});

/* ================================================== 表格里的 \| 是字面竖线
   从 Excel 粘贴时 app.js 会把单元格内的竖线转义成 \|。解析器不认的话，
   那一格被劈成两格、整行右移，最右边一列被 head.map 静默截掉。 */

test("表格单元格里的 \\| 不当分隔符，右边的列不被挤掉", () => {
  const h = R(["| 命令 | 说明 |", "|---|---|", "| cat a \\| wc -l | 数行 |"].join("\n"));
  assert.equal((h.match(/<td/g) || []).length, 2, h);
  assert.ok(h.includes("<td>cat a | wc -l</td>"), h);
  assert.ok(h.includes("数行"), "第二列的原值不该被顶掉");
});

test("一格里多个 \\| 也不会顶掉右侧多列", () => {
  const h = R(["| 取值 | 含义 |", "|---|---|", "| 低\\|中\\|高 | 等级 |"].join("\n"));
  assert.ok(h.includes("<td>低|中|高</td>"), h);
  assert.ok(h.includes("等级"), h);
});

test("以 \\| 结尾的单元格不被当成行尾收口竖线", () => {
  const h = R(["| a | b |", "|---|---|", "| x\\| | y |"].join("\n"));
  assert.equal((h.match(/<td/g) || []).length, 2, h);
  assert.ok(h.includes("<td>x|</td>"), h);
});

/* ============================================ 带方差的数值列（FORMAT.md §4）
   FORMAT.md 明写「有方差就写进去（0.943 ± 0.004）」，又承诺数值列自动右对齐
   并带底纹条。两条不能互相打架——照着标准写不该反而丢掉可视化。 */

test("0.943 ± 0.004 仍算数值列，右对齐不被方差关掉", () => {
  const h = R(["| 模型 | 准确率 |", "|---|---|", "| 基线 | 0.897 ± 0.010 |", "| 新 | **0.943 ± 0.004** |"].join("\n"));
  const ths = h.match(/<th(?=[\s>])[^>]*>/g);
  assert.equal(ths[1], '<th class="ta-right">', h);
});

test("底纹条按主数值算，误差项不参与", () => {
  const h = R(["| 模型 | 准确率 |", "|---|---|", "| 基线 | 0.897 ± 0.010 |", "| 新 | **0.943 ± 0.004** |"].join("\n"));
  assert.ok(h.includes('data-num="0.897"'), h);
  assert.ok(h.includes('data-num="0.943"'), h);
  assert.equal(numericValue("0.943 ± 0.004"), 0.943);
  assert.equal(numericValue("0.943±0.004"), 0.943);
  assert.equal(numericValue("0.943 +/- 0.004"), 0.943);
});

test("千分位 / 百分号 / 科学计数 / 负号都算数值", () => {
  const h = R(["| a | b | c |", "|---|---|---|", "| 1,024 | 87% | -4.5E+2 |", "| 2,048 | 91% | 1.2e-3 |"].join("\n"));
  const ths = h.match(/<th(?=[\s>])[^>]*>/g);
  assert.deepEqual(ths, ['<th class="ta-right">', '<th class="ta-right">', '<th class="ta-right">'], h);
  assert.equal(numericValue("1,024"), 1024);
  assert.equal(numericValue("-4.5E+2"), -450);
});

test("带单位的 40 s 仍然是文字列 —— FORMAT.md 说这是对的，别为了对齐去掉单位", () => {
  const h = R(["| 模型 | 训练耗时 |", "|---|---|", "| 基线 | 40 s |", "| 新 | 18 min |"].join("\n"));
  assert.equal(h.match(/<th(?=[\s>])[^>]*>/g)[1], "<th>", h);
  assert.ok(!h.includes("data-num"), "文字列不该带主数值");
  assert.ok(Number.isNaN(numericValue("40 s")));
});

test("占位横线的格子不产出 data-num，也不该让整列失去数值身份", () => {
  const h = R(["| x | v |", "|---|---|", "| a | 1.2 |", "| b | — |"].join("\n"));
  assert.ok(h.includes('<th class="ta-right">'), h);
  assert.equal((h.match(/data-num/g) || []).length, 1, h);
});

/* ==================================================== CommonMark 常见结构 */

test("有序列表的子列表仍是 ol，编号不丢", () => {
  const h = R("1. a\n   1. b\n   2. c\n2. d");
  assert.equal((h.match(/<ol/g) || []).length, 2, h);
  assert.ok(!h.includes("<ul"), "有序子列表不该退化成无序");
});

test("有序列表从 3 开始时保留起始编号", () => {
  assert.ok(R("3. 三\n4. 四").includes('<ol start="3">'));
});

test("三层缩进不被压平成两层", () => {
  const h = R("- L1\n  - L2\n    - L3");
  assert.equal((h.match(/<ul/g) || []).length, 3, h);
});

test("列表项里的围栏代码不把列表劈开，也不泄漏缩进", () => {
  const h = R("- 步骤\n  ```bash\n  cmd --x\n  ```\n- 下一步");
  assert.equal((h.match(/<ul/g) || []).length, 1, h);
  assert.ok(h.includes("<code>cmd --x</code>"), "代码内容不该带上列表的缩进：" + h);
  assert.ok(h.includes('data-lang="bash"'));
});

test("项与项之间的空行不把一个列表切成两个", () => {
  const h = R("- 第一项\n\n- 第二项");
  assert.equal((h.match(/<ul/g) || []).length, 1, h);
  assert.equal((h.match(/<li>/g) || []).length, 2, h);
});

test("嵌套任务列表里子项的勾选框不落在父项的 label 内", () => {
  const h = R("- [ ] 顶层\n  - [x] 子项");
  assert.ok(/<label class="task"><input type="checkbox" disabled>顶层<\/label>/.test(h), h);
});

test("表格单元格里的行内代码 / 链接 / 加粗都渲染", () => {
  const h = R(["| a | b |", "|---|---|", "| `code` | [x](https://e.com) **粗** |"].join("\n"));
  assert.ok(h.includes("<code>code</code>"), h);
  assert.ok(h.includes('href="https://e.com"'), h);
  assert.ok(h.includes("<strong>粗</strong>"), h);
});

test("代码块里的 markdown 一律不解析", () => {
  const h = R("```\n| a | b |\n- 列表\n**粗**\n```");
  assert.ok(!h.includes("<table"), h);
  assert.ok(!h.includes("<li>"), h);
  assert.ok(h.includes("**粗**"), h);
});

test("下划线强调生效，但 some_var_name 不被劈开", () => {
  const h = R("_不确定_ 与 __确定__，但 some_var_name 不变");
  assert.ok(h.includes("<em>不确定</em>"), h);
  assert.ok(h.includes("<strong>确定</strong>"), h);
  assert.ok(h.includes("some_var_name"), h);
});

test("删除线与水平线", () => {
  const h = R("~~废弃~~\n\n---\n\n后文");
  assert.ok(h.includes("<del>废弃</del>"), h);
  assert.ok(h.includes("<hr>"), h);
});

test("引用块里可以放表格和嵌套列表", () => {
  const h = R("> | a | b |\n> |---|---|\n> | 1 | 2 |");
  assert.ok(h.includes("<blockquote>") && h.includes("<table>"), h);
});

test("含成对括号的裸 URL 不被截成坏链接", () => {
  const h = R("见 https://en.wikipedia.org/wiki/Foo_(bar) 第 3 节");
  assert.ok(h.includes('href="https://en.wikipedia.org/wiki/Foo_(bar)"'), h);
});

test("强调规则不污染已经生成的 href", () => {
  const h = R("[x](https://a.com/**b**/c)");
  assert.ok(h.includes('href="https://a.com/**b**/c"'), h);
  assert.ok(!/href="[^"]*<strong>/.test(h), h);
});

test("反斜杠转义按 CommonMark 生效，但 Windows 路径不被吃掉", () => {
  const h = R("字面 \\* 星号，路径 C:\\Users\\wei\\data");
  assert.ok(h.includes("字面 * 星号"), h);
  assert.ok(!h.includes("<em>"), h);
  assert.ok(h.includes("C:\\Users\\wei\\data"), "路径里的反斜杠后面不是 ASCII 标点，必须原样保留：" + h);
});

test("<https://…> 自动链接", () => {
  assert.ok(R("见 <https://arxiv.org/abs/1>").includes('href="https://arxiv.org/abs/1"'));
});

/* 数学公式：不引 KaTeX（零依赖硬约束），但必须原样留住 —— 被 * _ \ 的规则啃过
   就再也读不回原式，人和 LLM 双双丢信息。 */

test("行内公式原样保留，不被强调和反斜杠规则啃掉", () => {
  const h = R("误差 $\\alpha_i \\pm 0.01$ 见下");
  assert.ok(h.includes("$\\alpha_i \\pm 0.01$"), h);
  assert.ok(!h.includes("<em>"), h);
});

test("$$ 行间公式整段原样保留，换行不丢", () => {
  const h = R("$$\n\\sum_{i=1}^{n} x_i^2\n$$");
  assert.ok(h.includes("\\sum_{i=1}^{n} x_i^2"), h);
  assert.ok(h.includes("$$"), "定界符要留着，grep 得到才算没丢信息");
});

test("货币写法不被误当成公式", () => {
  const h = R("花了 $5 又花了 $3");
  assert.ok(!h.includes('class="math"'), h);
  assert.ok(h.includes("$5") && h.includes("$3"), h);
});

/* =============================================================== XSS 面
   正文是人和 agent 通过 API 写进来的不可信输入，洞察面板还用恒等 resolve
   直接把地址落进 href，所以每一条都要钉住。 */

test("拒绝前置控制字符绕过的 javascript: 链接", () => {
  const h = R("[点我](\u0001javascript:alert(1))", RAW);
  assert.ok(h.includes('href="#"'), h);
  assert.ok(!/javascript:/i.test(h), h);
  assert.equal(safeHref("\u0001javascript:alert(1)"), "#");
  assert.equal(safeHref("\u0000\u001fdata:text/html,x"), "#");
});

test("拒绝 <尖括号> 目标里夹了制表符/换行的 javascript:", () => {
  assert.ok(R("[点我](<java\tscript:alert(1)>)", RAW).includes('href="#"'));
  assert.equal(safeHref("java\tscript:alert(1)"), "#");
});

test("拒绝 &#106;avascript: 这类实体绕过", () => {
  assert.ok(R("[点我](&#106;avascript:alert(1))", RAW).includes('href="#"'));
  assert.equal(safeHref("&#106;avascript:alert(1)"), "#");
  assert.equal(safeHref("&#x6a;avascript:alert(1)"), "#");
  assert.equal(safeHref("javascript&colon;alert(1)"), "#");
});

test("大小写混写和前后空白不能绕过协议黑名单", () => {
  assert.equal(safeHref("  JaVaScRiPt:alert(1)"), "#");
  assert.equal(safeHref("VBScript:msgbox(1)"), "#");
  assert.ok(R("[a](VBScript:msgbox(1))", RAW).includes('href="#"'));
});

test("safeHref 返回的是剥干净控制字符的地址，不把剥离工作留给浏览器", () => {
  assert.equal(safeHref("https://e.com/\u0001x", null), "https://e.com/x");
});

test("图片的 src 走同一道闸门", () => {
  assert.ok(R("![x](\u0001javascript:alert(1))", RAW).includes('src="#"'));
  assert.ok(R("![x](data:text/html;base64,PHNjcmlwdD4=)", RAW).includes('src="#"'));
});

test("iframe / style / 注释里藏的标签一律只是文本", () => {
  const h = R("<iframe src=x></iframe>\n<style>body{}</style>\n<!-- <script>alert(1)</script> -->");
  assert.ok(!/<(iframe|style|script)/i.test(h), h);
  assert.ok(h.includes("&lt;iframe"), h);
});

test("on* 事件属性写不进任何标签", () => {
  const h = R("<svg onload=alert(1)>\n\n[<img src=x onerror=alert(1)>](https://e.com)");
  assert.ok(!/<[a-z][^>]*\son[a-z]+=/i.test(h), h);
});

test("表格单元格和图注里的裸 HTML 同样进不去", () => {
  const t = R(["| a |", "|---|", "| <img src=x onerror=alert(1)> |"].join("\n"));
  assert.ok(!/<img[^>]*onerror/i.test(t), t);
  const f = R('![a](b.png "<script>alert(1)</script>")');
  assert.ok(!/<script/i.test(f), f);
});

test("正文里的 NUL 不再串掉行内代码的内容", () => {
  const h = R("`x` \u00000\u0000");
  assert.equal((h.match(/<code>/g) || []).length, 1, h);
  assert.ok(!h.includes("undefined"), h);
});
