# Custom Kestra Plugin — `PromotionGate`

A first-class, reusable Kestra task written in Java against the official Kestra
plugin API. It packages the control plane's promotion decision as a native
orchestrator primitive rather than re-implementing it in inline scripts.

## What it is

`io.controlplane.kestra.plugin.PromotionGate` is a custom Kestra task that turns
the control plane's promotion decision into a **reusable governance primitive**.
Instead of every flow re-implementing "compare scores to thresholds and decide
promote/reject" in a script, any flow in any namespace can drop in:

```yaml
- id: quality_gate
  type: io.controlplane.kestra.plugin.PromotionGate
  scores:
    completeness: "{{ outputs.gate.vars.completeness }}"
    uniqueness: "{{ outputs.gate.vars.uniqueness }}"
    embedding_coverage: "{{ outputs.embed.vars.coverage }}"
  thresholds:
    completeness: 0.95
    uniqueness: 0.99
    embedding_coverage: 0.98
  failOnReject: true   # throw → flow branches to its error handler on rejection
```

Outputs:

| Output | Meaning |
| --- | --- |
| `passed` | `true` if every check met its threshold |
| `decision` | `"promoted"` or `"rejected"` |
| `totalChecks` | number of checks evaluated |
| `failedChecks` | list of check names that failed |

It mirrors `controlplane.quality.QualityGateRunner` from the Python library — the
same governance logic, offered natively to the orchestrator so it's reusable
across teams and pipelines.

## Build & test

Requires JDK 21. The project ships a Gradle wrapper, so no separate Gradle
install is needed. The Kestra dependency version aligns with
[`../docker-compose.yml`](../docker-compose.yml).

```bash
cd plugin
./gradlew test       # runs PromotionGateTest against the Kestra test harness
./gradlew jar        # produces build/libs/plugin-controlplane-1.0.0.jar
```

On Windows use `gradlew.bat` instead of `./gradlew`.

## Install into your local Kestra

Mount the built jar into the Kestra container's plugin directory:

```yaml
# docker-compose.yml (kestra service)
volumes:
  - ./plugin/build/libs:/app/plugins
```

Restart Kestra and the `PromotionGate` task appears in the UI's plugin catalogue,
autocompletion included.

## Layout

```
plugin/
├─ gradlew / gradlew.bat # Gradle wrapper (JDK 21)
├─ gradle/wrapper/       # wrapper jar + pinned distribution (gradle 8.5)
├─ build.gradle          # java-library, Kestra deps, plugin manifest
├─ settings.gradle
└─ src/
   ├─ main/java/io/controlplane/kestra/plugin/
   │  ├─ PromotionGate.java   # the task
   │  └─ package-info.java    # @PluginSubGroup metadata
   └─ test/java/io/controlplane/kestra/plugin/
      └─ PromotionGateTest.java
```

## Publishing (stretch)

Follow the [official Kestra plugin developer guide](https://kestra.io/docs/plugin-developer-guide)
to publish to a Maven repository, then reference it in Kestra's plugin config.
