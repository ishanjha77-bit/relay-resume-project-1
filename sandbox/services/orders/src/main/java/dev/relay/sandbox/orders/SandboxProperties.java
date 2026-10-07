package dev.relay.sandbox.orders;

import java.net.URI;
import java.time.Duration;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "sandbox")
public record SandboxProperties(Upstream inventory, Upstream payments, Duration slowQueryThreshold) {

    public record Upstream(URI url, Duration timeout) {
    }
}
