import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../providers/task_provider.dart';
import '../widgets/melbel_ui.dart';
import 'home_screen.dart';
import 'candidate_review_screen.dart';

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
                    child: Container(
                      width: 120,
                      height: 100,
                      decoration: BoxDecoration(
                        color: const Color(0xFF2A2F42),
                        borderRadius: BorderRadius.circular(48),
                        border: Border.all(
                          color: _isListening
                              ? MelbelColors.kPrimaryContainer
                              : const Color(0xFF3A4160),
                          width: _isListening ? 2 : 1,
                        ),
                      ),
                      child: Stack(
                        alignment: Alignment.center,
                        children: [
                          // Inquiring tilted eyes
                          Row(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              Transform.rotate(
                                angle: -14 * 3.14159 / 180,
                                child: Container(
                                  width: 9,
                                  height: 16,
                                  decoration: BoxDecoration(
                                    color: MelbelColors.kPrimaryContainer,
                                    borderRadius: BorderRadius.circular(999),
                                  ),
                                ),
                              ),
                              const SizedBox(width: 22),
                              Transform.rotate(
                                angle: 18 * 3.14159 / 180,
                                child: Container(
                                  width: 9,
                                  height: 16,
                                  decoration: BoxDecoration(
                                    color: MelbelColors.kPrimaryContainer,
                                    borderRadius: BorderRadius.circular(999),
                                  ),
                                ),
                              ),
                            ],
                          ),
                          // Subtle breathing dot at bottom
                          Positioned(
                            bottom: 12,
                            child: AnimatedBuilder(
                              animation: _pulseController,
                              builder: (context, child) {
                                return Opacity(
                                  opacity: 0.3 + (_pulseController.value * 0.4),
                                  child: Container(
                                    width: 4,
                                    height: 4,
                                    decoration: const BoxDecoration(
                                      color: MelbelColors.kPrimaryContainer,
                                      shape: BoxShape.circle,
                                    ),
                                  ),
                                );
                              },
                            ),
                          ),
                        ],
                      ),
                    ),
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
                      border: Border.all(color: const Color(0xFF2E3347)),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Container(
                          width: 6,
                          height: 6,
                          decoration: const BoxDecoration(
                            color: MelbelColors.kSecondary,
                            shape: BoxShape.circle,
                          ),
                        ),
                        const SizedBox(width: 6),
                        Text(
                          _isListening
                              ? 'Melbel đang nghe bạn...'
                              : 'Melbel đang lắng nghe',
                          style: const TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 12,
                            fontWeight: FontWeight.w500,
                            color: Color(0xFF8B90A0),
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
            const Center(
              child: Column(
                children: [
                  Text(
                    'Bạn muốn chia việc này thế nào?',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 22,
                      fontWeight: FontWeight.w600,
                      color: Color(0xFFE8E4DA),
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
                      color: Color(0xFF8B90A0),
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
                color: const Color(0xFF212534),
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: const Color(0xFF2E3347)),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Ý định của bạn',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 12,
                      fontWeight: FontWeight.w500,
                      color: Color(0xFF8B90A0),
                    ),
                  ),
                  const SizedBox(height: 8),
                  TextField(
                    controller: _intentController,
                    maxLines: 3,
                    style: const TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 16,
                      color: Color(0xFFE8E4DA),
                    ),
                    decoration: const InputDecoration(
                      hintText: 'Nhập ý định hoặc số phiên bạn muốn làm...',
                      hintStyle: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 15,
                        color: Color(0xFF8B90A0),
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
                            ? const Color(0xFF2A2F42)
                            : const Color(0xFF212534),
                        shape: BoxShape.circle,
                        border: Border.all(
                          color: _isListening
                              ? MelbelColors.kPrimaryContainer
                              : const Color(0xFF2E3347),
                        ),
                      ),
                      child: Icon(
                        Icons.mic,
                        size: 28,
                        color: _isListening
                            ? MelbelColors.kPrimaryContainer
                            : const Color(0xFFE8E4DA),
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
                          : const Color(0xFF8B90A0),
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
                    const Icon(
                      Icons.auto_awesome,
                      color: Color(0xFF171A24),
                      size: 20,
                    ),
                    const SizedBox(width: 8),
                    Text(
                      _isGenerating ? 'Đang tạo phương án…' : 'Để AI đề xuất',
                      style: const TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 16,
                        fontWeight: FontWeight.w600,
                        color: Color(0xFF171A24),
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
                  color: const Color(0xFF212534),
                  borderRadius: BorderRadius.circular(16),
                  border: Border.all(color: const Color(0xFF2E3347)),
                ),
                child: const Center(
                  child: Text(
                    'Tự chọn phiên thủ công',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 16,
                      fontWeight: FontWeight.w500,
                      color: Color(0xFFE8E4DA),
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
                icon: const Icon(
                  Icons.sync_problem,
                  size: 16,
                  color: Color(0xFF8B90A0),
                ),
                label: const Text(
                  'Xem thử kịch bản AI phản hồi chậm (D-28)',
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 12,
                    color: Color(0xFF8B90A0),
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
          color: const Color(0xFF212534),
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: const Color(0xFF2E3347)),
        ),
        alignment: Alignment.center,
        child: Text(
          text,
          style: const TextStyle(
            fontFamily: 'Be Vietnam Pro',
            fontSize: 14,
            fontWeight: FontWeight.w500,
            color: Color(0xFF8B90A0),
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
  final TextEditingController _controller = TextEditingController(
    text: 'Dời bước 2 sang tối',
  );
  bool _isRecording = false;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: const BoxDecoration(
        color: MelbelColors.kSurfaceContainer,
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      padding: EdgeInsets.fromLTRB(
        20,
        12,
        20,
        20 + MediaQuery.viewInsetsOf(context).bottom,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Drag handle
          Center(
            child: Container(
              width: 36,
              height: 4,
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainerHighest,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
          ),
          const SizedBox(height: 12),

          // Header Pill
          const Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Row(
                children: [
                  Icon(
                    Icons.calendar_today,
                    size: 16,
                    color: MelbelColors.kOnSurfaceVariant,
                  ),
                  SizedBox(width: 6),
                  Text(
                    'Tiến độ hôm nay',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 12,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                ],
              ),
              Text(
                'Hạn chót: 12:00 Ngày mai',
                style: TextStyle(
                  fontFamily: 'Be Vietnam Pro',
                  fontSize: 12,
                  fontWeight: FontWeight.w500,
                  color: MelbelColors.kSecondary,
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),

          // User voice request bubble
          Align(
            alignment: Alignment.centerRight,
            child: Container(
              padding: const EdgeInsets.all(12),
              margin: const EdgeInsets.only(left: 48),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainerHighest,
                borderRadius: BorderRadius.circular(16),
              ),
              child: const Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(
                        Icons.mic,
                        size: 14,
                        color: MelbelColors.kOnSurfaceVariant,
                      ),
                      SizedBox(width: 4),
                      Text(
                        'Giọng nói',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 11,
                          color: MelbelColors.kOnSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                  SizedBox(height: 4),
                  Text(
                    '"Dời bước 2 sang tối"',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 15,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),

          // Melbel AI reply bubble
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 32,
                height: 32,
                decoration: const BoxDecoration(
                  color: MelbelColors.kPrimaryContainer,
                  shape: BoxShape.circle,
                ),
                child: const Icon(
                  Icons.dark_mode,
                  size: 18,
                  color: MelbelColors.kOnPrimaryContainer,
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Container(
                  padding: const EdgeInsets.all(14),
                  decoration: BoxDecoration(
                    color: MelbelColors.kSurfaceContainerLow,
                    borderRadius: BorderRadius.circular(16),
                  ),
                  child: const Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Melbel • Trợ lý êm dịu',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 12,
                          fontWeight: FontWeight.w500,
                          color: MelbelColors.kSecondary,
                        ),
                      ),
                      SizedBox(height: 4),
                      Text(
                        'Đã dời bước 2 sang 20:00',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 16,
                          fontWeight: FontWeight.w600,
                          color: MelbelColors.kOnSurface,
                        ),
                      ),
                      SizedBox(height: 4),
                      Text(
                        'Vẫn kịp hạn chót ngày mai và có 2 phiên nghỉ ngơi xen kẽ.',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 13,
                          color: MelbelColors.kOnSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),

          // Input Area
          Row(
            children: [
              Expanded(
                child: Container(
                  height: 52,
                  padding: const EdgeInsets.symmetric(horizontal: 14),
                  decoration: BoxDecoration(
                    color: MelbelColors.kSurfaceContainerLowest,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: TextField(
                    controller: _controller,
                    style: const TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 14,
                      color: MelbelColors.kOnSurface,
                    ),
                    decoration: const InputDecoration(
                      hintText: 'Nhập hoặc nói yêu cầu khác...',
                      hintStyle: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 14,
                        color: MelbelColors.kOnSurfaceVariant,
                      ),
                      border: InputBorder.none,
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              InkWell(
                onTap: () => setState(() => _isRecording = !_isRecording),
                borderRadius: BorderRadius.circular(12),
                child: Container(
                  width: 52,
                  height: 52,
                  decoration: BoxDecoration(
                    color: _isRecording
                        ? MelbelColors.kPrimaryContainer
                        : MelbelColors.kSurfaceContainerHighest,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Icon(
                    _isRecording ? Icons.mic_off : Icons.mic,
                    color: _isRecording
                        ? MelbelColors.kOnPrimaryContainer
                        : MelbelColors.kOnSurface,
                    size: 22,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),

          // Buttons
          PrimaryCta(
            label: 'Dùng thay đổi này',
            onPressed: () {
              Navigator.pop(context);
              widget.onApply(_controller.text.trim());
            },
          ),
          const SizedBox(height: 8),
          Center(
            child: TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text(
                'Hủy',
                style: TextStyle(
                  fontFamily: 'Be Vietnam Pro',
                  fontSize: 14,
                  color: MelbelColors.kOnSurfaceVariant,
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ============================================================================
// D-24: CHƯA ĐỦ THỜI GIAN (FEASIBILITY & DEFICIT WARNING SCREEN)
// ============================================================================

class FeasibilityScreen extends StatelessWidget {
  const FeasibilityScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();
    final displayTitle =
        task.title.isEmpty ? 'Soạn báo cáo đánh giá quý 3' : task.title;

    return Scaffold(
      appBar: const MelbelAppBar(title: 'Kiểm Tra Khả Thi', showBack: true),
      body: SafeArea(
        top: false,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
          children: [
            // Thẻ thông tin công việc (D-24)
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainer,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Row(
                children: [
                  const Icon(
                    Icons.assignment,
                    size: 24,
                    color: MelbelColors.kPrimaryContainer,
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          displayTitle,
                          style: const TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 16,
                            fontWeight: FontWeight.w600,
                            color: MelbelColors.kOnSurface,
                          ),
                        ),
                        const SizedBox(height: 4),
                        const Row(
                          children: [
                            Icon(
                              Icons.schedule,
                              size: 15,
                              color: MelbelColors.kOnSurfaceVariant,
                            ),
                            SizedBox(width: 4),
                            Text(
                              'Hạn chót: 17:00 hôm nay',
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
                ],
              ),
            ),
            const SizedBox(height: 16),

            // Thẻ cảnh báo dịu nhẹ (Hổ phách nhã nhặn, không kích động) (D-24)
            Container(
              padding: const EdgeInsets.all(20),
              decoration: BoxDecoration(
                color: MelbelColors.kSurfaceContainerHigh,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      const Row(
                        children: [
                          Icon(
                            Icons.timelapse,
                            size: 24,
                            color: MelbelColors.kTertiaryFixedDim,
                          ),
                          SizedBox(width: 8),
                          Text(
                            'Thiếu 105 phút',
                            style: TextStyle(
                              fontFamily: 'Be Vietnam Pro',
                              fontSize: 20,
                              fontWeight: FontWeight.w600,
                              color: MelbelColors.kTertiaryFixedDim,
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
                          color: MelbelColors.kSurfaceContainer,
                          borderRadius: BorderRadius.circular(999),
                        ),
                        child: const Text(
                          'Ước tính',
                          style: TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 12,
                            color: MelbelColors.kTertiaryFixedDim,
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  const Text(
                    'Cần 150 phút, hiện chỉ còn 45 phút trước hạn.',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 15,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                  const SizedBox(height: 16),

                  // Biểu đồ thanh tương quan tỷ lệ thời gian
                  ClipRRect(
                    borderRadius: BorderRadius.circular(999),
                    child: SizedBox(
                      height: 12,
                      width: double.infinity,
                      child: Row(
                        children: [
                          // Khả dụng (45p ~ 30%)
                          Expanded(
                            flex: 30,
                            child: Container(color: MelbelColors.kSecondary),
                          ),
                          // Thiếu hụt (105p ~ 70%)
                          Expanded(
                            flex: 70,
                            child: Container(
                              color: MelbelColors.kTertiaryFixedDim.withValues(
                                alpha: 0.4,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),

                  // Chip định lượng
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Row(
                        children: [
                          Container(
                            width: 8,
                            height: 8,
                            decoration: const BoxDecoration(
                              color: MelbelColors.kSecondary,
                              shape: BoxShape.circle,
                            ),
                          ),
                          const SizedBox(width: 4),
                          const Text(
                            'Còn 45p',
                            style: TextStyle(
                              fontFamily: 'Be Vietnam Pro',
                              fontSize: 12,
                              color: MelbelColors.kOnSurfaceVariant,
                            ),
                          ),
                        ],
                      ),
                      Row(
                        children: [
                          Container(
                            width: 8,
                            height: 8,
                            decoration: BoxDecoration(
                              color: MelbelColors.kTertiaryFixedDim.withValues(
                                alpha: 0.5,
                              ),
                              shape: BoxShape.circle,
                            ),
                          ),
                          const SizedBox(width: 4),
                          const Text(
                            'Thiếu 105p',
                            style: TextStyle(
                              fontFamily: 'Be Vietnam Pro',
                              fontSize: 12,
                              color: MelbelColors.kOnSurfaceVariant,
                            ),
                          ),
                        ],
                      ),
                      const Text(
                        'Tổng cần: 150p',
                        style: TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 12,
                          color: MelbelColors.kOnSurface,
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),

            // Trạng thái nhã nhặn
            const Center(
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    Icons.history_toggle_off,
                    size: 16,
                    color: MelbelColors.kOnSurfaceVariant,
                  ),
                  SizedBox(width: 6),
                  Text(
                    'Lịch chưa thay đổi.',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 14,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 24),

            // Cụm thao tác phản hồi dứt khoát (D-24)
            PrimaryCta(
              label: 'Thêm thời gian',
              icon: Icons.more_time,
              onPressed: () => _open(context, const ExtendTimeScreen()),
            ),
            const SizedBox(height: 12),
            SecondaryCta(
              label: 'Giữ nguyên, chưa chốt',
              onPressed: () => _open(context, const UnconfirmedPlanScreen()),
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
                    decoration: const BoxDecoration(
                      color: MelbelColors.kSurfaceContainerHigh,
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(
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
                        const Text(
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
                          style: const TextStyle(
                            fontFamily: 'Be Vietnam Pro',
                            fontSize: 17,
                            fontWeight: FontWeight.w500,
                            color: MelbelColors.kPrimary,
                          ),
                        ),
                        const SizedBox(height: 4),
                        const Text(
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
            const Row(
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
            const Row(
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
                      const Row(
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
                          style: const TextStyle(
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
                        style: const TextStyle(
                          fontFamily: 'Be Vietnam Pro',
                          fontSize: 16,
                          fontWeight: FontWeight.w500,
                          color: MelbelColors.kOnSurface,
                        ),
                      ),
                      const Text(
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
                    style: const TextStyle(
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
              child: const Row(
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
                            decoration: const BoxDecoration(
                              color: MelbelColors.kTertiaryContainer,
                              shape: BoxShape.circle,
                            ),
                          ),
                          const SizedBox(width: 8),
                          const Text(
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
                      const Icon(
                        Icons.nightlight,
                        size: 20,
                        color: MelbelColors.kOnSurfaceVariant,
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  Text(
                    displayTitle,
                    style: const TextStyle(
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
                  const Divider(color: MelbelColors.kSurfaceContainerHighest),
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
                                style: const TextStyle(
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
                                style: const TextStyle(
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
                  const Row(
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
            const Center(
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

  @override
  Widget build(BuildContext context) {
    final task = context.watch<TaskProvider>();
    final displayTitle =
        task.title.isEmpty ? 'Soạn báo cáo đánh giá quý 3' : task.title;
    final confirmedTask = task.confirmedTask;
    final confirmedSessions = confirmedTask?.sessions ?? const [];
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
      appBar: const MelbelAppBar(title: 'Phân Tách Nhiệm Vụ', showBack: true),
      bottomNavigationBar: Container(
        padding: EdgeInsets.fromLTRB(
          20,
          12,
          20,
          16 + MediaQuery.paddingOf(context).bottom,
        ),
        decoration: BoxDecoration(
          color: MelbelColors.kSurface.withValues(alpha: 0.95),
          border: const Border(
            top: BorderSide(color: MelbelColors.kOutlineVariant, width: 0.5),
          ),
        ),
        child: InkWell(
          onTap: () {
            Navigator.of(context).pushAndRemoveUntil(
              MaterialPageRoute<void>(builder: (_) => const HomeScreen()),
              (route) => false,
            );
          },
          borderRadius: BorderRadius.circular(16),
          child: Container(
            height: 56,
            decoration: BoxDecoration(
              color: MelbelColors.kPrimaryContainer,
              borderRadius: BorderRadius.circular(16),
            ),
            child: const Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(Icons.home, size: 20, color: Color(0xFF171A24)),
                SizedBox(width: 8),
                Text(
                  'Về trang chủ',
                  style: TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 16,
                    fontWeight: FontWeight.w600,
                    color: Color(0xFF171A24),
                  ),
                ),
              ],
            ),
          ),
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
                  Container(
                    width: 120,
                    height: 100,
                    decoration: BoxDecoration(
                      color: const Color(0xFF2A2F42),
                      borderRadius: BorderRadius.circular(38),
                      border: Border.all(color: const Color(0xFF3A4160)),
                    ),
                    child: Stack(
                      alignment: Alignment.center,
                      children: [
                        // Happy crescent eyes (SVG curves drawn with CustomPainter)
                        CustomPaint(
                          size: const Size(80, 24),
                          painter: _HappyEyesPainter(),
                        ),
                        // Blush dots at bottom
                        Positioned(
                          bottom: 18,
                          left: 18,
                          child: Container(
                            width: 8,
                            height: 6,
                            decoration: BoxDecoration(
                              color: const Color(0xFF3A4160),
                              borderRadius: BorderRadius.circular(999),
                            ),
                          ),
                        ),
                        Positioned(
                          bottom: 18,
                          right: 18,
                          child: Container(
                            width: 8,
                            height: 6,
                            decoration: BoxDecoration(
                              color: const Color(0xFF3A4160),
                              borderRadius: BorderRadius.circular(999),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 16),
                  const Text(
                    'Đã chốt lịch thành công',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 24,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                  const SizedBox(height: 6),
                  const Text(
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
                color: const Color(0xFF212534),
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: const Color(0xFF2E3347)),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text(
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
                            style: const TextStyle(
                              fontFamily: 'Be Vietnam Pro',
                              fontSize: 17,
                              fontWeight: FontWeight.w600,
                              color: MelbelColors.kOnSurface,
                            ),
                          ),
                        ],
                      ),
                      Container(
                        width: 32,
                        height: 32,
                        decoration: const BoxDecoration(
                          color: MelbelColors.kSurfaceContainerHigh,
                          shape: BoxShape.circle,
                        ),
                        child: const Icon(
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
                    iconColor: const Color(0xFF9DB8A5),
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
                ],
              ),
            ),
            const SizedBox(height: 16),

            // Note: Melbel sẽ nhắc khẽ
            Container(
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: const Color(0xFF212534),
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: const Color(0xFF2E3347)),
              ),
              child: const Row(
                children: [
                  Icon(
                    Icons.notifications_paused,
                    size: 20,
                    color: Color(0xFF9DB8A5),
                  ),
                  SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      'Melbel sẽ nhắc khẽ khi đến giờ, không báo dồn dập.',
                      style: TextStyle(
                        fontFamily: 'Be Vietnam Pro',
                        fontSize: 13,
                        color: Color(0xFF9DB8A5),
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
                const Text(
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
                icon: const Icon(
                  Icons.notifications_active,
                  size: 16,
                  color: MelbelColors.kPrimaryContainer,
                ),
                label: const Text(
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
                  style: const TextStyle(
                    fontFamily: 'Be Vietnam Pro',
                    fontSize: 12,
                    color: MelbelColors.kOnSurfaceVariant,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  desc,
                  style: const TextStyle(
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
        color: const Color(0xFF212534),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(
          color: isSelected
              ? MelbelColors.kPrimaryContainer
              : const Color(0xFF2E3347),
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

class _HappyEyesPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = MelbelColors.kPrimaryContainer
      ..strokeWidth = 3
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;

    // Left curved arch eye
    final path1 = Path()
      ..moveTo(size.width * 0.2, size.height * 0.7)
      ..quadraticBezierTo(
        size.width * 0.32,
        size.height * 0.2,
        size.width * 0.44,
        size.height * 0.7,
      );
    canvas.drawPath(path1, paint);

    // Right curved arch eye
    final path2 = Path()
      ..moveTo(size.width * 0.56, size.height * 0.7)
      ..quadraticBezierTo(
        size.width * 0.68,
        size.height * 0.2,
        size.width * 0.8,
        size.height * 0.7,
      );
    canvas.drawPath(path2, paint);
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
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
                  Container(
                    width: 120,
                    height: 100,
                    decoration: BoxDecoration(
                      color: const Color(0xFF2A2F42),
                      borderRadius: BorderRadius.circular(42),
                      border: Border.all(color: const Color(0xFF3A4160)),
                    ),
                    child: Stack(
                      alignment: Alignment.center,
                      children: [
                        // Sleepy relaxed arch eyes
                        Row(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            CustomPaint(
                              size: const Size(20, 12),
                              painter: _SleepyEyePainter(),
                            ),
                            const SizedBox(width: 24),
                            CustomPaint(
                              size: const Size(20, 12),
                              painter: _SleepyEyePainter(),
                            ),
                          ],
                        ),
                        // Soft peaceful smile
                        Positioned(
                          bottom: 24,
                          child: Container(
                            width: 12,
                            height: 4,
                            decoration: BoxDecoration(
                              color: const Color(0xFF3A4160),
                              borderRadius: BorderRadius.circular(2),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 16),
                  const Text(
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
                  const Text(
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
                        child: const Row(
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
                          const Icon(
                            Icons.schedule,
                            size: 15,
                            color: MelbelColors.kOnSurfaceVariant,
                          ),
                          const SizedBox(width: 4),
                          Text(
                            task.formatDeadline(task.deadline),
                            style: const TextStyle(
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
                    style: const TextStyle(
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
                        const Row(
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
                        const Row(
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
                  const Row(
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
                    child: const Row(
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
    Color textColor = MelbelColors.kOnSurfaceVariant,
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
              color: textColor,
            ),
          ),
        ],
      ),
    );
  }
}

class _SleepyEyePainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = MelbelColors.kPrimaryContainer
      ..strokeWidth = 3
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;

    final path = Path()
      ..moveTo(0, size.height * 0.2)
      ..quadraticBezierTo(
        size.width * 0.5,
        size.height * 0.9,
        size.width,
        size.height * 0.2,
      );
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
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
                      decoration: const BoxDecoration(
                        color: MelbelColors.kSurfaceContainerHigh,
                        shape: BoxShape.circle,
                      ),
                      child: const Icon(
                        Icons.bedtime,
                        size: 30,
                        color: MelbelColors.kPrimaryContainer,
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                  const Text(
                    'Sắp đến phiên làm việc',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 22,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                  const SizedBox(height: 4),
                  const Text(
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
                        child: const Row(
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
                      const Text(
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
                    style: const TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 18,
                      fontWeight: FontWeight.w600,
                      color: MelbelColors.kOnSurface,
                    ),
                  ),
                  const SizedBox(height: 6),
                  const Row(
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
                  const Divider(color: MelbelColors.kSurfaceContainerHigh),
                  const SizedBox(height: 8),

                  // Psychological Buffer Line (2-minute starter micro-task)
                  const Row(
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
                child: const Text(
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
                icon: const Icon(
                  Icons.lock_clock,
                  size: 16,
                  color: MelbelColors.kOutline,
                ),
                label: const Text(
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
                    icon: const Icon(
                      Icons.close,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                  const Text(
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
                  const Center(
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
                  const Center(
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
                  const Center(
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
                  const Center(
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
                    decoration: const BoxDecoration(
                      color: MelbelColors.kSurfaceContainer,
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(
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
                    decoration: const BoxDecoration(
                      color: MelbelColors.kSecondaryContainer,
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(
                      Icons.dark_mode,
                      size: 13,
                      color: MelbelColors.kSecondary,
                    ),
                  ),
                  const SizedBox(width: 8),
                  const Text(
                    'Melbel',
                    style: TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 12,
                      fontWeight: FontWeight.w500,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                  const SizedBox(width: 4),
                  const Text(
                    '•',
                    style: TextStyle(
                      fontSize: 11,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                  const SizedBox(width: 4),
                  Text(
                    timeAgo,
                    style: const TextStyle(
                      fontFamily: 'Be Vietnam Pro',
                      fontSize: 12,
                      color: MelbelColors.kOnSurfaceVariant,
                    ),
                  ),
                ],
              ),
              const Icon(
                Icons.expand_more,
                size: 18,
                color: MelbelColors.kOnSurfaceVariant,
              ),
            ],
          ),
          const SizedBox(height: 10),
          Text(
            title,
            style: const TextStyle(
              fontFamily: 'Be Vietnam Pro',
              fontSize: 15,
              fontWeight: FontWeight.w600,
              color: MelbelColors.kPrimary,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            body,
            style: const TextStyle(
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
                    style: const TextStyle(
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
