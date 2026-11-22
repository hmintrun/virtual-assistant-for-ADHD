import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core/theme.dart';
import '../models/focus_session_model.dart';
import '../models/task_model.dart';
import '../providers/focus_provider.dart';
import 'countdown_timer_widget.dart';
import 'melbel_ui.dart';

class FocusSessionSummaryCard extends StatelessWidget {
  const FocusSessionSummaryCard({
    required this.session,
    this.compact = false,
    super.key,
  });

  final FocusSessionInfo session;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final text = Theme.of(context).textTheme;

    return MelbelCard(
      borderRadius: 26,
      padding: EdgeInsets.all(compact ? 16 : 20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Semantics(
            header: true,
            child: Text(
              session.stepTitle,
              style: (compact ? text.titleMedium : text.titleLarge)?.copyWith(
                color: palette.onSurface,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
          if (session.taskTitle != session.stepTitle) ...[
            const SizedBox(height: 4),
            Text(session.taskTitle,
                style: text.bodyMedium?.copyWith(
                  color: palette.onSurfaceVariant,
                )),
          ],
          const SizedBox(height: 10),
          Wrap(
            spacing: 12,
            runSpacing: 4,
            children: [
              Text(
                '${formatFocusClock(session.scheduledStart)} · ${focusMinutes(session.workSeconds)} phút',
                style:
                    text.bodySmall?.copyWith(color: palette.onSurfaceVariant),
              ),
              if (session.deadline case final deadline?)
                Text(
                  'Hạn ${formatFocusDateTime(deadline)}',
                  style:
                      text.bodySmall?.copyWith(color: palette.onSurfaceVariant),
                ),
            ],
          ),
          if (compact &&
              (session.expectedOutput?.trim().isNotEmpty ?? false)) ...[
            const SizedBox(height: 8),
            Text(
              'Đầu ra: ${session.expectedOutput}',
              style: text.bodySmall?.copyWith(color: palette.onSurfaceVariant),
            ),
          ],
          if (!compact && session.microTaskText.trim().isNotEmpty) ...[
            const SizedBox(height: 12),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
              decoration: BoxDecoration(
                color: palette.sageSoft,
                borderRadius: BorderRadius.circular(16),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Icon(Icons.spa_outlined, size: 17, color: palette.sage),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'Khởi động 2 phút · ${session.microTaskText}',
                      style: text.bodySmall?.copyWith(
                        color: palette.onSageSoft,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class FocusTimerProgressCard extends StatelessWidget {
  const FocusTimerProgressCard({
    required this.segment,
    required this.index,
    required this.count,
    required this.remaining,
    required this.progress,
    this.isOffline = false,
    super.key,
  });

  final SessionSegmentModel? segment;
  final int index;
  final int count;
  final int remaining;
  final double progress;
  final bool isOffline;

  @override
  Widget build(BuildContext context) {
    final isRest = segment?.kind == 'rest';
    final palette = context.melbel;
    final theme = Theme.of(context);
    final subtitle = isRest ? 'Đang nghỉ' : 'Đang tập trung';
    final endAnchor = remaining > 0
        ? formatFocusClock(DateTime.now().add(Duration(seconds: remaining)))
        : null;

    return MelbelCard(
      tone: MelbelCardTone.muted,
      borderRadius: 28,
      elevated: false,
      padding: const EdgeInsets.fromLTRB(16, 18, 16, 22),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (count > 1) ...[
            Text(
              'Chặng ${index + 1}/$count',
              textAlign: TextAlign.center,
              style: theme.textTheme.labelLarge
                  ?.copyWith(color: palette.onSurfaceVariant),
            ),
            const SizedBox(height: 8),
          ],
          MelbelFocusTimerRing(
            timeText: formatFocusTimer(remaining),
            progress: 1 - progress.clamp(0.0, 1.0).toDouble(),
            subtitle: subtitle,
            isBreak: isRest,
            isOffline: isOffline,
            size: 204,
          ),
          if (endAnchor != null) ...[
            const SizedBox(height: 10),
            Center(
              child: MelbelStatusPill(
                label: isRest
                    ? 'Quay lại lúc $endAnchor'
                    : 'Hết giờ lúc $endAnchor',
                icon: Icons.schedule_rounded,
                tone: MelbelCardTone.neutral,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// UF4 Push-to-Talk raw audio button with calm haptic & scale feedback.
class FocusHoldToRecord extends StatefulWidget {
  const FocusHoldToRecord({
    required this.isRecording,
    required this.onStart,
    required this.onStop,
    required this.onCancel,
    this.label = 'Cất suy nghĩ',
    super.key,
  });

  final bool isRecording;
  final Future<void> Function() onStart;
  final Future<void> Function() onStop;
  final Future<void> Function() onCancel;
  final String label;

  @override
  State<FocusHoldToRecord> createState() => _FocusHoldToRecordState();
}

class _FocusHoldToRecordState extends State<FocusHoldToRecord> {
  bool _showFocus = false;
  bool _activating = false;
  Future<void>? _holdStart;

  void _startHold(LongPressStartDetails _) {
    if (_holdStart != null || _activating) return;
    unawaited(HapticFeedback.mediumImpact());
    _holdStart = widget.onStart();
  }

  Future<void> _finishHold({bool cancel = false}) async {
    final start = _holdStart;
    if (start == null) return;
    final finish = cancel ? widget.onCancel : widget.onStop;
    try {
      await start;
      if (!cancel) unawaited(HapticFeedback.lightImpact());
      await finish();
    } finally {
      _holdStart = null;
    }
  }

  Future<void> _activate() async {
    if (_activating || _holdStart != null) return;
    _activating = true;
    try {
      if (widget.isRecording) {
        await widget.onStop();
      } else {
        await widget.onStart();
      }
    } finally {
      _activating = false;
    }
  }

  @override
  Widget build(BuildContext context) {
    final isRecording = widget.isRecording;
    final label = widget.label;
    final palette = context.melbel;
    final text = Theme.of(context).textTheme;
    final reducedMotion = MediaQuery.disableAnimationsOf(context);

    return Center(
      child: FocusableActionDetector(
        onShowFocusHighlight: (focused) => setState(() => _showFocus = focused),
        shortcuts: const {
          SingleActivator(LogicalKeyboardKey.enter): ActivateIntent(),
          SingleActivator(LogicalKeyboardKey.space): ActivateIntent(),
        },
        actions: {
          ActivateIntent: CallbackAction<ActivateIntent>(onInvoke: (_) {
            unawaited(_activate());
            return null;
          }),
        },
        child: Semantics(
          label: isRecording ? 'Lưu ghi âm' : label,
          hint: isRecording
              ? 'Kết thúc ghi âm và lưu vào hộp nháp'
              : 'Kích hoạt để bắt đầu ghi âm',
          button: true,
          excludeSemantics: true,
          onTap: _activate,
          child: GestureDetector(
            onLongPressStart: _startHold,
            onLongPressEnd: (_) => _finishHold(),
            onLongPressCancel: () => _finishHold(cancel: true),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                RepaintBoundary(
                  child: AnimatedScale(
                    scale: isRecording && !reducedMotion ? 1.05 : 1.0,
                    duration: reducedMotion
                        ? Duration.zero
                        : const Duration(milliseconds: 140),
                    curve: Curves.easeOutCubic,
                    child: AnimatedContainer(
                      duration: reducedMotion
                          ? Duration.zero
                          : const Duration(milliseconds: 160),
                      width: 92,
                      height: 92,
                      decoration: BoxDecoration(
                        color: isRecording ? palette.amber : palette.sageSoft,
                        shape: BoxShape.circle,
                        border: Border.all(
                          color: _showFocus
                              ? palette.onSurface
                              : isRecording
                                  ? palette.amber
                                  : palette.sage.withValues(alpha: 0.45),
                          width: _showFocus ? 3 : 2,
                        ),
                      ),
                      child: Icon(
                        isRecording
                            ? Icons.graphic_eq_rounded
                            : Icons.mic_none_rounded,
                        size: 34,
                        color: isRecording ? palette.surface : palette.sage,
                      ),
                    ),
                  ),
                ),
                const SizedBox(height: 10),
                Text(
                  isRecording ? 'Đang cất suy nghĩ' : label,
                  style: text.titleMedium?.copyWith(color: palette.onSurface),
                ),
                const SizedBox(height: 2),
                Text(
                  _showFocus
                      ? (isRecording
                          ? 'Nhấn Enter để lưu'
                          : 'Nhấn Enter để nói')
                      : (isRecording ? 'Thả tay để lưu' : 'Nhấn giữ để nói'),
                  style: text.bodySmall?.copyWith(
                    color: palette.onSurfaceVariant,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class FocusThoughtTile extends StatelessWidget {
  const FocusThoughtTile({
    required this.thought,
    required this.isPlaying,
    required this.onPlay,
    required this.onDelete,
    super.key,
  });

  final ThoughtRecord thought;
  final bool isPlaying;
  final VoidCallback? onPlay;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    return MelbelCard(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      child: Row(
        children: [
          IconButton(
            tooltip: isPlaying ? 'Dừng phát' : 'Phát ghi âm',
            onPressed: onPlay,
            color: palette.sage,
            icon: Icon(
              isPlaying
                  ? Icons.stop_circle_outlined
                  : Icons.play_circle_outline_rounded,
            ),
          ),
          Expanded(
            child: Text(
              thought.text ?? 'Ghi âm · ${formatFocusClock(thought.createdAt)}',
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          IconButton(
            tooltip: 'Xóa ghi chú',
            onPressed: onDelete,
            color: palette.onSurfaceVariant,
            icon: const Icon(Icons.delete_outline_rounded),
          ),
        ],
      ),
    );
  }
}

class FocusInfoCard extends StatelessWidget {
  const FocusInfoCard({
    required this.icon,
    required this.title,
    required this.subtitle,
    this.tone = MelbelCardTone.neutral,
    super.key,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final MelbelCardTone tone;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final text = Theme.of(context).textTheme;
    final (accent, titleColor, subtitleColor) = switch (tone) {
      MelbelCardTone.amber => (
          palette.amber,
          palette.onAmberSoft,
          palette.onAmberSoft.withValues(alpha: 0.85),
        ),
      MelbelCardTone.sage => (
          palette.sage,
          palette.onSageSoft,
          palette.onSageSoft.withValues(alpha: 0.85),
        ),
      MelbelCardTone.softError => (
          palette.softError,
          palette.onSoftErrorSoft,
          palette.onSoftErrorSoft.withValues(alpha: 0.85),
        ),
      _ => (palette.sage, palette.onSurface, palette.onSurfaceVariant),
    };

    return MelbelCard(
      tone: tone,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, color: accent, size: 22),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: text.titleSmall?.copyWith(color: titleColor),
                ),
                const SizedBox(height: 4),
                Text(
                  subtitle,
                  style: text.bodySmall?.copyWith(color: subtitleColor),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class FocusOfflineBanner extends StatelessWidget {
  const FocusOfflineBanner({super.key});

  @override
  Widget build(BuildContext context) => const FocusInfoCard(
        icon: Icons.cloud_off_outlined,
        title: 'Kết nối tạm gián đoạn',
        subtitle: 'Phiên đang chạy trên thiết bị. Ghi âm vẫn được lưu cục bộ.',
        tone: MelbelCardTone.amber,
      );
}

String formatFocusTimer(int seconds) {
  final mins = (seconds ~/ 60).toString().padLeft(2, '0');
  final secs = (seconds % 60).toString().padLeft(2, '0');
  return '$mins:$secs';
}

String formatFocusClock(DateTime date) {
  final local = date.toLocal();
  return '${local.hour.toString().padLeft(2, '0')}:${local.minute.toString().padLeft(2, '0')}';
}

String formatFocusDateTime(DateTime date) {
  final local = date.toLocal();
  return '${formatFocusClock(local)} ${local.day}/${local.month}';
}

String formatFocusWeekday(DateTime date) {
  final local = date.toLocal();
  const days = [
    'Thứ 2',
    'Thứ 3',
    'Thứ 4',
    'Thứ 5',
    'Thứ 6',
    'Thứ 7',
    'Chủ nhật',
  ];
  return '${days[local.weekday - 1]}, ngày ${local.day} tháng ${local.month}';
}

int focusMinutes(int seconds) => (seconds / 60).ceil();
