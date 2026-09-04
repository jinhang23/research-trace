# Research Trace independent Recorder protocol

The Recorder edits research memory for a reader returning months later. It captures what the
research established, why a decision was made, which attempts failed usefully, and which directions
remain untested. It describes the research rather than its own recording work.

## Runtime boundary

The Recorder is an independent Claude Code CLI conversation. It does not fork or wake the main
agent and does not inherit the main conversation. Each call receives only:

1. the new durable batch;
2. a concise Project Overview, human-defined Chapters and unresolved human corrections;
3. a small set of recent and query-related older Nodes and known runs.

Each project reuses its own Recorder session for a bounded window, then starts a new one. This can
reuse that Recorder's prefix and conversation cache, but it never claims to reuse the main agent's
cache. Correctness does not depend on cache hits because the complete current packet is supplied on
every turn.

The model starts with `--setting-sources ""`, `--tools ""`, an empty strict MCP configuration and
`dontAsk` permissions. It cannot inspect files, submit SLURM jobs, edit code, contact agents or write
the database. It returns schema-validated JSON; ordinary Python code validates all IDs and performs
idempotent writes through the existing Research Trace API.

The implementation is in `research_trace/recorder.py`. Its prompt construction, explicit output
classification, isolated observer and quota pause state adapt the design of Claude-Mem commit
`be44b6c8e238a7e2bc5b3403c05afac071a59ead` under Apache-2.0. The fixed source and modification
mapping are documented in `docs/RECORDER_REUSE_PLAN.md`.

## Capture and project binding

Capture is opt-in per project through `.research-trace.json`. Without a marker the hook exits
without creating an outbox. Project binding belongs to the human/main-agent flow; see
`skills/research-trace/SKILL.md`. The Recorder cannot bind or create a project.

The hook writes visible events and transcript deltas to the local outbox. `thinking` and
`redacted_thinking` blocks are removed before persistence. The independent `trace-deliver` process
uploads raw history and only moves files from `pending/` to `sent/` after the central service returns
2xx. Semantic recording and raw delivery remain independent.

A batch manifest may refer to an event that moved from `pending/` to `sent/` after batching. The
loader checks both locations. Missing, truncated or compacted evidence remains an explicit limit;
the Recorder never reconstructs hidden reasoning or guesses missing facts.

## Subscription-only execution

The worker calls the official `claude --print` CLI using its normal Claude subscription login. It
refuses to start a model request when API-key, Bedrock, Vertex or Foundry credential environments
are present, when `claude auth status` does not report a subscription/OAuth login, or when the model
is outside the allowed subscription set.

Claude Code does not expose an API that proves the account-level Extra usage switch is disabled.
The project therefore requires a one-time explicit marker written by:

```bash
trace-project recorder-enable . --model sonnet --confirm-extra-usage-disabled
```

Use that command only after disabling Extra usage in the Claude account. A quota event pauses the
worker until its reset time while leaving the batch in place. An overage event stops processing.
There is no API-key, provider or fallback-model path. `trace-project recorder-disable` pauses model
calls without deleting queued evidence. After fixing authentication/configuration or disabling Extra
usage following an overage signal, run
`trace-recorder --retry-blocked --data-dir <plugin-data>` once. Overage is never retried automatically.

## Selecting durable meaning

A batch can create zero Nodes. Record only material that a future person or agent can reuse:

- a finding with its evidence, evaluation conditions and limits;
- a decision with its reason and alternatives actually considered;
- a failed attempt with the observed failure, what it rules out, and what remains unknown;
- an untried idea with its rationale and a possible check, clearly marked untested;
- an implementation when its purpose, design reason and observed validation matter later.

Skip routine listings, successful installs, repetitive status checks and edits with no durable
research consequence. An empty search is usually noise, while a controlled negative experiment can
be useful. A submitted job is not a completed result. Lower training loss alone does not prove
better downstream affinity prediction, and one failed run does not refute a scientific hypothesis.

Group related events by research question. Several commands or model variants can support one Node.
Do not produce one Node per tool, file, run or timestamp. Existing memory is context rather than a
new discovery. If the batch merely repeats a known conclusion, return `status=skip` with no records.

## What a Node looks like

Each Node uses a specific title and concise connected prose in the evidence's original language. It
must make the claim, basis and consequence understandable without phrases such as “this version” or
“it worked.” Keep observation, inference, hypothesis, user decision, proposed work and agreed work
distinguishable. Include known metric, split, baseline, variant and configuration; unknown values
stay unknown.

Only identifiers present in the packet are accepted:

- Every Node needs one or more `source_event_ids` from this batch that directly support it.
- `chapter_id` must name an existing human-defined Chapter. Omit it for Inbox when placement is
  uncertain. The Recorder cannot create Chapters.
- `parent_id` must be a known same-Chapter predecessor. Omit an unknown or independent relation.
- `run_ids` must already exist in the project and match the discussion. W&B remains a curve link;
  code is not uploaded to W&B.
- Selected code evidence can name a known repository, commit, file, symbol and short snippet/diff.
  Shared working trees use `ambiguous` attribution unless contribution is established.

The program assigns `semantic:<batch_id>:<index>` idempotency keys and persists the validated plan
before the first write. A crash after a partial write resumes the same plan and skips completed
indices. Recorder-created Nodes remain unreviewed. Human edits, moves, confirmations and corrections
have higher authority; the existing server refuses an old machine retry that would overwrite them.
Missing links are allowed. Never invent a parent to make the structure graph look complete. The
server's `structure_gaps` response is an informational receipt about omitted links or evidence, not
a request to fabricate them.

## Completion and status

A schema-valid `status=skip` result is a successful zero-record batch. Empty output, malformed JSON,
unknown IDs, unavailable storage, authentication failure, quota exhaustion and overage are different
states. None may be mislabeled as a successful skip.

Only after every planned Node write succeeds does the worker move the manifest and its processing
state to `batches/done/`. Local `trace-recorder --status`, `trace-deliver --status` and central health
telemetry expose pending count, last processed time, pause time and the most recent Recorder error.
The Web interface continues to read the same Projects, Chapters, Nodes, evidence and correction
records; no parallel memory database is introduced.
