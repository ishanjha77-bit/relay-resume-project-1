package dev.relay.alert;

import java.time.Instant;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

/** Alertmanager webhook payload (version 4). Alertmanager uses camelCase, unlike our API. */
@JsonIgnoreProperties(ignoreUnknown = true)
@JsonNaming(PropertyNamingStrategies.LowerCamelCaseStrategy.class)
public record AlertmanagerWebhook(
        String version,
        String groupKey,
        String status,
        Map<String, String> groupLabels,
        Map<String, String> commonLabels,
        List<Alert> alerts) {

    @JsonIgnoreProperties(ignoreUnknown = true)
    @JsonNaming(PropertyNamingStrategies.LowerCamelCaseStrategy.class)
    public record Alert(
            String status,
            Map<String, String> labels,
            Map<String, String> annotations,
            Instant startsAt,
            Instant endsAt,
            String fingerprint) {
    }
}
