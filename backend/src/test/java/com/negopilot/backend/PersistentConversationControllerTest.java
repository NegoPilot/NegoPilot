package com.negopilot.backend;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

class PersistentConversationControllerTest {
    private final JsonMapper mapper = JsonMapper.builder().build();
    private final UUID owner = UUID.randomUUID();
    private final UUID conversation = UUID.randomUUID();
    private PersistentConversationService service;
    private GenerationGateway gateway;
    private CurrentUser user;
    private PersistentConversationController controller;

    @BeforeEach void setup() {
        service = mock(PersistentConversationService.class);
        gateway = mock(GenerationGateway.class);
        user = mock(CurrentUser.class);
        when(user.requireActive()).thenReturn(new UserIdentity(owner, "USER", "ACTIVE"));
        controller = new PersistentConversationController(service, new GenerationRequestValidator(), gateway, user, mapper);
    }
    private void invalid(Runnable action) {
        assertThatThrownBy(action::run).isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.code).isEqualTo("VALIDATION_ERROR"));
    }

    @Test void ownershipComesFromThePrincipalAndMessageContextIsForwarded() {
        UUID key = UUID.randomUUID();
        var request = new MockHttpServletRequest();
        request.addHeader("Idempotency-Key", key.toString());
        controller.message(conversation.toString(), "{\"query\":\"새 질문\",\"context\":{\"selected_supplier_ids\":[\"SUP-0009\"]}}", request);
        verify(service).message(eq(owner), eq(conversation), argThat(input -> input.query().equals("새 질문")
                && input.context().get("selected_supplier_ids").get(0).stringValue().equals("SUP-0009")), eq(key), any());
        invalid(() -> controller.create("{\"context\":{\"selected_supplier_ids\":[]},\"owner_id\":\"" + owner + "\"}", new MockHttpServletRequest()));
    }

    @Test void rejectsMalformedAndRepeatedIdempotencyKeys() {
        var malformed = new MockHttpServletRequest();
        malformed.addHeader("Idempotency-Key", "not-a-uuid");
        invalid(() -> controller.message(conversation.toString(), "{\"query\":\"질문\"}", malformed));
        var repeated = new MockHttpServletRequest();
        repeated.addHeader("Idempotency-Key", UUID.randomUUID().toString());
        repeated.addHeader("Idempotency-Key", UUID.randomUUID().toString());
        invalid(() -> controller.message(conversation.toString(), "{\"query\":\"질문\"}", repeated));
        verifyNoInteractions(service);
    }

    @Test void validatesPaginationAndRejectsUnknownOrRepeatedQueryParameters() {
        controller.list(new MockHttpServletRequest());
        verify(service).list(eq(owner), isNull(), eq(20), any());
        controller.messages(conversation.toString(), new MockHttpServletRequest());
        verify(service).messages(eq(owner), eq(conversation), isNull(), eq(50), any());
        for (String value : new String[] {"0", "101", "-1", "1.5", "", "10000000000000"}) {
            var request = new MockHttpServletRequest();
            request.addParameter("size", value);
            invalid(() -> controller.list(request));
        }
        var before = new MockHttpServletRequest();
        before.addParameter("before_seq", "0");
        invalid(() -> controller.messages(conversation.toString(), before));
        var unknown = new MockHttpServletRequest();
        unknown.addParameter("owner_id", owner.toString());
        invalid(() -> controller.list(unknown));
        var duplicate = new MockHttpServletRequest();
        duplicate.addParameter("size", "1", "2");
        invalid(() -> controller.list(duplicate));
    }

    @Test void titleUpdatesAreStrictAndDeletesReturnNoContent() {
        controller.rename(conversation.toString(), "{\"title\":\"  새 제목  \"}", new MockHttpServletRequest());
        verify(service).rename(eq(owner), eq(conversation), eq("새 제목"), any());
        invalid(() -> controller.rename(conversation.toString(), "{\"title\":\" \"}", new MockHttpServletRequest()));
        invalid(() -> controller.rename(conversation.toString(), "{\"title\":\"제목\",\"owner_id\":\"x\"}", new MockHttpServletRequest()));
        invalid(() -> controller.rename(conversation.toString(), mapper.createObjectNode().put("title", "제목\0문자").toString(), new MockHttpServletRequest()));
        invalid(() -> controller.rename(conversation.toString(), "{\"title\":\"" + "가".repeat(101) + "\"}", new MockHttpServletRequest()));
        assertThat(controller.delete(conversation.toString(), new MockHttpServletRequest()).getStatusCode().value()).isEqualTo(204);
    }

    @Test void directQueriesRequireActiveAuthenticationBeforeCallingTheGateway() {
        ObjectNode result = mapper.createObjectNode().put("ok", true);
        when(gateway.execute(any(), any())).thenReturn(result);
        assertThat(controller.query("{\"query\":\"질문\"}", new MockHttpServletRequest())).isEqualTo(result);
        verify(user).requireActive();
        reset(gateway);
        when(user.requireActive()).thenThrow(ApiException.of(null, 401, "UNAUTHENTICATED", "Authentication required", false));
        assertThatThrownBy(() -> controller.query("{\"query\":\"질문\"}", new MockHttpServletRequest())).isInstanceOf(ApiException.class);
        verifyNoInteractions(gateway);
    }
}
