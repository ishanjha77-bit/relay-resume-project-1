package dev.relay.sandbox.inventory.stock;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import dev.relay.sandbox.inventory.InventoryProperties;

/**
 * Reserves and releases stock. Reservation is a single conditional UPDATE, so
 * concurrent orders can't oversell; it takes a row lock on the SKU until commit.
 */
@Service
public class StockService {

    private static final Logger log = LoggerFactory.getLogger(StockService.class);

    private final JdbcClient jdbc;
    private final InventoryProperties properties;

    StockService(JdbcClient jdbc, InventoryProperties properties) {
        this.jdbc = jdbc;
        this.properties = properties;
    }

    @Transactional
    public Reservation reserve(String sku, int quantity) {
        StockRow row = jdbc.sql("""
                UPDATE products SET stock = stock - :quantity, updated_at = now()
                WHERE sku = :sku AND stock >= :quantity
                RETURNING price_cents, stock
                """)
                .param("sku", sku)
                .param("quantity", quantity)
                .query((rs, n) -> new StockRow(rs.getLong("price_cents"), rs.getInt("stock")))
                .optional()
                .orElseThrow(() -> exists(sku) ? new OutOfStockException(sku, quantity) : new UnknownSkuException(sku));

        recordMovement(sku, -quantity, "reserve");

        int remaining = row.stock();
        if (remaining < properties.restockBelow()) {
            jdbc.sql("UPDATE products SET stock = stock + :amount WHERE sku = :sku")
                    .param("amount", properties.restockAmount())
                    .param("sku", sku)
                    .update();
            recordMovement(sku, properties.restockAmount(), "restock");
            remaining += properties.restockAmount();
            log.info("Restocked {} with {} units", sku, properties.restockAmount());
        }
        return new Reservation(sku, quantity, row.priceCents(), remaining);
    }

    @Transactional
    public void release(String sku, int quantity) {
        int updated = jdbc.sql("UPDATE products SET stock = stock + :quantity, updated_at = now() WHERE sku = :sku")
                .param("quantity", quantity)
                .param("sku", sku)
                .update();
        if (updated == 0) {
            throw new UnknownSkuException(sku);
        }
        recordMovement(sku, quantity, "release");
    }

    private boolean exists(String sku) {
        return jdbc.sql("SELECT EXISTS (SELECT 1 FROM products WHERE sku = :sku)")
                .param("sku", sku)
                .query(Boolean.class)
                .single();
    }

    private void recordMovement(String sku, int delta, String reason) {
        jdbc.sql("INSERT INTO stock_movements (sku, delta, reason) VALUES (:sku, :delta, :reason)")
                .param("sku", sku)
                .param("delta", delta)
                .param("reason", reason)
                .update();
    }

    private record StockRow(long priceCents, int stock) {
    }

    public record Reservation(String sku, int quantity, long unitPriceCents, int remainingStock) {
    }
}
