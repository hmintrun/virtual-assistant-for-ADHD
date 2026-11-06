import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../providers/task_provider.dart';
import '../services/voice_service.dart';
import '../widgets/melbel_ui.dart';
import 'drafts_screen.dart';
import 'task_summary_screen.dart';

class TaskEntryScreen extends StatefulWidget {
  const TaskEntryScreen({super.key});

  @override
  State<TaskEntryScreen> createState() => _TaskEntryScreenState();
}

class _TaskEntryScreenState extends State<TaskEntryScreen> {
  late final TextEditingController _titleController;
  bool _isNavigating = false;
  bool _isListening = false;
  final OnDeviceSpeechService _speechService = OnDeviceSpeechService();
  String _textBeforeListening = '';

  @override
  void initState() {
    super.initState();
    _titleController = TextEditingController(
      text: context.read<TaskProvider>().inputText,
    );
  }

  @override
  void dispose() {
    _speechService.dispose();
    _titleController.dispose();
    super.dispose();
  }

  void _onQuickDeadline(DateTime deadline) {
    context.read<TaskProvider>().setDeadline(deadline);
  }

  Future<void> _pickDeadline() async {
    final task = context.read<TaskProvider>();
    final now = DateTime.now();
    final current = task.deadline;
    final initial = current != null && current.isAfter(now)
        ? current
        : now.add(const Duration(days: 1));
    final day = await showDatePicker(
        context: context,
        initialDate: initial,
        firstDate: DateTime(now.year, now.month, now.day),
        lastDate: DateTime(9999, 12, 31),
        helpText: 'Chọn ngày hạn chót');
    if (day == null || !mounted) return;
    final time = await showTimePicker(
        context: context, initialTime: TimeOfDay.fromDateTime(initial));
    if (time == null || !mounted) return;
    final deadline =
        DateTime(day.year, day.month, day.day, time.hour, time.minute);
    if (!deadline.isAfter(DateTime.now())) {
      ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Hãy chọn hạn chót trong tương lai.')));
      return;
    }
    task.setDeadline(deadline);
  }

  Future<void> _continue() async {
    final task = context.read<TaskProvider>();
    if (task.isApiBusy || _isNavigating) return;
    if (task.title.trim().isEmpty && _titleController.text.trim().isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Hãy nhập nội dung công việc trước.')),
      );
      return;
    }
    if (_titleController.text.trim().isNotEmpty) {
      task.setTitle(_titleController.text.trim());
    }
    setState(() => _isNavigating = true);
    try {
      await Navigator.push(context,
          MaterialPageRoute<void>(builder: (_) => const AiLoadingScreen()));
    } finally {
      if (mounted) setState(() => _isNavigating = false);
    }
  }

  Future<void> _saveDraft() async {
    final task = context.read<TaskProvider>();
    if (_isNavigating || task.isApiBusy) return;
    if (_titleController.text.trim().isNotEmpty) {
      task.setTitle(_titleController.text.trim());
    } else if (task.title.trim().isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Nhập nội dung trước khi lưu nháp.')),
      );
      return;
    }
    task.saveDraft();
    try {
      await task.saveDraftToBackend();
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Đã lưu nháp an toàn trên thiết bị.'),
          duration: Duration(seconds: 2),
        ),
      );
    }
    if (!mounted) return;
    Navigator.push(
      context,
      MaterialPageRoute<void>(builder: (_) => const DraftSavedScreen()),
    );
  }

  Future<void> _toggleListening() async {
    if (_isListening) {
      await _speechService.stop();
      if (mounted) setState(() => _isListening = false);
      return;
    }

    _textBeforeListening = _titleController.text.trim();
    setState(() => _isListening = true);
    final started = await _speechService.start(
      onWords: (words, isFinal) {
        if (!mounted || words.trim().isEmpty) return;
        final combined = [
          if (_textBeforeListening.isNotEmpty) _textBeforeListening,
          words.trim(),
        ].join(' ');
        _titleController.value = TextEditingValue(
          text: combined,
          selection: TextSelection.collapsed(offset: combined.length),
        );
        if (isFinal) context.read<TaskProvider>().setTitle(combined);
        setState(() {});
      },
      onError: (message) {
        if (!mounted) return;
        setState(() => _isListening = false);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Không nhận được giọng nói: $message')),
        );
      },
    );
    if (!mounted) return;
    if (!started) {
      setState(() => _isListening = false);
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text(
              'Hãy cấp quyền micro và bật nhận diện giọng nói tiếng Việt.'),
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();
    final now = DateTime.now();
    final today18 = DateTime(now.year, now.month, now.day, 18);
    final tomorrow18 = today18.add(const Duration(days: 1));
    final weekend = now.add(Duration(days: (7 - now.weekday) % 7));
    final weekend18 = DateTime(weekend.year, weekend.month, weekend.day, 18);

    return Scaffold(
      appBar: const MelbelAppBar(title: 'Thêm việc mới', showBack: true),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 14, 20, 32),
          children: [
            // 1. Nhập việc bằng chữ hoặc giọng nói
            Container(
              padding: const EdgeInsets.all(20),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainer,
                borderRadius: BorderRadius.circular(16),
                border: Border.all(
                  color: MelbelColors.kOutlineVariant.withValues(alpha: 0.6),
                ),
              ),
              child: Column(
                children: [
                  Text(
                    'Bạn đang muốn hoàn thành việc gì?',
                    textAlign: TextAlign.left,
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 20,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Cứ nói hoặc gõ tự nhiên, mình sẽ giúp sắp xếp thông tin.',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 14,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                  const SizedBox(height: 16),
                  TextField(
                    controller: _titleController,
                    minLines: 3,
                    maxLines: 6,
                    textCapitalization: TextCapitalization.sentences,
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 16,
                      color: MelbelColors.kOnSurface,
                    ),
                    decoration: InputDecoration(
                      hintText:
                          'Ví dụ: Nộp bài trước tối Chủ nhật, rảnh các buổi tối…',
                      hintStyle: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 15,
                        color: MelbelColors.kOnSurfaceVariant
                            .withValues(alpha: 0.7),
                      ),
                      filled: true,
                      fillColor: MelbelColors.kSurfaceContainerLow,
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(12),
                        borderSide:
                            BorderSide(color: MelbelColors.kOutlineVariant),
                      ),
                      focusedBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(12),
                        borderSide:
                            BorderSide(color: MelbelColors.kPrimaryContainer),
                      ),
                    ),
                    onChanged: task.setTitle,
                  ),
                  const SizedBox(height: 16),
                  // Audio Waves + Mic Center Button (76x76)
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      _WaveBars(active: _isListening),
                      const SizedBox(width: 14),
                      GestureDetector(
                        onTap: _toggleListening,
                        child: AnimatedContainer(
                          duration: const Duration(milliseconds: 200),
                          width: 76,
                          height: 76,
                          decoration: BoxDecoration(
                            color: _isListening
                                ? MelbelColors.kSecondaryContainer
                                : MelbelColors.kSurfaceContainerHigh,
                            shape: BoxShape.circle,
                            border: Border.all(
                              color: MelbelColors.kPrimaryContainer,
                              width: 2,
                            ),
                            boxShadow: [
                              BoxShadow(
                                color: MelbelColors.kPrimaryContainer
                                    .withValues(alpha: 0.25),
                                blurRadius: 12,
                                spreadRadius: _isListening ? 4 : 0,
                              ),
                            ],
                          ),
                          child: Icon(
                            Icons.mic,
                            size: 36,
                            color: _isListening
                                ? MelbelColors.kSecondary
                                : MelbelColors.kPrimaryContainer,
                          ),
                        ),
                      ),
                      const SizedBox(width: 14),
                      _WaveBars(active: _isListening),
                    ],
                  ),
                  const SizedBox(height: 12),
                  Text(
                    _isListening
                        ? 'Đang lắng nghe bạn...'
                        : 'Chạm để bắt đầu hoặc dừng nói',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 14,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kPrimaryContainer,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Container(
                        width: 8,
                        height: 8,
                        decoration: BoxDecoration(
                          color: _isListening
                              ? MelbelColors.kPrimaryContainer
                              : MelbelColors.kSecondary,
                          shape: BoxShape.circle,
                        ),
                      ),
                      const SizedBox(width: 6),
                      Text(
                        _isListening
                            ? 'Đang nhận diện giọng nói'
                            : task.deadline == null
                                ? 'Có thể nêu hạn trong câu'
                                : 'Đã có hạn chót',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 12,
                          color: _isListening
                              ? MelbelColors.kPrimaryContainer
                              : MelbelColors.kSecondary,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),

                  // Live Transcription Box
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(14),
                    decoration: BoxDecoration(
                      color: MelbelColors.kSurfaceContainerLow,
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(
                        color: MelbelColors.kOutlineVariant.withValues(
                          alpha: 0.4,
                        ),
                      ),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            Row(
                              children: [
                                Icon(
                                  Icons.record_voice_over,
                                  size: 14,
                                  color: MelbelColors.kPrimaryContainer,
                                ),
                                SizedBox(width: 6),
                                Text(
                                  'Nội dung vừa nghe được:',
                                  style: TextStyle(
                                    fontFamily: 'Be Vietnam Pro',
                                    fontSize: 11,
                                    color: MelbelColors.kOnSurfaceVariant,
                                    fontWeight: FontWeight.w500,
                                  ),
                                ),
                              ],
                            ),
                            if (task.title.isNotEmpty ||
                                _titleController.text.isNotEmpty)
                              InkWell(
                                onTap: () {
                                  task.setTitle('');
                                  _titleController.clear();
                                  setState(() {});
                                },
                                child: Row(
                                  children: [
                                    Icon(
                                      Icons.close,
                                      size: 13,
                                      color: MelbelColors.kOnSurfaceVariant,
                                    ),
                                    SizedBox(width: 2),
                                    Text(
                                      'Xóa',
                                      style: TextStyle(
                                        fontFamily: 'Be Vietnam Pro',
                                        fontSize: 11,
                                        color: MelbelColors.kOnSurfaceVariant,
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                          ],
                        ),
                        const SizedBox(height: 6),
                        Text(
                          task.title.isEmpty && _titleController.text.isEmpty
                              ? 'Chưa có nội dung công việc'
                              : '“${_titleController.text.isEmpty ? task.title : _titleController.text}”',
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 14,
                            fontWeight: FontWeight.w500,
                            fontStyle: FontStyle.italic,
                            color: MelbelColors.kOnSurface,
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 12),
                ],
              ),
            ),
            const SizedBox(height: 16),

            // 3. Helper Card: Melbel AI Companion (Gentle support, no red errors)
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainer,
                borderRadius: BorderRadius.circular(16),
                border: Border.all(
                  color: MelbelColors.kOutlineVariant.withValues(alpha: 0.6),
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Container(
                        width: 36,
                        height: 36,
                        decoration: BoxDecoration(
                          color: MelbelColors.kSecondaryContainer,
                          shape: BoxShape.circle,
                          border: Border.all(
                            color: MelbelColors.kSecondary.withValues(
                              alpha: 0.4,
                            ),
                          ),
                        ),
                        child: Icon(
                          Icons.auto_awesome,
                          size: 20,
                          color: MelbelColors.kSecondary,
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              mainAxisAlignment: MainAxisAlignment.spaceBetween,
                              children: [
                                Text(
                                  'Melbel AI Đồng hành',
                                  style: TextStyle(
                                    fontFamily: 'Be Vietnam Pro',
                                    fontSize: 13,
                                    fontWeight: FontWeight.w600,
                                    color: MelbelColors.kOnSurface,
                                  ),
                                ),
                                Container(
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: 8,
                                    vertical: 2,
                                  ),
                                  decoration: BoxDecoration(
                                    color: MelbelColors.kSurfaceContainerHigh,
                                    borderRadius: BorderRadius.circular(999),
                                    border: Border.all(
                                      color: MelbelColors.kOutlineVariant
                                          .withValues(alpha: 0.4),
                                    ),
                                  ),
                                  child: Row(
                                    children: [
                                      Icon(
                                        Icons.hourglass_empty,
                                        size: 12,
                                        color: MelbelColors.kTertiaryFixedDim,
                                      ),
                                      SizedBox(width: 4),
                                      Text(
                                        task.deadline == null
                                            ? 'Hạn có thể nằm trong câu'
                                            : 'Đã nhận hạn chót',
                                        style: TextStyle(
                                          fontFamily: 'Be Vietnam Pro',
                                          fontSize: 11,
                                          color: MelbelColors.kTertiaryFixedDim,
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                              ],
                            ),
                            const SizedBox(height: 8),
                            Container(
                              padding: const EdgeInsets.all(12),
                              decoration: BoxDecoration(
                                color: MelbelColors.kSurfaceContainerLow,
                                borderRadius: BorderRadius.circular(12),
                                border: Border.all(
                                  color: MelbelColors.kOutlineVariant
                                      .withValues(alpha: 0.3),
                                ),
                              ),
                              child: Text(
                                'Cứ nói hoặc gõ tự nhiên, kể cả ngày cần xong và lúc bạn rảnh. Mình sẽ gom thông tin để bạn xem lại.',
                                style: TextStyle(
                                  fontFamily: 'Be Vietnam Pro',
                                  fontSize: 13,
                                  color: MelbelColors.kOnSurface,
                                  height: 1.4,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 10,
                          vertical: 4,
                        ),
                        decoration: BoxDecoration(
                          color: MelbelColors.kSurfaceContainerHigh,
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(
                            color: MelbelColors.kOutlineVariant.withValues(
                              alpha: 0.4,
                            ),
                          ),
                        ),
                        child: Row(
                          children: [
                            Icon(
                              Icons.check_circle,
                              size: 15,
                              color: MelbelColors.kSecondary,
                            ),
                            SizedBox(width: 6),
                            Text(
                              'Đã nhận nội dung việc',
                              style: TextStyle(
                                fontFamily: 'Be Vietnam Pro',
                                fontSize: 12,
                                fontWeight: FontWeight.w500,
                                color: MelbelColors.kOnSurface,
                              ),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(width: 8),
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 10,
                          vertical: 4,
                        ),
                        decoration: BoxDecoration(
                          color: MelbelColors.kPrimaryContainer.withValues(
                            alpha: 0.1,
                          ),
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(
                            color: MelbelColors.kPrimaryContainer.withValues(
                              alpha: 0.4,
                            ),
                          ),
                        ),
                        child: Row(
                          children: [
                            Icon(
                              Icons.mic,
                              size: 15,
                              color: MelbelColors.kPrimaryContainer,
                            ),
                            SizedBox(width: 6),
                            Text(
                              'Nói hoặc gõ tự nhiên',
                              style: TextStyle(
                                fontFamily: 'Be Vietnam Pro',
                                fontSize: 12,
                                fontWeight: FontWeight.w500,
                                color: MelbelColors.kPrimaryContainer,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),

            // Optional deadline shortcuts (Warm tone, zero red alarm)
            Container(
              padding: const EdgeInsets.all(20),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainer,
                borderRadius: BorderRadius.circular(16),
                border: Border.all(
                  color: MelbelColors.kPrimaryContainer.withValues(alpha: 0.6),
                  width: 2,
                ),
                boxShadow: [
                  BoxShadow(
                    color: Colors.black.withValues(alpha: 0.2),
                    blurRadius: 8,
                    offset: const Offset(0, 2),
                  ),
                ],
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Row(
                        children: [
                          Icon(
                            Icons.schedule,
                            size: 18,
                            color: MelbelColors.kTertiaryFixedDim,
                          ),
                          SizedBox(width: 8),
                          Eyebrow(
                            'Hạn chót công việc',
                            color: MelbelColors.kTertiaryFixedDim,
                          ),
                        ],
                      ),
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 10,
                          vertical: 2,
                        ),
                        decoration: BoxDecoration(
                          color: MelbelColors.kSurfaceContainerHigh,
                          borderRadius: BorderRadius.circular(999),
                          border: Border.all(
                            color: MelbelColors.kOutlineVariant.withValues(
                              alpha: 0.4,
                            ),
                          ),
                        ),
                        child: Row(
                          children: [
                            Icon(
                              Icons.info_outline,
                              size: 13,
                              color: MelbelColors.kTertiaryFixedDim,
                            ),
                            const SizedBox(width: 4),
                            Text(
                              task.deadline == null ? 'Chưa đặt' : 'Đã chọn',
                              style: TextStyle(
                                fontFamily: 'Be Vietnam Pro',
                                fontSize: 11,
                                fontWeight: FontWeight.w500,
                                color: MelbelColors.kOnSurfaceVariant,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text(
                    task.deadline == null
                        ? 'Chưa có hạn chót'
                        : task.formatDeadline(task.deadline),
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 22,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kTertiaryFixedDim,
                      letterSpacing: -0.3,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    'Việc này cần xong trước lúc nào?',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 13,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                  const SizedBox(height: 12),
                  Divider(
                    color: MelbelColors.kOutlineVariant,
                    thickness: 0.5,
                  ),
                  const SizedBox(height: 8),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text(
                        'Chọn nhanh hạn chót:',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 12,
                          color: MelbelColors.kOnSurfaceVariant,
                        ),
                      ),
                      Text(
                        '1 chạm',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 11,
                          fontWeight: FontWeight.w500,
                          color: MelbelColors.kSecondary,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      _QuickChip(
                        label: '18:00 hôm nay',
                        isSelected: task.deadline == today18,
                        onTap: () => _onQuickDeadline(today18),
                      ),
                      _QuickChip(
                        label: '18:00 ngày mai',
                        isSelected: task.deadline == tomorrow18,
                        onTap: () => _onQuickDeadline(tomorrow18),
                      ),
                      _QuickChip(
                        label: 'Cuối tuần',
                        isSelected: task.deadline == weekend18,
                        onTap: () => _onQuickDeadline(weekend18),
                      ),
                    ],
                  ),
                  TextButton.icon(
                      onPressed: task.isApiBusy ? null : _pickDeadline,
                      icon: const Icon(Icons.edit_calendar_outlined),
                      label: const Text('Chọn ngày và giờ khác')),
                ],
              ),
            ),
            const SizedBox(height: 16),

            // 4. Bottom Action Buttons
            PrimaryCta(
              label: 'Phân tích công việc',
              icon: Icons.arrow_forward,
              onPressed: task.isApiBusy || _isNavigating ? null : _continue,
            ),
            const SizedBox(height: 10),
            SecondaryCta(
              label: 'Lưu vào Hộp nháp',
              icon: Icons.drafts_outlined,
              height: 48,
              onPressed: task.isApiBusy ? null : _saveDraft,
            ),
          ],
        ),
      ),
    );
  }
}

class _WaveBars extends StatelessWidget {
  const _WaveBars({required this.active});
  final bool active;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        _bar(12),
        const SizedBox(width: 4),
        _bar(24),
        const SizedBox(width: 4),
        _bar(36),
        const SizedBox(width: 4),
        _bar(20),
      ],
    );
  }

  Widget _bar(double height) {
    return Container(
      width: 4,
      height: active ? height : height * 0.5,
      decoration: BoxDecoration(
        color: MelbelColors.kPrimaryContainer.withValues(alpha: 0.6),
        borderRadius: BorderRadius.circular(999),
      ),
    );
  }
}

class _QuickChip extends StatelessWidget {
  const _QuickChip({
    required this.label,
    required this.isSelected,
    required this.onTap,
  });

  final String label;
  final bool isSelected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(8),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
        decoration: BoxDecoration(
          color: isSelected
              ? MelbelColors.kPrimaryContainer.withValues(alpha: 0.2)
              : MelbelColors.kSurfaceContainerHigh,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(
            color: isSelected
                ? MelbelColors.kPrimaryContainer
                : MelbelColors.kOutlineVariant.withValues(alpha: 0.5),
          ),
        ),
        child: Text(
          label,
          style: TextStyle(
            fontFamily: 'Be Vietnam Pro',
            fontSize: 12,
            fontWeight: isSelected ? FontWeight.w600 : FontWeight.w400,
            color: isSelected
                ? MelbelColors.kPrimaryContainer
                : MelbelColors.kOnSurface,
          ),
        ),
      ),
    );
  }
}

// -------------------------------------------------------------
// D-16 AI Loading Screen (Exact Stitch 120x100 mascot + skeleton)
// -------------------------------------------------------------
class AiLoadingScreen extends StatefulWidget {
  const AiLoadingScreen({super.key});

  @override
  State<AiLoadingScreen> createState() => _AiLoadingScreenState();
}

class _AiLoadingScreenState extends State<AiLoadingScreen> {
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _analyze());
  }

  Future<void> _analyze() async {
    setState(() => _error = null);
    try {
      await context.read<TaskProvider>().prepareDraftAndAnalyze();
      if (!mounted) return;
      Navigator.pushReplacement(
        context,
        MaterialPageRoute<void>(builder: (_) => const TaskSummaryScreen()),
      );
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) {
      return Scaffold(
        appBar: const MelbelAppBar(title: 'Chưa kết nối được', showBack: true),
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(
                  Icons.cloud_off_outlined,
                  size: 42,
                  color: MelbelColors.kOnSurfaceVariant,
                ),
                const SizedBox(height: 16),
                const Text(
                  'Chưa gửi được công việc lên máy chủ.',
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 8),
                Text(_error!, textAlign: TextAlign.center),
                const SizedBox(height: 20),
                PrimaryCta(label: 'Thử kết nối lại', onPressed: _analyze),
                const SizedBox(height: 12),
                SecondaryCta(
                  label: 'Tiếp tục ngoại tuyến',
                  icon: Icons.offline_bolt_outlined,
                  onPressed: () {
                    Navigator.pushReplacement(
                      context,
                      MaterialPageRoute<void>(
                        builder: (_) => const TaskSummaryScreen(),
                      ),
                    );
                  },
                ),
              ],
            ),
          ),
        ),
      );
    }
    return Scaffold(
      backgroundColor: MelbelColors.kSurface,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 20),
          child: Column(
            children: [
              // Top Calm Status Bar
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Row(
                    children: [
                      Container(
                        width: 8,
                        height: 8,
                        decoration: BoxDecoration(
                          color: MelbelColors.kSecondary,
                          shape: BoxShape.circle,
                        ),
                      ),
                      const SizedBox(width: 6),
                      Text(
                        'Yên tĩnh',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 12,
                          color: MelbelColors.kOnSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                  Text(
                    'Trăng đen',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 12,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                ],
              ),
              const Spacer(),

              // Center Mascot (120x100px rounded blob with narrowed oval eyes)
              Container(
                width: 120,
                height: 100,
                decoration: BoxDecoration(
                  color: const Color(0xFF2A2F42),
                  borderRadius: const BorderRadius.all(
                    Radius.elliptical(60, 50),
                  ),
                  border: Border.all(color: const Color(0xFF3A4160)),
                ),
                child: Center(
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Container(
                        width: 16,
                        height: 5,
                        decoration: BoxDecoration(
                          color: MelbelColors.kPrimaryContainer,
                          borderRadius: BorderRadius.circular(2.5),
                        ),
                      ),
                      const SizedBox(width: 16),
                      Container(
                        width: 16,
                        height: 5,
                        decoration: BoxDecoration(
                          color: MelbelColors.kPrimaryContainer,
                          borderRadius: BorderRadius.circular(2.5),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 20),

              // Calm Reassuring Message
              Text(
                'AI đang bẻ nhỏ công việc giúp bạn...',
                textAlign: TextAlign.center,
                style: TextStyle(
                  fontFamily: 'Be Vietnam Pro',
                  fontSize: 17,
                  fontWeight: FontWeight.w500,
                  color: MelbelColors.kPrimary,
                  height: 1.5,
                ),
              ),
              const SizedBox(height: 6),
              Text(
                'Cứ thong thả, từng chút một',
                textAlign: TextAlign.center,
                style: TextStyle(
                  fontFamily: 'Be Vietnam Pro',
                  fontSize: 14,
                  color: MelbelColors.kOnSurfaceVariant,
                ),
              ),
              const SizedBox(height: 28),

              // Subtask Skeleton Placeholders (Static, sensory-friendly)
              Container(
                width: 280,
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: MelbelColors.kSurfaceContainer.withValues(alpha: 0.4),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(
                    color: MelbelColors.kOutlineVariant.withValues(alpha: 0.3),
                  ),
                ),
                child: Column(
                  children: [
                    _skeletonRow(0.8),
                    const SizedBox(height: 10),
                    _skeletonRow(0.6),
                    const SizedBox(height: 10),
                    _skeletonRow(0.4),
                  ],
                ),
              ),
              const Spacer(),

              // Bottom Sanctuary Reassurance
              Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 8,
                ),
                decoration: BoxDecoration(
                  color: MelbelColors.kSurfaceContainerLow,
                  borderRadius: BorderRadius.circular(999),
                  border: Border.all(
                    color: MelbelColors.kOutlineVariant.withValues(alpha: 0.2),
                  ),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.spa, size: 16, color: MelbelColors.kSecondary),
                    SizedBox(width: 6),
                    Text(
                      'Không áp lực thời gian',
                      style: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 12,
                        color: MelbelColors.kOnSurfaceVariant,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 16),
            ],
          ),
        ),
      ),
    );
  }

  Widget _skeletonRow(double widthFactor) {
    return Row(
      children: [
        Container(
          width: 16,
          height: 16,
          decoration: BoxDecoration(
            color: MelbelColors.kSurfaceContainer,
            borderRadius: BorderRadius.circular(4),
            border: Border.all(
              color: MelbelColors.kOutlineVariant.withValues(alpha: 0.5),
            ),
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: Align(
            alignment: Alignment.centerLeft,
            child: FractionallySizedBox(
              widthFactor: widthFactor,
              child: Container(
                height: 12,
                decoration: BoxDecoration(
                  color: MelbelColors.kSurfaceContainer,
                  borderRadius: BorderRadius.circular(999),
                  border: Border.all(
                    color: MelbelColors.kOutlineVariant.withValues(alpha: 0.5),
                  ),
                ),
              ),
            ),
          ),
        ),
      ],
    );
  }
}
