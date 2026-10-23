package com.negopilot.backend.admin.supplier;

import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.auth.CurrentUser;

import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class AdminSupplierValidatorTest {
    private final JsonMapper mapper = JsonMapper.builder().build();
    private final AdminSupplierValidator validator = new AdminSupplierValidator(mapper);
    private ObjectNode object(String value) { return (ObjectNode) mapper.readTree(value); }

    @ParameterizedTest
    @ValueSource(strings = {"{}", "{\"version\":-1}", "{\"version\":1.0}", "{\"version\":true}",
            "{\"version\":0,\"financials\":[]}", "{\"version\":0,\"profile\":{\"source\":\"DART\"}}",
            "{\"version\":0,\"profile\":{\"supplier_id\":\"SUP-0001\"}}", "{\"version\":0,\"profile\":{\"corp_code\":\"12345678\"}}",
            "{\"version\":0,\"profile\":{\"employees\":0}}", "{\"version\":0,\"profile\":{\"biz_no_masked\":\"123-45-67890\"}}",
            "{\"version\":0,\"certifications\":{\"iatf16949\":\"true\"}}", "{\"version\":0,\"profile\":{}}"})
    void rejectsInvalidVersionUnknownFieldsAndUnmaskedIdentifiers(String raw) {
        assertThatThrownBy(() -> validator.patch(object(raw), null)).isInstanceOf(ApiException.class);
    }

    @ParameterizedTest
    @ValueSource(strings = {"0.9999", "1.001", "-0.1", "\"0.9\"", "true", "null"})
    void qualityRejectsWrongRateAndPrecisionRatherThanRounding(String rate) {
        assertThatThrownBy(() -> validator.qualityRequest(object("{\"version\":0,\"quality\":{\"defect_ppm\":1,\"on_time_rate\":" + rate + ",\"audit_grade\":\"A\",\"years_dealing\":0}}"), null))
                .isInstanceOf(ApiException.class);
    }

    @Test void checksCapabilityDuplicationAndSupportsLargeSupplierIds() {
        assertThat(validator.supplierId("SUP-10000", null)).isEqualTo("SUP-10000");
        assertThatThrownBy(() -> validator.supplierId("SUP/1", null)).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> validator.capabilitiesRequest(object("""
                {"version":0,"capabilities":[{"process":"프레스","material":"SPCC","monthly_capacity":100},
                 {"process":"프레스","material":" SPCC ","monthly_capacity":100}]}
                """), null)).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> validator.capabilitiesRequest(object("{\"version\":0,\"capabilities\":[]}"), null)).isInstanceOf(ApiException.class);
    }

    private static final String CREATE = """
            {"profile":{"name":"수동기업","region":"부산","founded_year":2020,"biz_no_masked":"123-45-*****"%s},
             "certifications":{"iatf16949":true,"iso14001":false},
             "capabilities":[{"process":"프레스","material":"SPCC","monthly_capacity":1000}]}
            """;

    @Test void creationDefaultsToManualAndOnlyDartCarriesCorpCode() {
        assertThat(validator.create(object(CREATE.formatted("")), null).path("profile").path("source").stringValue()).isEqualTo("MANUAL");
        ObjectNode dart = validator.create(object(CREATE.formatted(",\"source\":\"DART\",\"corp_code\":\" 00123456 \"")), null);
        assertThat(dart.path("profile").path("corp_code").stringValue()).isEqualTo("00123456");
        assertThat(validator.create(object(CREATE.formatted(",\"source\":\"MOCK\",\"corp_code\":null")), null)
                .path("profile").path("corp_code").isNull()).isTrue();
    }

    @ParameterizedTest
    @ValueSource(strings = {",\"source\":\"DART\"", ",\"source\":\"DART\",\"corp_code\":\"1234567\"",
            ",\"source\":\"DART\",\"corp_code\":12345678", ",\"source\":\"MANUAL\",\"corp_code\":\"00123456\"",
            ",\"source\":\"OPEN\"", ",\"source\":\"manual\""})
    void creationRejectsUnknownSourceAndMismatchedCorpCode(String origin) {
        assertThatThrownBy(() -> validator.create(object(CREATE.formatted(origin)), null)).isInstanceOf(ApiException.class);
    }

    @Test void searchRejectsRepeatedFiltersAndUnsafePagination() {
        assertThatThrownBy(() -> validator.search(Map.of("page", new String[]{"0", "1"}), null)).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> validator.search(Map.of("size", new String[]{"101"}), null)).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> validator.search(Map.of("source", new String[]{"unknown"}), null)).isInstanceOf(ApiException.class);
        assertThat(validator.search(Map.of("q", new String[]{"  회사  "}), null).q()).isEqualTo("회사");
    }

    @Test void controllerChecksAdministrationBeforeReadingOrMutatingBusinessData() {
        CurrentUser users = mock(CurrentUser.class);
        AdminSupplierService service = mock(AdminSupplierService.class);
        AdminSupplierController controller = new AdminSupplierController(users, validator, service);
        when(users.requireAdmin()).thenThrow(ApiException.of(UUID.randomUUID(), 403, "FORBIDDEN", "Access is denied.", false));
        assertThatThrownBy(() -> controller.list(null)).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> controller.detail("SUP-0001", null)).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> controller.create("{}", null)).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> controller.patch("SUP-0001", "{}", null)).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> controller.capabilities("SUP-0001", "{}", null)).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> controller.quality("SUP-0001", "{}", null)).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> controller.status("SUP-0001", "{}", null)).isInstanceOf(ApiException.class);
        verifyNoInteractions(service);
    }

    @Test void disabledBusinessStoreAllowsServiceConstructionAndFailsOnlyOnAccess() {
        try (BusinessDataStore disabled = new BusinessDataStore(false, "", "", "")) {
            AdminSupplierRepository repository = new AdminSupplierRepository(disabled, mapper);
            AdminSupplierService service = new AdminSupplierService(disabled, repository);
            UUID requestId = UUID.randomUUID();
            ObjectNode inactive = validator.statusRequest(object("{\"version\":0,\"status\":\"INACTIVE\"}"), requestId);
            assertThat(disabled.enabled()).isFalse();
            assertThatThrownBy(disabled::jdbc).isInstanceOfSatisfying(ApiException.class,
                    AdminSupplierValidatorTest::assertBusinessDataNotConfigured);
            assertThatThrownBy(service::summary).isInstanceOfSatisfying(ApiException.class,
                    AdminSupplierValidatorTest::assertBusinessDataNotConfigured);
            assertThatThrownBy(() -> service.detail("SUP-0001", requestId)).isInstanceOfSatisfying(ApiException.class,
                    AdminSupplierValidatorTest::assertBusinessDataNotConfigured);
            assertThatThrownBy(() -> service.status("SUP-0001", inactive, UUID.randomUUID(), requestId))
                    .isInstanceOfSatisfying(ApiException.class, AdminSupplierValidatorTest::assertBusinessDataNotConfigured);
        }
    }

    private static void assertBusinessDataNotConfigured(ApiException error) {
        assertThat(error.status.value()).isEqualTo(503);
        assertThat(error.code).isEqualTo("BUSINESS_DATA_UNAVAILABLE");
        assertThat(error.getMessage()).isEqualTo("Business data is not configured.");
        assertThat(error.requestId).isNull();
        assertThat(error.retryable).isTrue();
    }
}
