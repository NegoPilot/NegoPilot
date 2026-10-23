package com.negopilot.backend.admin.supplier;

import com.negopilot.backend.common.api.ApiException;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Executors;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import static org.assertj.core.api.Assertions.*;

/** Uses a unique schema inside the explicitly provided disposable business_test database. */
@EnabledIfEnvironmentVariable(named = "TEST_DATABASE_URL", matches = ".+")
class AdminSupplierPostgresTest {
    static final JsonMapper JSON = JsonMapper.builder().build();
    static final UUID ACTOR = UUID.randomUUID();
    static BusinessDataStore store;
    static JdbcTemplate admin;
    static String schema, url, user, password;
    static AdminSupplierService service;
    static final AdminSupplierValidator validator = new AdminSupplierValidator(JSON);

    @BeforeAll static void prepare() throws Exception {
        String base = System.getenv("TEST_DATABASE_URL");
        if (!base.matches("jdbc:postgresql://[^/]+/business_test(?:\\?.*)?"))
            throw new IllegalStateException("Tests require the explicitly isolated business_test database.");
        user = System.getenv("TEST_DATABASE_USER"); password = System.getenv("TEST_DATABASE_PASSWORD");
        admin = new JdbcTemplate(new DriverManagerDataSource(base, user, password));
        schema = "admin_supplier_" + UUID.randomUUID().toString().replace("-", "");
        admin.execute("CREATE SCHEMA " + schema);
        url = base + (base.contains("?") ? "&" : "?") + "currentSchema=" + schema;
        JdbcTemplate seed = new JdbcTemplate(new DriverManagerDataSource(url, user, password));
        seed.execute(Files.readString(Path.of("../mcp-servers/business-mcp/schema.sql")));
        seed.update("INSERT INTO suppliers VALUES ('SUP-0001','DART','00123456','123456','30332','공시회사','부산',200,100,2001,'123-45-*****',true,false)");
        seed.update("INSERT INTO suppliers VALUES ('SUP-10000','MOCK',NULL,NULL,'30332','품질없는기업','경남',NULL,NULL,2020,'987-65-*****',false,false)");
        seed.update("INSERT INTO capabilities VALUES ('SUP-0001','프레스','SPCC',1000),('SUP-10000','프레스','SPCC',2000)");
        seed.update("INSERT INTO quality VALUES ('SUP-0001',10,0.975,'A',3)");
        seed.update("INSERT INTO financials VALUES ('SUP-0001',2025,'CFS','DART',9007199254740993,-100,0,900,600,300)");
        seed.update("INSERT INTO contracts VALUES ('SUP-0001',60,true,0.4,false)");
        seed.update("INSERT INTO quotes(supplier_id,process,material,unit_price,quoted_at) VALUES ('SUP-0001','프레스','SPCC',1000,'2026-01-01')");
        store = new BusinessDataStore(true, url, user, password);
        service = new AdminSupplierService(store, new AdminSupplierRepository(store, JSON));
    }
    @AfterAll static void cleanup() {
        if (store != null) store.close();
        if (admin != null && schema != null) admin.execute("DROP SCHEMA " + schema + " CASCADE");
    }
    static ObjectNode object(String text) { return (ObjectNode) JSON.readTree(text); }
    static ObjectNode createBody() {
        return validator.create(object("""
                {"profile":{"name":"수동기업","region":"부산","employees":12,"founded_year":2020,"biz_no_masked":"123-45-*****"},
                 "certifications":{"iatf16949":true,"iso14001":false},
                 "capabilities":[{"process":"프레스","material":"SPCC","monthly_capacity":1000}]}
                """), UUID.randomUUID());
    }
    static String create() {
        return (String) ((Map<?, ?>) service.create(createBody(), ACTOR, UUID.randomUUID()).get("supplier")).get("supplier_id");
    }
    static ObjectNode checkedPatch(String json) { return validator.patch(object(json), UUID.randomUUID()); }

    @Test void migrationIsNonDestructiveAndRepeatableAndQualityMayBeAbsent() {
        try (var reopened = new BusinessDataStore(true, url, user, password)) {
            assertThat(reopened.jdbc().queryForObject("SELECT revenue FROM financials WHERE supplier_id='SUP-0001'", Long.class))
                    .isEqualTo(9007199254740993L);
        }
        Map<String, Object> detail = service.detail("SUP-10000", UUID.randomUUID());
        assertThat(detail.get("quality")).isNull();
        assertThat(store.jdbc().queryForObject("SELECT count(*) FROM supplier_overview WHERE supplier_id='SUP-10000'", Integer.class)).isEqualTo(1);
        assertThat(((Map<?, ?>) detail.get("supplier")).get("profile_origin_source")).isEqualTo("MOCK");
        assertThat(service.list(new AdminSupplierValidator.Search(0, 100, "품질없는", null, null), UUID.randomUUID()).get("total")).isEqualTo(1L);
    }

    @Test void creationAllocatesLargerIdsAndNeverInsertsReadOnlyBusinessData() {
        Map<String, Object> created = service.create(createBody(), ACTOR, UUID.randomUUID());
        Map<?, ?> supplier = (Map<?, ?>) created.get("supplier");
        assertThat(Long.parseLong(((String) supplier.get("supplier_id")).substring(4))).isGreaterThan(10000);
        assertThat(supplier.get("source")).isEqualTo("MANUAL");
        assertThat(supplier.get("status")).isEqualTo("ACTIVE");
        assertThat(supplier.get("version")).isEqualTo(0L);
        assertThat(created.get("quality")).isNull();
        assertThat((List<?>) created.get("financials")).isEmpty();
        assertThat((List<?>) created.get("quotes")).isEmpty();
        assertThat(created.get("contract")).isNull();
        assertThat((List<?>) created.get("audit")).hasSize(1);
    }

    @Test void creationKeepsRequestedOriginAndRejectsDuplicateCorpCode() {
        String body = """
                {"profile":{"source":"DART","corp_code":"%s","name":"공시입력","region":"울산","founded_year":2010,"biz_no_masked":"222-33-*****"},
                 "certifications":{"iatf16949":false,"iso14001":false},
                 "capabilities":[{"process":"단조","material":"S45C","monthly_capacity":500}]}
                """;
        Map<?, ?> supplier = (Map<?, ?>) service.create(validator.create(object(body.formatted("00999999")), null), ACTOR, null).get("supplier");
        assertThat(supplier.get("source")).isEqualTo("DART");
        assertThat(supplier.get("corp_code")).isEqualTo("00999999");
        assertThat(supplier.get("profile_origin_source")).isEqualTo("DART");
        assertThat(supplier.get("capability_source")).isEqualTo("MANUAL");
        String id = (String) supplier.get("supplier_id");
        assertThatThrownBy(() -> service.patch(id, checkedPatch("{\"version\":0,\"profile\":{\"name\":\"changed\"}}"), ACTOR, null))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.code).isEqualTo("READ_ONLY_SOURCE"));

        long before = store.jdbc().queryForObject("SELECT count(*) FROM suppliers", Long.class);
        assertThatThrownBy(() -> service.create(validator.create(object(body.formatted("00123456")), null), ACTOR, null))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.code).isEqualTo("DATA_CONFLICT"));
        assertThat(store.jdbc().queryForObject("SELECT count(*) FROM suppliers", Long.class)).isEqualTo(before);
    }

    @Test void dartProfileAndFinancialsStayUntouchedWhileCertificationIsEditable() {
        long version = store.jdbc().queryForObject("SELECT version FROM suppliers WHERE supplier_id='SUP-0001'", Long.class);
        assertThatThrownBy(() -> service.patch("SUP-0001", checkedPatch("{\"version\":" + version + ",\"profile\":{\"name\":\"changed\"}}"), ACTOR, null))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.code).isEqualTo("READ_ONLY_SOURCE"));
        Map<String, Object> changed = service.patch("SUP-0001", checkedPatch("{\"version\":" + version + ",\"certifications\":{\"iso14001\":true}}"), ACTOR, null);
        Map<?, ?> supplier = (Map<?, ?>) changed.get("supplier");
        assertThat(supplier.get("source")).isEqualTo("DART");
        assertThat(supplier.get("name")).isEqualTo("공시회사");
        assertThat(supplier.get("certification_source")).isEqualTo("MANUAL");
        assertThat(store.jdbc().queryForObject("SELECT source FROM financials WHERE supplier_id='SUP-0001'", String.class)).isEqualTo("DART");
    }

    @Test void mockProfileBecomesManualAndOriginalSourceIsKept() {
        long version = store.jdbc().queryForObject("SELECT version FROM suppliers WHERE supplier_id='SUP-10000'", Long.class);
        Map<?, ?> supplier = (Map<?, ?>) service.patch("SUP-10000", checkedPatch("{\"version\":" + version + ",\"profile\":{\"employees\":20}}"), ACTOR, null).get("supplier");
        assertThat(supplier.get("source")).isEqualTo("MANUAL");
        assertThat(supplier.get("profile_origin_source")).isEqualTo("MOCK");
        assertThat(((tools.jackson.databind.JsonNode) supplier.get("profile_original")).path("source").stringValue()).isEqualTo("MOCK");
    }

    @Test void writesAreVersionedAndAllSectionsHaveAuditBeforeAndAfter() {
        String id = create();
        var caps = validator.capabilitiesRequest(object("{\"version\":0,\"capabilities\":[{\"process\":\"사출\",\"material\":\"PA66\",\"monthly_capacity\":2000}]}"), null);
        service.capabilities(id, caps, ACTOR, null);
        var quality = validator.qualityRequest(object("{\"version\":1,\"quality\":{\"defect_ppm\":0,\"on_time_rate\":0.934,\"audit_grade\":\"B\",\"years_dealing\":0}}"), null);
        Map<String, Object> updated = service.quality(id, quality, ACTOR, null);
        assertThat(((Map<?, ?>) updated.get("quality")).get("on_time_rate")).isEqualTo(new java.math.BigDecimal("0.934"));
        assertThatThrownBy(() -> service.capabilities(id, caps, ACTOR, null))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.code).isEqualTo("VERSION_CONFLICT"));
        List<?> audit = (List<?>) updated.get("audit");
        assertThat(audit).hasSize(3);
        assertAuditEvent(audit.get(0), "QUALITY", 1L, 2L);
        assertAuditEvent(audit.get(1), "CAPABILITIES", 0L, 1L);
        assertAuditEvent(audit.get(2), "CREATE", null, 0L);
        assertThat(store.jdbc().queryForObject("SELECT count(*) FROM supplier_change_events WHERE supplier_id=? AND before_data IS NOT NULL AND actor_user_id=?", Long.class, id, ACTOR)).isEqualTo(2);
        var inactive = validator.statusRequest(object("{\"version\":2,\"status\":\"INACTIVE\"}"), null);
        Map<String, Object> deactivated = service.status(id, inactive, ACTOR, null);
        assertAuditEvent(((List<?>) deactivated.get("audit")).getFirst(), "STATUS", 2L, 3L);
        assertThat(store.jdbc().queryForObject("SELECT count(*) FROM supplier_overview WHERE supplier_id=?", Long.class, id)).isZero();
        assertThat(((Map<?, ?>) service.detail(id, null).get("supplier")).get("status")).isEqualTo("INACTIVE");
        assertThat(store.jdbc().queryForObject("SELECT count(*) FROM capabilities WHERE supplier_id=?", Long.class, id)).isEqualTo(1);
    }

    private static void assertAuditEvent(Object value, String action, Long beforeVersion, long afterVersion) {
        Map<?, ?> event = (Map<?, ?>) value;
        assertThat(event.get("action")).isEqualTo(action);
        if (beforeVersion == null) {
            assertThat(event.get("before_data")).isNull();
        } else {
            JsonNode before = (JsonNode) event.get("before_data");
            assertThat(before.path("supplier").get("version").longValue()).isEqualTo(beforeVersion.longValue());
            assertThat(before.has("audit")).isFalse();
        }
        JsonNode after = (JsonNode) event.get("after_data");
        assertThat(after.path("supplier").get("version").longValue()).isEqualTo(afterVersion);
        assertThat(after.has("audit")).isFalse();
    }

    @Test void concurrentVersionUpdatesAllowExactlyOneWriter() throws Exception {
        String id = create();
        try (var executor = Executors.newFixedThreadPool(2)) {
            var writes = List.of(executor.submit(() -> tryUpdate(id)), executor.submit(() -> tryUpdate(id)));
            assertThat(List.of(writes.get(0).get(), writes.get(1).get())).containsExactlyInAnyOrder("OK", "VERSION_CONFLICT");
        }
    }
    private String tryUpdate(String id) {
        try { service.patch(id, checkedPatch("{\"version\":0,\"profile\":{\"employees\":50}}"), ACTOR, null); return "OK"; }
        catch (ApiException failure) { return failure.code; }
    }

    @Test void failedMultirowWriteRollsBackCapabilitiesVersionAndAudit() {
        String id = create();
        ObjectNode invalid = object("{\"version\":0,\"capabilities\":[{\"process\":\"프레스\",\"material\":\"SPCC\",\"monthly_capacity\":0}]}");
        assertThatThrownBy(() -> service.capabilities(id, invalid, ACTOR, null))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.code).isEqualTo("DATA_CONFLICT"));
        assertThat(store.jdbc().queryForObject("SELECT monthly_capacity FROM capabilities WHERE supplier_id=?", Integer.class, id)).isEqualTo(1000);
        assertThat(store.jdbc().queryForObject("SELECT version FROM suppliers WHERE supplier_id=?", Long.class, id)).isZero();
        assertThat(store.jdbc().queryForObject("SELECT count(*) FROM supplier_change_events WHERE supplier_id=?", Long.class, id)).isEqualTo(1);
    }

    @Test void failedAuditInsertRollsBackSupplierVersionCapabilitiesAndAudit() {
        String id = create();
        store.jdbc().update("UPDATE suppliers SET capability_source='MOCK' WHERE supplier_id=?", id);
        Map<String, Object> before = service.detail(id, null);
        UUID requestId = UUID.randomUUID();
        ObjectNode replacement = validator.capabilitiesRequest(object("""
                {"version":0,"capabilities":[
                    {"process":"사출","material":"PA66","monthly_capacity":2000},
                    {"process":"프레스","material":"SPCC","monthly_capacity":3000}]}
                """), requestId);

        // The audit actor is NOT NULL. This fails after replacing capabilities and advancing the version.
        assertThatThrownBy(() -> service.capabilities(id, replacement, null, requestId))
                .isInstanceOfSatisfying(ApiException.class, error -> {
                    assertThat(error.status.value()).isEqualTo(409);
                    assertThat(error.code).isEqualTo("DATA_CONFLICT");
                    assertThat(error.requestId).isEqualTo(requestId);
                    assertThat(error.retryable).isFalse();
                });

        assertThat(service.detail(id, null)).isEqualTo(before);
    }

    @Test void emptyDatabaseIsNeverSeededAndStandaloneMigrationCanPrecedeFlyway() throws Exception {
        String isolated = "business_empty_" + UUID.randomUUID().toString().replace("-", "");
        admin.execute("CREATE SCHEMA " + isolated);
        String base = System.getenv("TEST_DATABASE_URL");
        String emptyUrl = base + (base.contains("?") ? "&" : "?") + "currentSchema=" + isolated;
        JdbcTemplate empty = new JdbcTemplate(new DriverManagerDataSource(emptyUrl, user, password));
        try {
            assertThatThrownBy(() -> new BusinessDataStore(true, emptyUrl, user, password)).isInstanceOf(IllegalStateException.class);
            assertThat(empty.queryForObject("SELECT count(*) FROM information_schema.tables WHERE table_schema=?", Integer.class, isolated)).isZero();
            empty.execute(Files.readString(Path.of("../mcp-servers/business-mcp/schema.sql")));
            empty.update("INSERT INTO suppliers(supplier_id,source,name,region,founded_year,biz_no_masked) VALUES ('SUP-0900','MOCK','기존기업','부산',2000,'111-22-*****')");
            empty.execute(Files.readString(Path.of("src/main/resources/db/business/V1__supplier_management.sql")));
            empty.update("UPDATE suppliers SET name='수정기업',source='MANUAL',version=8,status='INACTIVE' WHERE supplier_id='SUP-0900'");
            try (BusinessDataStore adopted = new BusinessDataStore(true, emptyUrl, user, password)) {
                Map<String, Object> supplier = adopted.jdbc().queryForMap("SELECT name,source,version,status,profile_origin_source FROM suppliers WHERE supplier_id='SUP-0900'");
                assertThat(supplier).containsEntry("name", "수정기업").containsEntry("source", "MANUAL")
                        .containsEntry("version", 8L).containsEntry("status", "INACTIVE").containsEntry("profile_origin_source", "MOCK");
            }
        } finally { admin.execute("DROP SCHEMA " + isolated + " CASCADE"); }
    }

    @Test void sequenceCollisionWithLateImportsIsSkippedAndQueryWildcardsAreLiteral() {
        long next = store.jdbc().queryForObject("SELECT nextval('supplier_manual_id_seq')", Long.class);
        store.jdbc().update("INSERT INTO suppliers(supplier_id,source,name,region,founded_year,biz_no_masked) VALUES (?,'MANUAL','100%_사업','부산',2020,'111-22-*****')", "SUP-" + next);
        store.jdbc().queryForObject("SELECT setval('supplier_manual_id_seq',?,false)", Long.class, next);
        assertThat(Long.parseLong(create().substring(4))).isGreaterThan(next);
        assertThat(service.list(new AdminSupplierValidator.Search(0, 10, "%_", null, null), null).get("total")).isEqualTo(1L);
        Map<?, ?> summary = service.summary();
        assertThat(((Number) summary.get("total")).longValue()).isGreaterThanOrEqualTo(2);
    }
}
