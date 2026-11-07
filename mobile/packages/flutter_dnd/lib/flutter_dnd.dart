import 'dart:async';

import 'package:flutter/services.dart';

class FlutterDnd {
  /// Unknown filter
  static const int INTERRUPTION_FILTER_UNKNOWN = 0;

  /// No notifications are suppressed.
  static const int INTERRUPTION_FILTER_ALL = 1;

  /// Allow priority notifications.
  static const int INTERRUPTION_FILTER_PRIORITY = 2;

  /// Suppress all notifications. (Turn off DND)
  static const int INTERRUPTION_FILTER_NONE = 3;

  /// Allow alarm notifications.
  static const int INTERRUPTION_FILTER_ALARMS = 4;

  static const MethodChannel _channel = const MethodChannel('flutter_dnd');

  /// Check the application has access to change the DND settings
  static Future<bool?> get isNotificationPolicyAccessGranted async {
    return await _channel.invokeMethod('isNotificationPolicyAccessGranted');
  }

  /// Takes to DND system settings.
  /// Where the application gains access to change the DND settings.
  static void gotoPolicySettings() {
    _channel.invokeMethod('gotoPolicySettings');
  }

  /// Set new interruption [filter]
  static Future<bool?> setInterruptionFilter(int filter) async {
    return await _channel.invokeMethod('setInterruptionFilter', filter);
  }

  // ProPlan VA patch — NotificationManager.Policy constants.
  static const int PRIORITY_CATEGORY_CALLS = 8;
  static const int PRIORITY_CATEGORY_REPEAT_CALLERS = 16;
  static const int PRIORITY_CATEGORY_ALARMS = 32;
  static const int PRIORITY_SENDERS_ANY = 0;

  /// ProPlan VA patch. Android 15+ only: sets the policy of the DND rule this
  /// app creates (which calls/alarms may pass). Returns false (no-op) below
  /// Android 15 so the user's global DND policy is never overwritten.
  static Future<bool?> setAppNotificationPolicy({
    required int priorityCategories,
    int priorityCallSenders = PRIORITY_SENDERS_ANY,
    int priorityMessageSenders = PRIORITY_SENDERS_ANY,
  }) async {
    return await _channel.invokeMethod('setAppNotificationPolicy', {
      'priorityCategories': priorityCategories,
      'priorityCallSenders': priorityCallSenders,
      'priorityMessageSenders': priorityMessageSenders,
    });
  }

  /// Returns currently applied notification [filter]
  static Future<int?> getCurrentInterruptionFilter() async {
    return await _channel.invokeMethod('getCurrentInterruptionFilter');
  }

  /// Returns Filter name from the [filter]
  static String getFilterName(int filter) {
    switch (filter) {
      case 1:
        return 'INTERRUPTION_FILTER_ALL';
      case 2:
        return 'INTERRUPTION_FILTER_PRIORITY';
      case 3:
        return 'INTERRUPTION_FILTER_NONE';
      case 4:
        return 'INTERRUPTION_FILTER_ALARMS';
      default:
        return 'INTERRUPTION_FILTER_UNKNOWN';
    }
  }
}
