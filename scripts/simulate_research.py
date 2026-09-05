"""Simulate three days of real research through the whole pipeline and score it.

Real pieces: the capture hook (scripts/trace_hook.py) fed with Claude Code hook payloads
and a growing transcript, the deliverer (deliver_once), the central server (in-process
uvicorn on a free port), and the Recorder worker calling the real `claude --print`.
Only the research content is synthetic.  Eight Stop boundaries = up to eight model calls
on the logged-in Claude subscription (about 3 minutes with sonnet).

Run it from a normal terminal, not from inside a Claude Code session:

    python scripts/simulate_research.py            # sonnet (the default Recorder model)
    SIM_MODEL=haiku python scripts/simulate_research.py

It writes simulate_research.log / .json next to itself and exits 1 when any scorecard
check fails.  The scorecard is a pipeline check, not a scientific-quality evaluation:
read the printed Node bodies yourself for that (TODO.md, P1).
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ["TRACE_HOOK_NO_SPAWN"] = "1"
os.environ.pop("ANTHROPIC_BASE_URL", None)

import uvicorn  # noqa: E402

from research_trace import recorder as R  # noqa: E402
from research_trace.deliver import deliver_once, write_marker  # noqa: E402
from research_trace.server import create_app  # noqa: E402

SPEC = importlib.util.spec_from_file_location("trace_hook", REPO / "scripts" / "trace_hook.py")
H = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(H)
PROTOCOL = REPO / "hooks" / "RECORDER_PROTOCOL.md"

TOKEN = "sim-token"
MODEL = os.environ.get("SIM_MODEL", "sonnet")
WANDB_MAIN = "https://wandb.ai/wei-lab/affinity/runs/esm2-lr3e5-w500"
WANDB_MLP = "https://wandb.ai/wei-lab/affinity/runs/mlp-baseline-k5"
LOG = Path(__file__).with_name("simulate_research.log")
OUT = Path(__file__).with_name("simulate_research.json")


def log(*parts):
    line = " ".join(str(p) for p in parts)
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def api(url, path, body=None):
    request = urllib.request.Request(
        url + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


class Session:
    """One Claude Code session: a transcript file plus hook payloads."""

    def __init__(self, sim, session_id):
        self.sim, self.id = sim, session_id
        self.transcript = sim.work / f"{session_id}.jsonl"
        self.turn = 0

    def payload(self, name, **extra):
        value = {
            "session_id": self.id,
            "transcript_path": str(self.transcript),
            "cwd": str(self.sim.project_dir),
            "hook_event_name": name,
        }
        value.update(extra)
        return value

    def hook(self, name, **extra):
        return H.handle(self.payload(name, **extra), self.sim.data, PROTOCOL, self.sim.url)

    def write(self, role, text, thinking=None):
        content = []
        if thinking:
            content.append({"type": "thinking", "thinking": thinking, "signature": "sig-" + os.urandom(4).hex()})
        content.append({"type": "text", "text": text})
        with self.transcript.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "type": role,
                        "message": {"role": role, "content": content},
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    def start(self):
        self.hook("SessionStart", source="startup")

    def end(self):
        self.hook("SessionEnd", reason="exit")

    def run_turn(self, label, prompt, tools, answer):
        self.turn += 1
        self.write("user", prompt)
        self.hook("UserPromptSubmit", prompt=prompt)
        for index, (tool, tool_input, response) in enumerate(tools):
            use_id = f"toolu_{self.id}_{self.turn}_{index}"
            self.hook("PreToolUse", tool_name=tool, tool_input=tool_input, tool_use_id=use_id)
            self.hook("PostToolUse", tool_name=tool, tool_input=tool_input, tool_response=response, tool_use_id=use_id)
        self.write(
            "assistant", answer, thinking=f"SECRET-THOUGHT {label}: hidden reasoning must never leave this machine"
        )
        self.hook("Stop", stop_hook_active=False, last_assistant_message=answer)
        return self.sim.process(label)


class Simulation:
    def __init__(self):
        self.work = Path(tempfile.mkdtemp(prefix="rt-sim-"))
        self.central = self.work / "central"
        self.central.mkdir()
        self.data = self.work / "plugin-data"
        self.project_dir = self.work / "affinity-project"
        self.results = []
        self.packets = []

    def start_server(self):
        port = free_port()
        self.url = f"http://127.0.0.1:{port}"
        self.app = create_app(self.central, token=TOKEN)
        server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(50):
            try:
                api(self.url, "/api/health")
                return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("server did not start")

    def create_project(self):
        project = api(
            self.url, "/api/projects", {"name": "蛋白质-配体亲和力预测", "workspace_keys": ["rt-ws-affinity"]}
        )
        self.pid = project["id"]
        self.chapters = {}
        for name in ("主实验", "消融实验", "基线复现"):
            self.chapters[name] = api(self.url, f"/api/projects/{self.pid}/chapters", {"name": name})["id"]
        for path in (
            "common/featurize.py",
            "common/data.py",
            "models/esm2_gnn/train.py",
            "models/esm2_gnn/train.sh",
            "models/esm2_gnn/config.yaml",
            "models/baseline_mlp/train.py",
            "models/baseline_mlp/train.sh",
        ):
            target = self.project_dir / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"# {path}\n", encoding="utf-8")
        write_marker(
            self.project_dir,
            workspace_key="rt-ws-affinity",
            project_id=self.pid,
            capture=True,
            recorder={"enabled": True, "mode": "independent", "model": MODEL, "extra_usage_disabled": True},
        )
        self.worker = R.RecorderWorker(self.data, self.url, token=TOKEN, model_timeout=240)
        original = R.build_prompt

        def logged(manifest, material, context, related):
            prompt, packet = original(manifest, material, context, related)
            memory = packet["existing_memory"]
            stats = {
                "events": len(packet["new_evidence"]["events"]),
                "chunks": len(packet["new_evidence"]["visible_transcript_chunks"]),
                "recent_nodes": len(memory["recent_nodes"]),
                "related": len(memory["related_old_records"]),
                "corrections": len(memory["unresolved_human_corrections"]),
                "prompt_chars": len(prompt),
                "related_titles": [x.get("title") for x in memory["related_old_records"]],
            }
            self.packets.append(stats)
            return prompt, packet

        R.build_prompt = logged
        log("project", self.pid, "chapters", self.chapters)

    def done_states(self):
        return {p: p.stat().st_mtime for p in (self.data / "outbox").glob("*/*/batches/done/*.state.json")}

    def process(self, label):
        before = self.done_states()
        delivery = deliver_once(self.data, self.url, token=TOKEN)
        started = time.time()
        report = self.worker.run_once()
        elapsed = time.time() - started
        new = [p for p in self.done_states() if p not in before]
        state = json.loads(new[0].read_text(encoding="utf-8")) if new else {}
        usage = dict(self.worker.state.get("last_usage") or {})
        packet = self.packets[-1] if self.packets else {}
        result = {
            "label": label,
            "seconds": round(elapsed, 1),
            "delivered_events": delivery["delivered_events"],
            "delivered_chunks": delivery["delivered_chunks"],
            "counts": report["counts"],
            "outcome": state.get("outcome"),
            "status": state.get("status"),
            "attempts": state.get("attempts"),
            "last_error": state.get("last_error"),
            "packet": packet,
            "usage": usage,
            "records": [
                {
                    k: r.get(k)
                    for k in (
                        "title",
                        "chapter_id",
                        "parent_id",
                        "labels",
                        "source_event_ids",
                        "artifact_refs",
                        "code_evidence",
                        "body",
                    )
                }
                for r in state.get("plan") or []
            ],
            "curations": [
                {
                    k: c.get(k)
                    for k in ("target_type", "target_id", "expect_version", "resolve_comment_ids", "body", "reason")
                }
                for c in state.get("curations") or []
            ],
            "dropped_curations": state.get("dropped_curations"),
            "node_ids": state.get("node_ids") or {},
        }
        self.results.append(result)
        chapter_names = {v: k for k, v in self.chapters.items()}
        log(
            f"\n=== {label} === {elapsed:.0f}s status={report['counts']} outcome={result['outcome']} "
            f"packet={ {k: v for k, v in packet.items() if k != 'related_titles'} } usage={usage}"
        )
        if packet.get("related_titles"):
            log("  related:", packet["related_titles"])
        for record in result["records"]:
            log(
                f"  NODE  [{chapter_names.get(record['chapter_id'], 'Inbox' if not record['chapter_id'] else record['chapter_id'])}] "
                f"{record['title']} | parent={record['parent_id']} | labels={record['labels']} | "
                f"sources={record['source_event_ids'] and len(record['source_event_ids'])} | artifacts={[a['uri'] for a in record['artifact_refs'] or []]}"
            )
            log("        body:", (record["body"] or "")[:420].replace("\n", " "))
        for curation in result["curations"]:
            log(
                f"  CURATE {curation['target_type']} v{curation['expect_version']} reason={curation.get('reason')} resolve={curation['resolve_comment_ids']}"
            )
            log("        body:", (curation["body"] or "")[:420].replace("\n", " "))
        if result["last_error"]:
            log("  ERROR", result["status"], result["last_error"])
        return result


def main():
    LOG.write_text("", encoding="utf-8")
    sim = Simulation()
    sim.start_server()
    sim.create_project()
    ch = sim.chapters
    metrics_main = {
        "model": "esm2_gnn",
        "lr": 3e-5,
        "warmup_steps": 500,
        "pocket_cutoff_A": 8,
        "fold_ci": [0.884, 0.891, 0.887, 0.893, 0.890],
        "mean_ci": 0.889,
        "previous_run_mean_ci": 0.871,
        "wandb": WANDB_MAIN,
    }

    # ---------------- Day 1 ----------------
    s1 = Session(sim, "sess-day1")
    s1.start()
    s1.run_turn(
        "T1 纯讨论：10Å 截断的想法（未执行）",
        "现在 ESM-2+GNN 的口袋图用 8Å 截断取残基，我在想要不要试 10Å，把更多远端残基包进来。先别跑，讨论一下值不值得。",
        [],
        "值得作为一个消融来做，但不急。理由：8Å 已经覆盖了直接接触的残基，10Å 会把二层残基包进来，"
        "图的节点数大约翻一倍，噪声和显存都会涨；文献里 (Jiménez 2018, KDEEP) 用的 8Å 也没有明显损失。"
        "我的假设是 10Å 不会提高 CI，反而可能因为噪声略降。建议之后在消融实验里做一次 8Å vs 10Å 的 5 折对比，"
        "其它配置固定；现在不改代码也不提交任务。",
    )
    s1.run_turn(
        "T2 噪声：看环境和队列",
        "看下环境和队列",
        [
            ("Bash", {"command": "ls models/"}, {"stdout": "baseline_mlp\nesm2_gnn\n", "exit_code": 0}),
            ("Bash", {"command": "pip install -q rdkit==2024.3.5"}, {"stdout": "", "exit_code": 0}),
            (
                "Bash",
                {"command": "squeue -u jwei"},
                {"stdout": "JOBID PARTITION NAME USER ST TIME NODES NODELIST(REASON)\n", "exit_code": 0},
            ),
        ],
        "环境正常，rdkit 已经装好，队列里没有我们的任务。",
    )
    s1.run_turn(
        "T3 提交主实验（只提交，无结果）",
        "ESM-2 版本训练不稳定，loss 前 200 步就发散。把 lr 从 1e-4 降到 3e-5，加 500 步 warmup，提交主实验 5 折。",
        [
            (
                "Edit",
                {
                    "file_path": "models/esm2_gnn/config.yaml",
                    "old_string": "lr: 1e-4\nwarmup_steps: 0",
                    "new_string": "lr: 3e-5\nwarmup_steps: 500",
                },
                {"success": True},
            ),
            (
                "Bash",
                {"command": "sbatch models/esm2_gnn/train.sh --folds 5"},
                {"stdout": "Submitted batch job 8812345\n", "exit_code": 0},
            ),
        ],
        "已改 models/esm2_gnn/config.yaml：lr 1e-4 → 3e-5，warmup 0 → 500 步；提交 job 8812345，5 折，预计 3 小时。"
        "现在还没有结果。",
    )
    s1.end()

    # ---------------- Day 2 ----------------
    s2 = Session(sim, "sess-day2")
    s2.start()
    t4 = s2.run_turn(
        "T4 主实验结果 + W&B",
        "8812345 跑完了吗？看结果。",
        [
            (
                "Bash",
                {"command": "sacct -j 8812345 --format=JobID,State,Elapsed,MaxRSS"},
                {"stdout": "JobID State Elapsed MaxRSS\n8812345 COMPLETED 02:51:10 41.2G\n", "exit_code": 0},
            ),
            (
                "Bash",
                {"command": "cat results/esm2_lr3e-5/metrics.json"},
                {"stdout": json.dumps(metrics_main, ensure_ascii=False, indent=1), "exit_code": 0},
            ),
        ],
        f"跑完了。5 折 CI 0.884/0.891/0.887/0.893/0.890，平均 0.889，上一版 lr 1e-4 是 0.871。曲线在 {WANDB_MAIN}。"
        "训练现在很稳定，没有发散。但增益来自降 lr 还是 warmup 分不开，要单独消融才知道。",
    )
    # A human reviews the new node in the Web UI and corrects the baseline figure.
    node_id = next(iter(t4["node_ids"].values()), None) if t4 else None
    if node_id:
        store = sim.app.state.store
        comment = store.add_comment(
            sim.pid,
            target_type="node",
            target_id=node_id,
            body="0.871 是上周 8Å 单折的数字，不是 5 折平均。上一版的 5 折平均 CI 是 0.874，比较时请用 0.874。",
            kind="correction",
            author_type="human",
            author_id="jinhang",
        )
        log(f"\n--- HUMAN correction on {node_id}: comment {comment.get('id')} ---")
    else:
        log("\n--- HUMAN correction skipped: T4 wrote no node ---")
    s2.run_turn(
        "T5 消融 10Å：OOM 失败",
        "按之前说的做 10Å 截断的消融，其它和主实验一致。",
        [
            (
                "Edit",
                {
                    "file_path": "models/esm2_gnn/config.yaml",
                    "old_string": "pocket_cutoff: 8",
                    "new_string": "pocket_cutoff: 10",
                },
                {"success": True},
            ),
            (
                "Bash",
                {"command": "sbatch models/esm2_gnn/train.sh --folds 5 --tag cutoff10"},
                {"stdout": "Submitted batch job 8812900\n", "exit_code": 0},
            ),
            (
                "Bash",
                {"command": "sacct -j 8812900 --format=JobID,State,Elapsed,MaxRSS"},
                {"stdout": "JobID State Elapsed MaxRSS\n8812900 OUT_OF_MEMORY 00:06:41 63.8G\n", "exit_code": 0},
            ),
            (
                "Bash",
                {"command": "tail -n 5 logs/8812900.err"},
                {
                    "stdout": "torch.cuda.OutOfMemoryError: CUDA out of memory. Tried to allocate 3.20 GiB (GPU 0; 79.1 GiB total)\n"
                    "  at batch 3 of fold 0, graph nodes=2311 (8Å run: ~1150)\n",
                    "exit_code": 0,
                },
            ),
        ],
        "10Å 的图节点数差不多翻倍（2311 vs 约 1150），fold 0 第 3 个 batch 就 CUDA OOM，主机内存也到了 63.8G。"
        "所以 10Å 到底好不好还不知道——这是资源问题不是方法结论。下次把 batch size 从 16 降到 8 或者开 gradient checkpointing 再试。",
    )
    s2.run_turn(
        "T6 重复已知信息（应 skip）",
        "再确认一下主实验的数字",
        [
            (
                "Bash",
                {"command": "cat results/esm2_lr3e-5/metrics.json"},
                {"stdout": json.dumps(metrics_main, ensure_ascii=False, indent=1), "exit_code": 0},
            )
        ],
        "和之前一致：5 折平均 CI 0.889，没有变化。",
    )
    s2.run_turn(
        "T7 用户要求更新 Overview（含人工纠正的口径）",
        "把项目 Overview 更新一下：当前主线是 ESM-2+GNN 8Å 口袋图，lr 3e-5 + warmup 500 已经稳定；baseline 口径一律按 5 折平均；"
        "10Å 消融因为 OOM 还没做成。",
        [],
        "Overview 要点：目标是蛋白质-配体亲和力预测（CI 为主指标，5 折平均）。当前主线 ESM-2+GNN、8Å 口袋图、lr 3e-5 + 500 步 warmup，"
        "5 折平均 CI 0.889（上一版 lr 1e-4 的 5 折平均是 0.874）。待办：lr vs warmup 的单独消融；10Å 截断消融因 OOM 未完成，需要减 batch 或 gradient checkpointing。",
    )
    s2.end()

    # ---------------- Day 3: parallel model directory + shared code change ----------------
    s3 = Session(sim, "sess-day3")
    s3.start()
    s3.run_turn(
        "T8 并行版本：MLP 基线 + 公共代码改动",
        "在 models/baseline_mlp 用同一份数据和划分跑个 MLP 基线做对照。公共代码 common/featurize.py 加一个 --pocket-cutoff 参数，两个模型目录共用。",
        [
            (
                "Edit",
                {
                    "file_path": "common/featurize.py",
                    "old_string": "CUTOFF = 8.0",
                    "new_string": "parser.add_argument('--pocket-cutoff', type=float, default=8.0)\nCUTOFF = args.pocket_cutoff",
                },
                {"success": True},
            ),
            (
                "Bash",
                {"command": "sbatch models/baseline_mlp/train.sh --folds 5"},
                {"stdout": "Submitted batch job 8813010\n", "exit_code": 0},
            ),
            (
                "Bash",
                {"command": "sacct -j 8813010 --format=JobID,State,Elapsed"},
                {"stdout": "JobID State Elapsed\n8813010 COMPLETED 00:22:04\n", "exit_code": 0},
            ),
            (
                "Bash",
                {"command": "cat results/baseline_mlp/metrics.json"},
                {
                    "stdout": json.dumps(
                        {
                            "model": "baseline_mlp",
                            "features": "ECFP4+pocket_onehot",
                            "pocket_cutoff_A": 8,
                            "fold_ci": [0.849, 0.855, 0.850, 0.856, 0.850],
                            "mean_ci": 0.852,
                            "wandb": WANDB_MLP,
                        },
                        ensure_ascii=False,
                        indent=1,
                    ),
                    "exit_code": 0,
                },
            ),
        ],
        f"MLP 基线（ECFP4 + 口袋 one-hot，同一划分）5 折平均 CI 0.852，曲线 {WANDB_MLP}；ESM-2+GNN 是 0.889，差距 0.037。"
        "common/featurize.py 现在带 --pocket-cutoff 参数（默认 8.0），esm2_gnn 和 baseline_mlp 两个目录共用这份代码，没有 git 提交。",
    )
    s3.end()

    # ---------------- Report ----------------
    view = api(sim.url, f"/api/projects/{sim.pid}")
    nodes = view.get("nodes") or view.get("recent_nodes") or []
    store = sim.app.state.store
    with store._lock:
        attachments = [
            dict(r) for r in store._db.execute("SELECT target_id,name,direction,uri FROM attachments").fetchall()
        ]
        chunks = [r[0] for r in store._db.execute("SELECT search_text FROM transcript_chunks").fetchall()]
    outbox_text = "".join(
        p.read_text(encoding="utf-8") for p in (sim.data / "outbox").glob("*/*/transcripts/*/*.jsonl")
    )
    project_view = {
        "overview": (view.get("project") or view).get("overview")
        if isinstance(view.get("project"), dict)
        else view.get("overview"),
        "nodes": [
            {k: n.get(k) for k in ("id", "title", "chapter_id", "parent_id", "labels", "review_state")} for n in nodes
        ],
        "attachments": attachments,
    }
    log("\n=== FINAL PROJECT VIEW ===")
    chapter_names = {v: k for k, v in ch.items()}
    for n in project_view["nodes"]:
        log(
            f"  [{chapter_names.get(n['chapter_id'], 'Inbox')}] {n['title']} | parent={n['parent_id']} | {n['review_state']}"
        )
    log("  attachments:", [(a["direction"], a["uri"]) for a in attachments])
    log("  overview:", (project_view["overview"] or "")[:600].replace("\n", " "))

    def rec(label_prefix):
        return next((r for r in sim.results if r["label"].startswith(label_prefix)), {})

    checks = []
    t3, t4r, t5, t6, t7, t8 = (rec(p) for p in ("T3", "T4", "T5", "T6", "T7", "T8"))
    checks.append(
        (
            "hidden reasoning never reaches outbox or central",
            "SECRET-THOUGHT" not in outbox_text and not any("SECRET-THOUGHT" in c for c in chunks),
        )
    )
    checks.append(("T2 noise-only turn produced zero records", rec("T2").get("outcome") == "skipped"))
    checks.append(
        (
            "T3 submission did not claim the later result (0.889)",
            all("0.889" not in (r["body"] or "") for r in t3.get("records", [])),
        )
    )
    t4_records = t4r.get("records", [])
    # One result node in 主实验.  A second node for the *untested* lr-vs-warmup ablation idea
    # (in 消融实验) is legitimate: unexecuted directions are worth recording.
    t4_main = [r for r in t4_records if r["chapter_id"] == ch["主实验"]]
    checks.append(
        (
            "T4 wrote exactly one result node in 主实验 (extra untested-direction nodes allowed elsewhere)",
            len(t4_main) == 1 and all(r["chapter_id"] != ch["主实验"] for r in t4_records if r is not t4_main[0]),
        )
    )
    checks.append(("T4 registered the W&B run as an artifact", any(a["uri"] == WANDB_MAIN for a in attachments)))
    t5_records = t5.get("records", [])
    checks.append(
        (
            "T5 OOM recorded in 消融实验 as a resource failure",
            len(t5_records) >= 1
            and t5_records[0]["chapter_id"] == ch["消融实验"]
            and any(w in (t5_records[0]["body"] or "") for w in ("OOM", "内存", "显存", "OutOfMemory")),
        )
    )
    checks.append(
        (
            "T5 kept the 10Å question open (hedged, no verdict)",
            any(
                w in (r["body"] or "")
                for r in t5_records
                for w in (
                    "未验证",
                    "尚未验证",
                    "未得到验证",
                    "仍未",
                    "未知",
                    "无法判断",
                    "尚无法",
                    "待验证",
                    "不能判断",
                    "尚不能",
                    "未得到",
                    "未产生",
                    "未产出",
                    "没有得到",
                    "非方法",
                    "不是方法",
                    "尚无",
                    "无有效结果",
                    "资源限制",
                    "资源问题",
                )
            )
            and not any(
                w in (r["body"] or "") for r in t5_records for w in ("10Å更差", "10Å 更差", "证明了", "已证明")
            ),
        )
    )
    # Zero records is the point.  A summary rewrite is acceptable here only when it absorbs the
    # human correction issued just before (the Chapter summary still said 0.871).
    checks.append(
        (
            "T6 repeat of known numbers produced zero records (correction-absorbing summary allowed)",
            not t6.get("records") and all(c.get("reason") == "correction_absorbed" for c in t6.get("curations", [])),
        )
    )
    checks.append(
        (
            "T6/T7 packets carried the human correction",
            (t6.get("packet", {}).get("corrections", 0) >= 1) and (t7.get("packet", {}).get("corrections", 0) >= 1),
        )
    )
    checks.append(("T7 curated the Overview", any(c["target_type"] == "overview" for c in t7.get("curations", []))))
    checks.append(("Overview uses the corrected 5-fold baseline 0.874", "0.874" in (project_view["overview"] or "")))
    t8_records = t8.get("records", [])
    checks.append(
        (
            "T8 baseline recorded in 基线复现 with its W&B run",
            len(t8_records) >= 1
            and t8_records[0]["chapter_id"] == ch["基线复现"]
            and any(a["uri"] == WANDB_MLP for a in attachments),
        )
    )
    checks.append(
        (
            "no fabricated commit hash in code evidence",
            not any(
                (c.get("commit_hash") or "")
                for r in sim.results
                for rec_ in r["records"]
                for c in (rec_["code_evidence"] or [])
            ),
        )
    )
    checks.append(
        (
            "every record title is in the evidence's language (contains CJK)",
            all(any('一' <= ch_ <= '鿿' for ch_ in (r["title"] or "")) for res in sim.results for r in res["records"]),
        )
    )
    # In a project this small every search hit is already among recent_nodes (limit 8), so
    # related_old_records is empty by design; check the recall layer underneath instead.
    hits = {
        term: [
            h["title"]
            for h in store.search(term, project_id=sim.pid, scope="semantic", limit=5)
            if h["scope"] == "node"
        ]
        for term in ("消融", "10Å", "warmup", "ESM-2")
    }
    log("  recall hits:", hits)
    checks.append(
        (
            "server search finds old nodes for identifier and CJK terms (incl. 10Å)",
            all(hits[t] for t in ("消融", "10Å", "warmup")),
        )
    )
    calls = [r for r in sim.results if r["usage"]]
    checks.append(
        (
            "prompt cache hit on stateless calls after the first",
            sum(1 for r in calls[1:] if r["usage"].get("cache_read_input_tokens", 0) > 0) >= max(1, len(calls) // 2),
        )
    )
    checks.append(
        (
            "all source_event_ids resolve to delivered central events",
            all(
                not api(sim.url, f"/api/nodes/{nid}/sources").get("missing_event_ids")
                for r in sim.results
                for nid in r["node_ids"].values()
            ),
        )
    )
    checks.append(
        (
            "no batch left pending or errored",
            sim.worker.run_once()["pending_batches"] == 0 and not any(r["last_error"] for r in sim.results),
        )
    )
    chapter_curations = [c for r in sim.results for c in r["curations"] if c["target_type"] == "chapter"]
    checks.append(
        (
            "T3 submission-only turn did not rewrite the chapter summary",
            not any(c["target_type"] == "chapter" for c in t3.get("curations", [])),
        )
    )
    checks.append(
        (
            "chapter summaries rewritten at most 4 times over 8 turns (first/result/plan only)",
            len(chapter_curations) <= 4,
        )
    )
    log(
        "  chapter curations:",
        [(r["label"][:2], c["reason"]) for r in sim.results for c in r["curations"] if c["target_type"] == "chapter"],
        "| dropped:",
        sum(int(r.get("dropped_curations") or 0) for r in sim.results),
    )

    log("\n=== SCORECARD ===")
    for name, ok in checks:
        log(("PASS " if ok else "FAIL ") + name)
    total_usage = sim.worker.state.get("usage_totals")
    log(
        "usage totals:",
        total_usage,
        "| model calls:",
        len(calls),
        "| wall:",
        round(sum(r["seconds"] for r in sim.results), 1),
        "s",
    )
    OUT.write_text(
        json.dumps(
            {
                "results": sim.results,
                "checks": checks,
                "project": project_view,
                "usage_totals": total_usage,
                "work": str(sim.work),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    log("work dir:", sim.work)
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
