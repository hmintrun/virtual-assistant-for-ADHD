import 'dart:async';
import 'package:flutter/material.dart';
import 'package:proplan_va/models/checkin_models.dart';

/// Màn hình UF5: Voice Check-in tiến độ 2 chiều, ghi nhận thời lượng thực tế và xử lý 5 nhánh kết thúc
/// Tuân thủ nghiêm ngặt chuẩn W3C COGA cho người dùng ADHD: Tối đa 3-5 thành phần tương tác,
/// ngôn ngữ đồng cảm không phán xét, có cơ chế Hoàn tác (Undo) 5 giây.
class CheckInScreen extends StatefulWidget {
  final String runId;
  final String stepTitle;
  final int expectedRevision;
  final Future<void> Function(CheckInResponseModel response)? onSubmit;
  final VoidCallback? onTimeout;

  const CheckInScreen({
    super.key,
    required this.runId,
    required this.stepTitle,
    this.expectedRevision = 1,
    this.onSubmit,
    this.onTimeout,
  });

  @override
  State<CheckInScreen> createState() => _CheckInScreenState();
}

class _CheckInScreenState extends State<CheckInScreen> {
  bool _isVoiceActive = false;
  bool _isSubmitting = false;
  SkipScope? _selectedSkipScope;
  bool _showSkipOptions = false;
  Timer? _undoTimer;
  int _undoCountdown = 5;
  CheckInResponseModel? _pendingSubmission;

  @override
  void dispose() {
    _undoTimer?.cancel();
    super.dispose();
  }

  void _handleOutcomeSelected(SessionOutcome outcome, {SkipScope? skipScope}) {
    final response = CheckInResponseModel(
      expectedResponseRevision: widget.expectedRevision,
      respondedAt: DateTime.now(),
      sessionOutcome: outcome,
      stepCompleted: outcome == SessionOutcome.completed,
      taskCompleted: false,
      skipScope: skipScope,
    );

    // Kích hoạt Undo Window 5 giây (W3C COGA)
    setState(() {
      _pendingSubmission = response;
      _undoCountdown = 5;
    });

    _undoTimer?.cancel();
    _undoTimer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (_undoCountdown <= 1) {
        timer.cancel();
        _commitSubmission();
      } else {
        setState(() {
          _undoCountdown--;
        });
      }
    });

    ScaffoldMessenger.of(context).hideCurrentSnackBar();
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        duration: const Duration(seconds: 5),
        behavior: SnackBarBehavior.floating,
        backgroundColor: const Color(0xFF1E293B),
        content: Row(
          children: [
            const Icon(Icons.check_circle_outline, color: Color(0xFF38BDF8), size: 20),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                'Đã ghi nhận (${_undoCountdown}s)...',
                style: const TextStyle(color: Colors.white, fontSize: 14),
              ),
            ),
          ],
        ),
        action: SnackBarAction(
          label: 'HOÀN TÁC',
          textColor: const Color(0xFFFBBF24),
          onPressed: _cancelSubmission,
        ),
      ),
    );
  }

  void _cancelSubmission() {
    _undoTimer?.cancel();
    setState(() {
      _pendingSubmission = null;
      _showSkipOptions = false;
      _selectedSkipScope = null;
    });
  }

  Future<void> _commitSubmission() async {
    final submission = _pendingSubmission;
    if (submission == null) return;

    setState(() {
      _isSubmitting = true;
    });

    try {
      if (widget.onSubmit != null) {
        await widget.onSubmit!(submission);
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Lỗi gửi kết quả: $e'),
            backgroundColor: Colors.redAccent,
          ),
        );
      }
    } finally {
      if (mounted) {
        setState(() {
          _isSubmitting = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0F172A),
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: const Text(
          'Đánh giá kết quả',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600, color: Colors.white),
        ),
        centerTitle: true,
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 24.0, vertical: 16.0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // 1. Thẻ câu hỏi và bước làm việc (Tối đa 1 tiêu điểm chú ý)
              Container(
                padding: const EdgeInsets.all(20),
                decoration: BoxDecoration(
                  color: const Color(0xFF1E293B),
                  borderRadius: BorderRadius.circular(16),
                  border: Border.all(color: const Color(0xFF334155)),
                ),
                child: Column(
                  children: [
                    const Icon(Icons.psychology, color: Color(0xFF38BDF8), size: 36),
                    const SizedBox(height: 12),
                    Text(
                      widget.stepTitle,
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                        fontSize: 18,
                        fontWeight: FontWeight.bold,
                        color: Colors.white,
                      ),
                    ),
                    const SizedBox(height: 8),
                    const Text(
                      'Bạn đã hoàn thành bước này chưa?',
                      textAlign: TextAlign.center,
                      style: TextStyle(fontSize: 15, color: Color(0xFF94A3B8)),
                    ),
                  ],
                ),
              ),

              const SizedBox(height: 24),

              // 2. Nút bấm tương tác giọng nói 2 chiều
              Center(
                child: GestureDetector(
                  onTap: () {
                    setState(() {
                      _isVoiceActive = !_isVoiceActive;
                    });
                  },
                  child: Container(
                    width: 72,
                    height: 72,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: _isVoiceActive ? const Color(0xFF0284C7) : const Color(0xFF1E293B),
                      border: Border.all(
                        color: _isVoiceActive ? const Color(0xFF38BDF8) : const Color(0xFF475569),
                        width: 2,
                      ),
                      boxShadow: _isVoiceActive
                          ? [
                              BoxShadow(
                                color: const Color(0xFF38BDF8).withValues(alpha: 0.3),
                                blurRadius: 16,
                                spreadRadius: 4,
                              )
                            ]
                          : null,
                    ),
                    child: Icon(
                      _isVoiceActive ? Icons.mic : Icons.mic_none,
                      color: _isVoiceActive ? Colors.white : const Color(0xFF94A3B8),
                      size: 32,
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 8),
              Center(
                child: Text(
                  _isVoiceActive ? 'Đang lắng nghe câu trả lời...' : 'Chạm để nói câu trả lời',
                  style: TextStyle(
                    fontSize: 13,
                    color: _isVoiceActive ? const Color(0xFF38BDF8) : const Color(0xFF64748B),
                  ),
                ),
              ),

              const Spacer(),

              // 3. Nhóm 3-5 lựa chọn chính (W3C COGA: Rõ ràng, nhãn hành động cụ thể)
              if (!_showSkipOptions) ...[
                ElevatedButton.icon(
                  onPressed: _isSubmitting
                      ? null
                      : () => _handleOutcomeSelected(SessionOutcome.completed),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF10B981),
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(vertical: 16),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                  ),
                  icon: const Icon(Icons.done_all),
                  label: const Text('Đã hoàn thành bước này', style: TextStyle(fontSize: 16)),
                ),
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  onPressed: _isSubmitting
                      ? null
                      : () => _handleOutcomeSelected(SessionOutcome.incomplete),
                  style: OutlinedButton.styleFrom(
                    foregroundColor: const Color(0xFFF1F5F9),
                    side: const BorderSide(color: Color(0xFF475569)),
                    padding: const EdgeInsets.symmetric(vertical: 16),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                  ),
                  icon: const Icon(Icons.timelapse),
                  label: const Text('Chưa xong, cần thêm thời gian', style: TextStyle(fontSize: 16)),
                ),
                const SizedBox(height: 12),
                TextButton.icon(
                  onPressed: _isSubmitting
                      ? null
                      : () {
                          setState(() {
                            _showSkipOptions = true;
                          });
                        },
                  style: TextButton.styleFrom(
                    foregroundColor: const Color(0xFF94A3B8),
                    padding: const EdgeInsets.symmetric(vertical: 12),
                  ),
                  icon: const Icon(Icons.arrow_forward),
                  label: const Text('Bỏ qua hoặc Bận đột xuất...', style: TextStyle(fontSize: 14)),
                ),
              ] else ...[
                // Phân nhánh 3 Chip Bỏ qua (W3C COGA)
                const Text(
                  'Chọn phương án thích hợp:',
                  style: TextStyle(color: Color(0xFF94A3B8), fontSize: 14, fontWeight: FontWeight.w500),
                ),
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  runSpacing: 10,
                  children: SkipScope.values.map((scope) {
                    final isSelected = _selectedSkipScope == scope;
                    return ChoiceChip(
                      label: Text(scope.labelVi),
                      selected: isSelected,
                      onSelected: (selected) {
                        setState(() {
                          _selectedSkipScope = selected ? scope : null;
                        });
                        if (selected) {
                          _handleOutcomeSelected(SessionOutcome.skipped, skipScope: scope);
                        }
                      },
                      selectedColor: const Color(0xFF0284C7),
                      backgroundColor: const Color(0xFF1E293B),
                      labelStyle: TextStyle(
                        color: isSelected ? Colors.white : const Color(0xFFCBD5E1),
                        fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
                      ),
                    );
                  }).toList(),
                ),
                const SizedBox(height: 12),
                ElevatedButton.icon(
                  onPressed: () => _handleOutcomeSelected(SessionOutcome.busy),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFFEF4444),
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                  ),
                  icon: const Icon(Icons.error_outline),
                  label: const Text('Bận việc khẩn cấp ngoài đời'),
                ),
                const SizedBox(height: 8),
                TextButton(
                  onPressed: () {
                    setState(() {
                      _showSkipOptions = false;
                    });
                  },
                  child: const Text('Quay lại', style: TextStyle(color: Color(0xFF64748B))),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
