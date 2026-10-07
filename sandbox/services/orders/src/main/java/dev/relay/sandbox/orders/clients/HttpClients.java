package dev.relay.sandbox.orders.clients;

import java.net.http.HttpClient;
import java.time.Duration;

import org.springframework.http.client.ClientHttpRequestFactory;
import org.springframework.http.client.JdkClientHttpRequestFactory;

final class HttpClients {

    private static final Duration CONNECT_TIMEOUT = Duration.ofMillis(500);

    private HttpClients() {
    }

    /** A request factory with a hard read timeout — every remote call must be bounded. */
    static ClientHttpRequestFactory withReadTimeout(Duration readTimeout) {
        HttpClient client = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(CONNECT_TIMEOUT)
                .build();
        JdkClientHttpRequestFactory factory = new JdkClientHttpRequestFactory(client);
        factory.setReadTimeout(readTimeout);
        return factory;
    }
}
