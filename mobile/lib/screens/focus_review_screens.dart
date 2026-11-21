import '../core/api_client.dart';
import '../services/adaptation_api.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/focus_session_model.dart';
import '../providers/focus_provider.dart';
import '../services/schedule_service.dart';
import '../services/voice_service.dart';
import '../widgets/melbel_ui.dart';

class ReviewHeader extends StatelessWidget {
  const ReviewHeader(this.title, this.subtitle,
      {this.expression = CatExpression.neutral, super.key});

  final String title;
  final String subtitle;
  final CatExpression expression;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Center(child: MelbelMascot(expression: expression)),
          const SizedBox(height: 12),
          Text(title, style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 8),
          Text(subtitle),
        ],
      );
}

class ReviewSessionCard extends StatelessWidget {
  const ReviewSessionCard(
      {required this.session,
      this.actualSeconds,
      this.showSchedule = false,
      super.key});

  final FocusSessionInfo session;
  final int? actualSeconds;
  final bool showSchedule;

  @override
  Widget build(BuildContext context) {
    final deadline = session.deadline?.toLocal();
    final labels = MaterialLocalizations.of(context);
    final deadlineText = deadline == null
        ? 'Chưa có hạn chót'
        : '${labels.formatCompactDate(deadline)} · ${labels.formatTimeOfDay(TimeOfDay.fromDateTime(deadline), alwaysUse24HourFormat: true)}';
    return MelbelCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(session.taskTitle,
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 4),
          Text(session.stepTitle),
          const SizedBox(height: 12),
          if (showSchedule) ...[
            Text(
              '${labels.formatCompactDate(session.scheduledStart.toLocal())} '
              '${labels.formatTimeOfDay(TimeOfDay.fromDateTime(session.scheduledStart.toLocal()), alwaysUse24HourFormat: true)}'
              ' – '
              '${labels.formatCompactDate(session.scheduledEnd.toLocal())} '
              '${labels.formatTimeOfDay(TimeOfDay.fromDateTime(session.scheduledEnd.toLocal()), alwaysUse24HourFormat: true)}',
            ),
            const SizedBox(height: 4),
          ],
          Text('Hạn chót: $deadlineText'),
          if (actualSeconds case final actual?) ...[
            const SizedBox(height: 8),
            Text(
                'Kế hoạch: ${(session.workSeconds / 60).ceil()} phút · Thực tế: ${actual ~/ 60} phút'),
            if (session.workSeconds > 0) ...[
              const SizedBox(height: 8),
              LinearProgressIndicator(
                value:
                    (actual / session.workSeconds).clamp(0.0, 1.0).toDouble(),
                semanticsLabel: 'Thời gian đã làm so với kế hoạch',
              ),
            ],
          ],
        ],
      ),
    );
  }
}

class FocusReviewPage extends StatelessWidget {
  const FocusReviewPage(
      {required this.title, required this.children, super.key});

  final String title;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: MelbelAppBar(title: title, showBack: true),
        body: SafeArea(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 12, 20, 24),
            children: [
              for (final child in children)
                Padding(
                    padding: const EdgeInsets.only(bottom: 12), child: child),
            ],
          ),
        ),
      );
}

class UnfinishedReviewScreen extends StatefulWidget {
  const UnfinishedReviewScreen({required this.session, super.key});

  final FocusSessionInfo session;

  @override
  State<UnfinishedReviewScreen> createState() => _UnfinishedReviewScreenState();
}

class _UnfinishedReviewScreenState extends State<UnfinishedReviewScreen> {
  final _note = TextEditingController();
  final _speech = OnDeviceSpeechService();
  bool _listening = false;
  bool _starting = false;

  Future<void> _toggleVoice() async {
    if (_listening) {
      await _speech.stop();
      if (mounted) setState(() => _listening = false);
      return;
    }
    setState(() {
      _starting = true;
      _listening = true;
    });
    void done() {
      if (mounted) setState(() => _listening = false);
    }

    void voiceUnavailable(String _) {
      done();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text('Chưa nhận được giọng nói. Bạn có thể nhập ghi chú.'),
        ));
      }
    }

    try {
      final started = await _speech.start(
        onWords: (words, isFinal) {
          if (!mounted) return;
          _note.text = words;
          if (isFinal) done();
        },
        onError: voiceUnavailable,
        onDone: done,
      );
      if (!started && _listening) voiceUnavailable('');
    } catch (_) {
      voiceUnavailable('');
    } finally {
      if (mounted) setState(() => _starting = false);
    }
  }

  String? _estimate;

  @override
  void dispose() {
    _speech.dispose();
    _note.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => FocusReviewPage(
        title: 'Chưa xong',
        children: [
          const ReviewHeader(
              'Còn bao nhiêu việc?', 'Bạn có thể ước lượng hoặc để trống.'),
          ReviewSessionCard(session: widget.session),
          Wrap(
            spacing: 8,
            children: [
              for (final option in const [
                'Còn ít',
                'Còn khoảng nửa',
                'Còn nhiều'
              ])
                ChoiceChip(
                    label: Text(option),
                    selected: _estimate == option,
                    onSelected: (selected) =>
                        setState(() => _estimate = selected ? option : null)),
            ],
          ),
          TextField(
            controller: _note,
            maxLines: 3,
            decoration:
                const InputDecoration(labelText: 'Ghi chú (không bắt buộc)'),
          ),
          TextButton.icon(
            onPressed: _starting ? null : _toggleVoice,
            icon:
                Icon(_listening ? Icons.stop_circle_outlined : Icons.mic_none),
            label: Text(_listening ? 'Dừng nghe' : 'Nói nhanh ghi chú'),
          ),
          PrimaryCta(
            label: 'Ghi nhận tiến độ',
            onPressed: _listening
                ? null
                : () => Navigator.pop(
                    context,
                    [
                      if (_estimate != null) _estimate!,
                      if (_note.text.trim().isNotEmpty) _note.text.trim()
                    ].join(' · ')),
          ),
        ],
      );
}

class NextFocusActionScreen extends StatefulWidget {
  const NextFocusActionScreen({required this.session, super.key});

  final FocusSessionInfo session;

  @override
  State<NextFocusActionScreen> createState() => _NextFocusActionScreenState();
}

class _NextFocusActionScreenState extends State<NextFocusActionScreen> {
  late Future<FocusSessionInfo?> _nextSession;
  bool _starting = false;

  @override
  void initState() {
    super.initState();
    _nextSession = _loadNextSession();
  }

  Future<FocusSessionInfo?> _loadNextSession() async {
    final now = DateTime.now();
    final rows = await ScheduleService(ApiClient()).loadSessions(
      now,
      now.add(const Duration(days: 21)),
      includeTitles: true,
    );
    final candidates = rows
        .where((row) => row['status'] == 'scheduled')
        .map(FocusSessionInfo.fromJson)
        .where((session) =>
            session.sessionId != widget.session.sessionId &&
            session.scheduledEnd.isAfter(now))
        .toList()
      ..sort((a, b) => a.scheduledStart.compareTo(b.scheduledStart));
    return candidates.isEmpty ? null : candidates.first;
  }

  Future<void> _continue(FocusSessionInfo next) async {
    if (_starting) return;
    setState(() => _starting = true);
    final focus = context.read<FocusProvider>();
    await focus.startSession(next, continueImmediately: true);
    await focus.startRun();
    if (!mounted) return;
    if (focus.isActiveRun) {
      Navigator.of(context).pop();
      return;
    }
    await focus.restAfterCheckIn();
    if (!mounted) return;
    setState(() => _starting = false);
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(focus.runError ?? 'Chưa bắt đầu được phiên tiếp theo.'),
      duration: const Duration(seconds: 5),
    ));
  }

  @override
  Widget build(BuildContext context) => FutureBuilder<FocusSessionInfo?>(
        future: _nextSession,
        builder: (context, snapshot) => FocusReviewPage(
          title: 'Tiếp theo',
          children: [
            const ReviewHeader(
                'Đã ghi nhận phiên', 'Bạn muốn làm gì tiếp theo?',
                expression: CatExpression.happy),
            ReviewSessionCard(session: widget.session),
            if (snapshot.hasData && snapshot.data != null)
              PrimaryCta(
                label: 'Làm tiếp ngay phiên kế',
                icon: Icons.play_arrow_rounded,
                onPressed: _starting ? null : () => _continue(snapshot.data!),
              ),
            if (snapshot.hasError)
              TextButton(
                onPressed: () => setState(() {
                  _nextSession = _loadNextSession();
                }),
                child: const Text('Chưa tải được phiên kế · Thử lại'),
              ),
            PrimaryCta(
              label: 'Nghỉ giải lao',
              icon: Icons.coffee_outlined,
              onPressed: _starting
                  ? null
                  : () async {
                      await context.read<FocusProvider>().restAfterCheckIn();
                      if (!mounted) return;
                      Navigator.of(context).pushReplacement(
                        MaterialPageRoute<void>(
                            builder: (_) => const FocusRestScreen()),
                      );
                    },
            ),
            SecondaryCta(
              label: 'Xem lịch',
              onPressed: _starting
                  ? null
                  : () async {
                      await context.read<FocusProvider>().restAfterCheckIn();
                      if (!mounted) return;
                      Navigator.of(context).pushReplacementNamed('/calendar');
                    },
            ),
          ],
        ),
      );
}

class FocusRestScreen extends StatelessWidget {
  const FocusRestScreen({super.key});

  @override
  Widget build(BuildContext context) => FocusReviewPage(
        title: 'Nghỉ giải lao',
        children: [
          const ReviewHeader(
              'Nghỉ một chút', 'Thả lỏng mắt hoặc uống một ngụm nước.',
              expression: CatExpression.sleepy),
          PrimaryCta(
              label: 'Về trang chủ',
              onPressed: () => Navigator.of(context)
                  .pushNamedAndRemoveUntil('/home', (_) => false)),
        ],
      );
}

class ScheduleChangeReasonScreen extends StatefulWidget {
  const ScheduleChangeReasonScreen(
      {this.session, this.runId, this.initialReason, super.key});

  final String? runId;
  final String? initialReason;

  final FocusSessionInfo? session;

  @override
  State<ScheduleChangeReasonScreen> createState() =>
      _ScheduleChangeReasonScreenState();
}

class _ScheduleChangeReasonScreenState
    extends State<ScheduleChangeReasonScreen> {
  late final String? _checkInReason = switch (widget.initialReason) {
    'checkin_incomplete' => 'Chưa xong',
    'checkin_busy' => 'Bận việc khác',
    'checkin_skipped' => 'Hoãn phiên',
    _ => null,
  };
  String? _selectedReason;
  String? get _reason => _checkInReason ?? _selectedReason;

  @override
  Widget build(BuildContext context) => FocusReviewPage(
        title: 'Đổi lịch',
        children: [
          if (_checkInReason case final reason?)
            ReviewHeader('Điều chỉnh lịch', reason)
          else
            const ReviewHeader(
                'Vì sao cần đổi lịch?', 'Chọn lý do để xem bước tiếp theo.'),
          if (widget.session case final session?)
            ReviewSessionCard(session: session),
          if (_checkInReason == null)
            for (final (reason, detail, icon) in const [
              (
                'Chưa xong',
                'Cần thêm thời gian cho bước này.',
                Icons.edit_note_outlined
              ),
              (
                'Bận việc khác',
                'Đổi sang một khung giờ phù hợp hơn.',
                Icons.schedule_outlined
              ),
              (
                'Hoãn phiên',
                'Xem lịch để tiếp tục sau.',
                Icons.update_outlined
              ),
            ])
              MelbelCard(
                padding: EdgeInsets.zero,
                child: ListTile(
                  selected: _reason == reason,
                  leading: Icon(icon),
                  title: Text(reason),
                  subtitle: Text(detail),
                  trailing: Icon(_reason == reason
                      ? Icons.radio_button_checked
                      : Icons.radio_button_unchecked),
                  onTap: () => setState(() => _selectedReason = reason),
                ),
              ),
          PrimaryCta(
              label: 'Xem thay đổi',
              onPressed: _reason == null
                  ? null
                  : () => Navigator.of(context).push(
                        MaterialPageRoute<void>(
                            builder: (_) => ScheduleComparisonScreen(
                                  reason: _reason!,
                                  runId: widget.runId,
                                  trigger: widget.initialReason ??
                                      switch (_reason) {
                                        'Chưa xong' => 'checkin_incomplete',
                                        'Bận việc khác' => 'checkin_busy',
                                        _ => 'checkin_skipped',
                                      },
                                  session: widget.session,
                                )),
                      )),
          SecondaryCta(
              label: 'Để sau', onPressed: () => Navigator.pop(context)),
        ],
      );
}

class ScheduleComparisonScreen extends StatefulWidget {
  const ScheduleComparisonScreen({
    required this.reason,
    this.session,
    this.runId,
    this.trigger,
    this.proposalId,
    this.api,
    super.key,
  });

  final String reason;
  final FocusSessionInfo? session;
  final String? runId;
  final String? trigger;
  final String? proposalId;
  final AdaptationApi? api;

  @override
  State<ScheduleComparisonScreen> createState() =>
      _ScheduleComparisonScreenState();
}

class _ScheduleComparisonScreenState extends State<ScheduleComparisonScreen>
    with WidgetsBindingObserver {
  late final _api = widget.api ?? AdaptationApi(ApiClient());
  Map<String, dynamic>? _proposal;
  String? _error;
  bool _busy = false;
  bool _cached = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    if (widget.proposalId != null ||
        widget.runId != null ||
        widget.trigger == 'resume') {
      _load();
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed &&
        _proposal?['status'] == 'pending') {
      _load(forceCurrent: true);
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  Future<void> _perform(Future<void> Function() operation) async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await operation();
    } on ApiException catch (error) {
      if (error.statusCode == 409 &&
          error.code == 'REVISION_CONFLICT' &&
          _proposal != null) {
        try {
          var latest = await _api.currentProposal();
          if (latest?['status'] == 'pending' && latest?['stale'] == true) {
            latest = await _api.updateProposal('rebuild', latest!);
          }
          await _api.cacheProposal(latest);
          if (mounted) {
            setState(() {
              _proposal = latest;
              _cached = false;
              _error = latest == null
                  ? 'Lịch đã thay đổi. Phương án cũ không còn để duyệt.'
                  : 'Lịch đã thay đổi. Hãy xem lại phương án mới trước khi duyệt.';
            });
          }
          return;
        } on ApiException {
          // Keep the original conflict visible; the Retry action reloads it.
        } catch (_) {
          // Keep the original conflict visible; the Retry action reloads it.
        }
      }
      final cached = error.statusCode == null && _proposal == null
          ? await _api.cachedProposal()
          : null;
      if (mounted)
        setState(() {
          _error = error.message;
          if (cached != null &&
              widget.runId == null &&
              (widget.proposalId == null ||
                  cached['proposal_id'] == widget.proposalId)) {
            _proposal = cached;
            _cached = true;
          }
        });
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _load({bool forceCurrent = false}) => _perform(() async {
        final id = forceCurrent
            ? null
            : (_proposal?['proposal_id'] as String? ?? widget.proposalId);
        final run = id == null && !forceCurrent ? widget.runId : null;
        final current = id != null
            ? await _api.proposal(id)
            : run != null
                ? await _api.createProposal(run, widget.trigger!)
                : await _api.currentProposal();
        if (!mounted) return;
        setState(() {
          _proposal = current;
          _cached = false;
        });
        await _api.cacheProposal(current);
        if (run == null &&
            current?['status'] == 'pending' &&
            current?['stale'] == true) {
          final rebuilt = await _api.updateProposal('rebuild', current!);
          if (mounted) setState(() => _proposal = rebuilt);
          await _api.cacheProposal(rebuilt);
        }
      });

  Future<void> _act(String action) => _perform(() async {
        final updated = await _api.updateProposal(action, _proposal!);
        if (!mounted) return;
        await _api.cacheProposal(action == 'rebuild' ? updated : null);
        if (!mounted) return;
        if (action == 'rebuild') {
          setState(() => _proposal = updated);
          return;
        }
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text(
          action == 'approve'
              ? 'Đã áp dụng lịch mới.'
              : 'Đã giữ lịch hiện tại.',
        )));
        Navigator.pop(context, true);
      });

  Future<void> _openAvailabilityEditor() async {
    final taskId = _proposal?['target_task_id'] as String?;
    if (taskId == null) {
      setState(() => _error = 'Phương án chưa chỉ rõ công việc cần điều chỉnh.');
      return;
    }
    final proposalId = await Navigator.of(context).push<String>(
      MaterialPageRoute(
        builder: (_) => AvailabilityAdjustmentScreen(taskId: taskId),
      ),
    );
    if (!mounted || proposalId == null) return;
    await Navigator.of(context).push<void>(MaterialPageRoute(
      builder: (_) => ScheduleComparisonScreen(
        reason: 'Mở thêm giờ rảnh',
        proposalId: proposalId,
      ),
    ));
    if (mounted) await _load(forceCurrent: true);
  }

  void _explainDeadlineEditRequirement() {
    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
      duration: Duration(seconds: 6),
      content: Text(
        'Contract hiện yêu cầu sửa hạn chót trong Draft rồi xác nhận lại. '
        'Chưa có API sửa trực tiếp Task đã chốt.',
      ),
    ));
  }

  String _time(Object? value) {
    final date = value is String ? DateTime.tryParse(value)?.toLocal() : null;
    if (date == null) return '—';
    final labels = MaterialLocalizations.of(context);
    return '${labels.formatCompactDate(date)} ${labels.formatTimeOfDay(TimeOfDay.fromDateTime(date), alwaysUse24HourFormat: true)}';
  }

  @override
  Widget build(BuildContext context) {
    final proposal = _proposal;
    final changes = proposal?['proposed_changes'] as Map?;
    final feasibility = proposal?['feasibility'] as Map?;
    final deficit = feasibility?['deficit_seconds'] as int?;
    final pending = !_cached && proposal?['status'] == 'pending';
    final canApply = pending &&
        proposal?['stale'] != true &&
        feasibility?['confirmable'] == true &&
        _error == null;
    Iterable<Map> items(String key) =>
        (changes?[key] as List? ?? []).whereType<Map>();
    return FocusReviewPage(
      title: 'Điều chỉnh lịch',
      children: [
        ReviewHeader('Xem lại lịch', widget.reason),
        if (widget.session case final session?)
          ReviewSessionCard(session: session, showSchedule: true),
        if (_busy) const LinearProgressIndicator(),
        if (_error != null) MelbelCard(child: Text(_error!)),
        if (_cached)
          const Text(
              'Bản lưu trên máy. Kết nối lại để kiểm tra lịch trước khi duyệt.'),
        const Eyebrow('Lịch đề xuất'),
        if (proposal == null && !_busy && _error == null)
          const MelbelCard(
              child:
                  Text('Chưa có phương án mới. Lịch hiện tại vẫn được giữ.')),
        if (proposal != null) ...[
          MelbelCard(
              child: Text(proposal['explanation'] as String? ??
                  'Xem các thay đổi dưới đây.')),
          if (deficit != null && deficit > 0)
            Text('Còn thiếu ${(deficit / 60).ceil()} phút.'),
          if ((deficit ?? 0) > 0 || feasibility?['confirmable'] != true)
            Wrap(
              spacing: 8,
              runSpacing: 4,
              children: [
                ActionChip(
                  label: const Text('Mở thêm giờ rảnh'),
                  onPressed: _busy ? null : _openAvailabilityEditor,
                ),
                ActionChip(
                  label: const Text('Lùi hạn chót'),
                  onPressed: _busy ? null : _explainDeadlineEditRequirement,
                ),
              ],
            ),
          for (final item in items('steps_to_add'))
            MelbelCard(
              child: Text('Thêm bước: ${item['title']}'),
            ),
          for (final item in items('sessions_to_move'))
            MelbelCard(
                child: Text(
              'Trước: ${_time((item['before'] as Map?)?['start'] ?? item['from_start'])} – ${_time((item['before'] as Map?)?['end'] ?? item['from_end'])}'
              '\nSau: ${_time((item['after'] as Map?)?['start'] ?? item['to_start'])} – ${_time((item['after'] as Map?)?['end'] ?? item['to_end'])}',
            )),
          for (final item in items('sessions_to_add'))
            MelbelCard(
                child: Text(
                    'Thêm phiên: ${_time((item['after'] as Map?)?['start'])} – ${_time((item['after'] as Map?)?['end'])}')),
          for (final item in items('sessions_to_cancel'))
            MelbelCard(
                child: Text(item['reason'] == 'resegmented_by_settings_change'
                    ? 'Bỏ một phiên để chia lại theo cài đặt mới'
                    : 'Bỏ một phiên theo phương án này')),
        ],
        if (canApply)
          PrimaryCta(
              label: 'Áp dụng lịch mới',
              onPressed: _busy ? null : () => _act('approve')),
        if (pending && !canApply && _error == null)
          SecondaryCta(
              label: 'Tính lại phương án',
              onPressed: _busy ? null : () => _act('rebuild')),
        if (_error != null)
          SecondaryCta(
              label: 'Thử lại',
              onPressed:
                  _busy ? null : () => _load(forceCurrent: true)),
        if (widget.session == null &&
            proposal == null &&
            widget.trigger != 'resume')
          SecondaryCta(
              label: 'Mở lịch hiện tại',
              onPressed: () => Navigator.of(context).pushNamed('/calendar')),
        SecondaryCta(
            label: 'Giữ lịch hiện tại',
            onPressed: _busy
                ? null
                : pending
                    ? () => _act('reject')
                    : () => Navigator.pop(context)),
      ],
    );
  }
}

class AvailabilityAdjustmentScreen extends StatefulWidget {
  const AvailabilityAdjustmentScreen({required this.taskId, super.key});

  final String taskId;

  @override
  State<AvailabilityAdjustmentScreen> createState() =>
      _AvailabilityAdjustmentScreenState();
}

class _AvailabilityAdjustmentScreenState
    extends State<AvailabilityAdjustmentScreen> {
  final _api = ApiClient();
  DateTime _date = DateUtils.dateOnly(DateTime.now().add(const Duration(days: 1)));
  TimeOfDay _start = const TimeOfDay(hour: 18, minute: 0);
  TimeOfDay _end = const TimeOfDay(hour: 19, minute: 0);
  bool _busy = false;
  String? _error;

  String _clock(TimeOfDay time) =>
      '${time.hour.toString().padLeft(2, '0')}:${time.minute.toString().padLeft(2, '0')}';

  Future<void> _save() async {
    if (_busy) return;
    final startMinutes = _start.hour * 60 + _start.minute;
    final endMinutes = _end.hour * 60 + _end.minute;
    if (endMinutes <= startMinutes) {
      setState(() => _error = 'Giờ kết thúc phải sau giờ bắt đầu.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final task = await _api.getData(
        '/tasks/${Uri.encodeComponent(widget.taskId)}',
      );
      final availability = await _availabilityForTask(task);
      final dateKey =
          '${_date.year.toString().padLeft(4, '0')}-${_date.month.toString().padLeft(2, '0')}-${_date.day.toString().padLeft(2, '0')}';
      final weekdayRules = (availability['weekly_rules'] as List? ?? const [])
          .whereType<Map>()
          .where((rule) => rule['weekday'] == _date.weekday)
          .map((rule) => {
                'start_local': rule['start_local'],
                'end_local': rule['end_local'],
              })
          .toList();
      final overrides = (availability['date_overrides'] as List? ?? const [])
          .whereType<Map>()
          .map((item) => Map<String, dynamic>.from(item))
          .toList();
      final existingIndex = overrides.indexWhere(
        (item) => item['date'] == dateKey,
      );
      final baseIntervals = existingIndex >= 0
          ? (overrides[existingIndex]['intervals'] as List? ?? const [])
              .whereType<Map>()
              .map((interval) => {
                    'start_local': interval['start_local'],
                    'end_local': interval['end_local'],
                  })
              .toList()
          : weekdayRules;
      final addedInterval = {
        'start_local': _clock(_start),
        'end_local': _clock(_end),
      };
      if (baseIntervals.any((item) =>
          item['start_local'] == addedInterval['start_local'] &&
          item['end_local'] == addedInterval['end_local'])) {
        throw const ApiException('Khung giờ này đã có trong lịch rảnh.');
      }
      final mergedIntervals = [...baseIntervals, addedInterval]
        ..sort((a, b) => (a['start_local'] as String)
            .compareTo(b['start_local'] as String));
      final override = {'date': dateKey, 'intervals': mergedIntervals};
      if (existingIndex >= 0) {
        overrides[existingIndex] = override;
      } else {
        overrides.add(override);
      }
      final updatedAvailability = {
        'timezone': availability['timezone'],
        'weekly_rules': availability['weekly_rules'],
        'date_overrides': overrides,
      };
      final payload = {
        'target_task_id': widget.taskId,
        'expected_task_revision': task['revision'],
        'changes': {'availability': updatedAvailability},
      };
      final path = '/settings/change-requests';
      final result = await _api.postData(
        path,
        data: payload,
        idempotencyKey: ApiClient.stableIdempotencyKey(
          'task_availability_${widget.taskId}',
          payload.toString(),
        ),
      );
      final proposalId = result['pending_proposal_id'] as String?;
      if (proposalId == null || proposalId.isEmpty) {
        throw const ApiException('Chưa nhận được phương án UF6 để xem lại.');
      }
      if (mounted) Navigator.of(context).pop(proposalId);
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } catch (_) {
      if (mounted) setState(() => _error = 'Chưa cập nhật được giờ rảnh.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<Map<String, dynamic>> _availabilityForTask(
    Map<String, dynamic> task,
  ) async {
    final scoped = task['availability'];
    if (scoped is Map<String, dynamic>) return scoped;
    final reference = task['availability_ref'];
    if (reference is! Map) {
      throw const ApiException(
        'Công việc chưa có cấu hình giờ rảnh để mở rộng an toàn.',
      );
    }
    final shared = await _api.getData('/availability');
    if (shared['availability_id'] != reference['availability_id'] ||
        shared['revision'] != reference['revision']) {
      throw const ApiException(
        'Nguồn giờ rảnh đã đổi. Hãy tải lại phương án trước khi sửa.',
      );
    }
    return shared;
  }

  Future<void> _pickDate() async {
    final value = await showDatePicker(
      context: context,
      initialDate: _date,
      firstDate: DateUtils.dateOnly(DateTime.now()),
      lastDate: DateTime.now().add(const Duration(days: 365)),
    );
    if (value != null && mounted) setState(() => _date = value);
  }

  Future<void> _pickTime({required bool start}) async {
    final value = await showTimePicker(
      context: context,
      initialTime: start ? _start : _end,
    );
    if (value == null || !mounted) return;
    setState(() {
      if (start) {
        _start = value;
      } else {
        _end = value;
      }
      _error = null;
    });
  }

  @override
  Widget build(BuildContext context) => FocusReviewPage(
        title: 'Mở thêm giờ rảnh',
        children: [
          const ReviewHeader(
            'Thêm một khung giờ',
            'Khung mới chỉ áp dụng cho công việc này. Lịch cũ được giữ đến khi bạn duyệt phương án.',
          ),
          TextButton.icon(
            onPressed: _busy ? null : _pickDate,
            icon: const Icon(Icons.calendar_today_outlined),
            label: Text(MaterialLocalizations.of(context).formatMediumDate(_date)),
          ),
          Wrap(
            spacing: 8,
            children: [
              OutlinedButton(
                onPressed: _busy ? null : () => _pickTime(start: true),
                child: Text('Từ ${_clock(_start)}'),
              ),
              OutlinedButton(
                onPressed: _busy ? null : () => _pickTime(start: false),
                child: Text('Đến ${_clock(_end)}'),
              ),
            ],
          ),
          if (_error != null) Text(_error!),
          if (_busy) const LinearProgressIndicator(),
          PrimaryCta(
            label: 'Tạo phương án giờ rảnh',
            onPressed: _busy ? null : _save,
          ),
          SecondaryCta(
            label: 'Quay lại',
            onPressed: _busy ? null : () => Navigator.pop(context),
          ),
        ],
      );
}
