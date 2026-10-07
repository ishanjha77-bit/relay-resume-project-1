package dev.relay.alert;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Clock;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;

import jakarta.validation.constraints.NotBlank;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import dev.relay.RelayProperties;
import dev.relay.incident.AlertBatch;
import dev.relay.incident.AlertItem;
import dev.relay.incident.IncidentService;
import dev.relay.incident.IncidentService.Intake;
import dev.relay.incident.Severity;
import tools.jackson.databind.json.JsonMapper;

/**
 * Alert intake (docs/adr/0006): Alertmanager authenticates with a bearer token
 * (it can't sign bodies); other sources sign the body with HMAC.
 */
@RestController
@RequestMapping("/api/alerts")
class AlertController {

    private static final Logger log = LoggerFactory.getLogger(AlertController.class);
    private static final Instant NEVER = Instant.parse("0001-01-01T00:00:00Z");

    private final IncidentService incidents;
    private final JsonMapper json;
    private final HmacVerifier hmac;
    private final byte[] alertmanagerToken;

    AlertController(IncidentService incidents, JsonMapper json, RelayProperties properties, Clock clock) {
        this.incidents = incidents;
        this.json = json;
        this.hmac = new HmacVerifier(properties.security().webhookSecret(), clock);
        this.alertmanagerToken = ("Bearer " + properties.security().alertmanagerToken()).getBytes(StandardCharsets.UTF_8);
    }

    record SignedAlert(@NotBlank String name, String service, String severity, String summary, String namespace,
            String fingerprint, String status, Instant startsAt, Map<String, String> labels) {
    }

    @PostMapping("/alertmanager")
    ResponseEntity<Intake> alertmanager(@RequestHeader(value = HttpHeaders.AUTHORIZATION, required = false) String auth,
            @RequestBody byte[] body) {
        if (auth == null || !MessageDigest.isEqual(auth.getBytes(StandardCharsets.UTF_8), alertmanagerToken)) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
        }
        AlertmanagerWebhook webhook = json.readValue(body, AlertmanagerWebhook.class);
        String namespace = firstNonNull(webhook.commonLabels(), webhook.groupLabels(), "namespace");
        List<AlertItem> alerts = webhook.alerts().stream().map(a -> new AlertItem(
                a.fingerprint(),
                a.labels().getOrDefault("alertname", "unknown"),
                a.labels().get("service"),
                Severity.parse(a.labels().get("severity")),
                "firing".equals(a.status()),
                a.annotations() == null ? null : a.annotations().get("summary"),
                a.startsAt(),
                a.endsAt() == null || !a.endsAt().isAfter(NEVER) ? null : a.endsAt(),
                a.labels())).toList();
        Intake intake = incidents.ingest(new AlertBatch("alertmanager", "alertmanager:" + webhook.groupKey(),
                namespace == null ? "unknown" : namespace, alerts));
        log.info("Alertmanager notification: {} alerts -> incident {} (opened: {})", alerts.size(),
                intake.number(), intake.opened());
        return ResponseEntity.accepted().body(intake);
    }

    @PostMapping
    ResponseEntity<Intake> signed(@RequestHeader(value = "X-Relay-Signature", required = false) String signature,
            @RequestBody byte[] body) {
        if (!hmac.verify(signature, body)) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
        }
        SignedAlert alert = json.readValue(body, SignedAlert.class);
        if (alert.name() == null || alert.name().isBlank()) {
            throw new IllegalArgumentException("name is required");
        }
        String namespace = alert.namespace() == null ? "default" : alert.namespace();
        String fingerprint = alert.fingerprint() != null ? alert.fingerprint()
                : sha256(alert.name() + "|" + alert.service() + "|" + namespace);
        AlertItem item = new AlertItem(fingerprint, alert.name(), alert.service(), Severity.parse(alert.severity()),
                !"resolved".equals(alert.status()), alert.summary(), alert.startsAt(), null,
                alert.labels() == null ? Map.of() : alert.labels());
        Intake intake = incidents.ingest(new AlertBatch("webhook", "webhook:" + namespace, namespace, List.of(item)));
        return ResponseEntity.accepted().body(intake);
    }

    private static String firstNonNull(Map<String, String> a, Map<String, String> b, String key) {
        if (a != null && a.get(key) != null) {
            return a.get(key);
        }
        return b == null ? null : b.get(key);
    }

    private static String sha256(String value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8))).substring(0, 16);
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
