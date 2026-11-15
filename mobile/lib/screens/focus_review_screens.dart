import '../core/api_client.dart';
import '../services/adaptation_api.dart';
import 'package:flutter/material.dart';

import '../models/focus_session_model.dart';
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

class NextFocusActionScreen extends StatelessWidget {
  const NextFocusActionScreen({required this.session, super.key});

  final FocusSessionInfo session;

  @override
  Widget build(BuildContext context) => FocusReviewPage(
        title: 'Tiếp theo',
        children: [
          const ReviewHeader('Đã ghi nhận phiên', 'Bạn muốn làm gì tiếp theo?',
              expression: CatExpression.happy),
          ReviewSessionCard(session: session),
          PrimaryCta(
              label: 'Nghỉ giải lao',
              icon: Icons.coffee_outlined,
              onPressed: () => Navigator.of(context).pushReplacement(
                  MaterialPageRoute<void>(
                      builder: (_) => const FocusRestScreen()))),
          SecondaryCta(
              label: 'Xem lịch',
              onPressed: () =>
                  Navigator.of(context).pushReplacementNamed('/calendar')),
        ],
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
      _load();
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

  Future<void> _load() => _perform(() async {
        final id = _proposal?['proposal_id'] as String? ?? widget.proposalId;
        final run = id == null ? widget.runId : null;
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
                child: Text(
                    'Bỏ một phiên · ${item['reason'] ?? 'Theo phương án này'}')),
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
          SecondaryCta(label: 'Thử lại', onPressed: _busy ? null : _load),
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
