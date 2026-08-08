package com.tiangong.mobilebody;

import android.app.Notification;
import android.os.Bundle;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayDeque;
import java.util.Deque;

public final class TiangongNotificationService extends NotificationListenerService {
    private static final Object LOCK = new Object();
    private static final int MAX_ITEMS = 80;
    private static final Deque<JSONObject> ITEMS = new ArrayDeque<>();
    private static volatile boolean CONNECTED = false;

    @Override
    public void onListenerConnected() {
        CONNECTED = true;
        refreshActive();
    }

    @Override
    public void onListenerDisconnected() {
        CONNECTED = false;
    }

    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        JSONObject item = toJson(sbn);
        if (item == null) return;
        synchronized (LOCK) {
            ITEMS.addFirst(item);
            while (ITEMS.size() > MAX_ITEMS) ITEMS.removeLast();
        }
    }

    private void refreshActive() {
        StatusBarNotification[] active;
        try {
            active = getActiveNotifications();
        } catch (Throwable ignored) {
            return;
        }
        synchronized (LOCK) {
            ITEMS.clear();
            if (active == null) return;
            for (int i = active.length - 1; i >= 0 && ITEMS.size() < MAX_ITEMS; i--) {
                JSONObject item = toJson(active[i]);
                if (item != null) ITEMS.addFirst(item);
            }
        }
    }

    public static JSONObject snapshot() {
        JSONObject result = new JSONObject();
        JSONArray data = new JSONArray();
        synchronized (LOCK) {
            for (JSONObject item : ITEMS) data.put(item);
        }
        try {
            result.put("ok", true);
            result.put("data", new JSONObject()
                    .put("listener_connected", CONNECTED)
                    .put("notifications", data));
        } catch (Exception ignored) {
        }
        return result;
    }

    private static JSONObject toJson(StatusBarNotification sbn) {
        try {
            Notification notification = sbn.getNotification();
            Bundle extras = notification.extras;
            // Deliberately expose display text only. PendingIntent/action tokens are not exported.
            JSONObject item = new JSONObject();
            item.put("package", sbn.getPackageName());
            item.put("post_time_ms", sbn.getPostTime());
            item.put("ongoing", sbn.isOngoing());
            item.put("title", clip(string(extras.getCharSequence(Notification.EXTRA_TITLE)), 500));
            item.put("text", clip(string(extras.getCharSequence(Notification.EXTRA_TEXT)), 1000));
            item.put("subtext", clip(string(extras.getCharSequence(Notification.EXTRA_SUB_TEXT)), 500));
            return item;
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static String string(CharSequence value) {
        return value == null ? "" : value.toString();
    }

    private static String clip(String value, int max) {
        return value.length() <= max ? value : value.substring(0, max);
    }
}
