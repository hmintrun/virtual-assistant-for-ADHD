import 'dart:async';
import 'dart:ui' show FontFeature;

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../models/focus_session_model.dart';
import '../models/task_model.dart';
import '../providers/focus_provider.dart';
import '../widgets/melbel_ui.dart';

/// UF3–UF4 flow for an already scheduled Session. The session comes from the
/// confirmed plan; this screen does not create or complete Tasks/Steps.
class FocusScreen extends StatefulWidget {
  const FocusScreen({super.key});

  @override
  State<FocusScreen> createState() => _FocusScreenState();
}

class _FocusScreenState extends State<FocusScreen> with WidgetsBindingObserver {
  final TextEditingController _thoughtText = TextEditingController();
  final AudioPlayer _audioPlayer = AudioPlayer();
  String? _playingThoughtId;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      final focus = context.read<FocusProvider>();
      focus.refreshDndPermission();
      focus.handleAppResume();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _thoughtText.dispose();
    _audioPlayer.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final focus = context.watch<FocusProvider>();
    final session = focus.session;
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
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 28),
          children: [
            if (focus.isOffline && focus.isActiveRun) ...[
              const _OfflineBanner(),
              const SizedBox(height: 12),
            ],
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

  Widget _runClock(BuildContext context, FocusProvider focus) => Column(
        children: [
          _ProgressCard(
            segment: focus.currentSegment,
            index: focus.segmentIndex,
            count: focus.segments.length,
            remaining: focus.remainingSeconds,
            progress: focus.progress,
          ),
          const SizedBox(height: 14),
          Center(
            child: Text(
              _formatTime(focus.remainingSeconds),
              style: Theme.of(context).textTheme.headlineMedium?.copyWith(
                    color: MelbelColors.kOnSurface,
                    fontWeight: FontWeight.w500,
                    letterSpacing: 2,
                  ),
            ),
          ),
          const SizedBox(height: 4),
          Text(
            'Đồng hồ vẫn chạy trong lúc ghi chú',
            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                  color: MelbelColors.kTextSecondary,
                ),
          ),
        ],
      );

  Widget _reminder(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    final reminderAt =
        session.scheduledStart.subtract(const Duration(minutes: 15));
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _StatusPill(label: 'Nhắc trước phiên', icon: Icons.notifications_none),
      const SizedBox(height: 20),
      Text(
        'Sắp đến lúc bắt đầu',
        style: Theme.of(context).textTheme.headlineSmall?.copyWith(
              color: MelbelColors.kOnSurface,
              fontWeight: FontWeight.w600,
            ),
      ),
      const SizedBox(height: 8),
      Text(
        'Bạn có thể chuẩn bị từ từ. Không cần bắt đầu ngay.',
        style: Theme.of(context).textTheme.bodyLarge?.copyWith(
              color: MelbelColors.kOnSurfaceVariant,
            ),
      ),
      const SizedBox(height: 20),
      _SessionCard(session: session),
      const SizedBox(height: 12),
      _InfoCard(
        icon: Icons.schedule_outlined,
        title: 'Nhắc chuẩn bị lúc ${_time(reminderAt)}',
        subtitle: 'Trước giờ hẹn 15 phút',
      ),
      const SizedBox(height: 20),
      PrimaryCta(
        label: 'Bắt đầu phiên ${_minutes(session.workSeconds)} phút',
        icon: Icons.play_arrow_rounded,
        onPressed: focus.startRun,
      ),
      const SizedBox(height: 8),
      _SecondaryButton(
        label: 'Xem việc khởi động 2 phút',
        onPressed: focus.showMicroTask,
      ),
      const SizedBox(height: 8),
      _SecondaryButton(
        label: 'Xem thông báo màn khóa',
        onPressed: focus.showLockPreview,
      ),
      const SizedBox(height: 8),
      TextButton(
        onPressed: () => focus.skipMicroTask(),
        child: const Text('Đến màn bắt đầu phiên'),
      ),
    ]);
  }

  Widget _lockPreview(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _StatusPill(label: 'Bản xem trước thông báo', icon: Icons.lock_outline),
      const SizedBox(height: 24),
      Center(
        child: Text(
          _time(DateTime.now()),
          style: Theme.of(context).textTheme.displayMedium?.copyWith(
                color: MelbelColors.kOnSurface,
                fontWeight: FontWeight.w300,
              ),
        ),
      ),
      Center(
        child: Text(
          _weekday(DateTime.now()),
          style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                color: MelbelColors.kOnSurfaceVariant,
              ),
        ),
      ),
      const SizedBox(height: 28),
      _InfoCard(
        icon: Icons.nightlight_outlined,
        title: 'Melbel · Sắp đến phiên',
        subtitle:
            '${session.taskTitle}\nBắt đầu lúc ${_time(session.scheduledStart)} · Còn khoảng 15 phút',
      ),
      const SizedBox(height: 10),
      Text(
        'Đây là mô phỏng trong ứng dụng, chưa phải thông báo hệ thống đã được lên lịch.',
        style: Theme.of(context).textTheme.bodySmall?.copyWith(
              color: MelbelColors.kTextSecondary,
            ),
      ),
      const SizedBox(height: 24),
      PrimaryCta(
        label: 'Quay lại chuẩn bị',
        onPressed: () => focus.startSession(session),
      ),
    ]);
  }

  Widget _microTask(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _StatusPill(label: 'Bước đệm · 2 phút', icon: Icons.spa_outlined),
      const SizedBox(height: 20),
      Text(
        'Chỉ cần bắt đầu thật nhỏ',
        style: Theme.of(context).textTheme.headlineSmall?.copyWith(
              color: MelbelColors.kOnSurface,
              fontWeight: FontWeight.w600,
            ),
      ),
      const SizedBox(height: 8),
      Text(
        'Việc này giúp bạn vào guồng. Thời gian khởi động không tính vào phiên tập trung.',
        style: Theme.of(context).textTheme.bodyLarge?.copyWith(
              color: MelbelColors.kOnSurfaceVariant,
            ),
      ),
      const SizedBox(height: 20),
      _InfoCard(
        icon: Icons.checklist_outlined,
        title: 'Việc khởi động',
        subtitle: session.microTaskText,
      ),
      if (focus.isMicroTaskRunning) ...[
        const SizedBox(height: 12),
        MelbelCard(
          padding: const EdgeInsets.all(18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  const Text('Thời gian khởi động'),
                  Text(
                    _formatTime(focus.microTaskRemainingSeconds),
                    style: TextStyle(
                      color: MelbelColors.kPrimaryContainer,
                      fontWeight: FontWeight.w600,
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
                  backgroundColor: MelbelColors.kSurfaceContainerHighest,
                  valueColor: AlwaysStoppedAnimation<Color>(
                    MelbelColors.kPrimaryContainer,
                  ),
                ),
              ),
              const SizedBox(height: 10),
              Text(
                'Đây là bước đệm, không tính vào thời lượng phiên chính.',
                style: TextStyle(color: MelbelColors.kTextSecondary),
              ),
            ],
          ),
        ),
      ],
      const SizedBox(height: 12),
      _SessionCard(session: session),
      const SizedBox(height: 20),
      PrimaryCta(
        label: focus.isMicroTaskRunning ? 'Đang khởi động…' : 'Bắt đầu 2 phút',
        icon: focus.isMicroTaskRunning ? Icons.hourglass_top : Icons.play_arrow,
        onPressed:
            focus.isMicroTaskRunning ? null : focus.continueFromMicroTask,
      ),
      const SizedBox(height: 8),
      _SecondaryButton(
        label: 'Để sau · đến phiên chính',
        onPressed: focus.skipMicroTask,
      ),
    ]);
  }

  Widget _ready(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _StatusPill(label: 'Phiên đã sẵn sàng', icon: Icons.dark_mode_outlined),
      const SizedBox(height: 20),
      _SessionCard(session: session),
      const SizedBox(height: 12),
      _InfoCard(
        icon:
            focus.dndPermission ? Icons.do_not_disturb_on : Icons.info_outline,
        title: focus.dndPermission
            ? 'Quyền Không làm phiền đã sẵn sàng'
            : 'Chưa cấp quyền Không làm phiền',
        subtitle: focus.dndPermission
            ? 'Khi bắt đầu, Melbel sẽ bật chế độ tập trung.'
            : 'Bạn vẫn có thể bắt đầu. Có thể cấp quyền trong Cài đặt Android.',
      ),
      const SizedBox(height: 20),
      PrimaryCta(
        label: 'Bắt đầu đếm giờ ${_minutes(session.workSeconds)} phút',
        icon: Icons.play_arrow_rounded,
        onPressed: focus.startRun,
      ),
      if (!focus.dndPermission)
        TextButton(
          onPressed: focus.openDndSettings,
          child: const Text('Cấp quyền Không làm phiền'),
        ),
    ]);
  }

  Widget _late(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _StatusPill(
          label: 'Bắt đầu muộn',
          icon: Icons.schedule_outlined,
          color: MelbelColors.kAmber),
      const SizedBox(height: 20),
      Text(
        'Bạn đến sau giờ hẹn một chút',
        style: Theme.of(context).textTheme.headlineSmall?.copyWith(
              color: MelbelColors.kOnSurface,
              fontWeight: FontWeight.w600,
            ),
      ),
      const SizedBox(height: 8),
      Text(
        'Không sao cả. Bạn có thể bắt đầu khi đã sẵn sàng; lịch chưa bị tự ý thay đổi.',
        style: Theme.of(context).textTheme.bodyLarge?.copyWith(
              color: MelbelColors.kOnSurfaceVariant,
            ),
      ),
      const SizedBox(height: 20),
      _SessionCard(session: session),
      const SizedBox(height: 20),
      PrimaryCta(
        label: 'Bắt đầu phiên ngay',
        icon: Icons.play_arrow_rounded,
        onPressed: focus.startLateRun,
      ),
      TextButton(
        onPressed: () => focus.startSession(session),
        child: const Text('Quay lại chuẩn bị'),
      ),
    ]);
  }

  Widget _active(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    final segment = focus.currentSegment;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          _StatusPill(
            label: focus.dndApplied
                ? 'Đang tập trung · DND bật'
                : 'Đang tập trung',
            icon: Icons.circle,
            color: MelbelColors.kSecondary,
          ),
          IconButton(
            tooltip: 'Mở hộp suy nghĩ',
            onPressed: () => _showThoughts(context, focus),
            icon: Icon(Icons.inbox_outlined,
                color: MelbelColors.kOnSurfaceVariant),
          ),
        ],
      ),
      const SizedBox(height: 10),
      _SessionCard(session: session, compact: true),
      const SizedBox(height: 18),
      _ProgressCard(
        segment: segment,
        index: focus.segmentIndex,
        count: focus.segments.length,
        remaining: focus.remainingSeconds,
        progress: focus.progress,
      ),
      if (focus.alarmScheduled == false) ...[
        const SizedBox(height: 12),
        _InfoCard(
          icon: Icons.notifications_off_outlined,
          title: 'Chưa đặt được chuông kết thúc',
          subtitle:
              'Đồng hồ vẫn chạy. Kiểm tra quyền thông báo và báo thức chính xác trong Cài đặt Android.',
          accent: MelbelColors.kAmber,
        ),
      ],
      const SizedBox(height: 20),
      Center(
        child: Text(
          _formatTime(focus.remainingSeconds),
          style: Theme.of(context).textTheme.displaySmall?.copyWith(
                color: MelbelColors.kOnSurface,
                fontWeight: FontWeight.w500,
                fontFeatures: const [],
                letterSpacing: 2,
              ),
        ),
      ),
      const SizedBox(height: 6),
      Center(
        child: Text(
          'Cứ tập trung vào bước đang làm',
          style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                color: MelbelColors.kOnSurfaceVariant,
              ),
        ),
      ),
      const SizedBox(height: 18),
      _HoldToRecord(
        isRecording: focus.isRecording,
        onStart: focus.beginRecording,
        onStop: focus.stopAndSaveThought,
        onCancel: focus.cancelRecording,
      ),
      const SizedBox(height: 18),
      Row(
        children: [
          Expanded(
            child: TextButton.icon(
              onPressed: () => _endEarly(context, focus),
              icon: const Icon(Icons.stop_circle_outlined),
              label: const Text('Kết thúc phiên'),
            ),
          ),
          Expanded(
            child: TextButton.icon(
              onPressed: () => _showThoughts(context, focus),
              icon: const Icon(Icons.inbox_outlined),
              label: const Text('Hộp suy nghĩ'),
            ),
          ),
        ],
      ),
    ]);
  }

  Widget _break(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _StatusPill(
          label: 'Khoảng nghỉ ngắn',
          icon: Icons.coffee_outlined,
          color: MelbelColors.kSecondary),
      const SizedBox(height: 20),
      Text(
        'Nghỉ một chút nhé',
        style: Theme.of(context).textTheme.headlineSmall?.copyWith(
              color: MelbelColors.kOnSurface,
              fontWeight: FontWeight.w600,
            ),
      ),
      const SizedBox(height: 8),
      Text(
        'Phiên vẫn đang diễn ra. Quãng nghỉ không được tính vào thời gian làm việc.',
        style: Theme.of(context).textTheme.bodyLarge?.copyWith(
              color: MelbelColors.kOnSurfaceVariant,
            ),
      ),
      const SizedBox(height: 20),
      _SessionCard(session: session, compact: true),
      const SizedBox(height: 20),
      _ProgressCard(
        segment: focus.currentSegment,
        index: focus.segmentIndex,
        count: focus.segments.length,
        remaining: focus.remainingSeconds,
        progress: focus.progress,
      ),
      const SizedBox(height: 22),
      Center(
        child: Text(
          _formatTime(focus.remainingSeconds),
          style: Theme.of(context).textTheme.displaySmall?.copyWith(
                color: MelbelColors.kSecondary,
                fontWeight: FontWeight.w500,
                letterSpacing: 2,
              ),
        ),
      ),
      const SizedBox(height: 16),
      _InfoCard(
        icon: Icons.self_improvement_outlined,
        title: 'Bạn có thể đứng dậy hoặc uống nước',
        subtitle: 'Khối tiếp theo sẽ hiện khi giờ nghỉ kết thúc.',
      ),
      const SizedBox(height: 18),
      TextButton.icon(
        onPressed: () => _showThoughts(context, focus),
        icon: const Icon(Icons.inbox_outlined),
        label: const Text('Mở hộp suy nghĩ'),
      ),
    ]);
  }

  Widget _recovery(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _StatusPill(label: 'Đã khôi phục phiên', icon: Icons.restore),
      const SizedBox(height: 20),
      Text(
        'Bạn đang ở bước này',
        style: Theme.of(context).textTheme.headlineSmall?.copyWith(
              color: MelbelColors.kOnSurface,
              fontWeight: FontWeight.w600,
            ),
      ),
      const SizedBox(height: 8),
      Text(
        'Phiên đã được lưu trên thiết bị. Thời gian còn lại được tính từ mốc đồng hồ, không bị đặt lại.',
        style: Theme.of(context).textTheme.bodyLarge?.copyWith(
              color: MelbelColors.kOnSurfaceVariant,
            ),
      ),
      const SizedBox(height: 20),
      _SessionCard(session: session),
      const SizedBox(height: 12),
      _ProgressCard(
        segment: focus.currentSegment,
        index: focus.segmentIndex,
        count: focus.segments.length,
        remaining: focus.remainingSeconds,
        progress: focus.progress,
      ),
      const SizedBox(height: 20),
      PrimaryCta(
        label: 'Tiếp tục phiên',
        icon: Icons.play_arrow_rounded,
        onPressed: focus.resumeRecoveredRun,
      ),
      TextButton(
        onPressed: () => _showThoughts(context, focus),
        child: const Text('Mở hộp suy nghĩ đã cất'),
      ),
    ]);
  }

  Widget _thoughtSaved(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _StatusPill(
          label: 'Đã lưu trên thiết bị',
          icon: Icons.check_circle_outline,
          color: MelbelColors.kSecondary),
      const SizedBox(height: 20),
      _SessionCard(session: session, compact: true),
      const SizedBox(height: 14),
      _runClock(context, focus),
      const SizedBox(height: 16),
      _InfoCard(
        icon: Icons.check_circle_outline,
        title: 'Đã cất suy nghĩ',
        subtitle: 'Ghi chú được lưu trên thiết bị. Phiên vẫn tiếp tục.',
        accent: MelbelColors.kSecondary,
      ),
      const SizedBox(height: 12),
      if (focus.canUndoThought)
        _SecondaryButton(
          label: 'Hoàn tác trong 5 giây',
          onPressed: focus.undoLastThought,
        ),
      const SizedBox(height: 12),
      _ProgressCard(
        segment: focus.currentSegment,
        index: focus.segmentIndex,
        count: focus.segments.length,
        remaining: focus.remainingSeconds,
        progress: focus.progress,
      ),
    ]);
  }

  Widget _saveFailed(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _StatusPill(
          label: 'Phiên vẫn đang chạy',
          icon: Icons.circle,
          color: MelbelColors.kSecondary),
      const SizedBox(height: 20),
      _SessionCard(session: session, compact: true),
      const SizedBox(height: 16),
      _InfoCard(
        icon: Icons.mic_off_outlined,
        title: 'Chưa lưu được ghi âm lần ${focus.recordingAttempts}/3',
        subtitle:
            focus.lastError ?? 'Bạn có thể thử ghi lại. Đồng hồ vẫn chạy.',
        accent: MelbelColors.kAmber,
      ),
      const SizedBox(height: 20),
      _HoldToRecord(
        isRecording: focus.isRecording,
        onStart: focus.retryRecording,
        onStop: focus.stopAndSaveThought,
        onCancel: focus.cancelRecording,
        label: 'Giữ để thử ghi lại',
      ),
      const SizedBox(height: 12),
      TextButton(
        onPressed: focus.returnToActive,
        child: const Text('Quay lại phiên'),
      ),
    ]);
  }

  Widget _textFallback(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _StatusPill(
          label: 'Phiên vẫn đang chạy',
          icon: Icons.circle,
          color: MelbelColors.kSecondary),
      const SizedBox(height: 16),
      _SessionCard(session: session, compact: true),
      const SizedBox(height: 14),
      _runClock(context, focus),
      const SizedBox(height: 14),
      _InfoCard(
        icon: Icons.edit_note_outlined,
        title: 'Mình đã thử ghi âm 3 lần',
        subtitle: 'Bạn nhập ngắn gọn nhé. Đồng hồ không bị gián đoạn.',
        accent: MelbelColors.kSecondary,
      ),
      const SizedBox(height: 12),
      TextField(
        controller: _thoughtText,
        autofocus: true,
        maxLines: 1,
        textCapitalization: TextCapitalization.sentences,
        decoration: InputDecoration(
          hintText: 'Gõ nhanh suy nghĩ của bạn vào đây…',
          filled: true,
          fillColor: MelbelColors.kSurfaceContainerLowest,
          border: OutlineInputBorder(
            borderRadius: BorderRadius.all(Radius.circular(16)),
          ),
        ),
        onChanged: (_) => setState(() {}),
      ),
      const SizedBox(height: 14),
      PrimaryCta(
        label: 'Lưu và tiếp tục',
        icon: Icons.check,
        onPressed: _thoughtText.text.trim().isEmpty
            ? null
            : () async {
                await focus.saveTextThought(_thoughtText.text);
                _thoughtText.clear();
              },
      ),
      const SizedBox(height: 8),
      TextButton(
        onPressed: () {
          _thoughtText.clear();
          focus.returnToActive();
        },
        child: const Text('Bỏ qua'),
      ),
      Center(
        child: Text(
          'Nội dung chỉ được lưu trên thiết bị trong bản hiện tại.',
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.bodySmall?.copyWith(
                color: MelbelColors.kTextSecondary,
              ),
        ),
      ),
    ]);
  }

  Widget _ended(
    BuildContext context,
    FocusProvider focus,
    FocusSessionInfo session,
  ) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _StatusPill(label: 'Đã dừng đồng hồ', icon: Icons.pause_circle_outline),
      const SizedBox(height: 20),
      Text(
        'Phiên đã dừng',
        style: Theme.of(context).textTheme.headlineSmall?.copyWith(
              color: MelbelColors.kOnSurface,
              fontWeight: FontWeight.w600,
            ),
      ),
      const SizedBox(height: 8),
      Text(
        'Kết quả chưa được xác nhận. Session, bước và công việc chưa được đánh dấu hoàn thành.',
        style: Theme.of(context).textTheme.bodyLarge?.copyWith(
              color: MelbelColors.kOnSurfaceVariant,
            ),
      ),
      const SizedBox(height: 20),
      _InfoCard(
        icon: Icons.inbox_outlined,
        title: 'Suy nghĩ đã cất: ${focus.thoughts.length}',
        subtitle:
            'Bạn có thể mở hộp suy nghĩ để nghe lại ghi âm trên thiết bị.',
      ),
      const SizedBox(height: 20),
      PrimaryCta(
        label: 'Mở hộp suy nghĩ',
        onPressed: () => _showThoughts(context, focus),
      ),
      TextButton(
        onPressed: () => Navigator.of(context).pop(),
        child: const Text('Quay về trang trước'),
      ),
    ]);
  }

  Future<void> _leave(BuildContext context, FocusProvider focus) async {
    if (focus.isActiveRun) {
      // Leaving the screen preserves the active run for D-35 recovery.
      Navigator.of(context).pop();
      return;
    }
    Navigator.of(context).pop();
  }

  Future<void> _endEarly(BuildContext context, FocusProvider focus) async {
    await focus.endEarly();
  }

  Future<void> _showThoughts(BuildContext context, FocusProvider focus) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      backgroundColor: MelbelColors.kSurfaceContainer,
      builder: (sheetContext) => SafeArea(
        child: Padding(
          padding: EdgeInsets.fromLTRB(
            20,
            8,
            20,
            20 + MediaQuery.viewInsetsOf(sheetContext).bottom,
          ),
          child: ConstrainedBox(
            constraints: BoxConstraints(
              maxHeight: MediaQuery.sizeOf(sheetContext).height * .65,
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Hộp suy nghĩ',
                    style: Theme.of(sheetContext).textTheme.titleLarge),
                const SizedBox(height: 8),
                const Text('Ghi âm và ghi chú được lưu cục bộ trên thiết bị.'),
                const SizedBox(height: 12),
                Expanded(
                  child: focus.thoughts.isEmpty
                      ? const Center(
                          child: Text('Chưa có suy nghĩ nào được cất.'))
                      : ListView.builder(
                          itemCount: focus.thoughts.length,
                          itemBuilder: (context, index) {
                            final thought = focus.thoughts[index];
                            return Padding(
                              padding: const EdgeInsets.only(bottom: 8),
                              child: _ThoughtTile(
                                thought: thought,
                                isPlaying: _playingThoughtId == thought.id,
                                onPlay: thought.audioPath == null
                                    ? null
                                    : () => _playThought(thought),
                                onDelete: () async {
                                  await focus.deleteThought(thought);
                                  if (sheetContext.mounted) {
                                    ScaffoldMessenger.of(sheetContext)
                                        .showSnackBar(
                                      SnackBar(
                                        content: const Text('Đã xóa ghi chú'),
                                        duration: const Duration(seconds: 5),
                                        action: SnackBarAction(
                                          label: 'Hoàn tác',
                                          onPressed: () {
                                            unawaited(
                                              focus.undoDeletedThought(),
                                            );
                                          },
                                        ),
                                      ),
                                    );
                                  }
                                },
                              ),
                            );
                          },
                        ),
                ),
                const SizedBox(height: 8),
                if (focus.stage == FocusStage.ended &&
                    focus.thoughts.isNotEmpty)
                  Text(
                    'Chuyển ghi âm thành bản nháp UF1 sẽ được nối với API sau khi backend bàn giao endpoint.',
                    style: TextStyle(color: MelbelColors.kTextSecondary),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
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

class _SessionCard extends StatelessWidget {
  const _SessionCard({required this.session, this.compact = false});
  final FocusSessionInfo session;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    return MelbelCard(
      padding: EdgeInsets.all(compact ? 14 : 18),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              '${_time(session.scheduledStart)} · ${_minutes(session.workSeconds)} phút',
              style: TextStyle(color: MelbelColors.kOnSurfaceVariant),
            ),
            if (session.deadline != null)
              Text(
                'Hạn ${_dateTime(session.deadline!)}',
                style: TextStyle(fontSize: 12, color: MelbelColors.kAmber),
              ),
          ],
        ),
        const SizedBox(height: 8),
        Text(
          session.taskTitle,
          style: TextStyle(
            fontSize: compact ? 17 : 20,
            fontWeight: FontWeight.w600,
            color: MelbelColors.kOnSurface,
          ),
        ),
        const SizedBox(height: 4),
        Text(session.stepTitle,
            style: TextStyle(color: MelbelColors.kSecondary)),
        if (compact && (session.expectedOutput?.trim().isNotEmpty ?? false)) ...[
          const SizedBox(height: 8),
          Text('Đầu ra: ${session.expectedOutput}'),
        ],
        if (!compact) ...[
          const SizedBox(height: 12),
          Text('Khởi động 2 phút · ${session.microTaskText}',
              style: TextStyle(color: MelbelColors.kTextSecondary)),
        ],
      ]),
    );
  }
}

class _ProgressCard extends StatelessWidget {
  const _ProgressCard({
    required this.segment,
    required this.index,
    required this.count,
    required this.remaining,
    required this.progress,
  });

  final SessionSegmentModel? segment;
  final int index;
  final int count;
  final int remaining;
  final double progress;

  @override
  Widget build(BuildContext context) {
    final isRest = segment?.kind == 'rest';
    final theme = Theme.of(context);
    return MelbelCard(
      padding: const EdgeInsets.all(16),
      color: theme.colorScheme.surfaceContainerHigh,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Wrap(
          spacing: 12,
          runSpacing: 4,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Text('Tiến trình phiên', style: theme.textTheme.titleMedium),
            Text(
              '${isRest ? 'Nghỉ' : 'Khối làm'} ${index + 1}/$count',
              style: TextStyle(color: MelbelColors.kSecondary),
            ),
          ],
        ),
        const SizedBox(height: 12),
        Center(
          child: SizedBox.square(
            dimension: 152,
            child: Stack(
              fit: StackFit.expand,
              children: [
                CircularProgressIndicator(
                  value: 1 - progress.clamp(0.0, 1.0).toDouble(),
                  strokeWidth: 8,
                  backgroundColor: theme.colorScheme.outlineVariant,
                  color: theme.colorScheme.primary,
                  semanticsLabel: 'Thời gian còn lại',
                ),
                Center(
                  child: Text(
                    _formatTime(remaining),
                    style: theme.textTheme.headlineMedium?.copyWith(
                      fontWeight: FontWeight.w700,
                      fontFeatures: const [FontFeature.tabularFigures()],
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 8),
        Text(
          isRest
              ? 'Quãng nghỉ'
              : 'Đang làm · ${_minutes(segment?.durationSeconds ?? 0)} phút',
          style: TextStyle(color: MelbelColors.kTextSecondary),
        ),
      ]),
    );
  }
}

class _HoldToRecord extends StatelessWidget {
  const _HoldToRecord({
    required this.isRecording,
    required this.onStart,
    required this.onStop,
    required this.onCancel,
    this.label = 'Cất suy nghĩ',
  });

  final bool isRecording;
  final Future<void> Function() onStart;
  final Future<void> Function() onStop;
  final Future<void> Function() onCancel;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Semantics(
        label:
            isRecording ? 'Đang ghi âm, thả để lưu' : '$label, nhấn giữ để nói',
        button: true,
        child: GestureDetector(
          onLongPressStart: (_) async {
            unawaited(HapticFeedback.selectionClick());
            await onStart();
          },
          onLongPressEnd: (_) => onStop(),
          onLongPressCancel: onCancel,
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            AnimatedContainer(
              duration: MediaQuery.disableAnimationsOf(context)
                  ? Duration.zero
                  : const Duration(milliseconds: 180),
              width: 92,
              height: 92,
              decoration: BoxDecoration(
                color: isRecording
                    ? MelbelColors.kAmber
                    : MelbelColors.kSurfaceContainer,
                shape: BoxShape.circle,
                border: Border.all(
                  color: isRecording
                      ? MelbelColors.kAmber
                      : MelbelColors.kOutlineVariant,
                  width: 2,
                ),
              ),
              child: Icon(
                isRecording ? Icons.graphic_eq : Icons.mic_none_rounded,
                size: 34,
                color: isRecording
                    ? MelbelColors.kSurface
                    : MelbelColors.kPrimaryContainer,
              ),
            ),
            const SizedBox(height: 10),
            Text(
              isRecording ? 'Đang cất suy nghĩ' : label,
              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w500),
            ),
            const SizedBox(height: 2),
            Text(isRecording ? 'Thả tay để lưu' : 'Nhấn giữ để nói',
                style: TextStyle(color: MelbelColors.kTextSecondary)),
          ]),
        ),
      ),
    );
  }
}

class _ThoughtTile extends StatelessWidget {
  const _ThoughtTile({
    required this.thought,
    required this.isPlaying,
    required this.onPlay,
    required this.onDelete,
  });
  final ThoughtRecord thought;
  final bool isPlaying;
  final VoidCallback? onPlay;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    return MelbelCard(
      child: Row(children: [
        IconButton(
          tooltip: isPlaying ? 'Dừng phát' : 'Phát ghi âm',
          onPressed: onPlay,
          icon: Icon(isPlaying
              ? Icons.stop_circle_outlined
              : Icons.play_circle_outline),
        ),
        Expanded(
          child: Text(
            thought.text ?? 'Ghi âm · ${_time(thought.createdAt)}',
            maxLines: 3,
            overflow: TextOverflow.ellipsis,
          ),
        ),
        IconButton(
          tooltip: 'Xóa ghi chú',
          onPressed: onDelete,
          icon: const Icon(Icons.delete_outline),
        ),
      ]),
    );
  }
}

class _InfoCard extends StatelessWidget {
  _InfoCard({
    required this.icon,
    required this.title,
    required this.subtitle,
    Color? accent,
  }) : accent = accent ?? MelbelColors.kPrimaryContainer;
  final IconData icon;
  final String title;
  final String subtitle;
  final Color accent;

  @override
  Widget build(BuildContext context) => MelbelCard(
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(icon, color: accent, size: 22),
          const SizedBox(width: 12),
          Expanded(
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, style: const TextStyle(fontWeight: FontWeight.w600)),
              const SizedBox(height: 4),
              Text(subtitle,
                  style: TextStyle(color: MelbelColors.kOnSurfaceVariant)),
            ]),
          ),
        ]),
      );
}

class _StatusPill extends StatelessWidget {
  _StatusPill({
    required this.label,
    required this.icon,
    Color? color,
  }) : color = color ?? MelbelColors.kPrimaryContainer;
  final String label;
  final IconData icon;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        decoration: BoxDecoration(
          color: MelbelColors.kSurfaceContainer,
          borderRadius: BorderRadius.circular(99),
          border: Border.all(color: MelbelColors.kOutlineVariant),
        ),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          Icon(icon, size: 16, color: color),
          const SizedBox(width: 8),
          Text(label,
              style: TextStyle(color: color, fontWeight: FontWeight.w500)),
        ]),
      );
}

class _OfflineBanner extends StatelessWidget {
  const _OfflineBanner();

  @override
  Widget build(BuildContext context) => _InfoCard(
        icon: Icons.cloud_off_outlined,
        title: 'Kết nối tạm gián đoạn',
        subtitle: 'Phiên đang chạy trên thiết bị. Ghi âm vẫn được lưu cục bộ.',
        accent: MelbelColors.kAmber,
      );
}

class _SecondaryButton extends StatelessWidget {
  const _SecondaryButton({required this.label, required this.onPressed});
  final String label;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) => SizedBox(
        width: double.infinity,
        height: 52,
        child: OutlinedButton(
          onPressed: onPressed,
          style: OutlinedButton.styleFrom(
            foregroundColor: MelbelColors.kOnSurface,
            side: BorderSide(color: MelbelColors.kOutlineVariant),
            shape:
                RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
          ),
          child: Text(label),
        ),
      );
}

String _formatTime(int seconds) {
  final mins = (seconds ~/ 60).toString().padLeft(2, '0');
  final secs = (seconds % 60).toString().padLeft(2, '0');
  return '$mins:$secs';
}

String _time(DateTime date) =>
    '${date.hour.toString().padLeft(2, '0')}:${date.minute.toString().padLeft(2, '0')}';

String _dateTime(DateTime date) => '${_time(date)} ${date.day}/${date.month}';

String _weekday(DateTime date) {
  const days = [
    'Thứ 2',
    'Thứ 3',
    'Thứ 4',
    'Thứ 5',
    'Thứ 6',
    'Thứ 7',
    'Chủ nhật'
  ];
  return '${days[date.weekday - 1]}, ngày ${date.day} tháng ${date.month}';
}

int _minutes(int seconds) => (seconds / 60).ceil();
