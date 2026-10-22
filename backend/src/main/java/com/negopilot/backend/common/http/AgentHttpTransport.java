package com.negopilot.backend.common.http;

import com.negopilot.backend.agent.AgentProperties;
import com.negopilot.backend.common.api.ApiException;
import java.io.IOException;
import java.net.SocketTimeoutException;
import java.net.URI;
import java.net.http.HttpTimeoutException;
import java.util.Set;
import java.util.UUID;
import org.apache.hc.core5.http.ConnectionRequestTimeoutException;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** Shared wire handling. Each gateway retains its own response and error contract. */
@Component
public final class AgentHttpTransport {
    private final RestClient client;
    private final AgentProperties properties;
    private final JsonMapper mapper;

    public AgentHttpTransport(RestClient agentRestClient, AgentProperties properties, JsonMapper mapper) {
        this.client = agentRestClient;
        this.properties = properties;
        this.mapper = mapper;
    }

    public URI uri(String path) {
        return properties.baseUrl().resolve(path);
    }

    @FunctionalInterface
    public interface ResponseHandler {
        JsonNode handle(int status, JsonNode body);
    }

    public JsonNode exchange(HttpMethod method, URI uri, JsonNode request, UUID requestId,
                             Set<Integer> allowedStatuses, ResponseHandler handler) {
        if (properties.internalApiKey().isBlank()) throw ApiException.unavailable(requestId);
        try {
            var call = client.method(method).uri(uri).accept(MediaType.APPLICATION_JSON)
                    .header("X-Internal-Api-Key", properties.internalApiKey());
            if (request != null) call.contentType(MediaType.APPLICATION_JSON).body(mapper.writeValueAsBytes(request));
            return call.exchange((ignored, response) -> {
                int status = response.getStatusCode().value();
                if (!allowedStatuses.contains(status)) throw ApiException.invalidResponse(requestId);
                MediaType contentType;
                try { contentType = response.getHeaders().getContentType(); }
                catch (IllegalArgumentException failure) { throw ApiException.invalidResponse(requestId); }
                if (contentType == null || !MediaType.APPLICATION_JSON.isCompatibleWith(contentType)) {
                    throw ApiException.invalidResponse(requestId);
                }
                JsonNode body;
                try { body = mapper.readTree(response.getBody().readAllBytes()); }
                catch (IOException failure) {
                    if (isTimeout(failure)) throw ApiException.timeout(requestId);
                    throw ApiException.invalidResponse(requestId);
                } catch (RuntimeException failure) { throw ApiException.invalidResponse(requestId); }
                return handler.handle(status, body);
            });
        } catch (ApiException failure) {
            throw failure;
        } catch (ResourceAccessException failure) {
            if (isTimeout(failure)) throw ApiException.timeout(requestId);
            throw ApiException.unavailable(requestId);
        } catch (RestClientException failure) {
            throw ApiException.invalidResponse(requestId);
        }
    }

    private static boolean isTimeout(Throwable failure) {
        for (Throwable cause = failure; cause != null; cause = cause.getCause()) {
            if (cause instanceof SocketTimeoutException || cause instanceof HttpTimeoutException
                    || cause instanceof ConnectionRequestTimeoutException) return true;
        }
        return false;
    }
}
