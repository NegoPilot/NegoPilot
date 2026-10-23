package com.negopilot.backend.negotiation;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public final class LocalNegotiationExportStore implements NegotiationExportStore {
    private final Path root;

    @Autowired
    public LocalNegotiationExportStore(@Value("${negotiation.export-dir:./var/negotiation-exports}") String directory) {
        this(Path.of(directory));
    }

    public LocalNegotiationExportStore(Path directory) {
        root = directory.toAbsolutePath().normalize();
    }

    @Override
    public void write(String key, byte[] bytes) throws IOException {
        Path target = resolve(key);
        createDirectories(target.getParent());
        Path temporary = Files.createTempFile(target.getParent(), ".export-", ".tmp");
        try {
            Files.write(temporary, bytes);
            ensureNoLinks(target);
            Files.move(temporary, target, StandardCopyOption.ATOMIC_MOVE);
        } finally {
            Files.deleteIfExists(temporary);
        }
    }

    @Override
    public byte[] read(String key) throws IOException {
        Path target = resolve(key);
        ensureNoLinks(target);
        return Files.readAllBytes(target);
    }

    private Path resolve(String key) throws IOException {
        if (key == null || key.isBlank() || key.indexOf('\\') >= 0) throw new IOException("Invalid export key");
        Path relative;
        try { relative = Path.of(key); }
        catch (RuntimeException ex) { throw new IOException("Invalid export key", ex); }
        if (relative.isAbsolute()) throw new IOException("Invalid export key");
        for (Path part : relative) {
            if (part.toString().equals("..") || part.toString().equals(".")) throw new IOException("Invalid export key");
        }
        Path target = root.resolve(relative).normalize();
        if (!target.startsWith(root) || target.equals(root)) throw new IOException("Invalid export key");
        ensureNoLinks(target);
        return target;
    }

    private void createDirectories(Path directory) throws IOException {
        ensureNoLinks(directory);
        Files.createDirectories(directory);
        ensureNoLinks(directory);
    }

    private void ensureNoLinks(Path path) throws IOException {
        Path current = root;
        if (Files.isSymbolicLink(current)) throw new IOException("Symbolic links are not allowed in export paths");
        for (Path part : root.relativize(path)) {
            current = current.resolve(part);
            if (Files.isSymbolicLink(current)) throw new IOException("Symbolic links are not allowed in export paths");
            if (Files.exists(current, LinkOption.NOFOLLOW_LINKS) && !current.equals(path) && !Files.isDirectory(current)) {
                throw new IOException("Invalid export directory");
            }
        }
    }
}
