package com.negopilot.backend.admin.data;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.negopilot.backend.admin.supplier.AdminSupplierService;
import com.negopilot.backend.auth.CurrentUser;
import com.negopilot.backend.common.api.ApiException;
import java.time.Instant;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;

class AdminDataControllerTest {
    private CurrentUser currentUser;
    private AdminSupplierService suppliers;
    private AdminDataController controller;

    @BeforeEach void setup() {
        currentUser = mock(CurrentUser.class);
        suppliers = mock(AdminSupplierService.class);
        controller = new AdminDataController(currentUser, suppliers);
    }

    @Test void businessDataUnavailableIsReportedAsNullWithErrorCode() {
        when(suppliers.summary()).thenThrow(ApiException.of(null, 503, "BUSINESS_DATA_UNAVAILABLE",
                "Business data is not configured.", true));

        Map<String, Object> result = controller.summary(new MockHttpServletRequest());

        assertThat(result.keySet()).containsExactly("checked_at", "suppliers");
        Instant.parse((String) result.get("checked_at"));
        Map<?, ?> supplierSummary = (Map<?, ?>) result.get("suppliers");
        assertThat(supplierSummary).hasSize(3);
        assertThat(supplierSummary.get("status")).isEqualTo("UNAVAILABLE");
        assertThat(supplierSummary.get("data")).isNull();
        assertThat(supplierSummary.get("error_code")).isEqualTo("BUSINESS_DATA_UNAVAILABLE");
        var order = inOrder(currentUser, suppliers);
        order.verify(currentUser).requireAdmin();
        order.verify(suppliers).summary();
    }

    @Test void availableSourceHasNoErrorCode() {
        Map<String, Object> supplierData = Map.of("total", 0);
        when(suppliers.summary()).thenReturn(supplierData);

        Map<String, Object> result = controller.summary(new MockHttpServletRequest());

        assertThat(result.get("suppliers")).isEqualTo(Map.of("status", "AVAILABLE", "data", supplierData));
    }

    @Test void unexpectedFailureIsPropagatedInsteadOfReportedAsUnavailable() {
        var failure = new IllegalStateException("Unexpected supplier failure");
        when(suppliers.summary()).thenThrow(failure);

        assertThatThrownBy(() -> controller.summary(new MockHttpServletRequest())).isSameAs(failure);
    }

    @Test void rejectsQueryParametersBeforeReadingBusinessData() {
        var request = new MockHttpServletRequest();
        request.setParameter("force", "true");

        assertThatThrownBy(() -> controller.summary(request)).isInstanceOf(ApiException.class);

        verify(currentUser).requireAdmin();
        verifyNoInteractions(suppliers);
    }
}
