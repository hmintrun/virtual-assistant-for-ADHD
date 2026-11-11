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
  static const _voiceFailureLimit = 3;
  int _voiceFailures = 0;
  bool _showTextEntry = false;
  bool _startingSpeech = false;
  String _recognizedWords = '';
  final OnDeviceSpeechService _speechService = OnDeviceSpeechService();
  String _textBeforeListening = '';

  @override
  void initState() {
    super.initState();
    _titleController = TextEditingController(
      text: context.read<TaskProvider>().inputText,
    );
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _speechService.requestAndroidPermission();
    });
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
        ? current.toLocal()
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
    task.fillExplicitPlanningInput();
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

  void _finishSpeechAttempt({bool failed = false}) {
    if (!mounted || !_isListening) return;
    final words = _recognizedWords.trim();
    final success = !failed && words.isNotEmpty;
    setState(() {
      _isListening = false;
      if (success) {
        _voiceFailures = 0;
        final combined = [_textBeforeListening, words]
            .where((part) => part.isNotEmpty)
            .join(' ');
        _titleController.value = TextEditingValue(
          text: combined,
          selection: TextSelection.collapsed(offset: combined.length),
        );
        context.read<TaskProvider>().setTitle(combined);
      } else {
        _voiceFailures++;
        if (_voiceFailures >= _voiceFailureLimit) _showTextEntry = true;
      }
    });
    if (success) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(
        content: Text(_showTextEntry
            ? 'Bạn có thể nhập văn bản.'
            : 'Chưa nhận được giọng nói ($_voiceFailures/3).'),
      ));
  }

  Future<void> _toggleListening() async {
    if (_startingSpeech) return;
    if (_isListening) {
      _startingSpeech = true;
      _finishSpeechAttempt();
      try {
        await _speechService.stop().timeout(const Duration(seconds: 2));
      } catch (_) {
        try {
          await _speechService.cancel().timeout(const Duration(seconds: 2));
        } catch (_) {}
      } finally {
        if (mounted) setState(() => _startingSpeech = false);
      }
      return;
    }

    _textBeforeListening = _titleController.text.trim();
    _recognizedWords = '';
    _startingSpeech = true;
    setState(() => _isListening = true);
    try {
      final started = await _speechService.start(
        onWords: (words, isFinal) {
          if (!mounted || !_isListening) return;
          if (words.trim().isNotEmpty) _recognizedWords = words;
          if (isFinal) _finishSpeechAttempt();
        },
        onError: (_) => _finishSpeechAttempt(failed: true),
        onDone: _finishSpeechAttempt,
      );
      if (!started) _finishSpeechAttempt(failed: true);
    } catch (_) {
      _finishSpeechAttempt(failed: true);
    } finally {
      _startingSpeech = false;
      if (mounted) setState(() {});
    }
  }

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();
    final busy = task.isApiBusy || _isNavigating;
    if (!_showTextEntry) return _buildVoicePage(task, busy);
    final now = DateTime.now();
    final today18 = DateTime(now.year, now.month, now.day, 18);
    final tomorrow18 = today18.add(const Duration(days: 1));
    final weekend = now.add(Duration(days: (7 - now.weekday) % 7));
    final weekend18 = DateTime(weekend.year, weekend.month, weekend.day, 18);

    return Scaffold(
      appBar: const MelbelAppBar(title: 'Thêm công việc mới', showBack: true),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 24),
          children: [
            const PlanningStageHeader(step: 1, label: 'Nhập bằng chữ'),
            const SizedBox(height: 16),
            const MelbelCard(
              child: Row(
                children: [
                  SizedBox(width: 56, height: 56, child: MelbelMascot()),
                  SizedBox(width: 12),
                  Expanded(
                      child: Text(
                          'Bạn muốn làm việc gì? Có thể gõ cả hạn và giờ rảnh.')),
                ],
              ),
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _titleController,
              minLines: 3,
              maxLines: 6,
              textCapitalization: TextCapitalization.sentences,
              decoration: const InputDecoration(
                labelText: 'Nội dung công việc',
                hintText: 'Ví dụ: Viết báo cáo 3 trang trước 17:00 thứ Sáu.',
                alignLabelWithHint: true,
                border: OutlineInputBorder(),
              ),
              onChanged: task.setTitle,
            ),
            if (task.draftPersisted)
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Text('Đã lưu bản nháp trên thiết bị'),
              ),
            const SizedBox(height: 8),
            ExpansionTile(
              tilePadding: EdgeInsets.zero,
              title: const Text('Hạn chót'),
              subtitle: Text(task.deadline == null
                  ? 'Có thể nêu hạn ngay trong câu'
                  : task.formatDeadline(task.deadline)),
              children: [
                Wrap(
                  spacing: 8,
                  runSpacing: 4,
                  children: [
                    for (final option in [
                      ('18:00 hôm nay', today18),
                      ('18:00 ngày mai', tomorrow18),
                      ('Cuối tuần', weekend18),
                    ])
                      if (option.$2.isAfter(now))
                        ChoiceChip(
                          label: Text(option.$1),
                          selected: task.deadline == option.$2,
                          onSelected:
                              busy ? null : (_) => _onQuickDeadline(option.$2),
                        ),
                  ],
                ),
                TextButton.icon(
                  onPressed: busy ? null : _pickDeadline,
                  icon: const Icon(Icons.edit_calendar_outlined),
                  label: const Text('Chọn ngày và giờ khác'),
                ),
              ],
            ),
            const SizedBox(height: 8),
            PrimaryCta(
              label: 'Phân tích công việc',
              icon: Icons.arrow_forward,
              onPressed: busy || _isListening ? null : _continue,
            ),
            const SizedBox(height: 8),
            SecondaryCta(
              label: 'Lưu vào Hộp nháp',
              icon: Icons.drafts_outlined,
              onPressed: busy || _isListening ? null : _saveDraft,
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildVoicePage(TaskProvider task, bool busy) {
    final transcript = _titleController.text.trim();
    return Scaffold(
      appBar:
          const MelbelAppBar(title: 'Thêm việc bằng giọng nói', showBack: true),
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                _VoiceRecordButton(
                  listening: _isListening && !_startingSpeech,
                  starting: _isListening && _startingSpeech,
                  onPressed: busy || _startingSpeech ? null : _toggleListening,
                ),
                if (!_isListening)
                  TextButton(
                    onPressed: busy || _startingSpeech
                        ? null
                        : () => setState(() => _showTextEntry = true),
                    child: const Text('Nhập bằng chữ'),
                  ),
                if (!_isListening && transcript.isNotEmpty) ...[
                  const SizedBox(height: 16),
                  MelbelCard(child: Text(transcript)),
                  const SizedBox(height: 12),
                  TextButton(
                    onPressed: busy ? null : _pickDeadline,
                    child: Text(task.deadline == null
                        ? 'Chọn hạn chót'
                        : task.formatDeadline(task.deadline)),
                  ),
                  PrimaryCta(
                    label: 'Phân tích công việc',
                    onPressed: busy ? null : _continue,
                  ),
                  const SizedBox(height: 8),
                  SecondaryCta(
                    label: 'Lưu nháp',
                    onPressed: busy ? null : _saveDraft,
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _VoiceRecordButton extends StatefulWidget {
  const _VoiceRecordButton({
    required this.listening,
    required this.starting,
    required this.onPressed,
  });

  final bool listening;
  final bool starting;
  final VoidCallback? onPressed;

  @override
  State<_VoiceRecordButton> createState() => _VoiceRecordButtonState();
}

class _VoiceRecordButtonState extends State<_VoiceRecordButton>
    with SingleTickerProviderStateMixin {
  late final AnimationController _pulse = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1500),
  );
  bool _reduceMotion = false;
  bool _pressed = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _reduceMotion = MediaQuery.disableAnimationsOf(context);
    _updatePulse();
  }

  @override
  void didUpdateWidget(covariant _VoiceRecordButton oldWidget) {
    super.didUpdateWidget(oldWidget);
    _updatePulse();
  }

  void _updatePulse() {
    if (widget.listening && !_reduceMotion) {
      if (!_pulse.isAnimating) _pulse.repeat();
    } else {
      _pulse.reset();
    }
  }

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final recording = widget.listening || widget.starting;
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        SizedBox.square(
          dimension: 112,
          child: Stack(
            alignment: Alignment.center,
            children: [
              if (widget.listening && !_reduceMotion)
                IgnorePointer(
                  child: AnimatedBuilder(
                    animation: _pulse,
                    builder: (context, child) => Container(
                      width: 76 + 32 * _pulse.value,
                      height: 76 + 32 * _pulse.value,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        border: Border.all(
                          width: 2,
                          color: colors.primary.withValues(
                            alpha: 0.32 * (1 - _pulse.value),
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
              Listener(
                onPointerDown: widget.onPressed == null || _reduceMotion
                    ? null
                    : (_) => setState(() => _pressed = true),
                onPointerUp: (_) => setState(() => _pressed = false),
                onPointerCancel: (_) => setState(() => _pressed = false),
                child: AnimatedScale(
                  scale: _pressed && !_reduceMotion ? 0.93 : 1,
                  duration: _reduceMotion
                      ? Duration.zero
                      : const Duration(milliseconds: 120),
                  child: IconButton.filled(
                    tooltip: widget.listening ? 'Dừng nghe' : 'Bắt đầu nói',
                    iconSize: 36,
                    style: IconButton.styleFrom(
                      minimumSize: const Size.square(76),
                    ),
                    onPressed: widget.onPressed,
                    icon: Icon(recording ? Icons.stop_rounded : Icons.mic),
                  ),
                ),
              ),
            ],
          ),
        ),
        if (recording)
          Semantics(
            liveRegion: true,
            child: Text(widget.starting ? 'Đang bật mic…' : 'Đang nghe…'),
          ),
      ],
    );
  }
}

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
    if (!mounted) return;
    setState(() => _error = null);
    try {
      await context.read<TaskProvider>().prepareDraftAndAnalyze();
      if (!mounted || ModalRoute.of(context)?.isCurrent != true) return;
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
                const Text('Bạn có thể thử lại hoặc tiếp tục với bản nháp.',
                    textAlign: TextAlign.center),
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
      appBar: const MelbelAppBar(
        title: 'ProPlan VA',
        showBack: true,
        showGlobalActions: false,
      ),
      body: SafeArea(
        child: Center(
          child: ListView(
            shrinkWrap: true,
            padding: const EdgeInsets.all(24),
            children: [
              const Center(child: _BreathingCircle()),
              const SizedBox(height: 24),
              MelbelCard(
                child: Column(
                  children: [
                    Text('Melbel đang đọc công việc',
                        style: Theme.of(context).textTheme.titleMedium),
                    const SizedBox(height: 6),
                    const Text(
                      'Đang kiểm tra giờ rảnh và hạn chót.',
                      textAlign: TextAlign.center,
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 16),
              SecondaryCta(
                label: 'Quay lại sửa',
                onPressed: () => Navigator.maybePop(context),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _BreathingCircle extends StatefulWidget {
  const _BreathingCircle();

  @override
  State<_BreathingCircle> createState() => _BreathingCircleState();
}

class _BreathingCircleState extends State<_BreathingCircle>
    with SingleTickerProviderStateMixin {
  late final AnimationController _breath = AnimationController(
    vsync: this,
    duration: const Duration(seconds: 12),
  );
  bool _reduceMotion = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _reduceMotion = MediaQuery.disableAnimationsOf(context);
    if (_reduceMotion) {
      _breath.stop();
    } else if (!_breath.isAnimating) {
      _breath.repeat();
    }
  }

  @override
  void dispose() {
    _breath.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return AnimatedBuilder(
      animation: _breath,
      child: const MelbelMascot(expression: CatExpression.sleepy),
      builder: (context, mascot) {
        final phase = _breath.value * 3;
        final expansion = phase < 1 ? phase : (phase < 2 ? 1.0 : 3 - phase);
        return SizedBox.square(
          dimension: 174,
          child: Stack(
            alignment: Alignment.center,
            children: [
              Transform.scale(
                scale: _reduceMotion ? 1 : 0.84 + 0.16 * expansion,
                child: Container(
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: colors.surfaceContainerHigh,
                    border: Border.all(color: colors.outlineVariant),
                  ),
                ),
              ),
              Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  mascot!,
                  Text(_reduceMotion
                      ? 'Thở đều'
                      : phase < 1
                          ? 'Hít vào'
                          : phase < 2
                              ? 'Giữ nhẹ'
                              : 'Thở ra'),
                ],
              ),
            ],
          ),
        );
      },
    );
  }
}
