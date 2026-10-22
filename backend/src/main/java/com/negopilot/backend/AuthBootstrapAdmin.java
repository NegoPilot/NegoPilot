package com.negopilot.backend;

/** Compatibility entry point for existing deployment commands. */
public final class AuthBootstrapAdmin {
    private AuthBootstrapAdmin() { }

    public static void main(String[] args) {
        com.negopilot.backend.auth.AuthBootstrapAdmin.main(args);
    }
}
