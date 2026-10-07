package dev.relay.incident;

import java.util.List;

/**
 * Alerts that arrived together and belong to the same group: an Alertmanager
 * notification group, or one signed webhook delivery.
 *
 * @param groupKey identifies the alert group; one active incident exists per group
 */
public record AlertBatch(String source, String groupKey, String namespace, List<AlertItem> alerts) {
}
