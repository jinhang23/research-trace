# Claude-Mem adaptation

Research Trace's independent Recorder adapts Claude-Mem commit
`be44b6c8e238a7e2bc5b3403c05afac071a59ead` under Apache-2.0. The adapted parts are the observer
prompt structure, explicit output classification, restricted execution boundary, cache usage
counters, and quota/overage state handling.

This package does not bundle or run Claude-Mem's server, worker, Redis/Postgres storage, in-memory
message buffer, or API-key provider. Research Trace connects the adapted design to its existing
durable outbox and semantic Node API. See the accompanying `LICENSE` and `NOTICE` files.
