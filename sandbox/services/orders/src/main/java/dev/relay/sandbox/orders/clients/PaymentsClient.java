package dev.relay.sandbox.orders.clients;

import java.time.Duration;
import java.util.UUID;

import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import dev.relay.sandbox.orders.SandboxProperties;

@Component
public class PaymentsClient {

    private final RestClient http;
    private final Duration readTimeout;

    PaymentsClient(RestClient.Builder builder, SandboxProperties properties) {
        this.readTimeout = properties.payments().timeout();
        this.http = builder
                .baseUrl(properties.payments().url().toString())
                .requestFactory(HttpClients.withReadTimeout(properties.payments().timeout()))
                .build();
    }

    public PaymentResult charge(UUID orderId, long amountCents, String currency, String customerId) {
        try {
            ResponseEntity<ChargeResponse> response = http.post()
                    .uri("/payments/charge")
                    .body(new ChargeRequest(orderId.toString(), amountCents, currency, customerId))
                    .retrieve()
                    // A decline is a business outcome, not a failure of the dependency.
                    .onStatus(status -> status.value() == 402, (request, resp) -> {
                    })
                    .toEntity(ChargeResponse.class);
            if (response.getStatusCode().value() == 402 || response.getBody() == null) {
                return new PaymentResult(false, null);
            }
            return new PaymentResult(true, response.getBody().paymentId());
        } catch (RestClientException e) {
            throw new DependencyException("payments", "POST /payments/charge", readTimeout, e);
        }
    }

    record ChargeRequest(String orderId, long amountCents, String currency, String customerId) {
    }

    record ChargeResponse(String paymentId, String status, String reason) {
    }

    public record PaymentResult(boolean approved, String paymentId) {
    }
}
