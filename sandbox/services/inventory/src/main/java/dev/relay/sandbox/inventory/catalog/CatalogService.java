package dev.relay.sandbox.inventory.catalog;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.List;

import org.springframework.cache.annotation.Cacheable;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;

import dev.relay.sandbox.inventory.stock.UnknownSkuException;

/**
 * Product catalog with weekly sales figures. The aggregate over stock_movements
 * is the expensive part (~0.5 s); Redis caches it for 30 s. {@code sync = true}
 * makes concurrent misses wait for one computation instead of stampeding the
 * database every time an entry expires.
 */
@Service
public class CatalogService {

    private final JdbcClient jdbc;

    CatalogService(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    @Cacheable(cacheNames = "catalog", key = "'top100'", sync = true)
    public List<ProductView> topProducts() {
        // ArrayList, not toList(): cached values must be Serializable.
        return new ArrayList<>(jdbc.sql("""
                SELECT p.sku, p.name, p.category, p.price_cents, p.stock,
                       COALESCE(SUM(-m.delta), 0) AS sold_7d
                FROM products p
                LEFT JOIN stock_movements m
                       ON m.sku = p.sku
                      AND m.reason = 'reserve'
                      AND m.created_at > now() - interval '7 days'
                GROUP BY p.sku
                ORDER BY sold_7d DESC, p.sku
                LIMIT 100
                """)
                .query(CatalogService::map)
                .list());
    }

    @Cacheable(cacheNames = "product", key = "#sku", sync = true)
    public ProductView product(String sku) {
        return jdbc.sql("""
                SELECT p.sku, p.name, p.category, p.price_cents, p.stock,
                       (SELECT COALESCE(SUM(-m.delta), 0) FROM stock_movements m
                         WHERE m.sku = p.sku AND m.reason = 'reserve'
                           AND m.created_at > now() - interval '7 days') AS sold_7d
                FROM products p WHERE p.sku = :sku
                """)
                .param("sku", sku)
                .query(CatalogService::map)
                .optional()
                .orElseThrow(() -> new UnknownSkuException(sku));
    }

    private static ProductView map(ResultSet rs, int row) throws SQLException {
        return new ProductView(rs.getString("sku"), rs.getString("name"), rs.getString("category"),
                rs.getLong("price_cents"), rs.getInt("stock"), rs.getLong("sold_7d"));
    }
}
