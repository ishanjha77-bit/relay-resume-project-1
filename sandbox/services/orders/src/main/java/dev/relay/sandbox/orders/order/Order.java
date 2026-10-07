package dev.relay.sandbox.orders.order;

import java.time.Instant;
import java.util.UUID;

public record Order(
        UUID id,
        String customerId,
        String sku,
        int quantity,
        long totalCents,
        OrderStatus status,
        String paymentId,
        String discountCode,
        Instant createdAt) {
}
