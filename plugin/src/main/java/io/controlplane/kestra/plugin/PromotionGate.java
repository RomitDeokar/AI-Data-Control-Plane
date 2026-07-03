package io.controlplane.kestra.plugin;

import io.kestra.core.models.annotations.Example;
import io.kestra.core.models.annotations.Plugin;
import io.kestra.core.models.annotations.PluginProperty;
import io.kestra.core.models.property.Property;
import io.kestra.core.models.tasks.RunnableTask;
import io.kestra.core.models.tasks.Task;
import io.kestra.core.runners.RunContext;
import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotNull;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.experimental.SuperBuilder;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Custom Kestra task: <b>PromotionGate</b>.
 *
 * <p>Evaluates a set of quality-check scores against their thresholds and returns
 * a single, auditable promote / reject decision. This is the "should this dataset
 * version go to production?" decision expressed as a first-class, reusable Kestra
 * task rather than a bespoke script — so any flow in any namespace can drop in the
 * same governance gate.</p>
 *
 * <p>It mirrors {@code controlplane.quality.QualityGateRunner} in the Python
 * library; shipping it as a plugin is the ecosystem-native way to make the gate
 * reusable across teams and pipelines.</p>
 */
@SuperBuilder
@NoArgsConstructor
@Getter
@EqualsAndHashCode
@Schema(
    title = "Evaluate quality-gate scores and emit a promote/reject decision.",
    description = "Compares each provided check score against its threshold. If every "
        + "check passes, the version is eligible for promotion; otherwise it is rejected "
        + "and the failing checks are reported. Designed for the AI Data Control Plane, "
        + "but reusable in any governance workflow."
)
@Plugin(
    examples = {
        @Example(
            title = "Gate a dataset version on completeness, uniqueness and drift.",
            code = {
                "id: quality_gate",
                "type: io.controlplane.kestra.plugin.PromotionGate",
                "scores:",
                "  completeness: 0.99",
                "  uniqueness: 1.0",
                "  embedding_coverage: 0.985",
                "thresholds:",
                "  completeness: 0.95",
                "  uniqueness: 0.99",
                "  embedding_coverage: 0.98"
            }
        )
    }
)
public class PromotionGate extends Task implements RunnableTask<PromotionGate.Output> {

    @Schema(title = "Observed check scores", description = "Map of checkName → score (0.0–1.0).")
    @PluginProperty(dynamic = true)
    @NotNull
    private Property<Map<String, Double>> scores;

    @Schema(title = "Required thresholds", description = "Map of checkName → minimum passing score.")
    @PluginProperty(dynamic = true)
    @NotNull
    private Property<Map<String, Double>> thresholds;

    @Schema(
        title = "Fail the task run when the gate rejects",
        description = "If true (default), a rejected verdict throws so the flow branches into "
            + "its error handler. If false, the task succeeds and you branch on outputs.passed."
    )
    @PluginProperty
    @lombok.Builder.Default
    private Property<Boolean> failOnReject = Property.of(true);

    @Override
    public Output run(RunContext runContext) throws Exception {
        Map<String, Double> scoreMap = runContext.render(this.scores).asMap(String.class, Double.class);
        Map<String, Double> thresholdMap =
            runContext.render(this.thresholds).asMap(String.class, Double.class);
        boolean failOnRejectValue = runContext.render(this.failOnReject).as(Boolean.class).orElse(true);

        List<CheckResult> results = new ArrayList<>();
        for (Map.Entry<String, Double> entry : thresholdMap.entrySet()) {
            String name = entry.getKey();
            double threshold = entry.getValue();
            Double score = scoreMap.get(name);
            boolean passed = score != null && score >= threshold;
            results.add(new CheckResult(name, score, threshold, passed));
        }

        List<String> failed = results.stream()
            .filter(r -> !r.passed())
            .map(CheckResult::name)
            .toList();

        boolean allPassed = failed.isEmpty() && !results.isEmpty();

        runContext.logger().info(
            "PromotionGate: {} ({}/{} checks passed){}",
            allPassed ? "PROMOTE" : "REJECT",
            results.size() - failed.size(),
            results.size(),
            failed.isEmpty() ? "" : " — failed: " + String.join(", ", failed)
        );

        if (!allPassed && failOnRejectValue) {
            throw new IllegalStateException(
                "Promotion gate rejected the version. Failed checks: " + String.join(", ", failed)
            );
        }

        return Output.builder()
            .passed(allPassed)
            .decision(allPassed ? "promoted" : "rejected")
            .totalChecks(results.size())
            .failedChecks(failed)
            .build();
    }

    private record CheckResult(String name, Double score, double threshold, boolean passed) {}

    @lombok.Builder
    @Getter
    public static class Output implements io.kestra.core.models.tasks.Output {
        @Schema(title = "Whether every gate passed")
        private final boolean passed;

        @Schema(title = "The decision: promoted | rejected")
        private final String decision;

        @Schema(title = "Total number of checks evaluated")
        private final int totalChecks;

        @Schema(title = "Names of the checks that failed")
        private final List<String> failedChecks;
    }
}
