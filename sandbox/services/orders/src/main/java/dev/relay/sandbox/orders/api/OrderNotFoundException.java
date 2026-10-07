package dev.relay.sandbox.orders.api;

import java.util.UUID;

class OrderNotFoundException extends RuntimeException {

    OrderNotFoundException(UUID id) {
        super("Order " + id + " not found");
    }
}
