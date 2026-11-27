import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/api_client.dart';
import '../core/responsive_layout.dart';
import '../core/theme.dart';
import '../models/focus_session_model.dart';
import '../providers/focus_provider.dart';
import '../providers/streak_provider.dart';
import '../services/adaptation_api.dart';
import '../services/pre_session_reminder_sync.dart';
import '../services/schedule_service.dart';
import '../services/voice_service.dart';
import '../widgets/melbel_ui.dart';
import '../widgets/streak_widgets.dart';
import 'main_shell_screen.dart';

class ReviewHeader extends StatelessWidget {
  const ReviewHeader(
    this.title,
    this.subtitle, {
    this.expression = CatExpression.neutral,
    super.key,
  });

  final String title;
  final String? subtitle;
  final CatExpression expression;

  @override
  Widget build(BuildContext context) {
    final cleanSubtitle = subtitle?.trim();
    return MelbelEntrance(
      child: MelbelChatBlock(
        avatarOnRight: true,
        expression: expression,
        tone: MelbelCardTone.sage,
        senderLabel: 'Melbel',
        title: title,
        titleIsHeader: true,
        message: (cleanSubtitle?.isNotEmpty ?? false) ? cleanSubtitle : null,
        mascotWidth: 60,
        mascotHeight: 54,
      ),
    );
  }
}

class ReviewSessionCard extends StatelessWidget {
  const ReviewSessionCard({
    required this.session,
    this.actualSeconds,
    this.showSchedule = false,
    super.key,
  });

  final FocusSessionInfo session;
  final int? actualSeconds;
  final bool showSchedule;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final textTheme = Theme.of(context).textTheme;
    final deadline = session.deadline?.toLocal();
    final labels = MaterialLocalizations.of(context);
    final deadlineText = deadline == null
        ? 'Chưa có hạn chót'
        : '${labels.formatCompactDate(deadline)} · ${labels.formatTimeOfDay(TimeOfDay.fromDateTime(deadline), alwaysUse24HourFormat: true)}';

    return MelbelCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            session.stepTitle,
            style: textTheme.titleMedium?.copyWith(
              color: palette.onSurface,
              fontWeight: FontWeight.w700,
            ),
          ),
          if (session.taskTitle != session.stepTitle) ...[
            const SizedBox(height: 4),
            Text(session.taskTitle,
                style: textTheme.bodyMedium
                    ?.copyWith(color: palette.onSurfaceVariant)),
          ],
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 6,
            children: [
              if (showSchedule)
                MelbelStatusPill(
                  label:
                      '${labels.formatCompactDate(session.scheduledStart.toLocal())} '
                      '${labels.formatTimeOfDay(TimeOfDay.fromDateTime(session.scheduledStart.toLocal()), alwaysUse24HourFormat: true)}'
                      ' – '
                      '${labels.formatCompactDate(session.scheduledEnd.toLocal())} '
                      '${labels.formatTimeOfDay(TimeOfDay.fromDateTime(session.scheduledEnd.toLocal()), alwaysUse24HourFormat: true)}',
                  icon: Icons.schedule_rounded,
                  tone: MelbelCardTone.sage,
                ),
              MelbelStatusPill(
                label: 'Hạn chót: $deadlineText',
                icon: Icons.flag_outlined,
                tone: MelbelCardTone.muted,
              ),
            ],
          ),
          if (actualSeconds case final actual?) ...[
            const SizedBox(height: 12),
            Text(
              'Kế hoạch: ${(session.workSeconds / 60).ceil()} phút · Thực tế: ${actual ~/ 60} phút',
              style: textTheme.bodySmall?.copyWith(
                color: palette.onSurfaceVariant,
              ),
            ),
            if (session.workSeconds > 0) ...[
              const SizedBox(height: 8),
              MelbelAnimatedProgressBar(
                height: 8,
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
  const FocusReviewPage({
    required this.title,
    required this.children,
    this.bottomBar,
    super.key,
  });

  final String title;
  final List<Widget> children;
  final Widget? bottomBar;

  @override
  Widget build(BuildContext context) => MelbelPageBackdrop(
        showTopCloud: false,
        showBottomClouds: true,
        child: Scaffold(
          backgroundColor: Colors.transparent,
          appBar: MelbelAppBar(
            title: title,
            showBack: true,
            showTitleRays: true,
          ),
          bottomNavigationBar: bottomBar,
          body: SafeArea(
            child: ListView(
              padding: MelbelLayout.pageInsets(
                context,
                top: 12,
                bottom: bottomBar != null ? 20 : 28,
              ),
              children: [
                for (final child in children)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: child,
                  ),
              ],
            ),
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
      try {
        await context.read<FocusProvider?>()?.stopVoicePlayback();
      } catch (_) {}
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
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      final event = StreakProvider.maybeOf(context, listen: false)
          ?.consumePendingCelebration();
      if (event != null) {
        showMelbelStreakCelebration(context, event);
      }
    });
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
                'Đã ghi nhận phiên', null,
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
              'Nghỉ một chút', 'Thả lỏng mắt hoặc uống nước.',
              expression: CatExpression.sleepy),
          PrimaryCta(
              label: 'Về trang chủ',
              onPressed: () => MainShellScreen.returnHome(context)),
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
    'early_completion_pull_forward' => 'Xong sớm · làm phiên tiếp',
    'missed_start' => 'Bắt đầu trễ',
    'expand_next_horizon' => 'Mở rộng lịch tiếp',
    _ => null,
  };
  String? _selectedReason;
  String? get _reason => _checkInReason ?? _selectedReason;

  @override
  Widget build(BuildContext context) => FocusReviewPage(
        title: 'Đổi lịch',
        children: [
          if (_checkInReason case final reason?)
            ReviewHeader(reason, null)
          else
            const ReviewHeader('Vì sao cần đổi lịch?', null),
          if (widget.session case final session?)
            ReviewSessionCard(session: session),
          if (_checkInReason == null)
            for (final (reason, icon) in const [
              ('Chưa xong', Icons.edit_note_outlined),
              ('Bận việc khác', Icons.schedule_outlined),
              ('Hoãn phiên', Icons.update_outlined),
            ])
              MelbelCard(
                borderRadius: 24,
                padding: EdgeInsets.zero,
                tone: _reason == reason
                    ? MelbelCardTone.sage
                    : MelbelCardTone.neutral,
                child: ListTile(
                  contentPadding:
                      const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                  selected: _reason == reason,
                  leading: Container(
                    width: 42,
                    height: 42,
                    decoration: BoxDecoration(
                      color: _reason == reason
                          ? context.melbel.sage.withValues(alpha: 0.18)
                          : context.melbel.sageSoft,
                      borderRadius: BorderRadius.circular(14),
                    ),
                    child: Icon(
                      icon,
                      color: _reason == reason
                          ? context.melbel.sage
                          : context.melbel.onSurfaceVariant,
                    ),
                  ),
                  title: Text(
                    reason,
                    style: Theme.of(context).textTheme.titleSmall?.copyWith(
                          fontWeight: FontWeight.w700,
                        ),
                  ),
                  trailing: Icon(
                    _reason == reason
                        ? Icons.radio_button_checked
                        : Icons.radio_button_unchecked,
                    color: _reason == reason
                        ? context.melbel.sage
                        : context.melbel.onSurfaceVariant,
                  ),
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
                                  taskId: widget.session?.taskId,
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
    this.taskId,
    this.trigger,
    this.proposalId,
    this.api,
    super.key,
  });

  final String reason;
  final FocusSessionInfo? session;
  final String? runId;
  final String? taskId;
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
  String? _notice;
  bool _busy = false;
  bool _cached = false;
  bool? _explanationExpanded;
  bool _showAllChanges = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    if (widget.proposalId != null ||
        widget.runId != null ||
        (widget.taskId != null && widget.trigger != null) ||
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

  bool _needsRebuild(Map<String, dynamic>? proposal) =>
      proposal != null &&
      proposal['status'] == 'pending' &&
      (proposal['stale'] == true ||
          AdaptationApi.hasExpiredProposedSlots(proposal));

  Future<void> _perform(Future<void> Function() operation) async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = null;
      _notice = null;
    });
    try {
      await operation();
    } on ApiException catch (error) {
      if (error.statusCode == 409 &&
          error.requiresProposalRebuild &&
          _proposal != null) {
        try {
          Map<String, dynamic>? rebuilt;
          try {
            rebuilt = await _api.updateProposal('rebuild', _proposal!);
          } on ApiException catch (rebuildErr) {
            if (!rebuildErr.isRevisionConflict) rethrow;
            final latest = await _api.currentProposal();
            if (latest != null && latest['status'] == 'pending') {
              rebuilt = await _api.updateProposal('rebuild', latest);
            } else {
              rebuilt = latest;
            }
          }
          await _api.cacheProposal(rebuilt);
          if (mounted) {
            setState(() {
              _proposal = rebuilt;
              _cached = false;
              _explanationExpanded = null;
              _showAllChanges = false;
              if (rebuilt == null) {
                _error = 'Lịch đã thay đổi. Phương án cũ không còn để duyệt.';
              } else {
                _error = null;
                _notice =
                    'Nguồn lịch đã thay đổi. Phương án mới đã sẵn sàng để duyệt.';
              }
            });
          }
          return;
        } on ApiException {
          // Fall through to normal error handling if rebuild also fails.
        } catch (_) {
          // Fall through to normal error handling if rebuild also fails.
        }
      }
      if (error.statusCode == 409 &&
          error.isRevisionConflict &&
          _proposal != null) {
        try {
          var latest = await _api.currentProposal();
          if (_needsRebuild(latest)) {
            latest = await _api.updateProposal('rebuild', latest!);
          }
          await _api.cacheProposal(latest);
          if (mounted) {
            setState(() {
              _proposal = latest;
              _cached = false;
              _explanationExpanded = null;
              _showAllChanges = false;
              if (latest == null) {
                _error = 'Lịch đã thay đổi. Phương án cũ không còn để duyệt.';
              } else {
                _error = null;
                _notice =
                    'Lịch đã thay đổi. Hãy xem lại phương án mới trước khi duyệt.';
              }
            });
          }
          return;
        } on ApiException {
          // Keep the original conflict visible; the Retry action reloads it.
        } catch (_) {
          // Keep the original conflict visible; the Retry action reloads it.
        }
      }
      if (error.isActiveRunExists) {
        if (mounted) {
          setState(() {
            _error =
                'Đang có phiên tập trung hoạt động hoặc chờ check-in. Hãy hoàn tất check-in trước khi duyệt đổi lịch.';
          });
        }
        return;
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
        final taskId = id == null && run == null && !forceCurrent
            ? widget.taskId
            : null;
        final current = id != null
            ? await _api.proposal(id)
            : run != null
                ? await _api.createProposal(run, widget.trigger!)
                : (taskId != null && widget.trigger != null)
                    ? await _api.createProposalForTask(
                        taskId: taskId,
                        reason: widget.trigger!,
                      )
                    : await _api.currentProposal();
        if (!mounted) return;
        setState(() {
          _proposal = current;
          _cached = false;
          _explanationExpanded = null;
          _showAllChanges = false;
        });
        await _api.cacheProposal(current);
        if (run == null && taskId == null && _needsRebuild(current)) {
          final rebuilt = await _api.updateProposal('rebuild', current!);
          if (mounted) {
            setState(() {
              _proposal = rebuilt;
              _explanationExpanded = null;
              _showAllChanges = false;
            });
          }
          await _api.cacheProposal(rebuilt);
        }
      });

  Future<void> _act(String action) => _perform(() async {
        final updated = await _api.updateProposal(action, _proposal!);
        if (!mounted) return;
        await _api.cacheProposal(action == 'rebuild' ? updated : null);
        if (action == 'approve') {
          unawaited(PreSessionReminderSync.instance.sync());
        }
        if (!mounted) return;
        if (action == 'rebuild') {
          setState(() {
            _proposal = updated;
            _explanationExpanded = null;
            _showAllChanges = false;
          });
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
        'Chọn "Mở thêm giờ rảnh" hoặc sửa hạn trong bản nháp.',
      ),
    ));
  }

  DateTime? _parseLocal(Object? value) =>
      value is String ? DateTime.tryParse(value)?.toLocal() : null;

  String _clockOnly(DateTime date) => MaterialLocalizations.of(context)
      .formatTimeOfDay(TimeOfDay.fromDateTime(date), alwaysUse24HourFormat: true);

  String _shortDay(DateTime date) =>
      '${date.day.toString().padLeft(2, '0')}/${date.month.toString().padLeft(2, '0')}';

  String _compactClockSpan(
    DateTime? start,
    DateTime? end, {
    DateTime? omitDateIfSameAs,
  }) {
    if (start == null) {
      return end == null
          ? 'Chưa có giờ'
          : 'Đến ${_shortDay(end)} · ${_clockOnly(end)}';
    }
    final showDate = omitDateIfSameAs == null ||
        !DateUtils.isSameDay(start, omitDateIfSameAs);
    final prefix = showDate ? '${_shortDay(start)} · ' : '';
    if (end == null) return '$prefix${_clockOnly(start)}';
    if (DateUtils.isSameDay(start, end)) {
      return '$prefix${_clockOnly(start)}–${_clockOnly(end)}';
    }
    return '$prefix${_clockOnly(start)} – ${_shortDay(end)} · ${_clockOnly(end)}';
  }

  List<_ScheduleDiffEntry> _buildDiffEntries(
    Map? changes, {
    required DateTime? sharedDay,
  }) {
    Iterable<Map> items(String key) =>
        (changes?[key] as List? ?? const []).whereType<Map>();
    final entries = <_ScheduleDiffEntry>[];

    for (final item in items('steps_to_add')) {
      entries.add(
        _ScheduleDiffEntry(
          icon: Icons.add_task_rounded,
          text: 'Thêm bước: ${item['title'] ?? ''}',
        ),
      );
    }
    for (final item in items('sessions_to_move')) {
      final beforeMap = item['before'] as Map?;
      final afterMap = item['after'] as Map?;
      final bStart = _parseLocal(beforeMap?['start'] ?? item['from_start']);
      final bEnd = _parseLocal(beforeMap?['end'] ?? item['from_end']);
      final aStart = _parseLocal(afterMap?['start'] ?? item['to_start']);
      final aEnd = _parseLocal(afterMap?['end'] ?? item['to_end']);
      final baseDay = sharedDay ?? bStart;
      entries.add(
        _ScheduleDiffEntry(
          icon: Icons.swap_horiz_rounded,
          oldRange: _compactClockSpan(
            bStart,
            bEnd,
            omitDateIfSameAs: sharedDay,
          ),
          newRange: _compactClockSpan(
            aStart,
            aEnd,
            omitDateIfSameAs: baseDay,
          ),
        ),
      );
    }
    for (final item in items('sessions_to_add')) {
      final afterMap = item['after'] as Map?;
      final aStart = _parseLocal(afterMap?['start']);
      final aEnd = _parseLocal(afterMap?['end']);
      entries.add(
        _ScheduleDiffEntry(
          icon: Icons.add_circle_outline_rounded,
          newRange: _compactClockSpan(
            aStart,
            aEnd,
            omitDateIfSameAs: sharedDay,
          ),
          text: 'Thêm phiên',
        ),
      );
    }
    for (final item in items('sessions_to_cancel')) {
      entries.add(
        _ScheduleDiffEntry(
          icon: Icons.event_busy_outlined,
          text: item['reason'] == 'resegmented_by_settings_change'
              ? 'Chia lại theo cài đặt mới'
              : 'Bỏ 1 phiên',
        ),
      );
    }
    return entries;
  }

  DateTime? _detectSharedDay(Map? changes) {
    if (changes == null) return null;
    DateTime? common;
    void inspect(Object? raw) {
      final dt = _parseLocal(raw);
      if (dt == null) return;
      final d = DateUtils.dateOnly(dt);
      common ??= d;
      if (!DateUtils.isSameDay(common, d)) {
        common = DateTime(1900);
      }
    }

    for (final item
        in (changes['sessions_to_move'] as List? ?? const []).whereType<Map>()) {
      final b = item['before'] as Map?;
      final a = item['after'] as Map?;
      inspect(b?['start'] ?? item['from_start']);
      inspect(a?['start'] ?? item['to_start']);
    }
    for (final item
        in (changes['sessions_to_add'] as List? ?? const []).whereType<Map>()) {
      final a = item['after'] as Map?;
      inspect(a?['start']);
    }
    if (common == null || common!.year == 1900) return null;
    return common;
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final textTheme = Theme.of(context).textTheme;
    final proposal = _proposal;
    final changes = proposal?['proposed_changes'] as Map?;
    final feasibility = proposal?['feasibility'] as Map?;
    final deficit = feasibility?['deficit_seconds'] as int?;
    final pending = !_cached && proposal?['status'] == 'pending';
    final isStale = proposal?['stale'] == true;
    final confirmable = feasibility?['confirmable'] == true;
    final canApply = pending && !isStale && confirmable && _error == null;
    final needsAdjustment =
        pending && !isStale && ((deficit ?? 0) > 0 || !confirmable);

    final sharedDay = _detectSharedDay(changes);
    final diffEntries = _buildDiffEntries(changes, sharedDay: sharedDay);
    final visibleCount = _showAllChanges
        ? diffEntries.length
        : math.min(diffEntries.length, 3);
    final explanation = (proposal?['explanation'] as String?)?.trim();
    final hasExplanation = explanation != null && explanation.isNotEmpty;
    final explanationExpanded =
        _explanationExpanded ?? (hasExplanation && explanation.length <= 80);

    final fallbackReason =
        widget.reason == 'Đề xuất chờ duyệt' || widget.reason.trim().isEmpty
            ? 'Xem lại lịch'
            : widget.reason;

    final String statusHeadline;
    final String? statusHint;
    final MelbelCardTone statusTone;
    if (proposal == null) {
      statusHeadline = fallbackReason;
      statusHint = null;
      statusTone = MelbelCardTone.sage;
    } else if ((deficit ?? 0) > 0) {
      statusHeadline = 'Thiếu ${(deficit! / 60).ceil()} phút';
      statusHint = 'Mở thêm giờ rảnh hoặc lùi hạn chót.';
      statusTone = MelbelCardTone.amber;
    } else if (needsAdjustment) {
      statusHeadline = 'Cần mở thêm giờ rảnh';
      statusHint = 'Mở thêm giờ rảnh hoặc lùi hạn chót.';
      statusTone = MelbelCardTone.amber;
    } else if (canApply) {
      statusHeadline = diffEntries.isNotEmpty
          ? 'Đổi ${diffEntries.length} phiên'
          : 'Sẵn sàng áp dụng lịch mới';
      statusHint = 'Xem nhanh giờ mới rồi áp dụng.';
      statusTone = MelbelCardTone.sage;
    } else if (isStale) {
      statusHeadline = 'Phương án cần tính lại';
      statusHint = 'Bấm tính lại để cập nhật giờ mới.';
      statusTone = MelbelCardTone.amber;
    } else {
      statusHeadline = fallbackReason;
      statusHint = null;
      statusTone = MelbelCardTone.sage;
    }

    final narrowDock = MediaQuery.sizeOf(context).width < 340;
    final Widget dock = Container(
      decoration: BoxDecoration(
        color: palette.elevatedSurface,
        border: Border(top: BorderSide(color: palette.hairline, width: 1)),
      ),
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 14),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (_error != null) ...[
                PrimaryCta(
                  label: 'Thử lại',
                  icon: Icons.refresh_rounded,
                  onPressed: _busy ? null : () => _load(forceCurrent: true),
                ),
                const SizedBox(height: 8),
                SecondaryCta(
                  label: 'Giữ lịch hiện tại',
                  height: 48,
                  onPressed: _busy
                      ? null
                      : pending
                          ? () => _act('reject')
                          : () => Navigator.pop(context),
                ),
              ] else if (canApply) ...[
                PrimaryCta(
                  label: 'Áp dụng lịch mới',
                  icon: Icons.check_rounded,
                  onPressed: _busy ? null : () => _act('approve'),
                ),
                const SizedBox(height: 8),
                SecondaryCta(
                  label: 'Giữ lịch hiện tại',
                  height: 48,
                  onPressed: _busy ? null : () => _act('reject'),
                ),
              ] else if (needsAdjustment) ...[
                PrimaryCta(
                  label: 'Mở thêm giờ rảnh',
                  icon: Icons.more_time_rounded,
                  onPressed: _busy ? null : _openAvailabilityEditor,
                ),
                const SizedBox(height: 8),
                if (narrowDock) ...[
                  SecondaryCta(
                    label: 'Lùi hạn chót',
                    height: 48,
                    onPressed: _busy ? null : _explainDeadlineEditRequirement,
                  ),
                  const SizedBox(height: 8),
                  SecondaryCta(
                    label: 'Giữ lịch hiện tại',
                    height: 48,
                    onPressed: _busy ? null : () => _act('reject'),
                  ),
                ] else
                  Row(
                    children: [
                      Expanded(
                        child: SecondaryCta(
                          label: 'Lùi hạn chót',
                          height: 48,
                          onPressed:
                              _busy ? null : _explainDeadlineEditRequirement,
                        ),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: SecondaryCta(
                          label: 'Giữ lịch hiện tại',
                          height: 48,
                          onPressed: _busy ? null : () => _act('reject'),
                        ),
                      ),
                    ],
                  ),
              ] else if (pending) ...[
                PrimaryCta(
                  label: 'Tính lại phương án',
                  icon: Icons.refresh_rounded,
                  onPressed: _busy ? null : () => _act('rebuild'),
                ),
                const SizedBox(height: 8),
                SecondaryCta(
                  label: 'Giữ lịch hiện tại',
                  height: 48,
                  onPressed: _busy ? null : () => _act('reject'),
                ),
              ] else ...[
                if (widget.session == null &&
                    proposal == null &&
                    widget.trigger != 'resume') ...[
                  SecondaryCta(
                    label: 'Mở lịch hiện tại',
                    height: 48,
                    onPressed: () =>
                        Navigator.of(context).pushNamed('/calendar'),
                  ),
                  const SizedBox(height: 8),
                ],
                SecondaryCta(
                  label: 'Giữ lịch hiện tại',
                  height: 48,
                  onPressed: _busy ? null : () => Navigator.pop(context),
                ),
              ],
            ],
          ),
        ),
      ),
    );

    return FocusReviewPage(
      title: 'Điều chỉnh lịch',
      bottomBar: dock,
      children: [
        _ScheduleStatusBanner(
          headline: statusHeadline,
          hint: statusHint,
          tone: statusTone,
          expression:
              needsAdjustment ? CatExpression.sleepy : CatExpression.neutral,
          explanation: hasExplanation ? explanation : null,
          expanded: explanationExpanded,
          onToggleExplanation: hasExplanation
              ? () => setState(
                    () => _explanationExpanded = !explanationExpanded,
                  )
              : null,
        ),
        if (widget.session case final session?)
          ReviewSessionCard(session: session, showSchedule: true),
        if (_busy)
          ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: const LinearProgressIndicator(minHeight: 6),
          ),
        if (_error != null)
          MelbelCard(
            borderRadius: 22,
            tone: MelbelCardTone.amber,
            child: Text(_error!, textAlign: TextAlign.start),
          ),
        if (_notice != null && _error == null)
          MelbelCard(
            borderRadius: 22,
            tone: MelbelCardTone.amber,
            child: Text(_notice!, textAlign: TextAlign.start),
          ),
        if (_cached)
          const Align(
            alignment: Alignment.centerLeft,
            child: MelbelStatusPill(
              label: 'Bản lưu trên máy · cần mạng để duyệt',
              icon: Icons.cloud_off_outlined,
              tone: MelbelCardTone.muted,
            ),
          ),
        Row(
          children: [
            const Expanded(child: Eyebrow('Lịch đề xuất')),
            if (diffEntries.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(right: 4),
                child: Text(
                  '${diffEntries.length} thay đổi',
                  style: textTheme.labelSmall?.copyWith(
                    color: palette.onSurfaceVariant,
                    fontWeight: FontWeight.w600,
                    fontFeatures: const [FontFeature.tabularFigures()],
                  ),
                ),
              ),
            if (pending && needsAdjustment && _error == null)
              IconButton(
                tooltip: 'Tính lại phương án',
                constraints:
                    const BoxConstraints(minWidth: 48, minHeight: 48),
                onPressed: _busy ? null : () => _act('rebuild'),
                icon: Icon(
                  Icons.refresh_rounded,
                  size: 20,
                  color: palette.accentInk,
                ),
              ),
          ],
        ),
        if (proposal == null && !_busy && _error == null)
          MelbelCard(
            borderRadius: 22,
            child: Row(
              children: [
                Icon(
                  Icons.event_available_outlined,
                  size: 20,
                  color: palette.accentInk,
                ),
                const SizedBox(width: 10),
                const Expanded(
                  child: Text(
                    'Chưa có đề xuất đổi lịch.',
                    textAlign: TextAlign.start,
                  ),
                ),
              ],
            ),
          ),
        if (proposal != null && diffEntries.isNotEmpty)
          MelbelCard(
            borderRadius: 22,
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (sharedDay != null) ...[
                  Align(
                    alignment: Alignment.centerLeft,
                    child: MelbelStatusPill(
                      label:
                          MaterialLocalizations.of(context).formatCompactDate(
                        sharedDay,
                      ),
                      icon: Icons.calendar_today_outlined,
                      tone: MelbelCardTone.muted,
                    ),
                  ),
                  const SizedBox(height: 10),
                ],
                for (var i = 0; i < visibleCount; i++) ...[
                  if (i > 0)
                    Divider(
                      height: 16,
                      thickness: 1,
                      color: palette.hairline,
                    ),
                  _DiffEntryRow(entry: diffEntries[i]),
                ],
                if (diffEntries.length > 3) ...[
                  Divider(
                    height: 16,
                    thickness: 1,
                    color: palette.hairline,
                  ),
                  Center(
                    child: TextButton.icon(
                      style: TextButton.styleFrom(
                        minimumSize: const Size(48, 44),
                      ),
                      onPressed: () =>
                          setState(() => _showAllChanges = !_showAllChanges),
                      icon: Icon(
                        _showAllChanges
                            ? Icons.expand_less_rounded
                            : Icons.expand_more_rounded,
                        size: 18,
                      ),
                      label: Text(
                        _showAllChanges
                            ? 'Thu gọn'
                            : 'Xem thêm ${diffEntries.length - 3} thay đổi',
                      ),
                    ),
                  ),
                ],
              ],
            ),
          ),
      ],
    );
  }
}

class _ScheduleStatusBanner extends StatelessWidget {
  const _ScheduleStatusBanner({
    required this.headline,
    required this.tone,
    required this.expression,
    required this.expanded,
    this.hint,
    this.explanation,
    this.onToggleExplanation,
  });

  final String headline;
  final String? hint;
  final MelbelCardTone tone;
  final CatExpression expression;
  final String? explanation;
  final bool expanded;
  final VoidCallback? onToggleExplanation;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final textTheme = Theme.of(context).textTheme;
    final reducedMotion = MediaQuery.disableAnimationsOf(context);
    final isAmber = tone == MelbelCardTone.amber;
    final headlineColor = isAmber ? palette.onAmberSoft : palette.onSageSoft;
    final subColor = isAmber
        ? palette.onAmberSoft.withValues(alpha: 0.85)
        : palette.onSurfaceVariant;

    return MelbelCard(
      tone: tone,
      borderRadius: 24,
      padding: const EdgeInsets.fromLTRB(14, 12, 10, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              MelbelMascot(
                expression: expression,
                width: 52,
                height: 46,
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      headline,
                      textAlign: TextAlign.start,
                      style: textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w800,
                        color: headlineColor,
                      ),
                    ),
                    if (hint != null) ...[
                      const SizedBox(height: 2),
                      Text(
                        hint!,
                        textAlign: TextAlign.start,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: textTheme.bodySmall?.copyWith(
                          color: subColor,
                          height: 1.4,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              if (onToggleExplanation != null)
                IconButton(
                  tooltip: expanded ? 'Ẩn chi tiết' : 'Xem giải thích',
                  constraints:
                      const BoxConstraints(minWidth: 48, minHeight: 48),
                  onPressed: onToggleExplanation,
                  icon: Icon(
                    expanded
                        ? Icons.expand_less_rounded
                        : Icons.info_outline_rounded,
                    color: isAmber ? palette.amberInk : palette.accentInk,
                  ),
                ),
            ],
          ),
          AnimatedSize(
            duration: reducedMotion
                ? Duration.zero
                : const Duration(milliseconds: 200),
            curve: Curves.easeOutCubic,
            alignment: Alignment.topCenter,
            child: !expanded || explanation == null
                ? const SizedBox(width: double.infinity, height: 0)
                : Padding(
                    padding: const EdgeInsets.only(top: 10),
                    child: Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: palette.surface.withValues(alpha: 0.68),
                        borderRadius: BorderRadius.circular(14),
                      ),
                      child: Text(
                        explanation!,
                        textAlign: TextAlign.start,
                        style: textTheme.bodySmall?.copyWith(
                          color: palette.onSurface,
                          height: 1.5,
                        ),
                      ),
                    ),
                  ),
          ),
        ],
      ),
    );
  }
}

class _ScheduleDiffEntry {
  const _ScheduleDiffEntry({
    required this.icon,
    this.oldRange,
    this.newRange,
    this.text,
  });

  final IconData icon;
  final String? oldRange;
  final String? newRange;
  final String? text;
}

class _DiffEntryRow extends StatelessWidget {
  const _DiffEntryRow({required this.entry});

  final _ScheduleDiffEntry entry;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final textTheme = Theme.of(context).textTheme;

    if (entry.oldRange != null && entry.newRange != null) {
      return Row(
        children: [
          Flexible(
            child: Text(
              entry.oldRange!,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: textTheme.bodySmall?.copyWith(
                color: palette.onSurfaceVariant,
                decoration: TextDecoration.lineThrough,
                fontFeatures: const [FontFeature.tabularFigures()],
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8),
            child: Icon(
              Icons.arrow_forward_rounded,
              size: 16,
              color: palette.accentInk,
            ),
          ),
          Flexible(
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
              decoration: BoxDecoration(
                color: palette.sageSoft,
                borderRadius: BorderRadius.circular(10),
              ),
              child: FittedBox(
                fit: BoxFit.scaleDown,
                child: Text(
                  entry.newRange!,
                  maxLines: 1,
                  style: textTheme.labelMedium?.copyWith(
                    color: palette.onSageSoft,
                    fontWeight: FontWeight.w800,
                    fontFeatures: const [FontFeature.tabularFigures()],
                  ),
                ),
              ),
            ),
          ),
        ],
      );
    }

    return Row(
      children: [
        Icon(entry.icon, size: 18, color: palette.accentInk),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            entry.newRange != null && entry.text != null
                ? '${entry.text}: ${entry.newRange}'
                : (entry.text ?? entry.newRange ?? ''),
            textAlign: TextAlign.start,
            style: textTheme.bodyMedium?.copyWith(
              color: palette.onSurface,
              fontWeight: FontWeight.w600,
              fontFeatures: const [FontFeature.tabularFigures()],
            ),
          ),
        ),
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
    final value = await showMelbelTimePicker(
      context: context,
      helpText: start ? 'Giờ bắt đầu' : 'Giờ kết thúc',
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
            null,
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
        ],
      );
}
