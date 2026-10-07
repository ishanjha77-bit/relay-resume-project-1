package dev.relay.sandbox.orders.clients;

import java.time.Duration;

import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import dev.relay.sandbox.orders.SandboxProperties;

@Component
public class InventoryClient {

    private final RestClient http;
    private final Duration readTimeout;

    InventoryClient(RestClient.Builder builder, SandboxProperties properties) {
        this.readTimeout = properties.inventory().timeout();
        this.http = builder
                .baseUrl(properties.inventory().url().toString())
                .requestFactory(HttpClients.withReadTimeout(properties.inventory().timeout()))
                .build();
    }

    public Reservation reserve(String sku, int quantity) {
        try {
            return http.post()
                    .uri("/inventory/reserve")
                    .body(new StockChange(sku, quantity))
                    .retrieve()
                    .onStatus(status -> status.value() == 409, (request, response) -> {
                        throw new OutOfStockException(sku);
                    })
                    .body(Reservation.class);
        } catch (RestClientException e) {
            throw new DependencyException("inventory", "POST /inventory/reserve", readTimeout, e);
        }
    }

    public void release(String sku, int quantity) {
        try {
            http.post()
                    .uri("/inventory/release")
                    .body(new StockChange(sku, quantity))
                    .retrieve()
                    .toBodilessEntity();
        } catch (RestClientException e) {
            throw new DependencyException("inventory", "POST /inventory/release", readTimeout, e);
        }
    }

    record StockChange(String sku, int quantity) {
    }

    public record Reservation(String sku, int quantity, long unitPriceCents, int remainingStock) {
    }
}
