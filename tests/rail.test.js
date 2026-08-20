/* 列表装订线的车道分配。跑：node --test tests/rail.test.js
 *
 * 和 md.test.js 一样按标记从 research_trace/webapp.py 里把函数抠出来 eval，
 * 抠不到就直接报错。车道算错不会抛异常，只会画出一张读不懂的图 —— 正是那种
 * 没有测试就永远发现不了的东西。
 */
const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const SOURCE = fs.readFileSync(
  path.join(__dirname, "..", "research_trace", "webapp.py"), "utf8");
const BEGIN = "/* === rail lanes (begin) === */";
const END = "/* === rail lanes (end) === */";
const from = SOURCE.indexOf(BEGIN), to = SOURCE.indexOf(END);
if (from < 0 || to < 0) throw new Error("在 webapp.py 里找不到 rail lanes 的起止标记");
const railLanes = (0, eval)(SOURCE.slice(from + BEGIN.length, to) + "; railLanes");

const N = (id, parent) => ({id, parent_id: parent || null});
const lanesOf = nodes => { const r = railLanes(nodes); return nodes.map(n => r.lane.get(n.id)); };

test("一条直链只占一条车道", () => {
  const chain = [N("a"), N("b", "a"), N("c", "b"), N("d", "c")];
  assert.deepEqual(lanesOf(chain), [0, 0, 0, 0]);
  assert.equal(railLanes(chain).width, 1);
});

test("分叉时第一个孩子续用父车道，第二个另开一条", () => {
  const nodes = [N("a"), N("b", "a"), N("c", "a")];
  assert.deepEqual(lanesOf(nodes), [0, 0, 1]);
});

test("走完的支线把车道让出来给后面的用", () => {
  // a→b（b 是叶子，占了 0 之后就该释放），然后 c 是新的根，应当复用车道 0
  const nodes = [N("a"), N("b", "a"), N("c")];
  assert.deepEqual(lanesOf(nodes), [0, 0, 0]);
  assert.equal(railLanes(nodes).width, 1, "不释放的话每条走完的链都会永久占一列");
});

test("并列的两条根各占一条，互不挤占", () => {
  const nodes = [N("a"), N("x"), N("b", "a"), N("y", "x")];
  assert.deepEqual(lanesOf(nodes), [0, 1, 0, 1]);
});

test("父节点排在子节点后面时，子节点按新起点处理且不画边", () => {
  // 排序键是 (occurred_at, id)，同一毫秒内由随机 id 定先后，这种顺序真的会出现。
  const nodes = [N("child", "parent"), N("parent")];
  const result = railLanes(nodes);
  assert.equal(result.edges.length, 0, "指向还没出现的行的边只会更难懂");
  assert.ok(Number.isInteger(result.lane.get("child")));
  assert.ok(Number.isInteger(result.lane.get("parent")));
});

test("每条边的两端都是有效数字 —— NaN 会让整条 path 失效", () => {
  const nodes = [N("a"), N("b", "a"), N("c", "a"), N("d", "c"), N("e")];
  for (const edge of railLanes(nodes).edges) {
    for (const key of ["from", "to", "fromLane", "toLane"]) {
      assert.ok(Number.isFinite(edge[key]), `${key} 是 ${edge[key]}`);
    }
  }
});

test("父节点不在本章节里时当作新起点", () => {
  const nodes = [N("a", "别的章节的节点"), N("b", "a")];
  assert.deepEqual(lanesOf(nodes), [0, 0]);
  assert.equal(railLanes(nodes).edges.length, 1);
});
