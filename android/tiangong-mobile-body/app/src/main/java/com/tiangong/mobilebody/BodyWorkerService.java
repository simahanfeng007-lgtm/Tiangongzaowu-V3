package com.tiangong.mobilebody;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.IBinder;
import android.os.SystemClock;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class BodyWorkerService extends Service {
    private static final String CHANNEL = "tiangong_mobile_body";
    private static final int NOTIFICATION_ID = 7186;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private volatile boolean stopped;

    @Override
    public void onCreate() {
        super.onCreate();
        createNotificationChannel();
        startForeground(NOTIFICATION_ID, notification("等待天工任务"));
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        stopped = false;
        executor.execute(this::runLoop);
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        stopped = true;
        executor.shutdownNow();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void runLoop() {
        long lastHeartbeat = 0L;
        while (!stopped) {
            SharedPreferences prefs = LinkConfig.prefs(this);
            String base = prefs.getString(LinkConfig.BASE_URL, "");
            String deviceId = prefs.getString(LinkConfig.DEVICE_ID, "");
            String token = prefs.getString(LinkConfig.DEVICE_TOKEN, "");
            if (base.isEmpty() || deviceId.isEmpty() || token.isEmpty()) {
                updateNotification("尚未配对");
                SystemClock.sleep(2500L);
                continue;
            }
            try {
                long now = SystemClock.elapsedRealtime();
                if (now - lastHeartbeat >= 15000L) {
                    JSONObject heartbeat = new JSONObject();
                    heartbeat.put("capabilities", capabilities());
                    MobileHttp.request("POST", base + "/mobile/v1/heartbeat", token, heartbeat, 10000);
                    lastHeartbeat = now;
                }
                JSONObject response = MobileHttp.request(
                        "GET",
                        base + "/mobile/v1/tasks/next?wait_ms=25000",
                        token,
                        null,
                        33000
                );
                JSONObject task = response.optJSONObject("task");
                if (task == null) {
                    updateNotification("已连接 · 等待任务");
                    continue;
                }
                String taskId = task.optString("task_id", "");
                String action = task.optString("action", "");
                JSONObject arguments = task.optJSONObject("arguments");
                if (arguments == null) arguments = new JSONObject();
                updateNotification("执行中 · " + action);
                JSONObject result = execute(action, arguments);
                MobileHttp.request(
                        "POST",
                        base + "/mobile/v1/tasks/" + taskId + "/result",
                        token,
                        result,
                        15000
                );
                updateNotification(result.optBoolean("ok", false) ? "执行完成 · 已回传" : "执行失败 · 已回传");
            } catch (Throwable error) {
                updateNotification("连接中断 · 自动重连");
                SystemClock.sleep(2200L);
            }
        }
    }

    private JSONObject execute(String action, JSONObject arguments) {
        if ("mobile.notification_list".equals(action)) {
            return TiangongNotificationService.snapshot();
        }
        BodyAccessibilityService body = BodyAccessibilityService.instance();
        if (body == null) {
            return failure("accessibility_service_not_enabled");
        }
        return body.execute(action, arguments);
    }

    static JSONArray capabilities() {
        JSONArray values = new JSONArray();
        values.put("mobile.observe_ui");
        values.put("mobile.tap");
        values.put("mobile.tap_node");
        values.put("mobile.swipe");
        values.put("mobile.input_text");
        values.put("mobile.back");
        values.put("mobile.home");
        values.put("mobile.open_app");
        values.put("mobile.notification_list");
        values.put("mobile.screenshot");
        return values;
    }

    static JSONObject failure(String message) {
        JSONObject result = new JSONObject();
        try {
            result.put("ok", false);
            result.put("error", message);
        } catch (Exception ignored) {
        }
        return result;
    }

    private void createNotificationChannel() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager == null) return;
        NotificationChannel channel = new NotificationChannel(
                CHANNEL,
                "天工移动身体连接",
                NotificationManager.IMPORTANCE_LOW
        );
        channel.setDescription("保持天工造物与本机移动身体的受控连接");
        manager.createNotificationChannel(channel);
    }

    private Notification notification(String text) {
        return new Notification.Builder(this, CHANNEL)
                .setContentTitle("天工造物·移动身体")
                .setContentText(text)
                .setSmallIcon(android.R.drawable.stat_notify_sync)
                .setOngoing(true)
                .build();
    }

    private void updateNotification(String text) {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) manager.notify(NOTIFICATION_ID, notification(text));
    }
}
