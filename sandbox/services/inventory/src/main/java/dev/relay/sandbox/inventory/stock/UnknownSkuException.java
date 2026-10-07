package dev.relay.sandbox.inventory.stock;

public class UnknownSkuException extends RuntimeException {

    public UnknownSkuException(String sku) {
        super("Unknown SKU " + sku);
    }
}
