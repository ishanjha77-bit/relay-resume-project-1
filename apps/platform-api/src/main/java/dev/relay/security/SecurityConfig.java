package dev.relay.security;

import java.util.List;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.CorsConfigurationSource;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;

import dev.relay.RelayProperties;

/**
 * Two filter chains:
 * <ol>
 *   <li>/api/alerts/** — machine webhooks. They authenticate with an HMAC
 *       signature or a static bearer token, checked in the controller, so the
 *       JWT filter must not run (Alertmanager's token is not a JWT).</li>
 *   <li>Everything else — stateless JWT bearer auth with role-based access.</li>
 * </ol>
 * CSRF is off: no cookies or sessions are used, every request carries a bearer token.
 */
@Configuration
class SecurityConfig {

    static final String VIEWER = "VIEWER";
    static final String RESPONDER = "RESPONDER";
    static final String APPROVER = "APPROVER";

    @Bean
    @Order(1)
    SecurityFilterChain webhooks(HttpSecurity http) throws Exception {
        return http.securityMatcher("/api/alerts/**", "/api/alerts")
                .authorizeHttpRequests(auth -> auth.anyRequest().permitAll())
                .csrf(csrf -> csrf.disable())
                .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .build();
    }

    @Bean
    @Order(2)
    SecurityFilterChain api(HttpSecurity http) throws Exception {
        return http
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers("/actuator/health/**", "/actuator/prometheus", "/actuator/info").permitAll()
                        .requestMatchers("/.well-known/jwks.json", "/api/auth/token").permitAll()
                        .requestMatchers("/v3/api-docs/**", "/swagger-ui/**", "/swagger-ui.html").permitAll()
                        .requestMatchers("/ws/**").permitAll() // STOMP CONNECT frames carry the token
                        .requestMatchers(HttpMethod.POST, "/api/incidents/*/resolve").hasAnyRole(RESPONDER, APPROVER)
                        .requestMatchers(HttpMethod.POST, "/api/evals/runs").hasAnyRole(RESPONDER, APPROVER)
                        .requestMatchers(HttpMethod.POST, "/api/incidents/*/approvals/*").hasRole(APPROVER)
                        .requestMatchers(HttpMethod.POST, "/api/incidents/*/feedback").hasAnyRole(RESPONDER, APPROVER)
                        .requestMatchers(HttpMethod.GET, "/api/**").hasAnyRole(VIEWER, RESPONDER, APPROVER)
                        .anyRequest().authenticated())
                .oauth2ResourceServer(oauth -> oauth.jwt(jwt -> jwt.jwtAuthenticationConverter(JwtRoles.converter())))
                .cors(Customizer.withDefaults())
                .csrf(csrf -> csrf.disable())
                .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .build();
    }


    @Bean
    PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    @Bean
    CorsConfigurationSource corsConfigurationSource(RelayProperties properties) {
        CorsConfiguration cors = new CorsConfiguration();
        cors.setAllowedOrigins(properties.security().allowedOrigins());
        cors.setAllowedMethods(List.of("GET", "POST", "OPTIONS"));
        cors.setAllowedHeaders(List.of("Authorization", "Content-Type"));
        UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
        source.registerCorsConfiguration("/api/**", cors);
        return source;
    }
}
