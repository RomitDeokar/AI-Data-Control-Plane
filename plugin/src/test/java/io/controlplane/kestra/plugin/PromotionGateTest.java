package io.controlplane.kestra.plugin;

import io.kestra.core.junit.annotations.KestraTest;
import io.kestra.core.models.property.Property;
import io.kestra.core.runners.RunContext;
import io.kestra.core.runners.RunContextFactory;
import jakarta.inject.Inject;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

@KestraTest
class PromotionGateTest {

    @Inject
    private RunContextFactory runContextFactory;

    @Test
    void promotesWhenAllChecksPass() throws Exception {
        PromotionGate task = PromotionGate.builder()
            .scores(Property.of(Map.of("completeness", 0.99, "uniqueness", 1.0)))
            .thresholds(Property.of(Map.of("completeness", 0.95, "uniqueness", 0.99)))
            .failOnReject(Property.of(false))
            .build();

        RunContext runContext = runContextFactory.of();
        PromotionGate.Output output = task.run(runContext);

        assertTrue(output.isPassed());
        assertEquals("promoted", output.getDecision());
        assertEquals(2, output.getTotalChecks());
        assertTrue(output.getFailedChecks().isEmpty());
    }

    @Test
    void rejectsWhenAnyCheckFails() throws Exception {
        PromotionGate task = PromotionGate.builder()
            .scores(Property.of(Map.of("completeness", 0.80, "uniqueness", 1.0)))
            .thresholds(Property.of(Map.of("completeness", 0.95, "uniqueness", 0.99)))
            .failOnReject(Property.of(false))
            .build();

        PromotionGate.Output output = task.run(runContextFactory.of());

        assertFalse(output.isPassed());
        assertEquals("rejected", output.getDecision());
        assertEquals(1, output.getFailedChecks().size());
        assertTrue(output.getFailedChecks().contains("completeness"));
    }

    @Test
    void throwsWhenFailOnRejectIsTrue() {
        PromotionGate task = PromotionGate.builder()
            .scores(Property.of(Map.of("completeness", 0.10)))
            .thresholds(Property.of(Map.of("completeness", 0.95)))
            .failOnReject(Property.of(true))
            .build();

        assertThrows(IllegalStateException.class, () -> task.run(runContextFactory.of()));
    }
}
