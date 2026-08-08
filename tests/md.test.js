/* markdown 渲染器的断言。跑：node --test tests/
 *
 * 这是整个系统里最容易出错的一块（正则一层叠一层），而且它决定了人读到的东西
 * 长什么样，所以单独测。
 */
const { test } = require("node:test");
const assert = require("node:assert");

require("../web/md.js");
const { render } = globalThis.md;

const R = (s, opts) => render(s, opts || {});

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
