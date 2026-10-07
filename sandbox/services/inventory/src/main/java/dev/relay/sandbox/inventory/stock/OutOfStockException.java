package dev.relay.sandbox.inventory.stock;

public class OutOfStockException extends RuntimeException {

    public OutOfStockException(String sku, int requested) {
        super("Not enough stock for " + sku + " (requested " + requested + ")");
    }
}
