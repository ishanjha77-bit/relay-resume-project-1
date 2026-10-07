package dev.relay.sandbox.orders.clients;

public class OutOfStockException extends RuntimeException {

    public OutOfStockException(String sku) {
        super("SKU " + sku + " is out of stock");
    }
}
