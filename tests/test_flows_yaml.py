"""Static checks on the Kestra flow definitions — CI safety net.

These don't need a running Kestra: they assert structural invariants every
flow must satisfy (ids, namespaces, retries on network-bound tasks, etc.).
"""

from pathlib import Path

import pytest
import yaml

FLOW_DIR = Path(__file__).parent.parent / "flows"
FLOW_FILES = sorted(FLOW_DIR.rglob("*.yaml"))


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


@pytest.mark.parametrize("path", FLOW_FILES, ids=lambda p: p.name)
def test_flow_parses_and_has_required_keys(path: Path):
    flow = load(path)
    assert flow.get("id"), f"{path.name}: missing flow id"
    assert flow.get("namespace", "").startswith("controlplane."), (
        f"{path.name}: namespace must live under controlplane.*"
    )
    assert flow.get("tasks"), f"{path.name}: flow has no tasks"
    assert flow.get("description"), f"{path.name}: flows must be documented"


@pytest.mark.parametrize("path", FLOW_FILES, ids=lambda p: p.name)
def test_all_tasks_have_ids_and_types(path: Path):
    flow = load(path)

    def walk(tasks):
        for task in tasks:
            assert task.get("id"), f"{path.name}: task missing id"
            assert task.get("type", "").startswith("io.kestra."), (
                f"{path.name}: task {task.get('id')} has invalid type"
            )
            for key in ("tasks", "then", "else"):
                if key in task:
                    walk(task[key])

    walk(flow["tasks"])


def test_flagship_pipeline_has_reliability_features():
    flow = load(FLOW_DIR / "ingestion" / "dataset-pipeline.yaml")
    tasks_by_id = {}

    def walk(tasks):
        for task in tasks:
            tasks_by_id[task["id"]] = task
            for key in ("tasks", "then", "else"):
                if key in task:
                    walk(task[key])

    walk(flow["tasks"])

    # retries on the network-bound stages
    for stage in ("ingest", "validate", "embed"):
        assert "retry" in tasks_by_id[stage], f"{stage} must define a retry policy"
        assert "timeout" in tasks_by_id[stage], f"{stage} must define a timeout"

    # parallel processing exists
    assert tasks_by_id["parallel_processing"]["type"].endswith("flow.Parallel")

    # error handling defined
    assert flow.get("errors"), "flagship flow must define an errors branch"

    # webhook trigger wired to the gateway's key
    triggers = flow.get("triggers", [])
    webhook = [t for t in triggers if t["type"].endswith("trigger.Webhook")]
    assert webhook and webhook[0]["key"] == "controlplane-webhook-key"


def test_scheduled_rebuild_uses_subflow_composition():
    flow = load(FLOW_DIR / "pipelines" / "scheduled-rebuild.yaml")
    text = (FLOW_DIR / "pipelines" / "scheduled-rebuild.yaml").read_text()
    assert "io.kestra.plugin.core.flow.Subflow" in text, (
        "scheduled rebuild must reuse the flagship pipeline via Subflow"
    )
    triggers = flow.get("triggers", [])
    assert any(t["type"].endswith("trigger.Schedule") for t in triggers)


def test_event_relay_is_scheduled_and_calls_relay_module():
    path = FLOW_DIR / "_system" / "event-relay.yaml"
    flow = load(path)
    text = path.read_text()
    # runs on a schedule (the backstop poller)
    triggers = flow.get("triggers", [])
    assert any(t["type"].endswith("trigger.Schedule") for t in triggers), (
        "event-relay must be scheduled to poll the bus"
    )
    # actually invokes the relay module we ship (not a dangling reference)
    assert "controlplane.relay" in text, (
        "event-relay flow must call `python -m controlplane.relay`"
    )


def test_unique_flow_ids_within_namespace():
    seen = set()
    for path in FLOW_FILES:
        flow = load(path)
        key = (flow["namespace"], flow["id"])
        assert key not in seen, f"duplicate flow {key}"
        seen.add(key)
