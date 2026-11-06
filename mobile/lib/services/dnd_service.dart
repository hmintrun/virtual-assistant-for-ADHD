import 'package:flutter/services.dart';
import 'package:flutter_dnd/flutter_dnd.dart';

class DndSnapshot {
  const DndSnapshot({required this.previousFilter, required this.appApplied});

  final int? previousFilter;
  final bool appApplied;

  Map<String, dynamic> toJson() => {
        'previous_filter': previousFilter,
        'app_applied': appApplied,
      };

  factory DndSnapshot.fromJson(Map<String, dynamic> json) => DndSnapshot(
        previousFilter: json['previous_filter'] as int?,
        appApplied: json['app_applied'] as bool? ?? false,
      );
}

class DndService {
  Future<bool> get hasPermission async =>
      await FlutterDnd.isNotificationPolicyAccessGranted ?? false;

  void openPermissionSettings() => FlutterDnd.gotoPolicySettings();

  Future<DndSnapshot> enable() async {
    final previous = await FlutterDnd.getCurrentInterruptionFilter();
    final granted = await hasPermission;
    if (!granted) {
      return DndSnapshot(previousFilter: previous, appApplied: false);
    }

    // PRIORITY keeps alarms and user-selected priority interruptions available.
    final result = await FlutterDnd.setInterruptionFilter(
      FlutterDnd.INTERRUPTION_FILTER_PRIORITY,
    );
    return DndSnapshot(
      previousFilter: previous,
      appApplied: result == true,
    );
  }

  Future<void> restore(DndSnapshot? snapshot) async {
    if (snapshot == null || !snapshot.appApplied) return;
    final current = await FlutterDnd.getCurrentInterruptionFilter();
    // Do not overwrite a DND choice the user changed while this Run was active.
    if (current != FlutterDnd.INTERRUPTION_FILTER_PRIORITY) return;
    final previous = snapshot.previousFilter;
    if (previous != null &&
        previous != FlutterDnd.INTERRUPTION_FILTER_UNKNOWN) {
      await FlutterDnd.setInterruptionFilter(previous);
    } else {
      await FlutterDnd.setInterruptionFilter(
          FlutterDnd.INTERRUPTION_FILTER_ALL);
    }
  }

  bool isUnavailableError(Object error) =>
      error is MissingPluginException || error is PlatformException;
}
