package dev.relay.sandbox.orders.order;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import dev.relay.sandbox.orders.clients.DependencyException;
import dev.relay.sandbox.orders.clients.InventoryClient;
import dev.relay.sandbox.orders.clients.InventoryClient.Reservation;
import dev.relay.sandbox.orders.clients.PaymentsClient;
import dev.relay.sandbox.orders.clients.PaymentsClient.PaymentResult;
import dev.relay.sandbox.orders.pricing.DiscountService;

/**
 * Places orders: reserve stock, price, charge, persist. Remote calls happen
 * before the database write so no connection is held while waiting on them.
 */
@Service
public class OrderService {

    private static final Logger log = LoggerFactory.getLogger(OrderService.class);

    private final InventoryClient inventory;
    private final PaymentsClient payments;
    private final DiscountService discounts;
    private final OrderRepository repository;

    OrderService(InventoryClient inventory, PaymentsClient payments, DiscountService discounts, OrderRepository repository) {
        this.inventory = inventory;
        this.payments = payments;
        this.discounts = discounts;
        this.repository = repository;
    }

    public Order place(PlaceOrder command) {
        Reservation reservation = inventory.reserve(command.sku(), command.quantity());
        long subtotal = reservation.unitPriceCents() * command.quantity();
        long total = discounts.apply(command.discountCode(), subtotal);
        UUID orderId = UUID.randomUUID();

        PaymentResult payment;
        try {
            payment = payments.charge(orderId, total, "USD", command.customerId());
        } catch (DependencyException e) {
            releaseQuietly(command);
            throw e;
        }
        if (!payment.approved()) {
            releaseQuietly(command);
        }

        Order order = new Order(orderId, command.customerId(), command.sku(), command.quantity(), total,
                payment.approved() ? OrderStatus.CONFIRMED : OrderStatus.DECLINED,
                payment.paymentId(), command.discountCode(), Instant.now());
        repository.insert(order);
        return order;
    }

    public Optional<Order> find(UUID id) {
        return repository.findById(id);
    }

    public List<Order> recent(String customerId, int limit) {
        return customerId == null ? repository.findRecent(limit) : repository.findByCustomer(customerId, limit);
    }

    private void releaseQuietly(PlaceOrder command) {
        try {
            inventory.release(command.sku(), command.quantity());
        } catch (DependencyException e) {
            log.warn("Could not release reservation for {} x{}: {}", command.sku(), command.quantity(), e.getMessage());
        }
    }

    public record PlaceOrder(String customerId, String sku, int quantity, String discountCode) {
    }
}
