package com.negopilot.backend.legalreview;

import com.negopilot.backend.auth.CurrentUser;
import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.common.api.ApiPaths;
import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import tools.jackson.databind.JsonNode;

/** 엑셀 거래·계약 조건 업로드와 행별 법규 검토 (docs/contracts/legal-review-upload.md). */
@RestController
final class LegalReviewUploadController {
    static final long MAX_FILE_BYTES = 1024 * 1024;
    private static final String BASE = ApiPaths.LEGAL_REVIEW_UPLOADS;
    private final LegalReviewUploadService service;
    private final CurrentUser currentUser;

    LegalReviewUploadController(LegalReviewUploadService service, CurrentUser currentUser) {
        this.service = service;
        this.currentUser = currentUser;
    }

    @PostMapping(value = BASE, consumes = MediaType.MULTIPART_FORM_DATA_VALUE, produces = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<JsonNode> upload(@RequestParam(name = "file", required = false) MultipartFile file, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId();
        UUID id = begin(request, Set.of("supplier_id"));
        String fileName = fileName(file);
        if (fileName == null || file.isEmpty() || file.getSize() > MAX_FILE_BYTES) throw ApiException.validation(id);
        // 주면 그 협력사의 행만 검토한다(08-2 화면은 한 협력사의 행만 쓴다)
        String supplierId = request.getParameter("supplier_id");
        if (supplierId != null && !supplierId.matches("SUP-[0-9]{4}")) throw ApiException.validation(id);
        byte[] data;
        try { data = file.getBytes(); }
        catch (IOException e) { throw ApiException.validation(id); }
        return ResponseEntity.status(HttpStatus.ACCEPTED).body(service.upload(owner, fileName, data, supplierId, id));
    }

    @GetMapping(value = BASE, produces = MediaType.APPLICATION_JSON_VALUE)
    JsonNode list(HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId();
        UUID id = begin(request, Set.of("limit"));
        return service.list(owner, bounded(request.getParameter("limit"), 20, 1, 50, id));
    }

    @GetMapping(value = BASE + "/{uploadId}", produces = MediaType.APPLICATION_JSON_VALUE)
    JsonNode get(@PathVariable String uploadId, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId();
        UUID id = begin(request, Set.of());
        return service.get(owner, uuid(uploadId, id), id);
    }

    @GetMapping(value = BASE + "/{uploadId}/rows/{rowNumber}", produces = MediaType.APPLICATION_JSON_VALUE)
    JsonNode row(@PathVariable String uploadId, @PathVariable String rowNumber, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId();
        UUID id = begin(request, Set.of());
        return service.row(owner, uuid(uploadId, id), bounded(rowNumber, 0, 2, 1_000_000, id), id);
    }

    /** 실행에 실패한 행을 같은 입력으로 다시 검토한다. 조건을 바꾼 재검토는 협상 작업 API로 한다. */
    @PostMapping(value = BASE + "/{uploadId}/rows/{rowNumber}/retry", produces = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<JsonNode> retry(@PathVariable String uploadId, @PathVariable String rowNumber,
                                   @RequestBody(required = false) String raw, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId();
        UUID id = begin(request, Set.of());
        if (raw != null && !raw.isBlank() && !raw.strip().equals("{}")) throw ApiException.validation(id);
        return ResponseEntity.status(HttpStatus.ACCEPTED)
                .body(service.retry(owner, uuid(uploadId, id), bounded(rowNumber, 0, 2, 1_000_000, id), id));
    }

    private static UUID begin(HttpServletRequest request, Set<String> allowed) {
        UUID id = UUID.randomUUID();
        request.setAttribute("request_id", id);
        for (var entry : request.getParameterMap().entrySet()) {
            if (!allowed.contains(entry.getKey()) || entry.getValue().length != 1) throw ApiException.validation(id);
        }
        return id;
    }

    private static UUID uuid(String raw, UUID id) {
        try {
            UUID value = UUID.fromString(raw);
            if (!value.toString().equals(raw.toLowerCase(Locale.ROOT))) throw new IllegalArgumentException();
            return value;
        } catch (IllegalArgumentException e) {
            throw ApiException.validation(id);
        }
    }

    private static int bounded(String raw, int fallback, int min, int max, UUID id) {
        if (raw == null) return fallback;
        if (!raw.matches("[0-9]{1,7}")) throw ApiException.validation(id);
        int value = Integer.parseInt(raw);
        if (value < min || value > max) throw ApiException.validation(id);
        return value;
    }

    /** 경로를 떼어 낸 .xlsx 파일 이름. 화면 표시에만 쓴다. */
    private static String fileName(MultipartFile file) {
        if (file == null || file.getOriginalFilename() == null) return null;
        String name = file.getOriginalFilename().replace('\\', '/');
        name = name.substring(name.lastIndexOf('/') + 1).strip();
        if (name.isEmpty() || name.length() > 120 || !name.toLowerCase(Locale.ROOT).endsWith(".xlsx")) return null;
        return name;
    }
}
