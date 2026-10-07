package dev.relay.alert;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;

import org.junit.jupiter.api.Test;

class HmacVerifierTest {

    private static final Instant NOW = Instant.parse("2026-10-04T09:00:00Z");
    private static final byte[] BODY = "{\"name\":\"HighErrorRate\"}".getBytes(StandardCharsets.UTF_8);
    private final HmacVerifier verifier = new HmacVerifier("s3cret", Clock.fixed(NOW, ZoneOffset.UTC));

    @Test
    void acceptsAFreshCorrectSignature() {
        assertThat(verifier.verify(HmacVerifier.sign("s3cret", NOW.getEpochSecond() - 30, BODY), BODY)).isTrue();
    }

    @Test
    void rejectsWrongSecretTamperedBodyAndReplays() {
        long t = NOW.getEpochSecond();
        assertThat(verifier.verify(HmacVerifier.sign("other", t, BODY), BODY)).isFalse();
        assertThat(verifier.verify(HmacVerifier.sign("s3cret", t, BODY), "{}".getBytes(StandardCharsets.UTF_8))).isFalse();
        assertThat(verifier.verify(HmacVerifier.sign("s3cret", t - 3600, BODY), BODY)).isFalse();
    }

    @Test
    void rejectsMalformedHeaders() {
        assertThat(verifier.verify(null, BODY)).isFalse();
        assertThat(verifier.verify("garbage", BODY)).isFalse();
        assertThat(verifier.verify("t=abc,v1=zz", BODY)).isFalse();
        assertThat(verifier.verify("t=" + NOW.getEpochSecond() + ",v1=not-hex", BODY)).isFalse();
    }
}
