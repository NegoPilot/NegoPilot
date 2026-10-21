package com.negopilot.backend;

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

    @Test void disabledBusinessStoreDoesNotReplaceApplicationDatasourceOrRequireDatabase() {
        try (BusinessDataStore disabled = new BusinessDataStore(false, "", "", "")) {
            assertThat(disabled.enabled()).isFalse();
            assertThatThrownBy(disabled::jdbc).isInstanceOfSatisfying(ApiException.class,
                    e -> assertThat(e.code).isEqualTo("BUSINESS_DATA_UNAVAILABLE"));
        }
    }
}
