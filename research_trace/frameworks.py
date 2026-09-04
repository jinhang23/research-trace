"""Optional adapters to upstream frameworks; no imports/calls when unconfigured."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .storage import ValidationError


from .visible import stable_json, fingerprint, visible_content


def load_config(path: str | os.PathLike[str] | None) -> dict[str, Any]:
    if not path:
        return {}
    config = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("schema") != "research-trace.integrations.v1":
        raise ValidationError("invalid integrations config schema")
    bindings = config.get("projects", {})
    if not isinstance(bindings, dict):
        raise ValidationError("integrations.projects must map project ids to bindings")
    for pid, binding in bindings.items():
        if not isinstance(pid, str) or not pid or not isinstance(binding, dict):
            raise ValidationError("invalid integration project binding")
        ids = binding.get("mlflow_experiment_ids", [])
        if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
            raise ValidationError("mlflow_experiment_ids must be a list of strings")
        if "memory_project" in binding and not isinstance(binding["memory_project"], str):
            raise ValidationError("memory_project must be a Basic Memory project name")
    memory = config.get("basic_memory")
    if memory:
        if not isinstance(memory, dict) or bool(memory.get("url")) == bool(memory.get("command")):
            raise ValidationError("Basic Memory needs exactly one of url or command")
        if memory.get("url"):
            parsed = urlsplit(memory["url"])
            if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username:
                raise ValidationError("Basic Memory URL must be HTTP(S), without embedded credentials")
        if memory.get("command") and not isinstance(memory["command"], str):
            raise ValidationError("Basic Memory command must be one executable, not shell syntax")
        if not isinstance(memory.get("args", []), list) or not all(isinstance(a, str) for a in memory.get("args", [])):
            raise ValidationError("Basic Memory args must be an argument list")
        env = memory.get("env", {})
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            raise ValidationError("Basic Memory env must map strings to strings")
        timeout = memory.get("timeout_seconds", 30)
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValidationError("Basic Memory timeout_seconds must be positive and finite")
        if memory.get("token_env") and not isinstance(memory["token_env"], str):
            raise ValidationError("Basic Memory token_env must be an environment variable name")
    mlflow = config.get("mlflow")
    if mlflow and (not isinstance(mlflow, dict) or not isinstance(mlflow.get("tracking_uri"), str) or not mlflow["tracking_uri"]):
        raise ValidationError("MLflow tracking_uri is required")
    interval = config.get("sync_interval_seconds", 60)
    if not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval < 5:
        raise ValidationError("sync_interval_seconds must be at least 5")
    return config


def tool_payload(result: Any) -> Any:
    if getattr(result, "isError", False):
        # Backend errors may contain endpoints or secrets; do not return their
        # raw text through the Research Trace API or health panel.
        raise RuntimeError("Basic Memory rejected the operation; check its service log")
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    for block in getattr(result, "content", []):
        if getattr(block, "type", None) == "text":
            try:
                return json.loads(block.text)
            except ValueError:
                return {"text": block.text}
    raise RuntimeError("Basic Memory returned no result")


class BasicMemory:
    """Use the official MCP client against Basic Memory's public tools."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.timeout = float(config.get("timeout_seconds", 30))
        self._queue = asyncio.Queue()
        self._worker = None

    @asynccontextmanager
    async def session(self):
        # write_note acknowledges text before background embeddings finish.
        # Keep stdio alive across requests so those jobs can complete.
        yield self

    @asynccontextmanager
    async def _transport_session(self):
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as exc:
            raise RuntimeError("install research-trace[integrations] for the official MCP client") from exc
        if self.config.get("url"):
            headers = {}
            token_env = self.config.get("token_env")
            if token_env:
                token = os.environ.get(token_env)
                if not token:
                    raise RuntimeError("Basic Memory credential environment variable is unset")
                headers["Authorization"] = "Bearer " + token
            transport = streamablehttp_client(self.config["url"], headers=headers, timeout=self.timeout)
        else:
            transport = stdio_client(StdioServerParameters(
                command=self.config["command"], args=self.config.get("args", ["mcp"]),
                env={**os.environ, **self.config.get("env", {})},
            ))
        async with transport as channels:
            async with ClientSession(channels[0], channels[1],
                                     read_timeout_seconds=timedelta(seconds=self.timeout)) as client:
                await client.initialize()
                yield client

    async def call(self, client: Any, name: str, arguments: dict[str, Any]) -> Any:
        future = asyncio.get_running_loop().create_future()
        self._queue.put_nowait((name, arguments, future))
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._serve())
        return await asyncio.wait_for(future, self.timeout)

    async def _serve(self):
        pending = None
        try:
            async with self._transport_session() as client:
                while True:
                    name, arguments, pending = await self._queue.get()
                    if pending.cancelled():
                        continue
                    result = await asyncio.wait_for(client.call_tool(name, arguments), self.timeout)
                    payload = tool_payload(result)
                    if isinstance(payload, str):
                        try:
                            payload = json.loads(payload)
                        except ValueError:
                            pass
                    if isinstance(payload, dict) and payload.get("error"):
                        raise RuntimeError("Basic Memory reported an operation error")
                    if not pending.done():
                        pending.set_result(payload)
                    pending = None
        except (Exception, asyncio.CancelledError) as exc:
            if pending is not None and not pending.done():
                pending.set_exception(RuntimeError("Basic Memory connection closed"))
            while not self._queue.empty():
                _, _, future = self._queue.get_nowait()
                if not future.done():
                    future.set_exception(RuntimeError("Basic Memory connection unavailable"))
            if isinstance(exc, asyncio.CancelledError):
                raise

    async def close(self):
        if self._worker is not None:
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

    async def search(self, project: str, query: str, limit: int = 50) -> Any:
        async with self.session() as client:
            return await self.call(client, "search_notes", {
                "project": project, "query": query, "search_type": "hybrid",
                "page_size": min(limit, 100), "output_format": "json",
                "note_types": ["research_trace"],
            })


class MLflowEvidence:
    """Fetch immutable evidence snapshots using the official MLflow SDK."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.source_id = fingerprint(config["tracking_uri"])[:16]

    def client(self):
        try:
            from mlflow import MlflowClient
        except ImportError as exc:
            raise RuntimeError("install research-trace[integrations] for the MLflow client") from exc
        return MlflowClient(tracking_uri=self.config["tracking_uri"])

    def fetch(self, kind: str, external_id: str, allowed_experiments: list[str]) -> dict[str, Any]:
        if kind not in {"run", "trace"} or not isinstance(external_id, str) or not external_id.strip():
            raise ValidationError("MLflow evidence requires kind run/trace and an external_id")
        client = self.client()
        if kind == "run":
            value = client.get_run(external_id)
            experiment_id = str(value.info.experiment_id)
            payload = value.to_dictionary()
        else:
            value = client.get_trace(external_id, display=False)
            experiment_id = str(value.info.experiment_id)
            payload = value.to_dict()
        if experiment_id not in allowed_experiments:
            raise ValidationError("MLflow experiment is not bound to this Research Trace project")
        clean = visible_content(payload)
        return {
            "provider": "mlflow", "source_id": self.source_id, "kind": kind,
            "external_id": external_id, "experiment_id": experiment_id,
            "payload": clean, "sha256": fingerprint(clean),
        }
