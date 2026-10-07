package dev.relay.sandbox.inventory;

import java.util.List;
import java.util.stream.IntStream;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * @param restockBelow  restock a SKU when a reservation leaves fewer units than this
 * @param restockAmount units added per restock
 * @param hotSkus       how many of the lowest-numbered SKUs get most of the traffic
 */
@ConfigurationProperties(prefix = "inventory")
public record InventoryProperties(int restockBelow, int restockAmount, int hotSkus) {

    public List<String> hotSkuIds() {
        return IntStream.rangeClosed(1, hotSkus).mapToObj(i -> "SKU-%04d".formatted(i)).toList();
    }
}
