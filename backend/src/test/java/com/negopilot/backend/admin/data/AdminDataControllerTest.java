package com.negopilot.backend.admin.data;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.negopilot.backend.admin.law.AdminLawGateway;
import com.negopilot.backend.admin.supplier.AdminSupplierService;
import com.negopilot.backend.auth.CurrentUser;
import com.negopilot.backend.common.api.ApiException;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import tools.jackson.databind.json.JsonMapper;

class AdminDataControllerTest {
    private final JsonMapper mapper = JsonMapper.builder().build();
    private CurrentUser currentUser;
    private AdminSupplierService suppliers;
    private AdminLawGateway laws;
    private AdminDataController controller;

    @BeforeEach void setup() {
        currentUser = mock(CurrentUser.class);
        suppliers = mock(AdminSupplierService.class);
        laws = mock(AdminLawGateway.class);
        controller = new AdminDataController(currentUser, suppliers, laws);
    }

    @Test void businessDataUnavailableDoesNotHideAvailableLawData() {
        when(suppliers.summary()).thenThrow(ApiException.of(null, 503, "BUSINESS_DATA_UNAVAILABLE",
                "Business data is not configured.", true));
        var lawData = mapper.createObjectNode().put("law_count", 2);
        when(laws.read(anyString(), any())).thenReturn(lawData);
        var request = new MockHttpServletRequest();

        Map<String, Object> result = controller.summary(request);

        assertThat(result.keySet()).containsExactly("checked_at", "suppliers", "laws");
        Map<?, ?> supplierSummary = (Map<?, ?>) result.get("suppliers");
        assertThat(supplierSummary).hasSize(3);
        assertThat(supplierSummary.get("status")).isEqualTo("UNAVAILABLE");
        assertThat(supplierSummary.get("data")).isNull();
        assertThat(supplierSummary.get("error_code")).isEqualTo("BUSINESS_DATA_UNAVAILABLE");
        assertThat(result.get("laws")).isEqualTo(Map.of("status", "AVAILABLE", "data", lawData));
        var order = inOrder(currentUser, suppliers, laws);
        order.verify(currentUser).requireAdmin();
        order.verify(suppliers).summary();
        order.verify(laws).read("/internal/admin/laws", (UUID) request.getAttribute("request_id"));
    }

    @Test void availableSourcesHaveNoErrorCode() {
        Map<String, Object> supplierData = Map.of("total", 0);
        var lawData = mapper.createObjectNode().put("law_count", 0);
        when(suppliers.summary()).thenReturn(supplierData);
        when(laws.read(anyString(), any())).thenReturn(lawData);

        Map<String, Object> result = controller.summary(new MockHttpServletRequest());

        assertThat(result.get("suppliers")).isEqualTo(Map.of("status", "AVAILABLE", "data", supplierData));
        assertThat(result.get("laws")).isEqualTo(Map.of("status", "AVAILABLE", "data", lawData));
    }

    @Test void unexpectedFailureIsPropagatedInsteadOfReportedAsUnavailable() {
        var failure = new IllegalStateException("Unexpected supplier failure");
        when(suppliers.summary()).thenThrow(failure);

        assertThatThrownBy(() -> controller.summary(new MockHttpServletRequest())).isSameAs(failure);

        verifyNoInteractions(laws);
    }
}
