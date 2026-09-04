"""Exercise real upstream frameworks with synthetic, isolated project data.

Run with the Research Trace [server,integrations] environment; pass a separately
installed Basic Memory executable. Never reads existing research or MLflow data.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import time

from research_trace.frameworks import stable_json
from research_trace.integrations import Integrations
from research_trace.storage import Store


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--basic-memory", required=True)
    parser.add_argument("--directory", default=".integration-demo")
    parser.add_argument("--keyword-only", action="store_true", help="Skip local embedding download; does not verify vector retrieval")
    args = parser.parse_args()
    root = Path(args.directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / "report.json").exists():
        raise SystemExit("Use a fresh --directory to preserve the previous demonstration")
    memory_env = {"BASIC_MEMORY_CONFIG_DIR": str(root / "bm-config"),
                  "BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED": str(not args.keyword_only).lower(),
                  "BASIC_MEMORY_SEMANTIC_EMBEDDING_THREADS": "2", "PYTHONUTF8": "1"}
    # Explicit config avoids Basic Memory's default project pointing at the
    # user's home directory. All demo files and model state stay under root.
    (root / "bm-config").mkdir(exist_ok=True)
    (root / "knowledge").mkdir(exist_ok=True)
    (root / "bm-config" / "config.json").write_text(json.dumps({
        "projects": {"trace-demo": {"path": str(root / "knowledge"), "mode": "local"}},
        "default_project": "trace-demo"}), encoding="utf-8")
    import mlflow
    uri = "sqlite:///" + (root / "mlflow.db").as_posix()
    mlflow.set_tracking_uri(uri)
    experiment_id = mlflow.create_experiment("Research Trace synthetic verification",
                                              artifact_location=(root / "artifacts").as_uri())
    mlflow.set_experiment(experiment_id=experiment_id)
    with mlflow.start_run(run_name="synthetic dropout comparison") as run:
        mlflow.log_params({"dropout": 0.2, "seed": 42, "data": "synthetic"})
        mlflow.log_metric("validation_auc", 0.87)
        run_id = run.info.run_id
        with mlflow.start_span(name="synthetic research session") as span:
            span.set_inputs({"question": "Does regularization reduce overfitting?"})
            span.set_outputs({"summary": "Synthetic demonstration only", "thinking": "PRIVATE_SENTINEL"})
            trace_id = span.trace_id
    mlflow.flush_trace_async_logging()
    store = Store(root / "trace-data")
    project = store.create_project("框架集成演示（模拟数据）", overview="验证证据导入、知识检索和人工修订，不代表真实实验结果。")
    chapter = store.create_chapter(project["id"], "泛化性能")
    node = store.record_node(project["id"], chapter_id=chapter["id"], idempotency_key="demo",
                             title="Regularization experiment", body="Dropout reduces overfitting in this synthetic example. Validation AUC is 0.87; replication is still needed.")
    config = {"schema": "research-trace.integrations.v1", "sync_interval_seconds": 60,
              "basic_memory": {"command": str(Path(args.basic_memory).resolve()), "args": ["mcp"],
                               "env": memory_env, "timeout_seconds": 180},
              "mlflow": {"tracking_uri": uri},
              "projects": {project["id"]: {"memory_project": "trace-demo", "mlflow_experiment_ids": [experiment_id]}}}
    (root / "integrations.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    manager = Integrations(store, config=config)
    run_result = await manager.import_evidence(project["id"], kind="run", external_id=run_id, node_id=node["id"])
    repeated = await manager.import_evidence(project["id"], kind="run", external_id=run_id, node_id=node["id"])
    assert run_result["attachment"]["id"] == repeated["attachment"]["id"]
    trace_result = await manager.import_evidence(project["id"], kind="trace", external_id=trace_id, node_id=node["id"])
    assert "PRIVATE_SENTINEL" not in stable_json(trace_result)
    health = await manager.sync()
    assert health["basic_memory"]["state"] == "ready", health
    response = await manager.search("overfitting", project_id=project["id"], scope="semantic")
    assert any(h["id"] == node["id"] and h.get("retrieval_source") == "basic_memory" for h in response["hits"]), response
    # Same meaning, without sharing a literal keyword with the stored note.
    semantic_query = "How can a model perform better on unseen examples?"
    deadline = time.monotonic() + 90
    while True:
        semantic = await manager.search(semantic_query, project_id=project["id"], scope="semantic")
        semantic_hit = any(h["id"] == node["id"] and h.get("retrieval_source") == "basic_memory" for h in semantic["hits"])
        if semantic_hit or args.keyword_only or time.monotonic() >= deadline:
            break
        await asyncio.sleep(1)
    if not args.keyword_only:
        assert semantic_hit, semantic
    revised = store.update_node(node["id"], {"body": "人工修订：模拟指标只验证集成；还不能推断方法有效。"}, expect_version=1, actor_type="human", actor_id="demo-reviewer")
    response = await manager.search("overfitting", project_id=project["id"], scope="semantic")
    hit = next(h for h in response["hits"] if h["id"] == node["id"])
    assert hit["body"] == revised["body"] and hit["index_stale"] is True
    await manager.sync()
    report = {"project_id": project["id"], "node_id": node["id"], "run_id": run_id, "trace_id": trace_id,
              "mlflow_run_import": True, "mlflow_trace_import": True, "hidden_content_removed": True,
              "duplicate_attachment_prevented": True, "basic_memory_mcp_sync_and_search": True,
              "vector_retrieval_verified": semantic_hit and not args.keyword_only,
              "current_human_revision_returned": True, "health": manager.health()}
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    await manager.memory.close()
    store.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
