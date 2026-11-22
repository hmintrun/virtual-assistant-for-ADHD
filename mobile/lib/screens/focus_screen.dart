import 'dart:async';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/responsive_layout.dart';
import '../core/theme.dart';
import '../models/focus_session_model.dart';
import '../providers/focus_provider.dart';
import '../providers/task_provider.dart';
import '../services/checkin_voice_service.dart';
import '../widgets/focus_stage_widgets.dart';
import '../widgets/melbel_ui.dart';
import '../widgets/undo_snackbar.dart';
import 'focus_review_screens.dart';
import 'task_entry_screen.dart';
import 'task_summary_screen.dart';

/// UF3–UF4 flow for an already scheduled Session. The session comes from the
/// confirmed plan; this screen does not create or complete Tasks/Steps.
class FocusScreen extends StatefulWidget {
  const FocusScreen({super.key});

  @override
  State<FocusScreen> createState() => _FocusScreenState();
}

class _FocusScreenState extends State<FocusScreen> with WidgetsBindingObserver {
  final TextEditingController _thoughtText = TextEditingController();
  final _recordButtonKey = GlobalKey();
  String _breakWhisper = 'Chạm nhẹ Melbel để nghỉ ngơi cùng nhau nhé.';
  final CheckInVoiceService _checkInVoice = CheckInVoiceService();
  String? _spokenCheckInRunId;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        context.read<FocusProvider>().reportAppVisibility(foreground: true);
      }
    });
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      final focus = context.read<FocusProvider>();
      focus.refreshDndPermission();
      focus.handleAppResume();
      focus.reportAppVisibility(foreground: true);
      if (focus.stage == FocusStage.ended) focus.refreshRunCheckIn();
    } else if (state == AppLifecycleState.paused) {
      context.read<FocusProvider>().reportAppVisibility(foreground: false);
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _thoughtText.dispose();
    unawaited(_checkInVoice.dispose());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final focus = context.watch<FocusProvider>();
    final session = focus.session;
    final runId = focus.serverRunId;
    if (focus.stage == FocusStage.ended &&
        focus.hasOpenCheckIn &&
        focus.canAnswerCheckIn &&
        runId != null &&
        runId != _spokenCheckInRunId) {
      _spokenCheckInRunId = runId;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && context.read<FocusProvider>().canAnswerCheckIn) {
          unawaited(_checkInVoice.speakFinalPrompt(runId));
        }
      });
    }
    if (session == null) {
      return const Scaffold(
        appBar: MelbelAppBar(title: 'Phiên tập trung', showBack: true),
        body: Center(child: Text('Chưa có phiên được chọn.')),
      );
    }

    return Scaffold(
      appBar: MelbelAppBar(
        title: _titleFor(focus.stage, focus.isOffline),
        showBack: focus.stage != FocusStage.active &&
            focus.stage != FocusStage.breakTime &&
            focus.stage != FocusStage.recovery &&
            focus.stage != FocusStage.saveFailed &&
            focus.stage != FocusStage.textFallback,
        showGlobalActions: false,
        onBack: () => _leave(context, focus),
      ),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: MelbelLayout.pageInsets(context, top: 12, bottom: 28),
          children: [
            if (focus.isOffline && focus.isActiveRun) ...[
              const FocusOfflineBanner(),
              const SizedBox(height: 12),
            ],
            if (focus.runCommandBusy)
              const Padding(
                padding: EdgeInsets.only(bottom: 10),
                child: ClipRRect(
                  borderRadius: BorderRadius.all(Radius.circular(99)),
                  child: LinearProgressIndicator(
                    minHeight: 4,
                    semanticsLabel: 'Đang đồng bộ phiên',
                  ),
                ),
              ),
            if (focus.runError != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: FocusInfoCard(
                  icon: Icons.info_outline_rounded,
                  title: 'Lưu ý đồng bộ',
                  subtitle: focus.runError!,
                  tone: MelbelCardTone.amber,
                ),
              ),
            if (focus.startRequiresReplan)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: SecondaryCta(
                  label: 'Xem lịch',
                  icon: Icons.calendar_month_outlined,
                  onPressed: () =>
                      Navigator.of(context).pushReplacementNamed('/calendar'),
                ),
              ),
            _buildStage(context, focus, session),
          ],
        ),
      ),
    );
  }

  String _titleFor(FocusStage stage, bool offline) {
    if (offline && stage == FocusStage.active) return 'Tập trung ngoại tuyến';
    switch (stage) {
      case FocusStage.reminder:
        return 'Sắp đến phiên';
      case FocusStage.lockPreview:
        return 'Thông báo phiên';
      case FocusStage.microTask:
        return 'Khởi động nhẹ';
      case FocusStage.ready:
        return 'Bắt đầu phiên';
      case FocusStage.lateStart:
        return 'Phiên đã trễ giờ';
      case FocusStage.active:
        return 'Tập trung chuyên sâu';
      case FocusStage.breakTime:
        return 'Nghỉ giữa phiên';
      case FocusStage.recovery:
        return 'Tiếp tục phiên';
      case FocusStage.thoughtSaved:
        return 'Đã cất suy nghĩ';
      case FocusStage.saveFailed:
        return 'Chưa lưu được ghi âm';
      case FocusStage.textFallback:
        return 'Ghi chú bằng văn bản';
      case FocusStage.ended:
        return 'Phiên đã dừng';
    }
  }

  Widget _buildStage(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    switch (focus.stage) {
      case FocusStage.reminder:
        return _reminder(context, focus, session);
      case FocusStage.lockPreview:
        return _lockPreview(context, focus, session);
      case FocusStage.microTask:
        return _microTask(context, focus, session);
      case FocusStage.ready:
        return _ready(context, focus, session);
      case FocusStage.lateStart:
        return _late(context, focus, session);
      case FocusStage.active:
        return _active(context, focus, session);
      case FocusStage.breakTime:
        return _break(context, focus, session);
      case FocusStage.recovery:
        return _recovery(context, focus, session);
      case FocusStage.thoughtSaved:
        return _thoughtSaved(context, focus, session);
      case FocusStage.saveFailed:
        return _saveFailed(context, focus, session);
      case FocusStage.textFallback:
        return _textFallback(context, focus, session);
      case FocusStage.ended:
        return _ended(context, focus, session);
    }
  }

  Widget _captureClock(BuildContext context, FocusProvider focus) => Text(
        'Còn ${formatFocusTimer(focus.remainingSeconds)} trong chặng này',
        style: Theme.of(context).textTheme.bodyMedium?.copyWith(
          color: context.melbel.onSurfaceVariant,
          fontFeatures: const [FontFeature.tabularFigures()],
        ),
      );

  Widget _reminder(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    final palette = context.melbel;
    final text = Theme.of(context).textTheme;
    final reminderAt =
        session.scheduledStart.subtract(const Duration(minutes: 15));

    return MelbelEntrance(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const MelbelStatusPill(
            label: 'Nhắc trước phiên',
            icon: Icons.notifications_none_rounded,
            tone: MelbelCardTone.sage,
          ),
          const SizedBox(height: 16),
          Text(
            'Sắp đến lúc bắt đầu',
            style: text.headlineSmall?.copyWith(color: palette.onSurface),
          ),
          const SizedBox(height: 6),
          Text(
            'Bạn có thể chuẩn bị từ từ. Không cần bắt đầu ngay.',
            style: text.bodyLarge?.copyWith(color: palette.onSurfaceVariant),
          ),
          const SizedBox(height: 18),
          FocusSessionSummaryCard(session: session),
          const SizedBox(height: 12),
          FocusInfoCard(
            icon: Icons.schedule_outlined,
            title: 'Nhắc chuẩn bị lúc ${formatFocusClock(reminderAt)}',
            subtitle: 'Trước giờ hẹn 15 phút',
          ),
          const SizedBox(height: 20),
          PrimaryCta(
            label: 'Bắt đầu phiên ${focusMinutes(session.workSeconds)} phút',
            icon: Icons.play_arrow_rounded,
            onPressed: focus.runCommandBusy ? null : focus.startRun,
          ),
          const SizedBox(height: 10),
          SecondaryCta(
            label: 'Xem việc khởi động 2 phút',
            icon: Icons.spa_outlined,
            onPressed: focus.showMicroTask,
          ),
          const SizedBox(height: 8),
          SecondaryCta(
            label: 'Xem thông báo màn khóa',
            icon: Icons.lock_outline_rounded,
            onPressed: focus.showLockPreview,
          ),
          const SizedBox(height: 6),
          Center(
            child: TextButton(
              onPressed: focus.skipMicroTask,
              child: const Text('Đến màn bắt đầu phiên'),
            ),
          ),
        ],
      ),
    );
  }

  Widget _lockPreview(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    final palette = context.melbel;
    final text = Theme.of(context).textTheme;

    return MelbelEntrance(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const MelbelStatusPill(
            label: 'Bản xem trước thông báo',
            icon: Icons.lock_outline_rounded,
            tone: MelbelCardTone.muted,
          ),
          const SizedBox(height: 24),
          Center(
            child: Text(
              formatFocusClock(DateTime.now()),
              style: text.displayMedium?.copyWith(
                color: palette.onSurface,
                fontWeight: FontWeight.w300,
              ),
            ),
          ),
          Center(
            child: Text(
              formatFocusWeekday(DateTime.now()),
              style: text.bodyLarge?.copyWith(color: palette.onSurfaceVariant),
            ),
          ),
          const SizedBox(height: 24),
          FocusInfoCard(
            icon: Icons.spa_outlined,
            title: 'Melbel · Sắp đến phiên',
            subtitle:
                '${session.taskTitle}\nBắt đầu lúc ${formatFocusClock(session.scheduledStart)} · Còn khoảng 15 phút',
            tone: MelbelCardTone.sage,
          ),
          const SizedBox(height: 10),
          Text(
            'Đây là mô phỏng trong ứng dụng, chưa phải thông báo hệ thống đã được lên lịch.',
            style: text.bodySmall?.copyWith(color: palette.onSurfaceVariant),
          ),
          const SizedBox(height: 24),
          PrimaryCta(
            label: 'Quay lại chuẩn bị',
            onPressed: () => focus.startSession(session),
          ),
        ],
      ),
    );
  }

  Widget _microTask(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    final palette = context.melbel;
    final text = Theme.of(context).textTheme;

    return MelbelEntrance(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            session.microTaskText,
            style: text.headlineSmall?.copyWith(color: palette.onSurface),
          ),
          const SizedBox(height: 8),
          Text('Khởi động 2 phút trước phiên chính.', style: text.bodyMedium),
          if (focus.isMicroTaskRunning) ...[
            const SizedBox(height: 12),
            MelbelCard(
              padding: const EdgeInsets.all(18),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Wrap(
                    alignment: WrapAlignment.spaceBetween,
                    spacing: 12,
                    runSpacing: 4,
                    children: [
                      Text('Thời gian khởi động', style: text.titleSmall),
                      Text(
                        formatFocusTimer(focus.microTaskRemainingSeconds),
                        style: text.titleMedium?.copyWith(
                          color: palette.sage,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  ClipRRect(
                    borderRadius: BorderRadius.circular(99),
                    child: LinearProgressIndicator(
                      value: focus.microTaskProgress,
                      minHeight: 8,
                    ),
                  ),
                ],
              ),
            ),
          ],
          const SizedBox(height: 12),
          FocusSessionSummaryCard(session: session, compact: true),
          const SizedBox(height: 20),
          PrimaryCta(
            label:
                focus.isMicroTaskRunning ? 'Đang khởi động…' : 'Bắt đầu 2 phút',
            icon: focus.isMicroTaskRunning
                ? Icons.hourglass_top_rounded
                : Icons.play_arrow_rounded,
            onPressed:
                focus.isMicroTaskRunning ? null : focus.continueFromMicroTask,
          ),
          const SizedBox(height: 10),
          SecondaryCta(
            label: 'Để sau · đến phiên chính',
            onPressed: focus.skipMicroTask,
          ),
        ],
      ),
    );
  }

  Widget _ready(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    return MelbelEntrance(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const MelbelStatusPill(
            label: 'Phiên đã sẵn sàng',
            icon: Icons.spa_outlined,
            tone: MelbelCardTone.sage,
          ),
          const SizedBox(height: 18),
          FocusSessionSummaryCard(session: session),
          const SizedBox(height: 12),
          if (focus.supportsDnd)
            FocusInfoCard(
              icon: focus.dndPermission
                  ? Icons.do_not_disturb_on_rounded
                  : Icons.info_outline_rounded,
              title: focus.dndPermission
                  ? 'Quyền Không làm phiền đã sẵn sàng'
                  : 'Chưa cấp quyền Không làm phiền',
              subtitle: focus.dndPermission
                  ? 'Khi bắt đầu, Melbel sẽ bật chế độ tập trung.'
                  : 'Bạn vẫn có thể bắt đầu. Có thể cấp quyền trong Cài đặt Android.',
              tone: focus.dndPermission
                  ? MelbelCardTone.sage
                  : MelbelCardTone.muted,
            ),
          const SizedBox(height: 20),
          PrimaryCta(
            label: 'Bắt đầu đếm giờ ${focusMinutes(session.workSeconds)} phút',
            icon: Icons.play_arrow_rounded,
            onPressed: focus.runCommandBusy ? null : focus.startRun,
          ),
          if (focus.supportsDnd && !focus.dndPermission) ...[
            const SizedBox(height: 6),
            Center(
              child: TextButton(
                onPressed: focus.openDndSettings,
                child: const Text('Cấp quyền Không làm phiền'),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _late(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    final palette = context.melbel;
    final text = Theme.of(context).textTheme;

    return MelbelEntrance(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Bắt đầu khi bạn sẵn sàng',
            style: text.headlineSmall?.copyWith(color: palette.onSurface),
          ),
          const SizedBox(height: 6),
          Text(
            'Phiên này đã qua giờ bắt đầu.',
            style: text.bodyLarge?.copyWith(color: palette.onSurfaceVariant),
          ),
          const SizedBox(height: 18),
          FocusSessionSummaryCard(session: session),
          const SizedBox(height: 20),
          PrimaryCta(
            label: 'Bắt đầu phiên ngay',
            icon: Icons.play_arrow_rounded,
            onPressed: focus.runCommandBusy ? null : focus.startLateRun,
          ),
          const SizedBox(height: 6),
          Center(
            child: TextButton(
              onPressed: () => focus.startSession(session),
              child: const Text('Quay lại chuẩn bị'),
            ),
          ),
        ],
      ),
    );
  }

  Widget _active(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    final palette = context.melbel;
    final segment = focus.currentSegment;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Flexible(
              child: MelbelStatusPill(
                label: focus.dndApplied ? 'DND BẬT' : 'Đang tập trung',
                icon: focus.dndApplied
                    ? Icons.do_not_disturb_on_rounded
                    : Icons.circle,
                tone: MelbelCardTone.sage,
              ),
            ),
            IconButton(
              tooltip: 'Mở hộp suy nghĩ',
              onPressed: () => _showThoughts(context, focus),
              icon: Icon(
                Icons.inbox_outlined,
                color: palette.onSurfaceVariant,
              ),
            ),
          ],
        ),
        const SizedBox(height: 10),
        FocusSessionSummaryCard(session: session, compact: true),
        const SizedBox(height: 16),
        FocusTimerProgressCard(
          segment: segment,
          index: focus.segmentIndex,
          count: focus.segments.length,
          remaining: focus.remainingSeconds,
          progress: focus.progress,
          isOffline: focus.isOffline,
        ),
        if (focus.alarmScheduled == false) ...[
          const SizedBox(height: 12),
          const FocusInfoCard(
            icon: Icons.notifications_off_outlined,
            title: 'Chưa đặt được chuông kết thúc',
            subtitle:
                'Đồng hồ vẫn chạy. Kiểm tra quyền thông báo và báo thức chính xác trong Cài đặt Android.',
            tone: MelbelCardTone.amber,
          ),
        ],
        const SizedBox(height: 18),
        FocusHoldToRecord(
          key: _recordButtonKey,
          isRecording: focus.isRecording,
          onStart: focus.beginRecording,
          onStop: focus.stopAndSaveThought,
          onCancel: focus.cancelRecording,
        ),
        const SizedBox(height: 18),
        Row(
          children: [
            Expanded(
              child: SecondaryCta(
                label: 'Kết thúc phiên sớm',
                icon: Icons.stop_circle_outlined,
                height: 50,
                onPressed: () => _endEarly(context, focus),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: SecondaryCta(
                label: 'Hộp suy nghĩ',
                icon: Icons.inbox_outlined,
                height: 50,
                backgroundColor: palette.sageSoft,
                onPressed: () => _showThoughts(context, focus),
              ),
            ),
          ],
        ),
      ],
    );
  }

  Widget _break(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    final palette = context.melbel;
    final text = Theme.of(context).textTheme;

    return MelbelEntrance(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const MelbelStatusPill(
            label: 'Khoảng nghỉ ngắn',
            icon: Icons.coffee_outlined,
            tone: MelbelCardTone.amber,
          ),
          const SizedBox(height: 16),
          Text(
            'Nghỉ một chút nhé',
            style: text.headlineSmall?.copyWith(color: palette.onSurface),
          ),
          const SizedBox(height: 6),
          Text(
            'Thời gian nghỉ được tính riêng.',
            style: text.bodyLarge?.copyWith(color: palette.onSurfaceVariant),
          ),
          const SizedBox(height: 16),
          FocusTimerProgressCard(
            segment: focus.currentSegment,
            index: focus.segmentIndex,
            count: focus.segments.length,
            remaining: focus.remainingSeconds,
            progress: focus.progress,
            isOffline: focus.isOffline,
          ),
          const SizedBox(height: 14),
          MelbelChatBlock(
            avatarOnRight: true,
            expression: CatExpression.sleepy,
            tone: MelbelCardTone.amber,
            senderLabel: 'Melbel',
            badge: 'Giờ nghỉ',
            title: 'Đứng dậy hoặc uống ngụm nước',
            message: _breakWhisper,
            mascotWidth: 72,
            mascotHeight: 66,
            onPetted: (whisper) {
              setState(() => _breakWhisper = whisper);
            },
          ),
          const SizedBox(height: 12),
          FocusSessionSummaryCard(session: session, compact: true),
          const SizedBox(height: 14),
          Center(
            child: TextButton.icon(
              onPressed: () => _showThoughts(context, focus),
              icon: const Icon(Icons.inbox_outlined),
              label: const Text('Mở hộp suy nghĩ'),
            ),
          ),
        ],
      ),
    );
  }

  Widget _recovery(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    return MelbelEntrance(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const MelbelStatusPill(
            label: 'Đã khôi phục phiên',
            icon: Icons.restore_rounded,
            tone: MelbelCardTone.sage,
          ),
          const SizedBox(height: 12),
          FocusSessionSummaryCard(session: session, compact: true),
          const SizedBox(height: 12),
          PrimaryCta(
            label: 'Quay lại phiên đang làm',
            icon: Icons.play_arrow_rounded,
            onPressed: focus.resumeRecoveredRun,
          ),
          const SizedBox(height: 12),
          FocusTimerProgressCard(
            segment: focus.currentSegment,
            index: focus.segmentIndex,
            count: focus.segments.length,
            remaining: focus.remainingSeconds,
            progress: focus.progress,
            isOffline: focus.isOffline,
          ),
          Center(
            child: TextButton(
              onPressed: () => _showThoughts(context, focus),
              child: const Text('Mở hộp suy nghĩ đã cất'),
            ),
          ),
        ],
      ),
    );
  }

  Widget _thoughtSaved(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    return MelbelEntrance(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          FocusSessionSummaryCard(session: session, compact: true),
          const SizedBox(height: 14),
          _captureClock(context, focus),
          const SizedBox(height: 14),
          const FocusInfoCard(
            icon: Icons.check_circle_outline_rounded,
            title: 'Đã cất suy nghĩ',
            subtitle:
                'Đã lưu trên thiết bị. Bạn có thể quay lại bước đang làm.',
            tone: MelbelCardTone.sage,
          ),
          if (focus.canUndoThought) ...[
            const SizedBox(height: 12),
            SecondaryCta(
              label: 'Hoàn tác trong 5 giây',
              icon: Icons.undo_rounded,
              onPressed: focus.undoLastThought,
            ),
          ],
        ],
      ),
    );
  }

  Widget _saveFailed(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    return MelbelEntrance(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const MelbelStatusPill(
            label: 'Phiên vẫn đang chạy',
            icon: Icons.circle,
            tone: MelbelCardTone.sage,
          ),
          const SizedBox(height: 16),
          FocusSessionSummaryCard(session: session, compact: true),
          const SizedBox(height: 14),
          FocusInfoCard(
            icon: Icons.mic_off_outlined,
            title: 'Chưa lưu được ghi âm lần ${focus.recordingAttempts}/3',
            subtitle:
                focus.lastError ?? 'Bạn có thể thử ghi lại. Đồng hồ vẫn chạy.',
            tone: MelbelCardTone.amber,
          ),
          const SizedBox(height: 20),
          FocusHoldToRecord(
            key: _recordButtonKey,
            isRecording: focus.isRecording,
            onStart: focus.retryRecording,
            onStop: focus.stopAndSaveThought,
            onCancel: focus.cancelRecording,
            label: 'Giữ để thử ghi lại',
          ),
          const SizedBox(height: 12),
          Center(
            child: TextButton(
              onPressed: focus.returnToActive,
              child: const Text('Quay lại phiên'),
            ),
          ),
        ],
      ),
    );
  }

  Widget _textFallback(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    final palette = context.melbel;

    return MelbelEntrance(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const MelbelStatusPill(
            label: 'Phiên vẫn đang chạy',
            icon: Icons.circle,
            tone: MelbelCardTone.sage,
          ),
          const SizedBox(height: 14),
          FocusSessionSummaryCard(session: session, compact: true),
          const SizedBox(height: 12),
          _captureClock(context, focus),
          const SizedBox(height: 14),
          const FocusInfoCard(
            icon: Icons.edit_note_outlined,
            title: 'Chưa thu được sau 3 lần',
            subtitle: 'Gõ suy nghĩ để lưu vào hộp nháp.',
            tone: MelbelCardTone.sage,
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _thoughtText,
            autofocus: true,
            maxLines: 1,
            textCapitalization: TextCapitalization.sentences,
            decoration: const InputDecoration(
              labelText: 'Ghi chú suy nghĩ',
              hintText: 'Gõ nhanh suy nghĩ của bạn vào đây…',
            ),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 14),
          PrimaryCta(
            label: 'Cất ghi chú',
            icon: Icons.check_rounded,
            onPressed: _thoughtText.text.trim().isEmpty
                ? null
                : () async {
                    await focus.saveTextThought(_thoughtText.text);
                    _thoughtText.clear();
                  },
          ),
          const SizedBox(height: 6),
          Center(
            child: TextButton(
              onPressed: () {
                _thoughtText.clear();
                focus.returnToActive();
              },
              child: const Text('Bỏ qua'),
            ),
          ),
          Center(
            child: Text(
              'Ghi chú được lưu trên thiết bị.',
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    color: palette.onSurfaceVariant,
                  ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _ended(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    final palette = context.melbel;

    return MelbelEntrance(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  'Kết thúc phiên ${focusMinutes(session.workSeconds)} phút',
                  style: Theme.of(context).textTheme.titleLarge,
                ),
              ),
              if (focus.dndApplied)
                const MelbelStatusPill(
                  label: 'DND bật',
                  icon: Icons.do_not_disturb_on_outlined,
                  tone: MelbelCardTone.sage,
                ),
            ],
          ),
          const SizedBox(height: 12),
          ReviewHeader(
            focus.missedCheckIn
                ? 'Chưa phản hồi'
                : focus.hasOpenCheckIn
                    ? 'Bạn làm đến đâu rồi?'
                    : 'Phiên đã dừng',
            focus.missedCheckIn
                ? 'Bạn có thể ghi nhận kết quả phiên khi sẵn sàng.'
                : focus.hasOpenCheckIn
                    ? 'Chọn kết quả của phiên vừa rồi.'
                    : 'Xem thời gian đã làm bên dưới.',
            expression: focus.missedCheckIn
                ? CatExpression.sleepy
                : CatExpression.neutral,
          ),
          const SizedBox(height: 16),
          ReviewSessionCard(
            session: session,
            actualSeconds: focus.actualWorkSeconds,
          ),
          const SizedBox(height: 16),
          if (focus.needsEndSync) ...[
            PrimaryCta(
              label: 'Đồng bộ kết thúc phiên',
              onPressed: focus.runCommandBusy ? null : focus.syncRunEnd,
            ),
            const SizedBox(height: 10),
          ],
          if (focus.canUndoSkip) ...[
            SecondaryCta(
              label: 'Hoàn tác bỏ qua',
              icon: Icons.undo_rounded,
              onPressed: focus.undoSkippedCheckIn,
            ),
            const SizedBox(height: 10),
          ] else if (focus.hasPendingCheckInResponse) ...[
            PrimaryCta(
              label: 'Gửi lại kết quả phiên',
              onPressed:
                  focus.runCommandBusy ? null : focus.retryCheckInResponse,
            ),
            const SizedBox(height: 10),
          ],
          if (focus.canAnswerCheckIn && !focus.hasPendingCheckInResponse) ...[
            for (final (label, outcome, icon, toneAmber) in const [
              (
                'Hoàn thành phiên',
                'completed',
                Icons.check_circle_outline_rounded,
                false,
              ),
              (
                'Chưa xong',
                'incomplete',
                Icons.timelapse_outlined,
                true,
              ),
              (
                'Việc đột xuất',
                'busy',
                Icons.pause_circle_outline_rounded,
                true,
              ),
              (
                'Bỏ qua phiên',
                'skipped',
                Icons.skip_next_outlined,
                false,
              ),
            ])
              Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: MelbelCard(
                  borderRadius: 24,
                  padding: EdgeInsets.zero,
                  child: ListTile(
                    contentPadding: const EdgeInsets.symmetric(
                      horizontal: 16,
                      vertical: 4,
                    ),
                    leading: Container(
                      width: 42,
                      height: 42,
                      decoration: BoxDecoration(
                        color: toneAmber
                            ? palette.amberSoft
                            : palette.sageSoft,
                        borderRadius: BorderRadius.circular(14),
                      ),
                      alignment: Alignment.center,
                      child: Icon(
                        icon,
                        size: 21,
                        color: toneAmber ? palette.amber : palette.sage,
                      ),
                    ),
                    title: Text(
                      label,
                      style: Theme.of(context).textTheme.titleMedium?.copyWith(
                            fontWeight: FontWeight.w700,
                          ),
                    ),
                    trailing: Icon(
                      Icons.chevron_right_rounded,
                      color: palette.onSurfaceVariant,
                    ),
                    enabled: !focus.runCommandBusy,
                    onTap: focus.runCommandBusy
                        ? null
                        : () => outcome == 'skipped'
                            ? _chooseSkip(focus)
                            : _reviewOutcome(focus, session, outcome),
                  ),
                ),
              ),
            Center(
              child: TextButton(
                onPressed: focus.runCommandBusy
                    ? null
                    : () async {
                        await focus.restAfterCheckIn();
                        if (!context.mounted) return;
                        await Navigator.of(context).push(
                          MaterialPageRoute<void>(
                            builder: (_) => const FocusRestScreen(),
                          ),
                        );
                      },
                child: const Text('Nghỉ giải lao'),
              ),
            ),
          ],
          if (!focus.canAnswerCheckIn && focus.serverRunId != null) ...[
            SecondaryCta(
              label: 'Xem phương án đổi lịch',
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => ScheduleChangeReasonScreen(
                    session: session,
                    runId: focus.serverRunId,
                  ),
                ),
              ),
            ),
            const SizedBox(height: 10),
          ],
          PrimaryCta(
            label: 'Quay về trang trước',
            onPressed: () => Navigator.of(context).pop(),
          ),
          if (focus.thoughts.isNotEmpty)
            Center(
              child: TextButton(
                onPressed: () => _showThoughts(context, focus),
                child: const Text('Mở hộp suy nghĩ'),
              ),
            ),
        ],
      ),
    );
  }

  Future<void> _chooseSkip(FocusProvider focus) async {
    final session = focus.session;
    if (session == null) return;
    final scope = await Navigator.of(context).push<String>(
      MaterialPageRoute(
        builder: (context) => FocusReviewPage(
          title: 'Bỏ qua phiên',
          children: [
            const ReviewHeader(
              'Bạn muốn bỏ qua thế nào?',
              'Chọn phạm vi cho phiên này.',
            ),
            ReviewSessionCard(session: session),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final (label, value) in const [
                  ('Hoãn phiên này', 'postpone_session'),
                  ('Bỏ qua phiên này', 'skip_session'),
                  ('Bỏ toàn bộ bước', 'skip_step'),
                ])
                  ActionChip(
                    label: Text(label),
                    onPressed: () => Navigator.pop(context, value),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
    if (scope == null || !mounted) return;
    await focus.queueSkippedCheckIn(scope);
    if (!mounted || !focus.canUndoSkip) return;
    showMelbelUndoSnackBar(
      context,
      message: 'Đã chọn bỏ qua.',
      onUndo: focus.undoSkippedCheckIn,
    );
  }

  Future<void> _reviewOutcome(
    FocusProvider focus,
    FocusSessionInfo session,
    String outcome,
  ) async {
    String? note;
    bool? stepCompleted;
    if (outcome == 'incomplete') {
      note = await Navigator.of(context).push<String>(
        MaterialPageRoute(
          builder: (_) => UnfinishedReviewScreen(session: session),
        ),
      );
      if (!mounted || note == null) return;
    }
    if (outcome == 'completed' && session.isLastSessionOfStep) {
      stepCompleted = await Navigator.of(context).push<bool>(
        MaterialPageRoute(
          builder: (context) => FocusReviewPage(
            title: 'Phiên cuối của bước',
            children: [
              const ReviewHeader(
                'Bước này đã xong chưa?',
                'Ghi nhận riêng kết quả của bước.',
              ),
              ReviewSessionCard(session: session),
              PrimaryCta(
                label: 'Đã xong bước',
                onPressed: () => Navigator.pop(context, true),
              ),
              SecondaryCta(
                label: 'Bước chưa xong',
                onPressed: () => Navigator.pop(context, false),
              ),
            ],
          ),
        ),
      );
      if (!mounted || stepCompleted == null) return;
    }
    await focus.answerCheckIn(
      outcome,
      remainingNote: note,
      stepCompleted: stepCompleted,
      preserveFocusMode: outcome == 'completed',
    );
    if (!mounted || focus.canAnswerCheckIn || focus.runError != null) return;
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => outcome == 'completed'
            ? NextFocusActionScreen(session: session)
            : ScheduleChangeReasonScreen(
                session: session,
                runId: focus.serverRunId,
                initialReason:
                    outcome == 'busy' ? 'checkin_busy' : 'checkin_incomplete',
              ),
      ),
    );
  }

  Future<void> _leave(BuildContext context, FocusProvider focus) async {
    Navigator.of(context).pop();
  }

  Future<void> _endEarly(BuildContext context, FocusProvider focus) async {
    await focus.endEarly();
  }

  Future<void> _showThoughts(BuildContext context, FocusProvider focus) =>
      Navigator.of(context).push<void>(
        MaterialPageRoute(builder: (_) => const ThoughtInboxScreen()),
      );
}

class ThoughtInboxScreen extends StatefulWidget {
  const ThoughtInboxScreen({super.key});

  @override
  State<ThoughtInboxScreen> createState() => _ThoughtInboxScreenState();
}

class _ThoughtInboxScreenState extends State<ThoughtInboxScreen> {
  final _audioPlayer = AudioPlayer();
  String? _playingThoughtId;
  bool _showDraft = false;
  bool _isConverting = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) unawaited(context.read<FocusProvider>().loadThoughts());
    });
  }

  @override
  void dispose() {
    _audioPlayer.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final focus = context.watch<FocusProvider>();
    final task = context.watch<TaskProvider>();
    return FocusReviewPage(
      title: 'Hộp nháp',
      children: [
        const ReviewHeader(
          'Hộp suy nghĩ',
          'Nghe lại ghi âm hoặc xem ghi chú khi bạn sẵn sàng.',
        ),
        SegmentedButton<bool>(
          segments: const [
            ButtonSegment(value: true, label: Text('Công việc nháp')),
            ButtonSegment(value: false, label: Text('Suy nghĩ đã cất')),
          ],
          selected: {_showDraft},
          onSelectionChanged: (value) =>
              setState(() => _showDraft = value.single),
        ),
        if (_showDraft && task.title.isNotEmpty && !task.planLocked)
          MelbelCard(
            borderRadius: 24,
            padding: EdgeInsets.zero,
            child: ListTile(
              contentPadding:
                  const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
              leading: Container(
                width: 40,
                height: 40,
                decoration: BoxDecoration(
                  color: context.melbel.sageSoft,
                  borderRadius: BorderRadius.circular(14),
                ),
                child: Icon(
                  Icons.edit_note_rounded,
                  color: context.melbel.sage,
                ),
              ),
              title: Text(
                task.title,
                style: Theme.of(context)
                    .textTheme
                    .titleSmall
                    ?.copyWith(fontWeight: FontWeight.w700),
              ),
              subtitle: Text(task.formatDeadline(task.deadline)),
              trailing: const Icon(Icons.chevron_right_rounded),
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => const TaskEntryScreen(),
                ),
              ),
            ),
          )
        else if (_showDraft)
          MelbelCard(
            borderRadius: 24,
            tone: MelbelCardTone.muted,
            child: Row(
              children: [
                Container(
                  width: 40,
                  height: 40,
                  decoration: BoxDecoration(
                    color: context.melbel.sageSoft,
                    borderRadius: BorderRadius.circular(14),
                  ),
                  child: Icon(
                    Icons.inbox_outlined,
                    size: 20,
                    color: context.melbel.sage,
                  ),
                ),
                const SizedBox(width: 12),
                const Expanded(child: Text('Chưa có công việc nháp.')),
              ],
            ),
          ),
        if (!_showDraft && focus.thoughts.isEmpty)
          MelbelCard(
            borderRadius: 24,
            tone: MelbelCardTone.muted,
            child: Row(
              children: [
                Container(
                  width: 40,
                  height: 40,
                  decoration: BoxDecoration(
                    color: context.melbel.amberSoft,
                    borderRadius: BorderRadius.circular(14),
                  ),
                  child: Icon(
                    Icons.lightbulb_outline_rounded,
                    size: 20,
                    color: context.melbel.amber,
                  ),
                ),
                const SizedBox(width: 12),
                const Expanded(child: Text('Chưa có suy nghĩ nào được cất.')),
              ],
            ),
          ),
        if (!_showDraft)
          for (final thought in focus.thoughts)
            Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                FocusThoughtTile(
                  thought: thought,
                  isPlaying: _playingThoughtId == thought.id,
                  onPlay: thought.audioPath == null
                      ? null
                      : () => _playThought(thought),
                  onDelete: () async {
                    if (_playingThoughtId == thought.id) {
                      await _audioPlayer.stop();
                      if (mounted) setState(() => _playingThoughtId = null);
                    }
                    await focus.deleteThought(thought);
                    if (!context.mounted) return;
                    showMelbelUndoSnackBar(
                      context,
                      message: 'Đã xóa ghi chú',
                      onUndo: () => unawaited(focus.undoDeletedThought()),
                    );
                  },
                ),
                if (!focus.isActiveRun &&
                    !focus.needsEndSync &&
                    !focus.runCommandBusy &&
                    !task.hasUnconfirmedDraft &&
                    thought.serverThoughtId != null)
                  TextButton.icon(
                    onPressed: _isConverting || task.isApiBusy
                        ? null
                        : () => _convertThought(focus, task, thought),
                    icon: const Icon(Icons.edit_note_outlined),
                    label: const Text('Tạo việc từ suy nghĩ'),
                  ),
              ],
            ),
      ],
    );
  }

  Future<void> _convertThought(
    FocusProvider focus,
    TaskProvider task,
    ThoughtRecord thought,
  ) async {
    final text = await Navigator.of(context).push<String>(
      MaterialPageRoute(
        builder: (_) => _ThoughtDraftEntry(
          initialText: thought.text ?? '',
          transcribe: thought.localAudioPath == null
              ? null
              : () => focus.transcribeThought(thought),
        ),
      ),
    );
    if (!mounted ||
        text == null ||
        task.isApiBusy ||
        task.hasUnconfirmedDraft) {
      return;
    }
    setState(() => _isConverting = true);
    try {
      final draft = await focus.convertThoughtToDraft(thought, text);
      if (!mounted) return;
      try {
        await task.analyzeThoughtDraft(draft);
      } catch (_) {
        if (!mounted || task.draft?.draftId != draft.draftId) rethrow;
      }
      if (!mounted) return;
      await Navigator.of(context).push<void>(
        MaterialPageRoute(builder: (_) => const TaskSummaryScreen()),
      );
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(error.toString())),
        );
      }
    } finally {
      if (mounted) setState(() => _isConverting = false);
    }
  }

  Future<void> _playThought(ThoughtRecord thought) async {
    final path = thought.audioPath;
    if (path == null) return;
    try {
      if (_playingThoughtId == thought.id) {
        await _audioPlayer.stop();
        setState(() => _playingThoughtId = null);
      } else {
        setState(() => _playingThoughtId = thought.id);
        await _audioPlayer.play(DeviceFileSource(path));
        _audioPlayer.onPlayerComplete.first.then((_) {
          if (mounted) setState(() => _playingThoughtId = null);
        });
      }
    } catch (_) {
      if (mounted) {
        setState(() => _playingThoughtId = null);
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Chưa phát được tệp ghi âm này.')),
        );
      }
    }
  }
}

class _ThoughtDraftEntry extends StatefulWidget {
  const _ThoughtDraftEntry({required this.initialText, this.transcribe});
  final String initialText;
  final Future<String> Function()? transcribe;

  @override
  State<_ThoughtDraftEntry> createState() => _ThoughtDraftEntryState();
}

class _ThoughtDraftEntryState extends State<_ThoughtDraftEntry> {
  late final _text = TextEditingController(text: widget.initialText);
  bool _transcribing = false;

  Future<void> _transcribe() async {
    setState(() => _transcribing = true);
    try {
      final text = await widget.transcribe!();
      if (mounted) _text.text = text;
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Chưa chuyển được ghi âm. Bạn có thể nhập chữ.'),
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _transcribing = false);
    }
  }

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => FocusReviewPage(
        title: 'Tạo việc từ suy nghĩ',
        children: [
          if (widget.transcribe != null)
            SecondaryCta(
              label: _transcribing ? 'Đang chuyển…' : 'Chuyển ghi âm thành chữ',
              onPressed: _transcribing ? null : _transcribe,
            ),
          TextField(
            enabled: !_transcribing,
            controller: _text,
            minLines: 3,
            maxLines: 6,
            onChanged: (_) => setState(() {}),
            decoration: const InputDecoration(
              labelText: 'Nội dung công việc',
              hintText: 'Nhập nội dung từ ghi âm hoặc suy nghĩ của bạn.',
            ),
          ),
          PrimaryCta(
            label: 'Tạo bản nháp',
            onPressed: _transcribing || _text.text.trim().isEmpty
                ? null
                : () => Navigator.pop(context, _text.text.trim()),
          ),
        ],
      );
}
