package com.negopilot.backend.negotiation;

import com.negopilot.backend.agent.AgentProperties;
import com.negopilot.backend.agent.GenerationGateway;
import com.negopilot.backend.agent.LegalReviewRequestFactory;
import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.supplier.SupplierLookupGateway;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;
import static com.negopilot.backend.negotiation.NegotiationRequests.Result;

@Service
class NegotiationService {
    private static final List<String> GENERATION_FIELDS = List.of("supplier", "rfq_id", "as_of", "payment_days", "price_linkage",
            "raw_material_ratio", "energy_ratio", "contract_days", "linkage_waived_by_agreement", "supplier_size", "buyer_size");
    private final NegotiationRepository repository;
    private final TransactionTemplate transaction;
    private final GenerationGateway gateway;
    private final SupplierLookupGateway suppliers;
    private final LegalReviewRequestFactory reviews;
    private final JsonMapper mapper;
    private final NegotiationRequests requests;
    private final NegotiationExportStore store;
    private final long leaseSeconds;

    NegotiationService(NegotiationRepository repository, TransactionTemplate transaction, GenerationGateway gateway,
                       SupplierLookupGateway suppliers, LegalReviewRequestFactory reviews, JsonMapper mapper,
                       NegotiationRequests requests, NegotiationExportStore store, AgentProperties properties) {
        this.repository = repository; this.transaction = transaction; this.gateway = gateway; this.suppliers = suppliers;
        this.reviews = reviews; this.mapper = mapper; this.requests = requests; this.store = store;
        leaseSeconds = properties.responseTimeout().toSeconds() + 30;
    }

    Result create(UUID owner, ObjectNode body, UUID key, UUID requestId) { return create(owner, body, null, key, requestId); }

    /** titleSuffix가 있으면 기본 제목 뒤에 붙이고 100자에 맞춰 줄인다(엑셀 업로드 행). 제목까지 한 번에 저장한다. */
    Result create(UUID owner, ObjectNode body, String titleSuffix, UUID key, UUID requestId) {
        return requests.run(owner, key, "CREATE", "/negotiations", body, requestId, () -> {
            JsonNode supplier;
            try { supplier = suppliers.detail(body.path("supplier_id").asText(), requestId); }
            catch (ApiException failure) {
                if ("SUPPLIER_NOT_FOUND".equals(failure.code)) throw error(requestId, 422, failure.code);
                throw failure;
            }
            String named = body.has("title") ? body.path("title").asText()
                    : body.path("rfq_id").asText() + " · " + supplier.path("data").path("name").asText();
            String title = titleSuffix == null ? named : fit(named + " · " + titleSuffix);
            if (title.codePointCount(0, title.length()) > 100) throw ApiException.validation(requestId);
            return transaction.execute(tx -> {
                member(owner, requestId);
                ObjectNode row = body.deepCopy().put("id", UUID.randomUUID().toString()).put("owner_id", owner.toString())
                        .put("title", title).put("supplier_verified_at", now());
                repository.insert("neg_workspaces", row);
                return requests.complete(201, workspaceView(workspace(owner, uuid(row, "id"), false, requestId), false), requestId);
            });
        });
    }

    ObjectNode list(UUID owner, int limit) {
        ObjectNode result = object();
        var items = result.putArray("items");
        repository.rows("SELECT to_jsonb(w) FROM neg_workspaces w WHERE owner_id=? ORDER BY updated_at DESC,id DESC LIMIT ?", owner, limit)
                .forEach(row -> items.add(workspaceView(row, false)));
        return result;
    }

    ObjectNode get(UUID owner, UUID id, UUID requestId) { return workspaceView(workspace(owner, id, false, requestId), true); }

    Result patchWorkspace(UUID owner, UUID id, ObjectNode body, UUID requestId) {
        return transaction.execute(tx -> {
            member(owner, requestId);
            ObjectNode row = workspace(owner, id, true, requestId);
            if (body.path("row_version").longValue() != row.path("row_version").longValue()) {
                throw conflict(requestId, body.get("row_version"), row.get("row_version"));
            }
            ObjectNode update = body.deepCopy().put("row_version", row.path("row_version").longValue() + 1).put("updated_at", now());
            repository.update("neg_workspaces", update, "t.id=?", id);
            return new Result(200, workspaceView(workspace(owner, id, false, requestId), true));
        });
    }

    Result review(UUID owner, UUID id, ObjectNode body, UUID key, UUID requestId) {
        workspace(owner, id, false, requestId);
        return requests.run(owner, key, "REVIEW", id + "/reviews", body, requestId, () -> {
            UUID lockKey = UUID.nameUUIDFromBytes(("review-lease:" + id).getBytes(StandardCharsets.UTF_8));
            var lease = requests.claim(owner, lockKey, "REVIEW_LEASE", NegotiationRequests.sha256(id.toString().getBytes(StandardCharsets.UTF_8)), requestId);
            try {
                ObjectNode work = transaction.execute(tx -> {
                    member(owner, requestId);
                    ObjectNode found = workspace(owner, id, true, requestId);
                    checkReviewBase(found, body, requestId);
                    return found;
                });
                ObjectNode offer = body.deepCopy();
                offer.remove("base_revision");
                offer.put("supplier", work.path("supplier_id").asText()).put("rfq_id", work.path("rfq_id").asText());
                JsonNode upstream = gateway.review(requestId, reviews.create(requestId, offer));
                if (upstream == null || !upstream.isObject()) throw ApiException.invalidResponse(requestId);
                if ("FAILED".equals(upstream.path("execution_status").asText())) {
                    // 실행 실패는 저장하지 않고 기존 /api/legal-reviews와 같이 검증된 본문을 그대로 돌려준다
                    ObjectNode failed = ((ObjectNode) upstream).deepCopy();
                    removeTokens(failed);
                    return requests.complete(200, failed, requestId);
                }
                ObjectNode clean = ((ObjectNode) upstream).deepCopy();
                if (!Set.of("COMPLETED", "NEEDS_INPUT").contains(clean.path("execution_status").asText())) throw ApiException.invalidResponse(requestId);
                JsonNode legal = clean.get("legal_review");
                if (legal != null && !legal.isNull() && !legal.isObject()) throw ApiException.invalidResponse(requestId);
                ObjectNode saved = object().put("workspace_id", id.toString()).put("revision", work.path("review_head_revision").intValue() + 1)
                        .put("execution_status", clean.path("execution_status").asText()).put("agent_request_id", requestId.toString())
                        .put("created_by", owner.toString());
                saved.set("base_revision", body.has("base_revision") ? body.get("base_revision") : mapper.nullNode());
                for (String field : List.of("as_of", "unit_price", "payment_days", "contract_days", "price_linkage")) saved.set(field, offer.get(field));
                saved.set("input_snapshot", offer);
                if (legal != null && legal.isObject()) {
                    String assessment = legal.path("assessment").path("status").asText();
                    if (!Set.of("ready", "needs_information", "blocked").contains(assessment)) throw ApiException.invalidResponse(requestId);
                    saved.set("agent_review_id", legal.get("review_id"));
                    saved.set("input_hash", legal.get("input_hash"));
                    saved.set("review_token", legal.get("review_token"));
                    if (!legal.path("review_id").isString() || legal.path("review_id").asText().isBlank()
                            || legal.path("review_id").asText().length() > 64
                            || !legal.path("input_hash").isString() || !legal.path("input_hash").asText().matches("[0-9a-f]{64}")
                            || !legal.path("review_token").isString() || legal.path("review_token").asText().isBlank()
                            || !legal.path("expires_at").isIntegralNumber() || !legal.path("expires_at").canConvertToLong()) throw ApiException.invalidResponse(requestId);
                    try { saved.put("token_expires_at", Instant.ofEpochSecond(legal.path("expires_at").longValue()).atOffset(ZoneOffset.UTC).toString()); }
                    catch (java.time.DateTimeException failure) { throw ApiException.invalidResponse(requestId); }
                    saved.put("assessment_status", assessment).put("eligibility", "needs_information".equals(assessment) ? "conditional" : assessment);
                }
                removeTokens(clean);
                saved.set("response", clean);
                saved.set("legal_review", clean.get("legal_review"));
                return transaction.execute(tx -> {
                    member(owner, requestId);
                    ObjectNode current = workspace(owner, id, true, requestId);
                    checkReviewBase(current, body, requestId);
                    requests.assertLease(lease, requestId);
                    repository.insert("neg_review_revisions", saved);
                    repository.update("neg_workspaces", object().put("review_head_revision", saved.path("revision").intValue()).put("updated_at", now()), "t.id=?", id);
                    requests.release(lease);
                    return requests.complete(201, reviewView(reviewRow(id, saved.path("revision").intValue(), requestId)), requestId);
                });
            } finally { requests.release(lease); }
        });
    }

    ObjectNode getReview(UUID owner, UUID id, int revision, UUID requestId) {
        workspace(owner, id, false, requestId);
        return reviewView(reviewRow(id, revision, requestId));
    }

    Result propose(UUID owner, UUID id, ObjectNode body, UUID key, UUID requestId) {
        workspace(owner, id, false, requestId);
        return requests.run(owner, key, "PROPOSE", id + "/proposals", body, requestId, () -> {
            ObjectNode work = workspace(owner, id, false, requestId);
            int base = body.path("review_revision").intValue();
            checkReviewHead(work, base, requestId);
            ObjectNode review = reviewRow(id, base, requestId);
            String eligibility = review.path("eligibility").asText("");
            if (!Set.of("ready", "conditional").contains(eligibility)) {
                throw ApiException.of(requestId, 422, "REVIEW_NOT_READY", "협상안을 생성할 수 없는 검토입니다", false,
                        object().set("eligibility", review.get("eligibility")));
            }
            ObjectNode offer = object();
            for (String field : GENERATION_FIELDS) if (review.path("input_snapshot").has(field)) offer.set(field, review.path("input_snapshot").get(field));
            body.path("offer").properties().forEach(entry -> offer.set(entry.getKey(), entry.getValue()));
            ObjectNode sender = member(owner, requestId);
            sender.remove("status");
            offer.set("sender", sender);
            ObjectNode upstream = (ObjectNode) gateway.propose(requestId, offer);
            UUID pid = UUID.randomUUID();
            JsonNode document = upstream.get("offer_document");
            boolean external = document != null && document.isObject();
            ObjectNode proposal = object().put("id", pid.toString()).put("workspace_id", id.toString()).put("review_revision", base)
                    .put("generation_status", external ? "offer" : "no_offer").put("head_revision", 1).put("created_by", owner.toString());
            proposal.set("generation_input", object().put("request_id", requestId.toString()).set("offer", offer));
            if (external) proposal.put("mail_document_id", "NP-" + work.path("rfq_id").asText() + "-" + work.path("supplier_id").asText() + "-" + pid.toString().substring(0, 8));
            ObjectNode internal = ((ObjectNode) upstream.path("internal_briefing")).deepCopy();
            JsonNode terms = external ? document : internal.path("offer");
            ObjectNode revision = object().put("proposal_id", pid.toString()).put("revision", 1).put("origin", "GENERATED")
                    .put("terms_rev", 1).put("internal_content_rev", 1).put("created_by", owner.toString());
            for (String field : NegotiationDocumentAssembler.SHARED_FIELDS) {
                JsonNode value = terms.get(field);
                if ("as_of".equals(field) && (value == null || value.isNull())) value = review.path("input_snapshot").get("as_of");
                revision.set(field, value);
                if (internal.path("offer").isObject()) ((ObjectNode) internal.get("offer")).remove(field);
            }
            revision.set("internal_body", internal);
            if (external) {
                revision.set("external_body", NegotiationDocumentAssembler.externalBody(document));
                revision.put("external_content_rev", 1);
            }
            ObjectNode validation = checked(pid, 1, "GENERATION", owner, requestId, upstream.path("check"), eligibility, unresolved(review));
            return transaction.execute(tx -> {
                member(owner, requestId);
                checkReviewHead(workspace(owner, id, true, requestId), base, requestId);
                repository.execute("UPDATE neg_proposals SET status='SUPERSEDED',updated_at=clock_timestamp() WHERE workspace_id=? AND status='ACTIVE'", id);
                repository.insert("neg_proposals", proposal);
                repository.insert("neg_proposal_revisions", revision);
                // no_offer는 검사할 협상안이 없으므로 "위험 미발견" 결과를 남기지 않는다(not_checked)
                if (external) repository.insert("neg_validations", validation);
                repository.update("neg_workspaces", object().put("current_proposal_id", pid.toString()).put("rfq_verified_at", now()).put("updated_at", now()), "t.id=?", id);
                return requests.complete(201, getProposal(owner, id, pid, null, requestId), requestId);
            });
        });
    }

    ObjectNode getProposal(UUID owner, UUID id, UUID pid, Integer requestedRevision, UUID requestId) {
        ObjectNode work = workspace(owner, id, false, requestId);
        ObjectNode proposal = proposal(id, pid, false, requestId);
        int number = requestedRevision == null ? proposal.path("head_revision").intValue() : requestedRevision;
        ObjectNode revision = revision(pid, number, requestId);
        ObjectNode result = object().put("proposal_id", pid.toString()).put("proposal_revision", number)
                .put("head_revision", proposal.path("head_revision").intValue()).put("status", proposal.path("status").asText())
                .put("review_recheck_required", proposal.path("review_revision").intValue() != work.path("review_head_revision").intValue());
        result.set("based_on_review", object().put("review_id", id.toString()).put("review_revision", proposal.path("review_revision").intValue()));
        ObjectNode internal = ((ObjectNode) revision.path("internal_body")).deepCopy();
        if (internal.path("offer").isObject()) for (String field : NegotiationDocumentAssembler.SHARED_FIELDS) ((ObjectNode) internal.get("offer")).set(field, revision.get(field));
        result.set("proposal", internal);
        result.set("external_document", hasOffer(proposal) ? NegotiationDocumentAssembler.assemble(work, proposal, revision, "draft") : mapper.nullNode());
        result.set("generation_input_snapshot", proposal.get("generation_input"));
        result.set("validation", validationView(pid, number));
        var history = result.putArray("edit_history");
        List<ObjectNode> revisions = revisionRows(pid);
        revisions.stream().filter(row -> row.path("revision").intValue() > 1 && row.path("revision").intValue() <= number).forEach(row -> {
            ObjectNode edit = object().put("from_revision", row.path("base_revision").intValue()).put("to_revision", row.path("revision").intValue());
            edit.set("edited_by", row.get("created_by")); edit.set("edited_at", row.get("created_at"));
            edit.set("changes", row.get("change_set")); edit.set("change_note", row.get("change_note")); history.add(edit);
        });
        boolean stale = revisions.stream().filter(row -> row.path("revision").intValue() <= number)
                .anyMatch(row -> contains(row.path("changed_scopes"), "TERMS") || contains(row.path("changed_scopes"), "PRICE_RANGE"));
        result.put("derived_stale", stale);
        ObjectNode formats = result.putObject("available_export_formats");
        formats.putArray("internal");
        var external = formats.putArray("external");
        if (hasOffer(proposal)) external.add("pdf").add("docx");
        return result;
    }

    NegotiationExternalView external(UUID owner, UUID id, UUID pid, Integer number, UUID requestId) {
        ObjectNode work = workspace(owner, id, false, requestId);
        ObjectNode proposal = proposal(id, pid, false, requestId);
        requireOffer(proposal, requestId);
        int selected = number == null ? proposal.path("head_revision").intValue() : number;
        return NegotiationExternalView.from(work, proposal, revision(pid, selected, requestId), validationView(pid, selected));
    }

    Result patchProposal(UUID owner, UUID id, UUID pid, ObjectNode body, UUID key, UUID requestId) {
        workspace(owner, id, false, requestId);
        proposal(id, pid, false, requestId);
        return requests.run(owner, key, "PATCH_PROPOSAL", id + "/proposals/" + pid, body, requestId, () -> transaction.execute(tx -> {
            member(owner, requestId);
            workspace(owner, id, true, requestId);
            ObjectNode proposal = proposal(id, pid, true, requestId);
            if (!"ACTIVE".equals(proposal.path("status").asText())) throw error(requestId, 422, "PROPOSAL_SUPERSEDED");
            if (proposal.path("head_revision").intValue() != body.path("base_revision").intValue()) throw conflict(requestId, body.get("base_revision"), proposal.get("head_revision"));
            var edit = NegotiationProposalEditor.edit(requestId, revision(pid, proposal.path("head_revision").intValue(), requestId), (ObjectNode) body.get("changes"));
            if (edit.changed()) {
                ObjectNode row = edit.revision();
                row.put("created_by", owner.toString()).put("created_at", now());
                row.set("change_note", body.get("change_note"));
                repository.insert("neg_proposal_revisions", row);
                repository.update("neg_proposals", object().put("head_revision", row.path("revision").intValue()).put("updated_at", now()), "t.id=?", pid);
                repository.update("neg_workspaces", object().put("updated_at", now()), "t.id=?", id);
            }
            ObjectNode result = getProposal(owner, id, pid, null, requestId);
            result.set("impacts", edit.impacts());
            return requests.complete(200, result, requestId);
        }));
    }

    Result validate(UUID owner, UUID id, UUID pid, int number, ObjectNode body, UUID key, UUID requestId) {
        workspace(owner, id, false, requestId);
        proposal(id, pid, false, requestId);
        return requests.run(owner, key, "VALIDATE", id + "/" + pid + "/" + number + "/validations", body, requestId, () -> {
            ObjectNode started = transaction.execute(tx -> {
                member(owner, requestId);
                workspace(owner, id, true, requestId);
                ObjectNode proposal = proposal(id, pid, true, requestId);
                requireOffer(proposal, requestId);
                revision(pid, number, requestId);
                expireValidations(pid, number);
                if (repository.one("SELECT to_jsonb(v) FROM neg_validations v WHERE proposal_id=? AND revision=? AND status='checking'", pid, number) != null) {
                    throw ApiException.of(requestId, 409, "VALIDATION_IN_PROGRESS", "해당 버전을 검사 중입니다", true);
                }
                ObjectNode row = object().put("id", UUID.randomUUID().toString()).put("proposal_id", pid.toString()).put("revision", number)
                        .put("origin", "RECHECK").put("status", "checking").put("lease_until", OffsetDateTime.now(ZoneOffset.UTC).plusSeconds(leaseSeconds).toString())
                        .put("agent_request_id", requestId.toString()).put("requested_by", owner.toString());
                repository.insert("neg_validations", row);
                return row;
            });
            try {
                ObjectNode work = workspace(owner, id, false, requestId);
                ObjectNode proposal = proposal(id, pid, false, requestId);
                ObjectNode review = reviewRow(id, proposal.path("review_revision").intValue(), requestId);
                ObjectNode finished;
                try {
                    JsonNode rendered = gateway.render(requestId, NegotiationDocumentAssembler.assemble(work, proposal, revision(pid, number, requestId), "draft"), "pdf");
                    finished = checked(pid, number, "RECHECK", owner, requestId, rendered.path("check"), review.path("eligibility").asText(), unresolved(review));
                } catch (ApiException failure) {
                    if (!"OFFER_VIOLATION".equals(failure.code) || failure.status.value() != 422) throw failure;
                    finished = checked(pid, number, "RECHECK", owner, requestId, object().put("verdict_code", "VIOLATION"), review.path("eligibility").asText(), unresolved(review));
                    JsonNode details = mapper.valueToTree(failure.details);
                    finished.set("violations", details.path("findings"));
                }
                finished.remove(List.of("id", "proposal_id", "revision", "origin", "requested_by", "agent_request_id"));
                finished.putNull("lease_until");
                ObjectNode update = finished;
                return transaction.execute(tx -> {
                    member(owner, requestId);
                    workspace(owner, id, true, requestId);
                    int changed = repository.update("neg_validations", update, "t.id=? AND t.status='checking' AND t.lease_until>clock_timestamp()", uuid(started, "id"));
                    if (changed != 1) throw ApiException.of(requestId, 409, "VALIDATION_IN_PROGRESS", "검사 임대가 만료되었습니다", true);
                    ObjectNode response = object();
                    response.set("execution", repository.one("SELECT to_jsonb(v) FROM neg_validations v WHERE id=?", uuid(started, "id")));
                    response.set("validation", validationView(pid, proposal(id, pid, false, requestId).path("head_revision").intValue()));
                    return requests.complete(200, response, requestId);
                });
            } catch (RuntimeException failure) {
                failValidation(uuid(started, "id"), failure);
                throw failure;
            }
        });
    }

    ObjectNode validations(UUID owner, UUID id, UUID pid, int number, UUID requestId) {
        workspace(owner, id, false, requestId);
        proposal(id, pid, false, requestId);
        revision(pid, number, requestId);
        expireValidations(pid, number);
        ObjectNode result = object();
        var items = result.putArray("items");
        repository.rows("SELECT to_jsonb(v) FROM neg_validations v WHERE proposal_id=? AND revision=? ORDER BY started_at DESC,id DESC", pid, number).forEach(items::add);
        return result;
    }

    Result export(UUID owner, UUID id, UUID pid, ObjectNode body, UUID key, UUID requestId) {
        workspace(owner, id, false, requestId);
        proposal(id, pid, false, requestId);
        return requests.run(owner, key, "EXPORT", id + "/" + pid + "/exports", body, requestId, () -> {
            int number = body.path("revision").intValue();
            boolean finalDocument = body.path("final").asBoolean(false);
            List<String> formats = new ArrayList<>();
            body.path("formats").forEach(value -> formats.add(value.asText()));
            formats.sort(String::compareTo);
            ObjectNode canonicalFormats = object(); canonicalFormats.set("formats", mapper.valueToTree(formats));
            String formatsJson = mapper.writeValueAsString(canonicalFormats.get("formats"));
            ExportClaim claim = transaction.execute(tx -> {
                member(owner, requestId);
                ObjectNode work = workspace(owner, id, true, requestId);
                ObjectNode proposal = proposal(id, pid, true, requestId);
                requireOffer(proposal, requestId);
                ObjectNode revision = revision(pid, number, requestId);
                if (finalDocument) finalPolicy(work, proposal, number, requestId);
                repository.execute("UPDATE neg_export_bundles SET status='failed',error_code='STALE_EXPORT' WHERE proposal_id=? AND status='processing' "
                        + "AND created_at < clock_timestamp() - make_interval(secs => ?)", pid, leaseSeconds);
                ObjectNode prior = repository.one("SELECT to_jsonb(b) FROM neg_export_bundles b WHERE proposal_id=? AND revision=? AND doc_type='EXTERNAL' "
                        + "AND doc_status=? AND to_jsonb(formats)=?::jsonb AND status IN ('ready','processing')", pid, number, finalDocument ? "final" : "draft", formatsJson);
                if (prior != null) {
                    if ("processing".equals(prior.path("status").asText())) throw ApiException.of(requestId, 409, "IDEMPOTENCY_CONFLICT", "내보내기를 처리 중입니다", true);
                    if (filesIntact(uuid(prior, "id"))) return new ExportClaim(prior, null, requests.complete(200, bundleView(prior), requestId));
                    repository.update("neg_export_bundles", object().put("status", "failed").put("error_code", "FILE_MISSING"), "t.id=? AND t.status='ready'", uuid(prior, "id"));
                }
                ObjectNode bundle = object().put("id", UUID.randomUUID().toString()).put("proposal_id", pid.toString()).put("revision", number)
                        .put("doc_type", "EXTERNAL").put("doc_status", finalDocument ? "final" : "draft").put("status", "processing").put("created_by", owner.toString());
                bundle.set("formats", mapper.valueToTree(formats));
                bundle.set("validation_snapshot", validationSummary(validationView(pid, number)));
                repository.insert("neg_export_bundles", bundle);
                return new ExportClaim(bundle, NegotiationDocumentAssembler.assemble(work, proposal, revision, finalDocument ? "final" : "draft"), null);
            });
            if (claim.cached() != null) return claim.cached();
            UUID bundleId = uuid(claim.bundle(), "id");
            try {
                JsonNode rendered = gateway.render(requestId, claim.document(), formats.size() == 2 ? "both" : formats.getFirst());
                List<ObjectNode> files = new ArrayList<>();
                Set<String> seen = new java.util.HashSet<>();
                for (JsonNode attachment : rendered.path("attachments")) {
                    String filename = attachment.path("filename").asText();
                    if (filename.codePointCount(0, filename.length()) > 200) throw ApiException.invalidResponse(requestId);
                    String format = filename.endsWith(".pdf") ? "pdf" : filename.endsWith(".docx") ? "docx" : "";
                    if (!formats.contains(format) || !seen.add(format)) throw ApiException.invalidResponse(requestId);
                    String mediaType = "pdf".equals(format) ? "application/pdf" : "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
                    if (!mediaType.equals(attachment.path("content_type").asText())) throw ApiException.invalidResponse(requestId);
                    byte[] bytes;
                    try { bytes = Base64.getDecoder().decode(attachment.path("data_base64").asText()); }
                    catch (IllegalArgumentException failure) { throw ApiException.invalidResponse(requestId); }
                    if (bytes.length == 0) throw ApiException.invalidResponse(requestId);
                    UUID fileId = UUID.randomUUID();
                    OffsetDateTime time = OffsetDateTime.now(ZoneOffset.UTC);
                    String storageKey = "exports/%04d/%02d/%s.%s".formatted(time.getYear(), time.getMonthValue(), fileId, format);
                    String hash = NegotiationRequests.sha256(bytes);
                    try {
                        store.write(storageKey, bytes);
                        if (!hash.equals(NegotiationRequests.sha256(store.read(storageKey)))) throw ApiException.internal(requestId);
                    } catch (java.io.IOException failure) { throw ApiException.internal(requestId); }
                    files.add(object().put("id", fileId.toString()).put("bundle_id", bundleId.toString()).put("format", format)
                            .put("storage_key", storageKey).put("file_name", filename).put("content_type", mediaType).put("byte_size", bytes.length).put("sha256", hash));
                }
                if (seen.size() != formats.size()) throw ApiException.invalidResponse(requestId);
                return transaction.execute(tx -> {
                    member(owner, requestId);
                    ObjectNode work = workspace(owner, id, true, requestId);
                    ObjectNode proposal = proposal(id, pid, true, requestId);
                    if (finalDocument) finalPolicy(work, proposal, number, requestId);
                    ObjectNode ready = object().put("status", "ready");
                    ready.set("rendered_document", rendered.get("document"));
                    ready.set("content_hash", rendered.path("document").get("content_hash"));
                    ready.set("render_check", rendered.get("check"));
                    ready.set("validation_snapshot", validationSummary(validationView(pid, number)));
                    files.forEach(file -> repository.insert("neg_export_files", file));
                    repository.update("neg_export_bundles", ready, "t.id=?", bundleId);
                    return requests.complete(201, bundleView(repository.one("SELECT to_jsonb(b) FROM neg_export_bundles b WHERE id=?", bundleId)), requestId);
                });
            } catch (RuntimeException failure) {
                repository.update("neg_export_bundles", object().put("status", "failed").put("error_code", errorCode(failure)), "t.id=? AND t.status='processing'", bundleId);
                throw failure;
            }
        });
    }

    record Download(byte[] bytes, String contentType, String filename) {}
    Download download(UUID owner, UUID fileId, UUID requestId) {
        ObjectNode file = repository.one("SELECT to_jsonb(f) FROM neg_export_files f JOIN neg_export_bundles b ON b.id=f.bundle_id "
                + "JOIN neg_proposals p ON p.id=b.proposal_id JOIN neg_workspaces w ON w.id=p.workspace_id WHERE f.id=? AND w.owner_id=? AND b.status='ready'", fileId, owner);
        if (file == null) throw notFound(requestId);
        try {
            byte[] bytes = store.read(file.path("storage_key").asText());
            if (!NegotiationRequests.sha256(bytes).equals(file.path("sha256").asText())) throw ApiException.internal(requestId);
            return new Download(bytes, file.path("content_type").asText(), file.path("file_name").asText());
        } catch (java.io.IOException failure) { throw ApiException.internal(requestId); }
    }

    private ObjectNode bundleView(ObjectNode bundle) {
        ObjectNode result = object().put("bundle_id", bundle.path("id").asText()).put("status", bundle.path("status").asText())
                .put("final", "final".equals(bundle.path("doc_status").asText())).put("document_type", "external").put("proposal_revision", bundle.path("revision").intValue());
        for (String key : List.of("content_hash", "render_check", "validation_snapshot")) result.set(key, bundle.get(key));
        var files = result.putArray("files");
        repository.rows("SELECT to_jsonb(f) FROM neg_export_files f WHERE bundle_id=? ORDER BY format", uuid(bundle, "id")).forEach(file -> {
            ObjectNode view = object().put("file_id", file.path("id").asText());
            for (String field : List.of("format", "file_name", "content_type", "byte_size", "sha256")) view.set(field, file.get(field));
            view.put("download_url", "/api/negotiation-exports/" + file.path("id").asText() + "/file"); files.add(view);
        });
        return result;
    }

    private void finalPolicy(ObjectNode work, ObjectNode proposal, int number, UUID requestId) {
        var reasons = mapper.createArrayNode();
        if (number != proposal.path("head_revision").intValue()) reasons.add("REVISION_NOT_HEAD");
        if (!"ACTIVE".equals(proposal.path("status").asText())) reasons.add("PROPOSAL_SUPERSEDED");
        if (proposal.path("review_revision").intValue() != work.path("review_head_revision").intValue()) reasons.add("REVIEW_RECHECK_REQUIRED");
        if (!reasons.isEmpty()) throw ApiException.of(requestId, 422, "EXPORT_NOT_ALLOWED", "확정본 내보내기 조건을 확인하세요", false, object().set("reasons", reasons));
    }

    private ObjectNode workspace(UUID owner, UUID id, boolean lock, UUID requestId) {
        ObjectNode row = repository.one("SELECT to_jsonb(w) FROM neg_workspaces w WHERE id=? AND owner_id=?" + (lock ? " FOR UPDATE" : ""), id, owner);
        if (row == null) throw notFound(requestId);
        return row;
    }
    private ObjectNode proposal(UUID workspace, UUID pid, boolean lock, UUID requestId) {
        ObjectNode row = repository.one("SELECT to_jsonb(p) FROM neg_proposals p WHERE id=? AND workspace_id=?" + (lock ? " FOR UPDATE" : ""), pid, workspace);
        if (row == null) throw notFound(requestId);
        return row;
    }
    private ObjectNode revision(UUID pid, int number, UUID requestId) {
        ObjectNode row = repository.one("SELECT to_jsonb(r) FROM neg_proposal_revisions r WHERE proposal_id=? AND revision=?", pid, number);
        if (row == null) throw notFound(requestId);
        return row;
    }
    private ObjectNode reviewRow(UUID id, int number, UUID requestId) {
        ObjectNode row = repository.one("SELECT to_jsonb(r) FROM neg_review_revisions r WHERE workspace_id=? AND revision=?", id, number);
        if (row == null) throw notFound(requestId);
        return row;
    }
    private List<ObjectNode> revisionRows(UUID pid) {
        return repository.rows("SELECT to_jsonb(r) FROM neg_proposal_revisions r WHERE proposal_id=? ORDER BY revision", pid);
    }
    private ObjectNode validationView(UUID pid, int head) {
        return NegotiationValidationView.calculate(head, revisionRows(pid), repository.rows("SELECT to_jsonb(v) FROM neg_validations v WHERE proposal_id=? ORDER BY started_at DESC,id DESC", pid), OffsetDateTime.now(ZoneOffset.UTC));
    }
    private ObjectNode validationSummary(ObjectNode view) {
        ObjectNode result = object();
        for (String field : List.of("status", "checked_revision", "conclusion")) result.set(field, view.get(field));
        return result;
    }
    private ObjectNode workspaceView(ObjectNode row, boolean detail) {
        ObjectNode view = object().put("negotiation_id", row.path("id").asText()).put("review_id", row.path("id").asText());
        for (String key : List.of("supplier_id", "rfq_id", "title", "status", "row_version", "review_head_revision", "current_proposal_id", "rfq_verified_at", "created_at", "updated_at")) view.set(key, row.get(key));
        view.put("stage", !row.path("current_proposal_id").isNull() && row.has("current_proposal_id") ? "PROPOSED" : row.path("review_head_revision").intValue() > 0 ? "REVIEWED" : "NO_REVIEW");
        if (detail) {
            view.putNull("eligibility"); view.putNull("current_proposal");
            if (row.path("review_head_revision").intValue() > 0) view.set("eligibility", reviewRow(uuid(row, "id"), row.path("review_head_revision").intValue(), null).get("eligibility"));
            if (row.hasNonNull("current_proposal_id")) {
                UUID pid = uuid(row, "current_proposal_id");
                ObjectNode proposal = proposal(uuid(row, "id"), pid, false, null);
                ObjectNode summary = object().put("proposal_id", pid.toString()).put("head_revision", proposal.path("head_revision").intValue());
                summary.set("validation", validationView(pid, proposal.path("head_revision").intValue())); view.set("current_proposal", summary);
            }
        }
        return view;
    }
    private ObjectNode reviewView(ObjectNode row) {
        ObjectNode view = ((ObjectNode) row.path("response")).deepCopy();
        removeTokens(view);
        view.put("review_id", row.path("workspace_id").asText()).put("review_revision", row.path("revision").intValue());
        for (String key : List.of("base_revision", "input_snapshot", "eligibility", "created_at")) view.set(key, row.get(key));
        return view;
    }
    private ObjectNode member(UUID owner, UUID requestId) {
        ObjectNode row = repository.one("SELECT jsonb_build_object('name',name,'department',department,'job_title',job_title,'email',email,'status',status) FROM app_users WHERE id=? FOR SHARE", owner);
        if (row == null || !"ACTIVE".equals(row.path("status").asText())) throw ApiException.of(requestId, 403, "ACCOUNT_NOT_ACTIVE", "활성 회원만 사용할 수 있습니다", false);
        return row;
    }
    private void checkReviewBase(ObjectNode work, ObjectNode input, UUID id) {
        int head = work.path("review_head_revision").intValue();
        JsonNode base = input.get("base_revision");
        if (head == 0 ? base != null && !base.isNull() : base == null || !base.isIntegralNumber() || base.intValue() != head) {
            throw conflict(id, base, work.get("review_head_revision"));
        }
    }
    private void checkReviewHead(ObjectNode work, int expected, UUID id) {
        if (expected != work.path("review_head_revision").intValue()) throw error(id, 409, "REVIEW_RECHECK_REQUIRED");
    }
    private ObjectNode checked(UUID pid, int number, String origin, UUID owner, UUID requestId, JsonNode check, String eligibility, JsonNode unresolved) {
        String verdict = check.path("verdict_code").asText("");
        String conclusion = "VIOLATION".equals(verdict) ? "risk_identified" : "CHECK_NEEDED".equals(verdict) || "conditional".equals(eligibility) ? "conditional" : "no_risk_identified_in_scope";
        ObjectNode row = object().put("id", UUID.randomUUID().toString()).put("proposal_id", pid.toString()).put("revision", number)
                .put("origin", origin).put("status", "checked").put("conclusion", conclusion).put("requested_by", owner.toString())
                .put("agent_request_id", requestId.toString()).put("finished_at", now());
        row.putArray("scope").add("선택한 협상안의 조건 검사");
        var limitations = row.putArray("limitations");
        if ("GENERATION".equals(origin)) {
            ((tools.jackson.databind.node.ArrayNode) row.get("scope")).add("협상 범위의 가격별 검사");
            limitations.add("양보 순서의 모든 조건 조합을 검사하지는 않았습니다");
        } else limitations.add("협상 범위(목표 합의가·최대 수용가) 가격은 이번 재검사에 포함되지 않았습니다");
        limitations.add("문구 검사는 아직 구현되지 않았습니다");
        row.set("warnings", check.path("warnings").isArray() ? check.get("warnings") : mapper.createArrayNode());
        row.putArray("violations"); row.putNull("text_findings"); row.set("unresolved_issue_ids", unresolved);
        return row;
    }
    private JsonNode unresolved(ObjectNode review) {
        var ids = mapper.createArrayNode();
        for (JsonNode finding : review.path("legal_review").path("assessment").path("findings")) {
            if (!"supported".equals(finding.path("status").asText()) && finding.path("id").isString()) ids.add(finding.get("id"));
        }
        return ids;
    }
    private void expireValidations(UUID pid, int revision) {
        repository.execute("UPDATE neg_validations SET status='failed',error_code='STALE_CHECK',finished_at=clock_timestamp(),lease_until=NULL "
                + "WHERE proposal_id=? AND revision=? AND status='checking' AND lease_until<=clock_timestamp()", pid, revision);
    }
    private void failValidation(UUID id, RuntimeException failure) {
        repository.update("neg_validations", object().put("status", "failed").put("error_code", errorCode(failure)).put("finished_at", now()).putNull("lease_until"), "t.id=? AND t.status='checking'", id);
    }
    private static void removeTokens(JsonNode node) {
        if (node.isObject()) ((ObjectNode) node).remove("review_token");
        for (JsonNode child : node) removeTokens(child);
    }
    private boolean hasOffer(ObjectNode proposal) { return "offer".equals(proposal.path("generation_status").asText()); }
    private void requireOffer(ObjectNode proposal, UUID requestId) { if (!hasOffer(proposal)) throw error(requestId, 422, "NO_OFFER"); }
    private static boolean contains(JsonNode values, String expected) { for (JsonNode value : values) if (expected.equals(value.asText())) return true; return false; }
    private String errorCode(RuntimeException failure) { return failure instanceof ApiException api ? api.code : "INTERNAL_ERROR"; }
    private ApiException conflict(UUID id, JsonNode requested, JsonNode current) {
        ObjectNode details = object(); details.set("requested_revision", requested); details.set("current_revision", current);
        return ApiException.of(id, 409, "REVISION_CONFLICT", "현재 버전과 요청 버전이 다릅니다", false, details);
    }
    private ApiException notFound(UUID id) { return ApiException.of(id, 404, "RESOURCE_NOT_FOUND", "리소스를 찾을 수 없습니다", false); }
    private ApiException error(UUID id, int status, String code) { return ApiException.of(id, status, code, "요청 조건을 확인하세요", false); }
    private ObjectNode object() { return mapper.createObjectNode(); }

    private boolean filesIntact(UUID bundleId) {
        for (ObjectNode file : repository.rows("SELECT to_jsonb(f) FROM neg_export_files f WHERE bundle_id=?", bundleId)) {
            try {
                if (!NegotiationRequests.sha256(store.read(file.path("storage_key").asText())).equals(file.path("sha256").asText())) return false;
            } catch (java.io.IOException | RuntimeException missing) {
                return false;
            }
        }
        return true;
    }
    private static String now() { return OffsetDateTime.now(ZoneOffset.UTC).toString(); }
    private static UUID uuid(ObjectNode row, String field) { return UUID.fromString(row.path(field).asText()); }
    private static String fit(String title) {
        if (title.codePointCount(0, title.length()) <= 100) return title;
        return title.substring(0, title.offsetByCodePoints(0, 99)) + "…";
    }
    private record ExportClaim(ObjectNode bundle, ObjectNode document, Result cached) {}
}
