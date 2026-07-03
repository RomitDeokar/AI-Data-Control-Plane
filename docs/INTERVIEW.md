# Interview Talking Points

This project is designed to give you *stories*, not just code. Below are the
questions interviewers actually ask and strong, specific answers grounded in
what this repo does. Rephrase in your own words.

---

## The 30-second pitch

> "I built an **AI Data Control Plane** — an event-driven orchestration platform
> on Kestra that governs how data enters production. New data arrives via upload,
> webhook, or schedule; it's validated, enriched, and embedded; then it must pass
> a suite of **quality gates** before a **blue/green promotion** puts it live.
> Every decision is audited, and rollback is a sub-second alias switch. It's the
> layer *above* a normal pipeline — the part that decides whether data is even
> allowed to ship."

---

## "Why is this a *control plane* and not just a pipeline?"

A pipeline moves data; a control plane *governs* it. Three concrete
differences in this project:

1. **Gates block production.** Nothing is promoted unless it passes explicit,
   auditable checks (completeness, uniqueness, drift, coverage, pass-rate).
2. **Promotion is reversible.** Blue/green aliasing means every promotion is
   atomic and every rollback is instant and zero-copy.
3. **Everything is audited.** A Postgres ledger records every version, check,
   and promotion decision, so I can answer "why was version X rejected in March?"

---

## "How do you handle bad data?"

Two layers. At the **row level**, invalid records are *quarantined with reasons*
instead of crashing the batch — graceful degradation. At the **version level**,
the aggregate quality (e.g. validation pass-rate) is judged by the gates; if it's
below threshold the whole version is rejected and production keeps serving the
last good version. So one bad row degrades gracefully, and a broadly-bad batch is
stopped at the door.

---

## "What happens if the orchestrator is down when data arrives?"

The gateway publishes to a **Redis Streams event bus** *before* Kestra is
involved. If Kestra is restarting, events queue on the bus instead of being lost.
Duplicate uploads are dropped via **idempotency keys** (content hashes), and
messages that repeatedly fail are routed to a **dead-letter queue** for
inspection rather than blocking the stream.

---

## "How is each stage made reliable?"

- **Retries with exponential backoff** and **timeouts** on every task.
- Stages are **stateless**: they hand off through the object store, so a retry
  re-reads its inputs from storage rather than depending on lost in-memory state.
- **Blue/green** guarantees a failed rebuild can't corrupt live data.

---

## "Why Kestra instead of Airflow? What are the tradeoffs?"

Kestra keeps orchestration as **declarative YAML** with first-class triggers,
retries, and artifacts — readable even to reviewers who don't write Python. The
tradeoffs are real and worth naming: it's a **JVM service that's heavier on
resources**, and **YAML gets awkward for deeply dynamic branching**. My mitigation
is architectural: **all business logic lives in a tested Python library**
(`controlplane`) invoked via one CLI, so the flows stay thin and the logic stays
unit-testable. I get Kestra's observability without contorting logic into YAML.

---

## "How would you productionize the embeddings?"

The default is a **feature-hashing embedder** so the platform runs anywhere with
no model download, GPU, or API key — which also keeps CI fast. But it's behind an
interface: swapping in SentenceTransformers or an OpenAI embedder is just
implementing `embed_batch()` with the same signature. The control plane doesn't
care *how* vectors are produced; it cares about **coverage, dimensions, and safe
promotion** — and those contracts don't change.

---

## "How do you know promotion actually worked?"

The `/search` endpoint always queries the **prod alias**, never a specific
collection. So promotion is observable end-to-end: promote a new version →
search results change; roll back → they revert. It's not "trust the logs" — you
can *see* the cutover in query results.

---

## "What would you build next?"

- A real MinIO **bucket-notification trigger** for true file-landing events.
- **Slack/PagerDuty** notification tasks on gate failure (currently logged).
- **Multi-tenant namespaces** for dev/staging/prod isolation.
- A companion **Kestra-vs-Airflow benchmark** repo — same pipeline, both tools,
  measuring LOC, setup friction, and resource usage.

---

## Things to *say* that signal seniority

- "I optimized for **reversibility and auditability**, not just throughput."
- "I made stages **independently retryable** by hand-off through storage."
- "I chose **graceful degradation** (quarantine) over fail-fast at the row level,
  but **fail-closed** at the version level (gates)."
- "I kept the flows thin so **logic stays testable** — 68 tests, CI on every push."
- "I named the **tradeoffs of Kestra** up front and designed around them."
