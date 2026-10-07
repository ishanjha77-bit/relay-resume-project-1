package dev.relay.stream;

import org.springframework.messaging.simp.SimpMessagingTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionalEventListener;

import dev.relay.agent.AgentStep;
import dev.relay.agent.StepRecorded;
import dev.relay.incident.IncidentChanged;
import dev.relay.incident.IncidentRepository;
import dev.relay.incident.IncidentSummary;

/**
 * Pushes changes to the console after the transaction that made them commits,
 * so a client can never see a step or status that was rolled back.
 */
@Component
class LiveUpdates {

    /** {"type": "incident", "incident": {...}} */
    record IncidentMessage(String type, IncidentSummary incident) {
    }

    /** {"type": "step", "step": {...}} */
    record StepMessage(String type, AgentStep step) {
    }

    private final SimpMessagingTemplate broker;
    private final IncidentRepository incidents;

    LiveUpdates(SimpMessagingTemplate broker, IncidentRepository incidents) {
        this.broker = broker;
        this.incidents = incidents;
    }

    @TransactionalEventListener(fallbackExecution = true)
    void incidentChanged(IncidentChanged event) {
        incidents.findById(event.incidentId()).map(IncidentSummary::from).ifPresent(summary -> {
            IncidentMessage message = new IncidentMessage("incident", summary);
            broker.convertAndSend("/topic/incidents", message);
            broker.convertAndSend("/topic/incidents/" + event.incidentId(), message);
        });
    }

    @TransactionalEventListener(fallbackExecution = true)
    void stepRecorded(StepRecorded event) {
        broker.convertAndSend("/topic/incidents/" + event.incidentId(), new StepMessage("step", event.step()));
    }
}
