package com.tiangong.mobilebody;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Typeface;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONObject;

public final class MainActivity extends Activity {
    private TextView status;
    private EditText server;
    private EditText code;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(buildUi());
        requestNotificationPermissionIfNeeded();
        refreshStatus();
    }

    @Override
    protected void onResume() {
        super.onResume();
        refreshStatus();
    }

    private View buildUi() {
        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(24), dp(34), dp(24), dp(36));
        scroll.addView(root);

        TextView title = text("天工造物 · 移动身体", 27, true);
        root.addView(title);
        TextView intro = text("无账号。使用一次性配对码连接家里的天工造物；手机只负责感知与执行，推理、记忆与任务权威仍在家里的天工。", 16, false);
        intro.setPadding(0, dp(10), 0, dp(20));
        root.addView(intro);

        status = text("状态检查中", 15, true);
        status.setPadding(dp(14), dp(12), dp(14), dp(12));
        root.addView(status, full());

        root.addView(section("1. 授予手机执行权限"));
        root.addView(button("开启无障碍操作权限", v -> startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))));
        root.addView(button("开启通知读取权限", v -> startActivity(new Intent("android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS"))));

        root.addView(section("2. 连接家里的天工"));
        server = new EditText(this);
        server.setHint("例如：http://192.168.1.10:7186");
        server.setSingleLine(true);
        server.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        SharedPreferences prefs = LinkConfig.prefs(this);
        server.setText(prefs.getString(LinkConfig.BASE_URL, ""));
        root.addView(server, full());

        code = new EditText(this);
        code.setHint("6 位一次性配对码");
        code.setSingleLine(true);
        code.setInputType(InputType.TYPE_CLASS_NUMBER);
        code.setPadding(code.getPaddingLeft(), dp(12), code.getPaddingRight(), code.getPaddingBottom());
        root.addView(code, full());

        root.addView(button("配对并启动移动身体", v -> pair()));
        root.addView(button("启动已配对连接", v -> startWorker()));
        root.addView(button("断开并清除本机配对", v -> disconnect()));

        root.addView(section("能力范围"));
        TextView abilities = text(
                "• 读取当前界面结构\n• 点击 / 滑动 / 返回 / Home\n• 向普通输入框写入文本\n• 打开已安装 App\n• 读取已授权通知\n• 截取当前屏幕\n\n密码框自动脱敏，权限控制器与安装器界面禁止远端点击/输入。",
                15,
                false
        );
        root.addView(abilities);

        TextView network = text(
                "网络建议：同一 Wi‑Fi 直接使用局域网地址；异地连接优先使用 Tailscale / WireGuard 这类私网，不要把天工 7184 核心端口暴露到公网。",
                14,
                false
        );
        network.setPadding(0, dp(22), 0, 0);
        root.addView(network);
        return scroll;
    }

    private void pair() {
        final String base = LinkConfig.normalizeBaseUrl(server.getText().toString());
        final String pairingCode = code.getText().toString().trim();
        if (base.isEmpty() || pairingCode.length() != 6) {
            setStatus("请输入正确的服务器地址和 6 位配对码");
            return;
        }
        setStatus("正在配对…");
        new Thread(() -> {
            try {
                JSONObject payload = new JSONObject();
                payload.put("code", pairingCode);
                payload.put("device_name", Build.MANUFACTURER + " " + Build.MODEL);
                payload.put("capabilities", BodyWorkerService.capabilities());
                JSONObject response = MobileHttp.request("POST", base + "/mobile/v1/pair", "", payload, 12000);
                String deviceId = response.optString("device_id", "");
                String token = response.optString("device_token", "");
                if (!response.optBoolean("ok", false) || deviceId.isEmpty() || token.isEmpty()) {
                    throw new IllegalStateException("pairing_response_invalid");
                }
                LinkConfig.prefs(this).edit()
                        .putString(LinkConfig.BASE_URL, base)
                        .putString(LinkConfig.DEVICE_ID, deviceId)
                        .putString(LinkConfig.DEVICE_TOKEN, token)
                        .apply();
                runOnUiThread(() -> {
                    code.setText("");
                    setStatus("配对成功 · " + deviceId.substring(0, Math.min(12, deviceId.length())));
                    startWorker();
                });
            } catch (Throwable error) {
                runOnUiThread(() -> setStatus("配对失败：" + safe(error.getMessage())));
            }
        }, "tiangong-pair").start();
    }

    private void startWorker() {
        if (!LinkConfig.paired(this)) {
            setStatus("尚未完成配对");
            return;
        }
        Intent service = new Intent(this, BodyWorkerService.class);
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(service);
        else startService(service);
        setStatus("移动身体已启动 · 将自动保持连接");
    }

    private void disconnect() {
        stopService(new Intent(this, BodyWorkerService.class));
        LinkConfig.clear(this);
        server.setText("");
        code.setText("");
        setStatus("已断开。本机设备令牌已删除");
    }

    private void refreshStatus() {
        if (status == null) return;
        boolean paired = LinkConfig.paired(this);
        boolean accessibility = BodyAccessibilityService.instance() != null;
        String value = (paired ? "已配对" : "未配对") + " · " + (accessibility ? "无障碍已启用" : "无障碍未启用");
        setStatus(value);
    }

    private void requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 7186);
        }
    }

    private TextView section(String value) {
        TextView view = text(value, 18, true);
        view.setPadding(0, dp(28), 0, dp(8));
        return view;
    }

    private TextView text(String value, int sp, boolean bold) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(sp);
        view.setTextColor(0xFF111827);
        if (bold) view.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        return view;
    }

    private Button button(String value, View.OnClickListener listener) {
        Button button = new Button(this);
        button.setText(value);
        button.setAllCaps(false);
        button.setGravity(Gravity.CENTER);
        button.setOnClickListener(listener);
        LinearLayout.LayoutParams params = full();
        params.topMargin = dp(8);
        button.setLayoutParams(params);
        return button;
    }

    private LinearLayout.LayoutParams full() {
        return new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void setStatus(String value) {
        status.setText(value);
    }

    private static String safe(String value) {
        return value == null ? "未知错误" : value;
    }
}
