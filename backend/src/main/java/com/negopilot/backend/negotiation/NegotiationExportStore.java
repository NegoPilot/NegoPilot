package com.negopilot.backend.negotiation;

import java.io.IOException;

public interface NegotiationExportStore {
    void write(String key, byte[] bytes) throws IOException;
    byte[] read(String key) throws IOException;
}
