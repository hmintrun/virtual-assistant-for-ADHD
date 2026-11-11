import '../core/api_client.dart';
import '../services/schedule_service.dart';
import '../services/voice_service.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../providers/theme_provider.dart';
import '../widgets/melbel_ui.dart';
import 'onboarding_screens.dart';

/// Supporting Help flow attached to the global help icon.
class HelpScreen extends StatelessWidget {
  const HelpScreen({super.key});

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: const MelbelAppBar(
          title: 'Trợ giúp',
          showBack: true,
          showGlobalActions: false,
        ),
        body: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            MelbelCard(
              child: Column(
                children: [
                  CircleAvatar(
                    radius: 34,
                    backgroundColor: MelbelColors.kSecondaryContainer,
                    child: Icon(Icons.spa_outlined,
                        color: MelbelColors.kSecondary, size: 32),
                  ),
                  const SizedBox(height: 14),
                  const _Tag('Không gian an tâm tuyệt đối'),
                  const SizedBox(height: 10),
                  Text('Tra cứu cách dùng tại đây',
                      style: Theme.of(context).textTheme.titleLarge),
                  const SizedBox(height: 4),
                  const Text('Tra cứu nhanh các biểu tượng quan trọng'),
                ],
              ),
            ),
            const SizedBox(height: 22),
            Text('4 biểu tượng quan trọng',
                style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            const _HelpRow(
              icon: Icons.mic_none,
              title: 'Biểu tượng Mic · Bấm giữ',
              subtitle: 'Bấm giữ để cất suy nghĩ nhanh.',
            ),
            const _HelpRow(
              icon: Icons.nightlight_outlined,
              title: 'Mặt trăng dịu êm · Trạng thái phiên',
              subtitle: 'Theo dõi phiên làm việc hiện tại.',
            ),
            const _HelpRow(
              icon: Icons.cloud_outlined,
              title: 'Mây bảo toàn · Đóng băng chuỗi',
              subtitle: 'Giữ chuỗi khi bạn lỡ nhịp.',
            ),
            const _HelpRow(
              icon: Icons.calendar_month_outlined,
              title: 'Lịch thích ứng · Tự động xếp bù',
              subtitle: 'Melbel đề xuất lịch mới để bạn duyệt.',
            ),
            const SizedBox(height: 18),
            Text('Câu hỏi thường gặp',
                style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            const _Faq(
              question: 'Nếu tôi trễ giờ hẹn thì sao?',
              answer:
                  'Bạn vẫn có thể bắt đầu khi sẵn sàng. Melbel không tự trách phạt hoặc đổi hạn chót.',
            ),
            const _Faq(
              question: 'Dữ liệu của tôi được lưu ở đâu?',
              answer:
                  'Bản demo chưa đăng nhập hoặc kết nối Backend sẽ không có hồ sơ/lịch từ máy chủ. Ghi chú UF4 hiện được lưu trên thiết bị.',
            ),
            const _Faq(
              question: 'Tôi có thể bỏ qua quyền hệ thống không?',
              answer:
                  'Có. Bạn có thể dùng app và bật quyền sau trong cài đặt Android.',
            ),
            const SizedBox(height: 18),
            OutlinedButton(
              onPressed: () => Navigator.maybePop(context),
              child: const Text('Quay lại'),
            ),
          ],
        ),
      );
}

/// Blank profile state for use without the authentication and profile APIs.
class AccountScreen extends StatefulWidget {
  const AccountScreen({super.key});

  @override
  State<AccountScreen> createState() => _AccountScreenState();
}

class _AccountScreenState extends State<AccountScreen> {
  final _connection = ApiClient().getData('/capabilities');

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: const MelbelAppBar(
          title: 'Không gian cá nhân',
          showBack: true,
          showGlobalActions: false,
        ),
        body: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            Center(
              child: Column(
                children: [
                  CircleAvatar(
                    radius: 38,
                    backgroundColor: MelbelColors.kSurfaceContainerHigh,
                    child: CatAccountIcon(
                        size: 38, color: MelbelColors.kTextSecondary),
                  ),
                  const SizedBox(height: 12),
                  Text('Hồ sơ của bạn',
                      style: Theme.of(context).textTheme.titleMedium),
                ],
              ),
            ),
            const SizedBox(height: 16),
            const _AppearanceSettingsPanel(),
            const SizedBox(height: 16),
            const _EmptyProfileCard(),
            const SizedBox(height: 20),
            Text('Nhật ký thầm lặng',
                style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            const _HelpRow(
              icon: Icons.nightlight_outlined,
              title: 'Chuỗi ngày',
              subtitle: '— · Chưa có dữ liệu',
            ),
            const _HelpRow(
              icon: Icons.hourglass_top,
              title: 'Thời gian tập trung',
              subtitle: '— · Chưa có dữ liệu',
            ),
            const _HelpRow(
              icon: Icons.all_inbox_outlined,
              title: 'Hòm thư nháp',
              subtitle: '— · Chưa có dữ liệu tài khoản',
            ),
            const SizedBox(height: 20),
            Text('Tài khoản và lịch Google',
                style: Theme.of(context).textTheme.titleMedium),
            const _HelpRow(
              icon: Icons.cloud_sync_outlined,
              title: 'Đồng bộ Google',
              subtitle: 'Chưa liên kết tài khoản',
            ),
            FutureBuilder<Map<String, dynamic>>(
              future: _connection,
              builder: (context, snapshot) => _HelpRow(
                icon: Icons.cloud_outlined,
                title: 'Kết nối máy chủ',
                subtitle: snapshot.connectionState != ConnectionState.done
                    ? 'Đang kiểm tra…'
                    : snapshot.hasError
                        ? 'Chưa kết nối được'
                        : 'Đã kết nối',
              ),
            ),
            const SizedBox(height: 18),
            FilledButton.icon(
              onPressed: () => Navigator.of(context).pushNamedAndRemoveUntil(
                '/login',
                (_) => false,
              ),
              icon: const Icon(Icons.login),
              label: const Text('Đăng nhập Google'),
            ),
            const SizedBox(height: 8),
            OutlinedButton(
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                    builder: (_) => const SetupFlowScreen()),
              ),
              child: const Text('Mở thiết lập ban đầu'),
            ),
            const SizedBox(height: 8),
            Center(
              child: Text('Đăng nhập Google đang ở chế độ demo.',
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: MelbelColors.kTextSecondary,
                      )),
            ),
          ],
        ),
      );
}

class _AppearanceSettingsPanel extends StatelessWidget {
  const _AppearanceSettingsPanel();

  @override
  Widget build(BuildContext context) {
    final theme = context.watch<ThemeProvider>();
    return MelbelCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Giao diện', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 4),
          const Text('Tự động dùng giao diện tối từ 18:00 đến 06:00.'),
          RadioListTile<String>(
            contentPadding: EdgeInsets.zero,
            value: 'light',
            groupValue: theme.appearance,
            onChanged: (value) {
              if (value != null) theme.setAppearance(value);
            },
            title: const Text('Sáng (Trăng ngày)'),
          ),
          RadioListTile<String>(
            contentPadding: EdgeInsets.zero,
            value: 'dark',
            groupValue: theme.appearance,
            onChanged: (value) {
              if (value != null) theme.setAppearance(value);
            },
            title: const Text('Tối (Trăng đêm)'),
          ),
          RadioListTile<String>(
            contentPadding: EdgeInsets.zero,
            value: 'system',
            groupValue: theme.appearance,
            onChanged: (value) {
              if (value != null) theme.setAppearance(value);
            },
            title: const Text('Tự động theo giờ địa phương'),
          ),
        ],
      ),
    );
  }
}

/// D-14. The screen is navigable without STT or Calendar APIs and shows no
/// fabricated answer data.
class CalendarVoiceQuestionScreen extends StatefulWidget {
  const CalendarVoiceQuestionScreen({super.key});

  @override
  State<CalendarVoiceQuestionScreen> createState() =>
      _CalendarVoiceQuestionScreenState();
}

class _CalendarVoiceQuestionScreenState
    extends State<CalendarVoiceQuestionScreen> {
  final _controller = TextEditingController();
  final _speech = OnDeviceSpeechService();
  final _schedule = ScheduleService(ApiClient());
  Future<List<Map<String, dynamic>>>? _result;
  bool _listening = false;
  bool _starting = false;
  String? _hint;

  @override
  void dispose() {
    _controller.dispose();
    _speech.dispose();
    super.dispose();
  }

  Future<void> _toggleMic() async {
    if (_starting) return;
    if (_listening) {
      setState(() => _listening = false);
      try {
        await _speech.stop();
      } catch (_) {
        if (mounted) setState(() => _hint = 'Bạn có thể nhập bằng chữ.');
      }
      return;
    }
    setState(() {
      _starting = true;
      _listening = true;
      _hint = null;
    });
    void done([String? hint]) {
      if (!mounted) return;
      setState(() {
        _listening = false;
        _hint = hint;
      });
    }

    try {
      final started = await _speech.start(
        onWords: (words, _) {
          if (mounted && _listening) setState(() => _controller.text = words);
        },
        onError: (_) =>
            done('Chưa nhận được giọng nói. Bạn có thể nhập bằng chữ.'),
        onDone: () => done(_hint),
      );
      if (!started) done('Chưa bật được mic. Bạn có thể nhập bằng chữ.');
    } catch (_) {
      done('Chưa bật được mic. Bạn có thể nhập bằng chữ.');
    } finally {
      if (mounted) setState(() => _starting = false);
    }
  }

  void _lookup() {
    final query = _controller.text.toLowerCase();
    final now = DateTime.now();
    final offset = query.contains('ngày kia')
        ? 2
        : query.contains('ngày mai')
            ? 1
            : query.contains('hôm nay')
                ? 0
                : null;
    final date =
        RegExp(r'\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b').firstMatch(query);
    DateTime? day;
    if (date != null) {
      final d = int.parse(date[1]!);
      final m = int.parse(date[2]!);
      final parsed = DateTime(int.tryParse(date[3] ?? '') ?? now.year, m, d);
      if (parsed.day == d && parsed.month == m) day = parsed;
    } else if (offset != null) {
      day = DateTime(now.year, now.month, now.day + offset);
    }
    setState(() {
      _hint = day == null
          ? 'Hãy hỏi hôm nay, ngày mai hoặc nhập ngày dạng DD/MM.'
          : null;
      _result = day == null
          ? null
          : _schedule.loadSessions(
              day, DateTime(day.year, day.month, day.day + 1),
              includeTitles: true);
    });
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: const MelbelAppBar(
            title: 'Hỏi về lịch', showBack: true, showGlobalActions: false),
        body: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            Center(
                child: IconButton.filled(
              tooltip: _listening ? 'Dừng nghe' : 'Bắt đầu nói',
              onPressed: _starting ? null : _toggleMic,
              icon: Icon(_listening ? Icons.stop : Icons.mic_none),
            )),
            const SizedBox(height: 16),
            TextField(
              controller: _controller,
              decoration: const InputDecoration(
                labelText: 'Hỏi lịch theo ngày',
                hintText: 'Ngày mai tôi có lịch lúc mấy giờ?',
              ),
              onChanged: (_) => setState(() {
                _result = null;
                _hint = null;
              }),
            ),
            const SizedBox(height: 12),
            PrimaryCta(
                label: 'Tra cứu',
                onPressed: _listening || _controller.text.trim().isEmpty
                    ? null
                    : _lookup),
            if (_hint != null) Text(_hint!),
            if (_result != null)
              FutureBuilder<List<Map<String, dynamic>>>(
                future: _result,
                builder: (context, snapshot) {
                  if (snapshot.connectionState != ConnectionState.done) {
                    return const Center(child: CircularProgressIndicator());
                  }
                  if (snapshot.hasError)
                    return const Text('Chưa tải được lịch. Hãy thử lại.');
                  final sessions = snapshot.data ?? const [];
                  if (sessions.isEmpty)
                    return const Text('Ngày này chưa có phiên trong lịch.');
                  return Column(children: [
                    for (final session in sessions)
                      ListTile(
                        title: Text(session['step_title'] as String? ??
                            'Phiên làm việc'),
                        subtitle: Text(
                            '${_time(context, session['scheduled_start'])} – ${_time(context, session['scheduled_end'])}'),
                      ),
                  ]);
                },
              ),
          ],
        ),
      );

  String _time(BuildContext context, Object? value) {
    final date = DateTime.tryParse(value as String? ?? '')?.toLocal();
    if (date == null) return '—';
    return MaterialLocalizations.of(context).formatTimeOfDay(
        TimeOfDay.fromDateTime(date),
        alwaysUse24HourFormat: true);
  }
}

class _EmptyProfileCard extends StatelessWidget {
  const _EmptyProfileCard();

  @override
  Widget build(BuildContext context) => MelbelCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Chưa đăng nhập',
                style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 6),
            const Text('Tên: —'),
            const Text('Email: —'),
            const SizedBox(height: 12),
            const _Tag('Chưa liên kết Google'),
          ],
        ),
      );
}

class _HelpRow extends StatelessWidget {
  const _HelpRow({
    required this.icon,
    required this.title,
    required this.subtitle,
  });

  final IconData icon;
  final String title;
  final String subtitle;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: MelbelCard(
          child: Row(
            children: [
              Icon(icon, color: MelbelColors.kSecondary),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title,
                        style: const TextStyle(fontWeight: FontWeight.w600)),
                    const SizedBox(height: 4),
                    Text(subtitle,
                        style:
                            TextStyle(color: MelbelColors.kOnSurfaceVariant)),
                  ],
                ),
              ),
            ],
          ),
        ),
      );
}

class _Faq extends StatelessWidget {
  const _Faq({required this.question, required this.answer});
  final String question;
  final String answer;

  @override
  Widget build(BuildContext context) => Card(
        child: ExpansionTile(
          title: Text(question),
          childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
          children: [Text(answer)],
        ),
      );
}

class _Tag extends StatelessWidget {
  const _Tag(this.label);
  final String label;

  @override
  Widget build(BuildContext context) => DecoratedBox(
        decoration: BoxDecoration(
          color: MelbelColors.kSecondaryContainer,
          borderRadius: BorderRadius.circular(99),
        ),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          child: Text(label, style: TextStyle(color: MelbelColors.kSecondary)),
        ),
      );
}
