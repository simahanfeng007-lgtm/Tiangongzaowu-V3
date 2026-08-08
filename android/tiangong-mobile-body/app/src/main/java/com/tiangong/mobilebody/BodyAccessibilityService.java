package com.tiangong.mobilebody;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.AccessibilityService.ScreenshotResult;
import android.accessibilityservice.GestureDescription;
import android.graphics.Bitmap;
import android.graphics.Path;
import android.graphics.Rect;
import android.hardware.HardwareBuffer;
import android.os.Bundle;
import android.util.Base64;
import android.view.Display;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

public final class BodyAccessibilityService extends AccessibilityService {
    private static volatile BodyAccessibilityService INSTANCE;
    private static final Set<String> SENSITIVE_SYSTEM_PACKAGES = new HashSet<>();

    static {
        SENSITIVE_SYSTEM_PACKAGES.add("com.android.permissioncontroller");
        SENSITIVE_SYSTEM_PACKAGES.add("com.google.android.permissioncontroller");
        SENSITIVE_SYSTEM_PACKAGES.add("com.android.packageinstaller");
        SENSITIVE_SYSTEM_PACKAGES.add("com.google.android.packageinstaller");
    }

    public static BodyAccessibilityService instance() {
        return INSTANCE;
    }

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        INSTANCE = this;
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        // State is read on demand. No event content is persisted here.
    }

    @Override
    public void onInterrupt() {
    }

    @Override
    public boolean onUnbind(android.content.Intent intent) {
        if (INSTANCE == this) INSTANCE = null;
        return super.onUnbind(intent);
    }

    public synchronized JSONObject execute(String action, JSONObject args) {
        try {
            switch (action) {
                case "mobile.observe_ui":
                    return observeUi();
                case "mobile.tap":
                    return tap(args.optInt("x", -1), args.optInt("y", -1));
                case "mobile.tap_node":
                    return tapNode(args);
                case "mobile.swipe":
                    return swipe(args);
                case "mobile.input_text":
                    return inputText(args.optString("text", ""));
                case "mobile.back":
                    return boolResult(performGlobalAction(GLOBAL_ACTION_BACK));
                case "mobile.home":
                    return boolResult(performGlobalAction(GLOBAL_ACTION_HOME));
                case "mobile.open_app":
                    return openApp(args.optString("package", ""));
                case "mobile.screenshot":
                    return screenshot();
                default:
                    return error("unsupported_accessibility_action");
            }
        } catch (Throwable error) {
            return error(error.getClass().getSimpleName() + ":" + safe(error.getMessage()));
        }
    }

    private JSONObject observeUi() throws Exception {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return error("active_window_unavailable");
        try {
            JSONObject data = new JSONObject();
            data.put("package", string(root.getPackageName()));
            data.put("class", string(root.getClassName()));
            JSONArray nodes = new JSONArray();
            Deque<NodePath> queue = new ArrayDeque<>();
            queue.add(new NodePath(root, "0"));
            int count = 0;
            while (!queue.isEmpty() && count < 700) {
                NodePath item = queue.removeFirst();
                AccessibilityNodeInfo node = item.node;
                JSONObject out = nodeJson(node, item.path);
                nodes.put(out);
                count++;
                int childCount = Math.min(node.getChildCount(), 80);
                for (int i = 0; i < childCount; i++) {
                    AccessibilityNodeInfo child = node.getChild(i);
                    if (child != null) queue.addLast(new NodePath(child, item.path + "/" + i));
                }
                if (node != root) node.recycle();
            }
            data.put("nodes", nodes);
            data.put("truncated", !queue.isEmpty());
            JSONObject result = ok();
            result.put("data", data);
            return result;
        } finally {
            root.recycle();
        }
    }

    private JSONObject nodeJson(AccessibilityNodeInfo node, String path) throws Exception {
        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);
        JSONObject out = new JSONObject();
        out.put("path", path);
        out.put("view_id", string(node.getViewIdResourceName()));
        out.put("class", string(node.getClassName()));
        out.put("package", string(node.getPackageName()));
        out.put("text", node.isPassword() ? "[PASSWORD]" : clip(string(node.getText()), 500));
        out.put("description", node.isPassword() ? "[PASSWORD]" : clip(string(node.getContentDescription()), 500));
        out.put("password", node.isPassword());
        out.put("clickable", node.isClickable());
        out.put("editable", node.isEditable());
        out.put("enabled", node.isEnabled());
        out.put("focused", node.isFocused());
        out.put("selected", node.isSelected());
        out.put("bounds", new JSONArray(new int[]{bounds.left, bounds.top, bounds.right, bounds.bottom}));
        out.put("children", node.getChildCount());
        return out;
    }

    private JSONObject tap(int x, int y) throws Exception {
        if (x < 0 || y < 0) return error("tap_coordinates_invalid");
        String pkg = activePackage();
        if (isSensitive(pkg)) return error("sensitive_system_surface_blocked");
        return boolResult(dispatchGestureAndWait(singlePointGesture(x, y, 1L, 80L)));
    }

    private JSONObject tapNode(JSONObject args) throws Exception {
        String pkg = activePackage();
        if (isSensitive(pkg)) return error("sensitive_system_surface_blocked");
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return error("active_window_unavailable");
        AccessibilityNodeInfo found = null;
        try {
            String viewId = args.optString("view_id", "");
            String text = args.optString("text", "");
            String description = args.optString("description", "");
            String path = args.optString("path", "");
            found = findNode(root, viewId, text, description, path);
            if (found == null) return error("node_not_found");
            if (found.isPassword()) return error("password_node_action_blocked");
            AccessibilityNodeInfo cursor = found;
            while (cursor != null) {
                if (cursor.isClickable() && cursor.isEnabled() && cursor.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                    return boolResult(true);
                }
                AccessibilityNodeInfo parent = cursor.getParent();
                if (cursor != found) cursor.recycle();
                cursor = parent;
            }
            Rect bounds = new Rect();
            found.getBoundsInScreen(bounds);
            return boolResult(dispatchGestureAndWait(singlePointGesture(bounds.centerX(), bounds.centerY(), 1L, 80L)));
        } finally {
            if (found != null && found != root) found.recycle();
            root.recycle();
        }
    }

    private AccessibilityNodeInfo findNode(AccessibilityNodeInfo root, String viewId, String text, String desc, String targetPath) {
        Deque<NodePath> queue = new ArrayDeque<>();
        queue.add(new NodePath(AccessibilityNodeInfo.obtain(root), "0"));
        AccessibilityNodeInfo result = null;
        while (!queue.isEmpty()) {
            NodePath item = queue.removeFirst();
            AccessibilityNodeInfo node = item.node;
            boolean matches = (!targetPath.isEmpty() && targetPath.equals(item.path))
                    || (!viewId.isEmpty() && viewId.equals(string(node.getViewIdResourceName())))
                    || (!text.isEmpty() && text.equals(string(node.getText())))
                    || (!desc.isEmpty() && desc.equals(string(node.getContentDescription())));
            if (matches) {
                result = AccessibilityNodeInfo.obtain(node);
                node.recycle();
                break;
            }
            for (int i = 0; i < node.getChildCount(); i++) {
                AccessibilityNodeInfo child = node.getChild(i);
                if (child != null) queue.addLast(new NodePath(child, item.path + "/" + i));
            }
            node.recycle();
        }
        while (!queue.isEmpty()) queue.removeFirst().node.recycle();
        return result;
    }

    private JSONObject inputText(String text) throws Exception {
        if (text.length() > 10000) return error("input_text_too_long");
        String pkg = activePackage();
        if (isSensitive(pkg)) return error("sensitive_system_surface_blocked");
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return error("active_window_unavailable");
        AccessibilityNodeInfo node = null;
        try {
            node = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
            if (node == null) node = findFirstEditable(root);
            if (node == null) return error("editable_node_not_found");
            if (node.isPassword()) return error("password_node_action_blocked");
            Bundle arguments = new Bundle();
            arguments.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
            return boolResult(node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments));
        } finally {
            if (node != null && node != root) node.recycle();
            root.recycle();
        }
    }

    private AccessibilityNodeInfo findFirstEditable(AccessibilityNodeInfo root) {
        Deque<AccessibilityNodeInfo> queue = new ArrayDeque<>();
        queue.add(AccessibilityNodeInfo.obtain(root));
        AccessibilityNodeInfo result = null;
        while (!queue.isEmpty()) {
            AccessibilityNodeInfo node = queue.removeFirst();
            if (node.isEditable() && node.isEnabled() && !node.isPassword()) {
                result = AccessibilityNodeInfo.obtain(node);
                node.recycle();
                break;
            }
            for (int i = 0; i < node.getChildCount(); i++) {
                AccessibilityNodeInfo child = node.getChild(i);
                if (child != null) queue.addLast(child);
            }
            node.recycle();
        }
        while (!queue.isEmpty()) queue.removeFirst().recycle();
        return result;
    }

    private JSONObject swipe(JSONObject args) throws Exception {
        String pkg = activePackage();
        if (isSensitive(pkg)) return error("sensitive_system_surface_blocked");
        int x1 = args.optInt("x1", -1), y1 = args.optInt("y1", -1);
        int x2 = args.optInt("x2", -1), y2 = args.optInt("y2", -1);
        long duration = Math.max(80L, Math.min(2000L, args.optLong("duration_ms", 350L)));
        if (x1 < 0 || y1 < 0 || x2 < 0 || y2 < 0) return error("swipe_coordinates_invalid");
        Path path = new Path();
        path.moveTo(x1, y1);
        path.lineTo(x2, y2);
        GestureDescription.Builder builder = new GestureDescription.Builder();
        builder.addStroke(new GestureDescription.StrokeDescription(path, 1L, duration));
        return boolResult(dispatchGestureAndWait(builder.build()));
    }

    private JSONObject openApp(String packageName) throws Exception {
        String pkg = packageName.trim();
        if (pkg.isEmpty() || pkg.length() > 200) return error("package_invalid");
        if (isSensitive(pkg)) return error("sensitive_system_package_blocked");
        android.content.Intent launch = getPackageManager().getLaunchIntentForPackage(pkg);
        if (launch == null) return error("package_not_launchable");
        launch.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(launch);
        return boolResult(true);
    }

    private JSONObject screenshot() throws Exception {
        final CountDownLatch latch = new CountDownLatch(1);
        final AtomicReference<String> image = new AtomicReference<>();
        final AtomicReference<String> failure = new AtomicReference<>();
        Executor executor = command -> new Thread(command, "tiangong-screenshot").start();
        takeScreenshot(Display.DEFAULT_DISPLAY, executor, new TakeScreenshotCallback() {
            @Override
            public void onSuccess(ScreenshotResult screenshot) {
                HardwareBuffer buffer = screenshot.getHardwareBuffer();
                try {
                    Bitmap wrapped = Bitmap.wrapHardwareBuffer(buffer, screenshot.getColorSpace());
                    if (wrapped == null) {
                        failure.set("screenshot_bitmap_unavailable");
                        return;
                    }
                    Bitmap bitmap = wrapped.copy(Bitmap.Config.ARGB_8888, false);
                    ByteArrayOutputStream out = new ByteArrayOutputStream();
                    bitmap.compress(Bitmap.CompressFormat.JPEG, 82, out);
                    bitmap.recycle();
                    if (out.size() > 1_800_000) {
                        failure.set("screenshot_too_large");
                    } else {
                        image.set(Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP));
                    }
                } catch (Throwable t) {
                    failure.set(t.getClass().getSimpleName() + ":" + safe(t.getMessage()));
                } finally {
                    buffer.close();
                    latch.countDown();
                }
            }

            @Override
            public void onFailure(int errorCode) {
                failure.set("screenshot_error_" + errorCode);
                latch.countDown();
            }
        });
        if (!latch.await(8, TimeUnit.SECONDS)) return error("screenshot_timeout");
        if (image.get() == null) return error(failure.get() == null ? "screenshot_failed" : failure.get());
        JSONObject data = new JSONObject();
        data.put("mime_type", "image/jpeg");
        data.put("base64", image.get());
        JSONObject result = ok();
        result.put("data", data);
        return result;
    }

    private GestureDescription singlePointGesture(int x, int y, long start, long duration) {
        Path path = new Path();
        path.moveTo(x, y);
        GestureDescription.Builder builder = new GestureDescription.Builder();
        builder.addStroke(new GestureDescription.StrokeDescription(path, start, duration));
        return builder.build();
    }

    private boolean dispatchGestureAndWait(GestureDescription gesture) throws InterruptedException {
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<Boolean> succeeded = new AtomicReference<>(false);
        boolean accepted = dispatchGesture(gesture, new GestureResultCallback() {
            @Override
            public void onCompleted(GestureDescription gestureDescription) {
                succeeded.set(true);
                latch.countDown();
            }

            @Override
            public void onCancelled(GestureDescription gestureDescription) {
                latch.countDown();
            }
        }, null);
        if (!accepted) return false;
        latch.await(4, TimeUnit.SECONDS);
        return Boolean.TRUE.equals(succeeded.get());
    }

    private String activePackage() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return "";
        try {
            return string(root.getPackageName());
        } finally {
            root.recycle();
        }
    }

    private boolean isSensitive(String pkg) {
        return SENSITIVE_SYSTEM_PACKAGES.contains(pkg.toLowerCase(Locale.ROOT));
    }

    private JSONObject ok() throws Exception {
        JSONObject result = new JSONObject();
        result.put("ok", true);
        return result;
    }

    private JSONObject boolResult(boolean value) throws Exception {
        JSONObject result = new JSONObject();
        result.put("ok", value);
        result.put("data", new JSONObject().put("performed", value));
        if (!value) result.put("error", "action_not_performed");
        return result;
    }

    private JSONObject error(String message) {
        JSONObject result = new JSONObject();
        try {
            result.put("ok", false);
            result.put("error", clip(safe(message), 300));
        } catch (Exception ignored) {
        }
        return result;
    }

    private static String string(CharSequence value) {
        return value == null ? "" : value.toString();
    }

    private static String safe(String value) {
        return value == null ? "" : value;
    }

    private static String clip(String value, int max) {
        if (value == null) return "";
        return value.length() <= max ? value : value.substring(0, max);
    }

    private static final class NodePath {
        final AccessibilityNodeInfo node;
        final String path;

        NodePath(AccessibilityNodeInfo node, String path) {
            this.node = node;
            this.path = path;
        }
    }
}
