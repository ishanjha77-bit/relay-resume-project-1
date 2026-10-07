package dev.relay.sandbox.inventory.stock;

import java.util.List;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

import dev.relay.sandbox.inventory.InventoryProperties;
import dev.relay.sandbox.inventory.faults.FaultFlags;

/**
 * Recounts stock for the hottest SKUs from their movement history. It is gated by
 * the {@code stock-reconciliation} runtime flag and locks every SKU it recounts for
 * the whole pass — while it runs, reservations for those SKUs wait on row locks.
 */
@Component
class StockReconciliationJob {

    private static final Logger log = LoggerFactory.getLogger(StockReconciliationJob.class);

    private final FaultFlags flags;
    private final JdbcClient jdbc;
    private final TransactionTemplate tx;
    private final List<String> hotSkus;

    StockReconciliationJob(FaultFlags flags, JdbcClient jdbc, TransactionTemplate tx, InventoryProperties properties) {
        this.flags = flags;
        this.jdbc = jdbc;
        this.tx = tx;
        this.hotSkus = properties.hotSkuIds();
    }

    @Scheduled(fixedDelay = 250, initialDelay = 5000)
    void run() {
        flags.active("stock-reconciliation").ifPresent(p -> reconcile(p.integer("hold_ms", 3000)));
    }

    private void reconcile(int passMillis) {
        long start = System.nanoTime();
        double pausePerSku = passMillis / 1000.0 / hotSkus.size();
        Integer recounted = tx.execute(status -> {
            List<String> locked = jdbc.sql("SELECT sku FROM products WHERE sku IN (:skus) ORDER BY sku FOR UPDATE")
                    .param("skus", hotSkus)
                    .query(String.class)
                    .list();
            for (String sku : locked) {
                jdbc.sql("""
                        SELECT COALESCE(SUM(delta), 0) FROM stock_movements, pg_sleep(:pause)
                        WHERE sku = :sku AND created_at > now() - interval '30 days'
                        """)
                        .param("pause", pausePerSku)
                        .param("sku", sku)
                        .query(Long.class)
                        .single();
            }
            return locked.size();
        });
        log.info("Stock reconciliation pass finished: {} SKUs recounted in {} ms",
                recounted, (System.nanoTime() - start) / 1_000_000);
    }
}
