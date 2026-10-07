package dev.relay.security;

import java.time.Clock;
import java.time.Instant;
import java.util.List;
import java.util.Map;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.oauth2.jose.jws.SignatureAlgorithm;
import org.springframework.security.oauth2.jwt.JwsHeader;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.RSAKey;

import dev.relay.RelayProperties;

@RestController
class AuthController {

    private final Users users;
    private final PasswordEncoder passwords;
    private final JwtEncoder encoder;
    private final RSAKey signingKey;
    private final RelayProperties properties;
    private final Clock clock;

    AuthController(Users users, PasswordEncoder passwords, JwtEncoder encoder, RSAKey signingKey,
            RelayProperties properties, Clock clock) {
        this.users = users;
        this.passwords = passwords;
        this.encoder = encoder;
        this.signingKey = signingKey;
        this.properties = properties;
        this.clock = clock;
    }

    record LoginRequest(@NotBlank String username, @NotBlank String password) {
    }

    record TokenResponse(String accessToken, String tokenType, long expiresIn, String username, String displayName,
            List<String> roles) {
    }

    @PostMapping("/api/auth/token")
    ResponseEntity<TokenResponse> token(@Valid @RequestBody LoginRequest login) {
        return users.find(login.username())
                .filter(user -> passwords.matches(login.password(), user.passwordHash()))
                .map(user -> ResponseEntity.ok(issue(user)))
                .orElseGet(() -> ResponseEntity.status(HttpStatus.UNAUTHORIZED).build());
    }

    @GetMapping("/api/auth/me")
    Map<String, Object> me(@AuthenticationPrincipal Jwt jwt) {
        return Map.of("username", jwt.getSubject(), "roles", jwt.getClaimAsStringList("roles"));
    }

    /** Public signing key, for services that verify Relay tokens themselves. */
    @GetMapping("/.well-known/jwks.json")
    Map<String, Object> jwks() {
        return new JWKSet(signingKey.toPublicJWK()).toJSONObject();
    }

    private TokenResponse issue(Users.User user) {
        Instant now = clock.instant();
        var ttl = properties.security().tokenTtl();
        JwtClaimsSet claims = JwtClaimsSet.builder()
                .issuer(properties.security().issuer())
                .subject(user.username())
                .issuedAt(now)
                .expiresAt(now.plus(ttl))
                .claim("name", user.displayName())
                .claim("roles", user.roles())
                .build();
        JwsHeader header = JwsHeader.with(SignatureAlgorithm.RS256).keyId(signingKey.getKeyID()).build();
        String token = encoder.encode(JwtEncoderParameters.from(header, claims)).getTokenValue();
        return new TokenResponse(token, "Bearer", ttl.toSeconds(), user.username(), user.displayName(), user.roles());
    }
}
