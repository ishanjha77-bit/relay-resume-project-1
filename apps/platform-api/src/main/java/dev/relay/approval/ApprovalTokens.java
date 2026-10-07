package dev.relay.approval;

import java.time.Instant;
import java.util.List;

import org.springframework.security.oauth2.jose.jws.SignatureAlgorithm;
import org.springframework.security.oauth2.jwt.JwsHeader;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.stereotype.Component;

import com.nimbusds.jose.jwk.RSAKey;

import dev.relay.RelayProperties;

/**
 * Signs approval tokens: proof, for the MCP server that performs a write, that a
 * human approved this exact action. RS256 with the platform's key (verifiable
 * through /.well-known/jwks.json), audience {@value #AUDIENCE}, and the action's
 * SHA-256 in {@code action_sha256}. Short-lived and single-purpose: the API
 * refuses these tokens as logins (see JwtConfig).
 */
@Component
public class ApprovalTokens {

    public static final String AUDIENCE = "relay-actions";

    private final JwtEncoder encoder;
    private final RSAKey signingKey;
    private final RelayProperties properties;

    ApprovalTokens(JwtEncoder encoder, RSAKey signingKey, RelayProperties properties) {
        this.encoder = encoder;
        this.signingKey = signingKey;
        this.properties = properties;
    }

    String issue(Approval approval, String approvedBy, String incidentKey, Instant now) {
        JwtClaimsSet claims = JwtClaimsSet.builder()
                .issuer(properties.security().issuer())
                .subject(approval.id().toString())
                .audience(List.of(AUDIENCE))
                .issuedAt(now)
                .expiresAt(now.plus(properties.security().approvalTtl()))
                .claim("action_sha256", approval.actionSha256())
                .claim("approved_by", approvedBy)
                .claim("incident", incidentKey)
                .claim("run_id", approval.runId())
                .build();
        JwsHeader header = JwsHeader.with(SignatureAlgorithm.RS256).keyId(signingKey.getKeyID()).build();
        return encoder.encode(JwtEncoderParameters.from(header, claims)).getTokenValue();
    }
}
