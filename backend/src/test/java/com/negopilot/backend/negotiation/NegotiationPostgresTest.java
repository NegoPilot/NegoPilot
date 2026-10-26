package com.negopilot.backend.negotiation;

import com.negopilot.backend.agent.AgentProperties;
import com.negopilot.backend.agent.GenerationGateway;
import com.negopilot.backend.agent.LegalReviewRequestFactory;
import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.supplier.SupplierLookupGateway;
import java.net.URI;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;
import static com.negopilot.backend.negotiation.NegotiationFixtures.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/** 명시한 테스트 DB의 고유 스키마에서 V1~V3와 실제 행 잠금을 검증한다. */
@EnabledIfEnvironmentVariable(named = "TEST_DATABASE_URL", matches = ".+")
class NegotiationPostgresTest {
    static JdbcTemplate admin;
    static JdbcTemplate jdbc;
    static DriverManagerDataSource datasource;
    static String schema;
    @TempDir Path directory;
    UUID owner;
    GenerationGateway gateway;
    NegotiationService service;

    @BeforeAll static void migrate() {
        String base = System.getenv("TEST_DATABASE_URL");
        if (!base.matches("jdbc:postgresql://[^/]+/[A-Za-z0-9_]*test(?:\\?.*)?"))
            throw new IllegalStateException("명시적으로 분리된 test DB만 사용할 수 있습니다.");
        String user = System.getenv("TEST_DATABASE_USER");
        String password = System.getenv().getOrDefault("TEST_DATABASE_PASSWORD", "");
        admin = new JdbcTemplate(new DriverManagerDataSource(base, user, password));
        schema = "negotiation_" + UUID.randomUUID().toString().replace("-", "");
        String url = base + (base.contains("?") ? "&" : "?") + "currentSchema=" + schema;
        datasource = new DriverManagerDataSource(url, user, password);
        Flyway.configure().dataSource(datasource).schemas(schema).defaultSchema(schema)
                .locations("classpath:db/migration").load().migrate();
        jdbc = new JdbcTemplate(datasource);
    }

    @AfterAll static void cleanup() {
        if (admin != null && schema != null) admin.execute("DROP SCHEMA IF EXISTS " + schema + " CASCADE");
    }

    @BeforeEach void setup() {
        owner = UUID.randomUUID();
        jdbc.update("INSERT INTO app_users(id,email,name,department,job_title,password_hash,role,status) VALUES (?,?,?,'구매팀','매니저','unused','USER','ACTIVE')",
                owner, owner + "@example.com", "담당자");
        var repository = new NegotiationRepository(jdbc, JSON);
        var transaction = new TransactionTemplate(new DataSourceTransactionManager(datasource));
        var properties = new AgentProperties(URI.create("http://agent.invalid"), "test", Duration.ofSeconds(1), Duration.ofSeconds(2));
        gateway = mock(GenerationGateway.class);
        SupplierLookupGateway suppliers = mock(SupplierLookupGateway.class);
        when(suppliers.detail(anyString(), any())).thenReturn(object("{\"data\":{\"supplier_id\":\"SUP-0060\",\"name\":\"온새프레스\"}}"));
        when(gateway.review(any(), any())).thenAnswer(call -> {
            assertThat(TransactionSynchronizationManager.isActualTransactionActive()).isFalse();
            return reviewResponse(call.getArgument(0), "ready");
        });
        when(gateway.propose(any(), any())).thenAnswer(call -> {
            assertThat(TransactionSynchronizationManager.isActualTransactionActive()).isFalse();
            return proposalResponse(call.getArgument(0));
        });
        when(gateway.render(any(), any(), anyString(), anyBoolean())).thenAnswer(call -> {
            assertThat(TransactionSynchronizationManager.isActualTransactionActive()).isFalse();
            return renderResponse(call.getArgument(0), call.getArgument(1), call.getArgument(2));
        });
        service = new NegotiationService(repository, transaction, gateway, suppliers, new LegalReviewRequestFactory(JSON), JSON,
                new NegotiationRequests(repository, transaction, JSON, properties), new LocalNegotiationExportStore(directory), properties);
    }

    @Test void createEditRecheckAndExportPersistImmutableRevisionsAndExactlyOneActiveProposal() {
        UUID workspace = reviewed(); UUID proposal = proposal(workspace);
        service.patchProposal(owner, workspace, proposal, object("{\"base_revision\":1,\"changes\":{\"offer\":{\"payment_days\":45}}}"), null, UUID.randomUUID());
        service.validate(owner, workspace, proposal, 2, object("{}"), null, UUID.randomUUID());
        var exported = service.export(owner, workspace, proposal,
                object("{\"revision\":2,\"document_type\":\"external\",\"formats\":[\"pdf\",\"docx\"],\"final\":true}"), null, UUID.randomUUID());
        assertThat(exported.status()).isEqualTo(201);
        assertThat(jdbc.queryForList("SELECT payment_days FROM neg_proposal_revisions WHERE proposal_id=? ORDER BY revision", Integer.class, proposal))
                .containsExactly(60, 45);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM neg_validations WHERE proposal_id=? AND revision=2 AND status='checked'", Integer.class, proposal)).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM neg_export_bundles WHERE proposal_id=? AND status='ready'", Integer.class, proposal)).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM neg_export_files f JOIN neg_export_bundles b ON b.id=f.bundle_id WHERE b.proposal_id=?", Integer.class, proposal)).isEqualTo(2);
        assertThat(jdbc.queryForObject("SELECT response::text FROM neg_review_revisions WHERE workspace_id=? AND revision=1", String.class, workspace))
                .doesNotContain("review_token", TOKEN);
        assertThat(jdbc.queryForObject("SELECT review_token FROM neg_review_revisions WHERE workspace_id=? AND revision=1", String.class, workspace)).isEqualTo(TOKEN);
        UUID replacement = proposal(workspace);
        assertThat(replacement).isNotEqualTo(proposal);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM neg_proposals WHERE workspace_id=? AND status='ACTIVE'", Integer.class, workspace)).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT status FROM neg_proposals WHERE id=?", String.class, proposal)).isEqualTo("SUPERSEDED");
    }

    @Test void rewrittenCoverLetterIsStoredAsACoverSyncRevisionAndSentAsIs() {
        UUID workspace = reviewed(); UUID proposal = proposal(workspace);
        service.patchProposal(owner, workspace, proposal, object("{\"base_revision\":1,\"changes\":{\"offer\":{\"payment_days\":45}}}"), null, UUID.randomUUID());
        String fresh = "지급 조건은 목적물 수령일부터 45일 이내 현금 지급입니다.";
        doAnswer(call -> {
            ObjectNode response = renderResponse(call.getArgument(0), call.getArgument(1), call.getArgument(2));
            ((ObjectNode) response.get("document")).put("cover_letter", fresh);
            response.putObject("cover_letter").put("regenerated", true).put("source", "model").putArray("problems").add("입력에 없는 숫자 60");
            return response;
        }).when(gateway).render(any(), any(), anyString(), eq(true));
        var checked = service.validate(owner, workspace, proposal, 2, object("{}"), null, UUID.randomUUID());
        assertThat(checked.body().at("/cover_letter/revision").intValue()).isEqualTo(3);
        assertThat(jdbc.queryForList("SELECT origin FROM neg_proposal_revisions WHERE proposal_id=? ORDER BY revision", String.class, proposal))
                .containsExactly("GENERATED", "USER_EDIT", "COVER_SYNC");
        assertThat(jdbc.queryForObject("SELECT external_body->>'cover_letter' FROM neg_proposal_revisions WHERE proposal_id=? AND revision=3", String.class, proposal))
                .isEqualTo(fresh);
        assertThat(jdbc.queryForObject("SELECT head_revision FROM neg_proposals WHERE id=?", Integer.class, proposal)).isEqualTo(3);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM neg_validations WHERE proposal_id=? AND revision=3 AND status='checked'", Integer.class, proposal)).isEqualTo(1);
        var exported = service.export(owner, workspace, proposal,
                object("{\"revision\":3,\"document_type\":\"external\",\"formats\":[\"pdf\"],\"final\":true}"), null, UUID.randomUUID());
        assertThat(exported.status()).isEqualTo(201);
        assertThat(jdbc.queryForObject("SELECT rendered_document->>'cover_letter' FROM neg_export_bundles WHERE proposal_id=? AND status='ready'", String.class, proposal))
                .isEqualTo(fresh);
    }

    @Test void concurrentPatchAllowsExactlyOneWriter() throws Exception {
        UUID workspace = reviewed(); UUID proposal = proposal(workspace);
        CountDownLatch start = new CountDownLatch(1);
        try (var executor = Executors.newFixedThreadPool(2)) {
            var one = executor.submit(() -> patchResult(start, workspace, proposal, 45));
            var two = executor.submit(() -> patchResult(start, workspace, proposal, 30));
            start.countDown();
            assertThat(List.of(one.get(), two.get())).containsExactlyInAnyOrder("OK", "REVISION_CONFLICT");
        }
        assertThat(jdbc.queryForObject("SELECT count(*) FROM neg_proposal_revisions WHERE proposal_id=?", Integer.class, proposal)).isEqualTo(2);
        assertThat(jdbc.queryForObject("SELECT head_revision FROM neg_proposals WHERE id=?", Integer.class, proposal)).isEqualTo(2);
    }

    @Test void idempotencyPersistsResponseStatusAndAvoidsSecondAgentCall() {
        UUID workspace = workspace(); UUID key = UUID.randomUUID();
        var first = service.review(owner, workspace, reviewInput(), key, UUID.randomUUID());
        var same = service.review(owner, workspace, reviewInput(), key, UUID.randomUUID());
        assertThat(same.status()).isEqualTo(201); assertThat(same.body()).isEqualTo(first.body());
        assertThat(jdbc.queryForObject("SELECT state FROM neg_requests WHERE user_id=? AND idempotency_key=?", String.class, owner, key)).isEqualTo("DONE");
        verify(gateway, times(1)).review(any(), any());
    }

    private String patchResult(CountDownLatch start, UUID workspace, UUID proposal, int days) throws InterruptedException {
        start.await();
        try {
            service.patchProposal(owner, workspace, proposal,
                    object("{\"base_revision\":1,\"changes\":{\"offer\":{\"payment_days\":" + days + "}}}"), null, UUID.randomUUID());
            return "OK";
        } catch (ApiException failure) { return failure.code; }
    }
    private UUID workspace() {
        return id(service.create(owner, object("{\"supplier_id\":\"SUP-0060\",\"rfq_id\":\"RFQ-2026-002\"}"), null, UUID.randomUUID()).body(), "negotiation_id");
    }
    private UUID reviewed() {
        UUID workspace = workspace(); service.review(owner, workspace, reviewInput(), null, UUID.randomUUID()); return workspace;
    }
    private UUID proposal(UUID workspace) {
        return id(service.propose(owner, workspace, object("{\"review_revision\":1,\"offer\":{}}"), null, UUID.randomUUID()).body(), "proposal_id");
    }
    private static UUID id(JsonNode row, String key) { return UUID.fromString(row.path(key).asText()); }
}
