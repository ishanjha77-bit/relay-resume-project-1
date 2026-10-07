package dev.relay.sandbox.orders.api;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import dev.relay.sandbox.orders.order.Order;
import dev.relay.sandbox.orders.order.OrderService;
import dev.relay.sandbox.orders.order.OrderService.PlaceOrder;
import dev.relay.sandbox.orders.order.OrderStatus;

@RestController
@RequestMapping("/orders")
class OrderController {

    private final OrderService orders;

    OrderController(OrderService orders) {
        this.orders = orders;
    }

    @PostMapping
    ResponseEntity<OrderView> place(@Valid @RequestBody CreateOrderRequest request) {
        Order order = orders.place(new PlaceOrder(
                request.customerId(), request.sku(), request.quantity(), request.discountCode()));
        HttpStatus status = order.status() == OrderStatus.CONFIRMED ? HttpStatus.CREATED : HttpStatus.PAYMENT_REQUIRED;
        return ResponseEntity.status(status).body(OrderView.from(order));
    }

    @GetMapping("/{id}")
    OrderView get(@PathVariable UUID id) {
        return orders.find(id).map(OrderView::from).orElseThrow(() -> new OrderNotFoundException(id));
    }

    @GetMapping
    List<OrderView> list(@RequestParam(required = false) String customerId,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int limit) {
        return orders.recent(customerId, limit).stream().map(OrderView::from).toList();
    }

    record CreateOrderRequest(
            @NotBlank String customerId,
            @NotBlank String sku,
            @Min(1) @Max(10) int quantity,
            @Size(max = 64) String discountCode) {
    }

    record OrderView(UUID id, String customerId, String sku, int quantity, long totalCents,
            OrderStatus status, String paymentId, Instant createdAt) {

        static OrderView from(Order o) {
            return new OrderView(o.id(), o.customerId(), o.sku(), o.quantity(), o.totalCents(),
                    o.status(), o.paymentId(), o.createdAt());
        }
    }
}
