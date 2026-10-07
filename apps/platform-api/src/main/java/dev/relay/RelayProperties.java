package dev.relay;

import java.time.Duration;
import java.util.List;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "relay")
public record RelayProperties(Security security, Streams streams) {

    /**
     * @param jwtPrivateKey     PKCS#8 PEM used to sign access tokens; blank = ephemeral key
     * @param alertmanagerToken bearer token Alertmanager presents on its webhook
     * @param webhookSecret     HMAC secret for signed alert sources (eval runner, GitHub)
     * @param demoPassword      password of the seeded demo users (local demo only)
     * @param approvalTtl       how long an approval token stays valid for the action it approves
     */
    public record Security(
            String issuer,
            Duration tokenTtl,
            String jwtPrivateKey,
            String alertmanagerToken,
            String webhookSecret,
            String demoPassword,
            List<String> allowedOrigins,
            Duration approvalTtl) {
    }

    public record Streams(String incidents, String agentEvents, String approvals, String resolved,
            String consumerGroup) {
    }
}
