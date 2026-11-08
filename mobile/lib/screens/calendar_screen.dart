import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../core/api_client.dart';
import '../core/theme.dart';
import '../providers/task_provider.dart';
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
      final sessions = <Map<String, dynamic>>[];
      String? cursor;
      final visited = <String>{};
      do {
        final data = await _api.getData('/schedule', queryParameters: {
          'from': month.toUtc().toIso8601String(),
          'to': DateTime(month.year, month.month + 1).toUtc().toIso8601String(),
          'limit': 100,
          if (cursor != null) 'cursor': cursor,
        });
        if (!mounted || request != _request) return;
        sessions.addAll((data['items'] as List<dynamic>? ?? [])
            .whereType<Map<String, dynamic>>());
        cursor = data['next_cursor'] as String?;
        if (cursor != null && !visited.add(cursor))
          throw const ApiException('Máy chủ trả con trỏ lịch bị lặp.');
      } while (cursor != null);
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
    final add = await showModalBottomSheet<bool>(
        context: context,
        isScrollControlled: true,
        showDragHandle: true,
        builder: (context) => SafeArea(
            child: SizedBox(
                height: MediaQuery.sizeOf(context).height * .65,
                child: ListView(padding: const EdgeInsets.all(20), children: [
                  Text('${day.day}/${day.month}/${day.year}',
                      style: Theme.of(context).textTheme.titleLarge),
                  const SizedBox(height: 12),
                  if (sessions.isEmpty)
                    const Text('Chưa có phiên đã xác nhận trong ngày này.'),
                  for (final session in sessions)
                    Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: MelbelCard(
                            child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                              Text(
                                  '${_time(session['scheduled_start'])} – ${_time(session['scheduled_end'])}'),
                              Text(
                                  'Làm ${((session['work_seconds'] as int? ?? 0) / 60).ceil()} phút'),
                              Text(session['micro_task_text'] as String? ??
                                  'Phiên làm việc'),
                            ]))),
                  const SizedBox(height: 16),
                  PrimaryCta(
                      label: 'Thêm việc vào ngày này',
                      onPressed: () => Navigator.pop(context, true)),
                ]))));
    if (add != true || !mounted) return;
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
    setState(() => _month = DateTime(_month.year, _month.month + amount));
    _load();
  }

  @override
  Widget build(BuildContext context) {
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
          icon: const Icon(Icons.mic_none),
          label: const Text('Hỏi lịch'),
        ),
        body: ListView(padding: const EdgeInsets.all(20), children: [
          Row(children: [
            Expanded(
                child: Text('Tháng ${_month.month}, ${_month.year}',
                    style: Theme.of(context).textTheme.titleLarge)),
            IconButton(
                tooltip: 'Tháng trước',
                onPressed: () => _changeMonth(-1),
                icon: const Icon(Icons.chevron_left)),
            IconButton(
                tooltip: 'Tháng sau',
                onPressed: () => _changeMonth(1),
                icon: const Icon(Icons.chevron_right)),
          ]),
          const Text('Các phiên đã xác nhận trong ứng dụng'),
          const SizedBox(height: 12),
          const Row(
              mainAxisAlignment: MainAxisAlignment.spaceAround,
              children: [
                Text('T2'),
                Text('T3'),
                Text('T4'),
                Text('T5'),
                Text('T6'),
                Text('T7'),
                Text('CN')
              ]),
          GridView.builder(
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                  crossAxisCount: 7),
              itemCount: offset + days,
              itemBuilder: (context, index) {
                if (index < offset) return const SizedBox.shrink();
                final date =
                    DateTime(_month.year, _month.month, index - offset + 1);
                return InkWell(
                    onTap: _loading ? null : () => _openDay(date),
                    child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Text('${date.day}'),
                          if (_onDate(date).isNotEmpty)
                            Icon(Icons.circle,
                                size: 6, color: MelbelColors.kSecondary),
                        ]));
              }),
          if (_loading)
            const Center(
                child: Padding(
                    padding: EdgeInsets.all(20),
                    child: CircularProgressIndicator())),
          if (_error != null) ...[
            Text('Chưa tải được lịch: $_error'),
            SecondaryCta(label: 'Thử tải lại', onPressed: _load)
          ],
          if (!_loading && _error == null && _sessions.isEmpty)
            const Text('Tháng này chưa có phiên đã xác nhận.'),
        ]));
  }
}
