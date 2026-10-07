package dev.relay.stream;

import org.springframework.core.convert.converter.Converter;
import org.springframework.messaging.Message;
import org.springframework.messaging.MessageChannel;
import org.springframework.messaging.MessagingException;
import org.springframework.messaging.simp.stomp.StompCommand;
import org.springframework.messaging.simp.stomp.StompHeaderAccessor;
import org.springframework.messaging.support.ChannelInterceptor;
import org.springframework.messaging.support.MessageHeaderAccessor;
import org.springframework.security.authentication.AbstractAuthenticationToken;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtException;
import org.springframework.stereotype.Component;

import dev.relay.security.JwtRoles;

/**
 * Browsers can't set headers on the WebSocket handshake, so the bearer token
 * travels in the STOMP CONNECT frame instead. CONNECT without a valid token is
 * refused, and so is any SUBSCRIBE on an unauthenticated session.
 */
@Component
class StompAuthInterceptor implements ChannelInterceptor {

    private final JwtDecoder decoder;
    private final Converter<Jwt, AbstractAuthenticationToken> converter;

    StompAuthInterceptor(JwtDecoder decoder) {
        this.decoder = decoder;
        this.converter = JwtRoles.converter();
    }

    @Override
    public Message<?> preSend(Message<?> message, MessageChannel channel) {
        StompHeaderAccessor accessor = MessageHeaderAccessor.getAccessor(message, StompHeaderAccessor.class);
        if (accessor == null) {
            return message;
        }
        if (StompCommand.CONNECT.equals(accessor.getCommand())) {
            String header = accessor.getFirstNativeHeader("Authorization");
            if (header == null || !header.startsWith("Bearer ")) {
                throw new MessagingException("Missing bearer token in STOMP CONNECT");
            }
            try {
                accessor.setUser(converter.convert(decoder.decode(header.substring(7))));
            } catch (JwtException e) {
                throw new MessagingException("Invalid token: " + e.getMessage());
            }
        } else if (StompCommand.SUBSCRIBE.equals(accessor.getCommand()) && accessor.getUser() == null) {
            throw new MessagingException("Not authenticated");
        }
        return message;
    }
}
