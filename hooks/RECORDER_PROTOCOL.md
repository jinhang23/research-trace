# Research Trace independent Recorder protocol

The Recorder edits research memory for a reader returning months later. It captures what the
research established, why a decision was made, which attempts failed usefully, and which directions
remain untested. It describes the research rather than its own recording work.

## Runtime boundary

The Recorder is an independent Claude Code CLI conversation. It does not fork or wake the main
agent and does not inherit the main conversation. The capture hook only seals durable batches; it
never starts or manages the model process. A separately launched `trace-recorder --watch` consumer
waits for and processes those batches. Each model call receives only:

1. the new durable batch;
2. a concise Project Overview, human-defined Chapters and unresolved human corrections;
3. a small set of recent Nodes, each Chapter's newest one or two Nodes (`chapter_heads`, so a quiet
   line's head stays a parent candidate after it leaves the recent window), query-related older Nodes
   and known runs.

Every call is a fresh, stateless `claude --print --no-session-persistence` invocation: the complete
current packet is the whole input, and nothing from an earlier call is carried over. The fixed system
prompt and schema are a byte-identical prefix on every call, which is all a prompt cache keys on; the
Recorder never claims to reuse the main agent's cache, and correctness does not depend on cache hits.

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
refuses to start a model request when API-key, Bedrock, Vertex, Foundry or custom base-URL
environments are present, when `claude auth status` reports anything other than a first-party
subscription/OAuth login, or when the model is outside the allowed subscription set. A CLI that has
no `auth status` subcommand (older releases) passes the preflight as `unverified`; the model call
itself then reports an authentication failure as the `auth` state.

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

Every retry of an empty, malformed, format or timeout failure is a real model call. A batch gets at
most four such attempts; it then rests in `attempts_exhausted` with its material intact until the
operator runs `--retry-blocked`. Preflight failures (authentication, configuration, paid credentials)
cost no quota and retry on a fixed interval.

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
- `parent_id` must be a known same-Chapter predecessor (the data model keeps each Chapter's chain
  inside it). When the work builds on a Node in another Chapter, leave it null and name that Node in
  the body. A cross-Chapter parent is dropped by the program — not silently: the batch state, the
  `--watch` log line (`dropped_parents=… reason=cross-chapter`) and `--status` (`dropped_parents`) all
  say so. Omit an unknown or independent relation.
- `run_ids` must already exist in the project and match the discussion. W&B remains a curve link;
  code is not uploaded to W&B.
- `artifact_refs` register external artifacts the record produced, consumed or points at: a W&B run
  page, a checkpoint or dataset URL, a result file with a scheme. Each needs a `name`, an absolute
  `uri` copied verbatim from NEW EVIDENCE, and optionally `direction` (`output`, `input`; omitted
  means `reference`). A URI that does not appear in the batch is rejected as a guess. The program
  registers accepted refs on the written Node through the attachment API with an idempotent
  `capture_key`, so the link becomes a dataflow key rather than prose.
- Selected code evidence can name a known repository, commit, file, symbol and short snippet/diff.
  Shared working trees use `ambiguous` attribution unless contribution is established.

A Chapter summary or the Overview is the standing answer to that research line's question, not a
log of steps. Every curation names a `reason`: `first_summary` (the target has none yet),
`result_changed`, `plan_changed`, `direction_closed`, `correction_absorbed` or `milestone` — what a
reader of the old summary would now be misled about. Submitting a job, editing code, or adding a
Node the summary would merely repeat is `progress_only`; the program discards such curations (and
a `first_summary` for a Chapter that already has one) before any write. Most batches curate nothing.

A curation's `resolve_comment_ids` may list only corrections on that same Overview or Chapter whose
content the new body absorbs. A correction on a Node is context for records — use the corrected
figure and say it was corrected — and is never listed on a summary; the program drops such ids
silently because the server's correction gate is per target, while an id that is not in the packet
at all is rejected as fabricated.

The program assigns `semantic:<batch_id>:<index>` idempotency keys and persists the validated plan
before the first write. A crash after a partial write resumes the same plan and skips completed
indices. Recorder-created Nodes remain unreviewed. Human edits, moves, confirmations and corrections
have higher authority; the existing server refuses an old machine retry that would overwrite them.
Missing links are allowed. Never invent a parent to make the structure graph look complete. The
server's `structure_gaps` response is an informational receipt about omitted links or evidence, not
a request to fabricate them.

## Completion and status

A schema-valid `status=skip` result is a successful zero-record batch. Empty output, malformed JSON,
unknown IDs, unavailable storage, authentication failure, quota exhaustion, overage and an exhausted
attempt budget are different states. None may be mislabeled as a successful skip.

Only after every planned Node write succeeds does the worker move the manifest and its processing
state to `batches/done/`. Local `trace-recorder --status`, `trace-deliver --status` and central health
telemetry expose pending count, last processed time, pause time and the most recent Recorder error.
The Web interface continues to read the same Projects, Chapters, Nodes, evidence and correction
records; no parallel memory database is introduced.
