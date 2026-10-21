package com.negopilot.backend;

import java.io.Serializable;
import java.security.Principal;
import java.util.UUID;

/** The session principal contains no credentials or mutable member profile. */
public record UserIdentity(UUID userId, String role, String status) implements Serializable, Principal {
    @Override public String getName() { return userId.toString(); }
}
