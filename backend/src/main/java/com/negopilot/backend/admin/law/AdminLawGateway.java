package com.negopilot.backend.admin.law;

import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.common.http.AgentHttpTransport;

import java.net.URI;
import java.time.Instant;
import java.time.LocalDate;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;
import org.springframework.http.HttpMethod;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;

@Service
public final class AdminLawGateway {
    private static final String ROOT = "/internal/v1/admin/laws";
    private static final String JOBS = ROOT + "/refresh-jobs";
    private static final String UUID_PATTERN = "[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}";
    private final AgentHttpTransport transport;

    AdminLawGateway(AgentHttpTransport transport) {
        this.transport = transport;
    }

    public JsonNode read(String path, UUID requestId) { return exchange(HttpMethod.GET, path, 200, requestId); }
    JsonNode latestRefresh(UUID requestId) { return exchange(HttpMethod.GET, JOBS, 200, requestId); }
    JsonNode startRefresh(UUID requestId) { return exchange(HttpMethod.POST, JOBS, 202, requestId); }
    JsonNode refreshJob(UUID jobId, UUID requestId) { return exchange(HttpMethod.GET, JOBS + "/" + jobId, 200, requestId); }

    private JsonNode exchange(HttpMethod method, String path, int expected, UUID requestId) {
        return transport.exchange(method, transport.uri(path), null, requestId,
                Set.of(expected, 400, 404, 409, 502, 503, 504), (status, body) -> {
                    try {
                        if (status != expected) throw error(body, status, path.startsWith(JOBS), requestId);
                        fields(body, "schema_version", "data");
                        check("1.0".equals(text(body, "schema_version")));
                        JsonNode data = body.get("data");
                        if (path.equals(JOBS)) {
                            if (method == HttpMethod.GET) {
                                fields(data, "item");
                                if (!data.get("item").isNull()) job(data.get("item"), null);
                            } else job(data, null);
                        } else if (path.startsWith(JOBS + "/")) {
                            job(data, UUID.fromString(path.substring(JOBS.length() + 1)));
                        } else validateRead(path, data);
                        return data;
                    } catch (ApiException failure) { throw failure; }
                    catch (RuntimeException bad) { throw ApiException.invalidResponse(requestId); }
                });
    }

    private ApiException error(JsonNode body, int status, boolean refresh, UUID id) {
        fields(body, "request_id", "error");
        check(body.get("request_id").isNull() || (body.get("request_id").isString()
                && body.get("request_id").stringValue().matches(UUID_PATTERN)));
        JsonNode error = body.get("error");
        fields(error, "code", "message", "retryable");
        text(error, "message");
        check(error.get("retryable").isBoolean());
        String code = text(error, "code");
        String expected = switch (status) {
            case 400 -> "VALIDATION_ERROR";
            case 404 -> "LAW_NOT_FOUND";
            case 409 -> "REFRESH_BUSY";
            case 502 -> "UPSTREAM_INVALID_RESPONSE";
            case 503 -> "UPSTREAM_UNAVAILABLE";
            case 504 -> "UPSTREAM_TIMEOUT";
            default -> throw new IllegalArgumentException();
        };
        check(expected.equals(code) && (status != 409 || refresh));
        return switch (status) {
            case 400 -> ApiException.validation(id);
            case 404 -> ApiException.of(id, 404, "LAW_NOT_FOUND", "Law data not found", false);
            case 409 -> ApiException.of(id, 409, "REFRESH_BUSY", "Law refresh is already running", true);
            case 503 -> ApiException.unavailable(id);
            case 504 -> ApiException.timeout(id);
            default -> ApiException.invalidResponse(id);
        };
    }

    private void validateRead(String path, JsonNode data) {
        URI uri = URI.create(path);
        String[] parts = uri.getPath().split("/");
        if (path.equals(ROOT)) {
            fields(data, "dataset_id", "loaded_at", "collected_at", "law_count", "version_count", "article_count", "items");
            nonblank(data, "dataset_id"); instant(data, "loaded_at"); nullableInstant(data, "collected_at");
            array(data, "items");
            check(count(data, "law_count") == data.get("items").size());
            long versions = 0, articles = 0;
            Set<String> keys = new HashSet<>();
            for (JsonNode item : data.get("items")) {
                fields(item, "law_key", "law_name", "version_count", "article_count");
                check(text(item, "law_key").matches("[a-z_]{1,80}") && keys.add(text(item, "law_key")));
                nonblank(item, "law_name");
                versions = Math.addExact(versions, count(item, "version_count"));
                articles = Math.addExact(articles, count(item, "article_count"));
            }
            check(count(data, "version_count") == versions && count(data, "article_count") == articles);
            return;
        }
        check(parts.length >= 7 && parts[6].equals("versions"));
        check(parts[5].equals(text(data, "law_key")));
        if (parts.length == 7) {
            fields(data, "law_key", "law_name", "items");
            nonblank(data, "law_name"); array(data, "items");
            Set<String> ids = new HashSet<>();
            for (JsonNode item : data.get("items")) {
                fields(item, "version_id", "effective_date", "promulgation_date", "promulgation_no", "source_url", "source_commit", "article_count");
                version(item); check(ids.add(text(item, "version_id")));
            }
            return;
        }
        check((parts.length == 9 || parts.length == 10) && parts[8].equals("articles"));
        version(data); check(parts[7].equals(text(data, "version_id")));
        if (parts.length == 9) {
            fields(data, "law_key", "version_id", "effective_date", "promulgation_date", "promulgation_no", "source_url", "source_commit", "article_count", "total", "offset", "limit", "items");
            String[] page = uri.getQuery().split("&");
            check(page.length == 2 && page[0].startsWith("offset=") && page[1].startsWith("limit="));
            long offset = count(data, "offset"), limit = count(data, "limit"), total = count(data, "total");
            check(offset == Long.parseLong(page[0].substring(7)) && limit == Long.parseLong(page[1].substring(6)));
            check(limit >= 1 && limit <= 100 && total == count(data, "article_count"));
            array(data, "items"); check(data.get("items").size() == Math.min(limit, Math.max(0, total - offset)));
            Set<String> numbers = new HashSet<>();
            for (JsonNode item : data.get("items")) {
                fields(item, "no", "title", "deleted"); articleSummary(item); check(numbers.add(text(item, "no")));
            }
        } else {
            fields(data, "law_key", "law_name", "version_id", "effective_date", "promulgation_date", "promulgation_no", "source_url", "source_commit", "article_count", "article", "citation");
            nonblank(data, "law_name"); nonblank(data, "citation");
            JsonNode article = data.get("article");
            fields(article, "no", "title", "deleted", "chapter", "section", "paragraphs", "text");
            articleSummary(article); nullableText(article, "chapter"); nullableText(article, "section");
            text(article, "text"); array(article, "paragraphs");
            for (JsonNode paragraph : article.get("paragraphs")) {
                fields(paragraph, "no", "text"); nullableText(paragraph, "no"); text(paragraph, "text");
            }
            var number = java.util.regex.Pattern.compile("제?([0-9]+)(?:조)?(?:의([0-9]+))?").matcher(parts[9]);
            check(number.matches());
            check(("제" + number.group(1) + "조" + (number.group(2) == null ? "" : "의" + number.group(2))).equals(text(article, "no")));
        }
    }

    private void version(JsonNode data) {
        String date = text(data, "effective_date"), commit = text(data, "source_commit");
        LocalDate.parse(date); LocalDate.parse(text(data, "promulgation_date")); nonblank(data, "promulgation_no");
        check(commit.matches("[a-f0-9]{40}") && text(data, "version_id").equals(date + "_" + commit));
        URI source = URI.create(nonblank(data, "source_url"));
        check(source.getScheme() != null && Set.of("http", "https").contains(source.getScheme()) && source.getHost() != null && source.getUserInfo() == null);
        count(data, "article_count");
    }

    private void articleSummary(JsonNode data) {
        check(text(data, "no").matches("제[0-9]+조(?:의[0-9]+)?"));
        text(data, "title"); check(data.get("deleted") != null && data.get("deleted").isBoolean());
    }

    private void job(JsonNode data, UUID expectedId) {
        fields(data, "job_id", "status", "started_at", "finished_at", "previous_dataset_id", "dataset_id", "error_code");
        check(text(data, "job_id").matches(UUID_PATTERN));
        UUID id = UUID.fromString(text(data, "job_id"));
        check(expectedId == null || expectedId.equals(id));
        Instant started = instant(data, "started_at");
        nonblank(data, "previous_dataset_id");
        String status = text(data, "status");
        switch (status) {
            case "RUNNING" -> check(data.get("finished_at").isNull() && data.get("dataset_id").isNull() && data.get("error_code").isNull());
            case "SUCCEEDED" -> {
                check(!instant(data, "finished_at").isBefore(started));
                nonblank(data, "dataset_id"); check(data.get("error_code").isNull());
            }
            case "FAILED" -> {
                check(!instant(data, "finished_at").isBefore(started) && data.get("dataset_id").isNull());
                check(Set.of("REFRESH_FAILED", "REFRESH_INTERRUPTED").contains(text(data, "error_code")));
            }
            default -> throw new IllegalArgumentException();
        }
    }

    private static void fields(JsonNode node, String... names) {
        check(node != null && node.isObject() && node.propertyNames().equals(Set.of(names)));
    }
    private static void check(boolean valid) { if (!valid) throw new IllegalArgumentException(); }
    private static String text(JsonNode node, String name) {
        check(node != null && node.isObject());
        JsonNode value = node.get(name); check(value != null && value.isString()); return value.stringValue();
    }
    private static String nonblank(JsonNode node, String name) { String value = text(node, name); check(!value.isBlank()); return value; }
    private static long count(JsonNode node, String name) {
        JsonNode value = node.get(name); check(value != null && value.isIntegralNumber() && value.canConvertToLong() && value.longValue() >= 0); return value.longValue();
    }
    private static void array(JsonNode node, String name) { check(node.get(name) != null && node.get(name).isArray()); }
    private static void nullableText(JsonNode node, String name) { check(node.get(name) != null); if (!node.get(name).isNull()) text(node, name); }
    private static Instant instant(JsonNode node, String name) { return Instant.parse(text(node, name)); }
    private static void nullableInstant(JsonNode node, String name) { check(node.get(name) != null); if (!node.get(name).isNull()) instant(node, name); }
}
