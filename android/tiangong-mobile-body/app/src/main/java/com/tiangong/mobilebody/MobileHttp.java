package com.tiangong.mobilebody;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

final class MobileHttp {
    private MobileHttp() {}

    static JSONObject request(String method, String url, String bearer, JSONObject body, int readTimeoutMs) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(url).openConnection();
        connection.setConnectTimeout(8000);
        connection.setReadTimeout(readTimeoutMs);
        connection.setRequestMethod(method);
        connection.setRequestProperty("Accept", "application/json");
        connection.setRequestProperty("User-Agent", "TiangongMobileBody/0.1");
        if (bearer != null && !bearer.isEmpty()) {
            connection.setRequestProperty("Authorization", "Bearer " + bearer);
        }
        if (body != null) {
            byte[] payload = body.toString().getBytes(StandardCharsets.UTF_8);
            connection.setDoOutput(true);
            connection.setFixedLengthStreamingMode(payload.length);
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            try (OutputStream out = connection.getOutputStream()) {
                out.write(payload);
            }
        }
        int code = connection.getResponseCode();
        InputStream source = code >= 400 ? connection.getErrorStream() : connection.getInputStream();
        byte[] data = readAll(source, 3 * 1024 * 1024);
        connection.disconnect();
        JSONObject response = data.length == 0 ? new JSONObject() : new JSONObject(new String(data, StandardCharsets.UTF_8));
        if (code >= 400) {
            throw new IllegalStateException("http_" + code + ":" + response.optString("error", response.optString("reason_code", "request_failed")));
        }
        return response;
    }

    private static byte[] readAll(InputStream input, int limit) throws Exception {
        if (input == null) return new byte[0];
        try (InputStream in = input; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int total = 0;
            while (true) {
                int read = in.read(buffer);
                if (read < 0) break;
                total += read;
                if (total > limit) throw new IllegalStateException("response_too_large");
                out.write(buffer, 0, read);
            }
            return out.toByteArray();
        }
    }
}
