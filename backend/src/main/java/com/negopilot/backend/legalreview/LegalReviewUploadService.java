package com.negopilot.backend.legalreview;

import com.negopilot.backend.common.api.ApiException;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.JsonNodeFactory;
import tools.jackson.databind.node.ObjectNode;

/**
 * 엑셀 업로드 → 행별 저장·검토 요청, 소유자 조회, 실패 행 재시도.
 * 조건을 바꾼 재검토는 행에 연결한 협상 작업의 POST /api/negotiations/{id}/reviews로 한다.
 */
@Service
final class LegalReviewUploadService {
    private static final List<String> STATUSES = List.of("INVALID", "QUEUED", "RUNNING", "COMPLETED", "FAILED");
    private final LegalReviewUploadStore store;
    private final LegalReviewRunner runner;
    private final TransactionTemplate transaction;
    private final JsonMapper mapper;

    LegalReviewUploadService(LegalReviewUploadStore store, LegalReviewRunner runner, TransactionTemplate transaction,
                             JsonMapper mapper) {
        this.store = store;
        this.runner = runner;
        this.transaction = transaction;
        this.mapper = mapper;
    }

    ObjectNode upload(UUID owner, String fileName, byte[] data, UUID requestId) {
        ReviewWorkbookParser.Parsed parsed = ReviewWorkbookParser.parse(data, requestId);
        UUID id = UUID.randomUUID();
        List<LegalReviewUploadStore.Key> queued = new ArrayList<>();
        transaction.executeWithoutResult(status -> {
            ObjectNode upload = object().put("id", id.toString()).put("owner_id", owner.toString()).put("file_name", fileName)
                    .put("file_sha256", sha256(data)).put("row_count", parsed.rows().size());
            upload.set("sheet_errors", mapper.valueToTree(parsed.sheetErrors()));
            store.insertUpload(upload);
            for (ReviewWorkbookParser.Row row : parsed.rows()) {
                ObjectNode saved = object().put("upload_id", id.toString()).put("row_number", row.rowNumber())
                        .put("status", row.valid() ? "QUEUED" : "INVALID").put("created_by", owner.toString());
                saved.set("source_cells", row.source());
                if (row.supplierId() != null) saved.put("supplier_id", row.supplierId());
                if (row.rfqId() != null) saved.put("rfq_id", row.rfqId());
                if (row.valid()) {
                    saved.set("offer", row.offer());
                    queued.add(new LegalReviewUploadStore.Key(id, row.rowNumber()));
                } else {
                    saved.set("input_errors", mapper.valueToTree(row.errors()));
                }
                store.insertRow(saved);
            }
        });
        // 커밋한 뒤에 실행기에 넣어야 실행기가 행을 찾는다
        queued.forEach(runner::submit);
        return uploadView(store.upload(id, owner), true);
    }

    ObjectNode list(UUID owner, int limit) {
        store.expireRunning(false);
        ObjectNode body = object();
        var items = body.putArray("items");
        for (ObjectNode upload : store.uploads(owner, limit)) items.add(uploadView(upload, false));
        return body;
    }

    ObjectNode get(UUID owner, UUID id, UUID requestId) {
        store.expireRunning(false);
        return uploadView(owned(owner, id, requestId), true);
    }

    ObjectNode row(UUID owner, UUID id, int rowNumber, UUID requestId) {
        store.expireRunning(false);
        owned(owner, id, requestId);
        ObjectNode row = store.row(new LegalReviewUploadStore.Key(id, rowNumber));
        if (row == null) throw notFound(requestId);
        return LegalReviewRowView.detail(row, mapper);
    }

    /** 실행에 실패한 행을 같은 입력으로 다시 검토한다. 이미 만든 협상 작업이 있으면 그 작업에 검토를 저장한다. */
    ObjectNode retry(UUID owner, UUID id, int rowNumber, UUID requestId) {
        store.expireRunning(false);
        owned(owner, id, requestId);
        var key = new LegalReviewUploadStore.Key(id, rowNumber);
        ObjectNode row = store.row(key);
        if (row == null) throw notFound(requestId);
        switch (row.get("status").stringValue()) {
            case "INVALID" -> throw ApiException.of(requestId, 422, "ROW_INVALID", "입력 오류가 있는 행은 엑셀을 고쳐 다시 올려야 합니다", false);
            case "QUEUED", "RUNNING" -> throw ApiException.of(requestId, 409, "REVIEW_IN_PROGRESS", "이 행의 검토가 아직 진행 중입니다", true);
            case "COMPLETED" -> throw ApiException.of(requestId, 409, "ROW_ALREADY_REVIEWED",
                    "검토를 마친 행입니다. 조건을 바꾼 재검토는 협상 작업의 검토 API로 요청합니다", false,
                    Map.of("negotiation_id", row.get("negotiation_id").stringValue()));
            default -> { }
        }
        // 동시에 두 번 요청하면 한쪽만 대기열로 되돌린다
        if (!store.requeue(key)) throw ApiException.of(requestId, 409, "REVIEW_IN_PROGRESS", "이 행의 검토가 아직 진행 중입니다", true);
        runner.submit(key);
        return LegalReviewRowView.detail(store.row(key), mapper);
    }

    private ObjectNode uploadView(ObjectNode upload, boolean withRows) {
        UUID id = UUID.fromString(upload.get("id").stringValue());
        List<ObjectNode> rows = store.rows(id);
        Map<String, Integer> counts = new LinkedHashMap<>();
        for (String status : STATUSES) counts.put(status.toLowerCase(), 0);
        for (ObjectNode row : rows) counts.merge(row.get("status").stringValue().toLowerCase(), 1, Integer::sum);
        ObjectNode view = object().put("upload_id", id.toString());
        view.set("file_name", upload.get("file_name"));
        view.set("row_count", upload.get("row_count"));
        view.set("created_at", upload.get("created_at"));
        view.put("status", counts.get("queued") + counts.get("running") > 0 ? "PROCESSING" : "DONE");
        view.set("counts", mapper.valueToTree(counts));
        view.set("sheet_errors", upload.get("sheet_errors"));
        if (withRows) {
            var list = view.putArray("rows");
            for (ObjectNode row : rows) list.add(LegalReviewRowView.summary(row, mapper));
        }
        return view;
    }

    private ObjectNode owned(UUID owner, UUID id, UUID requestId) {
        ObjectNode upload = store.upload(id, owner);
        if (upload == null) throw notFound(requestId);
        return upload;
    }

    private static ApiException notFound(UUID id) {
        return ApiException.of(id, 404, "RESOURCE_NOT_FOUND", "리소스를 찾을 수 없습니다", false);
    }

    private static ObjectNode object() { return JsonNodeFactory.instance.objectNode(); }

    private static String sha256(byte[] data) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(data)); }
        catch (NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
    }
}
