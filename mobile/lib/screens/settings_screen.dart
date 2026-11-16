import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/api_client.dart';
import '../providers/theme_provider.dart';
import '../services/adaptation_api.dart';
import '../widgets/melbel_ui.dart';
import 'focus_review_screens.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _api = ApiClient();
  late final _adaptation = AdaptationApi(_api);
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

  Future<void> _respond(String action, {Object? editedValue}) async {
    final suggestion = _suggestion;
    final revision = _effectiveRevision;
    if (_busy || suggestion == null || revision == null) return;
    setState(() => _busy = true);
    try {
      await _adaptation.respond(suggestion, revision, action,
          editedValue: editedValue);
      if (mounted) await _load();
    } on ApiException catch (error) {
      if (mounted) await _handleCommandError(error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _reviewProposal(String id) async {
    await Navigator.of(context).push<bool>(MaterialPageRoute(
      builder: (_) => ScheduleComparisonScreen(
        reason: 'Thay đổi cài đặt',
        proposalId: id,
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
    final controller = TextEditingController(
      text: value is List
          ? value.whereType<int>().map((s) => s ~/ 60).join(', ')
          : '$value',
    );
    final result = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      builder: (sheet) => SafeArea(
          child: Padding(
        padding: EdgeInsets.fromLTRB(
            20, 20, 20, 20 + MediaQuery.viewInsetsOf(sheet).bottom),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          TextField(
            controller: controller,
            decoration: InputDecoration(
                labelText: value is List
                    ? 'Phút, cách nhau bằng dấu phẩy'
                    : 'Hệ số đệm'),
          ),
          const SizedBox(height: 12),
          PrimaryCta(
              label: 'Áp dụng',
              onPressed: () => Navigator.pop(sheet, controller.text.trim())),
        ]),
      )),
    );
    WidgetsBinding.instance.addPostFrameCallback((_) => controller.dispose());
    return result;
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
      final minutes =
          result.split(',').map((v) => int.tryParse(v.trim())).toList();
      if (minutes.every((v) => v != null && v >= 0)) {
        parsed = minutes.map((v) => v! * 60).toList();
      }
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
        idempotencyKey:
            ApiClient.stableIdempotencyKey('preferences', payload.toString()),
      );
      if (!mounted) return;
      _pendingChange = result;
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
        idempotencyKey: ApiClient.stableIdempotencyKey(path, '$revision'),
      );
      _pendingChange = null;
      if (mounted) await _load();
    } on ApiException catch (error) {
      if (mounted) await _handleCommandError(error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _minutes(String label, List<int> options, int? selected,
      ValueChanged<int> choose) {
    final values = <int>{
      ...options.map((minutes) => minutes * 60),
      if (selected != null) selected
    };
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label),
        Wrap(
          spacing: 8,
          children: [
            for (final seconds in values)
              ChoiceChip(
                label: Text(seconds % 60 == 0
                    ? '${seconds ~/ 60} phút'
                    : '$seconds giây'),
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
    final pending = _preferences?['pending_proposal_id'] as String?;
    final change =
        pending != null && pending == _pendingChange?['pending_proposal_id']
            ? _pendingChange
            : null;
    return FocusReviewPage(
      title: 'Cài đặt',
      children: [
        const ReviewHeader(
            'Nhịp làm việc của bạn', 'Cấu hình mới áp dụng từ phiên sau.'),
        if (_busy) const LinearProgressIndicator(),
        if (_error != null) Text(_error!),
        if (_preferences == null && !_busy)
          SecondaryCta(label: 'Tải lại cài đặt', onPressed: _load),
        if (_preferences != null) ...[
          MelbelCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Giọng nhắc',
                    style: Theme.of(context).textTheme.titleMedium),
                Wrap(
                  spacing: 8,
                  children: [
                    for (final entry in const {
                      'gentle': 'Nhẹ nhàng',
                      'upbeat': 'Tươi vui',
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
                if (_bufferMultiplier case final buffer?)
                  TextButton(
                    onPressed: _busy ? null : () => _editBuffer(buffer),
                    child: Text('Hệ số đệm: ×$buffer'),
                  ),
              ],
            ),
          ),
          if (pending != null)
            SecondaryCta(
                label: 'Xem thay đổi chờ duyệt',
                onPressed: _busy ? null : () => _reviewProposal(pending)),
          if (change?['change_request_id'] case final String id)
            if (change?['request_revision'] case final int revision)
              TextButton(
                onPressed: _busy ? null : () => _cancelChange(id, revision),
                child: const Text('Hủy yêu cầu đổi cài đặt'),
              ),
          if (_suggestionError != null) Text(_suggestionError!),
          if (_suggestion case final suggestion?)
            MelbelCard(
                child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Gợi ý cho phiên sau',
                    style: Theme.of(context).textTheme.titleMedium),
                Text(
                    '${_suggestionValue(suggestion['current_value'])} → ${_suggestionValue(suggestion['suggested_value'])}'),
                if (suggestion['reason'] case final String reason) Text(reason),
                Wrap(spacing: 8, children: [
                  TextButton(
                      onPressed: _busy ? null : () => _respond('accept'),
                      child: const Text('Áp dụng')),
                  if (suggestion['suggested_value'] case final Object value)
                    TextButton(
                        onPressed: _busy ? null : () => _editSuggestion(value),
                        child: const Text('Tùy chỉnh')),
                  TextButton(
                      onPressed: _busy ? null : () => _respond('reject'),
                      child: const Text('Bỏ qua')),
                ]),
              ],
            )),
          MelbelCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _minutes('Tập trung', [15, 25, 40], _work,
                    (value) => setState(() => _work = value)),
                const SizedBox(height: 8),
                _minutes('Nghỉ', [0, 5, 10], _rest,
                    (value) => setState(() => _rest = value)),
              ],
            ),
          ),
          MelbelCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Mốc nhắc trước phiên'),
                if (_reminders != null && _reminders!.isNotEmpty)
                  Text(
                      'Đang chọn: ${_reminders!.map((seconds) => '${seconds ~/ 60} phút').join(', ')}'),
                Wrap(spacing: 8, children: [
                  for (final entry in const {
                    'Ít': [300],
                    'Vừa': [600, 300],
                    'Nhiều': [900, 600, 300],
                  }.entries)
                    ChoiceChip(
                      label: Text(entry.key),
                      selected: _reminders?.join(',') == entry.value.join(','),
                      onSelected: _busy
                          ? null
                          : (_) => setState(() => _reminders = entry.value),
                    ),
                ]),
              ],
            ),
          ),
        ],
        MelbelCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Giao diện'),
              Wrap(spacing: 8, children: [
                for (final entry in const {
                  'dark': 'Tối',
                  'light': 'Sáng',
                  'system': 'Tự động'
                }.entries)
                  ChoiceChip(
                    label: Text(entry.value),
                    selected: theme.appearance == entry.key,
                    onSelected: (_) => theme.setAppearance(entry.key),
                  ),
              ]),
            ],
          ),
        ),
        if (_preferences != null)
          PrimaryCta(
              label: 'Lưu cài đặt',
              onPressed: _busy || _changes.isEmpty ? null : _save),
      ],
    );
  }
}
