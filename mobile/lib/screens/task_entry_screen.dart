import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/responsive_layout.dart';
import '../core/theme.dart';
import '../providers/task_provider.dart';
import '../services/audio_recorder_service.dart';
import '../services/voice_service.dart';
import '../widgets/melbel_ui.dart';
import 'drafts_screen.dart';
import 'task_summary_screen.dart';

class TaskEntryScreen extends StatefulWidget {
  const TaskEntryScreen({
    super.key,
    this.recorder,
    this.transcriptionService,
    this.speechService,
  });

  final Uf1VoiceRecorderService? recorder;
  final VoiceTranscriptionService? transcriptionService;
  final OnDeviceSpeechService? speechService;

  @override
  State<TaskEntryScreen> createState() => _TaskEntryScreenState();
}

class _TaskEntryScreenState extends State<TaskEntryScreen> {
  late final TextEditingController _titleController;
  final FocusNode _titleFocusNode = FocusNode();
  bool _isNavigating = false;
  bool _isListening = false;
  bool _startingSpeech = false;
  String _recognizedWords = '';
  late final OnDeviceSpeechService _speechService =
      widget.speechService ?? OnDeviceSpeechService();
  String _textBeforeListening = '';
  late final Uf1VoiceRecorderService? _recorder = widget.recorder ??
      (!kIsWeb && defaultTargetPlatform == TargetPlatform.android
          ? Uf1VoiceRecorderService()
          : null);
  late final VoiceTranscriptionService _transcription =
      widget.transcriptionService ?? VoiceTranscriptionService();
  bool _useOnDevice = false;
  Future<void>? _recordingStart;
  int _voiceFailures = 0;
  bool _showEditor = false;
  String? _voiceHint;
  String _melbelHint = 'Bạn muốn làm việc gì?';

  static const List<String> _samplePrompts = [
    'Viết báo cáo trước 18:00 ngày mai',
    'Ôn bài 1 tiếng tối nay 20:00–21:00',
    'Dọn bàn làm việc 25 phút',
  ];

  void _applySample(TaskProvider task, String sample) {
    _titleController.text = sample;
    _titleController.selection = TextSelection.collapsed(
      offset: sample.length,
    );
    task.setTitle(sample);
    setState(() => _showEditor = true);
  }

  @override
  void initState() {
    super.initState();
    _titleController = TextEditingController(
      text: context.read<TaskProvider>().inputText,
    );
    _showEditor = _titleController.text.trim().isNotEmpty;
    final recorder = _recorder;
    if (recorder != null) {
      recorder.onAutoStopped = (recording) => unawaited(_transcribe(recording));
      recorder.onAutoStopFailed = (_) => _offerOnDevice();
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && recorder == null) {
        _speechService.requestAndroidPermission();
      }
      if (mounted && recorder != null) {
        _recordingStart = recorder
            .hasPermission()
            .then<void>((_) {}, onError: (Object _, StackTrace __) {});
      }
    });
  }

  @override
  void dispose() {
    _speechService.dispose();
    final recorder = _recorder;
    if (recorder != null) {
      recorder.onAutoStopped =
          (recording) => unawaited(recorder.deleteRecording(recording.path));
      recorder.onAutoStopFailed = null;
      unawaited((_recordingStart ?? Future<void>.value())
          .whenComplete(recorder.dispose));
    }
    _titleFocusNode.dispose();
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

  void _finishSpeechAttempt({
    bool failed = false,
    bool lowConfidence = false,
  }) {
    if (!mounted || !_isListening) return;
    final words = _recognizedWords.trim();
    final success = !failed && words.isNotEmpty;
    setState(() {
      _isListening = false;
      if (success) {
        _showEditor = true;
        _voiceFailures = 0;
        _voiceHint = null;
        final combined = [_textBeforeListening, words]
            .where((part) => part.isNotEmpty)
            .join(' ');
        _titleController.value = TextEditingValue(
          text: combined,
          selection: TextSelection.collapsed(offset: combined.length),
        );
        context.read<TaskProvider>().setTitle(
              combined,
              fromVoice: true,
              lowConfidence: lowConfidence,
            );
        _showEditor = true;
      }
    });
    if (!success) _showVoiceFailure();
  }

  void _showVoiceFailure() {
    setState(() {
      _voiceFailures++;
      if (_voiceFailures >= 3) _showEditor = true;
      _voiceHint = _showEditor
          ? 'Bạn có thể nhập công việc bên dưới.'
          : 'Chưa nghe rõ ($_voiceFailures/3). Chạm mic để thử lại.';
    });
  }

  void _offerOnDevice() {
    if (!mounted || _useOnDevice) return;
    setState(() {
      _useOnDevice = true;
      _startingSpeech = false;
      _isListening = false;
      _voiceHint = null;
    });
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(const SnackBar(
        content: Text(
          'Chưa chuyển được giọng nói. Chạm mic để thử nhận diện trên máy, hoặc gõ chữ.',
        ),
      ));
  }

  Future<void> _transcribe(ThoughtRecording recording) async {
    final recorder = _recorder!;
    try {
      if (!mounted) return;
      setState(() => _startingSpeech = true);
      final result = await _transcription.transcribeM4aWithMetadata(
        recording.path,
        recordingId: recording.path,
      );
      _recognizedWords = result.text;
      _finishSpeechAttempt(lowConfidence: result.lowConfidence);
    } catch (_) {
      _offerOnDevice();
    } finally {
      await recorder.deleteRecording(recording.path);
      if (mounted) setState(() => _startingSpeech = false);
    }
  }

  Future<void> _toggleListening() async {
    if (_startingSpeech) return;
    final recorder = _recorder;
    if (recorder == null || _useOnDevice) return _toggleOnDevice();
    if (_isListening) {
      var isFinalizing = false;
      try {
        isFinalizing = recorder.isFinalizing;
      } on UnimplementedError {
        isFinalizing = false;
      }
      if (isFinalizing) {
        setState(() => _startingSpeech = true);
        return;
      }
      setState(() => _startingSpeech = true);
      try {
        await _transcribe(await recorder.stopAndFinalize());
      } catch (_) {
        _offerOnDevice();
      }
      return;
    }
    _textBeforeListening = _titleController.text.trim();
    _recognizedWords = '';
    setState(() => _startingSpeech = true);
    try {
      await _recordingStart;
      if (!mounted) return;
      final start = recorder.start(maxDuration: const Duration(seconds: 45));
      _recordingStart =
          start.then<void>((_) {}, onError: (Object _, StackTrace __) {});
      await start;
      if (mounted) setState(() => _isListening = true);
    } catch (_) {
      _offerOnDevice();
    } finally {
      if (mounted) setState(() => _startingSpeech = false);
    }
  }

  Future<void> _toggleOnDevice() async {
    if (_startingSpeech) return;
    if (_isListening) {
      setState(() => _startingSpeech = true);
      try {
        await _speechService.stop().timeout(const Duration(seconds: 2));
      } catch (_) {
        try {
          await _speechService.cancel().timeout(const Duration(seconds: 2));
        } catch (_) {}
      } finally {
        _finishSpeechAttempt();
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
    final canSubmit = !busy &&
        !_isListening &&
        !_startingSpeech &&
        _titleController.text.trim().isNotEmpty;
    final now = DateTime.now();
    final today18 = DateTime(now.year, now.month, now.day, 18);
    final tomorrow18 = today18.add(const Duration(days: 1));
    final weekend = now.add(Duration(days: (7 - now.weekday) % 7));
    final weekend18 = DateTime(weekend.year, weekend.month, weekend.day, 18);

    final palette = context.melbel;
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final textTheme = theme.textTheme;

    return MelbelPageBackdrop(
      showTopCloud: false,
      showBottomClouds: false,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: const MelbelAppBar(title: 'Việc mới', showBack: true),
        body: SafeArea(
          top: false,
          child: ListView(
            padding: MelbelLayout.pageInsets(context, top: 16, bottom: 28),
            children: [
              const PlanningStageHeader(step: 1, label: 'Nhập công việc'),
              const SizedBox(height: 16),
              MelbelEntrance(
                child: MelbelChatBlock(
                  senderLabel: 'Melbel',
                  badge: 'Trợ lý VA',
                  message: _melbelHint,
                  tone: MelbelCardTone.sage,
                  mascotWidth: 80,
                  mascotHeight: 74,
                  onPetted: (whisper) {
                    setState(() => _melbelHint = whisper);
                  },
                ),
              ),
              const SizedBox(height: 16),
              MelbelCard(
                borderRadius: 28,
                padding: EdgeInsets.zero,
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(28),
                  child: Stack(
                    children: [
                      Positioned.fill(
                        child: IgnorePointer(
                          child: RepaintBoundary(
                            child: CustomPaint(
                              painter: _VoiceCardDecorPainter(isDark: isDark),
                            ),
                          ),
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 20,
                          vertical: 40,
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            Center(
                              child: _VoiceRecordButton(
                                listening: _isListening && !_startingSpeech,
                                starting: _startingSpeech,
                                onPressed: busy || _startingSpeech
                                    ? null
                                    : _toggleListening,
                              ),
                            ),
                            if (_voiceHint != null &&
                                !_isListening &&
                                !_startingSpeech)
                              Padding(
                                padding: const EdgeInsets.only(top: 10),
                                child: Semantics(
                                  liveRegion: true,
                                  child: Text(
                                    _voiceHint!,
                                    textAlign: TextAlign.center,
                                    style: textTheme.bodySmall?.copyWith(
                                      color: palette.amber,
                                      fontWeight: FontWeight.w500,
                                    ),
                                  ),
                                ),
                              ),
                            if (_showEditor) ...[
                              const SizedBox(height: 16),
                              TextField(
                                controller: _titleController,
                                focusNode: _titleFocusNode,
                                minLines: 3,
                                maxLines: 6,
                                textCapitalization:
                                    TextCapitalization.sentences,
                                decoration: InputDecoration(
                                  labelText: 'Nội dung công việc',
                                  hintText:
                                      'Ví dụ: Viết báo cáo 3 trang trước 17:00 thứ Sáu.',
                                  alignLabelWithHint: true,
                                  suffixIcon: _titleController.text.isEmpty
                                      ? null
                                      : IconButton(
                                          tooltip: 'Xóa',
                                          onPressed: busy || _isListening
                                              ? null
                                              : () {
                                                  _titleController.clear();
                                                  task.setTitle('');
                                                  setState(() {});
                                                },
                                          icon: const Icon(Icons.close_rounded),
                                        ),
                                ),
                                enabled:
                                    !busy && !_isListening && !_startingSpeech,
                                onChanged: (value) {
                                  task.setTitle(value);
                                  setState(() {});
                                },
                              ),
                              if (task.isVoiceLowConfidence &&
                                  _titleController.text.trim().isNotEmpty) ...[
                                const SizedBox(height: 10),
                                Semantics(
                                  liveRegion: true,
                                  child: Material(
                                    key: const Key('uf1_low_confidence_banner'),
                                    color: palette.amberSoft,
                                    shape: RoundedRectangleBorder(
                                      borderRadius: BorderRadius.circular(16),
                                      side: isDark
                                          ? BorderSide(
                                              color: palette.amber
                                                  .withValues(alpha: 0.35),
                                            )
                                          : BorderSide.none,
                                    ),
                                    clipBehavior: Clip.antiAlias,
                                    child: InkWell(
                                      onTap: busy
                                          ? null
                                          : _titleFocusNode.requestFocus,
                                      child: Padding(
                                        padding: const EdgeInsets.symmetric(
                                          horizontal: 14,
                                          vertical: 10,
                                        ),
                                        child: Row(
                                          crossAxisAlignment:
                                              CrossAxisAlignment.start,
                                          children: [
                                            Icon(
                                              Icons.graphic_eq_rounded,
                                              size: 20,
                                              color: palette.amber,
                                            ),
                                            const SizedBox(width: 10),
                                            Expanded(
                                              child: Text(
                                                'Mình nghe chưa thật rõ (âm thanh nhỏ hoặc có tạp âm). Bạn kiểm tra hoặc chạm vào ô chữ ở trên để sửa trước khi phân tích nhé.',
                                                style: textTheme.bodyMedium
                                                    ?.copyWith(
                                                  color: palette.onAmberSoft,
                                                ),
                                              ),
                                            ),
                                          ],
                                        ),
                                      ),
                                    ),
                                  ),
                                ),
                              ],
                    if (_titleController.text.trim().isEmpty &&
                        !_isListening &&
                        !_startingSpeech) ...[
                      const SizedBox(height: 10),
                      ExpansionTile(
                        tilePadding: EdgeInsets.zero,
                        title: const Text('Dùng câu mẫu'),
                        children: [
                          for (final sample in _samplePrompts)
                            ListTile(
                              contentPadding: EdgeInsets.zero,
                              title: Text(sample),
                              onTap: busy
                                  ? null
                                  : () => _applySample(task, sample),
                            ),
                        ],
                      ),
                    ],
                    if (task.draftPersisted)
                      const Padding(
                        padding: EdgeInsets.only(top: 10),
                        child: Align(
                          alignment: Alignment.centerLeft,
                          child: MelbelStatusPill(
                            label: 'Đã lưu nháp',
                            icon: Icons.check_circle_outline_rounded,
                            tone: MelbelCardTone.sage,
                          ),
                        ),
                      ),
                  ],
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
            if (!_showEditor) ...[
              const SizedBox(height: 16),
              SecondaryCta(
                label: 'Nhập bằng bàn phím',
                icon: Icons.keyboard_alt_outlined,
                height: 56,
                onPressed: busy ? null : () => setState(() => _showEditor = true),
              ),
            ],
            if (_showEditor) ...[
              const SizedBox(height: 12),
              MelbelCard(
                padding:
                    const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
                child: Theme(
                  data: Theme.of(context).copyWith(
                    dividerColor: Colors.transparent,
                  ),
                  child: ExpansionTile(
                    tilePadding: EdgeInsets.zero,
                    childrenPadding: const EdgeInsets.only(bottom: 10),
                    leading: Icon(
                      Icons.event_available_outlined,
                      color: task.deadline != null
                          ? palette.sage
                          : palette.onSurfaceVariant,
                    ),
                    title: Text(
                      'Hạn chót',
                      style: textTheme.titleSmall,
                    ),
                    subtitle: Text(
                      task.deadline == null
                          ? 'Có thể nêu hạn ngay trong câu'
                          : task.formatDeadline(task.deadline),
                      style: textTheme.bodySmall?.copyWith(
                        color: task.deadline != null
                            ? palette.sage
                            : palette.onSurfaceVariant,
                      ),
                    ),
                    children: [
                      Align(
                        alignment: Alignment.centerLeft,
                        child: Wrap(
                          spacing: 8,
                          runSpacing: 6,
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
                                  onSelected: busy
                                      ? null
                                      : (_) => _onQuickDeadline(option.$2),
                                ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 6),
                      Align(
                        alignment: Alignment.centerLeft,
                        child: TextButton.icon(
                          onPressed: busy ? null : _pickDeadline,
                          icon: const Icon(Icons.edit_calendar_outlined),
                          label: const Text('Chọn ngày và giờ khác'),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 16),
              PrimaryCta(
                label: 'Phân tích công việc',
                icon: Icons.auto_awesome_rounded,
                onPressed: canSubmit ? _continue : null,
              ),
              const SizedBox(height: 10),
              SecondaryCta(
                label: 'Lưu vào Hộp nháp',
                icon: Icons.drafts_outlined,
                onPressed: canSubmit ? _saveDraft : null,
              ),
            ],
          ],
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
    final palette = context.melbel;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final recording = widget.listening || widget.starting;
    final haloColor = recording
        ? palette.amberSoft
        : (isDark ? const Color(0xFF392F50) : const Color(0xFFEBE1FA));
    final rayColor = recording
        ? palette.amber
        : (isDark ? const Color(0xFFB49EE8) : const Color(0xFF9C84D9));

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        RepaintBoundary(
          child: SizedBox.square(
            dimension: 164,
            child: Stack(
              alignment: Alignment.center,
              clipBehavior: Clip.none,
              children: [
                Positioned(
                  top: 8,
                  right: 6,
                  child: IgnorePointer(
                    child: Transform.rotate(
                      angle: 0.34,
                      child: MelbelDoodleRays(size: 20, color: rayColor),
                    ),
                  ),
                ),
                IgnorePointer(
                  child: DecoratedBox(
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: haloColor,
                    ),
                    child: const SizedBox.square(dimension: 142),
                  ),
                ),
                if (widget.listening && !_reduceMotion)
                  IgnorePointer(
                    child: AnimatedBuilder(
                      animation: _pulse,
                      builder: (context, child) => Transform.scale(
                        scale: 1.0 + 0.42 * _pulse.value,
                        child: Opacity(
                          opacity: (1 - _pulse.value).clamp(0.0, 1.0) * 0.45,
                          child: child,
                        ),
                      ),
                      child: Container(
                        width: 102,
                        height: 102,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          border: Border.all(
                            width: 2.5,
                            color: palette.sage,
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
                    scale: _pressed && !_reduceMotion ? 0.94 : 1,
                    duration: _reduceMotion
                        ? Duration.zero
                        : const Duration(milliseconds: 110),
                    child: DecoratedBox(
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        gradient: RadialGradient(
                          center: const Alignment(-0.25, -0.30),
                          radius: 0.95,
                          colors: recording
                              ? [
                                  const Color(0xFFEFC660),
                                  palette.amber,
                                ]
                              : [
                                  const Color(0xFFAE8CF0),
                                  palette.sage,
                                ],
                        ),
                        boxShadow: [
                          BoxShadow(
                            color: (recording ? palette.amber : palette.sage)
                                .withValues(alpha: isDark ? 0.32 : 0.24),
                            blurRadius: 18,
                            offset: const Offset(0, 6),
                          ),
                        ],
                      ),
                      child: IconButton.filled(
                        tooltip:
                            widget.listening ? 'Dừng nghe' : 'Bắt đầu nói',
                        iconSize: 44,
                        style: IconButton.styleFrom(
                          minimumSize: const Size.square(102),
                          backgroundColor: Colors.transparent,
                          foregroundColor: palette.onSage,
                        ),
                        onPressed: widget.onPressed,
                        icon: Icon(
                          recording ? Icons.stop_rounded : Icons.mic_rounded,
                        ),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 14),
        if (recording)
          Semantics(
            liveRegion: true,
            child: MelbelStatusPill(
              label: widget.starting ? 'Đang xử lý…' : 'Đang nghe…',
              icon: Icons.graphic_eq_rounded,
              tone: MelbelCardTone.amber,
            ),
          )
        else
          Text(
            'Chạm để nói',
            style: Theme.of(context).textTheme.titleMedium?.copyWith(
                  fontSize: 19,
                  color: palette.onSurface,
                  fontWeight: FontWeight.w800,
                ),
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
  Timer? _longPlanTimer;
  bool _isLongPlanHint = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _analyze());
  }

  @override
  void dispose() {
    _longPlanTimer?.cancel();
    super.dispose();
  }

  Future<void> _analyze() async {
    if (!mounted) return;
    _longPlanTimer?.cancel();
    setState(() {
      _error = null;
      _isLongPlanHint = false;
    });
    _longPlanTimer = Timer(const Duration(seconds: 5), () {
      if (mounted) {
        setState(() => _isLongPlanHint = true);
      }
    });
    try {
      await context.read<TaskProvider>().prepareDraftAndAnalyze();
      if (!mounted || ModalRoute.of(context)?.isCurrent != true) return;
      Navigator.pushReplacement(
        context,
        MaterialPageRoute<void>(builder: (_) => const TaskSummaryScreen()),
      );
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    } finally {
      _longPlanTimer?.cancel();
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) {
      final palette = context.melbel;
      return MelbelPageBackdrop(
        showTopCloud: false,
        showBottomClouds: true,
        child: Scaffold(
          backgroundColor: Colors.transparent,
          appBar: const MelbelAppBar(title: 'Chưa kết nối được', showBack: true),
          body: SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  MelbelCard(
                    borderRadius: 26,
                    padding: const EdgeInsets.all(22),
                    child: Column(
                      children: [
                        Container(
                          width: 48,
                          height: 48,
                          decoration: BoxDecoration(
                            color: palette.amberSoft,
                            borderRadius: BorderRadius.circular(15),
                          ),
                          child: Icon(
                            Icons.cloud_off_outlined,
                            size: 26,
                            color: palette.amber,
                          ),
                        ),
                        const SizedBox(height: 14),
                        Text(
                          'Chưa gửi được công việc lên máy chủ.',
                          textAlign: TextAlign.center,
                          style: Theme.of(context).textTheme.titleMedium?.copyWith(
                                fontWeight: FontWeight.w700,
                              ),
                        ),
                        const SizedBox(height: 6),
                        Text(
                          'Bạn có thể thử lại hoặc tiếp tục với bản nháp.',
                          textAlign: TextAlign.center,
                          style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                                color: palette.onSurfaceVariant,
                              ),
                        ),
                      ],
                    ),
                  ),
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
        ),
      );
    }
    return MelbelPageBackdrop(
      showTopCloud: true,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: const MelbelAppBar(
          title: 'ProPlan VA',
          showBack: true,
          showGlobalActions: false,
          showTitleRays: true,
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
                  borderRadius: 26,
                  padding: const EdgeInsets.all(20),
                  child: Column(
                    children: [
                      Text(
                        'AI đang phân tích và gom thông tin',
                        textAlign: TextAlign.center,
                        style: Theme.of(context).textTheme.titleMedium?.copyWith(
                              fontWeight: FontWeight.w700,
                            ),
                      ),
                      const SizedBox(height: 6),
                      Text(
                        _isLongPlanHint
                            ? 'Kế hoạch dài hạn đang được sắp theo từng cột mốc cho bạn...'
                            : 'Chờ Melbel một chút nhé...',
                        textAlign: TextAlign.center,
                        style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                              color: context.melbel.onSurfaceVariant,
                            ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 16),
                SecondaryCta(
                  label: 'Hủy và lưu nháp',
                  icon: Icons.drafts_outlined,
                  onPressed: () {
                    context.read<TaskProvider>().saveDraft();
                    Navigator.maybePop(context);
                  },
                ),
              ],
            ),
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
    final palette = context.melbel;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final haloColor =
        isDark ? const Color(0xFFA797C4) : const Color(0xFFFFF8E2);

    return RepaintBoundary(
      child: AnimatedBuilder(
        animation: _breath,
        child: Container(
          padding: const EdgeInsets.all(10),
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: haloColor,
          ),
          child: const MelbelMascot(
            expression: CatExpression.sleepy,
            interactive: false,
            forceLightSvg: true,
            width: 82,
            height: 74,
          ),
        ),
        builder: (context, mascot) {
          final phase = _breath.value * 3;
          final expansion = phase < 1 ? phase : (phase < 2 ? 1.0 : 3 - phase);
          final label = _reduceMotion
              ? 'Thở đều'
              : phase < 1
                  ? 'Hít vào'
                  : phase < 2
                      ? 'Giữ nhịp'
                      : 'Thở ra chậm';

          return SizedBox.square(
            dimension: 196,
            child: Stack(
              alignment: Alignment.center,
              children: [
                Transform.scale(
                  scale: _reduceMotion ? 1 : 0.84 + 0.16 * expansion,
                  child: Container(
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: palette.sageSoft,
                      border: Border.all(
                        color: palette.sage.withValues(alpha: 0.25),
                        width: 1.5,
                      ),
                    ),
                  ),
                ),
                Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    mascot!,
                    const SizedBox(height: 8),
                    MelbelStatusPill(
                      label: label,
                      tone: MelbelCardTone.neutral,
                    ),
                  ],
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}

class _VoiceCardDecorPainter extends CustomPainter {
  const _VoiceCardDecorPainter({required this.isDark});

  final bool isDark;

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width;
    final h = size.height;

    final starPaint = Paint()
      ..color = isDark ? const Color(0xFFF3CA52) : const Color(0xFFF3C854);

    void drawStar(Offset center, double r) {
      final path = Path()
        ..moveTo(center.dx, center.dy - r)
        ..quadraticBezierTo(center.dx, center.dy, center.dx + r, center.dy)
        ..quadraticBezierTo(center.dx, center.dy, center.dx, center.dy + r)
        ..quadraticBezierTo(center.dx, center.dy, center.dx - r, center.dy)
        ..quadraticBezierTo(center.dx, center.dy, center.dx, center.dy - r)
        ..close();
      canvas.drawPath(path, starPaint);
    }

    // Top-left gold sparkle stars
    drawStar(Offset(w * 0.145, h * 0.21), 11.0);
    drawStar(Offset(w * 0.105, h * 0.305), 5.5);

    // Bottom lavender clouds
    final cloudPaint = Paint()
      ..color = isDark
          ? const Color(0xFF342A4A).withValues(alpha: 0.92)
          : const Color(0xFFEEE5FA).withValues(alpha: 0.95);
    // Left cloud mound
    canvas.drawCircle(Offset(w * 0.08, h + 10), 44, cloudPaint);
    canvas.drawCircle(Offset(w * 0.25, h + 18), 36, cloudPaint);
    canvas.drawCircle(Offset(-4, h - 6), 28, cloudPaint);
    // Right cloud mound
    canvas.drawCircle(Offset(w * 0.85, h + 6), 46, cloudPaint);
    canvas.drawCircle(Offset(w * 0.68, h + 18), 34, cloudPaint);
    canvas.drawCircle(Offset(w + 6, h - 8), 30, cloudPaint);

    // Two tiny upright lavender petals peeking from bottom-left cloud
    final petalPaint = Paint()
      ..color = isDark ? const Color(0xFF9E88D4) : const Color(0xFFB8A3E8);
    void drawPetal(Offset base, double angle, double len) {
      canvas.save();
      canvas.translate(base.dx, base.dy);
      canvas.rotate(angle);
      final p = Path()
        ..moveTo(0, 0)
        ..quadraticBezierTo(-4.0, -len * 0.55, 0, -len)
        ..quadraticBezierTo(4.0, -len * 0.55, 0, 0)
        ..close();
      canvas.drawPath(p, petalPaint);
      canvas.restore();
    }

    drawPetal(Offset(w * 0.145, h - 28), -0.32, 13.0);
    drawPetal(Offset(w * 0.17, h - 27), 0.22, 11.5);

    // Subtle paw print tucked in bottom-right cloud
    final pawPaint = Paint()
      ..color = isDark
          ? const Color(0xFFA68CE6).withValues(alpha: 0.78)
          : const Color(0xFFBCA6EC).withValues(alpha: 0.82);
    canvas.save();
    canvas.translate(w * 0.85, h - 22);
    canvas.rotate(-0.14);
    canvas.drawOval(
      Rect.fromCenter(center: const Offset(0, 3.8), width: 12.5, height: 9.5),
      pawPaint,
    );
    canvas.drawCircle(const Offset(-7.2, -3.2), 2.4, pawPaint);
    canvas.drawCircle(const Offset(-2.5, -5.8), 2.5, pawPaint);
    canvas.drawCircle(const Offset(2.7, -5.7), 2.5, pawPaint);
    canvas.drawCircle(const Offset(7.2, -3.0), 2.3, pawPaint);
    canvas.restore();
  }

  @override
  bool shouldRepaint(_VoiceCardDecorPainter oldDelegate) =>
      oldDelegate.isDark != isDark;
}
