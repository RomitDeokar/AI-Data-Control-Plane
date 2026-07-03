# Architecture Deep Dive

This document explains **how the AI Data Control Plane is built and why** — the
component responsibilities, the data flow, and the design decisions behind each
part. Pair it with the top-level [README](../README.md) for the summary view.

---

## 1. Mental model

The platform is a **control plane**, not a pipeline. The distinction matters:

- A **pipeline** moves data from A to B.
- A **control plane** *governs* many pipelines: it decides **whether** data is
  allowed into production, records **why**, and can **undo** the decision.

Everything below serves that one idea: *nothing reaches production without
passing explicit, auditable, reversible checks.*

---

## 2. Component responsibilities

| Component | Responsibility | Why it exists |
| --- | --- | --- |
| **Gateway** (FastAPI) | Public front door: accept uploads/webhooks, validate payloads, write to the raw zone, publish an event, expose the registry/search/rollback APIs + web console | A single, observable entry point that fails fast on bad input |
| **Event bus** (Redis Streams) | Decouple ingestion from orchestration; idempotency + dead-letter queue | Ingestion shouldn't be lost or duplicated if Kestra is busy/restarting |
| **Kestra** | Orchestrate the stage graph with triggers, retries, timeouts, parallelism, subflows, artifacts | Declarative, reviewable orchestration; retries & observability for free |
| **`controlplane` library** | All business logic (validate, enrich, embed, gate, promote, rollback) behind a CLI | Keeps flows thin *and* keeps logic unit-testable |
| **Object store** (MinIO) | Data zones: `raw / staged / quarantine / artifacts` | Stage-to-stage hand-off + durable audit artifacts |
| **Vector store** (Qdrant) | Per-version collections + a stable `prod` alias | Blue/green promotion & instant rollback |
| **Registry** (Postgres) | Append-only audit ledger of versions, checks, promotions, quarantine | Auditability & queryable governance |
| **Prometheus + Grafana** | Metrics scraping + dashboards | Operational visibility |

---

## 3. The data zones

```
raw/        untouched source files, exactly as ingested   (provenance)
staged/     validated → enriched → embedded JSONL          (per version_id)
quarantine/ failed records with human-readable reasons     (never silently dropped)
artifacts/  validation / quality / promotion / profile     (UI + audits)
```

Each stage reads its input from a zone and writes its output back to a zone.
This is deliberate: **stages are stateless and independently retryable.** A
Kestra retry re-reads inputs from storage instead of depending on in-memory
state that vanished when the task failed.

---

## 4. The version state machine

```
                 ┌─────────── quarantine (bad rows peeled off)
                 │
ingested → validated → enriched → embedded → gated ──┬── promoted ──→ rolled_back
                                                     │
                                                     └── rejected (prod untouched)
```

Every transition is written to `controlplane.dataset_versions.status` and
surfaced through `GET /versions/{id}`.

The `version_id` (`products-20260703-142530-a1b2`) is **sortable and
human-readable**: dataset name + UTC timestamp + short random suffix. You can
eyeball the ordering and still guarantee uniqueness.

---

## 5. Quality gates — the promotion decision

Gates are the heart of the control plane. **All must pass** or the version is
rejected.

| Gate | Question it answers | Default threshold |
| --- | --- | --- |
| `min_records` | Did enough data survive validation? | ≥ 1 |
| `completeness` | Are required fields actually populated? | ≥ 0.95 |
| `uniqueness` | Are primary keys unique? | ≥ 0.99 |
| `validation_pass_rate` | What fraction of raw rows were valid? | ≥ 0.95 |
| `embedding_coverage` | Did (nearly) every record get a vector? | ≥ 0.98 |
| `schema_drift` | Did the schema fingerprint change vs. last promoted? | no drift |

Thresholds are environment-overridable (`GATE_*` in `.env`). Each gate produces
a `QualityCheckResult` (score, threshold, pass/fail, details) that is persisted
per version — so you can answer *"why was version X rejected?"* months later.

---

## 6. Blue/green promotion & rollback

```
                       prod alias
                          │
        ┌─────────────────┼──────────────────┐
        ▼ (before)                            ▼ (after promote)
  products__<v1>                        products__<v2>
  (previous good)                       (new, gate-passed)
```

- Each version is upserted into **its own collection** `{dataset}__{version_id}`.
- Production reads through the **stable alias** `{dataset}__prod`.
- **Promote** = atomically re-point the alias to the new collection.
- **Rollback** = re-point the alias back. Zero data movement, sub-second.
- Old collections are retained for `RETAIN_VERSIONS` (default 3) so rollback is
  instant; older ones are garbage-collected.

Because `/search` always queries the alias, you can *see* promotion working:
promote → results change; roll back → results revert. That's the demo money-shot.

---

## 7. Resilience patterns

- **Retries with exponential backoff** on every Kestra script task.
- **Timeouts** per task so a hung stage can't block the graph forever.
- **Idempotency keys** (content hash) drop duplicate uploads at the event bus.
- **Dead-letter queue** captures poison messages after `MAX_DELIVERIES`.
- **Quarantine over failure** — one bad row doesn't kill the batch.
- **Blue/green** — a failed rebuild never affects what production serves.

---

## 8. Where to swap in "real" components

The platform is intentionally dependency-light so it runs anywhere, but every
piece has a production-grade seam:

| Default (portable) | Production swap | Seam |
| --- | --- | --- |
| Feature-hashing embedder | SentenceTransformers / OpenAI | implement `embed_batch()` |
| Redis Streams event bus | Kafka / SQS | reimplement `EventBus` |
| MinIO | AWS S3 / GCS | same S3 API, change endpoint |
| Feature-hash search | ANN with a real model | same Qdrant collections |
| Log-based alerts | Slack / PagerDuty tasks | swap the Kestra `Log` task |

The control-plane *contract* (zones, gates, versioned promotion) stays identical.

---

## 9. Flow catalogue

| Flow | Namespace | Trigger | Purpose |
| --- | --- | --- | --- |
| `dataset-pipeline` | `controlplane.ingestion` | webhook | flagship ingest→promote lifecycle |
| `scheduled-rebuild` | `controlplane.pipelines` | cron `0 2 * * *` | nightly rebuild via subflow reuse + ForEach fan-out |
| `emergency-rollback` | `controlplane.governance` | manual/API | instant alias rollback |
| `quality-audit` | `controlplane.governance` | cron `0 6 * * *` | daily platform-health KPIs + conditional alert |
| `sync-namespace-files` | `controlplane.system` | manual/CI | distribute the `controlplane` library to namespaces |

Note how `scheduled-rebuild` **reuses** `dataset-pipeline` as a subflow instead
of duplicating logic — one engine, many callers. That's the "platform, not
pipeline" principle expressed in the flows themselves.
