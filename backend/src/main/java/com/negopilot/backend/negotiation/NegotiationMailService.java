package com.negopilot.backend.negotiation;

import com.negopilot.backend.agent.AgentProperties;
import com.negopilot.backend.agent.MailGateway;
import com.negopilot.backend.common.api.ApiException;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Base64;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;
import static com.negopilot.backend.negotiation.NegotiationRequests.Result;

/**
 * 외부 확정본(final) 내보내기 묶음을 협력사 담당자에게 메일로 보낸다.
 * 미리보기에서 받은 확인 토큰은 이 서버에만 두고, 발송은 미리보기와 같은 입력으로만 한다.
 * 결과가 불명확하면 UNKNOWN으로 두고 자동으로 다시 보내지 않는다. 복구는 client_ref(=행 id)로 mail-mcp 기록을 찾는다.
 */
@Service
class NegotiationMailService {
    // 협력사에게 보내는 추가 문구에 내부 협상 정보가 섞이지 않게 막는다.
    static final List<String> INTERNAL_WORDS = List.of("대외비", "내부 브리핑", "최대 수용가", "목표 합의가", "협상 전략",
            "양보", "walk_away", "settlement");
    private static final Pattern NUMBER = Pattern.compile("[0-9][0-9,]*");
    // 토큰이 만료된 뒤에도 기록이 없으면 보내지 않은 것으로 본다. 상류 최대 처리시간 120초 + 시계 오차 60초.
    private static final long NOT_SENT_GRACE_SECONDS = 180;
    private static final Set<String> NOT_SENT = Set.of("INVALID_DOCUMENT", "DOCUMENT_NOT_FINAL", "OFFER_VIOLATION",
            "HASH_MISMATCH", "SENDER_INCOMPLETE", "INVALID_ATTACHMENT", "INVALID_MESSAGE", "RECIPIENT_MISSING",
            "CONFIRMATION_REQUIRED", "CONFIRMATION_INVALID", "CONFIRMATION_EXPIRED", "CONFIRMATION_MISMATCH",
            "REQUESTER_REQUIRED", "MAIL_NOT_CONFIGURED", "INVALID_CLIENT_REF");
    private final NegotiationRepository repository;
    private final TransactionTemplate transaction;
    private final MailGateway gateway;
    private final JsonMapper mapper;
    private final NegotiationRequests requests;
    private final NegotiationExportStore store;
    private final long leaseSeconds;

    NegotiationMailService(NegotiationRepository repository, TransactionTemplate transaction, MailGateway gateway, JsonMapper mapper,
                           NegotiationRequests requests, NegotiationExportStore store, AgentProperties properties) {
        this.repository = repository; this.transaction = transaction; this.gateway = gateway; this.mapper = mapper;
        this.requests = requests; this.store = store;
        leaseSeconds = properties.responseTimeout().toSeconds() + 30;
    }

    Result preview(UUID owner, UUID id, UUID pid, ObjectNode body, UUID key, UUID requestId) {
        UUID bundleId = UUID.fromString(body.path("bundle_id").asText());
        Long contactId = body.hasNonNull("contact_id") ? body.get("contact_id").longValue() : null;
        String message = body.hasNonNull("message") && !body.get("message").asText().isBlank() ? body.get("message").asText() : null;
        ObjectNode acknowledgements = acknowledgements(body.path("acknowledgements"));
        return requests.run(owner, key, "MAIL_PREVIEW", id + "/" + pid + "/mails", body, requestId, () -> {
            Prepared prepared = transaction.execute(tx -> {
                member(owner, requestId);
                ObjectNode work = workspace(owner, id, false, requestId);
                ObjectNode proposal = proposal(id, pid, requestId);
                ObjectNode bundle = bundle(pid, bundleId, requestId);
                requireAllowed(policy(work, proposal, bundle, acknowledgements), requestId);
                checkMessage(message, pid, bundle.path("revision").intValue(), requestId);
                return new Prepared(bundle, attachments(bundleId, requestId), work.path("supplier_id").asText());
            });
            JsonNode preview = gateway.preview(requestId, document(prepared.bundle()), prepared.attachments(), contactId, message);
            // 해시가 다르면 확인한 내용과 보낼 내용이 다르다. 담당자가 다른 협력사면 수신자 오류다.
            if (!prepared.bundle().path("content_hash").asText().equals(preview.path("content_hash").asText())
                    || !prepared.supplierId().equals(preview.path("to").path("supplier_id").asText())) {
                throw ApiException.invalidResponse(requestId);
            }
            return transaction.execute(tx -> {
                member(owner, requestId);
                ObjectNode work = workspace(owner, id, true, requestId);
                ObjectNode proposal = proposal(id, pid, requestId);
                ObjectNode bundle = bundle(pid, bundleId, requestId);
                requireAllowed(policy(work, proposal, bundle, acknowledgements), requestId);
                ObjectNode row = object().put("id", UUID.randomUUID().toString()).put("proposal_id", pid.toString())
                        .put("revision", proposal.path("head_revision").intValue()).put("bundle_id", bundleId.toString())
                        .put("mail_document_id", proposal.path("mail_document_id").asText())
                        .put("document_version", bundle.path("rendered_document").path("version").intValue())
                        .put("content_hash", preview.path("content_hash").asText())
                        .put("contact_id", preview.path("to").path("contact_id").longValue())
                        .put("sender", preview.path("from").asText()).put("subject", preview.path("subject").asText())
                        .put("body", preview.path("body").asText()).put("dry_run", preview.path("dry_run").booleanValue())
                        .put("confirmation_token", preview.path("confirmation_token").asText())
                        .put("expires_at", preview.path("expires_at").asText()).put("status", "PREVIEWED")
                        .put("created_by", owner.toString());
                row.set("recipient", preview.get("to"));
                row.set("reply_to", preview.path("reply_to").isString() ? preview.get("reply_to") : mapper.nullNode());
                if (message == null) row.putNull("message"); else row.put("message", message);
                row.set("attachments", preview.get("attachments"));
                row.set("acknowledgements", acknowledgements);
                repository.insert("neg_mail_sends", row);
                return requests.complete(201, view(mail(owner, UUID.fromString(row.path("id").asText()), false, requestId)), requestId);
            });
        });
    }

    Result send(UUID owner, UUID mailId, ObjectNode body, UUID key, UUID requestId) {
        if (key == null) throw ApiException.of(requestId, 400, "IDEMPOTENCY_KEY_REQUIRED", "발송에는 Idempotency-Key가 필요합니다", false);
        return requests.run(owner, key, "MAIL_SEND", "mails/" + mailId, body, requestId, () -> {
            Claimed claimed = transaction.execute(tx -> {
                member(owner, requestId);
                ObjectNode row = mail(owner, mailId, true, requestId);
                switch (row.path("status").asText()) {
                    case "PREVIEWED" -> { }
                    case "STALE" -> throw error(requestId, 409, "PREVIEW_STALE", "미리보기 이후 제안서가 바뀌었습니다. 다시 미리보기 하세요");
                    case "FAILED" -> throw error(requestId, 409, "PREVIEW_USED", "이 미리보기로는 다시 보낼 수 없습니다. 다시 미리보기 하세요");
                    default -> throw error(requestId, 409, "ALREADY_SENT", "이미 발송했거나 발송 결과를 확인 중입니다");
                }
                if (!row.path("content_hash").asText().equals(body.path("content_hash").asText())) {
                    throw error(requestId, 409, "PREVIEW_STALE", "미리보기에서 확인한 내용과 다릅니다. 다시 미리보기 하세요");
                }
                if (!OffsetDateTime.parse(row.path("expires_at").asText()).isAfter(OffsetDateTime.now(ZoneOffset.UTC))) {
                    throw error(requestId, 409, "PREVIEW_EXPIRED", "미리보기 확인 시간이 지났습니다. 다시 미리보기 하세요");
                }
                UUID pid = uuid(row, "proposal_id");
                ObjectNode proposal = repository.one("SELECT to_jsonb(p) FROM neg_proposals p WHERE id=? FOR UPDATE", pid);
                ObjectNode work = repository.one("SELECT to_jsonb(w) FROM neg_workspaces w WHERE id=?", uuid(proposal, "workspace_id"));
                ObjectNode bundle = bundle(pid, uuid(row, "bundle_id"), requestId);
                List<String> reasons = policy(work, proposal, bundle, (ObjectNode) row.get("acknowledgements"));
                if (reasons.contains("BUNDLE_STALE")) {
                    repository.update("neg_mail_sends", object().put("status", "STALE").putNull("confirmation_token"), "t.id=? AND t.status='PREVIEWED'", mailId);
                    return null;
                }
                requireAllowed(reasons, requestId);
                try {
                    repository.update("neg_mail_sends", object().put("status", "SENDING").put("send_requested_at", now()), "t.id=? AND t.status='PREVIEWED'", mailId);
                } catch (DataIntegrityViolationException duplicate) {
                    throw error(requestId, 409, "ALREADY_SENT", "같은 제안서 버전을 같은 담당자에게 이미 보냈습니다");
                }
                return new Claimed(row, bundle, attachments(uuid(bundle, "id"), requestId));
            });
            if (claimed == null) throw error(requestId, 409, "PREVIEW_STALE", "미리보기 이후 제안서가 바뀌었습니다. 다시 미리보기 하세요");
            Outcome outcome = deliver(owner, claimed, requestId);
            return transaction.execute(tx -> {
                apply(mailId, "SENDING", outcome);
                ObjectNode row = mail(owner, mailId, false, requestId);
                return requests.complete("UNKNOWN".equals(row.path("status").asText()) ? 202 : 200, view(row), requestId);
            });
        });
    }

    Result reconcile(UUID owner, UUID mailId, UUID requestId) {
        expireSending();
        ObjectNode row = mail(owner, mailId, false, requestId);
        if (!"UNKNOWN".equals(row.path("status").asText())) return new Result(200, view(row));
        JsonNode records = gateway.history(requestId, row.path("mail_document_id").asText(), mailId.toString());
        Outcome outcome;
        if (records.size() == 1) outcome = recorded(records.get(0));
        else if (records.size() > 1) outcome = Outcome.unknown("RECORD_AMBIGUOUS");
        else {
            OffsetDateTime expires = OffsetDateTime.parse(row.path("expires_at").asText());
            OffsetDateTime current = OffsetDateTime.now(ZoneOffset.UTC);
            if (expires.isAfter(current)) {
                // 기록이 없고 토큰이 아직 유효하면 같은 토큰·같은 client_ref로 한 번 더 부른다. 이미 잡힌 요청이면 mail-mcp가 거절한다.
                ObjectNode bundle = repository.one("SELECT to_jsonb(b) FROM neg_export_bundles b WHERE id=?", uuid(row, "bundle_id"));
                outcome = deliver(owner, new Claimed(row, bundle, attachments(uuid(bundle, "id"), requestId)), requestId);
            } else if (expires.plusSeconds(NOT_SENT_GRACE_SECONDS).isBefore(current)) outcome = Outcome.failed("NOT_SENT");
            else outcome = Outcome.unknown("RECORD_PENDING");
        }
        return transaction.execute(tx -> {
            apply(mailId, "UNKNOWN", outcome);
            ObjectNode updated = mail(owner, mailId, false, requestId);
            return new Result(200, view(updated));
        });
    }

    ObjectNode list(UUID owner, UUID id, UUID pid, UUID requestId) {
        workspace(owner, id, false, requestId);
        proposal(id, pid, requestId);
        expireSending();
        ObjectNode result = object();
        var items = result.putArray("items");
        repository.rows("SELECT to_jsonb(m) FROM neg_mail_sends m WHERE proposal_id=? ORDER BY created_at DESC,id DESC", pid)
                .forEach(row -> items.add(view(row)));
        return result;
    }

    ObjectNode get(UUID owner, UUID mailId, UUID requestId) {
        expireSending();
        return view(mail(owner, mailId, false, requestId));
    }

    private Outcome deliver(UUID owner, Claimed claimed, UUID requestId) {
        ObjectNode row = claimed.row();
        try {
            JsonNode sent = gateway.send(requestId, document(claimed.bundle()), claimed.attachments(), row.path("contact_id").longValue(),
                    row.path("message").isString() ? row.path("message").asText() : null, row.path("confirmation_token").asText(),
                    owner.toString(), row.path("id").asText());
            return new Outcome(sent.path("status").asText(), sent.path("send_id").asText(),
                    sent.path("message_id").isString() ? sent.path("message_id").asText() : null, null);
        } catch (ApiException failure) {
            if (failure.status.value() == 422 && NOT_SENT.contains(failure.code)) return Outcome.failed(failure.code);
            if (failure.status.value() == 422 && "MAIL_SEND_FAILED".equals(failure.code)) return Outcome.failed(failure.code);
            // ALREADY_SENT·CLIENT_REF_USED·DB_UNAVAILABLE·시간 초과·연결 실패: 보냈는지 알 수 없다
            return Outcome.unknown(failure.code);
        } catch (RuntimeException failure) {
            return Outcome.unknown("INTERNAL_ERROR");
        }
    }

    private static Outcome recorded(JsonNode record) {
        String status = record.path("status").asText();
        return switch (status) {
            case "SENT", "DRY_RUN" -> new Outcome(status, record.path("send_id").asText(),
                    record.path("message_id").isString() ? record.path("message_id").asText() : null, null);
            case "FAILED" -> new Outcome("FAILED", record.path("send_id").asText(), null, "MAIL_SEND_FAILED");
            default -> Outcome.unknown("RECORD_SENDING");
        };
    }

    private void apply(UUID mailId, String expected, Outcome outcome) {
        ObjectNode update = object().put("status", outcome.status());
        if ("UNKNOWN".equals(outcome.status())) update.put("error_code", outcome.error());
        else {
            update.putNull("confirmation_token").put("finished_at", now());
            if (outcome.sendId() != null) update.put("send_id", outcome.sendId());
            if (outcome.messageId() != null) update.put("message_id", outcome.messageId());
            if (outcome.error() != null) update.put("error_code", outcome.error()); else update.putNull("error_code");
        }
        repository.update("neg_mail_sends", update, "t.id=? AND t.status=?", mailId, expected);
    }

    /** 발송 허용 조건. 생성 허용(conditional 포함)과 별개로, 검사 상태와 확인 체크를 본다. */
    private List<String> policy(ObjectNode work, ObjectNode proposal, ObjectNode bundle, ObjectNode acknowledgements) {
        List<String> reasons = new java.util.ArrayList<>();
        UUID pid = uuid(proposal, "id");
        int head = proposal.path("head_revision").intValue();
        if (!"ACTIVE".equals(proposal.path("status").asText())) reasons.add("PROPOSAL_SUPERSEDED");
        if (proposal.path("review_revision").intValue() != work.path("review_head_revision").intValue()) reasons.add("REVIEW_RECHECK_REQUIRED");
        if (!"EXTERNAL".equals(bundle.path("doc_type").asText()) || !"final".equals(bundle.path("doc_status").asText())
                || !"ready".equals(bundle.path("status").asText())) reasons.add("BUNDLE_NOT_FINAL");
        else if (!sameExternal(pid, bundle.path("revision").intValue(), head)) reasons.add("BUNDLE_STALE");
        List<ObjectNode> revisions = repository.rows("SELECT to_jsonb(r) FROM neg_proposal_revisions r WHERE proposal_id=? ORDER BY revision", pid);
        ObjectNode validation = NegotiationValidationView.calculate(head, revisions,
                repository.rows("SELECT to_jsonb(v) FROM neg_validations v WHERE proposal_id=? ORDER BY started_at DESC,id DESC", pid),
                OffsetDateTime.now(ZoneOffset.UTC));
        if (!"checked".equals(validation.path("status").asText())) reasons.add("VALIDATION_REQUIRED");
        else if ("risk_identified".equals(validation.path("conclusion").asText())) reasons.add("RISK_IDENTIFIED");
        String verdict = bundle.path("rendered_document").path("verdict_code").asText("");
        if ("VIOLATION".equals(verdict)) reasons.add("OFFER_VIOLATION");
        if ("CHECK_NEEDED".equals(verdict) && !acknowledgements.path("check_needed").asBoolean(false)) reasons.add("CHECK_NEEDED_NOT_ACKNOWLEDGED");
        if ("conditional".equals(validation.path("conclusion").asText())) {
            Set<String> confirmed = new HashSet<>();
            acknowledgements.path("unresolved_issue_ids").forEach(value -> confirmed.add(value.asText()));
            for (JsonNode issue : validation.path("unresolved_issue_ids")) {
                if (!confirmed.contains(issue.asText())) { reasons.add("UNRESOLVED_NOT_ACKNOWLEDGED"); break; }
            }
        }
        return reasons;
    }

    // 묶음 이후 내부 문구만 바뀌었으면 협력사 문서는 같다. 조건이나 외부 문서가 바뀌었으면 다시 내보내야 한다.
    private boolean sameExternal(UUID pid, int bundleRevision, int head) {
        if (bundleRevision == head) return true;
        ObjectNode exported = repository.one("SELECT to_jsonb(r) FROM neg_proposal_revisions r WHERE proposal_id=? AND revision=?", pid, bundleRevision);
        ObjectNode current = repository.one("SELECT to_jsonb(r) FROM neg_proposal_revisions r WHERE proposal_id=? AND revision=?", pid, head);
        return exported != null && current != null
                && exported.path("external_content_rev").equals(current.path("external_content_rev"))
                && exported.path("terms_rev").equals(current.path("terms_rev"));
    }

    private void checkMessage(String message, UUID pid, int revision, UUID requestId) {
        if (message == null) return;
        for (String word : INTERNAL_WORDS) {
            if (message.contains(word)) throw internalInfo(requestId);
        }
        ObjectNode row = repository.one("SELECT to_jsonb(r) FROM neg_proposal_revisions r WHERE proposal_id=? AND revision=?", pid, revision);
        Set<String> prices = new HashSet<>();
        long offered = row.path("unit_price").longValue();
        for (JsonNode price : row.path("internal_body").path("offer").path("price_range")) {
            if (price.path("unit_price").isIntegralNumber() && price.path("unit_price").longValue() != offered) prices.add(price.path("unit_price").asText());
        }
        Matcher numbers = NUMBER.matcher(message);
        while (numbers.find()) {
            if (prices.contains(numbers.group().replace(",", ""))) throw internalInfo(requestId);
        }
    }

    private ArrayNode attachments(UUID bundleId, UUID requestId) {
        ArrayNode files = mapper.createArrayNode();
        for (ObjectNode file : repository.rows("SELECT to_jsonb(f) FROM neg_export_files f WHERE bundle_id=? ORDER BY format", bundleId)) {
            byte[] bytes;
            try { bytes = store.read(file.path("storage_key").asText()); }
            catch (java.io.IOException | RuntimeException missing) { throw fileMissing(requestId); }
            if (!NegotiationRequests.sha256(bytes).equals(file.path("sha256").asText())) throw fileMissing(requestId);
            files.add(object().put("filename", file.path("file_name").asText()).put("content_type", file.path("content_type").asText())
                    .put("data_base64", Base64.getEncoder().encodeToString(bytes)));
        }
        if (files.isEmpty()) throw fileMissing(requestId);
        return files;
    }

    private ObjectNode view(ObjectNode row) {
        ObjectNode view = object().put("mail_id", row.path("id").asText()).put("proposal_id", row.path("proposal_id").asText())
                .put("proposal_revision", row.path("revision").intValue()).put("bundle_id", row.path("bundle_id").asText())
                .put("status", row.path("status").asText()).put("document_id", row.path("mail_document_id").asText())
                .put("document_version", row.path("document_version").intValue());
        view.set("to", row.get("recipient"));
        view.set("from", row.get("sender"));
        for (String key : List.of("reply_to", "subject", "body", "message", "attachments", "content_hash", "dry_run", "acknowledgements",
                "expires_at", "send_id", "message_id", "error_code", "created_at", "send_requested_at", "finished_at")) {
            view.set(key, row.has(key) ? row.get(key) : mapper.nullNode());
        }
        return view;
    }

    private void expireSending() {
        repository.execute("UPDATE neg_mail_sends SET status='UNKNOWN',error_code='SENDING_TIMEOUT' WHERE status='SENDING' "
                + "AND send_requested_at < clock_timestamp() - make_interval(secs => ?)", leaseSeconds);
    }

    private ObjectNode mail(UUID owner, UUID mailId, boolean lock, UUID requestId) {
        ObjectNode row = repository.one("SELECT to_jsonb(m) FROM neg_mail_sends m JOIN neg_proposals p ON p.id=m.proposal_id "
                + "JOIN neg_workspaces w ON w.id=p.workspace_id WHERE m.id=? AND w.owner_id=?" + (lock ? " FOR UPDATE OF m" : ""), mailId, owner);
        if (row == null) throw notFound(requestId);
        return row;
    }
    private ObjectNode workspace(UUID owner, UUID id, boolean lock, UUID requestId) {
        ObjectNode row = repository.one("SELECT to_jsonb(w) FROM neg_workspaces w WHERE id=? AND owner_id=?" + (lock ? " FOR UPDATE" : ""), id, owner);
        if (row == null) throw notFound(requestId);
        return row;
    }
    private ObjectNode proposal(UUID workspace, UUID pid, UUID requestId) {
        ObjectNode row = repository.one("SELECT to_jsonb(p) FROM neg_proposals p WHERE id=? AND workspace_id=?", pid, workspace);
        if (row == null) throw notFound(requestId);
        if (!"offer".equals(row.path("generation_status").asText())) throw error(requestId, 422, "NO_OFFER", "보낼 제안서가 없습니다");
        return row;
    }
    private ObjectNode bundle(UUID pid, UUID bundleId, UUID requestId) {
        ObjectNode row = repository.one("SELECT to_jsonb(b) FROM neg_export_bundles b WHERE id=? AND proposal_id=?", bundleId, pid);
        if (row == null) throw notFound(requestId);
        if ("INTERNAL".equals(row.path("doc_type").asText())) throw error(requestId, 400, "INTERNAL_DOCUMENT", "내부 문서는 메일로 보낼 수 없습니다");
        return row;
    }
    private void member(UUID owner, UUID requestId) {
        ObjectNode row = repository.one("SELECT jsonb_build_object('status',status) FROM app_users WHERE id=? FOR SHARE", owner);
        if (row == null || !"ACTIVE".equals(row.path("status").asText())) throw ApiException.of(requestId, 403, "ACCOUNT_NOT_ACTIVE", "활성 회원만 사용할 수 있습니다", false);
    }
    private void requireAllowed(List<String> reasons, UUID requestId) {
        if (reasons.isEmpty()) return;
        ObjectNode details = object(); details.set("reasons", mapper.valueToTree(reasons));
        throw ApiException.of(requestId, 422, "SEND_NOT_ALLOWED", "발송 조건을 확인하세요", false, details);
    }
    private ObjectNode acknowledgements(JsonNode source) {
        ObjectNode result = object().put("check_needed", source.path("check_needed").asBoolean(false));
        ArrayNode issues = result.putArray("unresolved_issue_ids");
        source.path("unresolved_issue_ids").forEach(issues::add);
        return result;
    }
    private ObjectNode document(ObjectNode bundle) { return ((ObjectNode) bundle.get("rendered_document")).deepCopy(); }
    private ApiException internalInfo(UUID id) { return error(id, 422, "MESSAGE_INTERNAL_INFO", "추가 문구에 내부 협상 정보가 들어 있습니다"); }
    private ApiException fileMissing(UUID id) { return error(id, 422, "FILE_MISSING", "제안서 파일이 없습니다. 다시 내보내기 하세요"); }
    private ApiException notFound(UUID id) { return ApiException.of(id, 404, "RESOURCE_NOT_FOUND", "리소스를 찾을 수 없습니다", false); }
    private static ApiException error(UUID id, int status, String code, String message) { return ApiException.of(id, status, code, message, false); }
    private ObjectNode object() { return mapper.createObjectNode(); }
    private static String now() { return OffsetDateTime.now(ZoneOffset.UTC).toString(); }
    private static UUID uuid(ObjectNode row, String field) { return UUID.fromString(row.path(field).asText()); }

    private record Prepared(ObjectNode bundle, ArrayNode attachments, String supplierId) {}
    private record Claimed(ObjectNode row, ObjectNode bundle, ArrayNode attachments) {}
    private record Outcome(String status, String sendId, String messageId, String error) {
        static Outcome failed(String code) { return new Outcome("FAILED", null, null, code); }
        static Outcome unknown(String code) { return new Outcome("UNKNOWN", null, null, code); }
    }
}
