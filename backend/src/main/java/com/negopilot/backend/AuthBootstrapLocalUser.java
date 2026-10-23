package com.negopilot.backend;

/** Compatibility entry point for existing deployment commands. */
public final class AuthBootstrapLocalUser {
    private AuthBootstrapLocalUser() { }

    public static void main(String[] args) {
        com.negopilot.backend.auth.AuthBootstrapLocalUser.main(args);
    }
}
