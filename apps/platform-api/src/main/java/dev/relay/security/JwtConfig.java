package dev.relay.security;

import java.security.KeyFactory;
import java.security.KeyPairGenerator;
import java.security.interfaces.RSAPrivateCrtKey;
import java.security.interfaces.RSAPublicKey;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.RSAPublicKeySpec;
import java.util.Base64;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.oauth2.core.DelegatingOAuth2TokenValidator;
import org.springframework.security.oauth2.core.OAuth2Error;
import org.springframework.security.oauth2.core.OAuth2TokenValidator;
import org.springframework.security.oauth2.core.OAuth2TokenValidatorResult;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.JwtValidators;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.security.oauth2.jwt.NimbusJwtEncoder;

import com.nimbusds.jose.JOSEException;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jose.jwk.source.ImmutableJWKSet;

import dev.relay.RelayProperties;
import dev.relay.approval.ApprovalTokens;

/**
 * platform-api issues its own access tokens (RS256). Other services verify them
 * with the public key from /.well-known/jwks.json, so pointing the resource
 * server at a real identity provider later is configuration, not code.
 */
@Configuration
class JwtConfig {

    private static final Logger log = LoggerFactory.getLogger(JwtConfig.class);

    @Bean
    RSAKey signingKey(RelayProperties properties) throws Exception {
        String pem = properties.security().jwtPrivateKey();
        RSAPrivateCrtKey privateKey;
        if (pem == null || pem.isBlank()) {
            log.warn("No RELAY_JWT_PRIVATE_KEY configured: using an ephemeral signing key (tokens die on restart)");
            KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA");
            generator.initialize(2048);
            privateKey = (RSAPrivateCrtKey) generator.generateKeyPair().getPrivate();
        } else {
            String body = pem.replaceAll("-----(BEGIN|END) PRIVATE KEY-----", "").replaceAll("\\s", "");
            privateKey = (RSAPrivateCrtKey) KeyFactory.getInstance("RSA")
                    .generatePrivate(new PKCS8EncodedKeySpec(Base64.getDecoder().decode(body)));
        }
        RSAPublicKey publicKey = (RSAPublicKey) KeyFactory.getInstance("RSA")
                .generatePublic(new RSAPublicKeySpec(privateKey.getModulus(), privateKey.getPublicExponent()));
        return new RSAKey.Builder(publicKey).privateKey(privateKey).keyIDFromThumbprint().build();
    }

    @Bean
    JwtEncoder jwtEncoder(RSAKey signingKey) {
        return new NimbusJwtEncoder(new ImmutableJWKSet<>(new JWKSet(signingKey)));
    }

    @Bean
    JwtDecoder jwtDecoder(RSAKey signingKey, RelayProperties properties) throws JOSEException {
        NimbusJwtDecoder decoder = NimbusJwtDecoder.withPublicKey(signingKey.toRSAPublicKey()).build();
        // Approval tokens share the key and issuer, but they authorize one action, never an API call.
        // (User tokens carry no audience at all.)
        OAuth2TokenValidator<Jwt> notAnApproval = jwt -> jwt.getAudience() != null
                && jwt.getAudience().contains(ApprovalTokens.AUDIENCE)
                        ? OAuth2TokenValidatorResult.failure(new OAuth2Error("invalid_token",
                                "approval tokens are not API credentials", null))
                        : OAuth2TokenValidatorResult.success();
        decoder.setJwtValidator(new DelegatingOAuth2TokenValidator<>(
                JwtValidators.createDefaultWithIssuer(properties.security().issuer()), notAnApproval));
        return decoder;
    }
}
