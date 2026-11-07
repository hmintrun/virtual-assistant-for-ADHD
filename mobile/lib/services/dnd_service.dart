// Service điều khiển Không làm phiền (flutter_dnd) và kiểm tra quyền DND.
import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:flutter_dnd/flutter_dnd.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Bật DND khi vào phiên Focus (UF3), trả lại ĐÚNG trạng thái cũ khi kết thúc.
///
/// Chỉ khôi phục phần chính app đã bật (owner = `runId`), không đè lựa chọn
/// của người dùng. Snapshot được lưu xuống máy để vẫn khôi phục được khi
/// Android kill app giữa phiên.
class DndService {
  DndService._();
  static final DndService instance = DndService._();

  static const String _snapshotKey = 'dnd_snapshot';

  /// PRIORITY: vẫn cho danh sách ưu tiên người dùng đã cài (người thân, gọi
  /// lặp lại) đi qua → an toàn khi khẩn cấp. Chuông hết phiên phát theo
  /// USAGE_ALARM nên vẫn kêu.
  static const int _focusFilter = FlutterDnd.INTERRUPTION_FILTER_PRIORITY;

  Future<bool> get hasPermission async {
    try {
      return await FlutterDnd.isNotificationPolicyAccessGranted ?? false;
    } on PlatformException {
      return false;
    }
  }

  void openPermissionSettings() => FlutterDnd.gotoPolicySettings();

  /// Bật DND cho phiên `runId`. Trả về true nếu CHÍNH APP vừa bật (app là owner).
  ///
  /// Trả về false (phiên vẫn chạy bình thường, chỉ không có DND) khi: thiếu
  /// quyền, máy không hỗ trợ, hoặc người dùng đã tự bật Priority từ trước.
  Future<bool> enable({required String runId}) async {
    try {
      if (!await hasPermission) return false;
      final previous = await FlutterDnd.getCurrentInterruptionFilter();
      // Người dùng tự bật sẵn → app không phải owner, không đụng vào.
      if (previous == _focusFilter) return false;

      // Android 15+: DND do app bật dùng chính sách riêng của app (mặc định chặn
      // cả cuộc gọi). Nhóm chốt: cho MỌI cuộc gọi + báo thức đi qua, chỉ chặn
      // thông báo/tin nhắn. Dưới Android 15 lệnh này không làm gì (giữ nguyên
      // chính sách DND của user).
      await FlutterDnd.setAppNotificationPolicy(
        priorityCategories: FlutterDnd.PRIORITY_CATEGORY_CALLS |
            FlutterDnd.PRIORITY_CATEGORY_REPEAT_CALLERS |
            FlutterDnd.PRIORITY_CATEGORY_ALARMS,
        priorityCallSenders: FlutterDnd.PRIORITY_SENDERS_ANY,
      );
      final ok = await FlutterDnd.setInterruptionFilter(_focusFilter) ?? false;
      if (ok) {
        final prefs = await SharedPreferences.getInstance();
        await prefs.setString(
          _snapshotKey,
          jsonEncode({'run_id': runId, 'previous': previous}),
        );
      }
      return ok;
    } on PlatformException {
      return false;
    }
  }

  /// Trả lại DND khi phiên `runId` kết thúc. Gọi nhiều lần vẫn an toàn
  /// (idempotent): lần sau không còn snapshot nên thoát ngay.
  Future<void> restore({required String runId}) async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_snapshotKey);
    if (raw == null) return; // app không bật gì → không đụng
    final snapshot = jsonDecode(raw) as Map<String, dynamic>;
    if (snapshot['run_id'] != runId) return; // không phải owner

    try {
      final current = await FlutterDnd.getCurrentInterruptionFilter();
      // Người dùng đã tự đổi DND giữa phiên → tôn trọng, không ghi đè.
      if (current == _focusFilter) {
        final previous = snapshot['previous'] as int?;
        final target = (previous == null ||
                previous == FlutterDnd.INTERRUPTION_FILTER_UNKNOWN)
            ? FlutterDnd.INTERRUPTION_FILTER_ALL
            : previous;
        await FlutterDnd.setInterruptionFilter(target);
      }
    } on PlatformException {
      // Mất quyền giữa phiên → không khôi phục được, vẫn xóa snapshot.
    }
    await prefs.remove(_snapshotKey);
  }

  /// Chuyển "quyền chủ" DND từ phiên `fromRunId` sang `toRunId` mà KHÔNG tắt
  /// rồi bật lại (UF5: làm tiếp phiên kế → giữ nguyên DND, không để thông báo
  /// dồn ào ra trong khoảng hở). Snapshot vẫn giữ trạng thái gốc trước phiên đầu.
  ///
  /// Trả về false nếu app không đang giữ DND cho `fromRunId` (khi đó gọi
  /// [enable] cho phiên mới như bình thường).
  Future<bool> handOver({
    required String fromRunId,
    required String toRunId,
  }) async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_snapshotKey);
    if (raw == null) return false;
    final snapshot = jsonDecode(raw) as Map<String, dynamic>;
    if (snapshot['run_id'] != fromRunId) return false;

    try {
      // User đã tự đổi DND giữa phiên → không còn là DND của app để chuyển.
      final current = await FlutterDnd.getCurrentInterruptionFilter();
      if (current != _focusFilter) {
        await prefs.remove(_snapshotKey);
        return false;
      }
    } on PlatformException {
      return false;
    }
    snapshot['run_id'] = toRunId;
    await prefs.setString(_snapshotKey, jsonEncode(snapshot));
    return true;
  }

  /// `runId` của phiên đang giữ DND (nếu có). Dùng khi mở lại app sau khi bị
  /// kill để biết còn DND nào cần trả lại.
  Future<String?> pendingOwnerRunId() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_snapshotKey);
    if (raw == null) return null;
    return (jsonDecode(raw) as Map<String, dynamic>)['run_id'] as String?;
  }
}
