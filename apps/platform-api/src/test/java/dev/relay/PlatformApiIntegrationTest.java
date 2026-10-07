package dev.relay;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.IOException;
import java.lang.reflect.Type;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.Callable;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.data.domain.Range;
import org.springframework.data.redis.connection.stream.MapRecord;
import org.springframework.data.redis.connection.stream.StreamRecords;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.messaging.converter.JacksonJsonMessageConverter;
import org.springframework.messaging.simp.stomp.StompFrameHandler;
import org.springframework.messaging.simp.stomp.StompHeaders;
import org.springframework.messaging.simp.stomp.StompSession;
import org.springframework.messaging.simp.stomp.StompSessionHandlerAdapter;
import org.springframework.web.socket.WebSocketHttpHeaders;
import org.springframework.web.socket.client.standard.StandardWebSocketClient;
import org.springframework.web.socket.messaging.WebSocketStompClient;

import com.nimbusds.jose.crypto.RSASSAVerifier;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import com.networknt.schema.Error;
import com.networknt.schema.InputFormat;
import com.networknt.schema.Schema;
import com.networknt.schema.SchemaRegistry;
import com.networknt.schema.SpecificationVersion;

import dev.relay.alert.HmacVerifier;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.json.JsonMapper;

/** End to end through real Postgres and Redis (Testcontainers) and a real HTTP/WebSocket server. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@Import(TestcontainersConfiguration.class)
class PlatformApiIntegrationTest {

    private static final Path CONTRACTS = Path.of("../../contracts");
    /** The incident the contract examples refer to; tests swap in a real one. */
    private static final String EXAMPLE_INCIDENT = "4f1c2a9e-7b3d-4e8a-9c1f-2d6b8e0a5c31";
    private static final String EXAMPLE_APPROVAL = "9b2e4c1a-5d6f-4a7b-8c9d-0e1f2a3b4c5d";
    private static final String AM_TOKEN = "Bearer dev-alertmanager-token";
    private static final String SECRET = "dev-webhook-secret";
    private static final TypeReference<Map<String, Object>> MAP = new TypeReference<>() {
    };

    @Value("${local.server.port}")
    int port;

    @Autowired
    StringRedisTemplate redis;

    @Autowired
    JsonMapper json;

    @Autowired
    JdbcClient jdbc;

    private final HttpClient http = HttpClient.newHttpClient();

    // --- alert intake ---------------------------------------------------------------------------

    @Test
    void alertmanagerWebhookRequiresItsToken() throws Exception {
        assertThat(post("/api/alerts/alertmanager", alertmanagerPayload("g-auth", "firing"), null).statusCode())
                .isEqualTo(401);
        assertThat(post("/api/alerts/alertmanager", alertmanagerPayload("g-auth", "firing"), "Bearer nope")
                .statusCode()).isEqualTo(401);
    }

    @Test
    void oneIncidentPerAlertGroupAndAContractValidEvent() throws Exception {
        String group = "g-" + UUID.randomUUID();
        Map<String, Object> first = body(post("/api/alerts/alertmanager", alertmanagerPayload(group, "firing"), AM_TOKEN));
        Map<String, Object> again = body(post("/api/alerts/alertmanager", alertmanagerPayload(group, "firing"), AM_TOKEN));

        assertThat(first.get("opened")).isEqualTo(true);
        assertThat(again.get("opened")).isEqualTo(false);
        assertThat(again.get("incident_id")).isEqualTo(first.get("incident_id"));

        String incidentId = first.get("incident_id").toString();
        String event = eventually(() -> redis.opsForStream().range("relay.incidents",
                org.springframework.data.domain.Range.unbounded()).stream()
                .map(r -> r.getValue().get("event").toString())
                .filter(e -> e.contains(incidentId))
                .findFirst().orElse(null));
        assertThat(validate("incident-opened", event)).isEmpty();
        assertThat(validate("incident-opened", Files.readString(CONTRACTS.resolve("examples/incident-opened.json"))))
                .isEmpty();
        // The outbox stores events as jsonb, which normalizes spacing and key order: compare parsed values.
        Map<?, ?> opened = (Map<?, ?>) json.readValue(event, MAP).get("incident");
        assertThat(opened.get("title")).isEqualTo("HighErrorRate on orders (+1 more)");
        assertThat((List<?>) opened.get("alerts")).hasSize(2);

        String token = login("vic");
        Map<String, Object> detail = body(get("/api/incidents/" + incidentId, token));
        assertThat((List<?>) detail.get("alerts")).hasSize(2);
        assertThat(kinds(detail)).containsExactly("incident.opened", "alert.firing", "alert.firing");

        post("/api/alerts/alertmanager", alertmanagerPayload(group, "resolved"), AM_TOKEN);
        Map<String, Object> afterResolve = body(get("/api/incidents/" + incidentId, token));
        assertThat(kinds(afterResolve)).contains("alert.resolved");
        @SuppressWarnings("unchecked")
        Map<String, Object> incident = (Map<String, Object>) afterResolve.get("incident");
        assertThat(incident.get("status")).isEqualTo("OPEN"); // symptoms clearing doesn't close an incident
    }

    @Test
    void signedWebhooksAreVerified() throws Exception {
        byte[] alert = signedAlert("ns-" + UUID.randomUUID());
        long now = Instant.now().getEpochSecond();

        assertThat(postSigned(alert, HmacVerifier.sign(SECRET, now, alert)).statusCode()).isEqualTo(202);
        assertThat(postSigned(alert, HmacVerifier.sign("wrong-secret", now, alert)).statusCode()).isEqualTo(401);
        assertThat(postSigned(alert, HmacVerifier.sign(SECRET, now - 3600, alert)).statusCode()).isEqualTo(401);
        byte[] tampered = new String(alert, StandardCharsets.UTF_8).replace("orders", "gateway").getBytes(StandardCharsets.UTF_8);
        assertThat(postSigned(tampered, HmacVerifier.sign(SECRET, now, alert)).statusCode()).isEqualTo(401);
    }

    // --- agent events -----------------------------------------------------------------------------

    @Test
    void agentEventsAreStoredOnceAndDriveTheIncident() throws Exception {
        String incidentId = openIncident();
        List<String> events = Files.readAllLines(CONTRACTS.resolve("examples/agent-events.jsonl")).stream()
                .filter(line -> !line.isBlank())
                .map(line -> line.replace(EXAMPLE_INCIDENT, incidentId))
                .toList();
        for (String event : events) {
            assertThat(validate("agent-event", event)).isEmpty();
            publish(event);
        }
        publish(events.get(3)); // a redelivered tool.called event must not create a second step

        String token = login("vic");
        List<Map<String, Object>> steps = eventually(() -> {
            List<Map<String, Object>> current = list(get("/api/incidents/" + incidentId + "/steps", token));
            return current.size() >= 7 ? current : null;
        });
        assertThat(steps).hasSize(7);
        assertThat(steps).extracting(s -> s.get("seq")).containsExactly(0, 1, 2, 3, 4, 5, 6);
        assertThat(steps.get(3)).containsEntry("tool", "logs__error_summary").containsEntry("evidence_id", "E1");

        Map<String, Object> detail = eventually(() -> {
            Map<String, Object> d = body(get("/api/incidents/" + incidentId, token));
            return "DIAGNOSED".equals(((Map<?, ?>) d.get("incident")).get("status")) ? d : null;
        });
        Map<?, ?> incident = (Map<?, ?>) detail.get("incident");
        assertThat(incident.get("root_cause_category")).isEqualTo("db_pool_exhaustion");
        assertThat(incident.get("root_cause_service")).isEqualTo("orders");
        assertThat(((Number) incident.get("cost_usd")).doubleValue()).isEqualTo(0.0636);
        List<?> hypotheses = (List<?>) detail.get("hypotheses");
        assertThat(((Map<?, ?>) hypotheses.getFirst()).get("verdict")).isEqualTo("verified");
        assertThat(kinds(detail)).contains("agent.started", "agent.diagnosed");
    }

    @Test
    void aRunThatFailsBeforeStartingStillFailsTheIncident() throws Exception {
        String incidentId = openIncident();
        String failed = runFailed(incidentId, "run-nokey");
        assertThat(validate("agent-event", failed)).isEmpty();
        publish(failed);

        String token = login("vic");
        Map<String, Object> detail = eventually(() -> {
            Map<String, Object> d = body(get("/api/incidents/" + incidentId, token));
            return "FAILED".equals(((Map<?, ?>) d.get("incident")).get("status")) ? d : null;
        });
        assertThat(detail.get("run_id")).isEqualTo("run-nokey");
        assertThat(kinds(detail)).contains("agent.failed");
        assertThat(list(get("/api/incidents/" + incidentId + "/steps", token)))
                .extracting(s -> s.get("kind")).containsExactly("run.failed");
    }

    // --- incident lifecycle -----------------------------------------------------------------------

    @Test
    void aFailedInvestigationLeavesTheIncidentOpenForItsAlerts() throws Exception {
        String group = "g-" + UUID.randomUUID();
        String incidentId = body(post("/api/alerts/alertmanager", alertmanagerPayload(group, "firing"), AM_TOKEN))
                .get("incident_id").toString();
        publish(runFailed(incidentId, "run-down"));
        String token = login("vic");
        eventually(() -> "FAILED".equals(((Map<?, ?>) body(get("/api/incidents/" + incidentId, token))
                .get("incident")).get("status")) ? true : null);

        // Without this, every repeat notification would open (and investigate) a new incident.
        Map<String, Object> repeat = body(post("/api/alerts/alertmanager", alertmanagerPayload(group, "firing"), AM_TOKEN));
        assertThat(repeat.get("opened")).isEqualTo(false);
        assertThat(repeat.get("incident_id")).isEqualTo(incidentId);
        assertThat(body(get("/api/incidents?status=active", token)).get("items").toString()).contains(incidentId);
    }

    @Test
    void alertsStillFiringAfterAResolveDoNotOpenAnotherIncident() throws Exception {
        String group = "g-" + UUID.randomUUID();
        String incidentId = body(post("/api/alerts/alertmanager", alertmanagerPayload(group, "firing"), AM_TOKEN))
                .get("incident_id").toString();
        assertThat(post("/api/incidents/" + incidentId + "/resolve", "", login("bob")).statusCode()).isEqualTo(204);

        // The fix is in, but the error-rate window takes minutes to drain.
        Map<String, Object> lingering = body(post("/api/alerts/alertmanager", alertmanagerPayload(group, "firing"), AM_TOKEN));
        assertThat(lingering.get("opened")).isEqualTo(false);
        assertThat(lingering.get("incident_id")).isNull();

        // The same alerts firing again later are a new episode: a new incident.
        String refired = alertmanagerPayload(group, "firing").replace("2026-10-04T09:00", "2026-10-04T11:00");
        Map<String, Object> next = body(post("/api/alerts/alertmanager", refired, AM_TOKEN));
        assertThat(next.get("opened")).isEqualTo(true);
        assertThat(next.get("incident_id")).isNotEqualTo(incidentId);
    }

    // --- access control ---------------------------------------------------------------------------

    @Test
    void rolesAreEnforced() throws Exception {
        String incidentId = openIncident();
        assertThat(get("/api/incidents", null).statusCode()).isEqualTo(401);
        assertThat(get("/api/incidents", login("vic")).statusCode()).isEqualTo(200);
        assertThat(post("/api/incidents/" + incidentId + "/resolve", "", login("vic")).statusCode()).isEqualTo(403);
        assertThat(post("/api/incidents/" + incidentId + "/resolve", "", login("bob")).statusCode()).isEqualTo(204);
        assertThat(post("/api/auth/token", "{\"username\":\"bob\",\"password\":\"wrong\"}", null).statusCode())
                .isEqualTo(401);
    }

    @Test
    void theInboxPaginatesWithACursor() throws Exception {
        for (int i = 0; i < 3; i++) {
            openIncident();
        }
        String token = login("vic");
        Map<String, Object> page1 = body(get("/api/incidents?status=all&limit=2", token));
        assertThat((List<?>) page1.get("items")).hasSize(2);
        String cursor = page1.get("next_cursor").toString();
        Map<String, Object> page2 = body(get("/api/incidents?status=all&limit=2&cursor=" + cursor, token));
        assertThat((List<?>) page2.get("items")).isNotEmpty();
        assertThat(page2.get("items").toString()).doesNotContain(((Map<?, ?>) ((List<?>) page1.get("items")).getFirst()).get("id").toString());
    }

    // --- live updates -----------------------------------------------------------------------------

    @Test
    void subscribersSeeIncidentsLive() throws Exception {
        WebSocketStompClient client = new WebSocketStompClient(new StandardWebSocketClient());
        client.setMessageConverter(new JacksonJsonMessageConverter());
        StompHeaders connect = new StompHeaders();
        connect.add("Authorization", "Bearer " + login("vic"));
        StompSession session = client.connectAsync("ws://localhost:" + port + "/ws", new WebSocketHttpHeaders(),
                connect, new StompSessionHandlerAdapter() {
                }).get(10, TimeUnit.SECONDS);
        BlockingQueue<Map<String, Object>> received = new LinkedBlockingQueue<>();
        session.subscribe("/topic/incidents", new StompFrameHandler() {
            @Override
            public Type getPayloadType(StompHeaders headers) {
                return Map.class;
            }

            @Override
            @SuppressWarnings("unchecked")
            public void handleFrame(StompHeaders headers, Object payload) {
                received.add((Map<String, Object>) payload);
            }
        });
        Thread.sleep(300); // let the SUBSCRIBE reach the broker

        String incidentId = openIncident();
        Map<String, Object> message = received.poll(10, TimeUnit.SECONDS);
        assertThat(message).isNotNull();
        assertThat(message.get("type")).isEqualTo("incident");
        assertThat(((Map<?, ?>) message.get("incident")).get("id")).isEqualTo(incidentId);
        session.disconnect();
    }

    @Test
    void stompConnectWithoutATokenIsRefused() {
        WebSocketStompClient client = new WebSocketStompClient(new StandardWebSocketClient());
        var future = client.connectAsync("ws://localhost:" + port + "/ws", new WebSocketHttpHeaders(),
                new StompHeaders(), new StompSessionHandlerAdapter() {
                });
        assertThat(future).failsWithin(Duration.ofSeconds(10));
    }

    // --- evals ------------------------------------------------------------------------------------

    @Test
    void evalRunsBuildAScorecardPerBatch() throws Exception {
        String batch = "batch-" + UUID.randomUUID();
        String alice = login("alice");
        assertThat(post("/api/evals/runs", evalRun(batch, "db-pool", true, 120.5), login("vic")).statusCode())
                .isEqualTo(403);
        assertThat(post("/api/evals/runs", evalRun(batch, "db-pool", false, 300.0), alice).statusCode()).isEqualTo(200);
        // Re-running a scenario replaces its result.
        assertThat(post("/api/evals/runs", evalRun(batch, "db-pool", true, 120.5), alice).statusCode()).isEqualTo(200);
        assertThat(post("/api/evals/runs", evalRun(batch, "psp-latency", false, 200.0), alice).statusCode())
                .isEqualTo(200);

        List<Map<String, Object>> runs = list(get("/api/evals/runs?batch=" + batch, alice));
        assertThat(runs).extracting(r -> r.get("scenario")).containsExactlyInAnyOrder("db-pool", "psp-latency");
        assertThat(runs.getFirst().get("details")).isEqualTo(Map.of("note", "test"));

        Map<String, Object> scorecard = list(get("/api/evals/batches", alice)).stream()
                .filter(b -> batch.equals(b.get("batch"))).findFirst().orElseThrow();
        assertThat(scorecard.get("runs")).isEqualTo(2);
        assertThat(((Number) scorecard.get("accuracy")).doubleValue()).isEqualTo(0.5);
        assertThat(((Number) scorecard.get("median_seconds")).doubleValue()).isEqualTo(160.25);
        assertThat(scorecard.get("models")).isEqualTo("gemini-3.6-flash");
    }

    private static String evalRun(String batch, String scenario, boolean correct, double seconds) {
        return """
                {"batch": "%s", "scenario": "%s", "category": "db_pool_exhaustion", "status": "scored",
                 "correct": %s, "correct_top3": true, "top_category": "db_pool_exhaustion", "top_service": "orders",
                 "confidence": 0.9, "steps": 12, "llm_calls": 8, "prompt_tokens": 150000, "output_tokens": 4000,
                 "cost_usd": 0, "seconds": %s, "agent_seconds": 100.0, "model": "gemini-3.6-flash",
                 "run_at": "2026-10-05T09:00:00Z", "details": {"note": "test"}}"""
                .formatted(batch, scenario, correct, seconds);
    }

    // --- approvals --------------------------------------------------------------------------------

    @Test
    @SuppressWarnings("unchecked")
    void anApproverDecidesOnceAndTheDecisionCarriesATokenBoundToTheAction() throws Exception {
        String incidentId = openIncident();
        String approvalId = UUID.randomUUID().toString();
        String runId = "run-" + UUID.randomUUID(); // steps are unique per (run, seq) across tests
        for (String event : examples("agent-events.jsonl", incidentId)) {
            publish(event.replace("run-example", runId));
        }
        List<String> requested = examples("approval-events.jsonl", incidentId).stream()
                .map(e -> e.replace(EXAMPLE_APPROVAL, approvalId).replace("run-example", runId))
                .toList();
        assertThat(validate("agent-event", requested.get(0))).isEmpty();
        publish(requested.get(0));
        publish(requested.get(0)); // redelivered: still one request

        String alice = login("alice");
        Map<String, Object> waiting = eventually(() -> {
            Map<String, Object> d = body(get("/api/incidents/" + incidentId, alice));
            return "AWAITING_APPROVAL".equals(((Map<?, ?>) d.get("incident")).get("status")) ? d : null;
        });
        List<Map<String, Object>> approvals = (List<Map<String, Object>>) waiting.get("approvals");
        assertThat(approvals).hasSize(1);
        assertThat(approvals.getFirst()).containsEntry("id", approvalId).containsEntry("status", "PENDING")
                .containsEntry("risk", "low").containsEntry("requested_by_agent", "fixer");
        String action = approvals.getFirst().get("action").toString();

        String path = "/api/incidents/" + incidentId + "/approvals/" + approvalId;
        assertThat(post(path, "{\"decision\": \"approve\"}", login("bob")).statusCode()).isEqualTo(403);
        assertThat(post(path, "{\"decision\": \"reject\"}", alice).statusCode()).isEqualTo(400); // no reason
        HttpResponse<String> approved = post(path, "{\"decision\": \"approve\", \"reason\": \"matches the deploy\"}", alice);
        assertThat(approved.statusCode()).isEqualTo(200);
        assertThat(body(approved)).containsEntry("status", "APPROVED").containsEntry("decided_by", "alice");
        assertThat(post(path, "{\"decision\": \"reject\", \"reason\": \"changed my mind\"}", alice).statusCode())
                .isEqualTo(409);

        // The decision reaches the agent service, contract-valid, with a token bound to the exact action.
        String decision = eventually(() -> redis.opsForStream().range("relay.approvals", Range.unbounded()).stream()
                .map(r -> r.getValue().get("event").toString())
                .filter(e -> e.contains(approvalId))
                .findFirst().orElse(null));
        assertThat(validate("approval-decision", decision)).isEmpty();
        Map<String, Object> message = body(decision);
        assertThat(message.get("action")).isEqualTo(action);
        String token = message.get("token").toString();
        SignedJWT jwt = SignedJWT.parse(token);
        JWKSet keys = JWKSet.parse(get("/.well-known/jwks.json", null).body());
        assertThat(jwt.verify(new RSASSAVerifier(keys.getKeys().getFirst().toRSAKey()))).isTrue();
        JWTClaimsSet claims = jwt.getJWTClaimsSet();
        assertThat(claims.getAudience()).containsExactly("relay-actions");
        assertThat(claims.getSubject()).isEqualTo(approvalId);
        assertThat(claims.getStringClaim("action_sha256")).isEqualTo(HexFormat.of().formatHex(
                MessageDigest.getInstance("SHA-256").digest(action.getBytes(StandardCharsets.UTF_8))));
        assertThat(claims.getStringClaim("approved_by")).isEqualTo("alice");
        // An approval token authorizes one action, never an API call.
        assertThat(get("/api/incidents/" + incidentId, token).statusCode()).isEqualTo(401);

        publish(requested.get(1)); // action.executed
        Map<String, Object> done = eventually(() -> {
            Map<String, Object> d = body(get("/api/incidents/" + incidentId, alice));
            Map<?, ?> a = ((List<Map<?, ?>>) d.get("approvals")).getFirst();
            return "EXECUTED".equals(a.get("status")) ? d : null;
        });
        assertThat(((Map<?, ?>) done.get("incident")).get("status")).isEqualTo("DIAGNOSED");
        assertThat(((Map<?, ?>) ((List<Map<?, ?>>) done.get("approvals")).getFirst().get("result")).get("pull_request"))
                .isEqualTo(3);
        assertThat(kinds(done)).contains("approval.requested", "approval.approved", "action.executed");
    }

    @Test
    @SuppressWarnings("unchecked")
    void resolvingAnIncidentRejectsWhatStillWaitsForADecision() throws Exception {
        String incidentId = openIncident();
        String approvalId = UUID.randomUUID().toString();
        String runId = "run-" + UUID.randomUUID();
        for (String event : examples("agent-events.jsonl", incidentId)) {
            publish(event.replace("run-example", runId));
        }
        publish(examples("approval-events.jsonl", incidentId).getFirst()
                .replace(EXAMPLE_APPROVAL, approvalId).replace("run-example", runId));
        String alice = login("alice");
        eventually(() -> "AWAITING_APPROVAL".equals(((Map<?, ?>) body(get("/api/incidents/" + incidentId, alice))
                .get("incident")).get("status")) ? true : null);

        assertThat(post("/api/incidents/" + incidentId + "/resolve", "", login("bob")).statusCode()).isEqualTo(204);

        Map<String, Object> detail = body(get("/api/incidents/" + incidentId, alice));
        Map<String, Object> approval = ((List<Map<String, Object>>) detail.get("approvals")).getFirst();
        assertThat(approval).containsEntry("status", "REJECTED").containsEntry("decided_by", "bob")
                .containsEntry("decision_reason", "The incident was resolved before anyone decided.");
        String decision = eventually(() -> redis.opsForStream().range("relay.approvals", Range.unbounded()).stream()
                .map(r -> r.getValue().get("event").toString())
                .filter(e -> e.contains(approvalId))
                .findFirst().orElse(null));
        assertThat(validate("approval-decision", decision)).isEmpty();
        assertThat(body(decision)).containsEntry("decision", "rejected").doesNotContainKey("token");
    }

    @Test
    @SuppressWarnings("unchecked")
    void aReviewLowersConfidenceAndKeepsWhatTheInvestigatorSaid() throws Exception {
        String incidentId = openIncident();
        String runId = "run-" + UUID.randomUUID();
        for (String event : examples("agent-events.jsonl", incidentId)) {
            publish(event.replace("run-example", runId));
        }
        for (String event : examples("review-events.jsonl", incidentId)) {
            assertThat(validate("agent-event", event)).isEmpty();
            publish(event.replace("run-example", runId));
        }
        String token = login("vic");
        Map<String, Object> detail = eventually(() -> {
            Map<String, Object> d = body(get("/api/incidents/" + incidentId, token));
            return kinds(d).contains("agent.reviewed") ? d : null;
        });
        Map<String, Object> top = ((List<Map<String, Object>>) detail.get("hypotheses")).getFirst();
        assertThat(((Number) top.get("confidence")).doubleValue()).isEqualTo(0.7);
        assertThat(((Number) top.get("original_confidence")).doubleValue()).isEqualTo(0.9);
        assertThat((Map<String, Object>) top.get("review")).containsEntry("verdict", "weak")
                .containsEntry("model", "gemini-3.6-flash");
        assertThat(((Map<?, ?>) detail.get("incident")).get("root_cause_category")).isEqualTo("db_pool_exhaustion");
        List<Map<String, Object>> steps = eventually(() -> {
            List<Map<String, Object>> s = list(get("/api/incidents/" + incidentId + "/steps?run_id=" + runId, token));
            return s.size() >= 10 ? s : null;
        });
        assertThat(steps).extracting(s -> s.get("kind")).endsWith("llm.completed", "review.completed", "run.finished");
        assertThat(steps.get(8)).containsEntry("agent", "reviewer");
    }

    @Test
    @SuppressWarnings("unchecked")
    void aResolvedIncidentGetsAPostmortemThatIsStoredAndIndexed() throws Exception {
        String incidentId = diagnosedIncident();
        assertThat(post("/api/incidents/" + incidentId + "/resolve", "", login("bob")).statusCode()).isEqualTo(204);

        String resolved = eventually(() -> redis.opsForStream().range("relay.resolved", Range.unbounded()).stream()
                .map(r -> r.getValue().get("event").toString())
                .filter(e -> e.contains(incidentId))
                .findFirst().orElse(null));
        assertThat(validate("incident-resolved", resolved)).isEmpty();
        Map<String, Object> incident = (Map<String, Object>) body(resolved).get("incident");
        assertThat(incident).containsEntry("resolved_by", "bob");
        assertThat((List<?>) incident.get("hypotheses")).hasSize(1);
        assertThat((List<Map<String, Object>>) incident.get("timeline")).extracting(e -> e.get("kind"))
                .contains("agent.diagnosed", "incident.resolved");

        String runId = "pm-" + UUID.randomUUID().toString().substring(0, 12);
        for (String event : examples("postmortem-events.jsonl", incidentId)) {
            assertThat(validate("agent-event", event)).isEmpty();
            publish(event.replace("pm-example", runId));
        }
        String vic = login("vic");
        Map<String, Object> detail = eventually(() -> {
            Map<String, Object> d = body(get("/api/incidents/" + incidentId, vic));
            return d.get("postmortem") != null ? d : null;
        });
        Map<String, Object> postmortem = (Map<String, Object>) detail.get("postmortem");
        assertThat(postmortem).containsEntry("title", "orders 1.4.0 broke checkout").containsEntry("run_id", runId);
        assertThat(postmortem.get("markdown").toString()).contains("## Action items");
        assertThat((Map<String, Object>) postmortem.get("document")).containsKey("timeline");
        assertThat(kinds(detail)).contains("postmortem.written");
        String key = ((Map<?, ?>) detail.get("incident")).get("key").toString();
        assertThat(jdbc.sql("""
                SELECT count(*) FROM knowledge_chunks
                 WHERE doc_id = :doc AND kind = 'postmortem' AND embedding IS NULL""")
                .param("doc", "postmortem:" + key).query(Integer.class).single()).isEqualTo(5);
    }

    @Test
    void anEvalRunResolvesWithoutAPostmortem() throws Exception {
        String incidentId = diagnosedIncident();
        assertThat(post("/api/incidents/" + incidentId + "/resolve?postmortem=false", "", login("bob")).statusCode())
                .isEqualTo(204);
        Thread.sleep(1_000); // the outbox ships every 250 ms
        assertThat(redis.opsForStream().range("relay.resolved", Range.unbounded()))
                .noneMatch(r -> r.getValue().get("event").toString().contains(incidentId));
    }

    private String diagnosedIncident() throws Exception {
        String incidentId = openIncident();
        String runId = "run-" + UUID.randomUUID();
        for (String event : examples("agent-events.jsonl", incidentId)) {
            publish(event.replace("run-example", runId));
        }
        String vic = login("vic");
        eventually(() -> "DIAGNOSED".equals(((Map<?, ?>) body(get("/api/incidents/" + incidentId, vic))
                .get("incident")).get("status")) ? true : null);
        return incidentId;
    }

    @Test
    @SuppressWarnings("unchecked")
    void aTriageCanRaiseTheSeverityButNeverLowerIt() throws Exception {
        String incidentId = openIncident(); // a warning
        String runId = "run-" + UUID.randomUUID();
        List<String> events = examples("triage-events.jsonl", incidentId);
        for (String event : events) {
            assertThat(validate("agent-event", event)).isEmpty();
            publish(event.replace("run-example", runId));
        }
        String vic = login("vic");
        Map<String, Object> detail = eventually(() -> {
            Map<String, Object> d = body(get("/api/incidents/" + incidentId, vic));
            return d.get("triage") != null ? d : null;
        });
        assertThat((Map<String, Object>) detail.get("incident")).containsEntry("severity", "critical");
        Map<String, Object> triage = (Map<String, Object>) detail.get("triage");
        assertThat(triage).containsEntry("service", "orders").containsEntry("run_id", runId);
        assertThat((List<?>) triage.get("related")).hasSize(3);
        assertThat(kinds(detail)).contains("agent.triaged");

        // A later look that calls it minor doesn't take the page back.
        publish(events.get(1).replace("run-example", runId).replace("\"seq\": 1", "\"seq\": 2")
                .replace("\"severity\": \"critical\"", "\"severity\": \"info\""));
        Map<String, Object> after = eventually(() -> {
            Map<String, Object> d = body(get("/api/incidents/" + incidentId, vic));
            return "info".equals(((Map<?, ?>) d.get("triage")).get("severity")) ? d : null;
        });
        assertThat((Map<String, Object>) after.get("incident")).containsEntry("severity", "critical");
    }

    @Test
    @SuppressWarnings("unchecked")
    void respondersVoteOnWhatTheAgentsShowedThemAndViewersCannot() throws Exception {
        String incidentId = diagnosedIncident();
        Map<String, Object> top = ((List<Map<String, Object>>) body(get("/api/incidents/" + incidentId, login("vic")))
                .get("hypotheses")).getFirst();
        String hypothesis = """
                {"hypothesis": {"run_id": "%s", "category": "%s", "service": "%s"}, "vote": "down"}"""
                .formatted(top.get("run_id"), top.get("category"), top.get("service"));
        String document = "{\"document\": \"runbook:db-connection-pool\", \"vote\": \"up\"}";
        String feedback = "/api/incidents/" + incidentId + "/feedback";

        assertThat(post(feedback, document, login("vic")).statusCode()).isEqualTo(403);
        String bob = login("bob");
        assertThat(post(feedback, document, bob).statusCode()).isEqualTo(204);
        assertThat(post(feedback, hypothesis, bob).statusCode()).isEqualTo(204);
        assertThat(post(feedback, hypothesis, bob).statusCode()).isEqualTo(204); // replaces, doesn't add
        assertThat(post(feedback, document, login("alice")).statusCode()).isEqualTo(204);
        assertThat(post(feedback, "{\"vote\": \"up\"}", bob).statusCode()).isEqualTo(400);

        Map<String, Object> votes = (Map<String, Object>) body(get("/api/incidents/" + incidentId, bob)).get("feedback");
        assertThat((Map<String, Object>) votes.get("documents"))
                .containsEntry("runbook:db-connection-pool", Map.of("up", 2, "down", 0, "mine", 1));
        assertThat((Map<String, Object>) votes.get("hypotheses"))
                .containsEntry(top.get("category") + ":" + top.get("service"), Map.of("up", 0, "down", 1, "mine", -1));

        assertThat(post(feedback, document.replace("\"up\"", "\"none\""), bob).statusCode()).isEqualTo(204);
        Map<String, Object> after = (Map<String, Object>) body(get("/api/incidents/" + incidentId, bob)).get("feedback");
        assertThat((Map<String, Object>) after.get("documents"))
                .containsEntry("runbook:db-connection-pool", Map.of("up", 1, "down", 0, "mine", 0));
    }

    @Test
    void theApprovalDecisionExampleMatchesItsContract() throws Exception {
        assertThat(validate("approval-decision", Files.readString(CONTRACTS.resolve("examples/approval-decision.json"))))
                .isEmpty();
        String rejectedWithToken = Files.readString(CONTRACTS.resolve("examples/approval-decision.json"))
                .replace("\"approved\"", "\"rejected\"");
        assertThat(validate("approval-decision", rejectedWithToken)).isNotEmpty();
    }

    // --- helpers ----------------------------------------------------------------------------------

    private List<String> examples(String file, String incidentId) throws IOException {
        return Files.readAllLines(CONTRACTS.resolve("examples/" + file)).stream()
                .filter(line -> !line.isBlank())
                .map(line -> line.replace(EXAMPLE_INCIDENT, incidentId))
                .toList();
    }

    private String openIncident() throws Exception {
        byte[] alert = signedAlert("ns-" + UUID.randomUUID());
        HttpResponse<String> response = postSigned(alert, HmacVerifier.sign(SECRET, Instant.now().getEpochSecond(), alert));
        assertThat(response.statusCode()).isEqualTo(202);
        return body(response).get("incident_id").toString();
    }

    private static String alertmanagerPayload(String group, String status) {
        return """
                {"version": "4", "groupKey": "%1$s", "status": "%2$s", "receiver": "relay",
                 "groupLabels": {"namespace": "sandbox"}, "commonLabels": {"namespace": "sandbox"},
                 "externalURL": "http://alertmanager:9093", "truncatedAlerts": 0,
                 "alerts": [
                   {"status": "%2$s", "labels": {"alertname": "HighErrorRate", "service": "orders", "severity": "critical", "namespace": "sandbox"},
                    "annotations": {"summary": "orders: 68%% of requests are failing"},
                    "startsAt": "2026-10-04T09:00:03Z", "endsAt": "0001-01-01T00:00:00Z", "generatorURL": "http://prom", "fingerprint": "fp-orders-%1$s"},
                   {"status": "%2$s", "labels": {"alertname": "HighErrorRate", "service": "gateway", "severity": "critical", "namespace": "sandbox"},
                    "annotations": {"summary": "gateway: 21%% of requests are failing"},
                    "startsAt": "2026-10-04T09:00:33Z", "endsAt": "0001-01-01T00:00:00Z", "generatorURL": "http://prom", "fingerprint": "fp-gateway-%1$s"}
                 ]}""".formatted(group, status);
    }

    private static String runFailed(String incidentId, String runId) {
        return """
                {"event_id": "%s", "type": "run.failed", "run_id": "%s", "incident_id": "%s",
                 "agent": "investigator", "seq": 0, "at": "2026-10-05T09:00:00Z",
                 "data": {"error": "RuntimeError: ANTHROPIC_API_KEY is not set"}}"""
                .formatted(UUID.randomUUID(), runId, incidentId);
    }

    private static byte[] signedAlert(String namespace) {
        return ("{\"name\": \"HighLatency\", \"service\": \"orders\", \"severity\": \"warning\", \"namespace\": \""
                + namespace + "\", \"summary\": \"p95 above 1s\"}").getBytes(StandardCharsets.UTF_8);
    }

    private List<Error> validate(String schemaName, String document) throws IOException {
        Schema schema = SchemaRegistry.withDefaultDialect(SpecificationVersion.DRAFT_2020_12)
                .getSchema(Files.readString(CONTRACTS.resolve("schemas/" + schemaName + ".schema.json")));
        return schema.validate(document, InputFormat.JSON);
    }

    private void publish(String event) {
        MapRecord<String, String, String> record = StreamRecords.string(Map.of("event", event))
                .withStreamKey("relay.agent-events");
        redis.opsForStream().add(record);
    }

    private String login(String user) throws Exception {
        HttpResponse<String> response = post("/api/auth/token",
                "{\"username\":\"" + user + "\",\"password\":\"relay-demo\"}", null);
        assertThat(response.statusCode()).isEqualTo(200);
        return body(response).get("access_token").toString();
    }

    private HttpResponse<String> get(String path, String token) throws Exception {
        HttpRequest.Builder request = HttpRequest.newBuilder(URI.create("http://localhost:" + port + path)).GET();
        if (token != null) {
            request.header("Authorization", "Bearer " + token);
        }
        return http.send(request.build(), HttpResponse.BodyHandlers.ofString());
    }

    private HttpResponse<String> post(String path, String body, String authorization) throws Exception {
        HttpRequest.Builder request = HttpRequest.newBuilder(URI.create("http://localhost:" + port + path))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body));
        if (authorization != null) {
            request.header("Authorization", authorization.startsWith("Bearer ") ? authorization : "Bearer " + authorization);
        }
        return http.send(request.build(), HttpResponse.BodyHandlers.ofString());
    }

    private HttpResponse<String> postSigned(byte[] body, String signature) throws Exception {
        return http.send(HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/api/alerts"))
                .header("Content-Type", "application/json")
                .header("X-Relay-Signature", signature)
                .POST(HttpRequest.BodyPublishers.ofByteArray(body)).build(), HttpResponse.BodyHandlers.ofString());
    }

    private Map<String, Object> body(HttpResponse<String> response) {
        return body(response.body());
    }

    private Map<String, Object> body(String document) {
        return json.readValue(document, MAP);
    }

    private List<Map<String, Object>> list(HttpResponse<String> response) {
        return json.readValue(response.body(), new TypeReference<>() {
        });
    }

    @SuppressWarnings("unchecked")
    private static List<String> kinds(Map<String, Object> detail) {
        return ((List<Map<String, Object>>) detail.get("timeline")).stream().map(e -> e.get("kind").toString()).toList();
    }

    private static <T> T eventually(Callable<T> check) throws Exception {
        long deadline = System.nanoTime() + Duration.ofSeconds(15).toNanos();
        while (System.nanoTime() < deadline) {
            T value = check.call();
            if (value != null) {
                return value;
            }
            Thread.sleep(100);
        }
        throw new AssertionError("condition not met within 15 s");
    }
}
