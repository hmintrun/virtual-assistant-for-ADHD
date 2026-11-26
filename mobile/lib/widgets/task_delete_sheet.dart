import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/api_client.dart';
import '../core/theme.dart';
import '../providers/task_provider.dart';
import '../services/planning_api.dart';
import '../services/pre_session_reminder_sync.dart';
import 'melbel_ui.dart';

/// W3C COGA-compliant bottom sheet confirming deletion of a confirmed task
/// via `DELETE /api/v1/tasks/{task_id}`. Never uses `AlertDialog`.
Future<bool> confirmAndDeleteTask(
  BuildContext context, {
  required String taskId,
  required String taskTitle,
  int? taskRevision,
  ApiClient? api,
}) async {
  final deleted = await showModalBottomSheet<bool>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (_) => _TaskDeleteSheet(
      taskId: taskId,
      taskTitle: taskTitle,
      taskRevision: taskRevision,
      api: api,
    ),
  );
  return deleted == true;
}

class _TaskDeleteSheet extends StatefulWidget {
  const _TaskDeleteSheet({
    required this.taskId,
    required this.taskTitle,
    this.taskRevision,
    this.api,
  });

  final String taskId;
  final String taskTitle;
  final int? taskRevision;
  final ApiClient? api;

  @override
  State<_TaskDeleteSheet> createState() => _TaskDeleteSheetState();
}

class _TaskDeleteSheetState extends State<_TaskDeleteSheet> {
  bool _busy = false;
  String? _error;

  Future<void> _deleteTask() async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    final client = widget.api ?? ApiClient();
    try {
      await PlanningApi(client: client).deleteTask(
        widget.taskId,
        expectedTaskRevision: widget.taskRevision,
      );
      if (!mounted) return;
      try {
        Provider.of<TaskProvider>(context, listen: false)
            .clearConfirmedTaskIfMatches(widget.taskId);
      } on ProviderNotFoundException {
        // Optional when invoked from a test tree without TaskProvider.
      }
      unawaited(PreSessionReminderSync.instance.sync(api: client));
      final messenger = ScaffoldMessenger.maybeOf(context);
      Navigator.of(context).pop(true);
      messenger?.showSnackBar(
        SnackBar(content: Text('Đã xóa công việc "${widget.taskTitle}".')),
      );
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = error.message;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = 'Chưa xóa được công việc này. Vui lòng thử lại.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final palette = context.melbel;
    return SafeArea(
      child: Padding(
        padding: EdgeInsets.fromLTRB(
          20,
          4,
          20,
          24 + MediaQuery.viewInsetsOf(context).bottom,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Xóa công việc này?',
              style: theme.textTheme.titleLarge?.copyWith(
                fontWeight: FontWeight.w800,
              ),
            ),
            const SizedBox(height: 12),
            MelbelCard(
              tone: MelbelCardTone.amber,
              borderRadius: 20,
              padding: const EdgeInsets.all(14),
              child: Text(
                widget.taskTitle.trim().isEmpty
                    ? 'Công việc đã chốt'
                    : widget.taskTitle,
                style: theme.textTheme.titleSmall?.copyWith(
                  color: palette.onAmberSoft,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ),
            const SizedBox(height: 12),
            Text(
              'Các phiên chưa thực hiện và lời nhắc sắp tới của công việc này sẽ bị hủy. Lịch Google Calendar liên kết (nếu có) cũng sẽ tự động cập nhật.',
              style: theme.textTheme.bodyMedium?.copyWith(
                color: palette.onSurfaceVariant,
                height: 1.45,
              ),
            ),
            if (_error != null) ...[
              const SizedBox(height: 12),
              MelbelCard(
                tone: MelbelCardTone.amber,
                borderRadius: 16,
                padding: const EdgeInsets.all(12),
                child: Text(
                  _error!,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: palette.onAmberSoft,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ],
            const SizedBox(height: 20),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                style: FilledButton.styleFrom(
                  backgroundColor: theme.colorScheme.error,
                  foregroundColor: theme.colorScheme.onError,
                  minimumSize: const Size.fromHeight(52),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(18),
                  ),
                ),
                onPressed: _busy ? null : _deleteTask,
                icon: _busy
                    ? SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: theme.colorScheme.onError,
                        ),
                      )
                    : const Icon(Icons.delete_outline_rounded),
                label: Text(_busy ? 'Đang xóa công việc…' : 'Xóa công việc'),
              ),
            ),
            const SizedBox(height: 10),
            SecondaryCta(
              label: 'Giữ lại',
              height: 50,
              onPressed: _busy ? null : () => Navigator.of(context).pop(false),
            ),
          ],
        ),
      ),
    );
  }
}
