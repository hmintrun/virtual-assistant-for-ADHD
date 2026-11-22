import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../core/api_client.dart';
import '../core/responsive_layout.dart';
import '../core/theme.dart';
import '../providers/task_provider.dart';
import '../providers/focus_provider.dart';
import '../models/focus_session_model.dart';
import '../services/schedule_service.dart';
import 'focus_screen.dart';
import '../widgets/melbel_ui.dart';
import 'support_screens.dart';
import 'task_entry_screen.dart';

class CalendarScreen extends StatefulWidget {
  const CalendarScreen({super.key, this.client});
  final ApiClient? client;
  @override
  State<CalendarScreen> createState() => _CalendarScreenState();
}

class _CalendarScreenState extends State<CalendarScreen> {
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
    _load();
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
      if (mounted && request == _request)
        setState(() {
          _sessions = sessions;
          _cached = service.usedCache;
        });
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

  Future<void> _openDay(DateTime day) async {
    if (_loading) return;
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
                                          ])))),
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

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final palette = context.melbel;
    final isDark = theme.brightness == Brightness.dark;
    final today = DateTime.now();
    final offset = _month.weekday - 1;
    final days = DateTime(_month.year, _month.month + 1, 0).day;
    final activeTint =
        isDark ? const Color(0xFFC5B2F2) : palette.sage;

    return Scaffold(
      appBar: const MelbelAppBar(title: 'Lịch', showBack: true),
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
                            const Positioned(
                              top: 6,
                              child: MelbelMascot(
                                width: 112,
                                height: 102,
                                forceLightSvg: true,
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
                          SizedBox(
                            width: double.infinity,
                            child: Wrap(
                              alignment: WrapAlignment.spaceBetween,
                              crossAxisAlignment: WrapCrossAlignment.center,
                              spacing: 8,
                              runSpacing: 2,
                              children: [
                                TextButton.icon(
                                  style: TextButton.styleFrom(
                                    foregroundColor: activeTint,
                                    tapTargetSize:
                                        MaterialTapTargetSize.shrinkWrap,
                                    padding: const EdgeInsets.symmetric(
                                      horizontal: 4,
                                      vertical: 2,
                                    ),
                                    minimumSize: const Size(48, 34),
                                  ),
                                  onPressed: _loading ? null : _showToday,
                                  icon: const Icon(
                                    Icons.today_outlined,
                                    size: 16,
                                  ),
                                  label: const Text('Xem hôm nay'),
                                ),
                                Row(
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    if (!_loading &&
                                        _error == null &&
                                        _sessions.isNotEmpty)
                                      Padding(
                                        padding: const EdgeInsets.only(right: 4),
                                        child: MelbelStatusPill(
                                          label: '${_sessions.length} phiên',
                                          icon: Icons.event_note_outlined,
                                          tone: MelbelCardTone.sage,
                                        ),
                                      ),
                                    TextButton.icon(
                                      style: TextButton.styleFrom(
                                        foregroundColor:
                                            palette.onSurfaceVariant,
                                        tapTargetSize:
                                            MaterialTapTargetSize.shrinkWrap,
                                        padding: const EdgeInsets.symmetric(
                                          horizontal: 6,
                                          vertical: 2,
                                        ),
                                        minimumSize: const Size(48, 34),
                                      ),
                                      onPressed: () =>
                                          Navigator.of(context).push(
                                        MaterialPageRoute<void>(
                                          builder: (_) =>
                                              const CalendarVoiceQuestionScreen(),
                                        ),
                                      ),
                                      icon: const Icon(
                                        Icons.mic_none_rounded,
                                        size: 16,
                                      ),
                                      label: const Text('Hỏi lịch'),
                                    ),
                                  ],
                                ),
                              ],
                            ),
                          ),
                          const SizedBox(height: 4),
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
                              final openDay =
                                  _loading ? null : () => _openDay(date);
                              final isInteractiveHighlight = openDay != null &&
                                  (date == _hoveredDay || date == _focusedDay);
                              final showPaw = isToday ||
                                  hasSessions ||
                                  isInteractiveHighlight;
                              final pawColor = isToday
                                  ? colors.primary
                                  : isDark
                                      ? palette.selectedSurface
                                      : const Color(0xFFE7DDF9);

                              return Semantics(
                                button: true,
                                enabled: openDay != null,
                                onTap: openDay,
                                label: '${date.day}/${date.month}/${date.year}'
                                    '${isToday ? ', hôm nay' : ''}'
                                    '${hasSessions ? ', $count phiên' : ''}',
                                excludeSemantics: true,
                                child: Padding(
                                  padding: const EdgeInsets.all(1),
                                  child: Material(
                                    color: Colors.transparent,
                                    borderRadius: BorderRadius.circular(12),
                                    child: InkWell(
                                      borderRadius: BorderRadius.circular(12),
                                      onTap: openDay,
                                      hoverColor: Colors.transparent,
                                      focusColor: Colors.transparent,
                                      onHover: (hovering) {
                                        if (!hovering && _hoveredDay != date) {
                                          return;
                                        }
                                        setState(() =>
                                            _hoveredDay = hovering ? date : null);
                                      },
                                      onFocusChange: (focused) {
                                        if (!focused && _focusedDay != date) {
                                          return;
                                        }
                                        setState(() =>
                                            _focusedDay = focused ? date : null);
                                      },
                                      child: Stack(
                                        alignment: Alignment.center,
                                        children: [
                                          if (showPaw)
                                            CustomPaint(
                                              size: const Size(36, 36),
                                              painter: _TodayPawPainter(
                                                pawColor,
                                              ),
                                            ),
                                          Text(
                                            '${date.day}',
                                            style: theme.textTheme.titleMedium
                                                ?.copyWith(
                                              fontSize: 15.5,
                                              color: isToday
                                                  ? colors.onPrimary
                                                  : colors.onSurface,
                                              fontWeight:
                                                  isToday || hasSessions
                                                      ? FontWeight.w700
                                                      : FontWeight.w500,
                                            ),
                                          ),
                                        ],
                                      ),
                                    ),
                                  ),
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
                                  Icon(
                                    Icons.pets_rounded,
                                    size: 13,
                                    color: activeTint,
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
                                  Icon(
                                    Icons.circle,
                                    size: 8,
                                    color: activeTint,
                                  ),
                                  const SizedBox(width: 6),
                                  Text(
                                    'Ngày có phiên',
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
                    color:
                        isDark ? palette.amberSoft : const Color(0xFFFCF2D6),
                    borderColor:
                        isDark ? palette.hairline : const Color(0xFFF0DFB8),
                    borderWidth: 1.0,
                    borderRadius: 24,
                    padding: const EdgeInsets.symmetric(
                      horizontal: 14,
                      vertical: 2,
                    ),
                    child: Row(
                      children: [
                        Icon(
                          Icons.cloud_off_outlined,
                          color: palette.amber,
                          size: 20,
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Text(
                            'Chưa tải được lịch.',
                            style: theme.textTheme.bodyMedium?.copyWith(
                              fontSize: 14.5,
                              fontWeight: FontWeight.w600,
                              color: isDark
                                  ? palette.onAmberSoft
                                  : palette.onSurface,
                            ),
                          ),
                        ),
                        TextButton(
                          onPressed: _load,
                          style: TextButton.styleFrom(
                            foregroundColor: activeTint,
                            visualDensity: VisualDensity.compact,
                            padding: const EdgeInsets.symmetric(horizontal: 8),
                          ),
                          child: const Text(
                            'Thử tải lại',
                            style: TextStyle(fontWeight: FontWeight.w700),
                          ),
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
                  label: 'Thêm việc cho ngày này',
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

class _TodayPawPainter extends CustomPainter {
  const _TodayPawPainter(this.color);

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    final center = size.center(Offset.zero);
    canvas.translate(center.dx, center.dy - size.height * 0.13);
    canvas.scale(1.1);
    canvas.translate(-center.dx, -center.dy);
    // Fit the large pad behind the fixed, centered date label.
    canvas.scale(size.width / 38, size.height / 43);
    final fill = Paint()..color = color;
    final pad = Path()
      ..moveTo(19, 15)
      ..cubicTo(12, 15, 5, 23, 5, 29)
      ..cubicTo(5, 35, 11, 38, 19, 38)
      ..cubicTo(27, 38, 33, 35, 33, 29)
      ..cubicTo(33, 23, 26, 15, 19, 15)
      ..close();
    canvas.drawPath(pad, fill);
    for (final center in const [
      Offset(5, 13),
      Offset(14, 7),
      Offset(24, 7),
      Offset(33, 13),
    ]) {
      canvas.drawOval(
        Rect.fromCenter(center: center, width: 7, height: 9),
        fill,
      );
    }
    canvas.restore();
  }

  @override
  bool shouldRepaint(_TodayPawPainter oldDelegate) =>
      color != oldDelegate.color;
}
