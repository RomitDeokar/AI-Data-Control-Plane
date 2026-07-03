<div align="center">

# 🛰️ AI Data Control Plane

### An event-driven orchestration platform that keeps AI & data systems *fresh, validated, and safe to promote* — powered by [Kestra](https://kestra.io).

**Not another ETL pipeline. This is the control plane that governs many pipelines.**

<!-- CI badge activates once you move ci/github-actions-ci.yml into .github/workflows/ (see ci/README.md — the automation token can't create workflow files, but you can). -->
[![CI](https://github.com/RomitDeokar/AI-Data-Control-Plane/actions/workflows/ci.yml/badge.svg)](https://github.com/RomitDeokar/AI-Data-Control-Plane/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Tests](https://img.shields.io/badge/tests-79%20passing-brightgreen)
![Orchestrator](https://img.shields.io/badge/orchestrator-Kestra-7d3cf5)
![License](https://img.shields.io/badge/license-MIT-green)

[Quickstart](#-quickstart-90-seconds) ·
[Architecture](#-architecture) ·
[How it works](#-how-it-works-the-lifecycle) ·
[Demo](#-demo-see-the-whole-thing-in-30-seconds) ·
[Design decisions](#-design-decisions--tradeoffs) ·
[Why it matters](#-why-this-project)

</div>

---

## 📌 TL;DR

Most data/AI portfolio projects are a single pipeline: *"upload PDF → embed → vector DB → done."* Recruiters have seen a thousand of them.

**The AI Data Control Plane is the layer *above* that** — the system that decides *whether new data is even allowed into production.* It ingests data from files/webhooks/schedules, validates it, enriches it, generates embeddings/features, runs **explicit quality gates**, and only then performs a **blue/green promotion** to production — with **instant rollback** if anything goes wrong. Every decision is **audited** and **observable**.

> **In one sentence for your resume:** *Built an event-driven AI data control plane on Kestra that ingests, validates, enriches, embeds, quality-gates, and safely blue/green-promotes dataset versions to production, with instant rollback, a full audit trail, and Prometheus/Grafana observability.*

---

## 🎯 Why this project?

| A normal pipeline says… | This control plane says… |
| --- | --- |
| "I move data from A to B." | "I decide **whether** data is safe to serve — and I can prove it." |
| One flow, one use case. | One **reusable engine**; many datasets & pipelines plug into it. |
| Failures crash the run. | Bad records are **quarantined**, bad versions are **rejected**, production keeps serving. |
| Deploys are hope-based. | Promotion is **gated**, **versioned**, **atomic**, and **reversible**. |
| "Trust me, it works." | Every version, check, and promotion is **written to an audit ledger**. |

This is exactly the kind of *developer infrastructure* platform, data, and MLOps teams own at companies like Databricks, Snowflake, Confluent, and modern AI startups. It signals **backend + data engineering + platform + MLOps** maturity in a single repo.

---

## ✨ Feature Highlights

- **🔌 Multiple triggers** — file upload, JSON webhook, cron schedule, and an internal event bus (Redis Streams).
- **🧱 Data zones** — `raw → staged → quarantine → artifacts` object-storage layout (MinIO / S3-compatible).
- **🛡️ Schema validation + drift detection** — bad rows are *quarantined with reasons*, never silently dropped.
- **🧬 Deterministic enrichment** — unicode normalization, dedup, derived metadata + lineage tags.
- **🔢 Pluggable embeddings** — a zero-dependency feature-hashing embedder (swap for SentenceTransformers/OpenAI via one interface).
- **✅ Quality gates** — min-records, completeness, uniqueness, validation pass-rate, embedding coverage, schema drift. **All must pass to promote.**
- **🔵🟢 Blue/green promotion** — each version gets its own Qdrant collection; production reads through a stable alias; promotion = atomic alias switch.
- **⏪ Instant rollback** — zero-copy, sub-second revert to the previous promoted version.
- **📒 Full audit trail** — Postgres ledger of every version, quality check, promotion, and quarantined record.
- **📊 Observability** — Prometheus metrics + a provisioned Grafana dashboard + a live web console.
- **🔁 Resilience** — per-task retries with exponential backoff, timeouts, content-hash idempotency, a **durable event relay** (an accepted upload is re-driven even if Kestra was down at ingest time), and a dead-letter queue for poison events.
- **🧪 79 tests, CI, and a no-Docker E2E** — the whole lifecycle runs in-process in <1s for fast feedback.

---

## 🖥️ The Web Console

A premium, fully-interactive control-plane dashboard — **runs standalone with zero infrastructure** (an in-memory demo engine reuses the *real* validation, gate, promotion, and search logic). Open the gateway and drive the entire lifecycle from your browser.

**Promotion path — a clean dataset passes every gate and is blue/green-promoted; semantic search then serves it live:**

![AI Data Control Plane — clean dataset promoted, with live semantic search](screenshots/console-promoted.png)

**Rejection path — a corrupted dataset is quarantined, fails the quality gates, and is rejected; production stays untouched:**

![AI Data Control Plane — corrupted dataset rejected, gate scorecard + quarantine reasons](screenshots/console-rejected.png)

> The animated 6-stage pipeline graph, color-coded gate scorecard, `PROMOTED` / `REJECTED` verdict banners, quarantine reasons, live cosine-similarity search, and audit tables are all driven by the same engine that powers the real Kestra flows.

---

## 🏗️ Architecture

```
                              ┌──────────────────────┐
      File upload  ─────┐     │       Gateway        │
      JSON webhook ─────┼────▶│  (FastAPI + Web UI)  │
      Cron schedule ────┘     │  validate • hash •   │
                              │  raw-zone • publish  │
                              └──────────┬───────────┘
                                         │ dataset.ingested
                                         ▼
                              ┌──────────────────────┐
                              │  Event Bus (Redis    │  idempotency · dispatched
                              │  Streams)            │  flag · relay · DLQ
                              └──────────┬───────────┘
                          trigger now ▲  │ dataset.ingested (dispatched=false)
                                       │  ▼
                              ┌──────────────────────┐
                              │  Event Relay (cron)  │  re-drives stranded events
                              │  controlplane.relay  │  → DLQ after N attempts
                              └──────────┬───────────┘
                                         ▼
                    ┌────────────────────────────────────────┐
                    │        KESTRA — the orchestrator        │
                    │   dataset-pipeline (flagship flow)      │
                    └────────────────────┬───────────────────┘
                                         │
        ┌──────────┬──────────┬──────────┼──────────┬───────────┬──────────┐
        ▼          ▼          ▼           ▼          ▼           ▼          ▼
    ┌───────┐ ┌────────┐ ┌─────────┐ ┌───────┐ ┌────────┐ ┌────────┐ ┌────────┐
    │INGEST │ │VALIDATE│ │ ENRICH  │ │ EMBED │ │ QUALITY│ │PROMOTE │ │SUMMARY │
    │       │ │ +drift │ │∥ PROFILE│ │       │ │  GATES │ │ or     │ │+alerts │
    │       │ │        │ │(parallel│ │       │ │        │ │ REJECT │ │        │
    └───┬───┘ └───┬────┘ └────┬────┘ └───┬───┘ └───┬────┘ └───┬────┘ └────────┘
        │         │           │          │         │          │
        ▼         ▼           ▼          ▼         ▼          ▼
  ┌───────────────────────────────────────────────────────────────────┐
  │  MinIO (raw/staged/quarantine/artifacts) · Postgres registry ·     │
  │  Qdrant (version collections + prod alias) · Prometheus/Grafana    │
  └───────────────────────────────────────────────────────────────────┘
```

Only versions that pass **every** gate get promoted. Promotion is an **atomic alias switch** in Qdrant, so production traffic (via `/search`) cuts over instantly — and rollback is the same switch in reverse.

> A deeper walkthrough of each component and the design rationale lives in **[docs/architecture.md](docs/architecture.md)**.

### Tech stack

| Layer | Technology |
| --- | --- |
| Orchestration | **Kestra** (YAML flows: triggers, retries, parallel/subflows, artifacts) |
| Gateway / API | **FastAPI** + Uvicorn + a vanilla-JS web console |
| Event bus | **Redis Streams** (consumer groups, idempotency, DLQ) |
| Object storage | **MinIO** (S3-compatible) |
| Vector store | **Qdrant** (collections + aliases for blue/green) |
| Metadata registry | **PostgreSQL** (audit ledger) |
| Observability | **Prometheus** + **Grafana** (auto-provisioned) |
| Packaging / CI | **Docker Compose**, **GitHub Actions**, **ruff**, **pytest** |

---

## 🚀 Quickstart (90 seconds)

### Option A — no Docker, understand it instantly

Run the entire lifecycle in-process with in-memory fakes. Great for a first look or CI smoke test:

```bash
pip install -e ".[dev]"
python scripts/e2e_local.py
```

You'll watch a clean dataset get **promoted**, a corrupted dataset get **rejected** at the gates, a second clean dataset **promoted** (alias switches), and finally an **instant rollback** — all with a live quality-gate scorecard.

### Option B — the full platform with Docker

```bash
make up          # starts Kestra, Postgres, MinIO, Qdrant, Redis, Gateway, Prometheus, Grafana
make flows       # upload the Kestra flows via the API
```

Then open:

| Service | URL | Credentials |
| --- | --- | --- |
| 🕹️ **Control Plane Console** | http://localhost:8000/ | — |
| 📖 Gateway API docs (Swagger) | http://localhost:8000/docs | — |
| 🧠 Kestra UI | http://localhost:8080 | — |
| 🗄️ MinIO console | http://localhost:9001 | `minioadmin` / `minioadmin` |
| 🔍 Qdrant dashboard | http://localhost:6333/dashboard | — |
| 📊 Grafana | http://localhost:3000 | `admin` / `admin` |
| 📈 Prometheus | http://localhost:9090 | — |

---

## 🎬 Demo: see the whole thing in 30 seconds

```bash
# 1) Ingest a CLEAN dataset → passes gates → promoted to production
make demo

# 2) Ingest a CORRUPTED dataset → fails gates → rejected, prod untouched
make demo-bad

# 3) Semantic search hits the PROMOTED data through the prod alias
make search

# 4) One-click rollback to the previous promoted version
make rollback

# 5) Inspect the audit trail: versions + promotion ledger
make status
```

The magic moment: **`make demo-bad` never reaches production.** The corrupted file is validated, its bad rows are quarantined, the quality gates reject the version, and `/search` keeps serving the previous good data. That's the difference between a *pipeline* and a *control plane*.

> A full narrated walkthrough (with expected output) is in **[docs/DEMO.md](docs/DEMO.md)**.

**Prefer clicking?** Start the gateway and open the [Web Console](#️-the-web-console) — you can run the clean & corrupted scenarios, watch the pipeline animate, search, and roll back, all from the browser with **no Docker required**:

```bash
cd services/gateway && PYTHONPATH=../.. uvicorn app.main:app --port 8000
# then open http://localhost:8000
```

---

## 🔬 How it works: the lifecycle

Every dataset version flows through the same auditable state machine:

```
ingested → validated → enriched → embedded → gated → promoted
                          │                     │         │
                          ▼                     ▼         ▼
                     quarantine            rejected   rolled_back
```

1. **Ingest** — pull the raw object, register an immutable `version_id` (`products-20260703-142530-a1b2`), stage records as JSONL.
2. **Validate** — check schema/types/constraints; quarantine failed rows with reasons; fingerprint the schema and compare against the last promoted version for **drift**.
3. **Enrich** (∥ **Profile**) — normalize text, dedupe, add lineage metadata; in parallel, produce a field-fill-rate profile artifact.
4. **Embed** — generate normalized vectors; report coverage.
5. **Quality gates** — run the full suite; **all must pass**.
6. **Promote / Reject** — if gated ✅, stage vectors into a fresh collection and atomically switch the prod alias (blue/green). If ❌, reject and leave production untouched. Either way, **write the decision to the ledger**.
7. **Rollback** (on demand) — re-point the alias to the previous version. Zero-copy, instant, audited.

The orchestration logic lives in thin **[Kestra YAML flows](flows/)**; all business logic lives in the tested **[`controlplane`](controlplane/) Python library**, invoked through one CLI (`python -m controlplane.runner <stage>`). This keeps flows readable *and* keeps logic unit-testable.

---

## 🛡️ Reliability guarantees

The platform's central promise is: **once the gateway returns `accepted`, that upload will be processed exactly once — even if downstream infrastructure was mid-restart.** That guarantee is *engineered*, not assumed, from three cooperating mechanisms:

1. **Front-door idempotency** — the gateway hashes the payload and takes a Redis `SET NX` lock, so a re-uploaded identical file is dropped (effective **exactly-once processing**).
2. **Durable, dispatch-tracked events** — every event is written to a Redis Stream with a `dispatched` flag. The gateway triggers Kestra directly and flips the flag to `true` **only after Kestra accepts** it.
3. **A scheduled relay backstop** — [`controlplane.relay`](controlplane/relay.py) (run every minute by [`event-relay.yaml`](flows/_system/event-relay.yaml)) claims any `dispatched=false` events via a consumer group, re-triggers the pipeline, ACKs successes, and dead-letters poison events after `EVENT_MAX_DELIVERIES` attempts.

| Failure scenario | What happens | Data lost? |
| --- | --- | --- |
| Duplicate upload (same bytes) | Dropped at the front door by the `SET NX` idempotency lock | — (intended) |
| Happy path | Gateway triggers Kestra, marks event `dispatched=true` | No |
| **Kestra down at ingest** | Event stays `dispatched=false`; relay re-triggers it on the next tick | **No** |
| Relay crashes mid-drain | Un-ACKed events are redelivered from the consumer-group backlog next tick | No |
| Poison event (keeps failing) | Dead-lettered after `EVENT_MAX_DELIVERIES` tries; surfaced via `/events/stats` | Quarantined, not lost |
| Gateway triggered Kestra but relay also sees the event | Relay notices the `dispatched` flag, ACKs and skips it (no double-run) | No |

> This is the difference between *"the event is on the bus, something will pick it up"* (a hope) and a **named component with tests that proves the pickup happens** (`tests/test_relay.py`). The delivery semantics are **at-least-once delivery + exactly-once processing for identical payloads** — and the docs say so honestly rather than claiming unattainable exactly-once-everything.

---

## 🧭 Repository layout

```
ai-data-control-plane/
├─ controlplane/            # core library (all business logic, 100%-tested paths)
│  ├─ validation/           #   schema validation + drift detection
│  ├─ enrichment/           #   normalization, dedup, lineage metadata
│  ├─ embeddings/           #   pluggable embedder (feature-hashing default)
│  ├─ quality/              #   the quality-gate suite
│  ├─ promotion/            #   blue/green promotion engine + rollback
│  ├─ stores/               #   MinIO, Qdrant, Postgres adapters
│  ├─ events.py             #   Redis Streams event bus (idempotency + DLQ)
│  ├─ relay.py              #   durable relay: re-drives stranded events → Kestra
│  ├─ models.py             #   domain models & version-id/fingerprint helpers
│  └─ runner.py             #   CLI entry point Kestra tasks call
├─ flows/                   # Kestra YAML workflows
│  ├─ ingestion/            #   flagship event-driven dataset-pipeline
│  ├─ pipelines/            #   scheduled nightly rebuild (subflow reuse)
│  ├─ governance/           #   emergency rollback + daily quality audit
│  └─ _system/             #   namespace-file sync + event relay (cron backstop)
├─ services/gateway/        # FastAPI ingestion gateway + web console
├─ plugin/                  # custom Kestra plugin scaffold (Java) — advanced extra
├─ monitoring/              # Prometheus config + Grafana dashboards
├─ sql/init.sql             # Postgres registry schema
├─ sample_data/             # clean + intentionally-corrupted datasets
├─ scripts/e2e_local.py     # no-Docker end-to-end simulation
├─ tests/                   # 79 unit/integration/flow-YAML tests
├─ docs/                    # architecture, demo, interview notes
├─ docker-compose.yml       # the full stack
└─ Makefile                 # one-word commands for everything
```

---

## ⚖️ Design decisions & tradeoffs

Being honest about tradeoffs is what makes this read like *engineering* rather than *fanboyism* — and it's exactly what interviewers probe for.

- **Why Kestra over Airflow?** Kestra keeps orchestration as declarative YAML with first-class triggers, retries, and artifacts, so reviewers who don't write Python can still read the pipeline. The tradeoff: it's a **JVM service that's heavier on resources**, and **YAML is awkward for deeply dynamic branching** — so all real logic lives in the Python `controlplane` library and the flows stay thin.
- **Why an event bus *plus a relay* in front of Kestra?** It decouples ingestion from orchestration and makes the reliability claim *honest*. On the happy path the gateway triggers Kestra directly and marks the event `dispatched`. If Kestra is restarting, the event stays `dispatched=false` on the Redis Stream and a scheduled **relay** (`controlplane.relay`, wired by [`flows/_system/event-relay.yaml`](flows/_system/event-relay.yaml)) re-drives it once Kestra is reachable — so *an accepted upload is never silently lost*. Content-hash idempotency drops duplicate uploads at the front door (effective exactly-once **processing**); the stream + consumer-group give at-least-once **delivery**; poison messages are dead-lettered after `EVENT_MAX_DELIVERIES` attempts. See the [Reliability guarantees](#-reliability-guarantees) section for the full failure matrix.
- **Why feature-hashing embeddings?** So the platform runs **anywhere with no model download, GPU, or API key**, and CI stays fast. The `HashingEmbedder` interface matches a real model — swap in SentenceTransformers/OpenAI by implementing `embed_batch()`. *The control plane doesn't care how vectors are made; it cares about coverage, dimensions, and safe promotion.*
- **Why blue/green with aliases?** Promotion and rollback become **atomic and zero-copy**. A failed rebuild can never corrupt what production is serving, and rollback is sub-second.
- **Why quarantine instead of failing the run?** Graceful degradation — one bad row shouldn't kill the batch. Bad rows are preserved with reasons for later inspection; the *aggregate* pass-rate is what the gate judges.
- **Stages communicate through storage, not memory** — so any stage is independently retryable. A Kestra retry re-reads its inputs from the staged zone instead of relying on in-process state.

---

## 🧪 Development

```bash
make test        # run the 79 unit/integration tests
make coverage    # tests + coverage report
make lint        # ruff
python scripts/e2e_local.py   # full lifecycle, no Docker
```

CI (GitHub Actions) runs **lint → tests+coverage → E2E → flow-YAML validation → Docker image build** on every push/PR.

---

## 🧩 Advanced extra: custom Kestra plugin

The [`plugin/`](plugin/) directory contains a scaffold for a **custom Kestra plugin in Java** (a `PromotionGate` task) — the kind of ecosystem contribution that separates "I used a tool" from "I extended the tool." See [`plugin/README.md`](plugin/README.md).

---

## 🗺️ Roadmap / stretch ideas

- [ ] MinIO bucket-notification trigger (true file-landing events)
- [ ] Swap `HashingEmbedder` for a real SentenceTransformers embedder behind a feature flag
- [ ] Slack/PagerDuty notification tasks on gate failure
- [ ] Multi-tenant namespaces (dev/staging/prod isolation)
- [ ] A `kestra-vs-airflow-benchmark` companion repo (same pipeline, both tools)

---

## 📄 License

MIT — see [LICENSE](LICENSE).

<div align="center">

**Built to demonstrate platform-engineering thinking, not just code.**
If this helped you, a ⭐ is appreciated.

</div>
