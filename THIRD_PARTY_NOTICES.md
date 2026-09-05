# Third-party notices

The independent Recorder's observer prompt structure, explicit output classification, restricted
execution boundary and quota state handling are adapted from
[Claude-Mem](https://github.com/thedotmack/claude-mem) commit
`be44b6c8e238a7e2bc5b3403c05afac071a59ead`.

Claude-Mem is Copyright 2026 Alex Newman and licensed under the Apache License, Version 2.0. The
upstream [LICENSE](research_trace/third_party/claude_mem/LICENSE) and
[NOTICE](research_trace/third_party/claude_mem/NOTICE) are included with this distribution. The
wheel also carries an [adaptation note](research_trace/third_party/claude_mem/ADAPTATION.md).

Research Trace does not bundle or run the Claude-Mem server, worker, Redis/Postgres storage or
credential provider. The adapted Python implementation connects the upstream design to Research
Trace's existing durable outbox and semantic Node API.
