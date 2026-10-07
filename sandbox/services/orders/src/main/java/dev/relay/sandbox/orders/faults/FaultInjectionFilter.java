package dev.relay.sandbox.orders.faults;

import java.io.IOException;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.Arrays;
import java.util.Queue;
import java.util.concurrent.ConcurrentLinkedQueue;

import javax.sql.DataSource;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Injects request-path faults into /orders, driven by {@link FaultFlags}:
 *
 * <pre>
 * db-connection-leak  {"ratio": 0.2}            borrow a pooled connection and never return it
 * memory-leak         {"kb_per_request": 384}   retain a buffer per request; heap grows until OOM
 * thread-stall        {"ratio": 0.3, "stall_ms": 15000}  serialize requests on a lock; Tomcat threads pile up
 * </pre>
 *
 * Clearing a flag undoes its damage on the next reconcile tick (leaked connections are
 * closed, retained memory released), so the sandbox recovers without a restart.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
class FaultInjectionFilter extends OncePerRequestFilter {

    private static final Queue<Connection> LEAKED = new ConcurrentLinkedQueue<>();
    private static final Queue<byte[]> RETAINED = new ConcurrentLinkedQueue<>();
    private static final Object STALL_LOCK = new Object();

    private final FaultFlags flags;
    private final DataSource dataSource;

    FaultInjectionFilter(FaultFlags flags, DataSource dataSource) {
        this.flags = flags;
        this.dataSource = dataSource;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return !request.getRequestURI().startsWith("/orders");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        flags.hit("db-connection-leak").ifPresent(p -> leakConnection());
        flags.active("memory-leak").ifPresent(p -> retain(p.integer("kb_per_request", 384)));
        flags.hit("thread-stall").ifPresent(p -> stall(p.integer("stall_ms", 15_000)));
        chain.doFilter(request, response);
    }

    private void leakConnection() {
        try {
            LEAKED.add(dataSource.getConnection());
        } catch (SQLException e) {
            // Pool already exhausted; the request fails on its own a moment later.
        }
    }

    private static void retain(int kb) {
        byte[] block = new byte[kb * 1024];
        Arrays.fill(block, (byte) 1);
        RETAINED.add(block);
    }

    private static void stall(int millis) {
        synchronized (STALL_LOCK) {
            try {
                Thread.sleep(millis);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
    }

    @Scheduled(fixedDelay = 2000, initialDelay = 2000)
    void reconcile() {
        if (flags.active("db-connection-leak").isEmpty()) {
            Connection c;
            while ((c = LEAKED.poll()) != null) {
                try {
                    c.close();
                } catch (SQLException ignored) {
                    // returning to the pool is best effort
                }
            }
        }
        if (flags.active("memory-leak").isEmpty()) {
            RETAINED.clear();
        }
    }
}
