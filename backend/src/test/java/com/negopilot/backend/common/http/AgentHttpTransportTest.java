package com.negopilot.backend.common.http;

import com.negopilot.backend.agent.AgentHttpConfiguration;
import com.negopilot.backend.agent.AgentProperties;
import com.negopilot.backend.common.api.ApiException;
import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpMethod;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class AgentHttpTransportTest {
    @Test
    void stalledResponseBodyMapsToTimeoutWithoutRetrying() throws Exception {
        var upstream = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        var workers = Executors.newCachedThreadPool();
        var release = new CountDownLatch(1);
        var calls = new AtomicInteger();
        upstream.setExecutor(workers);
        upstream.createContext("/body", exchange -> {
            calls.incrementAndGet();
            try {
                exchange.getResponseHeaders().set("Content-Type", "application/json");
                exchange.sendResponseHeaders(200, 100);
                exchange.getResponseBody().write("{\"started\":".getBytes(StandardCharsets.UTF_8));
                exchange.getResponseBody().flush();
                release.await(5, TimeUnit.SECONDS);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            } finally { exchange.close(); }
        });
        upstream.start();
        var configuration = new AgentHttpConfiguration();
        var properties = new AgentProperties(URI.create("http://127.0.0.1:" + upstream.getAddress().getPort()),
                "body-timeout-key", Duration.ofSeconds(2), Duration.ofMillis(250));
        UUID requestId = UUID.randomUUID();
        try (var http = configuration.agentHttpClient(properties)) {
            var transport = new AgentHttpTransport(configuration.agentRestClient(http), properties,
                    configuration.contractJsonMapper());
            assertThatThrownBy(() -> transport.exchange(HttpMethod.GET, transport.uri("/body"), null,
                    requestId, Set.of(200), (status, body) -> body))
                    .isInstanceOfSatisfying(ApiException.class, error -> {
                        assertThat(error.status.value()).isEqualTo(504);
                        assertThat(error.code).isEqualTo("UPSTREAM_TIMEOUT");
                        assertThat(error.requestId).isEqualTo(requestId);
                        assertThat(error.retryable).isTrue();
                    });
            assertThat(calls.get()).isEqualTo(1);
        } finally {
            release.countDown();
            upstream.stop(0);
            workers.shutdownNow();
        }
    }
}
