package dev.relay.security;

import java.util.List;
import java.util.Optional;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

import dev.relay.RelayProperties;

/**
 * Local user store for the demo. Each user's roles map to what they may do:
 * viewers read, responders also resolve incidents, approvers also approve agent actions.
 * Demo users are seeded at startup when a demo password is configured.
 */
@Component
class Users implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(Users.class);

    record User(String username, String displayName, String passwordHash, List<String> roles) {
    }

    private final JdbcClient jdbc;
    private final PasswordEncoder passwords;
    private final RelayProperties properties;

    Users(JdbcClient jdbc, PasswordEncoder passwords, RelayProperties properties) {
        this.jdbc = jdbc;
        this.passwords = passwords;
        this.properties = properties;
    }

    Optional<User> find(String username) {
        return jdbc.sql("SELECT username, display_name, password_hash, roles FROM users WHERE username = :u")
                .param("u", username)
                .query((rs, n) -> new User(rs.getString("username"), rs.getString("display_name"),
                        rs.getString("password_hash"), List.of((String[]) rs.getArray("roles").getArray())))
                .optional();
    }

    @Override
    public void run(ApplicationArguments args) {
        String password = properties.security().demoPassword();
        if (password == null || password.isBlank()) {
            return;
        }
        upsert("alice", "Alice (approver)", password, List.of("VIEWER", "RESPONDER", "APPROVER"));
        upsert("bob", "Bob (responder)", password, List.of("VIEWER", "RESPONDER"));
        upsert("vic", "Vic (viewer)", password, List.of("VIEWER"));
        log.info("Demo users ready: alice (approver), bob (responder), vic (viewer)");
    }

    private void upsert(String username, String displayName, String password, List<String> roles) {
        jdbc.sql("""
                INSERT INTO users (username, display_name, password_hash, roles)
                VALUES (:u, :d, :p, CAST(:r AS text[]))
                ON CONFLICT (username) DO UPDATE
                   SET display_name = EXCLUDED.display_name, password_hash = EXCLUDED.password_hash,
                       roles = EXCLUDED.roles""")
                .param("u", username)
                .param("d", displayName)
                .param("p", passwords.encode(password))
                .param("r", "{" + String.join(",", roles) + "}")
                .update();
    }
}
