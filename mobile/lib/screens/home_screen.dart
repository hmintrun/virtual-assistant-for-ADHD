import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../core/api_client.dart';
import '../core/responsive_layout.dart';
import '../core/theme.dart';
import '../models/focus_session_model.dart';
import '../providers/focus_provider.dart';
import '../providers/streak_provider.dart';
import '../providers/task_provider.dart';
import '../services/adaptation_api.dart';
import '../services/schedule_service.dart';
import '../widgets/melbel_ui.dart';
import '../widgets/shimmer_loading.dart';
import '../widgets/streak_widgets.dart';
import 'focus_screen.dart';
import 'candidate_review_screen.dart';
import 'task_entry_screen.dart';
import 'task_summary_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({this.api, super.key});

  final ApiClient? api;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> with WidgetsBindingObserver {
  late final _api = widget.api ?? ApiClient();
  late final _schedule = ScheduleService(_api);
  late final _adaptation = AdaptationApi(_api);
  bool _showPendingProposal = false;
  bool _showSuggestion = false;
  Future<List<Map<String, dynamic>>>? _sessions;
  Object? _confirmedTask;
  String? _melbelWhisper;
  bool _showLaterToday = false;
  FocusProvider? _observedFocus;
  bool _openingRecoveredCheckIn = false;
  String? _expiredNoticeRunId;
  bool _showingStreakCelebration = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _refreshAdaptation();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final focus = context.read<FocusProvider>();
    if (!identical(_observedFocus, focus)) {
      _observedFocus?.removeListener(_onFocusChanged);
      _observedFocus = focus..addListener(_onFocusChanged);
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _handleRecoveredCheckIn(focus);
      });
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _observedFocus?.removeListener(_onFocusChanged);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _refreshAdaptation();
      final focus = _observedFocus;
      if (focus != null) unawaited(_refreshCheckInAfterResume(focus));
    }
  }

  void _onFocusChanged() {
    final focus = _observedFocus;
    if (focus == null) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _handleRecoveredCheckIn(focus);
    });
  }

  Future<void> _refreshCheckInAfterResume(FocusProvider focus) async {
    if (focus.stage == FocusStage.ended && focus.serverRunId != null) {
      await focus.refreshRunCheckIn();
    }
    if (mounted) _handleRecoveredCheckIn(focus);
  }

  void _handleRecoveredCheckIn(FocusProvider focus) {
    final noticeRunId = focus.serverRunId ?? focus.session?.sessionId;
    if (focus.checkInExpired &&
        noticeRunId != null &&
        _expiredNoticeRunId != noticeRunId) {
      _expiredNoticeRunId = noticeRunId;
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
        content: Text(
          'Phiên check-in đã hết hạn. Lịch hiện tại vẫn được giữ nguyên.',
        ),
        duration: Duration(seconds: 5),
      ));
      focus.clearCheckInExpiredBanner();
      return;
    }
    if (!focus.awaitingCheckIn || _openingRecoveredCheckIn) return;
    _openingRecoveredCheckIn = true;
    unawaited(Navigator.of(context)
        .push(MaterialPageRoute<void>(
          builder: (_) => const FocusScreen(),
        ))
        .whenComplete(() {
          _openingRecoveredCheckIn = false;
          if (mounted) _refreshSchedule();
        }));
  }

  Future<bool> _hasCurrent(
    Future<Map<String, dynamic>?> Function() load,
  ) async {
    try {
      return await load() != null;
    } on ApiException {
      return false;
    }
  }

  Future<void> _refreshAdaptation() async {
    final current = await Future.wait([
      _hasCurrent(_adaptation.currentProposal),
      _hasCurrent(_adaptation.suggestion),
    ]);
    if (!mounted) return;
    setState(() {
      _showPendingProposal = current[0];
      _showSuggestion = current[1];
    });
  }

  Future<void> _openAdaptation(String route) async {
    await Navigator.of(context).pushNamed(route);
    if (!mounted) return;
    _refreshAdaptation();
    _refreshSchedule();
  }

  void _loadWhenTaskChanges() {
    final confirmed = context.watch<TaskProvider>().confirmedTask;
    if (_sessions == null || !identical(_confirmedTask, confirmed)) {
      _confirmedTask = confirmed;
      _sessions = _loadSessions();
    }
  }

  Future<List<Map<String, dynamic>>> _loadSessions() async {
    final now = DateTime.now();
    final streak = StreakProvider.maybeOf(context, listen: false);
    final items = await _schedule.loadSessions(
      DateUtils.dateOnly(now),
      DateTime(now.year, now.month, now.day + 30),
      includeTitles: true,
    );
    if (streak != null) {
      unawaited(streak.ingestCompletedSessions(items));
    }
    return items;
  }

  void _maybeShowStreakCelebration(StreakProvider? streak) {
    if (_showingStreakCelebration || streak == null) return;
    if (streak.pendingCelebration == null) return;
    if (!(ModalRoute.of(context)?.isCurrent ?? true)) return;
    _showingStreakCelebration = true;
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      try {
        if (!mounted) return;
        final event = streak.consumePendingCelebration();
        if (event == null) return;
        await showMelbelStreakCelebration(context, event);
      } finally {
        _showingStreakCelebration = false;
      }
    });
  }

  Future<void> _refreshAll() async {
    await _refreshAdaptation();
    if (!mounted) return;
    final next = _loadSessions();
    setState(() => _sessions = next);
    try {
      await next;
    } catch (_) {}
  }

  void _refreshSchedule() {
    if (!mounted) return;
    setState(() {
      _sessions = _loadSessions();
    });
  }

  String get _greetingText {
    final hour = DateTime.now().hour;
    if (hour < 5) return 'Chào bạn';
    if (hour < 12) return 'Chào buổi sáng';
    if (hour < 18) return 'Chào buổi chiều';
    return 'Chào buổi tối';
  }

  Future<void> _openNewTask() async {
    try {
      await context.read<TaskProvider>().startNewDraft();
      if (!mounted) return;
      await Navigator.push(
        context,
        MaterialPageRoute<void>(builder: (_) => const TaskEntryScreen()),
      );
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Chưa mở việc mới: $error')),
        );
      }
    }
  }

  Future<void> _resumeDraft() async {
    final task = context.read<TaskProvider>();
    if (!task.hasUnconfirmedDraft || task.isApiBusy) return;
    final draft = task.draft;
    final Widget screen;
    if (task.candidate != null) {
      screen = const CandidateReviewScreen();
    } else if (draft != null &&
        (draft.stage != 'collecting' || draft.clarificationIssues.isNotEmpty)) {
      screen = const TaskSummaryScreen();
    } else {
      screen = const TaskEntryScreen();
    }
    await Navigator.push(
        context, MaterialPageRoute<void>(builder: (_) => screen));
    _refreshSchedule();
  }

  Future<void> _openFocus(FocusSessionInfo focusSession) async {
    final focus = context.read<FocusProvider>();
    if (focus.isActiveRun || focus.hasUnfinishedServerRun) {
      await _reopenFocus(focus);
      return;
    }
    await focus.startSession(focusSession);
    if (!mounted) return;
    await Navigator.push(
      context,
      MaterialPageRoute<void>(builder: (_) => const FocusScreen()),
    );
    _refreshSchedule();
  }

  Future<void> _reopenFocus(FocusProvider focus) async {
    if (focus.session == null ||
        (!focus.isActiveRun &&
            !focus.hasUnfinishedServerRun &&
            !focus.missedCheckIn)) {
      return;
    }
    await focus.refreshRunCheckIn();
    if (!mounted) return;
    await Navigator.push(
      context,
      MaterialPageRoute<void>(builder: (_) => const FocusScreen()),
    );
    _refreshSchedule();
  }

  Future<void> _onTabSelected(int index) async {
    HapticFeedback.selectionClick();
    if (index == 1) {
      await Navigator.of(context).pushNamed('/calendar');
      _refreshSchedule();
    } else if (index == 2) {
      await _openNewTask();
      _refreshSchedule();
    } else if (index == 3) {
      await Navigator.of(context).pushNamed('/account');
    }
  }

  @override
  Widget build(BuildContext context) {
    _loadWhenTaskChanges();
    final theme = Theme.of(context);
    final palette = context.melbel;
    final streak = StreakProvider.maybeOf(context);
    _maybeShowStreakCelebration(streak);

    final hasDraft =
        context.select<TaskProvider, bool>((task) => task.hasUnconfirmedDraft);
    final resumeRun = context.select<FocusProvider, bool>((focus) =>
        focus.session != null &&
        (focus.isActiveRun ||
            focus.hasUnfinishedServerRun ||
            focus.missedCheckIn));
    final upcoming = _upcomingSessions(theme, palette,
        hasDraft: hasDraft, resumeRun: resumeRun);
    final now = DateTime.now();
    final compactDate =
        '${now.day.toString().padLeft(2, '0')}/${now.month.toString().padLeft(2, '0')}/${now.year}';

    return Scaffold(
      backgroundColor: theme.scaffoldBackgroundColor,
      appBar: MelbelAppBar(
        title: 'Hôm nay',
        showThemeToggle: true,
        showAccountAction: false,
        trailingAction: streak == null
            ? null
            : MelbelStreakPill(
                streak: streak,
                onTap: () => showMelbelStreakSheet(context, streak),
              ),
      ),
      bottomNavigationBar: MelbelFloatingNavBar(
        selectedIndex: 0,
        onSelected: _onTabSelected,
      ),
      body: SafeArea(
        top: false,
        child: RefreshIndicator(
          color: palette.sage,
          backgroundColor: palette.surface,
          onRefresh: _refreshAll,
          child: ListView(
            padding: MelbelLayout.pageInsets(context, top: 10, bottom: 20),
            children: [
              Consumer<FocusProvider>(
                builder: (context, focus, _) {
                  if (focus.session == null ||
                      (!focus.isActiveRun &&
                          !focus.hasUnfinishedServerRun &&
                          !focus.missedCheckIn)) {
                    return const SizedBox.shrink();
                  }
                  return Padding(
                    padding: const EdgeInsets.only(bottom: 14),
                    child: MelbelEntrance(
                      child: MelbelCard(
                        tone: MelbelCardTone.sage,
                        borderRadius: 28,
                        padding: const EdgeInsets.all(20),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            MelbelStatusPill(
                              label: focus.isActiveRun
                                  ? 'Phiên đang chạy'
                                  : 'Chờ xác nhận kết quả',
                              icon: Icons.bolt_rounded,
                              tone: MelbelCardTone.neutral,
                            ),
                            const SizedBox(height: 10),
                            Text(
                              focus.session!.stepTitle,
                              style: theme.textTheme.titleMedium?.copyWith(
                                color: palette.onSageSoft,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                            const SizedBox(height: 14),
                            PrimaryCta(
                              label: focus.hasUnfinishedServerRun ||
                                      focus.missedCheckIn
                                  ? 'Xem kết quả phiên'
                                  : focus.stage == FocusStage.recovery
                                      ? 'Khôi phục phiên'
                                      : 'Quay lại phiên',
                              icon: Icons.play_arrow_rounded,
                              onPressed: () => _reopenFocus(focus),
                            ),
                          ],
                        ),
                      ),
                    ),
                  );
                },
              ),
              Padding(
                padding: const EdgeInsets.only(bottom: 16),
                child: MelbelHeroBanner(
                  senderLabel: 'Melbel',
                  badge: compactDate,
                  title: _greetingText,
                  message:
                      _melbelWhisper ?? 'Hôm nay đi từng bước nhỏ nhé.',
                  onPetted: (whisper) =>
                      setState(() => _melbelWhisper = whisper),
                ),
              ),
              if (resumeRun)
                MelbelDisclosure(
                    title: 'Xem lịch tiếp theo',
                    icon: Icons.today_outlined,
                    children: [upcoming])
              else
                upcoming,
              if (hasDraft) ...[
                const SizedBox(height: 14),
                MelbelCard(
                  borderRadius: 24,
                  padding: EdgeInsets.zero,
                  child: ListTile(
                    leading: const Icon(Icons.edit_note_rounded),
                    title: const Text('Làm tiếp bản nháp'),
                    subtitle: Text(context.read<TaskProvider>().title,
                        maxLines: 2, overflow: TextOverflow.ellipsis),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: context.read<TaskProvider>().isApiBusy
                        ? null
                        : _resumeDraft,
                  ),
                ),
              ],
              if (_showPendingProposal) ...[
                const SizedBox(height: 14),
                _AdaptationBanner(
                  eyebrow: 'Lịch chờ duyệt',
                  message: 'Lịch hiện tại vẫn có hiệu lực.',
                  ctaLabel: 'Xem đề xuất đổi lịch',
                  icon: Icons.event_repeat_rounded,
                  onTap: () => _openAdaptation('/schedule-change'),
                ),
              ],
              if (_showSuggestion) ...[
                const SizedBox(height: 14),
                _AdaptationBanner(
                  eyebrow: 'Cài đặt phiên',
                  message: 'Có gợi ý điều chỉnh nhịp làm việc.',
                  ctaLabel: 'Xem gợi ý cài đặt',
                  icon: Icons.auto_awesome_outlined,
                  onTap: () => _openAdaptation('/settings'),
                ),
              ],
              if (!resumeRun) ...[
                const SizedBox(height: 16),
                IntrinsicHeight(
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Expanded(
                        child: MelbelActionTile(
                          icon: Icons.lightbulb_outline_rounded,
                          title: 'Hộp suy nghĩ',
                          useHoney: true,
                          onTap: () =>
                              Navigator.of(context).pushNamed('/thoughts'),
                        ),
                      ),
                      const SizedBox(width: 14),
                      Expanded(
                        child: MelbelActionTile(
                          icon: Icons.tune_rounded,
                          title: 'Nhịp tập trung',
                          useHoney: false,
                          onTap: () =>
                              Navigator.of(context).pushNamed('/settings'),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
              const SizedBox(height: 14),
            ],
          ),
        ),
      ),
    );
  }

  Widget _upcomingSessions(ThemeData theme, MelbelPalette palette,
      {required bool hasDraft, required bool resumeRun}) {
    final isDark = theme.brightness == Brightness.dark;
    return FutureBuilder<List<Map<String, dynamic>>>(
      future: _sessions,
      builder: (context, snapshot) {
        if (snapshot.hasError) {
          return MelbelCard(
            color: isDark ? palette.surface : palette.amberSoft,
            borderRadius: 28,
            padding: const EdgeInsets.all(22),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Container(
                      width: 60,
                      height: 60,
                      decoration: BoxDecoration(
                        color: isDark
                            ? const Color(0xFF4D4168)
                            : const Color(0xFFECE6D3),
                        borderRadius: BorderRadius.circular(20),
                      ),
                      child: Icon(
                        Icons.event_note_rounded,
                        size: 28,
                        color: isDark
                            ? const Color(0xFFCBBBEF)
                            : const Color(0xFF8F7C49),
                      ),
                    ),
                    const SizedBox(width: 14),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Flexible(
                                child: Text(
                                  'Chưa tải được lịch',
                                  style: theme.textTheme.titleLarge?.copyWith(
                                    fontSize: 20,
                                    fontWeight: FontWeight.w800,
                                    color: isDark
                                        ? palette.onSurface
                                        : palette.onAmberSoft,
                                  ),
                                ),
                              ),
                              const SizedBox(width: 4),
                              Transform.translate(
                                offset: const Offset(0, -6),
                                child: Transform.rotate(
                                  angle: 0.25,
                                  child: MelbelDoodleRays(
                                    size: 20,
                                    color: isDark
                                        ? palette.amber
                                        : const Color(0xFF99A48D),
                                  ),
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 6),
                          Text(
                            'Không sao đâu! Hãy thử tải lại, hoặc thêm một việc mới để bắt đầu nhé.',
                            style: theme.textTheme.bodyMedium?.copyWith(
                              fontSize: 14.5,
                              height: 1.45,
                              color: isDark
                                  ? palette.onSurfaceVariant
                                  : palette.onAmberSoft.withValues(alpha: 0.78),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 20),
                Row(
                  children: [
                    Expanded(
                      child: SecondaryCta(
                        label: 'Thử tải lại',
                        icon: Icons.refresh_rounded,
                        height: 54,
                        onPressed: _refreshSchedule,
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: PrimaryCta(
                        label: 'Thêm việc',
                        icon: Icons.add_rounded,
                        height: 54,
                        onPressed: _openNewTask,
                      ),
                    ),
                  ],
                ),
              ],
            ),
          );
        }
        if (!snapshot.hasData) {
          return const MelbelLoadingSkeleton(
            cardCount: 2,
            caption: 'Đang tải lịch trình hôm nay...',
          );
        }
        final focus = context.watch<FocusProvider>();
        final now = DateTime.now();
        final sessions = snapshot.data!
            .where((row) => row['status'] == 'scheduled')
            .map(FocusSessionInfo.fromJson)
            .where((session) =>
                session.scheduledEnd.isAfter(now) &&
                !(focus.stage == FocusStage.ended &&
                    session.sessionId == focus.session?.sessionId) &&
                !(focus.isActiveRun &&
                    session.sessionId == focus.session?.sessionId))
            .toList()
          ..sort(
            (a, b) => a.scheduledStart.compareTo(b.scheduledStart),
          );
        if (sessions.isEmpty && resumeRun)
          return const Text('Chưa có phiên nào sau phiên này.');
        if (sessions.isEmpty && hasDraft) return const SizedBox.shrink();
        if (resumeRun) {
          final next = sessions.first;
          return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(next.stepTitle, style: theme.textTheme.titleMedium),
                Text(MaterialLocalizations.of(context)
                    .formatFullDate(next.scheduledStart.toLocal())),
                TextButton(
                    onPressed: () => _onTabSelected(1),
                    child: const Text('Mở lịch')),
              ]);
        }
        final today = sessions
            .skip(1)
            .where((session) => DateUtils.isSameDay(
                  session.scheduledStart.toLocal(),
                  now,
                ))
            .toList();

        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (sessions.isNotEmpty)
              const _HomeSectionTitle(
                'Phiên tiếp theo',
                icon: Icons.spa_outlined,
              ),
            const SizedBox(height: 10),
            if (sessions.isEmpty)
              MelbelCard(
                borderRadius: 28,
                padding: const EdgeInsets.all(24),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Bạn chưa có phiên sắp tới',
                      style: theme.textTheme.headlineSmall?.copyWith(
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const SizedBox(height: 6),
                    Text(
                      'Chạm bên dưới để bắt đầu.',
                      style: theme.textTheme.bodyMedium?.copyWith(
                        color: palette.onSurfaceVariant,
                      ),
                    ),
                    const SizedBox(height: 20),
                    PrimaryCta(
                      label: 'Thêm công việc',
                      icon: Icons.add_rounded,
                      height: 58,
                      onPressed: _openNewTask,
                    ),
                  ],
                ),
              )
            else
              _sessionTile(sessions.first, isNextHero: true),
            if (today.isNotEmpty) ...[
              const SizedBox(height: 20),
              MelbelCard(
                tone: MelbelCardTone.muted,
                padding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 14,
                ),
                onTap: () => setState(
                  () => _showLaterToday = !_showLaterToday,
                ),
                child: Row(
                  children: [
                    Icon(
                      Icons.today_outlined,
                      size: 19,
                      color: palette.onSurfaceVariant,
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        'Còn lại hôm nay · ${today.length} phiên',
                        style: theme.textTheme.titleSmall?.copyWith(
                          color: palette.onSurface,
                        ),
                      ),
                    ),
                    Icon(
                      _showLaterToday
                          ? Icons.keyboard_arrow_up_rounded
                          : Icons.keyboard_arrow_down_rounded,
                      color: palette.onSurfaceVariant,
                    ),
                  ],
                ),
              ),
              if (_showLaterToday) ...[
                const SizedBox(height: 10),
                for (final session in today) _sessionTile(session),
              ],
            ],
          ],
        );
      },
    );
  }

  String _relativeStartLabel(DateTime start, DateTime now) {
    final diff = start.difference(now);
    if (diff.inMinutes <= 0) return 'Đang trong khung giờ';
    if (diff.inMinutes <= 60) return 'Còn ${diff.inMinutes} phút nữa';
    if (DateUtils.isSameDay(start, now)) {
      final hours = diff.inHours;
      return 'Khoảng $hours giờ nữa';
    }
    if (DateUtils.isSameDay(start, now.add(const Duration(days: 1)))) {
      return 'Ngày mai';
    }
    return '${start.day}/${start.month}';
  }

  Widget _sessionTile(FocusSessionInfo session, {bool isNextHero = false}) {
    final now = DateTime.now();
    final start = session.scheduledStart.toLocal();
    final end = session.scheduledEnd.toLocal();
    final localizations = MaterialLocalizations.of(context);
    String clock(DateTime date) => localizations.formatTimeOfDay(
          TimeOfDay.fromDateTime(date),
          alwaysUse24HourFormat: true,
        );

    final date = isNextHero && !DateUtils.isSameDay(start, now)
        ? '${start.day}/${start.month} · '
        : '';
    final relativeLabel = _relativeStartLabel(start, now);
    final deadline = session.deadline;
    final deadlineLabel = deadline == null
        ? 'Chưa có hạn chót'
        : 'Hạn ${context.read<TaskProvider>().formatDeadline(deadline)}';
    final durationMinutes = (session.workSeconds / 60).ceil();
    final palette = context.melbel;
    final text = Theme.of(context).textTheme;
    final showTaskTitle = session.taskTitle != session.stepTitle &&
        session.taskTitle != 'Phiên tập trung';
    final microTask = session.microTaskText.trim();

    if (isNextHero) {
      return Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: MelbelCard(
          tone: MelbelCardTone.neutral,
          borderRadius: 28,
          padding: const EdgeInsets.all(22),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Wrap(
                spacing: 8,
                runSpacing: 6,
                children: [
                  const MelbelStatusPill(
                    label: 'PHIÊN TIẾP THEO',
                    icon: Icons.bolt_rounded,
                    tone: MelbelCardTone.sage,
                  ),
                  MelbelStatusPill(
                    label: relativeLabel,
                    tone: start.difference(now).inMinutes <= 20
                        ? MelbelCardTone.amber
                        : MelbelCardTone.muted,
                  ),
                  MelbelStatusPill(
                    label: '$date${clock(start)}–${clock(end)}',
                    icon: Icons.schedule_rounded,
                    tone: MelbelCardTone.muted,
                  ),
                ],
              ),
              const SizedBox(height: 12),
              Text(
                session.stepTitle,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: text.titleLarge?.copyWith(
                  color: palette.onSurface,
                  fontWeight: FontWeight.w800,
                ),
              ),
              if (showTaskTitle) ...[
                const SizedBox(height: 4),
                Text(
                  session.taskTitle,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: text.bodySmall?.copyWith(
                    color: palette.onSurfaceVariant,
                  ),
                ),
              ],
              if (microTask.isNotEmpty) ...[
                const SizedBox(height: 12),
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.symmetric(
                    horizontal: 14,
                    vertical: 12,
                  ),
                  decoration: BoxDecoration(
                    color: palette.sageSoft,
                    borderRadius: BorderRadius.circular(18),
                  ),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(Icons.spa_outlined,
                          size: 17, color: palette.accentInk),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          'Khởi động nhẹ 2p: $microTask',
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: text.bodySmall?.copyWith(
                            color: palette.onSageSoft,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
              const SizedBox(height: 10),
              Row(
                children: [
                  Icon(
                    Icons.flag_outlined,
                    size: 15,
                    color: palette.onSurfaceVariant,
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      deadlineLabel,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: text.labelSmall?.copyWith(
                        color: palette.onSurfaceVariant,
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              PrimaryCta(
                label: durationMinutes > 0
                    ? 'Bắt đầu phiên ${durationMinutes}p'
                    : 'Bắt đầu phiên tập trung',
                icon: Icons.play_arrow_rounded,
                height: 56,
                onPressed: () => _openFocus(session),
              ),
            ],
          ),
        ),
      );
    }

    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: MelbelCard(
        padding: const EdgeInsets.all(16),
        onTap: () => _openFocus(session),
        child: Row(
          children: [
            Container(
              width: 42,
              height: 42,
              decoration: BoxDecoration(
                color: palette.sageSoft,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Icon(
                Icons.timelapse_rounded,
                size: 20,
                color: palette.accentInk,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '$date${clock(start)}–${clock(end)}',
                    style: text.labelMedium?.copyWith(
                      color: palette.accentInk,
                      fontWeight: FontWeight.w700,
                      fontFeatures: const [FontFeature.tabularFigures()],
                    ),
                  ),
                  const SizedBox(height: 3),
                  Text(
                    session.stepTitle,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: text.titleSmall?.copyWith(
                      color: palette.onSurface,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  if (showTaskTitle)
                    Text(
                      session.taskTitle,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: text.bodySmall?.copyWith(
                        color: palette.onSurfaceVariant,
                      ),
                    ),
                  const SizedBox(height: 4),
                  Text(
                    deadlineLabel,
                    style: text.labelSmall?.copyWith(
                      color: palette.onSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 6),
            Icon(
              Icons.chevron_right_rounded,
              color: palette.onSurfaceVariant,
            ),
          ],
        ),
      ),
    );
  }
}

class _AdaptationBanner extends StatelessWidget {
  const _AdaptationBanner({
    required this.eyebrow,
    required this.message,
    required this.ctaLabel,
    required this.icon,
    required this.onTap,
  });

  final String eyebrow;
  final String message;
  final String ctaLabel;
  final IconData icon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final text = Theme.of(context).textTheme;
    return MelbelCard(
      tone: MelbelCardTone.amber,
      padding: const EdgeInsets.all(14),
      onTap: onTap,
      child: Row(
        children: [
          Container(
            width: 40,
            height: 40,
            decoration: BoxDecoration(
              color: palette.amber.withValues(alpha: 0.14),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Icon(icon, size: 20, color: palette.amberInk),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Eyebrow(eyebrow, color: palette.onAmberSoft),
                const SizedBox(height: 2),
                Text(
                  message,
                  style: text.bodySmall?.copyWith(
                    color: palette.onAmberSoft,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  ctaLabel,
                  style: text.labelMedium?.copyWith(
                    color: palette.onAmberSoft,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ],
            ),
          ),
          Icon(Icons.chevron_right_rounded, color: palette.amberInk),
        ],
      ),
    );
  }
}

class _HomeSectionTitle extends StatelessWidget {
  const _HomeSectionTitle(this.label, {required this.icon});

  final String label;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final palette = context.melbel;
    return Semantics(
      header: true,
      child: Row(
        children: [
          Icon(icon, size: 19, color: palette.accentInk),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              label,
              style: theme.textTheme.titleLarge?.copyWith(
                color: palette.onSurface,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
