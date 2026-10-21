package com.negopilot.backend;

import java.net.URI;
import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties("agent")
public record AgentProperties(URI baseUrl, String internalApiKey,
                              Duration connectTimeout, Duration responseTimeout) {
    public AgentProperties {
        if (baseUrl == null || !("http".equals(baseUrl.getScheme()) || "https".equals(baseUrl.getScheme()))
                || baseUrl.getHost() == null || baseUrl.getUserInfo() != null
                || baseUrl.getQuery() != null || baseUrl.getFragment() != null
                || !(baseUrl.getPath().isEmpty() || "/".equals(baseUrl.getPath()))) {
            throw new IllegalArgumentException("AGENT_BASE_URL must be an HTTP origin without credentials");
        }
        if (connectTimeout == null || connectTimeout.isZero() || connectTimeout.isNegative()
                || responseTimeout == null || responseTimeout.isZero() || responseTimeout.isNegative()) {
            throw new IllegalArgumentException("Agent timeouts must be positive");
        }
        if (internalApiKey == null) internalApiKey = "";
        if (internalApiKey.indexOf('\r') >= 0 || internalApiKey.indexOf('\n') >= 0) {
            throw new IllegalArgumentException("AGENT_INTERNAL_API_KEY contains invalid characters");
        }
    }

    @Override
    public String toString() {
        return "AgentProperties[credentials redacted]";
    }
}
