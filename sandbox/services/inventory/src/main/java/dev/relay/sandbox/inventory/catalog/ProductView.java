package dev.relay.sandbox.inventory.catalog;

import java.io.Serializable;

public record ProductView(String sku, String name, String category, long priceCents, int stock, long sold7d)
        implements Serializable {
}
