import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../core/api_client.dart';
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
      final sessions = await ScheduleService(_api).loadSessions(
        month,
        DateTime(month.year, month.month + 1),
        includeTitles: true,
      );
      if (mounted && request == _request) setState(() => _sessions = sessions);
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
                        Text('${day.day}/${day.month}/${day.year}',
                            style: Theme.of(context).textTheme.titleLarge),
                        const SizedBox(height: 12),
                        if (sessions.isEmpty)
                          const Text(
                              'Chưa có phiên đã xác nhận trong ngày này.'),
                        for (final session in sessions)
                          Padding(
                              padding: const EdgeInsets.only(bottom: 10),
                              child: MelbelCard(
                                  child: ListTile(
                                      onTap: session['status'] == 'scheduled'
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
      if (mounted)
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.toString())));
    }
  }

  String _time(Object? raw) {
    final date = DateTime.tryParse(raw as String? ?? '')?.toLocal();
    if (date == null) return 'Chưa có giờ';
    return '${date.hour.toString().padLeft(2, '0')}:${date.minute.toString().padLeft(2, '0')}';
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
    final today = DateTime.now();
    final offset = _month.weekday - 1;
    final days = DateTime(_month.year, _month.month + 1, 0).day;

    return Scaffold(
      appBar: const MelbelAppBar(title: 'Lịch', showBack: true),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) => const CalendarVoiceQuestionScreen(),
          ),
        ),
        icon: const Icon(Icons.date_range_rounded),
        label: const Text('Hỏi lịch'),
      ),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 88),
          children: [
            TextButton.icon(
              icon: const Icon(Icons.event_available_outlined),
              label: const Text('Google Calendar'),
              onPressed: () =>
                  Navigator.of(context).pushNamed('/google-calendar'),
            ),
            MelbelCard(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          'Tháng ${_month.month}, ${_month.year}',
                          style: theme.textTheme.titleLarge,
                        ),
                      ),
                      IconButton.outlined(
                        tooltip: 'Tháng trước',
                        onPressed: () => _changeMonth(-1),
                        icon: const Icon(Icons.chevron_left_rounded),
                      ),
                      const SizedBox(width: 4),
                      IconButton.outlined(
                        tooltip: 'Tháng sau',
                        onPressed: () => _changeMonth(1),
                        icon: const Icon(Icons.chevron_right_rounded),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Divider(color: colors.outlineVariant),
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
                            padding: const EdgeInsets.symmetric(vertical: 8),
                            child: Text(
                              day,
                              textAlign: TextAlign.center,
                              style: theme.textTheme.labelMedium?.copyWith(
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
                      mainAxisExtent: 48,
                    ),
                    itemCount: offset + days,
                    itemBuilder: (context, index) {
                      if (index < offset) return const SizedBox.shrink();
                      final date = DateTime(
                          _month.year, _month.month, index - offset + 1);
                      final isToday = DateUtils.isSameDay(date, today);
                      final hasSessions = _onDate(date).isNotEmpty;
                      final openDay = _loading ? null : () => _openDay(date);
                      final showPaw = isToday ||
                          (openDay != null &&
                              (date == _hoveredDay || date == _focusedDay));
                      return Semantics(
                        button: true,
                        enabled: openDay != null,
                        onTap: openDay,
                        label: '${date.day}/${date.month}/${date.year}'
                            '${isToday ? ', hôm nay' : ''}'
                            '${hasSessions ? ', có phiên' : ''}',
                        excludeSemantics: true,
                        child: Padding(
                          padding: const EdgeInsets.all(2),
                          child: Material(
                            color: hasSessions && !showPaw
                                ? colors.surfaceContainerHigh
                                : Colors.transparent,
                            borderRadius: BorderRadius.circular(10),
                            child: InkWell(
                              borderRadius: BorderRadius.circular(10),
                              onTap: openDay,
                              hoverColor: Colors.transparent,
                              focusColor: Colors.transparent,
                              onHover: (hovering) {
                                if (!hovering && _hoveredDay != date) return;
                                setState(
                                    () => _hoveredDay = hovering ? date : null);
                              },
                              onFocusChange: (focused) {
                                if (!focused && _focusedDay != date) return;
                                setState(
                                    () => _focusedDay = focused ? date : null);
                              },
                              child: Stack(
                                alignment: Alignment.center,
                                children: [
                                  if (showPaw)
                                    CustomPaint(
                                      size: const Size(38, 38),
                                      painter: _TodayPawPainter(
                                        isToday
                                            ? colors.primary
                                            : colors.primary
                                                .withValues(alpha: .4),
                                      ),
                                    ),
                                  Text(
                                    '${date.day}',
                                    style: theme.textTheme.bodyMedium?.copyWith(
                                      color: isToday ||
                                              (showPaw &&
                                                  colors.brightness ==
                                                      Brightness.dark)
                                          ? colors.onPrimary
                                          : colors.onSurface,
                                      fontWeight: isToday || hasSessions
                                          ? FontWeight.w700
                                          : FontWeight.w500,
                                    ),
                                  ),
                                  if (hasSessions)
                                    Positioned(
                                      bottom: 5,
                                      child: Icon(
                                        Icons.circle,
                                        size: 5,
                                        color: colors.onSurface,
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
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      Icon(Icons.circle, size: 6, color: colors.onSurface),
                      const SizedBox(width: 6),
                      const Expanded(child: Text('Ngày có phiên đã xác nhận')),
                    ],
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            if (_loading) const LinearProgressIndicator(),
            if (_error != null)
              MelbelCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('Chưa tải được lịch.'),
                    TextButton(
                      onPressed: _load,
                      child: const Text('Thử tải lại'),
                    ),
                  ],
                ),
              ),
            if (!_loading && _error == null && _sessions.isEmpty)
              const MelbelCard(
                child: Row(
                  children: [
                    Icon(Icons.event_available_outlined),
                    SizedBox(width: 10),
                    Expanded(child: Text('Tháng này chưa có phiên.')),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
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
