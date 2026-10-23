package com.negopilot.backend.legalreview;

import static com.negopilot.backend.legalreview.LegalReviewUploadServiceTest.JSON;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.negopilot.backend.agent.AgentProperties;
import com.negopilot.backend.agent.GenerationGateway;
import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.negotiation.NegotiationTestSupport;
import com.negopilot.backend.supplier.SupplierLookupGateway;
import java.net.URI;
import java.nio.file.Path;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * 명시한 테스트 DB의 고유 스키마에서 V5 제약과, 엑셀 행 → 협상 작업 → 검토 저장 → 협상안 생성까지 이어지는지 검증한다.
 */
@EnabledIfEnvironmentVariable(named = "TEST_DATABASE_URL", matches = ".+")
class LegalReviewUploadPostgresTest {
    static JdbcTemplate admin;
    static JdbcTemplate jdbc;
    static DriverManagerDataSource datasource;
    static String schema;
    @TempDir Path directory;
    UUID owner;
    GenerationGateway gateway;
    LegalReviewUploadStore store;
    LegalReviewUploadService service;
    NegotiationTestSupport negotiations;
    final List<Runnable> pending = new ArrayList<>();

    @BeforeAll static void migrate() {
        String base = System.getenv("TEST_DATABASE_URL");
        if (!base.matches("jdbc:postgresql://[^/]+/[A-Za-z0-9_]*test(?:\\?.*)?"))
            throw new IllegalStateException("명시적으로 분리된 test DB만 사용할 수 있습니다.");
        String user = System.getenv("TEST_DATABASE_USER");
        String password = System.getenv().getOrDefault("TEST_DATABASE_PASSWORD", "");
        admin = new JdbcTemplate(new DriverManagerDataSource(base, user, password));
        schema = "legal_review_" + UUID.randomUUID().toString().replace("-", "");
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
        gateway = mock(GenerationGateway.class);
        SupplierLookupGateway suppliers = mock(SupplierLookupGateway.class);
        when(suppliers.detail(anyString(), any())).thenAnswer(inv -> JSON.readTree(
                "{\"data\":{\"supplier_id\":\"" + inv.getArgument(0) + "\",\"name\":\"새길다이캐스팅\"}}"));
        when(suppliers.detail(eq("SUP-0070"), any())).thenThrow(ApiException.supplierNotFound(UUID.randomUUID()));
        var agent = new AgentProperties(URI.create("http://agent.invalid"), "test", Duration.ofSeconds(1), Duration.ofSeconds(2));
        negotiations = new NegotiationTestSupport(jdbc, datasource, gateway, suppliers, agent, directory);
        store = new LegalReviewUploadStore(jdbc, JSON);
        var runner = new LegalReviewRunner(store, negotiations.reviews, agent, pending::add);
        service = new LegalReviewUploadService(store, runner, new TransactionTemplate(new DataSourceTransactionManager(datasource)), JSON);
    }

    private void runPending() {
        List<Runnable> now = new ArrayList<>(pending);
        pending.clear();
        now.forEach(Runnable::run);
    }

    private static List<Object> row(String supplier) {
        List<Object> row = new ArrayList<>(ReviewWorkbookParserTest.validRow());
        row.set(0, supplier);
        return row;
    }

    @Test void uploadedRowReviewIsStoredInNegotiationAndCanBecomeProposal() {
        List<Object> bad = row("SUP-0034");
        bad.set(2, "내일");
        byte[] file = TestXlsx.of(List.of(ReviewWorkbookParserTest.headers(), row("SUP-0034"), bad, row("SUP-0070")));
        when(gateway.review(any(), any())).thenAnswer(inv -> NegotiationTestSupport.reviewResponseFor(inv.getArgument(0), "ready"));
        when(gateway.propose(any(), any())).thenAnswer(inv -> NegotiationTestSupport.proposalResponseFor(inv.getArgument(0)));

        UUID id = UUID.fromString(service.upload(owner, "조건.xlsx", file, UUID.randomUUID()).get("upload_id").stringValue());
        assertThat(jdbc.queryForObject("SELECT status FROM legal_review_rows WHERE upload_id=? AND row_number=3", String.class, id)).isEqualTo("INVALID");
        runPending();

        JsonNode rows = service.get(owner, id, UUID.randomUUID()).get("rows");
        JsonNode done = rows.get(0);
        assertThat(done.get("status").stringValue()).isEqualTo("COMPLETED");
        assertThat(done.get("eligibility").stringValue()).isEqualTo("ready");
        UUID workspace = UUID.fromString(done.get("negotiation_id").stringValue());
        int revision = done.get("review_revision").intValue();
        assertThat(revision).isEqualTo(1);
        // 검토는 협상 작업 테이블에만 저장된다. 토큰도 그쪽 서버 전용 열에만 있다
        assertThat(jdbc.queryForObject("SELECT title FROM neg_workspaces WHERE id=?", String.class, workspace))
                .isEqualTo("RFQ-2026-001 · 새길다이캐스팅 · 조건.xlsx 2행");
        assertThat(jdbc.queryForObject("SELECT input_snapshot->'transaction'->>'transaction_type' FROM neg_review_revisions WHERE workspace_id=? AND revision=1",
                String.class, workspace)).isEqualTo("manufacturing");
        assertThat(jdbc.queryForObject("SELECT review_token IS NOT NULL FROM neg_review_revisions WHERE workspace_id=?", Boolean.class, workspace)).isTrue();
        assertThat(service.row(owner, id, 2, UUID.randomUUID()).toString()).doesNotContain("review_token");
        assertThat(rows.get(2).get("error").get("code").stringValue()).isEqualTo("SUPPLIER_NOT_FOUND");

        // ② 검토 결과 → ③ 협상안 만들기
        assertThat(negotiations.propose(owner, workspace, revision)).isEqualTo(201);

        // 조건을 바꾼 재검토는 같은 작업에 버전을 쌓는다. 행에는 업로드로 만든 버전과 현재 최신 버전이 함께 보인다
        ObjectNode changed = (ObjectNode) jdbc.queryForObject("SELECT offer::text FROM legal_review_rows WHERE upload_id=? AND row_number=2",
                (rs, n) -> JSON.readTree(rs.getString(1)), id);
        changed.put("payment_days", 45);
        assertThat(negotiations.reviews.review(owner, workspace, changed, UUID.randomUUID()).status()).isEqualTo(201);
        JsonNode after = service.row(owner, id, 2, UUID.randomUUID());
        assertThat(after.get("review_revision").intValue()).isEqualTo(1);
        assertThat(after.get("review_head_revision").intValue()).isEqualTo(2);
        assertThat(jdbc.queryForList("SELECT payment_days FROM neg_review_revisions WHERE workspace_id=? ORDER BY revision", Integer.class, workspace))
                .containsExactly(60, 45);
        assertThat(service.list(UUID.randomUUID(), 20).get("items")).isEmpty();
    }

    @Test void stateTransitionsAndConstraints() {
        when(gateway.review(any(), any())).thenAnswer(inv -> NegotiationTestSupport.reviewResponseFor(inv.getArgument(0), "needs_information"));
        byte[] file = TestXlsx.of(List.of(ReviewWorkbookParserTest.headers(), row("SUP-0034")));
        UUID id = UUID.fromString(service.upload(owner, "조건.xlsx", file, UUID.randomUUID()).get("upload_id").stringValue());
        var key = new LegalReviewUploadStore.Key(id, 2);

        assertThat(store.claim(key, OffsetDateTime.now().minusSeconds(1), UUID.randomUUID())).isTrue();
        assertThat(store.claim(key, OffsetDateTime.now().plusMinutes(1), UUID.randomUUID())).isFalse();
        assertThat(store.expireRunning(false)).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT error_code FROM legal_review_rows WHERE upload_id=?", String.class, id)).isEqualTo("STALE_REVIEW");
        assertThat(store.finish(key, JSON.createObjectNode().put("status", "FAILED").put("error_code", "X"))).isFalse();
        runPending();
        assertThat(jdbc.queryForObject("SELECT count(*) FROM neg_workspaces WHERE owner_id=?", Integer.class, owner)).isZero();

        // 재시도하면 대기열로 돌아가 검토를 마친다
        service.retry(owner, id, 2, UUID.randomUUID());
        runPending();
        JsonNode row = service.row(owner, id, 2, UUID.randomUUID());
        assertThat(row.get("status").stringValue()).isEqualTo("COMPLETED");
        assertThat(row.get("eligibility").stringValue()).isEqualTo("conditional");
        assertThat(row.get("attempts").intValue()).isEqualTo(2);

        assertThatThrownBy(() -> jdbc.update("INSERT INTO legal_review_rows(upload_id,row_number,status,source_cells,offer,created_by)"
                + " VALUES (?,5,'INVALID','{}','{}',?)", id, owner)).isInstanceOf(DataIntegrityViolationException.class);
        assertThatThrownBy(() -> jdbc.update("INSERT INTO legal_review_rows(upload_id,row_number,status,source_cells,offer,created_by)"
                + " VALUES (?,6,'COMPLETED','{}','{}',?)", id, owner)).isInstanceOf(DataIntegrityViolationException.class);
        assertThatThrownBy(() -> jdbc.update("INSERT INTO legal_review_rows(upload_id,row_number,status,source_cells,offer,negotiation_id,review_revision,created_by)"
                + " VALUES (?,7,'COMPLETED','{}','{}',?,9,?)", id, UUID.fromString(row.get("negotiation_id").stringValue()), owner))
                .isInstanceOf(DataIntegrityViolationException.class);
    }
}
