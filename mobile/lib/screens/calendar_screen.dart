import 'dart:async';

import 'package:flutter/foundation.dart' show ValueListenable;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import '../core/api_client.dart';
import '../core/responsive_layout.dart';
import '../core/theme.dart';
import '../providers/task_provider.dart';
import '../providers/focus_provider.dart';
import '../providers/streak_provider.dart';
import '../models/focus_session_model.dart';
import '../services/melbel_sound_service.dart';
import '../services/schedule_service.dart';
import 'focus_screen.dart';
import '../widgets/melbel_ui.dart';
import '../widgets/streak_widgets.dart';
import '../widgets/task_delete_sheet.dart';
import 'support_screens.dart';
import 'task_entry_screen.dart';

class CalendarScreen extends StatefulWidget {
  const CalendarScreen({
    super.key,
    this.client,
    this.embeddedInShell = false,
    this.refreshSignal,
  });

  final ApiClient? client;
  final bool embeddedInShell;
  final ValueListenable<int>? refreshSignal;

  @override
  State<CalendarScreen> createState() => _CalendarScreenState();
}

class _CalendarScreenState extends State<CalendarScreen> {
  final GlobalKey<MelbelMascotState> _melbelKey =
      GlobalKey<MelbelMascotState>();
  late DateTime _month;
  DateTime? _hoveredDay;
  DateTime? _focusedDay;
  late final ApiClient _api;
  List<Map<String, dynamic>> _sessions = [];
  bool _loading = true;
  bool _cached = false;
  String? _error;
  int _request = 0;

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    _month = DateTime(now.year, now.month);
    _api = widget.client ?? ApiClient();
    widget.refreshSignal?.addListener(_onShellRefresh);
    _load();
  }

  @override
  void didUpdateWidget(covariant CalendarScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.refreshSignal, widget.refreshSignal)) {
      oldWidget.refreshSignal?.removeListener(_onShellRefresh);
      widget.refreshSignal?.addListener(_onShellRefresh);
    }
  }

  void _onShellRefresh() {
    if (!mounted) return;
    unawaited(_load());
  }

  @override
  void dispose() {
    widget.refreshSignal?.removeListener(_onShellRefresh);
    super.dispose();
  }

  Future<void> _load() async {
    final request = ++_request, month = _month;
    setState(() {
      _loading = true;
      _error = null;
      _sessions = [];
    });
    try {
      final service = ScheduleService(_api);
      final sessions = await service.loadCalendarMonth(
        month,
        DateTime(month.year, month.month + 1),
      );
      if (mounted && request == _request) {
        setState(() {
          _sessions = sessions;
          _cached = service.usedCache;
        });
        final streak = StreakProvider.maybeOf(context, listen: false);
        if (streak != null) {
          unawaited(streak.ingestCompletedSessions(sessions));
        }
      }
    } catch (error) {
      if (mounted && request == _request)
        setState(() => _error = error.toString());
    } finally {
      if (mounted && request == _request) setState(() => _loading = false);
    }
  }

  List<Map<String, dynamic>> _onDate(DateTime day) => _sessions.where((s) {
        final start =
            DateTime.tryParse(s['scheduled_start'] as String? ?? '')?.toLocal();
        final end =
            DateTime.tryParse(s['scheduled_end'] as String? ?? '')?.toLocal();
        return start != null &&
            end != null &&
            start.isBefore(DateTime(day.year, day.month, day.day + 1)) &&
            end.isAfter(day);
      }).toList();

  void _petMelbel() {
    final melbelState = _melbelKey.currentState;
    if (melbelState != null) {
      melbelState.pet();
      return;
    }
    HapticFeedback.lightImpact();
    unawaited(MelbelSoundService.instance.playMeow(volume: 0.6));
  }

  Future<void> _openDay(DateTime day) async {
    if (_loading) return;
    _petMelbel();
    final streak = StreakProvider.maybeOf(context, listen: false);
    final streakStatus = streak?.dayStatus(day);
    final streakMinutes = streak?.dayMinutes(day) ?? 0;
    final sessions = _onDate(day);
    final dayLabel = MaterialLocalizations.of(context).formatFullDate(day);
    final selected = await showModalBottomSheet<Object>(
        context: context,
        isScrollControlled: true,
        showDragHandle: true,
        builder: (context) => SafeArea(
            child: SizedBox(
                height: MediaQuery.sizeOf(context).height * .65,
                child: Column(children: [
                  Expanded(
                      child: ListView(
                          padding: const EdgeInsets.all(20),
                          children: [
                        Semantics(
                            header: true,
                            child: Text(dayLabel,
                                style: Theme.of(context).textTheme.titleLarge)),
                        const SizedBox(height: 12),
                        if (streakStatus != null) ...[
                          MelbelCard(
                            tone: streakStatus == StreakDayKind.repair
                                ? MelbelCardTone.amber
                                : MelbelCardTone.sage,
                            borderRadius: 18,
                            padding: const EdgeInsets.symmetric(
                              horizontal: 14,
                              vertical: 10,
                            ),
                            child: Text(
                              switch (streakStatus) {
                                StreakDayKind.done =>
                                  'Đã giữ nhịp $streakMinutes phút cùng Melbel.',
                                StreakDayKind.freeze =>
                                  'Khiên ngủ nướng đã giữ chuỗi ngày này.',
                                StreakDayKind.repair =>
                                  'Chờ nối nhịp 24h · làm ≥ 2 phút để nối chuỗi.',
                              },
                              style: Theme.of(context).textTheme.bodySmall,
                            ),
                          ),
                          const SizedBox(height: 12),
                        ],
                        if (_error != null)
                          const Text('Chưa tải được các phiên của ngày này.')
                        else if (sessions.isEmpty)
                          const Text('Ngày này chưa có phiên đã chốt.'),
                        for (final session in sessions)
                          Padding(
                              padding: const EdgeInsets.only(bottom: 10),
                              child: MelbelCard(
                                  child: ListTile(
                                      onTap: !_cached &&
                                              session['status'] == 'scheduled'
                                          ? () => Navigator.pop(
                                              context,
                                              FocusSessionInfo.fromJson(
                                                  session))
                                          : null,
                                      title: Text(
                                          session['step_title'] as String? ??
                                              'Phiên làm việc'),
                                      subtitle: Column(
                                          crossAxisAlignment:
                                              CrossAxisAlignment.start,
                                          children: [
                                            Text(
                                                '${_time(session['scheduled_start'])} – ${_time(session['scheduled_end'])}'),
                                            Text(
                                                'Làm ${((session['work_seconds'] as int? ?? 0) / 60).ceil()} phút · Nghỉ ${((session['rest_seconds'] as int? ?? 0) / 60).ceil()} phút'),
                                            if (session['task_title'] is String)
                                              Text(session['task_title']
                                                  as String),
                                          ]),
                                      trailing: !_cached &&
                                              session['status'] == 'scheduled' &&
                                              session['task_id'] is String &&
                                              (session['task_id'] as String)
                                                  .isNotEmpty
                                          ? IconButton(
                                              tooltip: 'Xóa công việc',
                                              constraints: const BoxConstraints(
                                                minWidth: 48,
                                                minHeight: 48,
                                              ),
                                              padding: EdgeInsets.zero,
                                              onPressed: () => Navigator.pop(
                                                context,
                                                _CalendarDeleteTaskRequest(
                                                  taskId: session['task_id']
                                                      as String,
                                                  taskTitle: (session[
                                                              'task_title']
                                                          as String?) ??
                                                      (session['step_title']
                                                          as String?) ??
                                                      'Công việc',
                                                  taskRevision: (session[
                                                          'task_revision']
                                                      as num?)?.toInt(),
                                                ),
                                              ),
                                              icon: const Icon(
                                                Icons.delete_outline_rounded,
                                              ),
                                            )
                                          : null))),
                      ])),
                  Padding(
                    padding: const EdgeInsets.all(16),
                    child: PrimaryCta(
                      label: 'Thêm việc ngày này',
                      onPressed: () => Navigator.pop(context, true),
                    ),
                  ),
                ]))));
    if (!mounted) return;
    if (selected is _CalendarDeleteTaskRequest) {
      final deleted = await confirmAndDeleteTask(
        context,
        taskId: selected.taskId,
        taskTitle: selected.taskTitle,
        taskRevision: selected.taskRevision,
        api: _api,
      );
      if (deleted && mounted) await _load();
      return;
    }
    if (selected is FocusSessionInfo) {
      final focus = context.read<FocusProvider>();
      if (!focus.isActiveRun && !focus.hasUnfinishedServerRun) {
        await focus.startSession(selected);
      }
      await focus.refreshRunCheckIn();
      if (!mounted) return;
      await Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) => const FocusScreen(),
      ));
      if (mounted) await _load();
      return;
    }
    if (selected != true) return;
    await _addTaskForDay(day);
  }

  Future<void> _addTaskForDay(DateTime day) async {
    try {
      final task = context.read<TaskProvider>();
      await task.startNewDraft();
      if (!mounted) return;
      final end = DateTime(day.year, day.month, day.day, 18);
      if (end.isAfter(DateTime.now())) task.setDeadline(end);
      await Navigator.push(context,
          MaterialPageRoute<void>(builder: (_) => const TaskEntryScreen()));
      if (mounted) await _load();
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.toString())));
      }
    }
  }

  String _time(Object? raw) {
    final date = DateTime.tryParse(raw as String? ?? '')?.toLocal();
    if (date == null) return 'Chưa có giờ';
    return '${date.hour.toString().padLeft(2, '0')}:${date.minute.toString().padLeft(2, '0')}';
  }

  Future<void> _showToday() async {
    final today = DateUtils.dateOnly(DateTime.now());
    if (!DateUtils.isSameMonth(_month, today)) {
      setState(() {
        _month = DateTime(today.year, today.month);
        _hoveredDay = null;
        _focusedDay = null;
      });
      await _load();
      if (!mounted || !DateUtils.isSameMonth(_month, today)) return;
    }
    await _openDay(today);
  }

  void _changeMonth(int amount) {
    setState(() {
      _month = DateTime(_month.year, _month.month + amount);
      _hoveredDay = null;
      _focusedDay = null;
    });
    _load();
  }

  Widget _quickHeaderPill({
    required IconData icon,
    required String label,
    required String tooltip,
    required Color bg,
    required Color fg,
    required Color borderColor,
    required VoidCallback? onPressed,
  }) =>
      Tooltip(
        message: tooltip,
        child: Material(
          color: bg,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
            side: BorderSide(color: borderColor),
          ),
          clipBehavior: Clip.antiAlias,
          child: InkWell(
            onTap: onPressed,
            borderRadius: BorderRadius.circular(16),
            child: ConstrainedBox(
              constraints: const BoxConstraints(minHeight: 48),
              child: Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(icon, size: 19, color: fg),
                    const SizedBox(width: 6),
                    Text(
                      label,
                      style: Theme.of(context).textTheme.labelMedium?.copyWith(
                            color: fg,
                            fontWeight: FontWeight.w700,
                          ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final palette = context.melbel;
    final isDark = theme.brightness == Brightness.dark;
    final streak = StreakProvider.maybeOf(context);
    final today = DateTime.now();
    final offset = _month.weekday - 1;
    final days = DateTime(_month.year, _month.month + 1, 0).day;
    final activeTint = palette.accentInk;

    return Scaffold(
      appBar: MelbelAppBar(title: 'Lịch', showBack: !widget.embeddedInShell),
      body: SafeArea(
        child: ListView(
          padding: MelbelLayout.pageInsets(context, top: 4, bottom: 16),
          children: [
            MelbelEntrance(
              child: Stack(
                clipBehavior: Clip.none,
                children: [
                  Positioned(
                    top: 0,
                    left: 0,
                    right: 0,
                    child: Center(
                      child: SizedBox(
                        width: 240,
                        height: 82,
                        child: Stack(
                          clipBehavior: Clip.none,
                          alignment: Alignment.topCenter,
                          children: [
                            Positioned.fill(
                              child: RepaintBoundary(
                                child: CustomPaint(
                                  painter: _CalendarPeekerBackdropPainter(
                                    isDark: isDark,
                                  ),
                                ),
                              ),
                            ),
                            Positioned(
                              top: 6,
                              child: MelbelMascot(
                                key: _melbelKey,
                                width: 112,
                                height: 102,
                                showBackdrop: false,
                              ),
                            ),
                            Positioned(
                              top: 6,
                              right: 36,
                              child: IgnorePointer(
                                child: Transform.rotate(
                                  angle: 0.35,
                                  child: MelbelDoodleRays(
                                    size: 19,
                                    color: isDark ? palette.amber : palette.sage,
                                  ),
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                  Padding(
                    padding: const EdgeInsets.only(top: 62),
                    child: MelbelCard(
                      borderRadius: 28,
                      padding: const EdgeInsets.fromLTRB(16, 18, 16, 14),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            children: [
                              Expanded(
                                child: Text(
                                  'Tháng ${_month.month}, ${_month.year}',
                                  style: theme.textTheme.headlineSmall?.copyWith(
                                    fontSize: 21,
                                    fontWeight: FontWeight.w800,
                                  ),
                                ),
                              ),
                              IconButton(
                                tooltip: 'Tháng trước',
                                constraints: const BoxConstraints(
                                  minWidth: 48,
                                  minHeight: 48,
                                ),
                                padding: EdgeInsets.zero,
                                onPressed: () => _changeMonth(-1),
                                icon: Container(
                                  width: 40,
                                  height: 40,
                                  decoration: BoxDecoration(
                                    color: palette.mutedSurface,
                                    borderRadius: BorderRadius.circular(14),
                                  ),
                                  child: Icon(
                                    Icons.chevron_left_rounded,
                                    color: colors.onSurface,
                                  ),
                                ),
                              ),
                              const SizedBox(width: 4),
                              IconButton(
                                tooltip: 'Tháng sau',
                                constraints: const BoxConstraints(
                                  minWidth: 48,
                                  minHeight: 48,
                                ),
                                padding: EdgeInsets.zero,
                                onPressed: () => _changeMonth(1),
                                icon: Container(
                                  width: 40,
                                  height: 40,
                                  decoration: BoxDecoration(
                                    color: palette.mutedSurface,
                                    borderRadius: BorderRadius.circular(14),
                                  ),
                                  child: Icon(
                                    Icons.chevron_right_rounded,
                                    color: colors.onSurface,
                                  ),
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 8),
                          SizedBox(
                            width: double.infinity,
                            child: Wrap(
                              alignment: WrapAlignment.spaceBetween,
                              crossAxisAlignment: WrapCrossAlignment.center,
                              spacing: 8,
                              runSpacing: 6,
                              children: [
                                _quickHeaderPill(
                                  icon: Icons.today_rounded,
                                  label: 'Hôm nay',
                                  tooltip: 'Xem hôm nay',
                                  bg: palette.sageSoft,
                                  fg: palette.accentInk,
                                  borderColor:
                                      palette.sage.withValues(alpha: 0.25),
                                  onPressed: _loading ? null : _showToday,
                                ),
                                Wrap(
                                  crossAxisAlignment: WrapCrossAlignment.center,
                                  spacing: 6,
                                  runSpacing: 6,
                                  children: [
                                    if (streak != null)
                                      InkWell(
                                        borderRadius:
                                            BorderRadius.circular(999),
                                        onTap: () => showMelbelStreakSheet(
                                            context, streak),
                                        child: MelbelStatusPill(
                                          label: streak.repairPending
                                              ? 'Nối ${streak.effectiveDisplayStreak} ngày'
                                              : !streak.isUnlocked
                                                  ? '${streak.effectiveDisplayStreak}/${StreakProvider.unlockDays}'
                                                  : '${streak.effectiveDisplayStreak} ngày',
                                          icon: streak.isUnlocked
                                              ? Icons
                                                  .local_fire_department_outlined
                                              : Icons.eco_outlined,
                                          tone: streak.repairPending ||
                                                  !streak.isUnlocked
                                              ? MelbelCardTone.amber
                                              : MelbelCardTone.sage,
                                        ),
                                      ),
                                    if (!_loading &&
                                        _error == null &&
                                        _sessions.isNotEmpty)
                                      MelbelStatusPill(
                                        label: '${_sessions.length} phiên',
                                        icon: Icons.event_note_outlined,
                                        tone: MelbelCardTone.sage,
                                      ),
                                    _quickHeaderPill(
                                      icon: Icons.mic_none_rounded,
                                      label: 'Hỏi',
                                      tooltip: 'Hỏi lịch',
                                      bg: palette.amberSoft,
                                      fg: palette.amberInk,
                                      borderColor:
                                          palette.amber.withValues(alpha: 0.28),
                                      onPressed: () =>
                                          Navigator.of(context).push(
                                        MaterialPageRoute<void>(
                                          builder: (_) =>
                                              const CalendarVoiceQuestionScreen(),
                                        ),
                                      ),
                                    ),
                                  ],
                                ),
                              ],
                            ),
                          ),
                          const SizedBox(height: 8),
                          Row(
                            children: [
                              for (final day in [
                                'T2',
                                'T3',
                                'T4',
                                'T5',
                                'T6',
                                'T7',
                                'CN'
                              ])
                                Expanded(
                                  child: Padding(
                                    padding: const EdgeInsets.symmetric(
                                      vertical: 4,
                                    ),
                                    child: Text(
                                      day,
                                      textAlign: TextAlign.center,
                                      style:
                                          theme.textTheme.labelMedium?.copyWith(
                                        color: colors.onSurfaceVariant,
                                        fontWeight: FontWeight.w700,
                                      ),
                                    ),
                                  ),
                                ),
                            ],
                          ),
                          GridView.builder(
                            shrinkWrap: true,
                            physics: const NeverScrollableScrollPhysics(),
                            gridDelegate:
                                const SliverGridDelegateWithFixedCrossAxisCount(
                              crossAxisCount: 7,
                              mainAxisExtent: 39,
                            ),
                            itemCount: offset + days,
                            itemBuilder: (context, index) {
                              if (index < offset) {
                                return const SizedBox.shrink();
                              }
                              final date = DateTime(
                                _month.year,
                                _month.month,
                                index - offset + 1,
                              );
                              final isToday = DateUtils.isSameDay(date, today);
                              final count = _onDate(date).length;
                              final hasSessions = count > 0;
                              final streakKind = streak?.dayStatus(date);
                              final isDoneDay =
                                  streakKind == StreakDayKind.done;
                              final isFreezeDay =
                                  streakKind == StreakDayKind.freeze;
                              final isRepairDay =
                                  streakKind == StreakDayKind.repair;
                              final inRibbon =
                                  streak?.isInUnlockedRibbon(date) ?? false;
                              final col = index % 7;
                              final ribbonPrev = inRibbon &&
                                  col > 0 &&
                                  (streak?.hasRibbonPrev(date) ?? false);
                              final ribbonNext = inRibbon &&
                                  col < 6 &&
                                  (streak?.hasRibbonNext(date) ?? false);
                              final openDay =
                                  _loading ? null : () => _openDay(date);
                              final isInteractiveHighlight = openDay != null &&
                                  (date == _hoveredDay || date == _focusedDay);
                              final showPaw = isToday ||
                                  isDoneDay ||
                                  isFreezeDay ||
                                  isRepairDay ||
                                  hasSessions ||
                                  isInteractiveHighlight;
                              final Color pawFill;
                              Color? pawStroke;
                              if (isDoneDay) {
                                pawFill = colors.primary;
                              } else if (isFreezeDay) {
                                pawFill = palette.surface;
                                pawStroke = colors.primary;
                              } else if (isRepairDay) {
                                pawFill = palette.amberSoft;
                                pawStroke = palette.amber;
                              } else if (isToday) {
                                if (streak != null) {
                                  pawFill = palette.amberSoft;
                                  pawStroke = palette.amber;
                                } else {
                                  pawFill = colors.primary;
                                }
                              } else {
                                pawFill = isDark
                                    ? palette.selectedSurface
                                    : const Color(0xFFE7DDF9);
                              }
                              final Color textColor = isDoneDay ||
                                      (isToday && streak == null)
                                  ? colors.onPrimary
                                  : isRepairDay || (isToday && streak != null)
                                      ? palette.amberInk
                                      : isFreezeDay
                                          ? activeTint
                                          : colors.onSurface;

                              return Semantics(
                                button: true,
                                enabled: openDay != null,
                                onTap: openDay,
                                label: '${date.day}/${date.month}/${date.year}'
                                    '${isToday ? ', hôm nay' : ''}'
                                    '${isDoneDay ? ', giữ chuỗi' : ''}'
                                    '${isFreezeDay ? ', khiên ngủ nướng' : ''}'
                                    '${hasSessions ? ', $count phiên' : ''}',
                                excludeSemantics: true,
                                child: Stack(
                                  alignment: Alignment.center,
                                  children: [
                                    if (inRibbon && (ribbonPrev || ribbonNext))
                                      Positioned(
                                        top: 11,
                                        bottom: 4,
                                        left: ribbonPrev ? 0 : 4,
                                        right: ribbonNext ? 0 : 4,
                                        child: DecoratedBox(
                                          decoration: BoxDecoration(
                                            color: palette.sageSoft,
                                            borderRadius:
                                                BorderRadius.horizontal(
                                              left: ribbonPrev
                                                  ? Radius.zero
                                                  : const Radius.circular(999),
                                              right: ribbonNext
                                                  ? Radius.zero
                                                  : const Radius.circular(999),
                                            ),
                                          ),
                                        ),
                                      ),
                                    Padding(
                                      padding: const EdgeInsets.all(1),
                                      child: Material(
                                        color: Colors.transparent,
                                        borderRadius: BorderRadius.circular(12),
                                        child: InkWell(
                                          borderRadius:
                                              BorderRadius.circular(12),
                                          onTap: openDay,
                                          hoverColor: Colors.transparent,
                                          focusColor: Colors.transparent,
                                          onHover: (hovering) {
                                            if (!hovering &&
                                                _hoveredDay != date) {
                                              return;
                                            }
                                            setState(() => _hoveredDay =
                                                hovering ? date : null);
                                          },
                                          onFocusChange: (focused) {
                                            if (!focused &&
                                                _focusedDay != date) {
                                              return;
                                            }
                                            setState(() => _focusedDay =
                                                focused ? date : null);
                                          },
                                          child: Stack(
                                            alignment: Alignment.center,
                                            children: [
                                              if (showPaw)
                                                CustomPaint(
                                                  size: const Size(36, 36),
                                                  painter: MelbelPawPainter(
                                                    fillColor: pawFill,
                                                    strokeColor: pawStroke,
                                                    strokeWidth:
                                                        pawStroke != null
                                                            ? 2.0
                                                            : 0,
                                                    dashed: isRepairDay,
                                                  ),
                                                ),
                                              Text(
                                                '${date.day}',
                                                style: theme
                                                    .textTheme.titleMedium
                                                    ?.copyWith(
                                                  fontSize: 15.5,
                                                  color: textColor,
                                                  fontWeight: isToday ||
                                                          isDoneDay ||
                                                          isFreezeDay ||
                                                          hasSessions
                                                      ? FontWeight.w700
                                                      : FontWeight.w500,
                                                ),
                                              ),
                                            ],
                                          ),
                                        ),
                                      ),
                                    ),
                                  ],
                                ),
                              );
                            },
                          ),
                          const SizedBox(height: 6),
                          Wrap(
                            spacing: 16,
                            runSpacing: 4,
                            children: [
                              Row(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  SizedBox(
                                    width: 14,
                                    height: 16,
                                    child: CustomPaint(
                                      painter: MelbelPawPainter(
                                        fillColor: streak != null
                                            ? palette.amberSoft
                                            : colors.primary,
                                        strokeColor:
                                            streak != null ? palette.amber : null,
                                        strokeWidth: streak != null ? 2.4 : 0,
                                      ),
                                    ),
                                  ),
                                  const SizedBox(width: 6),
                                  Text(
                                    'Hôm nay',
                                    style:
                                        theme.textTheme.labelSmall?.copyWith(
                                      color: colors.onSurfaceVariant,
                                    ),
                                  ),
                                ],
                              ),
                              Row(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  SizedBox(
                                    width: 14,
                                    height: 16,
                                    child: CustomPaint(
                                      painter: MelbelPawPainter(
                                        fillColor: colors.primary,
                                      ),
                                    ),
                                  ),
                                  const SizedBox(width: 6),
                                  Text(
                                    streak != null
                                        ? 'Đã giữ nhịp'
                                        : 'Ngày có phiên',
                                    style:
                                        theme.textTheme.labelSmall?.copyWith(
                                      color: colors.onSurfaceVariant,
                                    ),
                                  ),
                                ],
                              ),
                              if (streak != null)
                                Row(
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    SizedBox(
                                      width: 14,
                                      height: 16,
                                      child: CustomPaint(
                                        painter: MelbelPawPainter(
                                          fillColor: palette.surface,
                                          strokeColor: colors.primary,
                                          strokeWidth: 2.4,
                                        ),
                                      ),
                                    ),
                                    const SizedBox(width: 6),
                                    Text(
                                      'Khiên ngủ nướng',
                                      style:
                                          theme.textTheme.labelSmall?.copyWith(
                                        color: colors.onSurfaceVariant,
                                      ),
                                    ),
                                  ],
                                ),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ),
                  Positioned(
                    top: 47,
                    left: 0,
                    right: 0,
                    child: IgnorePointer(
                      child: Center(
                        child: RepaintBoundary(
                          child: CustomPaint(
                            size: const Size(150, 28),
                            painter: _CalendarPeekingPawsPainter(
                              isDark: isDark,
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
            if (_loading) ...[
              const SizedBox(height: 8),
              const ClipRRect(
                borderRadius: BorderRadius.all(Radius.circular(999)),
                child: LinearProgressIndicator(minHeight: 4),
              ),
            ],
            if (_cached && !_loading && _error == null) ...[
              const SizedBox(height: 6),
              Center(
                child: TextButton(
                  onPressed: _load,
                  child: const Text('Lịch đã lưu · Tải lại'),
                ),
              ),
            ],
            if (_error != null) ...[
              const SizedBox(height: 10),
              Stack(
                clipBehavior: Clip.none,
                children: [
                  MelbelCard(
                    tone: MelbelCardTone.neutral,
                    borderRadius: 24,
                    padding: const EdgeInsets.symmetric(
                      horizontal: 14,
                      vertical: 8,
                    ),
                    child: Row(
                      children: [
                        Icon(
                          Icons.cloud_off_outlined,
                          color: palette.amberInk,
                          size: 20,
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Text(
                            'Chưa tải được lịch.',
                            style: theme.textTheme.bodyMedium?.copyWith(
                              fontSize: 14.5,
                              fontWeight: FontWeight.w600,
                              color: palette.onSurface,
                            ),
                          ),
                        ),
                        const SizedBox(width: 8),
                        MelbelIconObjectButton(
                          icon: Icons.refresh_rounded,
                          tooltip: 'Thử tải lại',
                          size: 48,
                          tone: MelbelCardTone.neutral,
                          onPressed: _load,
                        ),
                      ],
                    ),
                  ),
                  Positioned(
                    top: -9,
                    left: 6,
                    child: IgnorePointer(
                      child: Transform.rotate(
                        angle: -0.55,
                        child: MelbelDoodleRays(
                          size: 16,
                          color: palette.amber,
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ],
            if (!_loading && _error == null && _sessions.isEmpty) ...[
              const SizedBox(height: 10),
              MelbelCard(
                tone: MelbelCardTone.muted,
                borderRadius: 24,
                padding: const EdgeInsets.all(12),
                child: Row(
                  children: [
                    Icon(
                      Icons.event_available_outlined,
                      color: activeTint,
                      size: 20,
                    ),
                    const SizedBox(width: 10),
                    const Expanded(child: Text('Tháng này chưa có phiên.')),
                  ],
                ),
              ),
            ],
            const SizedBox(height: 10),
            IntrinsicHeight(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Expanded(
                    child: MelbelActionTile(
                      icon: Icons.event_available_outlined,
                      title: 'Google Calendar',
                      useHoney: false,
                      compact: true,
                      onTap: () =>
                          Navigator.of(context).pushNamed('/google-calendar'),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: MelbelActionTile(
                      icon: Icons.swap_horiz_rounded,
                      title: 'Đổi lịch',
                      useHoney: true,
                      compact: true,
                      onTap: () async {
                        final changed = await Navigator.of(context)
                            .pushNamed('/schedule-change');
                        if (mounted && changed == true) await _load();
                      },
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 10),
            Stack(
              clipBehavior: Clip.none,
              children: [
                PrimaryCta(
                  label: 'Thêm việc',
                  icon: Icons.add_rounded,
                  height: 52,
                  onPressed: () =>
                      _addTaskForDay(DateUtils.dateOnly(DateTime.now())),
                ),
                Positioned(
                  top: 8,
                  right: 20,
                  child: IgnorePointer(
                    child: Transform.rotate(
                      angle: 0.45,
                      child: MelbelDoodleRays(
                        size: 18,
                        color: Colors.white.withValues(alpha: 0.85),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _CalendarPeekerBackdropPainter extends CustomPainter {
  const _CalendarPeekerBackdropPainter({required this.isDark});

  final bool isDark;

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width;

    // 1. Warm moon halo behind Melbel's ears
    final moonPaint = Paint()
      ..color = isDark ? const Color(0xFFA797C4) : const Color(0xFFFFF7E0);
    canvas.drawCircle(Offset(w * 0.50, 50), 44, moonPaint);

    // 2. Soft pastel lavender cloud puffs flanking left and right along card rim
    final backCloud = Paint()
      ..color = isDark ? const Color(0xFF3D3252) : const Color(0xFFE4D8F8);
    final frontCloud = Paint()
      ..color = isDark ? const Color(0xFF4B3E66) : const Color(0xFFD6C7F4);

    canvas.drawCircle(Offset(w * 0.16, 66), 15, backCloud);
    canvas.drawCircle(Offset(w * 0.27, 58), 18, frontCloud);
    canvas.drawCircle(Offset(w * 0.84, 66), 15, backCloud);
    canvas.drawCircle(Offset(w * 0.73, 58), 18, frontCloud);

    // 3. Two curved sage leaves peeking cleanly from the left cloud
    final darkLeaf = Paint()
      ..color = isDark ? const Color(0xFF7D9368) : const Color(0xFF90A678);
    final lightLeaf = Paint()
      ..color = isDark ? const Color(0xFF9BAF85) : const Color(0xFFB2C19A);
    final veinPaint = Paint()
      ..color = isDark ? const Color(0xFF667B54) : const Color(0xFF778D60)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.2
      ..strokeCap = StrokeCap.round;

    void drawLeaf(
      Offset base,
      double angle,
      double len,
      double halfWidth,
      Paint fill,
    ) {
      canvas.save();
      canvas.translate(base.dx, base.dy);
      canvas.rotate(angle);
      final path = Path()
        ..moveTo(0, 0)
        ..quadraticBezierTo(-halfWidth, -len * 0.48, 0, -len)
        ..quadraticBezierTo(halfWidth, -len * 0.48, 0, 0)
        ..close();
      canvas.drawPath(path, fill);
      canvas.drawLine(Offset(0, -len * 0.08), Offset(0, -len * 0.78), veinPaint);
      canvas.restore();
    }

    drawLeaf(Offset(w * 0.34, 60), -0.45, 32, 7.8, darkLeaf);
    drawLeaf(Offset(w * 0.30, 63), -0.92, 25, 6.4, lightLeaf);
  }

  @override
  bool shouldRepaint(_CalendarPeekerBackdropPainter oldDelegate) =>
      oldDelegate.isDark != isDark;
}

class _CalendarPeekingPawsPainter extends CustomPainter {
  const _CalendarPeekingPawsPainter({required this.isDark});

  final bool isDark;

  @override
  void paint(Canvas canvas, Size size) {
    final shadowPaint = Paint()
      ..color = Colors.black.withValues(alpha: isDark ? 0.35 : 0.14);
    final pawFill = Paint()..color = const Color(0xFF18181B);
    final creasePaint = Paint()
      ..color = const Color(0xFF3E3E46)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.4
      ..strokeCap = StrokeCap.round;

    void drawPaw(double cx) {
      // Soft contact shadow onto the top border of the calendar card
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromCenter(center: Offset(cx, 16.5), width: 28, height: 22),
          const Radius.circular(11),
        ),
        shadowPaint,
      );

      final pawRect = Rect.fromCenter(
        center: Offset(cx, 13.5),
        width: 26,
        height: 21,
      );
      canvas.drawRRect(
        RRect.fromRectAndRadius(pawRect, const Radius.circular(10.5)),
        pawFill,
      );

      // Subtle toe dividers for chubby paw feel
      canvas.drawLine(
        Offset(cx - 4.5, 19.5),
        Offset(cx - 4.5, 22.8),
        creasePaint,
      );
      canvas.drawLine(
        Offset(cx + 4.5, 19.5),
        Offset(cx + 4.5, 22.8),
        creasePaint,
      );
    }

    drawPaw(35);
    drawPaw(size.width - 35);
  }

  @override
  bool shouldRepaint(_CalendarPeekingPawsPainter oldDelegate) =>
      oldDelegate.isDark != isDark;
}

class _CalendarDeleteTaskRequest {
  const _CalendarDeleteTaskRequest({
    required this.taskId,
    required this.taskTitle,
    this.taskRevision,
  });

  final String taskId;
  final String taskTitle;
  final int? taskRevision;
}
