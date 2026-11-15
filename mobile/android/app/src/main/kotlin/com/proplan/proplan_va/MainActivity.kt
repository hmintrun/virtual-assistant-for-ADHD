package com.proplan.proplan_va

import android.Manifest
import android.app.AlarmManager
import android.app.NotificationManager
import android.content.pm.PackageManager
import android.os.Build
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "proplan/device_state")
            .setMethodCallHandler { call, result ->
                if (call.method != "readPermissions") {
                    result.notImplemented()
                    return@setMethodCallHandler
                }
                val notifications = getSystemService(NotificationManager::class.java)
                val alarms = getSystemService(AlarmManager::class.java)
                fun permission(allowed: Boolean?) = when (allowed) {
                    true -> "granted"
                    false -> "denied"
                    null -> "unknown"
                }
                result.success(mapOf(
                    "notification_permission" to permission(notifications?.areNotificationsEnabled()),
                    "microphone_permission" to permission(
                        checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
                    ),
                    "dnd_permission" to permission(notifications?.isNotificationPolicyAccessGranted),
                    "alarm_capability" to when {
                        alarms == null -> "unknown"
                        Build.VERSION.SDK_INT < Build.VERSION_CODES.S || alarms.canScheduleExactAlarms() -> "exact_granted"
                        else -> "inexact_only"
                    }
                ))
            }
    }
}
