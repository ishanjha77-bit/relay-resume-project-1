package dev.relay.sandbox.orders.order;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Duration;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import java.util.function.Supplier;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

import dev.relay.sandbox.orders.SandboxProperties;

@Repository
public class OrderRepository {

    private static final Logger log = LoggerFactory.getLogger(OrderRepository.class);

    private static final String COLUMNS =
            "id, customer_id, sku, quantity, total_cents, status, payment_id, discount_code, created_at";

    private final JdbcClient jdbc;
    private final Duration slowQueryThreshold;

    OrderRepository(JdbcClient jdbc, SandboxProperties properties) {
        this.jdbc = jdbc;
        this.slowQueryThreshold = properties.slowQueryThreshold();
    }

    public void insert(Order order) {
        timed("INSERT INTO orders (...) VALUES (...)", () -> jdbc.sql("""
                INSERT INTO orders (id, customer_id, sku, quantity, total_cents, status, payment_id, discount_code, created_at)
                VALUES (:id, :customerId, :sku, :quantity, :totalCents, :status, :paymentId, :discountCode, :createdAt)
                """)
                .param("id", order.id())
                .param("customerId", order.customerId())
                .param("sku", order.sku())
                .param("quantity", order.quantity())
                .param("totalCents", order.totalCents())
                .param("status", order.status().name())
                .param("paymentId", order.paymentId())
                .param("discountCode", order.discountCode())
                .param("createdAt", Timestamp.from(order.createdAt()))
                .update());
    }

    public Optional<Order> findById(UUID id) {
        return timed("SELECT … FROM orders WHERE id = ?", () -> jdbc
                .sql("SELECT " + COLUMNS + " FROM orders WHERE id = :id")
                .param("id", id)
                .query(OrderRepository::map)
                .optional());
    }

    public List<Order> findByCustomer(String customerId, int limit) {
        return timed("SELECT … FROM orders WHERE customer_id = ? ORDER BY created_at DESC LIMIT ?", () -> jdbc
                .sql("SELECT " + COLUMNS + " FROM orders WHERE customer_id = :customerId ORDER BY created_at DESC LIMIT :limit")
                .param("customerId", customerId)
                .param("limit", limit)
                .query(OrderRepository::map)
                .list());
    }

    public List<Order> findRecent(int limit) {
        return timed("SELECT … FROM orders ORDER BY created_at DESC LIMIT ?", () -> jdbc
                .sql("SELECT " + COLUMNS + " FROM orders ORDER BY created_at DESC LIMIT :limit")
                .param("limit", limit)
                .query(OrderRepository::map)
                .list());
    }

    /** Logs statements slower than the threshold — the app's slow-query log. */
    private <T> T timed(String statement, Supplier<T> query) {
        long start = System.nanoTime();
        try {
            return query.get();
        } finally {
            Duration took = Duration.ofNanos(System.nanoTime() - start);
            if (took.compareTo(slowQueryThreshold) > 0) {
                log.warn("Slow query ({} ms): {}", took.toMillis(), statement);
            }
        }
    }

    private static Order map(ResultSet rs, int row) throws SQLException {
        return new Order(
                rs.getObject("id", UUID.class),
                rs.getString("customer_id"),
                rs.getString("sku"),
                rs.getInt("quantity"),
                rs.getLong("total_cents"),
                OrderStatus.valueOf(rs.getString("status")),
                rs.getString("payment_id"),
                rs.getString("discount_code"),
                rs.getTimestamp("created_at").toInstant());
    }
}
