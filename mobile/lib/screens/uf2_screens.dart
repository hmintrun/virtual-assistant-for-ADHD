import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../core/responsive_layout.dart';
import '../services/voice_service.dart';
import '../models/focus_session_model.dart';
import '../providers/focus_provider.dart';
import '../providers/task_provider.dart';
import '../widgets/calendar_fallback.dart';
import '../widgets/melbel_ui.dart';
import 'candidate_review_screen.dart';
import 'focus_screen.dart';
import 'main_shell_screen.dart';

// Helper for opening new screens
Future<void> _open(BuildContext context, Widget page) => Navigator.push<void>(
      context,
      MaterialPageRoute<void>(builder: (_) => page),
    );

// ============================================================================
// D-21: CHIA VIỆC (TASK DECOMPOSITION SCREEN)
// ============================================================================

class DecompositionChoiceScreen extends StatefulWidget {
  const DecompositionChoiceScreen({super.key, this.returnToReview = false});

  final bool returnToReview;

  @override
  State<DecompositionChoiceScreen> createState() =>
      _DecompositionChoiceScreenState();
}

class _DecompositionChoiceScreenState extends State<DecompositionChoiceScreen> {
  final _intentController = TextEditingController();
  bool _isGenerating = false;

  @override
  void dispose() {
    _intentController.dispose();
    super.dispose();
  }

  void _applyChip(String text) {
    setState(() {
      _intentController.text = text;
      _intentController.selection = TextSelection.fromPosition(
        TextPosition(offset: text.length),
      );
    });
  }

  Future<void> _dictateRequest() async {
    final words = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (sheet) => VoiceAssistantModal(
        applyLabel: 'Dùng yêu cầu này',
        onApply: (value) => Navigator.pop(sheet, value),
      ),
    );
    if (mounted && words != null) _applyChip(words);
  }

  Future<void> _generateCandidate({bool useManualAvailability = false}) async {
    final task = context.read<TaskProvider>();
    if (_isGenerating || task.isApiBusy) return;
    setState(() => _isGenerating = true);
    try {
      final proposal = _intentController.text.trim();
      await task.generateCandidateOnBackend(
        stepProposal: proposal.isEmpty ? null : proposal,
        useManualAvailability: useManualAvailability,
      );
      if (!mounted) return;
      if (widget.returnToReview) {
        Navigator.pop(context);
        return;
      }
      await Navigator.push(
        context,
        MaterialPageRoute<void>(builder: (_) => const CandidateReviewScreen()),
      );
    } catch (error) {
      if (!mounted) return;
      if (isCalendarUnavailable(error)) {
        showCalendarFallbackSnackBar(
          context,
          onUseManualAvailability: () =>
              _generateCandidate(useManualAvailability: true),
        );
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Chưa tạo được phương án: $error')),
        );
      }
    } finally {
      if (mounted) setState(() => _isGenerating = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();

    final busy = _isGenerating || task.isApiBusy;
    final text = Theme.of(context).textTheme;
    return MelbelPageBackdrop(
      showTopCloud: false,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: const MelbelAppBar(
          title: 'Chia nhỏ công việc',
          showBack: true,
          showTitleRays: true,
        ),
        body: SafeArea(
          top: false,
          child: ListView(
            padding: MelbelLayout.pageInsets(context, top: 16, bottom: 32),
            children: [
              const PlanningStageHeader(step: 3, label: 'Lên phương án'),
              const SizedBox(height: 16),
              MelbelCard(
                borderRadius: 26,
                child: Row(
                  children: [
                    const MelbelCushionAvatar(width: 64, height: 56),
                    const SizedBox(width: 12),
                    Expanded(
                        child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                            task.title.isEmpty ? 'Công việc của bạn' : task.title,
                            style: text.titleMedium?.copyWith(
                              fontWeight: FontWeight.w700,
                            )),
                        const SizedBox(height: 4),
                        Text('Hạn chót: ${task.formatDeadline(task.deadline)}',
                            style: text.bodyMedium),
                      ],
                    )),
                  ],
                ),
              ),
            const SizedBox(height: 16),
            PrimaryCta(
              label: _isGenerating ? 'Đang tạo phương án…' : 'Để AI đề xuất',
              icon: Icons.auto_awesome_rounded,
              onPressed: busy
                  ? null
                  : () {
                      task.setDecompositionChoice('ai');
                      _generateCandidate();
                    },
            ),
            const SizedBox(height: 12),
            ExpansionTile(
              initiallyExpanded: widget.returnToReview,
              title: const Text('Tùy chỉnh cách chia việc'),
              childrenPadding: const EdgeInsets.only(bottom: 12),
              children: [
                TextField(
                  controller: _intentController,
                  enabled: !busy,
                  minLines: 1,
                  maxLines: 4,
                  decoration: const InputDecoration(
                    labelText: 'Bạn muốn chia việc thế nào?',
                    hintText: 'Ví dụ: Chia thành hai phiên 15 phút',
                  ),
                ),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  runSpacing: 6,
                  children: [
                    for (final option in const [
                      ('2 phiên 15 phút', '2 phiên nhẹ nhàng (15p)'),
                      ('3 phiên có nghỉ', '3 chặng có nghỉ giãn cách'),
                      ('Thử 5 phút', 'Làm thử 5 phút trước'),
                    ])
                      ActionChip(
                          label: Text(option.$1),
                          onPressed: busy ? null : () => _applyChip(option.$2)),
                  ],
                ),
                TextButton.icon(
                  icon: const Icon(Icons.mic_none),
                  label: const Text('Nói yêu cầu'),
                  onPressed: busy ? null : _dictateRequest,
                ),
                SecondaryCta(
                  label: 'Tạo theo ý tôi',
                  onPressed: busy
                      ? null
                      : () {
                          task.setDecompositionChoice('manual');
                          _generateCandidate();
                        },
                ),
              ],
            ),
            TextButton.icon(
              onPressed:
                  busy ? null : () => _open(context, const AiFallbackScreen()),
              icon: const Icon(Icons.sync_problem),
              label: const Text('Xem thử khi AI phản hồi chậm'),
            ),
          ],
        ),
      ),
    ),
    );
  }
}

// ============================================================================
// D-22: KẾ HOẠCH & TIẾN ĐỘ THỜI GIAN (PLAN OVERVIEW / SCHEDULE REVIEW)
// ============================================================================

class PlanOverviewScreen extends StatelessWidget {
  const PlanOverviewScreen({super.key});
  @override
  Widget build(BuildContext context) {
    if (context.watch<TaskProvider>().candidate != null)
      return const CandidateReviewScreen();
    return const MelbelPageBackdrop(
      showTopCloud: false,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: MelbelAppBar(
          title: 'Kế hoạch',
          showBack: true,
          showTitleRays: true,
        ),
        body: Center(
          child: Padding(
            padding: EdgeInsets.all(24),
            child: MelbelCard(
              borderRadius: 24,
              child: Text(
                'Chưa có phương án từ máy chủ. Hãy quay lại bản nháp để tạo kế hoạch.',
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class VoiceAssistantModal extends StatefulWidget {
  const VoiceAssistantModal({
    required this.onApply,
    this.applyLabel = 'Tạo phương án mới',
    super.key,
  });
  final String applyLabel;
  final ValueChanged<String> onApply;

  @override
  State<VoiceAssistantModal> createState() => _VoiceAssistantModalState();
}

class _VoiceAssistantModalState extends State<VoiceAssistantModal> {
  final _controller = TextEditingController();
  final _speech = OnDeviceSpeechService();
  bool _listening = false;
  bool _starting = false;
  double _level = 0;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _speech.requestAndroidPermission();
    });
  }

  @override
  void dispose() {
    _speech.dispose();
    _controller.dispose();
    super.dispose();
  }

  Future<void> _toggleMic() async {
    if (_starting) return;
    if (_listening) {
      try {
        await _speech.stop();
      } finally {
        if (mounted) setState(() => _listening = false);
      }
      return;
    }
    setState(() {
      _starting = true;
      _listening = true;
      _error = null;
      _level = 0;
    });
    void stop([String? error]) {
      if (!mounted) return;
      setState(() {
        _listening = false;
        if (error != null) _error = error;
      });
    }

    try {
      final started = await _speech.start(
        onWords: (words, isFinal) {
          if (!mounted || words.trim().isEmpty) return;
          _controller.text = words;
          if (isFinal) stop();
        },
        onError: (_) =>
            stop('Chưa nhận được giọng nói. Bạn có thể gõ yêu cầu.'),
        onDone: stop,
        onSoundLevel: (level) {
          if (!mounted || !_listening) return;
          final fraction = (level / 10).clamp(0.0, 1.0).toDouble();
          if ((fraction - _level).abs() > 0.05) {
            setState(() => _level = fraction);
          }
        },
      );
      if (!started) stop('Chưa bật được mic. Bạn có thể gõ yêu cầu.');
    } catch (_) {
      stop('Chưa bật được mic. Bạn có thể gõ yêu cầu.');
    } finally {
      if (mounted) setState(() => _starting = false);
    }
  }

  @override
  Widget build(BuildContext context) => SafeArea(
        child: SingleChildScrollView(
          padding: EdgeInsets.fromLTRB(
            20,
            8,
            20,
            20 + MediaQuery.viewInsetsOf(context).bottom,
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('Tùy chỉnh phương án',
                  style: Theme.of(context).textTheme.titleLarge),
              const SizedBox(height: 12),
              IconButton.filled(
                tooltip: _listening ? 'Dừng nói' : 'Bắt đầu nói',
                onPressed: _starting ? null : _toggleMic,
                icon: Icon(_listening ? Icons.graphic_eq : Icons.mic_none),
              ),
              if (_listening) ...[
                const Text('Đang nghe…'),
                ExcludeSemantics(
                  child: Padding(
                    padding: const EdgeInsets.symmetric(vertical: 8),
                    child: SizedBox(
                      height: 28,
                      child: Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          for (final height in const [
                            8.0,
                            16.0,
                            24.0,
                            16.0,
                            8.0
                          ])
                            AnimatedContainer(
                              duration: MediaQuery.disableAnimationsOf(context)
                                  ? Duration.zero
                                  : const Duration(milliseconds: 90),
                              width: 4,
                              height: 5 + height * _level,
                              margin: const EdgeInsets.symmetric(horizontal: 3),
                              decoration: BoxDecoration(
                                color: Theme.of(context).colorScheme.primary,
                                borderRadius: BorderRadius.circular(4),
                              ),
                            ),
                        ],
                      ),
                    ),
                  ),
                ),
              ],
              if (_error != null) Text(_error!),
              const SizedBox(height: 12),
              TextField(
                controller: _controller,
                decoration: const InputDecoration(
                  labelText: 'Yêu cầu chỉnh các bước',
                  hintText: 'Ví dụ: Tách bước viết thành hai phần.',
                  border: OutlineInputBorder(),
                ),
                onChanged: (_) => setState(() {}),
              ),
              const SizedBox(height: 12),
              PrimaryCta(
                label: widget.applyLabel,
                onPressed: _listening || _controller.text.trim().isEmpty
                    ? null
                    : () => widget.onApply(_controller.text.trim()),
              ),
              TextButton(
                onPressed: () => Navigator.pop(context),
                child: const Text('Hủy'),
              ),
            ],
          ),
        ),
      );
}

enum FeasibilityAdjustment { deadline, availability, scope }

class FeasibilityScreen extends StatefulWidget {
  const FeasibilityScreen({super.key, this.deficitMinutes});

  final int? deficitMinutes;

  @override
  State<FeasibilityScreen> createState() => _FeasibilityScreenState();
}

class _FeasibilityScreenState extends State<FeasibilityScreen> {
  FeasibilityAdjustment _choice = FeasibilityAdjustment.deadline;

  @override
  Widget build(BuildContext context) {
    final deficit = widget.deficitMinutes;
    final palette = context.melbel;
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final textTheme = theme.textTheme;
    return MelbelPageBackdrop(
      showTopCloud: false,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: const MelbelAppBar(
          title: 'Điều chỉnh kế hoạch',
          showBack: true,
          showTitleRays: true,
        ),
        body: SafeArea(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 16, 20, 28),
            children: [
              const MelbelEntrance(
                child: Center(
                  child: MelbelCushionAvatar(width: 84, height: 74),
                ),
              ),
              const SizedBox(height: 14),
              MelbelCard(
                borderRadius: 24,
                tone: MelbelCardTone.amber,
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(Icons.schedule_outlined, color: palette.amberInk),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        deficit == null || deficit <= 0
                            ? 'Phương án cần được điều chỉnh trước khi chốt.'
                            : 'Cần thêm $deficit phút để xếp đủ các phiên.',
                        style: textTheme.titleSmall?.copyWith(
                          color: palette.onAmberSoft,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 16),
              for (final (option, label, icon) in const [
                (
                  FeasibilityAdjustment.deadline,
                  'Nới hạn chót',
                  Icons.event_outlined,
                ),
                (
                  FeasibilityAdjustment.availability,
                  'Bổ sung giờ rảnh',
                  Icons.access_time_rounded,
                ),
                (
                  FeasibilityAdjustment.scope,
                  'Giảm phạm vi',
                  Icons.tune_rounded,
                ),
              ])
                Padding(
                  padding: const EdgeInsets.only(bottom: 10),
                  child: MelbelCard(
                    borderRadius: 24,
                    tone: _choice == option
                        ? MelbelCardTone.sage
                        : MelbelCardTone.neutral,
                    borderColor: _choice == option
                        ? palette.sage.withValues(alpha: 0.72)
                        : (isDark ? palette.hairline : Colors.transparent),
                    borderWidth:
                        _choice == option ? 1.5 : (isDark ? 1.0 : 0.0),
                    padding: EdgeInsets.zero,
                    child: ListTile(
                      contentPadding: const EdgeInsets.symmetric(
                        horizontal: 16,
                        vertical: 4,
                      ),
                      leading: Container(
                        width: 40,
                        height: 40,
                        decoration: BoxDecoration(
                          color: _choice == option
                              ? palette.sage.withValues(alpha: 0.18)
                              : palette.sageSoft,
                          borderRadius: BorderRadius.circular(14),
                        ),
                        child: Icon(
                          icon,
                          size: 20,
                          color: _choice == option
                              ? palette.sage
                              : palette.onSurfaceVariant,
                        ),
                      ),
                      title: Text(
                        label,
                        style: textTheme.titleSmall?.copyWith(
                          fontWeight: FontWeight.w700,
                          color: _choice == option
                              ? palette.onSageSoft
                              : palette.onSurface,
                        ),
                      ),
                      trailing: Icon(
                        _choice == option
                            ? Icons.radio_button_checked_rounded
                            : Icons.radio_button_off_rounded,
                        color: _choice == option
                            ? palette.sage
                            : palette.controlOutline,
                      ),
                      selected: _choice == option,
                      onTap: () => setState(() => _choice = option),
                    ),
                  ),
                ),
              const SizedBox(height: 12),
              PrimaryCta(
                label: 'Mở mục cần sửa',
                icon: Icons.arrow_forward_rounded,
                onPressed: () => Navigator.pop(context, _choice),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ============================================================================
// D-25: THÊM THỜI GIAN HOẶC HẠN MỚI (ADD TIME / NEW DEADLINE SCREEN)
// ============================================================================

class ExtendTimeScreen extends StatefulWidget {
  const ExtendTimeScreen({super.key});

  @override
  State<ExtendTimeScreen> createState() => _ExtendTimeScreenState();
}

class _ExtendTimeScreenState extends State<ExtendTimeScreen> {
  int _selectedOption = 1; // 0 = +1h, 1 = +2h (default), 2 = Ngày mai

  final List<Map<String, String>> _options = [
    {
      'label': '+1 giờ',
      'sub': 'Dời sang 18:00',
      'range': '17:00 — 18:00',
      'desc': '1 phiên sâu (45m) + 1 nghỉ ngắn (15m)',
      'deadline': '18:00',
    },
    {
      'label': '+2 giờ',
      'sub': 'Dời sang 19:00 (đủ 3 phiên)',
      'range': '17:00 — 19:00',
      'desc':
          '3 phiên sâu (35m) xen kẽ 2 khoảng đệm nghỉ (5m). Nhịp thở tự nhiên, không vội vã.',
      'deadline': '19:00',
    },
    {
      'label': 'Chọn hạn mới',
      'sub': 'Mở lịch ngày mai',
      'range': '09:00 — 11:00 (Ngày mai)',
      'desc': 'Tạm gác lại tối nay để nghỉ ngơi phục hồi thể lực và trí não.',
      'deadline': '11:00 mai',
    },
  ];

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();
    final displayTitle =
        task.title.trim().isEmpty ? 'Công việc hiện tại' : task.title;
    final currentDeadlineLabel = task.formatDeadline(task.deadline);
    final candidateSteps = task.candidate?.steps ?? const [];
    final totalMinutes = candidateSteps.fold<int>(
      0,
      (sum, step) => sum + ((step['estimated_base_seconds'] as num?)?.toInt() ?? 0),
    );
    final totalDurationLabel = totalMinutes > 0
        ? 'Tổng cộng: ${(totalMinutes / 60).ceil()} phút'
        : 'Tổng cộng: Đang cập nhật';
    final current = _options[_selectedOption];

    return MelbelPageBackdrop(
      showTopCloud: false,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: const MelbelAppBar(
          title: 'Phân Tách Nhiệm Vụ',
          showBack: true,
          showTitleRays: true,
        ),
        body: SafeArea(
          top: false,
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
            children: [
              // Context Card (D-25)
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainer,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    width: 32,
                    height: 32,
                    decoration: BoxDecoration(
                      color: MelbelColors.kSurfaceContainerHigh,
                      shape: BoxShape.circle,
                    ),
                    child: Icon(
                      Icons.edit_calendar,
                      size: 18,
                      color: MelbelColors.kSecondary,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'CÔNG VIỆC ĐANG THỰC HIỆN',
                          style: TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                            letterSpacing: 0.5,
                            color: MelbelColors.kOutline,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          displayTitle,
                          style: TextStyle(
                            fontSize: 17,
                            fontWeight: FontWeight.w500,
                            color: MelbelColors.kPrimary,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          'Hạn hiện tại: $currentDeadlineLabel',
                          style: TextStyle(
                            fontSize: 13,
                            color: MelbelColors.kOnSurfaceVariant,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),

            // Selection Title
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  'Chọn thêm thời gian phù hợp',
                  style: TextStyle(
                    fontSize: 17,
                    fontWeight: FontWeight.w600,
                    color: MelbelColors.kPrimary,
                  ),
                ),
                Text(
                  'Gợi ý êm dịu',
                  style: TextStyle(
                    fontSize: 12,
                    color: MelbelColors.kOutline,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),

            // Duration Radio Chips (D-25)
            for (int i = 0; i < _options.length; i++) ...[
              _radioCard(
                index: i,
                label: _options[i]['label']!,
                sub: _options[i]['sub']!,
                isSelected: _selectedOption == i,
              ),
              const SizedBox(height: 8),
            ],
            const SizedBox(height: 16),

            // Reassuring Message
            Row(
              children: [
                Icon(Icons.dark_mode, size: 20, color: MelbelColors.kSecondary),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    'Mình sẽ xếp lại và kiểm tra lại. Cứ thong thả, từng chút một.',
                    style: TextStyle(
                      fontSize: 13,
                      color: MelbelColors.kOutline,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 20),

            // Schedule Preview Card (D-25)
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainer,
                borderRadius: BorderRadius.circular(12),
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
                            Icons.cloud_download,
                            size: 16,
                            color: MelbelColors.kOutline,
                          ),
                          const SizedBox(width: 6),
                          Text(
                            'KHOẢNG GIỜ MỚI',
                            style: TextStyle(
                              fontSize: 12,
                              fontWeight: FontWeight.w600,
                              letterSpacing: 0.5,
                              color: MelbelColors.kOutline,
                            ),
                          ),
                        ],
                      ),
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 8,
                          vertical: 2,
                        ),
                        decoration: BoxDecoration(
                          color: MelbelColors.kSecondaryContainer,
                          borderRadius: BorderRadius.circular(999),
                        ),
                        child: Text(
                          'Hạn mới: ${current['deadline']}',
                          style: TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w500,
                            color: MelbelColors.kSecondary,
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text(
                        current['range']!,
                        style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w500,
                          color: MelbelColors.kOnSurface,
                        ),
                      ),
                      Text(
                        totalDurationLabel,
                        style: TextStyle(
                          fontSize: 13,
                          color: MelbelColors.kOnSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),

                  // 5 Flat Visual Segment Blocks
                  Row(
                    children: [
                      Expanded(
                        flex: 35,
                        child: Container(
                          height: 12,
                          decoration: BoxDecoration(
                            color: MelbelColors.kPrimaryContainer,
                            borderRadius: BorderRadius.circular(2),
                          ),
                        ),
                      ),
                      const SizedBox(width: 4),
                      Expanded(
                        flex: 10,
                        child: Container(
                          height: 12,
                          decoration: BoxDecoration(
                            color: MelbelColors.kSurfaceContainerHighest,
                            borderRadius: BorderRadius.circular(2),
                          ),
                        ),
                      ),
                      const SizedBox(width: 4),
                      Expanded(
                        flex: 35,
                        child: Container(
                          height: 12,
                          decoration: BoxDecoration(
                            color: MelbelColors.kPrimaryContainer,
                            borderRadius: BorderRadius.circular(2),
                          ),
                        ),
                      ),
                      const SizedBox(width: 4),
                      Expanded(
                        flex: 10,
                        child: Container(
                          height: 12,
                          decoration: BoxDecoration(
                            color: MelbelColors.kSurfaceContainerHighest,
                            borderRadius: BorderRadius.circular(2),
                          ),
                        ),
                      ),
                      const SizedBox(width: 4),
                      Expanded(
                        flex: 35,
                        child: Container(
                          height: 12,
                          decoration: BoxDecoration(
                            color: MelbelColors.kPrimaryContainer,
                            borderRadius: BorderRadius.circular(2),
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  Text(
                    current['desc']!,
                    style: TextStyle(
                      fontSize: 13,
                      color: MelbelColors.kOutline,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 24),

            // Confirm Button
            PrimaryCta(
              label: 'Xác nhận thời gian bổ sung',
              icon: Icons.check,
              onPressed: () {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                    content: Text('Đã dời thời gian an tâm và cập nhật lịch.'),
                  ),
                );
                Navigator.pop(context);
              },
            ),
          ],
        ),
      ),
    ),
    );
  }

  Widget _radioCard({
    required int index,
    required String label,
    required String sub,
    required bool isSelected,
  }) {
    return Semantics(
      inMutuallyExclusiveGroup: true,
      selected: isSelected,
      button: true,
      label: '$label, $sub',
      excludeSemantics: true,
      child: InkWell(
        onTap: () => setState(() => _selectedOption = index),
        borderRadius: BorderRadius.circular(12),
        child: Container(
          height: 52,
          padding: const EdgeInsets.symmetric(horizontal: 16),
          decoration: BoxDecoration(
            color: isSelected
                ? MelbelColors.kPrimaryContainer
                : MelbelColors.kSurfaceContainer,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Row(
                children: [
                  Container(
                    width: 8,
                    height: 8,
                    decoration: BoxDecoration(
                      color: isSelected
                          ? MelbelColors.kOnPrimaryContainer
                          : MelbelColors.kOutline,
                      shape: BoxShape.circle,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Text(
                    label,
                    style: TextStyle(
                      fontSize: 16,
                      fontWeight:
                          isSelected ? FontWeight.w600 : FontWeight.w400,
                      color: isSelected
                          ? MelbelColors.kOnPrimaryContainer
                          : MelbelColors.kOnSurface,
                    ),
                  ),
                ],
              ),
              Text(
                sub,
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: isSelected ? FontWeight.w500 : FontWeight.w400,
                  color: isSelected
                      ? MelbelColors.kOnPrimaryContainer
                      : MelbelColors.kOnSurfaceVariant,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ============================================================================
// D-26: PHƯƠNG ÁN CHƯA XÁC NHẬN ĐƯỢC GIỮ NGUYÊN (UNCONFIRMED PLAN KEPT)
// ============================================================================

class UnconfirmedPlanScreen extends StatelessWidget {
  const UnconfirmedPlanScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();
    final candidate = task.candidate;
    final displayTitle = task.title.isEmpty ? 'Công việc' : task.title;
    final sessionCount = candidate?.sessions.length ?? 0;
    final deficit = candidate?.feasibility['deficit_seconds'] as int? ?? 0;
    final unscheduled =
        candidate?.feasibility['unscheduled_seconds'] as int? ?? 0;
    final feasibilityText = candidate == null
        ? 'Chưa có phương án'
        : candidate.isFeasible
            ? 'Có thể xếp lịch'
            : deficit > 0
                ? 'Cần thêm ít nhất ${(deficit / 60).ceil()} phút'
                : unscheduled > 0
                    ? 'Còn ${(unscheduled / 60).ceil()} phút chưa xếp lịch'
                    : 'Chưa thể xếp lịch với giờ rảnh hiện tại';

    return MelbelPageBackdrop(
      showTopCloud: false,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: const MelbelAppBar(
          title: 'Phân Tách Nhiệm Vụ',
          showBack: true,
          showTitleRays: true,
        ),
        body: SafeArea(
          top: false,
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
            children: [
              // Reassuring Status Banner (D-26)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainerHigh,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Row(
                children: [
                  Icon(
                    Icons.shield_outlined,
                    size: 20,
                    color: MelbelColors.kSecondary,
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      'Chưa ghi vào lịch chính thức · Lịch của bạn vẫn an toàn',
                      style: TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w500,
                        color: MelbelColors.kSecondary,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),

            // Main Card: Phương án chưa xác nhận (D-26)
            Container(
              padding: const EdgeInsets.all(20),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainer,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Row(
                        children: [
                          Container(
                            width: 8,
                            height: 8,
                            decoration: BoxDecoration(
                              color: MelbelColors.kOnSurfaceVariant,
                              shape: BoxShape.circle,
                            ),
                          ),
                          const SizedBox(width: 8),
                          Text(
                            'BẢN NHÁP TẠM LƯU',
                            style: TextStyle(
                              fontSize: 12,
                              fontWeight: FontWeight.w600,
                              letterSpacing: 0.5,
                              color: MelbelColors.kOnSurfaceVariant,
                            ),
                          ),
                        ],
                      ),
                      Icon(
                        Icons.nightlight,
                        size: 20,
                        color: MelbelColors.kOnSurfaceVariant,
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  Text(
                    displayTitle,
                    style: TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 10,
                      vertical: 4,
                    ),
                    decoration: BoxDecoration(
                      color: MelbelColors.kSurfaceContainerHighest,
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(
                          Icons.schedule,
                          size: 15,
                          color: MelbelColors.kOnSurfaceVariant,
                        ),
                        const SizedBox(width: 6),
                        Text(
                          'Hạn: ${task.formatDeadline(task.deadline)}',
                          style: TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w500,
                            color: MelbelColors.kOnSurfaceVariant,
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 16),
                  Divider(color: MelbelColors.kSurfaceContainerHighest),
                  const SizedBox(height: 12),

                  // 2 Sub-boxes
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: MelbelColors.kSurfaceContainerLow,
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Icon(
                          Icons.pending_actions,
                          size: 18,
                          color: MelbelColors.kOnSurfaceVariant,
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                'Chưa chốt',
                                style: TextStyle(
                                  fontSize: 12,
                                  color: MelbelColors.kOnSurfaceVariant,
                                ),
                              ),
                              const SizedBox(height: 2),
                              Text(
                                feasibilityText,
                                style: TextStyle(
                                  fontSize: 13,
                                  color: MelbelColors.kOnSurface,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 8),

                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: MelbelColors.kSurfaceContainerLow,
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Icon(
                          Icons.timelapse,
                          size: 18,
                          color: MelbelColors.kSecondary,
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                'Số phiên dự kiến',
                                style: TextStyle(
                                  fontSize: 12,
                                  color: MelbelColors.kOnSurfaceVariant,
                                ),
                              ),
                              const SizedBox(height: 2),
                              Text(
                                '$sessionCount phiên đã đề xuất',
                                style: TextStyle(
                                  fontSize: 13,
                                  color: MelbelColors.kOnSurface,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 14),

                  // Gentle non-judgmental note
                  Row(
                    children: [
                      Icon(Icons.eco, size: 16, color: MelbelColors.kSecondary),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          'Bạn có thể chỉnh sửa lại bất cứ lúc nào, không lo bị trùng lịch.',
                          style: TextStyle(
                            fontSize: 13,
                            color: MelbelColors.kOnSurfaceVariant,
                          ),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
            const SizedBox(height: 24),

            // Primary Action: Xem lại phương án
            PrimaryCta(
              label: 'Xem lại phương án',
              icon: Icons.edit_calendar,
              onPressed: () => Navigator.pop(context),
            ),
            const SizedBox(height: 12),

            // Secondary: Để sau
            SecondaryCta(
              label: 'Để sau',
              onPressed: () {
                MainShellScreen.returnHome(context);
              },
            ),
            const SizedBox(height: 20),

            // Footer lock
            Center(
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    Icons.lock,
                    size: 14,
                    color: MelbelColors.kOnSurfaceVariant,
                  ),
                  const SizedBox(width: 6),
                  Flexible(
                      child: Text(
                    'Lưu trữ an toàn trong không gian riêng của bạn',
                    style: TextStyle(
                      fontSize: 12,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  )),
                ],
              ),
            ),
          ],
        ),
      ),
    ),
    );
  }
}

// ============================================================================
// D-27: ĐÃ CHỐT LỊCH THÀNH CÔNG (PLAN LOCKED SCREEN)
// ============================================================================

class PlanLockedScreen extends StatelessWidget {
  const PlanLockedScreen({super.key});

  Future<void> _startFirstSession(BuildContext context) async {
    final taskProvider = context.read<TaskProvider>();
    final task = taskProvider.confirmedTask;
    if (task == null || task.sessions.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Chưa có phiên đầu tiên trong lịch.')),
      );
      return;
    }
    final session = task.sessions.first;
    final matchingSteps =
        task.steps.where((step) => step.stepId == session.stepId);
    final step = matchingSteps.isNotEmpty
        ? matchingSteps.first
        : (task.steps.isEmpty ? null : task.steps.first);
    final focusSession = FocusSessionInfo(
      sessionId: session.sessionId,
      scheduleRevision: session.scheduleRevision,
      isLastSessionOfStep: !task.sessions.any((other) =>
          other.stepId == session.stepId &&
          other.status == 'scheduled' &&
          other.scheduledStart.isAfter(session.scheduledStart)),
      taskTitle: taskProvider.title,
      stepTitle: step?.title ?? 'Bước hiện tại',
      expectedOutput:
          matchingSteps.isNotEmpty ? matchingSteps.first.expectedOutput : null,
      scheduledStart: session.scheduledStart,
      scheduledEnd: session.scheduledEnd,
      workSeconds: session.workSeconds,
      restSeconds: session.restSeconds,
      microTaskText: session.microTaskText,
      segments: session.segments,
      deadline: taskProvider.deadline,
    );
    await context.read<FocusProvider>().startSession(focusSession);
    if (!context.mounted) return;
    await Navigator.push(
      context,
      MaterialPageRoute<void>(builder: (_) => const FocusScreen()),
    );
  }

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();
    final palette = context.melbel;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final textTheme = Theme.of(context).textTheme;
    final confirmedTask = task.confirmedTask;
    final title = (confirmedTask?.title ?? task.title).trim();
    final input = task.inputText.trim();
    final firstSentence = input.split(RegExp(r'[.!?](?:\s|$)')).first.trim();
    final displayTitle = title.isEmpty
        ? 'Công việc'
        : input.startsWith(title) &&
                firstSentence.isNotEmpty &&
                firstSentence.length < title.length
            ? firstSentence
            : title;
    final confirmedSessions = confirmedTask?.sessions ?? const [];
    final confirmedSteps = confirmedTask?.steps ?? const [];
    final firstSession = confirmedSessions.firstOrNull;
    final firstStep = firstSession == null
        ? confirmedSteps.firstOrNull
        : confirmedSteps
                .where((s) => s.stepId == firstSession.stepId)
                .firstOrNull ??
            confirmedSteps.firstOrNull;
    final firstMinutes =
        firstSession == null ? 0 : (firstSession.workSeconds / 60).ceil();
    final firstMicroTask = firstSession?.microTaskText.trim() ?? '';
    final localizations = MaterialLocalizations.of(context);
    final firstStart = firstSession?.scheduledStart.toLocal();
    final deadlineAt =
        task.draft?.summary['time_constraint']?['deadline_at'] as String?;
    final confirmedDeadline = deadlineAt == null
        ? task.formatDeadline(task.deadline)
        : task.formatDeadline(DateTime.tryParse(deadlineAt)?.toLocal());
    final calendarStatus = switch (task.calendarSyncState) {
      'synced' => 'Đã đồng bộ Google Calendar',
      'pending' || 'outbox_queued' => 'Đang chờ đồng bộ lịch',
      'not_integrated' => 'Chưa liên kết lịch để ghi phiên',
      'conflict' => 'Có thay đổi cần xem lại',
      'failed' => 'Đồng bộ chưa thành công',
      _ => 'Chưa có trạng thái đồng bộ',
    };
    final haloColor =
        isDark ? const Color(0xFFA797C4) : const Color(0xFFFFF8E2);

    return MelbelPageBackdrop(
      showTopCloud: false,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: const MelbelAppBar(
          title: 'Kế hoạch đã sẵn sàng',
          showBack: true,
          showTitleRays: true,
        ),
        bottomNavigationBar: ColoredBox(
          color: Colors.transparent,
        child: Padding(
          padding: EdgeInsets.fromLTRB(
            20,
            8,
            20,
            16 + MediaQuery.paddingOf(context).bottom,
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              PrimaryCta(
                label: firstMinutes > 0
                    ? 'Bắt đầu đếm giờ ${firstMinutes}p ngay bây giờ'
                    : 'Bắt đầu phiên đầu tiên',
                icon: Icons.play_arrow_rounded,
                onPressed: confirmedSessions.isEmpty
                    ? null
                    : () => _startFirstSession(context),
              ),
              const SizedBox(height: 10),
              SecondaryCta(
                label: 'Để sau, nhắc tôi trước 15 phút',
                icon: Icons.notifications_active_outlined,
                height: 50,
                onPressed: () {
                  MainShellScreen.returnHome(context);
                },
              ),
            ],
          ),
        ),
      ),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: MelbelLayout.pageInsets(context, top: 12, bottom: 28),
          children: [
            MelbelCard(
              tone: MelbelCardTone.sage,
              borderRadius: 28,
              padding: const EdgeInsets.fromLTRB(20, 22, 20, 22),
              child: Column(
                children: [
                  Stack(
                    clipBehavior: Clip.none,
                    alignment: Alignment.center,
                    children: [
                      Container(
                        width: 106,
                        height: 106,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: haloColor,
                        ),
                        alignment: Alignment.center,
                        child: const MelbelMascot(
                          expression: CatExpression.happy,
                          forceLightSvg: true,
                          width: 94,
                          height: 84,
                        ),
                      ),
                      Positioned(
                        top: 2,
                        right: -10,
                        child: Transform.rotate(
                          angle: 0.35,
                          child: MelbelDoodleRays(
                            size: 20,
                            color: isDark ? palette.amber : palette.sage,
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  Text(
                    'Kế hoạch đã sẵn sàng!',
                    textAlign: TextAlign.center,
                    style: textTheme.headlineSmall?.copyWith(
                      fontWeight: FontWeight.w800,
                      color: palette.onSageSoft,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    'Bạn đã vượt qua bước khó nhất: bắt đầu lập kế hoạch.',
                    textAlign: TextAlign.center,
                    style: textTheme.bodyMedium?.copyWith(
                      color: palette.onSageSoft.withValues(alpha: 0.86),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),
            MelbelCard(
              borderRadius: 28,
              padding: const EdgeInsets.all(20),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Eyebrow('Phiên đầu tiên'),
                            const SizedBox(height: 4),
                            Text(
                              firstStep?.title ?? displayTitle,
                              style: textTheme.titleLarge?.copyWith(
                                fontWeight: FontWeight.w800,
                                color: palette.onSurface,
                              ),
                            ),
                            if (firstStep != null &&
                                firstStep.title != displayTitle) ...[
                              const SizedBox(height: 2),
                              Text(
                                displayTitle,
                                style: textTheme.bodySmall?.copyWith(
                                  color: palette.onSurfaceVariant,
                                ),
                              ),
                            ],
                          ],
                        ),
                      ),
                      const SizedBox(width: 12),
                      Container(
                        width: 40,
                        height: 40,
                        decoration: BoxDecoration(
                          color: palette.sageSoft,
                          borderRadius: BorderRadius.circular(14),
                        ),
                        child: Icon(
                          Icons.lock_rounded,
                          size: 20,
                          color: palette.accentInk,
                        ),
                      ),
                    ],
                  ),
                  if (firstMicroTask.isNotEmpty) ...[
                    const SizedBox(height: 14),
                    Container(
                      width: double.infinity,
                      padding: const EdgeInsets.all(14),
                      decoration: BoxDecoration(
                        color:
                            isDark ? palette.sageSoft : palette.amberSoft,
                        borderRadius: BorderRadius.circular(18),
                      ),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Icon(
                            Icons.rocket_launch_outlined,
                            size: 18,
                            color: isDark ? palette.sage : palette.amber,
                          ),
                          const SizedBox(width: 10),
                          Expanded(
                            child: Text(
                              'Khởi động 2p: $firstMicroTask',
                              style: textTheme.bodySmall?.copyWith(
                                fontWeight: FontWeight.w600,
                                color: isDark
                                    ? palette.onSageSoft
                                    : palette.onAmberSoft,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                  const SizedBox(height: 14),
                  _lockedRow(
                    context,
                    icon: Icons.play_circle_outline_rounded,
                    iconColor: palette.sage,
                    title: 'Thời điểm dự kiến',
                    desc: firstStart == null
                        ? 'Chưa có giờ'
                        : '${firstStart.day.toString().padLeft(2, '0')}/${firstStart.month.toString().padLeft(2, '0')} ${firstStart.hour.toString().padLeft(2, '0')}:${firstStart.minute.toString().padLeft(2, '0')}',
                  ),
                  const SizedBox(height: 10),
                  _lockedRow(
                    context,
                    icon: Icons.cloud_done_outlined,
                    iconColor: palette.sage,
                    title: 'Google Calendar',
                    desc: calendarStatus,
                  ),
                  if (task.canRefreshCalendarSync)
                    TextButton.icon(
                      icon: const Icon(Icons.sync_rounded),
                      label: Text(task.isApiBusy
                          ? 'Đang cập nhật…'
                          : 'Cập nhật đồng bộ'),
                      onPressed: task.isApiBusy
                          ? null
                          : () async {
                              try {
                                await task.refreshCalendarSync();
                              } catch (_) {
                                if (!context.mounted) return;
                                ScaffoldMessenger.of(context).showSnackBar(
                                  const SnackBar(
                                      content: Text(
                                          'Chưa cập nhật được trạng thái lịch.')),
                                );
                              }
                            },
                    ),
                  const SizedBox(height: 10),
                  _lockedRow(
                    context,
                    icon: Icons.flag_outlined,
                    iconColor: palette.onSurfaceVariant,
                    title: 'Hạn chót',
                    desc: confirmedDeadline,
                  ),
                  if (confirmedSessions.isNotEmpty) ...[
                    const SizedBox(height: 10),
                    Theme(
                      data: Theme.of(context)
                          .copyWith(dividerColor: Colors.transparent),
                      child: ExpansionTile(
                        tilePadding: EdgeInsets.zero,
                        title: Text(
                            'Xem lịch ${confirmedSessions.length} phiên đã chốt'),
                        children: [
                          for (final session in confirmedSessions)
                            ListTile(
                              contentPadding: EdgeInsets.zero,
                              leading:
                                  const Icon(Icons.event_available_outlined),
                              title: Text(
                                localizations.formatShortDate(
                                  session.scheduledStart.toLocal(),
                                ),
                              ),
                              subtitle: Text(
                                '${localizations.formatTimeOfDay(TimeOfDay.fromDateTime(session.scheduledStart.toLocal()), alwaysUse24HourFormat: true)} – '
                                '${localizations.formatTimeOfDay(TimeOfDay.fromDateTime(session.scheduledEnd.toLocal()), alwaysUse24HourFormat: true)}',
                              ),
                            ),
                        ],
                      ),
                    ),
                  ],
                ],
              ),
            ),
            const SizedBox(height: 12),
            Center(
              child: TextButton.icon(
                onPressed: () => _open(context, const SessionReminderScreen()),
                icon: const Icon(Icons.notifications_none),
                label: const Text('Xem thử nhắc phiên'),
              ),
            ),
          ],
        ),
      ),
    ),
    );
  }

  Widget _lockedRow(
    BuildContext context, {
    required IconData icon,
    required Color iconColor,
    required String title,
    required String desc,
  }) {
    final palette = context.melbel;
    final textTheme = Theme.of(context).textTheme;
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: palette.mutedSurface,
        borderRadius: BorderRadius.circular(18),
      ),
      child: Row(
        children: [
          Container(
            width: 38,
            height: 38,
            decoration: BoxDecoration(
              color: palette.surface,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Icon(icon, size: 20, color: iconColor),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: textTheme.labelSmall?.copyWith(
                    color: palette.onSurfaceVariant,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  desc,
                  style: textTheme.titleSmall?.copyWith(
                    color: palette.onSurface,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

// ============================================================================
// D-28: LỖI AI / PHƯƠNG ÁN DỰ PHÒNG (AI ERROR FALLBACK SCREEN)
// ============================================================================

class AiFallbackScreen extends StatelessWidget {
  const AiFallbackScreen({super.key});

  Future<void> _requestFallback(BuildContext context) async {
    final task = context.read<TaskProvider>();
    try {
      await task.generateCandidateOnBackend();
      if (!context.mounted) return;
      await Navigator.push(
        context,
        MaterialPageRoute<void>(builder: (_) => const CandidateReviewScreen()),
      );
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Chưa tạo được phương án dự phòng: $error'),
          ),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();
    final displayTitle =
        task.title.isEmpty ? 'Soạn báo cáo đánh giá quý 3' : task.title;

    return MelbelPageBackdrop(
      showTopCloud: false,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: const MelbelAppBar(
          title: 'Phân Tách Nhiệm Vụ',
          showBack: true,
          showTitleRays: true,
        ),
        body: SafeArea(
          top: false,
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
            children: [
              // Character Mascot with Peaceful Blob (D-28)
              Center(
                child: Column(
                  children: [
                    const MelbelCushionAvatar(
                      expression: CatExpression.sleepy,
                      width: 88,
                      height: 78,
                    ),
                  const SizedBox(height: 16),
                  Text(
                    'Không sao cả, mình cùng đi từng bước nhé',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontSize: 20,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    'Hệ thống chuẩn bị sẵn lịch mẫu tiêu chuẩn cho bạn',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontSize: 14,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),

            // Fallback Plan Card (D-28)
            Container(
              padding: const EdgeInsets.all(20),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainer,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 10,
                          vertical: 4,
                        ),
                        decoration: BoxDecoration(
                          color: MelbelColors.kSecondaryContainer.withValues(
                            alpha: 0.4,
                          ),
                          borderRadius: BorderRadius.circular(999),
                        ),
                        child: Row(
                          children: [
                            Icon(
                              Icons.circle,
                              size: 8,
                              color: MelbelColors.kSecondary,
                            ),
                            const SizedBox(width: 6),
                            Text(
                              'Lộ trình chuẩn',
                              style: TextStyle(
                                fontSize: 12,
                                fontWeight: FontWeight.w500,
                                color: MelbelColors.kSecondary,
                              ),
                            ),
                          ],
                        ),
                      ),
                      Row(
                        children: [
                          Icon(
                            Icons.schedule,
                            size: 15,
                            color: MelbelColors.kOnSurfaceVariant,
                          ),
                          const SizedBox(width: 4),
                          Text(
                            task.formatDeadline(task.deadline),
                            style: TextStyle(
                              fontSize: 12,
                              color: MelbelColors.kOnSurfaceVariant,
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  Text(
                    displayTitle,
                    style: TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                  const SizedBox(height: 16),

                  // Rhythm & Cadence Details
                  Container(
                    padding: const EdgeInsets.all(14),
                    decoration: BoxDecoration(
                      color: MelbelColors.kSurfaceContainerHigh,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Icon(
                              Icons.spa,
                              size: 18,
                              color: MelbelColors.kPrimaryContainer,
                            ),
                            const SizedBox(width: 8),
                            Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  'Nhịp độ gợi ý',
                                  style: TextStyle(
                                    fontSize: 12,
                                    color: MelbelColors.kOnSurfaceVariant,
                                  ),
                                ),
                                Text(
                                  'Nhịp tiêu chuẩn Pomodoro êm dịu',
                                  style: TextStyle(
                                    fontSize: 14,
                                    fontWeight: FontWeight.w500,
                                    color: MelbelColors.kOnSurface,
                                  ),
                                ),
                              ],
                            ),
                          ],
                        ),
                        const SizedBox(height: 12),
                        Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            Text(
                              '3 phiên (25p tập trung + 5p nghỉ)',
                              style: TextStyle(
                                fontSize: 12,
                                color: MelbelColors.kOnSurfaceVariant,
                              ),
                            ),
                            Text(
                              '90 phút',
                              style: TextStyle(
                                fontSize: 13,
                                fontWeight: FontWeight.w500,
                                color: MelbelColors.kPrimaryContainer,
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 8),

                        // Visual Cadence Blocks (5 blocks)
                        Row(
                          children: [
                            _cadenceBlock(
                              '25p',
                              MelbelColors.kPrimaryContainer,
                            ),
                            const SizedBox(width: 4),
                            _cadenceBlock(
                              '5p',
                              MelbelColors.kSecondaryContainer,
                              textColor: MelbelColors.kSecondary,
                            ),
                            const SizedBox(width: 4),
                            _cadenceBlock(
                              '25p',
                              MelbelColors.kPrimaryContainer,
                            ),
                            const SizedBox(width: 4),
                            _cadenceBlock(
                              '5p',
                              MelbelColors.kSecondaryContainer,
                              textColor: MelbelColors.kSecondary,
                            ),
                            const SizedBox(width: 4),
                            _cadenceBlock(
                              '25p',
                              MelbelColors.kPrimaryContainer,
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 14),

                  // Schedule slot timing
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Row(
                        children: [
                          Icon(
                            Icons.play_circle,
                            size: 18,
                            color: MelbelColors.kOnSurfaceVariant,
                          ),
                          const SizedBox(width: 8),
                          Text(
                            'Thời điểm khởi động',
                            style: TextStyle(
                              fontSize: 13,
                              color: MelbelColors.kOnSurfaceVariant,
                            ),
                          ),
                        ],
                      ),
                      Text(
                        'Giờ bắt đầu sẽ hiện trong phương án',
                        style: TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w500,
                          color: MelbelColors.kOnSurface,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),

                  // Soft Note
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: MelbelColors.kSurfaceContainerLow,
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Row(
                      children: [
                        Icon(
                          Icons.check_circle,
                          size: 18,
                          color: MelbelColors.kSecondary,
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            'Bạn luôn có thể điều chỉnh thời lượng bất kỳ lúc nào.',
                            style: TextStyle(
                              fontSize: 13,
                              color: MelbelColors.kSecondary,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 24),

            // Actions
            PrimaryCta(
              label: 'Tạo phương án dự phòng',
              icon: Icons.calendar_today,
              onPressed:
                  task.isApiBusy ? null : () => _requestFallback(context),
            ),
            const SizedBox(height: 12),
            SecondaryCta(
              label: 'Tự chỉnh tay',
              onPressed: () => Navigator.pop(context),
            ),
          ],
        ),
      ),
    ),
    );
  }

  Widget _cadenceBlock(
    String label,
    Color bg, {
    Color? textColor,
  }) {
    return Expanded(
      child: Column(
        children: [
          Container(
            height: 10,
            decoration: BoxDecoration(
              color: bg,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          const SizedBox(height: 4),
          Text(
            label,
            style: TextStyle(
              fontSize: 12,
              color: textColor ?? MelbelColors.kOnSurfaceVariant,
            ),
          ),
        ],
      ),
    );
  }
}

// ============================================================================
// D-29: NHẮC TRƯỚC PHIÊN (PRE-SESSION REMINDER SCREEN)
// ============================================================================

class SessionReminderScreen extends StatelessWidget {
  const SessionReminderScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();
    final displayTitle =
        task.title.isEmpty ? 'Soạn báo cáo đánh giá quý 3' : task.title;

    return MelbelPageBackdrop(
      showTopCloud: false,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: const MelbelAppBar(
          title: 'Nhắc Trước Phiên',
          showBack: true,
          showTitleRays: true,
        ),
        body: SafeArea(
          top: false,
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
            children: [
              // Subtle Visual Hero: Moon Glow & Gentle Icon (D-29)
            Center(
              child: Column(
                children: [
                  Container(
                    width: 88,
                    height: 88,
                    decoration: BoxDecoration(
                      color: MelbelColors.kSurfaceContainer.withValues(
                        alpha: 0.6,
                      ),
                      shape: BoxShape.circle,
                    ),
                    alignment: Alignment.center,
                    child: Container(
                      width: 60,
                      height: 60,
                      decoration: BoxDecoration(
                        color: MelbelColors.kSurfaceContainerHigh,
                        shape: BoxShape.circle,
                      ),
                      child: Icon(
                        Icons.bedtime,
                        size: 30,
                        color: MelbelColors.kPrimaryContainer,
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                  Text(
                    'Sắp đến phiên làm việc',
                    style: TextStyle(
                      fontSize: 22,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    'Chuẩn bị vào phiên lúc 15:30.',
                    style: TextStyle(
                      fontSize: 15,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 24),

            // Focused Task Card (D-29)
            Container(
              padding: const EdgeInsets.all(20),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainer,
                borderRadius: BorderRadius.circular(16),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 10,
                          vertical: 4,
                        ),
                        decoration: BoxDecoration(
                          color: MelbelColors.kSurfaceContainerHigh,
                          borderRadius: BorderRadius.circular(999),
                        ),
                        child: Row(
                          children: [
                            Icon(
                              Icons.circle,
                              size: 8,
                              color: MelbelColors.kPrimaryContainer,
                            ),
                            const SizedBox(width: 6),
                            Text(
                              'Phiên 1 / 3 • 25 phút',
                              style: TextStyle(
                                fontSize: 12,
                                fontWeight: FontWeight.w500,
                                color: MelbelColors.kOnSurfaceVariant,
                              ),
                            ),
                          ],
                        ),
                      ),
                      Text(
                        'Hạn chót: 18:00 ngày mai',
                        style: TextStyle(
                          fontSize: 12,
                          color: MelbelColors.kOnSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  Text(
                    displayTitle,
                    style: TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Row(
                    children: [
                      Icon(
                        Icons.subdirectory_arrow_right,
                        size: 18,
                        color: MelbelColors.kSecondary,
                      ),
                      const SizedBox(width: 6),
                      Text(
                        'Bước 1: Soạn dàn ý',
                        style: TextStyle(
                          fontSize: 15,
                          fontWeight: FontWeight.w500,
                          color: MelbelColors.kSecondary,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 16),
                  Divider(color: MelbelColors.kSurfaceContainerHigh),
                  const SizedBox(height: 8),

                  // Psychological Buffer Line (2-minute starter micro-task)
                  Row(
                    children: [
                      Icon(
                        Icons.self_improvement,
                        size: 18,
                        color: MelbelColors.kPrimaryContainer,
                      ),
                      const SizedBox(width: 8),
                      Text(
                        'Có 2 phút khởi động nhẹ nhàng.',
                        style: TextStyle(
                          fontSize: 13,
                          color: MelbelColors.kOnSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
            const SizedBox(height: 24),

            // Action CTAs (D-29)
            PrimaryCta(
              label: 'Bắt đầu 25p',
              icon: Icons.play_arrow,
              onPressed: () {
                showMelbelInfo(
                  context,
                  title: 'Phiên 1 bắt đầu',
                  message:
                      'Chúc bạn làm việc êm dịu, không áp lực. Melbel sẽ đếm giờ yên lặng.',
                  icon: Icons.spa,
                );
              },
            ),
            const SizedBox(height: 12),
            SecondaryCta(
              label: 'Nghỉ thêm 5 phút',
              icon: Icons.snooze,
              onPressed: () {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                    content: Text('Đã cộng thêm 5 phút nghỉ ngơi.'),
                  ),
                );
              },
            ),
            const SizedBox(height: 8),
            Center(
              child: TextButton(
                onPressed: () => _open(context, const PlanOverviewScreen()),
                child: Text(
                  'Dời lịch',
                  style: TextStyle(
                    fontSize: 14,
                    color: MelbelColors.kOnSurfaceVariant,
                  ),
                ),
              ),
            ),
            const SizedBox(height: 16),

            // Demo link for D-30
            Center(
              child: TextButton.icon(
                onPressed: () => _open(context, const LockScreenPreview()),
                icon: Icon(
                  Icons.lock_clock,
                  size: 16,
                  color: MelbelColors.kOutline,
                ),
                label: Text(
                  'Xem giao diện thông báo màn hình khóa (D-30)',
                  style: TextStyle(
                    fontSize: 12,
                    color: MelbelColors.kOutline,
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    ),
    );
  }
}

// ============================================================================
// D-30: THÔNG BÁO MÀN HÌNH KHÓA (LOCK SCREEN NOTIFICATIONS PREVIEW)
// ============================================================================

class LockScreenPreview extends StatefulWidget {
  const LockScreenPreview({super.key});

  @override
  State<LockScreenPreview> createState() => _LockScreenPreviewState();
}

class _LockScreenPreviewState extends State<LockScreenPreview> {
  bool _flashlightOn = false;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: MelbelColors.kSurface,
      body: SafeArea(
        child: Column(
          children: [
            // Top exit preview button
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  IconButton(
                    onPressed: () => Navigator.pop(context),
                    icon: Icon(
                      Icons.close,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                  Text(
                    'Mô phỏng Màn hình khóa',
                    style: TextStyle(
                      fontSize: 13,
                      color: MelbelColors.kOutline,
                    ),
                  ),
                  const SizedBox(width: 48),
                ],
              ),
            ),

            Expanded(
              child: ListView(
                padding: const EdgeInsets.symmetric(horizontal: 20),
                children: [
                  const SizedBox(height: 12),
                  // Lock Icon
                  Center(
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(
                          Icons.lock,
                          size: 16,
                          color: MelbelColors.kOnSurfaceVariant,
                        ),
                        const SizedBox(width: 4),
                        Text(
                          'BẢO MẬT',
                          style: TextStyle(
                            fontSize: 12,
                            letterSpacing: 1.5,
                            fontWeight: FontWeight.w600,
                            color: MelbelColors.kOnSurfaceVariant,
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 8),

                  // Clock Display
                  Center(
                    child: Text(
                      '15:25',
                      style: TextStyle(
                        fontSize: 64,
                        fontWeight: FontWeight.w500,
                        color: MelbelColors.kPrimary,
                        letterSpacing: -1,
                        fontFeatures: const [FontFeature.tabularFigures()],
                      ),
                    ),
                  ),
                  Center(
                    child: Text(
                      'Thứ Năm, 24 tháng 10',
                      style: TextStyle(
                        fontSize: 15,
                        color: MelbelColors.kOnSurfaceVariant,
                      ),
                    ),
                  ),
                  const SizedBox(height: 32),

                  // Notification 1: Pre-session reminder (D-30)
                  _notificationCard(
                    title: 'Sắp đến phiên: Bắt đầu 25p',
                    body:
                        'Soạn báo cáo quý 3 · Bước 1: Soạn dàn ý sơ bộ. Hạn chót: 18:00.',
                    timeAgo: '5 phút trước',
                    btn1Text: 'Bắt đầu ngay',
                    btn2Text: 'Nghỉ thêm 5p',
                    onBtn1: () => ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('Bắt đầu phiên 25 phút.')),
                    ),
                    onBtn2: () => ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('Đã hoãn thêm 5 phút.')),
                    ),
                  ),
                  const SizedBox(height: 14),

                  // Notification 2: End of session follow-up (D-30)
                  _notificationCard(
                    title: 'Hết phiên rồi, bạn làm đến đâu rồi?',
                    body:
                        'Đã hoàn thành 25 phút. Nghỉ ngơi 5 phút hoặc ghi nhanh tiến độ nhé.',
                    timeAgo: 'Vừa xong',
                    btn1Text: 'Nghỉ giải lao',
                    btn2Text: 'Tiếp tục phiên',
                    isBtn1Secondary: true,
                    onBtn1: () => ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('Bắt đầu nghỉ 5 phút.')),
                    ),
                    onBtn2: () => ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('Tiếp tục phiên làm việc.')),
                    ),
                  ),
                  const SizedBox(height: 48),

                  // Swipe indicator
                  Center(
                    child: Column(
                      children: [
                        Icon(
                          Icons.keyboard_arrow_up,
                          size: 20,
                          color: MelbelColors.kOnSurfaceVariant,
                        ),
                        const SizedBox(height: 2),
                        Text(
                          'Vuốt lên để mở',
                          style: TextStyle(
                            fontSize: 12,
                            letterSpacing: 0.5,
                            color: MelbelColors.kOnSurfaceVariant,
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 24),
                ],
              ),
            ),

            // Bottom shortcuts (Flashlight & Camera)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 16),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Semantics(
                    button: true,
                    toggled: _flashlightOn,
                    label: 'Đèn pin',
                    excludeSemantics: true,
                    child: InkWell(
                      onTap: () => setState(() => _flashlightOn = !_flashlightOn),
                      borderRadius: BorderRadius.circular(999),
                      child: Container(
                        width: 48,
                        height: 48,
                        decoration: BoxDecoration(
                          color: _flashlightOn
                              ? MelbelColors.kPrimaryContainer
                              : MelbelColors.kSurfaceContainer,
                          shape: BoxShape.circle,
                        ),
                        child: Icon(
                          Icons.flashlight_on,
                          size: 22,
                          color: _flashlightOn
                              ? MelbelColors.kOnPrimaryContainer
                              : MelbelColors.kOnSurface,
                        ),
                      ),
                    ),
                  ),
                  Container(
                    width: 120,
                    height: 4,
                    decoration: BoxDecoration(
                      color: MelbelColors.kSurfaceBright.withValues(alpha: 0.4),
                      borderRadius: BorderRadius.circular(999),
                    ),
                  ),
                  Container(
                    width: 48,
                    height: 48,
                    decoration: BoxDecoration(
                      color: MelbelColors.kSurfaceContainer,
                      shape: BoxShape.circle,
                    ),
                    child: Icon(
                      Icons.photo_camera,
                      size: 22,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _notificationCard({
    required String title,
    required String body,
    required String timeAgo,
    required String btn1Text,
    required String btn2Text,
    required VoidCallback onBtn1,
    required VoidCallback onBtn2,
    bool isBtn1Secondary = false,
  }) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: MelbelColors.kSurfaceContainer,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Row(
                children: [
                  Container(
                    width: 20,
                    height: 20,
                    decoration: BoxDecoration(
                      color: MelbelColors.kSecondaryContainer,
                      shape: BoxShape.circle,
                    ),
                    child: Icon(
                      Icons.dark_mode,
                      size: 13,
                      color: MelbelColors.kSecondary,
                    ),
                  ),
                  const SizedBox(width: 8),
                  Text(
                    'Melbel',
                    style: TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w500,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                  const SizedBox(width: 4),
                  Text(
                    '•',
                    style: TextStyle(
                      fontSize: 12,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                  const SizedBox(width: 4),
                  Text(
                    timeAgo,
                    style: TextStyle(
                      fontSize: 12,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                ],
              ),
              Icon(
                Icons.expand_more,
                size: 18,
                color: MelbelColors.kOnSurfaceVariant,
              ),
            ],
          ),
          const SizedBox(height: 10),
          Text(
            title,
            style: TextStyle(
              fontSize: 15,
              fontWeight: FontWeight.w600,
              color: MelbelColors.kPrimary,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            body,
            style: TextStyle(
              fontSize: 13,
              color: MelbelColors.kOnSurfaceVariant,
              height: 1.4,
            ),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Semantics(
                button: true,
                label: btn1Text,
                excludeSemantics: true,
                child: InkWell(
                  onTap: onBtn1,
                  borderRadius: BorderRadius.circular(12),
                  child: Container(
                    constraints: const BoxConstraints(minHeight: 44),
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    decoration: BoxDecoration(
                      color: MelbelColors.kSurfaceContainerHigh,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    alignment: Alignment.center,
                    child: Text(
                      btn1Text,
                      style: TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w500,
                        color: isBtn1Secondary
                            ? MelbelColors.kSecondary
                            : MelbelColors.kPrimary,
                      ),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Semantics(
                button: true,
                label: btn2Text,
                excludeSemantics: true,
                child: InkWell(
                  onTap: onBtn2,
                  borderRadius: BorderRadius.circular(12),
                  child: Container(
                    constraints: const BoxConstraints(minHeight: 44),
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    decoration: BoxDecoration(
                      color: MelbelColors.kSurfaceContainerLow,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    alignment: Alignment.center,
                    child: Text(
                      btn2Text,
                      style: TextStyle(
                        fontSize: 13,
                        color: MelbelColors.kOnSurfaceVariant,
                      ),
                    ),
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
