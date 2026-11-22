import 'package:flutter/material.dart';

import '../core/theme.dart';
import 'melbel_ui.dart';

class WeeklyAvailabilityEditor extends StatefulWidget {
  const WeeklyAvailabilityEditor({
    super.key,
    required this.initialRules,
    required this.onSave,
  });

  final List<Map<String, dynamic>> initialRules;
  final Future<void> Function(List<Map<String, dynamic>> rules) onSave;

  @override
  State<WeeklyAvailabilityEditor> createState() =>
      _WeeklyAvailabilityEditorState();
}

class _WeeklyAvailabilityEditorState extends State<WeeklyAvailabilityEditor> {
  static const _weekdayNames = <int, String>{
    1: 'Thứ 2',
    2: 'Thứ 3',
    3: 'Thứ 4',
    4: 'Thứ 5',
    5: 'Thứ 6',
    6: 'Thứ 7',
    7: 'Chủ nhật',
  };

  late final List<Map<String, dynamic>> _rules;
  bool _isSaving = false;

  @override
  void initState() {
    super.initState();
    _rules = widget.initialRules
        .map((rule) => Map<String, dynamic>.from(rule))
        .toList();
  }

  String _timeLabel(Object? value) => value is String ? value : '--:--';

  Future<void> _editRule({int? index}) async {
    final existing = index == null ? null : _rules[index];
    var weekday = existing?['weekday'] as int? ?? DateTime.monday;
    var start = _parseTime(existing?['start_local'] as String?) ??
        const TimeOfDay(hour: 9, minute: 0);
    var end = _parseTime(existing?['end_local'] as String?) ??
        const TimeOfDay(hour: 10, minute: 0);
    final rule = await showModalBottomSheet<Map<String, dynamic>>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (sheetContext) => StatefulBuilder(
        builder: (context, setSheetState) => Padding(
          padding: EdgeInsets.fromLTRB(
            20,
            8,
            20,
            MediaQuery.viewInsetsOf(context).bottom + 20,
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                existing == null ? 'Thêm giờ rảnh' : 'Sửa giờ rảnh',
                style: TextStyle(
                  color: MelbelColors.kOnSurface,
                  fontSize: 20,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(height: 14),
              DropdownButtonFormField<int>(
                initialValue: weekday,
                decoration: const InputDecoration(labelText: 'Ngày trong tuần'),
                items: _weekdayNames.entries
                    .map((entry) => DropdownMenuItem<int>(
                          value: entry.key,
                          child: Text(entry.value),
                        ))
                    .toList(),
                onChanged: (value) {
                  if (value != null) setSheetState(() => weekday = value);
                },
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: () async {
                        final value = await showMelbelTimePicker(
                          context: context,
                          initialTime: start,
                          helpText: 'Giờ bắt đầu rảnh',
                          confirmText: 'Chọn giờ',
                        );
                        if (value != null) setSheetState(() => start = value);
                      },
                      icon: const Icon(Icons.access_time, size: 18),
                      label: Text('Từ ${_formatTime(start)}'),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: () async {
                        final value = await showMelbelTimePicker(
                          context: context,
                          initialTime: end,
                          helpText: 'Giờ kết thúc rảnh',
                          confirmText: 'Chọn giờ',
                        );
                        if (value != null) setSheetState(() => end = value);
                      },
                      icon: const Icon(Icons.access_time_filled, size: 18),
                      label: Text('Đến ${_formatTime(end)}'),
                    ),
                  ),
                ],
              ),
              if (_minutes(start) >= _minutes(end)) ...[
                const SizedBox(height: 8),
                Text(
                  'Giờ kết thúc cần sau giờ bắt đầu. Khoảng qua đêm hãy tách thành hai ngày.',
                  style: TextStyle(
                    color: MelbelColors.kTertiaryFixedDim,
                    fontSize: 12,
                  ),
                ),
              ],
              const SizedBox(height: 16),
              PrimaryCta(
                label: 'Lưu khung giờ',
                onPressed: _minutes(start) >= _minutes(end)
                    ? null
                    : () => Navigator.pop(sheetContext, {
                          'weekday': weekday,
                          'start_local': _formatTime(start),
                          'end_local': _formatTime(end),
                        }),
              ),
            ],
          ),
        ),
      ),
    );
    if (rule == null || !mounted) return;
    setState(() {
      if (index == null) {
        _rules.add(rule);
      } else {
        _rules[index] = rule;
      }
      _sortRules();
    });
  }

  Future<void> _save() async {
    if (_isSaving) return;
    setState(() => _isSaving = true);
    try {
      await widget.onSave(List<Map<String, dynamic>>.unmodifiable(_rules));
      if (mounted) Navigator.pop(context);
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Chưa cập nhật được giờ rảnh: $error')),
      );
    } finally {
      if (mounted) setState(() => _isSaving = false);
    }
  }

  void _sortRules() {
    _rules.sort((left, right) {
      final dayCompare =
          (left['weekday'] as int).compareTo(right['weekday'] as int);
      return dayCompare != 0
          ? dayCompare
          : (left['start_local'] as String)
              .compareTo(right['start_local'] as String);
    });
  }

  static int _minutes(TimeOfDay time) => time.hour * 60 + time.minute;

  static String _formatTime(TimeOfDay time) =>
      '${time.hour.toString().padLeft(2, '0')}:${time.minute.toString().padLeft(2, '0')}';

  static TimeOfDay? _parseTime(String? value) {
    final match = RegExp(r'^(\d{1,2}):(\d{2})$').firstMatch(value ?? '');
    if (match == null) return null;
    final hour = int.tryParse(match.group(1)!);
    final minute = int.tryParse(match.group(2)!);
    if (hour == null || minute == null || hour > 23 || minute > 59) {
      return null;
    }
    return TimeOfDay(hour: hour, minute: minute);
  }

  @override
  Widget build(BuildContext context) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Giờ rảnh hằng tuần',
                style: TextStyle(
                  color: MelbelColors.kOnSurface,
                  fontSize: 20,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                'Khoảng giờ này áp dụng cho lịch của bạn. Lịch bận từ Calendar vẫn được kiểm tra riêng.',
                style: TextStyle(
                  color: MelbelColors.kOnSurfaceVariant,
                  height: 1.35,
                ),
              ),
              const SizedBox(height: 12),
              if (_rules.isEmpty)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 10),
                  child: Text(
                    'Chưa có khung giờ rảnh nào.',
                    style: TextStyle(color: MelbelColors.kOnSurfaceVariant),
                  ),
                ),
              Flexible(
                child: ListView.builder(
                  shrinkWrap: true,
                  itemCount: _rules.length,
                  itemBuilder: (context, index) {
                    final rule = _rules[index];
                    final weekday = rule['weekday'] as int?;
                    return ListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text(
                        '${_weekdayNames[weekday] ?? 'Ngày khác'} · ${_timeLabel(rule['start_local'])}–${_timeLabel(rule['end_local'])}',
                        style: TextStyle(color: MelbelColors.kOnSurface),
                      ),
                      trailing: Wrap(
                        spacing: 0,
                        children: [
                          IconButton(
                            tooltip: 'Sửa khung giờ',
                            onPressed: () => _editRule(index: index),
                            icon: const Icon(Icons.edit_outlined),
                          ),
                          IconButton(
                            tooltip: 'Xóa khung giờ',
                            onPressed: () {
                              setState(() => _rules.removeAt(index));
                              ScaffoldMessenger.of(context).showSnackBar(
                                SnackBar(
                                  content: const Text('Đã xóa khung giờ'),
                                  duration: const Duration(seconds: 5),
                                  action: SnackBarAction(
                                    label: 'Hoàn tác',
                                    onPressed: () {
                                      if (!mounted) return;
                                      setState(() => _rules.insert(
                                          index.clamp(0, _rules.length), rule));
                                    },
                                  ),
                                ),
                              );
                            },
                            icon: const Icon(Icons.remove_circle_outline),
                          ),
                        ],
                      ),
                    );
                  },
                ),
              ),
              const SizedBox(height: 8),
              TextButton.icon(
                onPressed: () => _editRule(),
                icon: const Icon(Icons.add),
                label: const Text('Thêm khung giờ'),
              ),
              const SizedBox(height: 8),
              PrimaryCta(
                label: _isSaving ? 'Đang lưu…' : 'Lưu giờ rảnh',
                onPressed: _isSaving ? null : _save,
              ),
            ],
          ),
        ),
      );
}
