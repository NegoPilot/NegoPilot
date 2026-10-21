package com.negopilot.backend;

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
        Map<String, String> values = new HashMap<>();
        Set<String> fields = Set.of("email", "name", "department", "job_title");
        for (String arg : args) {
            int split = arg.indexOf('=');
            if (split < 3 || !arg.startsWith("--") || !fields.contains(arg.substring(2, split))
                    || values.put(arg.substring(2, split), arg.substring(split + 1)) != null) {
                throw new IllegalArgumentException("Use --email= --name= --department= and optional --job_title=. Password is read securely.");
            }
        }
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
            createInitialAdministrator(input, repository, context.getBean(PasswordEncoder.class), transactions);
            System.out.println("Initial administrator created.");
        }
    }

    static void createInitialAdministrator(AuthRequestValidator.Signup input, MembershipRepository repository,
                                           PasswordEncoder passwords, TransactionTemplate transactions) {
        String hash = passwords.encode(input.password());
        transactions.executeWithoutResult(tx -> {
            repository.serializeChanges();
            if (repository.activeAdminCount() > 0) throw new IllegalStateException("An active administrator already exists.");
            if (repository.byEmail(input.email()) != null) throw new IllegalStateException("This email is already registered.");
            var member = repository.insert(input, hash, "ADMIN", "ACTIVE");
            repository.audit(null, member.id(), "BOOTSTRAP", null, "ADMIN");
        });
    }
}
