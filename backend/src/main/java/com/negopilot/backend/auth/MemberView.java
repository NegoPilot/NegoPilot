package com.negopilot.backend.auth;

import java.time.Instant;
import java.util.UUID;

record MemberView(UUID id, String email, String name, String department, String job_title,
                  String role, String status, Instant created_at, Instant updated_at) { }
