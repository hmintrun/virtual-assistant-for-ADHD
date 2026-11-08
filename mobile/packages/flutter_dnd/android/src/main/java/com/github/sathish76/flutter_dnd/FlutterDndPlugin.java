package com.github.sathish76.flutter_dnd;

import androidx.annotation.NonNull;

import io.flutter.embedding.engine.plugins.FlutterPlugin;
import io.flutter.plugin.common.MethodCall;
import io.flutter.plugin.common.MethodChannel;
import io.flutter.plugin.common.MethodChannel.MethodCallHandler;
import io.flutter.plugin.common.MethodChannel.Result;
import io.flutter.embedding.engine.plugins.activity.ActivityPluginBinding;

import android.app.NotificationManager;
import android.content.Context;
import android.os.Bundle;
import android.util.Log;
import android.content.Intent;
import android.provider.Settings;

/**
 * FlutterDndPlugin
 */

public class FlutterDndPlugin implements FlutterPlugin, MethodCallHandler {

    private static NotificationManager notificationManager;
    private static Context context;

    @Override
    public void onAttachedToEngine(@NonNull FlutterPluginBinding flutterPluginBinding) {
        context = flutterPluginBinding.getApplicationContext();
        notificationManager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        final MethodChannel channel = new MethodChannel(flutterPluginBinding.getBinaryMessenger(),
                "flutter_dnd");
        channel.setMethodCallHandler(new FlutterDndPlugin());
    }

    @Override
    public void onMethodCall(@NonNull MethodCall call, @NonNull Result result) {
        if (!isAboveMarshmello()) {
            result.error("ERROR: INCOMPATIBLE_ANDROID_VERSION", "This methods required android version above 23", null);
            return;
        }
        switch (call.method) {
            case "isNotificationPolicyAccessGranted":
                result.success(isNotificationPolicyAccessGranted());
                break;
            case "gotoPolicySettings":
                gotoPolicySettings();
                result.success(null);
                break;
            case "setInterruptionFilter":
                int interruptionFilter = call.arguments();
                result.success(setInterruptionFilter(interruptionFilter));
                break;
            case "getCurrentInterruptionFilter":
                result.success(getCurrentInterruptionFilter());
                break;
            case "setAppNotificationPolicy":
                Integer categories = call.argument("priorityCategories");
                Integer callSenders = call.argument("priorityCallSenders");
                Integer messageSenders = call.argument("priorityMessageSenders");
                result.success(setAppNotificationPolicy(
                        categories == null ? 0 : categories,
                        callSenders == null ? NotificationManager.Policy.PRIORITY_SENDERS_ANY : callSenders,
                        messageSenders == null ? NotificationManager.Policy.PRIORITY_SENDERS_ANY : messageSenders));
                break;
            default:
                result.notImplemented();
                break;
        }
    }

    private boolean isAboveMarshmello() {
        return android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.M;
    }

    private boolean isNotificationPolicyAccessGranted() {
        return notificationManager.isNotificationPolicyAccessGranted();
    }

    private void gotoPolicySettings() {
        Intent intent = new Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        context.startActivity(intent);
    }

    private boolean setInterruptionFilter(int interruptionFilter) {
        if (notificationManager.isNotificationPolicyAccessGranted()) {
            notificationManager.setInterruptionFilter(interruptionFilter);
            return true;
        }
        return false;
    }

    /**
     * ProPlan VA patch: Android 15+ (API 35, targetSdk >= 35) turns DND set by an app into an
     * implicit rule that uses the APP's own policy (default blocks calls). This sets that app
     * policy. Below API 35 the same call would overwrite the user's GLOBAL DND policy, so it is
     * deliberately skipped there (returns false).
     */
    private boolean setAppNotificationPolicy(int categories, int callSenders, int messageSenders) {
        if (android.os.Build.VERSION.SDK_INT < 35) return false;
        if (!notificationManager.isNotificationPolicyAccessGranted()) return false;
        notificationManager.setNotificationPolicy(
                new NotificationManager.Policy(categories, callSenders, messageSenders));
        return true;
    }

    private int getCurrentInterruptionFilter() {
        return notificationManager.getCurrentInterruptionFilter();
    }

    @Override
    public void onDetachedFromEngine(@NonNull FlutterPluginBinding binding) {
    }

}
