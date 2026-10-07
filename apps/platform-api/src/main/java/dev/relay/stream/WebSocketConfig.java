package dev.relay.stream;

import org.springframework.context.annotation.Configuration;
import org.springframework.messaging.simp.config.ChannelRegistration;
import org.springframework.messaging.simp.config.MessageBrokerRegistry;
import org.springframework.web.socket.config.annotation.EnableWebSocketMessageBroker;
import org.springframework.web.socket.config.annotation.StompEndpointRegistry;
import org.springframework.web.socket.config.annotation.WebSocketMessageBrokerConfigurer;

import dev.relay.RelayProperties;

/**
 * STOMP over WebSocket at /ws. Topics:
 * <ul>
 *   <li>/topic/incidents — every incident change (the inbox)</li>
 *   <li>/topic/incidents/{id} — one incident's agent steps and status, live</li>
 * </ul>
 */
@Configuration
@EnableWebSocketMessageBroker
class WebSocketConfig implements WebSocketMessageBrokerConfigurer {

    private final StompAuthInterceptor auth;
    private final RelayProperties properties;

    WebSocketConfig(StompAuthInterceptor auth, RelayProperties properties) {
        this.auth = auth;
        this.properties = properties;
    }

    @Override
    public void registerStompEndpoints(StompEndpointRegistry registry) {
        registry.addEndpoint("/ws").setAllowedOrigins(properties.security().allowedOrigins().toArray(String[]::new));
    }

    @Override
    public void configureMessageBroker(MessageBrokerRegistry registry) {
        registry.enableSimpleBroker("/topic");
        registry.setApplicationDestinationPrefixes("/app");
    }

    @Override
    public void configureClientInboundChannel(ChannelRegistration registration) {
        registration.interceptors(auth);
    }
}
