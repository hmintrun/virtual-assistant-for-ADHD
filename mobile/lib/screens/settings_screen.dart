import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/api_client.dart';
import '../providers/theme_provider.dart';
import '../widgets/melbel_ui.dart';
import 'focus_review_screens.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _api = ApiClient();
  Map<String, dynamic>? _preferences;
  int? _work;
  int? _rest;
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
        _reminders = (data['reminder_offsets'] as List?)?.cast<int>();
        _reminderStyle = data['reminder_style'] as String?;
      });
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Map<String, dynamic> get _changes {
    final current = _preferences;
    if (current == null) return const {};
    return {
      if (_work != null && _work != current['work_interval_seconds'])
        'work_interval_seconds': _work,
      if (_rest != null && _rest != current['rest_interval_seconds'])
        'rest_interval_seconds': _rest,
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
    final revision = _preferences!['revision'];
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
      await _load();
      if (!mounted) return;
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
      if (!mounted) return;
      setState(() => _error = error.message);
      if (error.code == 'POLICY_NOT_CONFIGURED') {
        await Navigator.of(context).push(MaterialPageRoute<void>(
          builder: (context) => FocusReviewPage(
            title: 'Chia lại phiên',
            children: [
              const ReviewHeader('Lịch cần được xem lại',
                  'Thay đổi thời lượng có thể ảnh hưởng các phiên đã chốt.'),
              MelbelCard(
                  child: Text(
                      'Đang áp dụng: ${(_preferences?['work_interval_seconds'] as int? ?? 0) ~/ 60} phút làm · ${(_preferences?['rest_interval_seconds'] as int? ?? 0) ~/ 60} phút nghỉ')),
              MelbelCard(
                  child: Text(
                      'Bạn chọn: ${(_work ?? 0) ~/ 60} phút làm · ${(_rest ?? 0) ~/ 60} phút nghỉ')),
              const Text(
                  'Chưa thể tạo phương án. Lịch và cấu hình hiện tại vẫn được giữ.'),
              PrimaryCta(
                  label: 'Về cài đặt', onPressed: () => Navigator.pop(context)),
            ],
          ),
        ));
      }
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
    final pending = _preferences?['pending_proposal_id'] != null;
    return FocusReviewPage(
      title: 'Cài đặt',
      children: [
        const ReviewHeader(
            'Nhịp làm việc của bạn', 'Cấu hình mới áp dụng từ phiên sau.'),
        if (_busy) const LinearProgressIndicator(),
        if (_error != null) Text(_error!),
        if (_preferences == null && !_busy)
          SecondaryCta(label: 'Tải lại cài đặt', onPressed: _load),
        if (_preferences case final preferences?) ...[
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
                if (preferences['buffer_multiplier'] case final num buffer)
                  Text('Hệ số đệm đang dùng: ×$buffer'),
              ],
            ),
          ),
          if (pending)
            const MelbelCard(
                child: Text(
                    'Có thay đổi đang chờ duyệt. Cấu hình hiệu lực vẫn được giữ.')),
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
