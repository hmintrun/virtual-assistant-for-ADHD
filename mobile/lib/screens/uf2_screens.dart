import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../services/voice_service.dart';
import '../models/focus_session_model.dart';
import '../providers/focus_provider.dart';
import '../providers/task_provider.dart';
import '../widgets/melbel_ui.dart';
import 'home_screen.dart';
import 'candidate_review_screen.dart';
import 'focus_screen.dart';

// Helper for opening new screens
Future<void> _open(BuildContext context, Widget page) => Navigator.push<void>(
      context,
      MaterialPageRoute<void>(builder: (_) => page),
    );

// ============================================================================
// D-21: CHIA VIỆC (TASK DECOMPOSITION SCREEN)
// ============================================================================

class DecompositionChoiceScreen extends StatefulWidget {
  const DecompositionChoiceScreen({super.key});

  @override
  State<DecompositionChoiceScreen> createState() =>
      _DecompositionChoiceScreenState();
}

class _DecompositionChoiceScreenState extends State<DecompositionChoiceScreen>
    with SingleTickerProviderStateMixin {
  final TextEditingController _intentController = TextEditingController();
  bool _isListening = false;
  bool _isGenerating = false;
  late AnimationController _pulseController;

  @override
  void initState() {
    super.initState();
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 900),
    )..repeat(reverse: true);
  }

  @override
  void dispose() {
    _intentController.dispose();
    _pulseController.dispose();
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

  void _startHold() {
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text(
          'Nhập giọng nói chưa sẵn sàng. Hãy gõ ý tưởng vào ô bên dưới.',
        ),
      ),
    );
  }

  void _stopHold() {
    if (_isListening) setState(() => _isListening = false);
  }

  Future<void> _generateCandidate() async {
    final task = context.read<TaskProvider>();
    if (_isGenerating || task.isApiBusy) return;
    setState(() => _isGenerating = true);
    try {
      final proposal = _intentController.text.trim();
      await task.generateCandidateOnBackend(
        stepProposal: proposal.isEmpty ? null : proposal,
      );
      if (!mounted) return;
      await Navigator.push(
        context,
        MaterialPageRoute<void>(builder: (_) => const CandidateReviewScreen()),
      );
    } catch (error) {
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Chưa tạo được phương án: $error')),
        );
    } finally {
      if (mounted) setState(() => _isGenerating = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();

    return Scaffold(
      appBar: const MelbelAppBar(title: 'Phân Tách Nhiệm Vụ', showBack: true),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
          children: [
            // Mascot Container Melbel (D-21)
            Center(
              child: Column(
                children: [
                  const SizedBox(height: 8),
                  AnimatedScale(
                    scale: _isListening ? 1.05 : 1.0,
                    duration: const Duration(milliseconds: 200),
                    child: const MelbelMascot(),
                  ),
                  const SizedBox(height: 12),
                  // Companion status badge
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 12,
                      vertical: 5,
                    ),
                    decoration: BoxDecoration(
                      color: MelbelColors.kSurfaceContainerHigh,
                      borderRadius: BorderRadius.circular(999),
                      border: Border.all(color: MelbelColors.kOutline),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Container(
                          width: 6,
                          height: 6,
                          decoration: BoxDecoration(
                            color: MelbelColors.kSecondary,
                            shape: BoxShape.circle,
                          ),
                        ),
                        const SizedBox(width: 6),
                        Text(
                          _isListening
                              ? 'Melbel đang nghe bạn...'
                              : 'Melbel đang lắng nghe',
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 12,
                            fontWeight: FontWeight.w500,
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

            // Header & Guiding Copy
            Center(
              child: Column(
                children: [
                  Text(
                    'Bạn muốn chia việc này thế nào?',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 22,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kOnSurface,
                      letterSpacing: -0.4,
                    ),
                  ),
                  SizedBox(height: 8),
                  Text(
                    'Chia theo phiên nhỏ hoặc để Melbel tính toán thời gian phù hợp với bạn.',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 14,
                      color: MelbelColors.kOnSurfaceVariant,
                      height: 1.4,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),

            // Primary Input Card (Executive Shielding)
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainer,
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: MelbelColors.kOutline),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Ý định của bạn',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 12,
                      fontWeight: FontWeight.w500,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                  const SizedBox(height: 8),
                  TextField(
                    controller: _intentController,
                    maxLines: 3,
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 16,
                      color: MelbelColors.kOnSurface,
                    ),
                    decoration: InputDecoration(
                      hintText: 'Nhập ý định hoặc số phiên bạn muốn làm...',
                      hintStyle: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 15,
                        color: MelbelColors.kOnSurfaceVariant,
                      ),
                      border: InputBorder.none,
                      contentPadding: EdgeInsets.zero,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),

            // Quick Selection Micro-Chips
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              clipBehavior: Clip.none,
              child: Row(
                children: [
                  _quickChip(
                    '2 phiên nhẹ nhàng',
                    () => _applyChip('2 phiên nhẹ nhàng (15p)'),
                  ),
                  const SizedBox(width: 8),
                  _quickChip(
                    '3 chặng thong thả',
                    () => _applyChip('3 chặng có nghỉ giãn cách'),
                  ),
                  const SizedBox(width: 8),
                  _quickChip(
                    'Khởi động 5 phút',
                    () => _applyChip('Làm thử 5 phút trước'),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),

            // Voice Input Option: Hold-to-Talk Mic
            Center(
              child: Column(
                children: [
                  GestureDetector(
                    onTapDown: (_) => _startHold(),
                    onTapUp: (_) => _stopHold(),
                    onTapCancel: () => _stopHold(),
                    child: Container(
                      width: 64,
                      height: 64,
                      decoration: BoxDecoration(
                        color: _isListening
                            ? MelbelColors.kSelectedSurface
                            : MelbelColors.kSurfaceContainer,
                        shape: BoxShape.circle,
                        border: Border.all(
                          color: _isListening
                              ? MelbelColors.kPrimaryContainer
                              : MelbelColors.kOutline,
                        ),
                      ),
                      child: Icon(
                        Icons.mic,
                        size: 28,
                        color: _isListening
                            ? MelbelColors.kPrimaryContainer
                            : MelbelColors.kOnSurface,
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    _isListening ? 'Melbel đang nghe bạn nói...' : 'Giữ để nói',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 12,
                      fontWeight: FontWeight.w500,
                      color: _isListening
                          ? MelbelColors.kPrimaryContainer
                          : MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 24),

            // Primary Hero Action: Để AI đề xuất
            InkWell(
              onTap: _isGenerating
                  ? null
                  : () {
                      task.setDecompositionChoice('ai');
                      _generateCandidate();
                    },
              borderRadius: BorderRadius.circular(16),
              child: Container(
                height: 56,
                decoration: BoxDecoration(
                  color: MelbelColors.kPrimaryContainer,
                  borderRadius: BorderRadius.circular(16),
                ),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(
                      Icons.auto_awesome,
                      color: MelbelColors.kOnPrimary,
                      size: 20,
                    ),
                    const SizedBox(width: 8),
                    Text(
                      _isGenerating ? 'Đang tạo phương án…' : 'Để AI đề xuất',
                      style: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 16,
                        fontWeight: FontWeight.w600,
                        color: MelbelColors.kOnPrimary,
                      ),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 12),

            // Secondary Quiet Path: Tự chọn phiên thủ công
            InkWell(
              onTap: _isGenerating
                  ? null
                  : () {
                      task.setDecompositionChoice('manual');
                      _generateCandidate();
                    },
              borderRadius: BorderRadius.circular(16),
              child: Container(
                height: 56,
                decoration: BoxDecoration(
                  color: MelbelColors.kSurfaceContainer,
                  borderRadius: BorderRadius.circular(16),
                  border: Border.all(color: MelbelColors.kOutline),
                ),
                child: Center(
                  child: Text(
                    'Tự chọn phiên thủ công',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 16,
                      fontWeight: FontWeight.w500,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                ),
              ),
            ),
            const SizedBox(height: 12),

            // Demo link for AI Error Fallback (D-28)
            Center(
              child: TextButton.icon(
                onPressed: () => _open(context, const AiFallbackScreen()),
                icon: Icon(
                  Icons.sync_problem,
                  size: 16,
                  color: MelbelColors.kOnSurfaceVariant,
                ),
                label: Text(
                  'Xem thử kịch bản AI phản hồi chậm (D-28)',
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 12,
                    color: MelbelColors.kOnSurfaceVariant,
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _quickChip(String text, VoidCallback onTap) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(16),
      child: Container(
        height: 40,
        padding: const EdgeInsets.symmetric(horizontal: 16),
        decoration: BoxDecoration(
          color: MelbelColors.kSurfaceContainer,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: MelbelColors.kOutline),
        ),
        alignment: Alignment.center,
        child: Text(
          text,
          style: TextStyle(
            fontFamily: 'Be Vietnam Pro',
            fontSize: 14,
            fontWeight: FontWeight.w500,
            color: MelbelColors.kOnSurfaceVariant,
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
    return const Scaffold(
        appBar: MelbelAppBar(title: 'Kế hoạch', showBack: true),
        body: Center(
            child: Padding(
                padding: EdgeInsets.all(24),
                child: Text(
                    'Chưa có phương án từ máy chủ. Hãy quay lại bản nháp để tạo kế hoạch.'))));
  }
}

class VoiceAssistantModal extends StatefulWidget {
  const VoiceAssistantModal({required this.onApply, super.key});
  final ValueChanged<String> onApply;

  @override
  State<VoiceAssistantModal> createState() => _VoiceAssistantModalState();
}

class _VoiceAssistantModalState extends State<VoiceAssistantModal> {
  final _controller = TextEditingController();
  final _speech = OnDeviceSpeechService();
  bool _listening = false;
  bool _starting = false;
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
        onError: (_) => stop('Chưa nhận được giọng nói. Bạn có thể gõ yêu cầu.'),
        onDone: stop,
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
            20, 8, 20, 20 + MediaQuery.viewInsetsOf(context).bottom,
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
              if (_listening) const Text('Đang nghe…'),
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
                label: 'Tạo phương án mới',
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
    return Scaffold(
      appBar: const MelbelAppBar(title: 'Điều chỉnh kế hoạch', showBack: true),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            const Center(child: MelbelMascot()),
            const SizedBox(height: 12),
            MelbelCard(
              child: Text(deficit == null || deficit <= 0
                  ? 'Phương án cần được điều chỉnh trước khi chốt.'
                  : 'Cần thêm $deficit phút để xếp đủ các phiên.'),
            ),
            const SizedBox(height: 16),
            for (final (option, label) in const [
              (FeasibilityAdjustment.deadline, 'Nới hạn chót'),
              (FeasibilityAdjustment.availability, 'Bổ sung giờ rảnh'),
              (FeasibilityAdjustment.scope, 'Giảm phạm vi'),
            ])
              ListTile(
                leading: Icon(_choice == option
                    ? Icons.radio_button_checked
                    : Icons.radio_button_off),
                title: Text(label),
                selected: _choice == option,
                onTap: () => setState(() => _choice = option),
              ),
            const SizedBox(height: 16),
            PrimaryCta(
              label: 'Mở mục cần sửa',
              onPressed: () => Navigator.pop(context, _choice),
            ),
          ],
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
        task.title.isEmpty ? 'Soạn báo cáo đánh giá quý 3' : task.title;
    final current = _options[_selectedOption];

    return Scaffold(
      appBar: const MelbelAppBar(title: 'Phân Tách Nhiệm Vụ', showBack: true),
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
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 11,
                            fontWeight: FontWeight.w600,
                            letterSpacing: 0.5,
                            color: MelbelColors.kOutline,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          displayTitle,
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 17,
                            fontWeight: FontWeight.w500,
                            color: MelbelColors.kPrimary,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          'Hạn hiện tại: 17:00 hôm nay',
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
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
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 17,
                    fontWeight: FontWeight.w600,
                    color: MelbelColors.kPrimary,
                  ),
                ),
                Text(
                  'Gợi ý êm dịu',
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
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
                SizedBox(width: 8),
                Expanded(
                  child: Text(
                    'Mình sẽ xếp lại và kiểm tra lại. Cứ thong thả, từng chút một.',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
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
                          SizedBox(width: 6),
                          Text(
                            'KHOẢNG GIỜ MỚI',
                            style: TextStyle(
                              fontFamily: 'Be Vietnam Pro',
                              fontSize: 11,
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
                            fontFamily: 'Be Vietnam Pro',
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
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 16,
                          fontWeight: FontWeight.w500,
                          color: MelbelColors.kOnSurface,
                        ),
                      ),
                      Text(
                        'Tổng cộng: 120 phút',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
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
                      fontFamily: 'Be Vietnam Pro',
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
    );
  }

  Widget _radioCard({
    required int index,
    required String label,
    required String sub,
    required bool isSelected,
  }) {
    return InkWell(
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
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 16,
                    fontWeight: isSelected ? FontWeight.w600 : FontWeight.w400,
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
                fontFamily: 'Be Vietnam Pro',
                fontSize: 13,
                fontWeight: isSelected ? FontWeight.w500 : FontWeight.w400,
                color: isSelected
                    ? MelbelColors.kOnPrimaryContainer
                    : MelbelColors.kOutline,
              ),
            ),
          ],
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
    final deficit = candidate?.feasibility['deficit_seconds'] as int?;

    return Scaffold(
      appBar: const MelbelAppBar(title: 'Phân Tách Nhiệm Vụ', showBack: true),
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
                  SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      'Chưa ghi vào lịch chính thức · Lịch của bạn vẫn an toàn',
                      style: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
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
                              color: MelbelColors.kTertiaryContainer,
                              shape: BoxShape.circle,
                            ),
                          ),
                          const SizedBox(width: 8),
                          Text(
                            'BẢN NHÁP TẠM LƯU',
                            style: TextStyle(
                              fontFamily: 'Be Vietnam Pro',
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
                      fontFamily: 'Be Vietnam Pro',
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
                          color: MelbelColors.kTertiaryContainer,
                        ),
                        SizedBox(width: 6),
                        Text(
                          'Hạn: ${task.formatDeadline(task.deadline)}',
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 12,
                            fontWeight: FontWeight.w500,
                            color: MelbelColors.kTertiaryContainer,
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
                          color: MelbelColors.kTertiaryContainer,
                        ),
                        SizedBox(width: 10),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                'Chưa chốt',
                                style: TextStyle(
                                  fontFamily: 'Be Vietnam Pro',
                                  fontSize: 12,
                                  color: MelbelColors.kOnSurfaceVariant,
                                ),
                              ),
                              SizedBox(height: 2),
                              Text(
                                deficit == null
                                    ? 'Trạng thái: ${candidate?.feasibilityStatus ?? 'chưa có phương án'}'
                                    : 'Backend báo thiếu ${(deficit / 60).ceil()} phút',
                                style: TextStyle(
                                  fontFamily: 'Be Vietnam Pro',
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
                        SizedBox(width: 10),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                'Số phiên dự kiến',
                                style: TextStyle(
                                  fontFamily: 'Be Vietnam Pro',
                                  fontSize: 12,
                                  color: MelbelColors.kOnSurfaceVariant,
                                ),
                              ),
                              SizedBox(height: 2),
                              Text(
                                '$sessionCount phiên theo phương án backend',
                                style: TextStyle(
                                  fontFamily: 'Be Vietnam Pro',
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
                      SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          'Bạn có thể chỉnh sửa lại bất cứ lúc nào, không lo bị trùng lịch.',
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
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
                Navigator.of(context).popUntil((route) => route.isFirst);
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
                  SizedBox(width: 6),
                  Text(
                    'Lưu trữ an toàn trong không gian riêng của bạn',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 12,
                      color: MelbelColors.kOnSurfaceVariant,
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
    final localizations = MaterialLocalizations.of(context);
    final firstStart = confirmedSessions.isEmpty
        ? null
        : confirmedSessions.first.scheduledStart.toLocal();
    final deadlineAt =
        task.draft?.summary['time_constraint']?['deadline_at'] as String?;
    final confirmedDeadline = deadlineAt == null
        ? task.formatDeadline(task.deadline)
        : task.formatDeadline(DateTime.tryParse(deadlineAt)?.toLocal());
    final sessionSummary = confirmedSessions.isEmpty
        ? 'Backend chưa trả các phiên đã lưu.'
        : '${confirmedSessions.length} phiên theo lịch backend';

    return Scaffold(
      appBar: const MelbelAppBar(title: 'Kế hoạch đã sẵn sàng', showBack: true),
      bottomNavigationBar: Container(
        padding: EdgeInsets.fromLTRB(
          20,
          12,
          20,
          16 + MediaQuery.paddingOf(context).bottom,
        ),
        decoration: BoxDecoration(
          color: MelbelColors.kSurface.withValues(alpha: 0.95),
          border: Border(
            top: BorderSide(color: MelbelColors.kOutlineVariant, width: 0.5),
          ),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            PrimaryCta(
              label: 'Bắt đầu phiên đầu tiên',
              icon: Icons.play_arrow,
              onPressed: () => _startFirstSession(context),
            ),
            const SizedBox(height: 8),
            SecondaryCta(
              label: 'Về trang chủ',
              icon: Icons.home_outlined,
              height: 48,
              onPressed: () {
                Navigator.of(context).pushAndRemoveUntil(
                  MaterialPageRoute<void>(builder: (_) => const HomeScreen()),
                  (route) => false,
                );
              },
            ),
          ],
        ),
      ),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
          children: [
            // Character Mascot with Happy Crescent Eyes (D-27)
            Center(
              child: Column(
                children: [
                  const MelbelMascot(expression: CatExpression.happy),
                  const SizedBox(height: 16),
                  Text(
                    'Đã chốt lịch thành công',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 24,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    'Mọi thứ đã sẵn sàng theo nhịp êm dịu của bạn',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 15,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),

            // Card: Kế hoạch vừa khóa (D-27)
            Container(
              padding: const EdgeInsets.all(20),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainer,
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: MelbelColors.kOutline),
              ),
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
                            Text(
                              'KẾ HOẠCH VỪA KHÓA',
                              style: TextStyle(
                                fontFamily: 'Be Vietnam Pro',
                                fontSize: 11,
                                fontWeight: FontWeight.w600,
                                letterSpacing: 0.5,
                                color: MelbelColors.kOutline,
                              ),
                            ),
                            const SizedBox(height: 4),
                            Text(
                              displayTitle,
                              style: TextStyle(
                                fontFamily: 'Be Vietnam Pro',
                                fontSize: 17,
                                fontWeight: FontWeight.w600,
                                color: MelbelColors.kOnSurface,
                              ),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(width: 12),
                      Container(
                        width: 32,
                        height: 32,
                        decoration: BoxDecoration(
                          color: MelbelColors.kSurfaceContainerHigh,
                          shape: BoxShape.circle,
                        ),
                        child: Icon(
                          Icons.lock,
                          size: 18,
                          color: MelbelColors.kPrimaryContainer,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 16),
                  _lockedRow(
                    icon: Icons.play_circle,
                    iconColor: MelbelColors.kPrimaryContainer,
                    title: 'Khởi động nhẹ nhàng',
                    desc:
                        'Phiên đầu: ${firstStart == null ? 'Chưa có giờ' : '${firstStart.day.toString().padLeft(2, '0')}/${firstStart.month.toString().padLeft(2, '0')} ${firstStart.hour.toString().padLeft(2, '0')}:${firstStart.minute.toString().padLeft(2, '0')}'}',
                  ),
                  const SizedBox(height: 10),
                  _lockedRow(
                    icon: Icons.timelapse,
                    iconColor: MelbelColors.kOnSurfaceVariant,
                    title: 'Nhịp độ phân bổ',
                    desc: sessionSummary,
                  ),
                  const SizedBox(height: 10),
                  _lockedRow(
                    icon: Icons.calendar_today,
                    iconColor: MelbelColors.kOutline,
                    title: 'Thời hạn mong muốn',
                    desc: 'Hạn chót: $confirmedDeadline',
                  ),
                  if (confirmedSessions.isNotEmpty) ...[
                    const Divider(height: 24),
                    const Eyebrow('Các phiên đã xác nhận'),
                    for (final session in confirmedSessions)
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: const Icon(Icons.event_available_outlined),
                        title: Text(
                          localizations.formatShortDate(
                            session.scheduledStart.toLocal(),
                          ),
                        ),
                        subtitle: Text(
                          '${TimeOfDay.fromDateTime(session.scheduledStart.toLocal()).format(context)} – '
                          '${TimeOfDay.fromDateTime(session.scheduledEnd.toLocal()).format(context)}',
                        ),
                      ),
                  ],
                ],
              ),
            ),
            const SizedBox(height: 16),

            // Note: Melbel sẽ nhắc khẽ
            Container(
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainer,
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: MelbelColors.kOutline),
              ),
              child: Row(
                children: [
                  Icon(
                    Icons.notifications_paused,
                    size: 20,
                    color: MelbelColors.kOnSurfaceVariant,
                  ),
                  SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      'Melbel sẽ nhắc khẽ khi đến giờ, không báo dồn dập.',
                      style: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 13,
                        color: MelbelColors.kOnSurfaceVariant,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),

            // Mood check
            Wrap(
              spacing: 12,
              runSpacing: 8,
              children: [
                Text(
                  'Cảm giác của bạn lúc này?',
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 14,
                    color: MelbelColors.kOnSurfaceVariant,
                  ),
                ),
                Row(
                  children: [
                    _moodChip('An tâm', isSelected: true),
                    const SizedBox(width: 8),
                    _moodChip('Cần nghỉ chút', isSelected: false),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 20),

            // Demo link for D-29 & D-30
            Center(
              child: TextButton.icon(
                onPressed: () => _open(context, const SessionReminderScreen()),
                icon: Icon(
                  Icons.notifications_active,
                  size: 16,
                  color: MelbelColors.kPrimaryContainer,
                ),
                label: Text(
                  'Xem thử nhắc nhở trước phiên (D-29) & Màn hình khóa (D-30)',
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 12,
                    color: MelbelColors.kPrimaryContainer,
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _lockedRow({
    required IconData icon,
    required Color iconColor,
    required String title,
    required String desc,
  }) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: MelbelColors.kSurfaceContainerLow,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        children: [
          Container(
            width: 36,
            height: 36,
            decoration: BoxDecoration(
              color: MelbelColors.kSurfaceContainer,
              borderRadius: BorderRadius.circular(8),
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
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 12,
                    color: MelbelColors.kOnSurfaceVariant,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  desc,
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 14,
                    fontWeight: FontWeight.w500,
                    color: MelbelColors.kOnSurface,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _moodChip(String text, {required bool isSelected}) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      decoration: BoxDecoration(
        color: MelbelColors.kSurfaceContainer,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(
          color: isSelected
              ? MelbelColors.kPrimaryContainer
              : MelbelColors.kOutline,
        ),
      ),
      child: Text(
        text,
        style: TextStyle(
          fontFamily: 'Be Vietnam Pro',
          fontSize: 12,
          fontWeight: isSelected ? FontWeight.w500 : FontWeight.w400,
          color: isSelected
              ? MelbelColors.kPrimary
              : MelbelColors.kOnSurfaceVariant,
        ),
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
            content: Text('Backend chưa tạo được phương án dự phòng: $error'),
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

    return Scaffold(
      appBar: const MelbelAppBar(title: 'Phân Tách Nhiệm Vụ', showBack: true),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
          children: [
            // Character Mascot with Peaceful Blob (D-28)
            Center(
              child: Column(
                children: [
                  const MelbelMascot(expression: CatExpression.sleepy),
                  const SizedBox(height: 16),
                  Text(
                    'Không sao cả, mình cùng đi từng bước nhé',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
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
                      fontFamily: 'Be Vietnam Pro',
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
                            SizedBox(width: 6),
                            Text(
                              'Lộ trình chuẩn',
                              style: TextStyle(
                                fontFamily: 'Be Vietnam Pro',
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
                              fontFamily: 'Be Vietnam Pro',
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
                      fontFamily: 'Be Vietnam Pro',
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
                            SizedBox(width: 8),
                            Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  'Nhịp độ gợi ý',
                                  style: TextStyle(
                                    fontFamily: 'Be Vietnam Pro',
                                    fontSize: 12,
                                    color: MelbelColors.kOnSurfaceVariant,
                                  ),
                                ),
                                Text(
                                  'Nhịp tiêu chuẩn Pomodoro êm dịu',
                                  style: TextStyle(
                                    fontFamily: 'Be Vietnam Pro',
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
                                fontFamily: 'Be Vietnam Pro',
                                fontSize: 12,
                                color: MelbelColors.kOnSurfaceVariant,
                              ),
                            ),
                            Text(
                              '90 phút',
                              style: TextStyle(
                                fontFamily: 'Be Vietnam Pro',
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
                          SizedBox(width: 8),
                          Text(
                            'Thời điểm khởi động',
                            style: TextStyle(
                              fontFamily: 'Be Vietnam Pro',
                              fontSize: 13,
                              color: MelbelColors.kOnSurfaceVariant,
                            ),
                          ),
                        ],
                      ),
                      Text(
                        'Giờ bắt đầu sẽ do backend xếp sau khi tạo phương án',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
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
                        SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            'Bạn luôn có thể điều chỉnh thời lượng bất kỳ lúc nào.',
                            style: TextStyle(
                              fontFamily: 'Be Vietnam Pro',
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
              fontFamily: 'Be Vietnam Pro',
              fontSize: 10,
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

    return Scaffold(
      appBar: const MelbelAppBar(title: 'Nhắc Trước Phiên', showBack: true),
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
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 22,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    'Chuẩn bị vào phiên lúc 15:30.',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
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
                            SizedBox(width: 6),
                            Text(
                              'Phiên 1 / 3 • 25 phút',
                              style: TextStyle(
                                fontFamily: 'Be Vietnam Pro',
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
                          fontFamily: 'Be Vietnam Pro',
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
                      fontFamily: 'Be Vietnam Pro',
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
                      SizedBox(width: 6),
                      Text(
                        'Bước 1: Soạn dàn ý',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
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
                      SizedBox(width: 8),
                      Text(
                        'Có 2 phút khởi động nhẹ nhàng.',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
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
                    fontFamily: 'Be Vietnam Pro',
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
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 12,
                    color: MelbelColors.kOutline,
                  ),
                ),
              ),
            ),
          ],
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
                      fontFamily: 'Be Vietnam Pro',
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
                        SizedBox(width: 4),
                        Text(
                          'BẢO MẬT',
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 11,
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
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 64,
                        fontWeight: FontWeight.w300,
                        color: MelbelColors.kPrimary,
                        letterSpacing: -1,
                      ),
                    ),
                  ),
                  Center(
                    child: Text(
                      'Thứ Năm, 24 tháng 10',
                      style: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
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
                        SizedBox(height: 2),
                        Text(
                          'Vuốt lên để mở',
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
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
                  InkWell(
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
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 12,
                      fontWeight: FontWeight.w500,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                  const SizedBox(width: 4),
                  Text(
                    '•',
                    style: TextStyle(
                      fontSize: 11,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                  const SizedBox(width: 4),
                  Text(
                    timeAgo,
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
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
              fontFamily: 'Be Vietnam Pro',
              fontSize: 15,
              fontWeight: FontWeight.w600,
              color: MelbelColors.kPrimary,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            body,
            style: TextStyle(
              fontFamily: 'Be Vietnam Pro',
              fontSize: 13,
              color: MelbelColors.kOnSurfaceVariant,
              height: 1.4,
            ),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              InkWell(
                onTap: onBtn1,
                borderRadius: BorderRadius.circular(12),
                child: Container(
                  height: 38,
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  decoration: BoxDecoration(
                    color: MelbelColors.kSurfaceContainerHigh,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  alignment: Alignment.center,
                  child: Text(
                    btn1Text,
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 13,
                      fontWeight: FontWeight.w500,
                      color: isBtn1Secondary
                          ? MelbelColors.kSecondary
                          : MelbelColors.kPrimary,
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              InkWell(
                onTap: onBtn2,
                borderRadius: BorderRadius.circular(12),
                child: Container(
                  height: 38,
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  decoration: BoxDecoration(
                    color: MelbelColors.kSurfaceContainerLow,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  alignment: Alignment.center,
                  child: Text(
                    btn2Text,
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 13,
                      color: MelbelColors.kOnSurfaceVariant,
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
