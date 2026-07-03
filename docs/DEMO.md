# Demo Walkthrough

A narrated, copy-pasteable tour. Two modes: **no-Docker** (instant) and
**full stack** (the real thing). Expected output is shown so you know what
"correct" looks like.

---

## Mode 1 — No Docker (10 seconds, great for a first look or interviews)

```bash
pip install -e ".[dev]"
python scripts/e2e_local.py
```

This runs the *entire* lifecycle in-process with in-memory fakes for the vector
store and registry. You'll see three runs and a rollback:

```
RUN 1 — clean dataset        → PROMOTED   (alias now serves v1)
RUN 2 — corrupted dataset    → REJECTED   (gates fail; prod still serves v1)
RUN 3 — clean dataset again  → PROMOTED   (alias switches v1 → v3)
ROLLBACK                     → alias reverts v3 → v1  (instant, zero-copy)
E2E RESULT: ✅ PASS
```

The key line to point at in an interview is **RUN 2**: a corrupted dataset is
validated, its bad rows are quarantined, the `validation_pass_rate` gate fails,
and **production is never touched.** That single behaviour is the whole thesis of
the project.

---

## Mode 2 — Full stack (the real platform)

### 1. Start everything

```bash
make up
```

Wait ~30–60s for Kestra to become healthy, then upload the flows:

```bash
make flows
```

Open the **Control Plane Console** at http://localhost:8000/ — it shows live
versions, promotions, event-bus depth, and lets you run searches/rollbacks from
the browser (perfect for screenshots / a demo GIF).

### 2. Promote a clean dataset

```bash
make demo
```

Expected (abridged):

```json
{
  "status": "accepted",
  "dataset": "products",
  "record_count": 60,
  "event_id": "...",
  "kestra": { "triggered": true, "execution_id": "..." }
}
```

Watch the execution graph light up in the Kestra UI (http://localhost:8080).
When it finishes, the version's status is `promoted`.

### 3. Try to promote a corrupted dataset

```bash
make demo-bad
```

The pipeline runs, but the quality gates **reject** the version. Check it:

```bash
make status
```

You'll see the bad version with `decision: rejected` in the promotion ledger,
and the previous good version still serving.

### 4. Prove promotion works with semantic search

```bash
make search
```

`/search/products?q=wireless+headphones` queries the **prod alias**, so it always
reflects whatever is currently promoted. Results include the `serving_collection`
and each hit's `version_id`.

### 5. Roll back

```bash
make rollback
```

```json
{ "decision": "rolled_back", "alias": "products__prod", "now_serving": "products__<previous>" }
```

Run `make search` again — results revert to the previous version. Sub-second,
zero-copy, fully audited.

### 6. Inspect the audit trail

```bash
make status        # versions + promotion ledger
curl -s localhost:8000/events/stats | python3 -m json.tool   # event bus depth + DLQ
```

---

## What to capture for your portfolio

For maximum impact on GitHub/LinkedIn, record:

1. A **GIF of the Kestra execution graph** running the flagship pipeline.
2. A **screenshot of the Control Plane Console** showing versions + promotions.
3. A **screenshot of the Grafana dashboard** (http://localhost:3000).
4. The **`demo-bad` rejection** — the moment production is protected.

Drop them in a `screenshots/` folder and embed them in the README.

---

## Cleaning up

```bash
make down          # stop everything (keep data)
make clean         # stop everything and delete all volumes
```
