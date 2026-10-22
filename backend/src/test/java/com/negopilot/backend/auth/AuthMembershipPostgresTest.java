package com.negopilot.backend.auth;

import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.UserIdentity;

import java.net.CookieManager;
import java.net.CookiePolicy;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@EnabledIfEnvironmentVariable(named = "TEST_DATABASE_URL", matches = ".+")
class AuthMembershipPostgresTest {
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final String PASSWORD = "CorrectPassword1!";
    private static final String HASH = new BCryptPasswordEncoder().encode(PASSWORD);
    @LocalServerPort int port;
    @Autowired JdbcTemplate jdbc;
    @Autowired MembershipRepository members;
    @Autowired MembershipService service;
    @Autowired CurrentUser current;
    @Autowired PasswordEncoder passwords;
    @Autowired TransactionTemplate transactions;
    UUID admin;
    UUID user;
    UUID pending;

    @DynamicPropertySource static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", () -> System.getenv("TEST_DATABASE_URL"));
        registry.add("spring.datasource.username", () -> System.getenv("TEST_DATABASE_USER"));
        registry.add("spring.datasource.password", () -> System.getenv().getOrDefault("TEST_DATABASE_PASSWORD", ""));
        registry.add("server.servlet.session.cookie.secure", () -> "false");
        registry.add("agent.internal-api-key", () -> "");
    }
    @BeforeEach void reset() {
        jdbc.execute("TRUNCATE app_users,app_access_logs,app_membership_audit,spring_session CASCADE");
        admin = seed("admin@example.com", "ADMIN", "ACTIVE");
        user = seed("user@example.com", "USER", "ACTIVE");
        pending = seed("pending@example.com", "USER", "PENDING");
    }

    @Test void csrfIsRequiredEvenForSignupAndLogin() throws Exception {
        try (var browser = new Browser()) {
            assertError(browser.send("POST", "/api/v1/auth/signup", signup("new@example.com"), false), 403, "CSRF_INVALID");
            assertError(browser.send("POST", "/api/v1/auth/login", credentials("admin@example.com", PASSWORD), false), 403, "CSRF_INVALID");
            browser.csrf();
            ObjectNode body = signup("  New@Example.com  ");
            var response = browser.send("POST", "/api/v1/auth/signup", body, true);
            assertThat(response.statusCode()).as(response.body()).isEqualTo(201);
            JsonNode created = json(response).get("user");
            assertPublicMember(created);
            assertThat(created.get("email").stringValue()).isEqualTo("new@example.com");
            assertThat(created.get("role").stringValue()).isEqualTo("USER");
            assertThat(created.get("status").stringValue()).isEqualTo("PENDING");
            assertThat(response.body()).doesNotContain(PASSWORD, "password_hash", "$2a$");
            assertError(browser.send("POST", "/api/v1/auth/signup", signup("NEW@example.com"), true), 409, "EMAIL_ALREADY_REGISTERED");
            assertError(browser.send("POST", "/api/v1/auth/signup", signup("x@example.com").put("role", "ADMIN"), true), 400, "VALIDATION_ERROR");
            String hash = jdbc.queryForObject("SELECT password_hash FROM app_users WHERE email='new@example.com'", String.class);
            assertThat(new BCryptPasswordEncoder().matches(PASSWORD, hash)).isTrue();
        }
    }

    @Test void sessionsRotateAndPendingIsLimitedUntilAdminApproval() throws Exception {
        try (var applicant = new Browser(); var administrator = new Browser()) {
            assertError(applicant.get("/api/v2/business/catalog"), 401, "UNAUTHENTICATED");
            applicant.csrf();
            String before = applicant.cookie();
            String oldCsrf = applicant.token;
            assertThat(applicant.send("POST", "/api/v1/auth/login", credentials("pending@example.com", PASSWORD), true).statusCode()).isEqualTo(200);
            assertThat(applicant.cookie()).isNotEqualTo(before);
            assertThat(applicant.get("/api/v1/auth/me").statusCode()).isEqualTo(200);
            assertError(applicant.get("/api/v1/admin/members"), 403, "FORBIDDEN");
            assertError(applicant.get("/api/v2/business/catalog"), 403, "FORBIDDEN");
            applicant.token = oldCsrf;
            assertError(applicant.send("POST", "/api/v1/auth/logout", null, true), 403, "CSRF_INVALID");
            applicant.csrf();
            assertError(applicant.send("POST", "/api/v2/conversations", context(), true), 403, "FORBIDDEN");
            administrator.login("admin@example.com");
            String beforeApproval = applicant.cookie();
            var approval = administrator.send("PATCH", "/api/v1/admin/members/" + pending + "/status", JSON.createObjectNode().put("status", "ACTIVE"), true);
            assertThat(approval.statusCode()).as(approval.body()).isEqualTo(200);
            assertPublicMember(json(approval).get("user"));
            var me = applicant.get("/api/v1/auth/me");
            assertThat(me.statusCode()).as(me.body()).isEqualTo(200);
            assertPublicMember(json(me).get("user"));
            assertThat(json(me).at("/user/status").stringValue()).isEqualTo("ACTIVE");
            assertThat(applicant.cookie()).isNotEqualTo(beforeApproval);
            assertThat(applicant.send("POST", "/api/v2/conversations", context(), true).statusCode()).isEqualTo(201);
            assertThat(jdbc.queryForObject("SELECT count(*) FROM spring_session WHERE principal_name=?", Integer.class, pending.toString())).isEqualTo(1);
        }
    }

    @Test void disableRevokesEverySessionAndPreservesMemberAndAudit() throws Exception {
        try (var first = new Browser(); var second = new Browser(); var administrator = new Browser()) {
            first.login("user@example.com"); second.login("user@example.com"); administrator.login("admin@example.com");
            assertThat(jdbc.queryForObject("SELECT count(*) FROM spring_session WHERE principal_name=?", Integer.class, user.toString())).isEqualTo(2);
            assertThat(administrator.send("PATCH", "/api/v1/admin/members/" + user + "/status", JSON.createObjectNode().put("status", "DISABLED"), true).statusCode()).isEqualTo(200);
            assertError(first.get("/api/v1/auth/me"), 401, "UNAUTHENTICATED");
            assertError(second.get("/api/v1/auth/me"), 401, "UNAUTHENTICATED");
            assertThat(jdbc.queryForObject("SELECT count(*) FROM app_users WHERE id=?", Integer.class, user)).isEqualTo(1);
            assertThat(jdbc.queryForObject("SELECT count(*) FROM app_membership_audit WHERE target_id=?", Integer.class, user)).isEqualTo(1);
            first.csrf();
            assertError(first.send("POST", "/api/v1/auth/login", credentials("user@example.com", PASSWORD), true), 401, "INVALID_CREDENTIALS");
            assertThat(administrator.send("PATCH", "/api/v1/admin/members/" + user + "/status", JSON.createObjectNode().put("status", "ACTIVE"), true).statusCode()).isEqualTo(200);
            assertThat(first.send("POST", "/api/v1/auth/login", credentials("user@example.com", PASSWORD), true).statusCode()).isEqualTo(200);
        }
    }

    @Test void rejectsPendingAllowsAdminReapprovalAndNeverAllowsSelfChange() throws Exception {
        try (var administrator = new Browser(); var applicant = new Browser()) {
            administrator.login("admin@example.com"); applicant.login("pending@example.com");
            assertThat(administrator.send("PATCH", "/api/v1/admin/members/" + pending + "/status", JSON.createObjectNode().put("status", "REJECTED"), true).statusCode()).isEqualTo(200);
            assertError(applicant.get("/api/v1/auth/me"), 401, "UNAUTHENTICATED");
            applicant.csrf();
            assertError(applicant.send("POST", "/api/v1/auth/login", credentials("pending@example.com", PASSWORD), true), 401, "INVALID_CREDENTIALS");
            assertThat(administrator.send("PATCH", "/api/v1/admin/members/" + pending + "/status", JSON.createObjectNode().put("status", "ACTIVE"), true).statusCode()).isEqualTo(200);
            assertError(administrator.send("PATCH", "/api/v1/admin/members/" + admin + "/role", JSON.createObjectNode().put("role", "USER"), true), 409, "SELF_CHANGE_FORBIDDEN");
            assertError(administrator.send("PATCH", "/api/v1/admin/members/" + admin + "/status", JSON.createObjectNode().put("status", "DISABLED"), true), 409, "SELF_CHANGE_FORBIDDEN");
        }
    }

    @ParameterizedTest
    @CsvSource({
            "PENDING,PENDING,true", "PENDING,ACTIVE,true", "PENDING,REJECTED,true", "PENDING,DISABLED,false",
            "ACTIVE,PENDING,false", "ACTIVE,ACTIVE,true", "ACTIVE,REJECTED,false", "ACTIVE,DISABLED,true",
            "REJECTED,PENDING,false", "REJECTED,ACTIVE,true", "REJECTED,REJECTED,true", "REJECTED,DISABLED,false",
            "DISABLED,PENDING,false", "DISABLED,ACTIVE,true", "DISABLED,REJECTED,false", "DISABLED,DISABLED,true"
    })
    void statusTransitionsPreserveTheirValidationAndSideEffects(String from, String to, boolean allowed) throws Exception {
        try (var administrator = new Browser(); var target = new Browser()) {
            administrator.login("admin@example.com"); target.login("user@example.com");
            // Keep an existing session to exercise state changes made outside the membership API.
            members.changeStatus(user, from);
            var before = members.byId(user);
            var sessionsBefore = sessionIds(user);
            assertThat(sessionsBefore).hasSize(1);
            var response = administrator.send("PATCH", "/api/v1/admin/members/" + user + "/status",
                    JSON.createObjectNode().put("status", to), true);
            if (!allowed) {
                assertError(response, 409, "INVALID_STATUS_TRANSITION");
            } else {
                assertThat(response.statusCode()).as(response.body()).isEqualTo(200);
                assertPublicMember(json(response).get("user"));
                assertThat(json(response).at("/user/status").stringValue()).isEqualTo(to);
            }
            if (!allowed || from.equals(to)) {
                assertThat(members.byId(user)).isEqualTo(before);
                assertThat(sessionIds(user)).isEqualTo(sessionsBefore);
                assertThat(jdbc.queryForObject("SELECT count(*) FROM app_membership_audit WHERE target_id=?", Integer.class, user)).isZero();
            } else {
                assertThat(members.byId(user).status()).isEqualTo(to);
                var audit = jdbc.queryForMap("SELECT actor_id,action,old_value,new_value FROM app_membership_audit WHERE target_id=?", user);
                assertThat(audit).containsEntry("actor_id", admin).containsEntry("action", "STATUS_CHANGED")
                        .containsEntry("old_value", from).containsEntry("new_value", to);
                if (to.equals("DISABLED") || to.equals("REJECTED")) assertThat(sessionIds(user)).isEmpty();
                else assertThat(sessionIds(user)).isEqualTo(sessionsBefore);
            }
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {"PENDING", "ACTIVE", "REJECTED", "DISABLED"})
    void sameRoleIsANoOpEvenForInactiveMembers(String status) throws Exception {
        try (var administrator = new Browser(); var target = new Browser()) {
            administrator.login("admin@example.com"); target.login("user@example.com");
            members.changeStatus(user, status);
            var before = members.byId(user);
            var sessionsBefore = sessionIds(user);
            assertThat(sessionsBefore).hasSize(1);
            var response = administrator.send("PATCH", "/api/v1/admin/members/" + user + "/role",
                    JSON.createObjectNode().put("role", "USER"), true);
            assertThat(response.statusCode()).as(response.body()).isEqualTo(200);
            assertPublicMember(json(response).get("user"));
            assertThat(json(response).at("/user/role").stringValue()).isEqualTo("USER");
            if (!status.equals("ACTIVE")) {
                assertError(administrator.send("PATCH", "/api/v1/admin/members/" + user + "/role",
                        JSON.createObjectNode().put("role", "ADMIN"), true), 409, "MEMBER_NOT_ACTIVE");
            }
            assertThat(members.byId(user)).isEqualTo(before);
            assertThat(sessionIds(user)).isEqualTo(sessionsBefore);
            assertThat(jdbc.queryForObject("SELECT count(*) FROM app_membership_audit WHERE target_id=?", Integer.class, user)).isZero();
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {"status", "role"})
    void auditFailureRollsBackMembershipSessionRevocationAndAudit(String field) throws Exception {
        try (var target = new Browser()) {
            target.login("user@example.com");
            var before = members.byId(user);
            var sessionsBefore = sessionIds(user);
            assertThat(sessionsBefore).hasSize(1);
            var failingAudit = new MembershipRepository(jdbc) {
                @Override void audit(UUID actor, UUID targetId, String action, String oldValue, String newValue) {
                    assertThat(sessionIds(targetId)).isEmpty();
                    assertThat(field.equals("status") ? byId(targetId).status() : byId(targetId).role()).isEqualTo(newValue);
                    super.audit(actor, targetId, action, oldValue, newValue);
                    throw new IllegalStateException("Audit unavailable");
                }
            };
            var failingService = new MembershipService(failingAudit, current, new AuthSessionService(jdbc), transactions);
            SecurityContextHolder.getContext().setAuthentication(AuthSessionService.authentication(new UserIdentity(admin, "ADMIN", "ACTIVE")));
            try {
                assertThatThrownBy(() -> {
                    if (field.equals("status")) failingService.changeStatus(user, "DISABLED");
                    else failingService.changeRole(user, "ADMIN");
                }).isInstanceOf(IllegalStateException.class).hasMessage("Audit unavailable");
            } finally { SecurityContextHolder.clearContext(); }
            assertThat(members.byId(user)).isEqualTo(before);
            assertThat(sessionIds(user)).isEqualTo(sessionsBefore);
            assertThat(jdbc.queryForObject("SELECT count(*) FROM app_membership_audit WHERE target_id=?", Integer.class, user)).isZero();
            var me = target.get("/api/v1/auth/me");
            assertThat(me.statusCode()).as(me.body()).isEqualTo(200);
            assertThat(json(me).at("/user/id").stringValue()).isEqualTo(user.toString());
        }
    }

    @Test void roleChangesRevokeSessionsAndRefreshPreventsStalePrivileges() throws Exception {
        UUID otherAdmin = seed("other@example.com", "ADMIN", "ACTIVE");
        try (var administrator = new Browser(); var target = new Browser()) {
            administrator.login("admin@example.com"); target.login("other@example.com");
            assertThat(administrator.send("PATCH", "/api/v1/admin/members/" + otherAdmin + "/role", JSON.createObjectNode().put("role", "USER"), true).statusCode()).isEqualTo(200);
            assertError(target.get("/api/v1/admin/members"), 401, "UNAUTHENTICATED");
            target.login("other@example.com");
            assertError(target.get("/api/v1/admin/members"), 403, "FORBIDDEN");
            // Simulate an external status update without relying on session deletion.
            jdbc.update("UPDATE app_users SET status='DISABLED' WHERE id=?", admin);
            assertError(administrator.get("/api/v1/admin/members"), 401, "UNAUTHENTICATED");
        }
    }

    @Test void memberAndLogQueriesAreAdminOnlyAndNeverExposePasswords() throws Exception {
        try (var administrator = new Browser(); var standard = new Browser()) {
            administrator.login("admin@example.com"); standard.login("user@example.com");
            assertError(standard.get("/api/v1/admin/members"), 403, "FORBIDDEN");
            assertError(standard.get("/api/v1/admin/access-logs"), 403, "FORBIDDEN");
            var response = administrator.get("/api/v1/admin/members?search=pending&role=USER&page=0&size=1");
            assertThat(response.statusCode()).isEqualTo(200);
            assertThat(json(response).get("total").intValue()).isEqualTo(1);
            assertThat(json(response).at("/items/0/id").stringValue()).isEqualTo(pending.toString());
            assertPublicMember(json(response).at("/items/0"));
            assertThat(response.body()).doesNotContain("password", HASH);
            var detail = administrator.get("/api/v1/admin/members/" + pending);
            assertThat(detail.statusCode()).as(detail.body()).isEqualTo(200);
            assertPublicMember(json(detail).get("user"));
            assertThat(json(detail).get("user")).isEqualTo(json(response).at("/items/0"));
            assertError(administrator.get("/api/v1/admin/members?size=101"), 400, "VALIDATION_ERROR");
            assertError(administrator.get("/api/v1/admin/members?role=TEAM_LEADER"), 400, "VALIDATION_ERROR");
            assertError(administrator.get("/api/v1/admin/members?page=0&page=1"), 400, "VALIDATION_ERROR");
            assertError(administrator.get("/api/v1/admin/access-logs?from=2026-01-02T00:00:00Z&to=2026-01-01T00:00:00Z"), 400, "VALIDATION_ERROR");
            assertError(administrator.get("/api/v1/admin/access-logs?success=yes"), 400, "VALIDATION_ERROR");
        }
    }

    @Test void recordsOnlyLoginOutcomesAndLogoutAndPurgesOnlyExpiredAccessLogs() throws Exception {
        try (var browser = new Browser(); var administrator = new Browser()) {
            browser.csrf();
            assertError(browser.send("POST", "/api/v1/auth/login", credentials("unknown@example.com", "WrongPass1!"), true), 401, "INVALID_CREDENTIALS");
            browser.login("user@example.com");
            browser.get("/api/v1/auth/me");
            assertThat(browser.send("POST", "/api/v1/auth/logout", null, true).statusCode()).isEqualTo(204);
            assertError(browser.get("/api/v1/auth/me"), 401, "UNAUTHENTICATED");
            assertThat(jdbc.queryForList("SELECT event FROM app_access_logs ORDER BY occurred_at", String.class))
                    .containsExactly("LOGIN_FAILURE", "LOGIN_SUCCESS", "LOGOUT");
            administrator.login("admin@example.com");
            var logs = administrator.get("/api/v1/admin/access-logs?user_id=" + user + "&size=100");
            assertThat(logs.statusCode()).isEqualTo(200);
            assertThat(json(logs).get("total").intValue()).isEqualTo(2);
            assertThat(logs.body()).doesNotContain("password", PASSWORD, "WrongPass1!", "unknown@example.com");
            var failed = administrator.get("/api/v1/admin/access-logs?success=false");
            assertThat(json(failed).get("total").intValue()).isEqualTo(1);
            assertThat(json(failed).at("/items/0/event").stringValue()).isEqualTo("LOGIN_FAILURE");
            jdbc.update("UPDATE app_access_logs SET occurred_at=current_timestamp-interval '91 days' WHERE event='LOGIN_FAILURE'");
            members.audit(admin, user, "ROLE_CHANGED", "ADMIN", "USER");
            assertThat(members.deleteExpiredLogs()).isEqualTo(1);
            assertThat(jdbc.queryForObject("SELECT count(*) FROM app_membership_audit", Integer.class)).isEqualTo(1);
            assertThat(jdbc.queryForObject("SELECT count(*) FROM app_users", Integer.class)).isEqualTo(3);
        }
    }

    @Test void simultaneousAdminsCannotDisableEachOther() throws Exception {
        UUID second = seed("second@example.com", "ADMIN", "ACTIVE");
        CountDownLatch start = new CountDownLatch(1);
        Callable<Boolean> first = () -> changeAfterBarrier(admin, second, start);
        Callable<Boolean> other = () -> changeAfterBarrier(second, admin, start);
        try (var executor = Executors.newFixedThreadPool(2)) {
            var a = executor.submit(first); var b = executor.submit(other); start.countDown();
            assertThat(List.of(a.get(5, TimeUnit.SECONDS), b.get(5, TimeUnit.SECONDS))).containsExactlyInAnyOrder(true, false);
        }
        assertThat(members.activeAdminCount()).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM app_membership_audit", Integer.class)).isEqualTo(1);
    }

    @Test void authenticatedSessionsCannotSwitchIdentityOrRegisterAnotherMember() throws Exception {
        try (var browser = new Browser()) {
            browser.login("user@example.com");
            assertError(browser.send("POST", "/api/v1/auth/login", credentials("admin@example.com", PASSWORD), true), 403, "FORBIDDEN");
            assertError(browser.send("POST", "/api/v1/auth/signup", signup("another@example.com"), true), 403, "FORBIDDEN");
            assertThat(json(browser.get("/api/v1/auth/me")).at("/user/id").stringValue()).isEqualTo(user.toString());
            assertThat(jdbc.queryForObject("SELECT count(*) FROM app_access_logs WHERE event='LOGIN_SUCCESS'", Integer.class)).isEqualTo(1);
            assertThat(jdbc.queryForObject("SELECT count(*) FROM app_users", Integer.class)).isEqualTo(3);
        }
    }

    @Test void bootstrapSerializesTwoFirstAdministratorRequests() throws Exception {
        jdbc.execute("TRUNCATE app_users,app_access_logs,app_membership_audit,spring_session CASCADE");
        CountDownLatch start = new CountDownLatch(1);
        try (var executor = Executors.newFixedThreadPool(2)) {
            var first = executor.submit(() -> bootstrapAfterBarrier("first@example.com", start));
            var second = executor.submit(() -> bootstrapAfterBarrier("second@example.com", start));
            start.countDown();
            assertThat(List.of(first.get(10, TimeUnit.SECONDS), second.get(10, TimeUnit.SECONDS)))
                    .containsExactlyInAnyOrder(true, false);
        }
        assertThat(members.activeAdminCount()).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM app_users", Integer.class)).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM app_membership_audit WHERE action='BOOTSTRAP'", Integer.class)).isEqualTo(1);
    }

    @Test void idempotentBootstrapCreatesOnceAndPreservesExistingAdministrator() {
        jdbc.execute("TRUNCATE app_users,app_access_logs,app_membership_audit,spring_session CASCADE");
        var input = AuthRequestValidator.signup(signup("local@example.com"));
        assertThat(AuthBootstrapAdmin.createInitialAdministrator(input, members, passwords, transactions, true))
                .isEqualTo(AuthBootstrapAdmin.BootstrapResult.CREATED);
        var created = members.byEmail(input.email());
        assertThat(created.role()).isEqualTo("ADMIN");
        assertThat(created.status()).isEqualTo("ACTIVE");
        assertThat(passwords.matches(PASSWORD, created.passwordHash())).isTrue();

        var changed = AuthRequestValidator.signup(signup(input.email())
                .put("password", "ReplacementPassword2!").put("name", "Changed name").put("department", "Changed department"));
        assertThat(AuthBootstrapAdmin.createInitialAdministrator(changed, members, passwords, transactions, true))
                .isEqualTo(AuthBootstrapAdmin.BootstrapResult.SKIPPED);
        assertThat(members.byEmail(input.email())).isEqualTo(created);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM app_users", Integer.class)).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM app_membership_audit WHERE action='BOOTSTRAP'", Integer.class)).isEqualTo(1);
        assertThatThrownBy(() -> AuthBootstrapAdmin.createInitialAdministrator(input, members, passwords, transactions))
                .isInstanceOf(IllegalStateException.class).hasMessage("An active administrator already exists.");
    }

    @Test void idempotentBootstrapSkipsWhenAnotherActiveAdministratorExists() {
        var before = members.byId(admin);
        var input = AuthRequestValidator.signup(signup("new-admin@example.com"));
        assertThat(AuthBootstrapAdmin.createInitialAdministrator(input, members, passwords, transactions, true))
                .isEqualTo(AuthBootstrapAdmin.BootstrapResult.SKIPPED);
        assertThat(members.byEmail(input.email())).isNull();
        assertThat(members.byId(admin)).isEqualTo(before);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM app_users", Integer.class)).isEqualTo(3);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM app_membership_audit", Integer.class)).isZero();
    }

    @ParameterizedTest
    @CsvSource({"USER,ACTIVE", "USER,PENDING", "USER,REJECTED", "USER,DISABLED",
            "ADMIN,PENDING", "ADMIN,REJECTED", "ADMIN,DISABLED"})
    void idempotentBootstrapRejectsConflictingEmailEvenWhenAnotherAdminExists(String role, String status) {
        UUID conflicting = seed("local@example.com", role, status);
        var before = members.byId(conflicting);
        var input = AuthRequestValidator.signup(signup(before.email()));
        assertThatThrownBy(() -> AuthBootstrapAdmin.createInitialAdministrator(input, members, passwords, transactions, true))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("not an active administrator");
        assertThat(members.byId(conflicting)).isEqualTo(before);
        assertThat(members.activeAdminCount()).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM app_membership_audit", Integer.class)).isZero();
    }

    @Test void concurrentIdempotentBootstrapsCreateOnlyOneAdministrator() throws Exception {
        jdbc.execute("TRUNCATE app_users,app_access_logs,app_membership_audit,spring_session CASCADE");
        var input = AuthRequestValidator.signup(signup("local@example.com"));
        CountDownLatch start = new CountDownLatch(1);
        Callable<AuthBootstrapAdmin.BootstrapResult> bootstrap = () -> {
            start.await();
            return AuthBootstrapAdmin.createInitialAdministrator(input, members, passwords, transactions, true);
        };
        try (var executor = Executors.newFixedThreadPool(2)) {
            var first = executor.submit(bootstrap);
            var second = executor.submit(bootstrap);
            start.countDown();
            assertThat(List.of(first.get(10, TimeUnit.SECONDS), second.get(10, TimeUnit.SECONDS)))
                    .containsExactlyInAnyOrder(AuthBootstrapAdmin.BootstrapResult.CREATED, AuthBootstrapAdmin.BootstrapResult.SKIPPED);
        }
        assertThat(members.activeAdminCount()).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM app_users", Integer.class)).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM app_membership_audit WHERE action='BOOTSTRAP'", Integer.class)).isEqualTo(1);
    }

    private boolean bootstrapAfterBarrier(String email, CountDownLatch start) throws Exception {
        start.await();
        try {
            AuthBootstrapAdmin.createInitialAdministrator(AuthRequestValidator.signup(signup(email)), members, passwords, transactions);
            return true;
        } catch (IllegalStateException expected) { return false; }
    }

    private boolean changeAfterBarrier(UUID actor, UUID target, CountDownLatch start) throws Exception {
        SecurityContextHolder.getContext().setAuthentication(AuthSessionService.authentication(new UserIdentity(actor, "ADMIN", "ACTIVE")));
        try { start.await(); service.changeStatus(target, "DISABLED"); return true; }
        catch (ApiException expected) { return false; }
        finally { SecurityContextHolder.clearContext(); }
    }
    private UUID seed(String email, String role, String status) {
        UUID id = UUID.randomUUID();
        jdbc.update("INSERT INTO app_users(id,email,name,department,job_title,password_hash,role,status) VALUES (?,?,?,?,?,?,?,?)",
                id, email, "테스트 사용자", "구매", "담당", HASH, role, status);
        return id;
    }
    private List<String> sessionIds(UUID userId) {
        return jdbc.queryForList("SELECT session_id FROM spring_session WHERE principal_name=? ORDER BY session_id", String.class, userId.toString());
    }
    private static ObjectNode signup(String email) {
        return credentials(email, PASSWORD).put("name", "신청자").put("department", "구매").put("job_title", "담당");
    }
    private static ObjectNode credentials(String email, String password) { return JSON.createObjectNode().put("email", email).put("password", password); }
    private static ObjectNode context() { return JSON.createObjectNode().set("context", JSON.createObjectNode().set("selected_supplier_ids", JSON.createArrayNode())); }
    private static JsonNode json(HttpResponse<String> response) { return JSON.readTree(response.body()); }
    private static void assertPublicMember(JsonNode member) {
        assertThat(member).isNotNull();
        assertThat(member.propertyNames()).containsExactlyInAnyOrder(
                "id", "email", "name", "department", "job_title", "role", "status", "created_at", "updated_at");
    }
    private static void assertError(HttpResponse<String> response, int status, String code) {
        assertThat(response.statusCode()).as(response.body()).isEqualTo(status);
        assertThat(json(response).at("/error/code").stringValue()).isEqualTo(code);
    }
    private final class Browser implements AutoCloseable {
        final CookieManager cookies = new CookieManager(null, CookiePolicy.ACCEPT_ALL);
        final HttpClient client = HttpClient.newBuilder().cookieHandler(cookies).connectTimeout(Duration.ofSeconds(3)).build();
        String token;
        String header;
        HttpResponse<String> get(String path) throws Exception { return send("GET", path, null, false); }
        void csrf() throws Exception {
            var response = get("/api/v1/auth/csrf");
            assertThat(response.statusCode()).as(response.body()).isEqualTo(200);
            token = json(response).get("token").stringValue(); header = json(response).get("header_name").stringValue();
        }
        void login(String email) throws Exception {
            csrf();
            var response = send("POST", "/api/v1/auth/login", credentials(email, PASSWORD), true);
            assertThat(response.statusCode()).as(response.body()).isEqualTo(200);
            assertPublicMember(json(response).get("user"));
            csrf();
        }
        String cookie() { return cookies.getCookieStore().getCookies().stream().filter(c -> c.getName().equals("SESSION")).findFirst().orElseThrow().getValue(); }
        HttpResponse<String> send(String method, String path, JsonNode data, boolean csrf) throws Exception {
            var builder = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + path)).timeout(Duration.ofSeconds(10))
                    .header("User-Agent", "MembershipTest").header("Content-Type", "application/json");
            if (csrf) builder.header(header, token);
            return client.send(builder.method(method, data == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(data.toString())).build(), HttpResponse.BodyHandlers.ofString());
        }
        @Override public void close() { client.close(); }
    }
}
