package dev.relay.alert;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Clock;
import java.time.Duration;
import java.util.HexFormat;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * Stripe-style webhook signatures: {@code X-Relay-Signature: t=<unix seconds>,v1=<hex>}
 * where v1 = HMAC-SHA256(secret, t + "." + body). Signing the timestamp bounds
 * replay to {@link #TOLERANCE}; comparison is constant-time.
 */
public final class HmacVerifier {

    static final Duration TOLERANCE = Duration.ofMinutes(5);

    private final byte[] secret;
    private final Clock clock;

    public HmacVerifier(String secret, Clock clock) {
        if (secret == null || secret.isBlank()) {
            throw new IllegalArgumentException("webhook secret must be set");
        }
        this.secret = secret.getBytes(StandardCharsets.UTF_8);
        this.clock = clock;
    }

    public boolean verify(String header, byte[] body) {
        if (header == null) {
            return false;
        }
        Long timestamp = null;
        String signature = null;
        for (String part : header.split(",")) {
            String[] kv = part.trim().split("=", 2);
            if (kv.length != 2) {
                continue;
            }
            switch (kv[0]) {
                case "t" -> timestamp = parseLong(kv[1]);
                case "v1" -> signature = kv[1];
                default -> { }
            }
        }
        if (timestamp == null || signature == null) {
            return false;
        }
        long skew = Math.abs(clock.instant().getEpochSecond() - timestamp);
        if (skew > TOLERANCE.toSeconds()) {
            return false;
        }
        byte[] expected = mac(secret, timestamp, body);
        byte[] provided;
        try {
            provided = HexFormat.of().parseHex(signature);
        } catch (IllegalArgumentException e) {
            return false;
        }
        return MessageDigest.isEqual(expected, provided);
    }

    /** The header value a sender would compute; used by tests and by the eval runner's counterpart. */
    public static String sign(String secret, long timestamp, byte[] body) {
        return "t=" + timestamp + ",v1=" + HexFormat.of().formatHex(mac(secret.getBytes(StandardCharsets.UTF_8), timestamp, body));
    }

    private static byte[] mac(byte[] secret, long timestamp, byte[] body) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret, "HmacSHA256"));
            mac.update((timestamp + ".").getBytes(StandardCharsets.UTF_8));
            return mac.doFinal(body);
        } catch (java.security.GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }

    private static Long parseLong(String value) {
        try {
            return Long.parseLong(value);
        } catch (NumberFormatException e) {
            return null;
        }
    }
}
