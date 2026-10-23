package com.negopilot.backend.negotiation;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class LocalNegotiationExportStoreTest {
    @TempDir Path directory;

    @Test
    void writesAndReadsCompleteBytesWithoutTemporaryFiles() throws Exception {
        var store = new LocalNegotiationExportStore(directory);
        byte[] bytes = "제안서 원문".getBytes(StandardCharsets.UTF_8);
        String key = "exports/2026/10/file.pdf";
        store.write(key, bytes);
        assertThat(store.read(key)).isEqualTo(bytes);
        try (var files = Files.list(directory.resolve("exports/2026/10"))) {
            assertThat(files.map(path -> path.getFileName().toString()).toList()).containsExactly("file.pdf");
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {"../outside.pdf", "exports/../../outside.pdf", "/tmp/outside.pdf", "exports/./file.pdf", "..\\outside.pdf", ""})
    void refusesTraversalOnReadAndWrite(String key) {
        var store = new LocalNegotiationExportStore(directory);
        assertThatThrownBy(() -> store.write(key, new byte[]{1})).isInstanceOf(IOException.class);
        assertThatThrownBy(() -> store.read(key)).isInstanceOf(IOException.class);
    }

    @Test
    void refusesSymlinkDirectoriesAndSymlinkFiles() throws Exception {
        Path outside = Files.createDirectory(directory.resolve("outside"));
        Path root = Files.createDirectory(directory.resolve("store"));
        var store = new LocalNegotiationExportStore(root);
        Files.createSymbolicLink(root.resolve("exports"), outside);
        assertThatThrownBy(() -> store.write("exports/file.pdf", new byte[]{1})).isInstanceOf(IOException.class);
        Files.write(outside.resolve("original.pdf"), new byte[]{1});
        Files.createSymbolicLink(root.resolve("linked.pdf"), outside.resolve("original.pdf"));
        assertThatThrownBy(() -> store.read("linked.pdf")).isInstanceOf(IOException.class);
        assertThatThrownBy(() -> store.write("linked.pdf", new byte[]{2})).isInstanceOf(IOException.class);
        assertThat(Files.readAllBytes(outside.resolve("original.pdf"))).isEqualTo(new byte[]{1});
    }
}
