package com.negopilot.backend.chat;

import com.negopilot.backend.UserIdentity;
import com.negopilot.backend.auth.CurrentUser;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.util.UUID;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;

/** Exposes /api/v2/agent/queries for HTTP slice tests. Stored conversations are mocked because the query path never uses them. */
@TestConfiguration(proxyBeanMethods = false)
@Import(PersistentConversationController.class)
public class AgentQueryEndpointTestConfiguration {
    @Bean
    PersistentConversationService persistentConversationService() {
        return mock(PersistentConversationService.class);
    }

    @Bean
    CurrentUser currentUser() {
        CurrentUser user = mock(CurrentUser.class);
        when(user.requireActive()).thenReturn(new UserIdentity(UUID.randomUUID(), "USER", "ACTIVE"));
        return user;
    }
}
