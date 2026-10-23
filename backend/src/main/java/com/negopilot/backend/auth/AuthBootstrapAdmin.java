package com.negopilot.backend.auth;

import com.negopilot.backend.NegoPilotApplication;

import java.util.Arrays;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import org.springframework.boot.WebApplicationType;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import tools.jackson.databind.json.JsonMapper;

/** Run this CLI with the application classpath. It never starts an HTTP listener. */
public final class AuthBootstrapAdmin {
    private AuthBootstrapAdmin() { }
    public static void main(String[] args) {
        Arguments arguments = parseArguments(args);
        Map<String, String> values = new HashMap<>(arguments.values());
        char[] password = System.console() == null ? null : System.console().readPassword("Admin password: ");
        if (password == null) {
            String supplied = System.getenv("ADMIN_BOOTSTRAP_PASSWORD");
            if (supplied == null || supplied.isBlank()) throw new IllegalArgumentException("Provide password using a terminal or ADMIN_BOOTSTRAP_PASSWORD.");
            password = supplied.toCharArray();
        }
        AuthRequestValidator.Signup input;
        try {
            values.put("password", new String(password));
            input = AuthRequestValidator.signup(JsonMapper.builder().build().valueToTree(values));
        } finally { Arrays.fill(password, '\0'); values.remove("password"); }
        try (var context = new SpringApplicationBuilder(NegoPilotApplication.class)
                .web(WebApplicationType.NONE).profiles("bootstrap-admin").run()) {
            var repository = context.getBean(MembershipRepository.class);
            var transactions = context.getBean(TransactionTemplate.class);
            BootstrapResult result = createInitialAdministrator(input, repository,
                    context.getBean(PasswordEncoder.class), transactions, arguments.ifAbsent());
            System.out.println(result == BootstrapResult.CREATED ? "Initial administrator created."
                    : "An active administrator already exists. Bootstrap skipped; existing accounts and passwords were not changed.");
        }
    }

    record Arguments(Map<String, String> values, boolean ifAbsent) { }
    enum BootstrapResult { CREATED, SKIPPED }

    static Arguments parseArguments(String[] args) {
        Map<String, String> values = new HashMap<>();
        Set<String> fields = Set.of("email", "name", "department", "job_title");
        boolean ifAbsent = false;
        for (String arg : args) {
            if ("--if-absent".equals(arg) && !ifAbsent) {
                ifAbsent = true;
                continue;
            }
            int split = arg.indexOf('=');
            if (split < 3 || !arg.startsWith("--") || !fields.contains(arg.substring(2, split))
                    || values.put(arg.substring(2, split), arg.substring(split + 1)) != null) {
                throw new IllegalArgumentException("Use --email= --name= --department= and optional --job_title= or --if-absent. Password is read securely.");
            }
        }
        return new Arguments(Map.copyOf(values), ifAbsent);
    }

    static void createInitialAdministrator(AuthRequestValidator.Signup input, MembershipRepository repository,
                                           PasswordEncoder passwords, TransactionTemplate transactions) {
        createInitialAdministrator(input, repository, passwords, transactions, false);
    }

    static BootstrapResult createInitialAdministrator(AuthRequestValidator.Signup input, MembershipRepository repository,
            PasswordEncoder passwords, TransactionTemplate transactions, boolean ifAbsent) {
        String hash = passwords.encode(input.password());
        return transactions.execute(tx -> {
            repository.serializeChanges();
            // Check a conflicting email before skipping for a different administrator.
            // Local initialization must never silently promote or reactivate an existing member.
            var existing = repository.byEmail(input.email());
            if (ifAbsent && existing != null
                    && !("ADMIN".equals(existing.role()) && "ACTIVE".equals(existing.status()))) {
                throw new IllegalStateException("Bootstrap email is registered to an account that is not an active administrator. No account was changed.");
            }
            if (repository.activeAdminCount() > 0) {
                if (ifAbsent) return BootstrapResult.SKIPPED;
                throw new IllegalStateException("An active administrator already exists.");
            }
            if (repository.byEmail(input.email()) != null) throw new IllegalStateException("This email is already registered.");
            var member = repository.insert(input, hash, "ADMIN", "ACTIVE");
            repository.audit(null, member.id(), "BOOTSTRAP", null, "ADMIN");
            return BootstrapResult.CREATED;
        });
    }
}
