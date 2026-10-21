package com.negopilot.backend;

import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;
import org.springframework.context.ApplicationContext;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.transaction.support.TransactionTemplate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
@ActiveProfiles("bootstrap-admin")
@EnabledIfEnvironmentVariable(named = "TEST_DATABASE_URL", matches = ".+")
class AuthBootstrapLocalUserPostgresTest {
    private static final String EMAIL = "local-user@example.com";
    private static final String PASSWORD = "LocalUser2026!";
    @Autowired JdbcTemplate jdbc;
    @Autowired MembershipRepository members;
    @Autowired PasswordEncoder passwords;
    @Autowired TransactionTemplate transactions;
    @Autowired ApplicationContext context;

    @DynamicPropertySource static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", () -> System.getenv("TEST_DATABASE_URL"));
        registry.add("spring.datasource.username", () -> System.getenv("TEST_DATABASE_USER"));
        registry.add("spring.datasource.password", () -> System.getenv().getOrDefault("TEST_DATABASE_PASSWORD", ""));
        registry.add("agent.internal-api-key", () -> "");
    }

    @BeforeEach void reset() {
        jdbc.execute("TRUNCATE app_users,app_access_logs,app_membership_audit,spring_session CASCADE");
        seed("admin@example.com", "ADMIN", "ACTIVE");
    }

    @Test
    @ExtendWith(OutputCaptureExtension.class)
    void createsApprovedUserWithHashedPasswordAndOneCredentialFreeAudit(CapturedOutput output) {
        var input = input(PASSWORD);
        var result = AuthBootstrapLocalUser.createLocalUser(input, members, passwords, transactions);
        System.out.println(result.message());
        assertThat(result).isEqualTo(AuthBootstrapLocalUser.BootstrapResult.CREATED);
        var member = members.byEmail(EMAIL);
        assertThat(member.role()).isEqualTo("USER");
        assertThat(member.status()).isEqualTo("ACTIVE");
        assertThat(member.passwordHash()).isNotEqualTo(PASSWORD);
        assertThat(passwords.matches(PASSWORD, member.passwordHash())).isTrue();
        assertThat(member.name()).isEqualTo("로컬 테스트 회원");
        assertThat(member.department()).isEqualTo("개발팀");
        assertThat(context.containsBean("membershipLogRetention")).isFalse();
        var audit = jdbc.queryForMap("SELECT actor_id,action,old_value,new_value FROM app_membership_audit WHERE target_id=?", member.id());
        assertThat(audit).containsEntry("actor_id", null).containsEntry("action", "LOCAL_USER_BOOTSTRAP")
                .containsEntry("old_value", null).containsEntry("new_value", "USER");
        assertThat(audit.toString()).doesNotContain(PASSWORD, member.passwordHash());
        assertThat(output.getAll()).contains("Local test user created").doesNotContain(PASSWORD, member.passwordHash(), EMAIL);
    }

    @Test void repeatedInitializationPreservesAllExistingFieldsAndDoesNotDuplicateAudit() {
        AuthBootstrapLocalUser.createLocalUser(input(PASSWORD), members, passwords, transactions);
        var original = members.byEmail(EMAIL);
        jdbc.update("UPDATE app_users SET name='Existing name',department='Existing department',job_title='Existing title' WHERE id=?", original.id());
        var before = members.byEmail(EMAIL);
        assertThat(AuthBootstrapLocalUser.createLocalUser(input("DifferentPassword2!"), members, passwords, transactions))
                .isEqualTo(AuthBootstrapLocalUser.BootstrapResult.SKIPPED);
        assertThat(members.byEmail(EMAIL)).isEqualTo(before);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM app_users WHERE email=?", Integer.class, EMAIL)).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM app_membership_audit WHERE target_id=?", Integer.class, before.id())).isEqualTo(1);
    }

    @Test void existingActiveUserIsSkippedWithoutInventingBootstrapAudit() {
        UUID id = seed(EMAIL, "USER", "ACTIVE");
        var before = members.byId(id);
        assertThat(AuthBootstrapLocalUser.createLocalUser(input(PASSWORD), members, passwords, transactions))
                .isEqualTo(AuthBootstrapLocalUser.BootstrapResult.SKIPPED);
        assertThat(members.byId(id)).isEqualTo(before);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM app_membership_audit", Integer.class)).isZero();
    }

    @ParameterizedTest
    @CsvSource({"USER,PENDING", "USER,REJECTED", "USER,DISABLED", "ADMIN,ACTIVE",
            "ADMIN,PENDING", "ADMIN,REJECTED", "ADMIN,DISABLED"})
    void conflictingMembershipIsNeverPromotedReactivatedOrDowngraded(String role, String status) {
        UUID id = seed(EMAIL, role, status);
        var before = members.byId(id);
        assertThatThrownBy(() -> AuthBootstrapLocalUser.createLocalUser(input(PASSWORD), members, passwords, transactions))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("not an active USER")
                .hasMessageNotContaining(EMAIL).hasMessageNotContaining(PASSWORD).hasMessageNotContaining(before.passwordHash());
        assertThat(members.byId(id)).isEqualTo(before);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM app_membership_audit", Integer.class)).isZero();
    }

    @Test void requiresAnActiveAdministratorBeforeCreatingOrSkippingUser() {
        jdbc.update("UPDATE app_users SET status='DISABLED' WHERE role='ADMIN'");
        assertThatThrownBy(() -> AuthBootstrapLocalUser.createLocalUser(input(PASSWORD), members, passwords, transactions))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("active administrator is required");
        assertThat(members.byEmail(EMAIL)).isNull();
        UUID id = seed(EMAIL, "USER", "ACTIVE");
        var before = members.byId(id);
        assertThatThrownBy(() -> AuthBootstrapLocalUser.createLocalUser(input(PASSWORD), members, passwords, transactions))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("active administrator is required");
        assertThat(members.byId(id)).isEqualTo(before);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM app_membership_audit", Integer.class)).isZero();
    }

    @Test void concurrentInitializationsCreateOnlyOneUserAndOneAudit() throws Exception {
        CountDownLatch start = new CountDownLatch(1);
        Callable<AuthBootstrapLocalUser.BootstrapResult> initialize = () -> {
            start.await();
            return AuthBootstrapLocalUser.createLocalUser(input(PASSWORD), members, passwords, transactions);
        };
        try (var executor = Executors.newFixedThreadPool(2)) {
            var first = executor.submit(initialize);
            var second = executor.submit(initialize);
            start.countDown();
            assertThat(List.of(first.get(10, TimeUnit.SECONDS), second.get(10, TimeUnit.SECONDS)))
                    .containsExactlyInAnyOrder(AuthBootstrapLocalUser.BootstrapResult.CREATED, AuthBootstrapLocalUser.BootstrapResult.SKIPPED);
        }
        var member = members.byEmail(EMAIL);
        assertThat(member.role()).isEqualTo("USER");
        assertThat(member.status()).isEqualTo("ACTIVE");
        assertThat(jdbc.queryForObject("SELECT count(*) FROM app_users WHERE email=?", Integer.class, EMAIL)).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM app_membership_audit WHERE target_id=?", Integer.class, member.id())).isEqualTo(1);
    }

    @Test void auditFailureRollsBackTheInsertedUser() {
        var failAudit = new MembershipRepository(jdbc) {
            @Override void audit(UUID actor, UUID target, String action, String oldValue, String newValue) {
                throw new IllegalStateException("Audit unavailable");
            }
        };
        assertThatThrownBy(() -> AuthBootstrapLocalUser.createLocalUser(input(PASSWORD), failAudit, passwords, transactions))
                .isInstanceOf(IllegalStateException.class).hasMessage("Audit unavailable");
        assertThat(members.byEmail(EMAIL)).isNull();
        assertThat(jdbc.queryForObject("SELECT count(*) FROM app_membership_audit", Integer.class)).isZero();
    }

    private static AuthRequestValidator.Signup input(String password) {
        return AuthBootstrapLocalUser.inputFromEnvironment(Map.of("LOCAL_USER_EMAIL", EMAIL, "LOCAL_USER_PASSWORD", password));
    }

    private UUID seed(String email, String role, String status) {
        UUID id = UUID.randomUUID();
        jdbc.update("INSERT INTO app_users(id,email,name,department,job_title,password_hash,role,status) VALUES (?,?,?,?,?,?,?,?)",
                id, email, "Existing member", "Existing department", "Existing title", passwords.encode("ExistingPassword1!"), role, status);
        return id;
    }
}
