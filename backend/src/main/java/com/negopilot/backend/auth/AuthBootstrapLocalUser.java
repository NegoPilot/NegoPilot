package com.negopilot.backend.auth;

import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.NegoPilotApplication;

import java.util.Map;
import org.springframework.boot.WebApplicationType;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

/** Explicit local-test CLI. Normal application startup never calls this class. */
public final class AuthBootstrapLocalUser {
    private AuthBootstrapLocalUser() { }

    public static void main(String[] args) {
        if (args.length != 0) {
            throw new IllegalArgumentException("This CLI accepts no arguments. Set LOCAL_USER_EMAIL and LOCAL_USER_PASSWORD in the environment.");
        }
        AuthRequestValidator.Signup input = inputFromEnvironment(System.getenv());
        try (var context = new SpringApplicationBuilder(NegoPilotApplication.class)
                .web(WebApplicationType.NONE).profiles("bootstrap-admin").run()) {
            BootstrapResult result = createLocalUser(input, context.getBean(MembershipRepository.class),
                    context.getBean(PasswordEncoder.class), context.getBean(TransactionTemplate.class));
            System.out.println(result.message());
        }
    }

    enum BootstrapResult {
        CREATED("Local test user created with USER/ACTIVE membership."),
        SKIPPED("An active user already exists. Bootstrap skipped; existing account and password were not changed.");

        private final String message;
        BootstrapResult(String message) { this.message = message; }
        String message() { return message; }
    }

    static AuthRequestValidator.Signup inputFromEnvironment(Map<String, String> environment) {
        String email = environment.get("LOCAL_USER_EMAIL");
        String password = environment.get("LOCAL_USER_PASSWORD");
        if (email == null || email.isBlank() || password == null || password.isBlank()) {
            throw new IllegalArgumentException("LOCAL_USER_EMAIL and LOCAL_USER_PASSWORD are required.");
        }
        try {
            return AuthRequestValidator.signup(JsonMapper.builder().build().valueToTree(Map.of(
                    "email", email, "password", password, "name", "로컬 테스트 회원",
                    "department", "개발팀", "job_title", "테스트")));
        } catch (ApiException invalid) {
            throw new IllegalArgumentException("LOCAL_USER_EMAIL or LOCAL_USER_PASSWORD does not meet membership validation rules.");
        }
    }

    static BootstrapResult createLocalUser(AuthRequestValidator.Signup input, MembershipRepository repository,
            PasswordEncoder passwords, TransactionTemplate transactions) {
        String hash = passwords.encode(input.password());
        return transactions.execute(tx -> {
            repository.serializeChanges();
            var existing = repository.byEmail(input.email());
            if (existing != null && !("USER".equals(existing.role()) && "ACTIVE".equals(existing.status()))) {
                throw new IllegalStateException("Local user email is registered to an account that is not an active USER. No account was changed.");
            }
            if (repository.activeAdminCount() == 0) {
                throw new IllegalStateException("An active administrator is required before local user initialization.");
            }
            if (existing != null) return BootstrapResult.SKIPPED;
            var member = repository.insert(input, hash, "USER", "ACTIVE");
            repository.audit(null, member.id(), "LOCAL_USER_BOOTSTRAP", null, "USER");
            return BootstrapResult.CREATED;
        });
    }
}
