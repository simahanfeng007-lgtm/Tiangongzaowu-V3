package com.tiangong.mobilebody;

import android.content.Context;
import android.content.SharedPreferences;

final class LinkConfig {
    static final String PREFS = "tiangong_mobile_link";
    static final String BASE_URL = "base_url";
    static final String DEVICE_ID = "device_id";
    static final String DEVICE_TOKEN = "device_token";

    private LinkConfig() {}

    static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static String normalizeBaseUrl(String raw) {
        String value = raw == null ? "" : raw.trim();
        while (value.endsWith("/")) value = value.substring(0, value.length() - 1);
        if (!(value.startsWith("http://") || value.startsWith("https://"))) return "";
        return value;
    }

    static boolean paired(Context context) {
        SharedPreferences prefs = prefs(context);
        return !prefs.getString(BASE_URL, "").isEmpty()
                && !prefs.getString(DEVICE_ID, "").isEmpty()
                && !prefs.getString(DEVICE_TOKEN, "").isEmpty();
    }

    static void clear(Context context) {
        prefs(context).edit().clear().apply();
    }
}
