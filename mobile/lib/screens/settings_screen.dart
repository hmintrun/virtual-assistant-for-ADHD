import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/api_client.dart';
import '../core/theme.dart';
import '../providers/streak_provider.dart';
import '../providers/theme_provider.dart';
import '../services/adaptation_api.dart';
import '../services/pre_session_reminder_sync.dart';
import '../widgets/melbel_ui.dart';
import '../widgets/shimmer_loading.dart';
import 'focus_review_screens.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({this.api, this.adaptation, super.key});

  final ApiClient? api;
  final AdaptationApi? adaptation;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final _api = widget.api ?? ApiClient();
  late final _adaptation = widget.adaptation ?? AdaptationApi(_api);
  Map<String, dynamic>? _suggestion;
  String? _suggestionError;
  Map<String, dynamic>? _preferences;
  Map<String, dynamic>? _pendingChange;
  int? get _effectiveRevision =>
      (_preferences?['effective_revision'] ?? _preferences?['revision'])
          as int?;
  int? _work;
  int? _rest;
  double? _bufferMultiplier;
  List<int>? _reminders;
  String? _reminderStyle;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final data = await _api.getData('/preferences');
      if (!mounted) return;
      setState(() {
        _preferences = data;
        _work = data['work_interval_seconds'] as int?;
        _rest = data['rest_interval_seconds'] as int?;
        _bufferMultiplier = (data['buffer_multiplier'] as num?)?.toDouble();
        _reminders = (data['reminder_offsets'] as List?)?.cast<int>();
        _reminderStyle = data['reminder_style'] as String?;
      });
      await _loadSuggestion();
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _loadSuggestion() async {
    try {
      final value = await _adaptation.suggestion();
      if (mounted)
        setState(() {
          _suggestion = value;
          _suggestionError = null;
        });
    } on ApiException catch (error) {
      if (mounted)
        setState(() {
          _suggestion = null;
          _suggestionError = error.message;
        });
    }
  }

  Future<void> _respond(
    String action, {
    Object? editedValue,
    String? annoyanceFeedback,
  }) async {
    final suggestion = _suggestion;
    final revision = _effectiveRevision;
    if (_busy || suggestion == null || revision == null) return;
    setState(() => _busy = true);
    try {
      final result = await _adaptation.respond(
        suggestion,
        revision,
        action,
        editedValue: editedValue,
        annoyanceFeedback: annoyanceFeedback,
      );
      if (!mounted) return;
      if (result['application_state'] == 'pending_review') {
        _pendingChange = result;
      }
      await _load();
      if (!mounted) return;
      if (result['pending_proposal_id'] case final String proposalId
          when proposalId.isNotEmpty) {
        await _reviewProposal(proposalId);
        return;
      }
      if (result['application_state'] == 'effective_from_next_session') {
        unawaited(PreSessionReminderSync.instance.sync(api: _api));
      }
      if (action == 'accept' || action == 'edit') {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              result['application_state'] == 'pending_review'
                  ? 'Đã tạo yêu cầu thay đổi chờ duyệt lịch.'
                  : 'Đã áp dụng gợi ý cho phiên sau.',
            ),
            behavior: SnackBarBehavior.floating,
          ),
        );
      }
    } on ApiException catch (error) {
      if (mounted) await _handleCommandError(error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _rejectSuggestion() async {
    if (_busy || _suggestion == null || _effectiveRevision == null) return;
    final palette = context.melbel;
    final textTheme = Theme.of(context).textTheme;
    const options = [
      (
        value: '',
        label: 'Chỉ bỏ qua lần này',
        subtitle: 'Tạm ẩn gợi ý trong 7 ngày',
        icon: Icons.schedule_rounded,
      ),
      (
        value: 'too_frequent',
        label: 'Gợi ý quá thường xuyên',
        subtitle: 'Tạm ẩn gợi ý trong 14 ngày',
        icon: Icons.notifications_paused_outlined,
      ),
      (
        value: 'not_helpful',
        label: 'Gợi ý chưa hữu ích',
        subtitle: 'Tạm ẩn gợi ý trong 14 ngày',
        icon: Icons.thumb_down_off_alt_rounded,
      ),
      (
        value: 'interrupted_me',
        label: 'Đang làm phiền tôi',
        subtitle: 'Tạm ẩn gợi ý trong 14 ngày',
        icon: Icons.do_not_disturb_on_outlined,
      ),
    ];

    final selected = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      backgroundColor: palette.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
      ),
      builder: (sheetContext) => SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                child: Text(
                  'Bỏ qua gợi ý này?',
                  style: textTheme.titleMedium?.copyWith(
                    color: palette.onSurface,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
              const SizedBox(height: 6),
              for (final option in options)
                ListTile(
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(18),
                  ),
                  leading: Icon(option.icon, color: palette.accentInk),
                  title: Text(option.label),
                  subtitle: Text(
                    option.subtitle,
                    style: textTheme.bodySmall?.copyWith(
                      color: palette.onSurfaceVariant,
                    ),
                  ),
                  onTap: () => Navigator.of(sheetContext).pop(option.value),
                ),
            ],
          ),
        ),
      ),
    );
    if (!mounted || selected == null) return;
    if (selected.isEmpty) {
      await _respond('reject');
    } else {
      await _respond('reject', annoyanceFeedback: selected);
    }
  }

  Future<void> _reviewProposal(String id) async {
    await Navigator.of(context).push<bool>(MaterialPageRoute(
      builder: (_) => ScheduleComparisonScreen(
        reason: 'Thay đổi cài đặt',
        proposalId: id,
        api: _adaptation,
      ),
    ));
    if (mounted) await _load();
  }

  String _suggestionValue(Object? value) => value is List
      ? value
          .whereType<int>()
          .map((seconds) => '${seconds ~/ 60} phút')
          .join(', ')
      : '×$value';

  Future<String?> _showPreferenceEditor(Object value) async {
    final isList = value is List;
    final controller = TextEditingController(
      text: isList
          ? value.whereType<int>().map((s) => s ~/ 60).join(', ')
          : '$value',
    );
    const reminderPresets = [
      ('15 & 5 phút', '15, 5'),
      ('10 & 5 phút', '10, 5'),
      ('5 phút', '5'),
    ];
    const bufferPresets = [
      ('×1.0', '1.0'),
      ('×1.2', '1.2'),
      ('×1.4', '1.4'),
      ('×1.6', '1.6'),
    ];

    bool isValidValue(String raw) {
      final text = raw.trim();
      if (text.isEmpty) return false;
      if (isList) {
        return _parseReminderOffsetsSeconds(text) != null;
      }
      return _parseBuffer(text) != null;
    }

    final result = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (sheet) => StatefulBuilder(
        builder: (sheet, setSheetState) {
          final palette = sheet.melbel;
          final textTheme = Theme.of(sheet).textTheme;
          final currentText = controller.text.trim();
          final valid = isValidValue(currentText);
          final presets = isList ? reminderPresets : bufferPresets;

          return SafeArea(
            child: SingleChildScrollView(
              padding: EdgeInsets.fromLTRB(
                20,
                4,
                20,
                20 + MediaQuery.viewInsetsOf(sheet).bottom,
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(
                    isList ? 'Nhắc trước phiên' : 'Hệ số đệm thời gian',
                    style: textTheme.titleLarge?.copyWith(
                      color: palette.onSurface,
                    ),
                  ),
                  const SizedBox(height: 12),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      for (final (label, presetVal) in presets)
                        ChoiceChip(
                          label: Text(label),
                          selected: currentText == presetVal,
                          onSelected: (_) {
                            controller.text = presetVal;
                            setSheetState(() {});
                          },
                        ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: controller,
                    keyboardType: TextInputType.text,
                    onChanged: (_) => setSheetState(() {}),
                    decoration: InputDecoration(
                      labelText: isList
                          ? 'Phút, cách nhau bằng dấu phẩy'
                          : 'Hệ số đệm',
                      errorText: !valid
                          ? (isList
                              ? 'Nhập 1–3 mốc giảm dần (vd: 15, 10, 5).'
                              : 'Nhập hệ số đệm từ 1,0 đến 2,0.')
                          : null,
                    ),
                  ),
                  const SizedBox(height: 14),
                  PrimaryCta(
                    label: 'Áp dụng',
                    onPressed:
                        valid ? () => Navigator.pop(sheet, currentText) : null,
                  ),
                  const SizedBox(height: 4),
                  TextButton(
                    onPressed: () => Navigator.pop(sheet),
                    child: const Text('Hủy'),
                  ),
                ],
              ),
            ),
          );
        },
      ),
    );
    WidgetsBinding.instance.addPostFrameCallback((_) => controller.dispose());
    return result;
  }

  List<int>? _parseReminderOffsetsSeconds(String raw) {
    final parts = raw
        .split(',')
        .map((v) => int.tryParse(v.trim()))
        .toList(growable: false);
    if (parts.isEmpty ||
        parts.length > 3 ||
        parts.any((v) => v == null || v < 0)) {
      return null;
    }
    final minutes = parts.cast<int>();
    for (var i = 0; i < minutes.length - 1; i++) {
      if (minutes[i] <= minutes[i + 1]) return null;
    }
    return minutes.map((v) => v * 60).toList(growable: false);
  }

  double? _parseBuffer(String text) {
    final value = double.tryParse(text.replaceAll(',', '.'));
    return value != null && value >= 1 && value <= 2 ? value : null;
  }

  Future<void> _editBuffer(double current) async {
    final result = await _showPreferenceEditor(current);
    if (!mounted || result == null) return;
    final value = _parseBuffer(result);
    setState(() {
      _error = value == null ? 'Nhập hệ số đệm từ 1,0 đến 2,0.' : null;
      if (value != null) _bufferMultiplier = value;
    });
  }

  Future<void> _editSuggestion(Object value) async {
    final result = await _showPreferenceEditor(value);
    if (!mounted || result == null) return;
    Object? parsed;
    if (value is List) {
      parsed = _parseReminderOffsetsSeconds(result);
    } else {
      parsed = _parseBuffer(result);
    }
    if (parsed == null) {
      setState(() => _suggestionError = 'Giá trị chưa hợp lệ.');
      return;
    }
    await _respond('edit', editedValue: parsed);
  }

  Map<String, dynamic> get _changes {
    final current = _preferences;
    if (current == null) return const {};
    return {
      if (_work != null && _work != current['work_interval_seconds'])
        'work_interval_seconds': _work,
      if (_rest != null && _rest != current['rest_interval_seconds'])
        'rest_interval_seconds': _rest,
      if (_bufferMultiplier != null &&
          _bufferMultiplier != current['buffer_multiplier'])
        'buffer_multiplier': _bufferMultiplier,
      if (_reminders != null &&
          _reminders!.join(',') !=
              (current['reminder_offsets'] as List?)?.join(','))
        'reminder_offsets': _reminders,
      if (_reminderStyle != null && _reminderStyle != current['reminder_style'])
        'reminder_style': _reminderStyle,
    };
  }

  Future<void> _save() async {
    final changes = _changes;
    if (_busy || changes.isEmpty) return;
    final revision = _effectiveRevision;
    if (revision == null) return;
    final payload = {
      'expected_preferences_revision': revision,
      'changes': {'preferences': changes},
    };
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final result = await _api.postData(
        '/settings/change-requests',
        data: payload,
        idempotencyKey: ApiClient.newIdempotencyKey('settings_change'),
      );
      if (!mounted) return;
      _pendingChange = result;
      unawaited(
        PreSessionReminderSync.instance.sync(
          api: _api,
          offsetsSeconds: _reminders,
        ),
      );
      await _load();
      if (!mounted) return;
      if (result['pending_proposal_id'] case final String proposalId) {
        await _reviewProposal(proposalId);
        return;
      }
      await Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (context) => FocusReviewPage(
          title: 'Đã lưu cài đặt',
          children: [
            ReviewHeader(
              result['pending_proposal_id'] == null
                  ? 'Áp dụng từ phiên sau'
                  : 'Thay đổi đang chờ duyệt',
              'Phiên đang chạy tiếp tục theo cấu hình lúc bắt đầu.',
              expression: CatExpression.happy,
            ),
            PrimaryCta(
                label: 'Về cài đặt', onPressed: () => Navigator.pop(context)),
          ],
        ),
      ));
    } on ApiException catch (error) {
      if (mounted) await _handleCommandError(error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _handleCommandError(ApiException error) async {
    if (error.isActiveRunExists) {
      await _load();
      if (!mounted) return;
      setState(() {
        _error =
            'Đang có phiên tập trung hoạt động. Hãy hoàn tất check-in trước khi đổi lịch.';
      });
      return;
    }
    if (error.statusCode == 409) {
      _pendingChange = null;
      await _load();
      if (!mounted) return;
      setState(() {
        _error ??= 'Cài đặt đã thay đổi. Hãy kiểm tra lại trước khi lưu.';
      });
      return;
    }
    setState(() => _error = error.message);
  }

  Future<void> _cancelChange(String id, int revision) async {
    if (_busy) return;
    setState(() => _busy = true);
    final path = '/settings/change-requests/${Uri.encodeComponent(id)}/cancel';
    try {
      await _api.postData(
        path,
        data: {'expected_request_revision': revision},
        idempotencyKey: ApiClient.newIdempotencyKey('settings_cancel'),
      );
      _pendingChange = null;
      if (mounted) await _load();
    } on ApiException catch (error) {
      if (mounted) await _handleCommandError(error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String _duration(int seconds) =>
      seconds % 60 == 0 ? '${seconds ~/ 60} phút' : '$seconds giây';

  Widget _minutes(
    String label,
    List<int> options,
    int? selected,
    ValueChanged<int> choose,
  ) {
    final values = <int>{
      ...options.map((minutes) => minutes * 60),
      if (selected != null) selected,
    };
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: Theme.of(context).textTheme.titleSmall),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          runSpacing: 6,
          children: [
            for (final seconds in values)
              ChoiceChip(
                label: Text(
                  _duration(seconds),
                ),
                selected: seconds == selected,
                onSelected: _busy ? null : (_) => choose(seconds),
              ),
          ],
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = context.watch<ThemeProvider>();
    final streak = StreakProvider.maybeOf(context);
    final palette = context.melbel;
    final textTheme = Theme.of(context).textTheme;
    final pendingProposalId = _preferences?['pending_proposal_id'] as String?;
    final inMemoryChange = pendingProposalId != null &&
            pendingProposalId == _pendingChange?['pending_proposal_id']
        ? _pendingChange
        : null;
    final pendingRequestId =
        (_preferences?['pending_change_request_id'] as String?) ??
            inMemoryChange?['change_request_id'] as String?;
    final pendingRequestRevision =
        (_preferences?['pending_change_request_revision'] as num?)?.toInt() ??
            (inMemoryChange?['request_revision'] as num?)?.toInt();
    final hasPendingBanner =
        pendingProposalId != null || pendingRequestId != null;

    return FocusReviewPage(
      title: 'Cài đặt',
      children: [
        const ReviewHeader(
          'Nhịp làm việc',
          'Áp dụng từ phiên sau.',
        ),
        if (_busy && _preferences == null)
          const MelbelLoadingSkeleton(
            cardCount: 3,
            caption: 'Đang tải cài đặt của bạn...',
          )
        else if (_busy)
          const ClipRRect(
            borderRadius: BorderRadius.all(Radius.circular(99)),
            child: LinearProgressIndicator(minHeight: 4),
          ),
        if (_error != null)
          MelbelCard(
            tone: MelbelCardTone.amber,
            padding: const EdgeInsets.all(14),
            child: Row(
              children: [
                Icon(Icons.info_outline_rounded,
                    size: 18, color: palette.amberInk),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    _error!,
                    style: textTheme.bodySmall?.copyWith(
                      color: palette.onAmberSoft,
                    ),
                  ),
                ),
              ],
            ),
          ),
        if (_preferences == null && !_busy)
          SecondaryCta(
            label: 'Tải lại cài đặt',
            icon: Icons.refresh_rounded,
            onPressed: _load,
          ),
        if (_preferences != null) ...[
          if (hasPendingBanner)
            MelbelCard(
              tone: MelbelCardTone.amber,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Eyebrow('Bạn đang có thay đổi lịch chờ duyệt',
                      color: palette.amberInk),
                  const SizedBox(height: 6),
                  Text(
                    'Ảnh hưởng tới lịch tương lai.',
                    style: textTheme.bodySmall?.copyWith(
                      color: palette.onAmberSoft,
                    ),
                  ),
                  if (pendingProposalId != null) ...[
                    const SizedBox(height: 12),
                    SecondaryCta(
                      label: 'Xem thay đổi chờ duyệt',
                      icon: Icons.event_repeat_rounded,
                      onPressed: _busy
                          ? null
                          : () => _reviewProposal(pendingProposalId),
                    ),
                  ],
                  if (pendingRequestId != null &&
                      pendingRequestRevision != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 6),
                      child: TextButton(
                        onPressed: _busy
                            ? null
                            : () => _cancelChange(
                                pendingRequestId, pendingRequestRevision),
                        child: const Text('Hủy yêu cầu đổi cài đặt'),
                      ),
                    ),
                ],
              ),
            ),
          if (_suggestionError != null)
            MelbelCard(
              tone: MelbelCardTone.muted,
              padding: const EdgeInsets.all(12),
              child: Text(
                _suggestionError!,
                style: textTheme.bodySmall?.copyWith(
                  color: palette.onSurfaceVariant,
                ),
              ),
            ),
          if (_suggestion case final suggestion?)
            MelbelCard(
              tone: MelbelCardTone.sage,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const MelbelStatusPill(
                    label: 'Gợi ý cho phiên sau',
                    icon: Icons.auto_awesome_outlined,
                    tone: MelbelCardTone.neutral,
                  ),
                  const SizedBox(height: 10),
                  Text(
                    '${_suggestionValue(suggestion['current_value'])} → ${_suggestionValue(suggestion['suggested_value'])}',
                    style: textTheme.titleMedium?.copyWith(
                      color: palette.onSageSoft,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  if (suggestion['reason'] case final String reason) ...[
                    const SizedBox(height: 6),
                    Text(
                      reason,
                      style: textTheme.bodySmall?.copyWith(
                        color: palette.onSageSoft.withValues(alpha: 0.9),
                      ),
                    ),
                  ],
                  if (pendingProposalId != null &&
                      suggestion['status'] == 'pending') ...[
                    const SizedBox(height: 6),
                    Text(
                      'Đang chờ duyệt lịch ở mục trên.',
                      style: textTheme.bodySmall?.copyWith(
                        color: palette.onSageSoft.withValues(alpha: 0.85),
                      ),
                    ),
                  ],
                  const SizedBox(height: 12),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      FilledButton.tonal(
                        onPressed: _busy ? null : () => _respond('accept'),
                        child: const Text('Áp dụng'),
                      ),
                      if (suggestion['suggested_value'] case final Object value)
                        OutlinedButton(
                          onPressed:
                              _busy ? null : () => _editSuggestion(value),
                          child: const Text('Tùy chỉnh'),
                        ),
                      TextButton(
                        onPressed: _busy ? null : _rejectSuggestion,
                        child: const Text('Bỏ qua'),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          MelbelDisclosure(
            title: 'Tập trung và nghỉ',
            icon: Icons.timer_outlined,
            summary: [
              if (_work != null) 'Làm ${_duration(_work!)}',
              if (_rest != null) 'Nghỉ ${_duration(_rest!)}',
            ].join(' · '),
            children: [
              _minutes(
                'Tập trung',
                [15, 25, 40],
                _work,
                (value) => setState(() => _work = value),
              ),
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 12),
                child: Divider(),
              ),
              _minutes(
                'Nghỉ',
                [0, 5, 10],
                _rest,
                (value) => setState(() => _rest = value),
              ),
            ],
          ),
          MelbelDisclosure(
            title: 'Nhắc nhở và thời gian dự phòng',
            icon: Icons.notifications_none_rounded,
            children: [
              Text('Giọng nhắc', style: textTheme.titleSmall),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 6,
                children: [
                  for (final entry in const {
                    'gentle': 'Nhẹ nhàng',
                    'upbeat': 'Tươi vui',
                    'encouraging': 'Khích lệ',
                  }.entries)
                    ChoiceChip(
                      label: Text(entry.value),
                      selected: _reminderStyle == entry.key,
                      onSelected: _busy
                          ? null
                          : (_) => setState(() => _reminderStyle = entry.key),
                    ),
                ],
              ),
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 12),
                child: Divider(),
              ),
              Text('Mốc nhắc trước phiên', style: textTheme.titleSmall),
              if (_reminders != null && _reminders!.isNotEmpty) ...[
                const SizedBox(height: 4),
                Text(
                  'Đang chọn: ${_reminders!.map((seconds) => '${seconds ~/ 60} phút').join(', ')}',
                  style: textTheme.bodySmall?.copyWith(
                    color: palette.onSurfaceVariant,
                  ),
                ),
              ],
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 6,
                children: [
                  for (final entry in const {
                    '5 phút': [300],
                    '10 và 5 phút': [600, 300],
                    '15, 10 và 5 phút': [900, 600, 300],
                  }.entries)
                    ChoiceChip(
                      label: Text(entry.key),
                      selected: _reminders?.join(',') == entry.value.join(','),
                      onSelected: _busy
                          ? null
                          : (_) => setState(() => _reminders = entry.value),
                    ),
                ],
              ),
              if (_bufferMultiplier case final buffer?) ...[
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 12),
                  child: Divider(),
                ),
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: Icon(Icons.shield_outlined, color: palette.accentInk),
                  title: Text('Hệ số đệm: ×$buffer'),
                  trailing: const MelbelStatusPill(
                    label: 'Chỉnh',
                    tone: MelbelCardTone.sage,
                  ),
                  onTap: _busy ? null : () => _editBuffer(buffer),
                ),
              ],
            ],
          ),
        ],
        if (streak != null && !streak.isUnlocked)
          MelbelCard(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
            child: SwitchListTile.adaptive(
              contentPadding: EdgeInsets.zero,
              title: const Text('Thẻ nhịp tập trung ở Trang chủ'),
              value: !streak.introDismissed,
              onChanged: (show) =>
                  show ? streak.restoreIntro() : streak.dismissIntro(),
            ),
          ),
        MelbelDisclosure(
          title: 'Giao diện',
          icon: Icons.contrast_rounded,
          summary: switch (theme.appearance) {
            'dark' => 'Tối',
            'light' => 'Sáng',
            _ => 'Tự động theo giờ địa phương',
          },
          children: [
            Wrap(
              spacing: 8,
              runSpacing: 6,
              children: [
                for (final entry in const {
                  'dark': 'Tối',
                  'light': 'Sáng',
                  'system': 'Tự động',
                }.entries)
                  ChoiceChip(
                    label: Text(entry.value),
                    selected: theme.appearance == entry.key,
                    onSelected: (_) => theme.setAppearance(entry.key),
                  ),
              ],
            ),
          ],
        ),
        if (_preferences != null && _changes.isNotEmpty)
          PrimaryCta(
            label: 'Lưu cài đặt',
            icon: Icons.check_rounded,
            onPressed: _busy ? null : _save,
          ),
      ],
    );
  }
}
