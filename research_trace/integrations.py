"""Research Trace remains authoritative; frameworks provide evidence and retrieval."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from typing import Any

from .frameworks import BasicMemory, MLflowEvidence, fingerprint, load_config, stable_json
from .storage import NotFound, ValidationError, now_utc


def notes_for_project(project: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Project a consistent central snapshot into independently addressable notes."""
    pid = project["id"]
    notes = {}
    items = [("overview", project, project.get("overview", ""), project["name"])]
    items += [("chapter", c, c.get("summary", ""), c["name"]) for c in project["chapters"]]
    items += [("node", n, n.get("body", ""), n["title"]) for n in project["nodes"]]
    for kind, item, body, title in items:
        comments = item.get("comments", []) if kind == "node" else [
            c for c in project.get("comments", [])
            if c["target_type"] == kind and c["target_id"] == item["id"]
        ]
        key = f"{pid}/{kind}/{item['id']}"
        hit = {
            "id": item["id"], "project_id": pid, "scope": kind, "title": title,
            "body": body, "version": item.get("version", 1),
            "chapter_id": item.get("chapter_id"),
            "review_state": item.get("review_state"),
            "occurred_at": item.get("occurred_at") or item.get("updated_at"),
        }
        content = (
            "此文件是 Research Trace 的检索副本。请在 Research Trace 中编辑和确认记录。\n\n"
            f"# {title}\n\n{body}\n\n"
            f"项目：{project['name']}\n来源 ID：{key}\n"
            f"版本：{hit['version']}\n确认状态：{hit['review_state'] or 'summary'}\n"
        )
        if comments:
            content += "\n## 人工反馈与讨论\n\n" + "\n\n".join(
                f"- {c.get('kind', 'comment')} / {c.get('author_type', 'unknown')}: {c['body']}"
                + ("（已处理）" if c.get("resolved_at") else "")
                for c in comments
            )
        for evidence in item.get("code_evidence", []):
            content += f"\n\n代码：{evidence.get('file_path', '')}\n{evidence.get('annotation', '')}"
        for attachment in item.get("attachments", []):
            content += f"\n\n附件：{attachment['name']}\n{attachment.get('uri') or ''}"
        notes[key] = {
            "key": key, "hit": hit, "content": content,
            "fingerprint": fingerprint(content),
            "title": f"rt-{kind}-{item['id']}".replace("_", "-"),
            "directory": f"research-trace/{pid}",
        }
    return notes


def dictionaries(value: Any):
    """Walk the documented JSON output and tolerate MCP wrapper envelopes."""
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from dictionaries(item)
    elif isinstance(value, list):
        for item in value:
            yield from dictionaries(item)
    elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            yield from dictionaries(json.loads(value))
        except ValueError:
            pass


def permalink_from(value: Any) -> str | None:
    return next((str(d["permalink"]) for d in dictionaries(value) if d.get("permalink")), None)


class Integrations:
    def __init__(self, store, *, config_path=None, config=None, memory=None, mlflow=None):
        self.store = store
        self.config = config if config is not None else load_config(config_path)
        self.bindings = self.config.get("projects", {})
        self.memory = memory or (BasicMemory(self.config["basic_memory"])
                                 if self.config.get("basic_memory") else None)
        self.mlflow = mlflow or (MLflowEvidence(self.config["mlflow"])
                                 if self.config.get("mlflow") else None)
        self.state_path = store.data_dir / "integration-index-state.json"
        self.state = {}
        # Binding changes should retire old managed notes on the same backend.
        # Credentials/timeouts may rotate without invalidating managed identity.
        memory_config = self.config.get("basic_memory", {})
        self.identity = fingerprint({k: memory_config.get(k) for k in ("url", "command", "args", "env")})
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
            if saved.get("identity") == self.identity:
                self.state = saved.get("notes", {})
        except (OSError, ValueError):
            pass
        self.sync_lock = asyncio.Lock()
        self.import_lock = asyncio.Lock()
        self.stop_event = asyncio.Event()
        self.status = {
            "basic_memory": {"enabled": bool(self.memory), "state": "pending" if self.memory else "disabled"},
            "mlflow": {"enabled": bool(self.mlflow), "state": "configured" if self.mlflow else "disabled"},
        }

    def health(self):
        return {**self.status, "bound_projects": list(self.bindings),
                "capabilities": {pid: {"mlflow": bool(self.mlflow and b.get("mlflow_experiment_ids")),
                                       "memory": bool(self.memory and b.get("memory_project"))}
                                 for pid, b in self.bindings.items()},
                "indexed_notes": len(self.state)}

    def _save_state(self):
        temporary = self.state_path.with_name(f".{self.state_path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(stable_json({"identity": self.identity, "notes": self.state}), encoding="utf-8")
        os.replace(temporary, self.state_path)

    def _notes(self, project_id=None):
        notes = {}
        for pid, binding in self.bindings.items():
            if not binding.get("memory_project") or (project_id and pid != project_id):
                continue
            try:
                notes.update(notes_for_project(self.store.get_project(pid)))
            except NotFound:
                # Purged projects are absent. Their managed index entries are
                # removed by sync and are immediately excluded from retrieval.
                continue
        return notes

    async def sync(self):
        if not self.memory:
            return self.health()
        async with self.sync_lock:
            status = self.status["basic_memory"]
            status.update(state="syncing", last_attempt_at=now_utc())
            try:
                current = self._notes()
                changes = [(key, note) for key, note in current.items()
                           if self.state.get(key, {}).get("fingerprint") != note["fingerprint"]
                           or self.state.get(key, {}).get("memory_project") != self.bindings[note["hit"]["project_id"]]["memory_project"]]
                removals = [key for key in self.state if key not in current
                            or self.state[key]["memory_project"] != self.bindings[current[key]["hit"]["project_id"]]["memory_project"]]
                if changes or removals:
                    async with self.memory.session() as client:
                        for key in removals:
                            prior = self.state[key]
                            result = await self.memory.call(client, "delete_note", {
                                "project": prior["memory_project"], "identifier": prior["permalink"],
                                "is_directory": False, "output_format": "json",
                            })
                            # Upstream can return deleted=false as a successful
                            # MCP result. Only an explicit absent-note response
                            # is equivalent to a successful idempotent delete.
                            ack = next((d for d in dictionaries(result) if "deleted" in d), {})
                            absent = (ack.get("deleted") is False and not ack.get("error")
                                      and all(k in ack and ack[k] is None for k in ("title", "permalink", "file_path")))
                            if not (ack.get("deleted") is True or absent):
                                raise RuntimeError("Basic Memory did not acknowledge managed note removal")
                            del self.state[key]
                            self._save_state()
                        for key, note in changes:
                            pid = note["hit"]["project_id"]
                            result = await self.memory.call(client, "write_note", {
                                "project": self.bindings[pid]["memory_project"],
                                "title": note["title"], "directory": note["directory"],
                                "content": note["content"], "note_type": "research_trace",
                                "tags": ["research-trace", note["hit"]["scope"]],
                                "metadata": {"research_trace_key": key,
                                             "research_trace_version": note["hit"]["version"],
                                             "research_trace_fingerprint": note["fingerprint"]},
                                "overwrite": True, "output_format": "json",
                            })
                            permalink = permalink_from(result)
                            if not permalink:
                                raise RuntimeError("Basic Memory did not acknowledge a note permalink")
                            self.state[key] = {
                                "project_id": pid, "memory_project": self.bindings[pid]["memory_project"],
                                "permalink": permalink, "fingerprint": note["fingerprint"],
                            }
                            self._save_state()
                status.update(state="ready", last_success_at=now_utc(), error=None,
                              pending=0, indexed_notes=len(self.state))
            except Exception as exc:
                status.update(state="error", error=f"{type(exc).__name__}: knowledge index sync failed",
                              pending=True)
        return self.health()

    async def run(self):
        while not self.stop_event.is_set():
            await self.sync()
            try:
                await asyncio.wait_for(self.stop_event.wait(),
                                       float(self.config.get("sync_interval_seconds", 60)))
            except asyncio.TimeoutError:
                pass

    async def search(self, query, *, project_id=None, scope="all", limit=50):
        result = self.store.search(query, project_id=project_id, scope=scope, limit=limit).as_dict()
        result["retrieval"] = {"backend": "local", "index_state": self.status["basic_memory"]["state"]}
        if not self.memory or scope == "raw":
            return result
        current = self._notes(project_id)
        if not current:
            return result
        matched_keys = []
        try:
            for pid in dict.fromkeys(note["hit"]["project_id"] for note in current.values()):
                response = await self.memory.search(self.bindings[pid]["memory_project"], query, 100)
                aliases = {
                    value["permalink"]: key for key, value in self.state.items()
                    if value["project_id"] == pid and key in current
                }
                seen = set()
                for item in dictionaries(response):
                    permalink = item.get("permalink")
                    if not isinstance(permalink, str) or permalink not in aliases:
                        continue
                    key = aliases[permalink]
                    if key in seen:
                        continue
                    seen.add(key)
                    matched_keys.append(key)
            # Network waits allow edits/purges in the meantime. Resolve BOTH
            # keyword hits and index references again after the last wait.
            result = self.store.search(query, project_id=project_id, scope=scope, limit=limit).as_dict()
            current = self._notes(project_id)
            resolved = [
                {**current[key]["hit"], "retrieval_source": "basic_memory",
                 "index_stale": self.state.get(key, {}).get("fingerprint") != current[key]["fingerprint"]}
                for key in matched_keys if key in current
            ]
            curated, raw, seen = [], [], set()
            for hit in [*resolved, *result["hits"]]:
                key = (hit["scope"], hit["id"])
                if key in seen:
                    continue
                seen.add(key)
                (raw if hit["scope"] in {"event", "transcript"} else curated).append(hit)
            limit = max(1, min(int(limit), 200))
            if curated and raw:
                curated_count = min(len(curated), (limit + 1) // 2)
                raw_count = min(len(raw), limit - curated_count)
                curated_count = min(len(curated), limit - raw_count)
                selected = curated[:curated_count] + raw[:raw_count]
            else:
                selected = (curated or raw)[:limit]
            result.update(hits=selected, returned={
                name: sum(h["scope"] == name for h in selected)
                for name in {h["scope"] for h in selected}
            })
            result["retrieval"] = {"backend": "basic_memory+local", "index_state": self.status["basic_memory"]["state"],
                                   "external_matches": len(resolved), "totals_scope": "local_keyword_matches",
                                   "candidate_omitted": len(curated) + len(raw) - len(selected)}
        except Exception as exc:
            result = self.store.search(query, project_id=project_id, scope=scope, limit=limit).as_dict()
            result["retrieval"] = {"backend": "local", "fallback": True,
                                   "error": f"{type(exc).__name__}: knowledge search unavailable"}
        return result

    async def import_evidence(self, project_id, *, kind, external_id, node_id=None, name=None):
        if not self.mlflow:
            raise ValidationError("MLflow integration is not configured")
        project = self.store.get_project(project_id)
        binding = self.bindings.get(project_id, {})
        allowed = binding.get("mlflow_experiment_ids", [])
        if not allowed:
            raise ValidationError("this project has no MLflow experiment binding")
        if node_id and not any(n["id"] == node_id for n in project["nodes"]):
            raise NotFound("evidence target is not a node in this project")
        try:
            snapshot = await asyncio.to_thread(self.mlflow.fetch, kind, external_id, allowed)
        except ValidationError:
            raise
        except Exception as exc:
            self.status["mlflow"].update(state="error", error=f"{type(exc).__name__}: evidence fetch failed")
            raise ValidationError("MLflow evidence could not be read; check the configured source") from exc
        raw = stable_json(snapshot).encode("utf-8")
        if len(raw) > self.store.attachment_limit:
            raise ValidationError("MLflow snapshot exceeds the configured evidence size limit")
        event_id = "mlflow_" + fingerprint({"project": project_id, **snapshot})
        session_id = "mlflow_" + fingerprint([project_id, snapshot["source_id"], kind, external_id])
        async with self.import_lock:
            ingest = self.store.ingest(
                batch_id=event_id, project_id=project_id,
                session={"id": session_id, "source": "mlflow",
                         "metadata": {"kind": kind, "external_id": external_id}},
                agents=[], events=[{"event_id": event_id, "event_type": f"MLflow{kind.title()}",
                                    "payload": snapshot}],
                delivered_by="integration:mlflow",
            )
            attachment = None
            if node_id:
                node = next((n for n in self.store.get_project(project_id)["nodes"] if n["id"] == node_id), None)
                if node is None:
                    raise NotFound("evidence target no longer exists")
                attachment = next((a for a in node["attachments"]
                                   if a["metadata"].get("source_event_id") == event_id), None)
                if not attachment:
                    attachment = self.store.attach(
                        project_id, target_type="node", target_id=node_id,
                        name=name or f"MLflow {kind} {external_id}", direction="reference",
                        mime_type="application/json", data_base64=base64.b64encode(raw).decode("ascii"),
                        metadata={"provider": "mlflow", "kind": kind, "external_id": external_id,
                                  "source_event_id": event_id, "experiment_id": snapshot["experiment_id"]},
                    )
            self.status["mlflow"].update(state="ready", last_success_at=now_utc(), error=None)
            return {"source_event_id": event_id, "duplicate": ingest.get("duplicate", False),
                    "attachment": attachment, "evidence": snapshot}
