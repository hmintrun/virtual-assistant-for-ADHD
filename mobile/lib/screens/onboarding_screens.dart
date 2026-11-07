import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../providers/theme_provider.dart';
import '../widgets/melbel_ui.dart';

enum _LoginStep { welcome, connecting, failed }

/// D-01–D-03. Google auth is intentionally a clear demo placeholder until the
/// team supplies an authentication API/provider.
class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  _LoginStep _step = _LoginStep.welcome;

  Future<void> _tryGoogleSignIn() async {
    setState(() => _step = _LoginStep.connecting);
    await Future<void>.delayed(const Duration(milliseconds: 900));
    if (mounted) setState(() => _step = _LoginStep.failed);
  }

  void _continueAsGuest() {
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(builder: (_) => const SetupFlowScreen()),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (_step == _LoginStep.connecting) return const _SigningInScreen();
    if (_step == _LoginStep.failed) {
      return _LoginErrorScreen(
        onRetry: () => setState(() => _step = _LoginStep.welcome),
        onContinueAsGuest: _continueAsGuest,
      );
    }

    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(28, 24, 28, 28),
          child: Column(
            children: [
              const Spacer(),
              const _MelbelMascot(),
              const SizedBox(height: 32),
              Text('Chào bạn',
                  style: Theme.of(context).textTheme.headlineMedium),
              const SizedBox(height: 10),
              Text(
                'Mình là Melbel, bạn đồng hành cùng bạn hôm nay ✨',
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.titleMedium?.copyWith(
                      color: MelbelColors.kSecondary,
                    ),
              ),
              const SizedBox(height: 8),
              Text(
                'Đăng nhập nhẹ nhàng để bắt đầu nhé',
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                      color: MelbelColors.kTextSecondary,
                    ),
              ),
              const Spacer(flex: 2),
              SizedBox(
                width: double.infinity,
                height: 58,
                child: OutlinedButton.icon(
                  onPressed: _tryGoogleSignIn,
                  icon: const Text('G', style: TextStyle(fontSize: 20)),
                  label: const Text('Tiếp tục với Google'),
                ),
              ),
              const SizedBox(height: 10),
              TextButton(
                onPressed: _continueAsGuest,
                child: const Text('Dùng thử không cần đăng nhập'),
              ),
              const SizedBox(height: 6),
              Text(
                'Điều khoản và quyền riêng tư',
                style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: MelbelColors.kTextSecondary,
                    ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _SigningInScreen extends StatelessWidget {
  const _SigningInScreen();

  @override
  Widget build(BuildContext context) => Scaffold(
        body: SafeArea(
          child: Center(
            child: Padding(
              padding: const EdgeInsets.all(28),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const _MelbelMascot(),
                  const SizedBox(height: 28),
                  const CircularProgressIndicator(),
                  const SizedBox(height: 20),
                  Text('Đang đăng nhập...',
                      style: Theme.of(context).textTheme.titleLarge),
                  const SizedBox(height: 8),
                  const Text('Đang kết nối với Google...'),
                  const SizedBox(height: 4),
                  const Text('Bạn đợi Melbel một xíu nhé ✨'),
                ],
              ),
            ),
          ),
        ),
      );
}

class _LoginErrorScreen extends StatelessWidget {
  const _LoginErrorScreen({
    required this.onRetry,
    required this.onContinueAsGuest,
  });

  final VoidCallback onRetry;
  final VoidCallback onContinueAsGuest;

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(leading: BackButton(onPressed: onRetry)),
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              children: [
                const Spacer(),
                const _MelbelMascot(),
                const SizedBox(height: 32),
                const _NoticeCard(
                  icon: Icons.help_outline,
                  title: 'Đăng nhập chưa được',
                  message:
                      'Bản demo chưa kết nối xác thực Google. Bạn có thể thử lại sau hoặc dùng thử không cần tài khoản.',
                ),
                const Spacer(),
                FilledButton.icon(
                  onPressed: onRetry,
                  icon: const Text('G'),
                  label: const Text('Thử lại với Google'),
                  style: FilledButton.styleFrom(
                    minimumSize: const Size.fromHeight(56),
                  ),
                ),
                TextButton(
                  onPressed: onContinueAsGuest,
                  child: const Text('Dùng thử không cần đăng nhập'),
                ),
              ],
            ),
          ),
        ),
      );
}

/// D-04–D-10. Preferences and permission choices stay local in this prototype;
/// values are not represented as server-saved until the contracts are ready.
class SetupFlowScreen extends StatefulWidget {
  const SetupFlowScreen({super.key});

  @override
  State<SetupFlowScreen> createState() => _SetupFlowScreenState();
}

class _SetupFlowScreenState extends State<SetupFlowScreen> {
  int _step = 0;
  String _appearance = 'light';
  int _focusMinutes = 25;
  int _breakMinutes = 5;
  String _reminderFrequency = 'Vừa';
  String _reminderStyle = 'Hỏi tiến độ';
  bool _microTask = true;
  bool _voiceReadout = true;
  bool _breakReminder = true;

  @override
  void initState() {
    super.initState();
    _appearance = context.read<ThemeProvider>().appearance;
  }

  Future<void> _selectAppearance(String value) async {
    setState(() => _appearance = value);
    await context.read<ThemeProvider>().setAppearance(value);
  }

  static const _permissionTitles = [
    'Không làm phiền',
    'Báo thức chính xác',
    'Thông báo nhắc nhở',
  ];
  static const _permissionSubtitles = [
    'Ẩn thông báo khi làm việc.',
    'Báo giờ khi hết phiên.',
    'Nhắc việc theo lịch của bạn.',
  ];
  static const _permissionIcons = [
    Icons.nightlight_outlined,
    Icons.alarm_outlined,
    Icons.notifications_none_outlined,
  ];

  void _next() {
    if (_step < 6) {
      setState(() => _step++);
    } else {
      _openHome();
    }
  }

  void _back() {
    if (_step == 0) {
      Navigator.maybePop(context);
    } else {
      setState(() => _step--);
    }
  }

  void _openHome() {
    Navigator.of(context).pushNamedAndRemoveUntil('/home', (_) => false);
  }

  String get _title => switch (_step) {
        0 => 'Giao diện',
        1 => 'Nhịp làm việc',
        2 => 'Nhắc nhở',
        3 => 'Hướng dẫn lần đầu',
        4 => 'Không làm phiền',
        5 => 'Báo thức chính xác',
        _ => 'Thông báo nhắc nhở',
      };

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: MelbelAppBar(title: _title, showBack: true, onBack: _back),
      body: Column(
        children: [
          if (_step >= 4)
            Padding(
              padding: const EdgeInsets.fromLTRB(24, 8, 24, 0),
              child: Row(
                children: List.generate(
                  3,
                  (index) => Expanded(
                    child: Container(
                      height: 5,
                      margin: const EdgeInsets.only(right: 8),
                      decoration: BoxDecoration(
                        color: index <= _step - 4
                            ? MelbelColors.kPrimaryContainer
                            : MelbelColors.kSurfaceContainerHighest,
                        borderRadius: BorderRadius.circular(99),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          Expanded(
            child: SingleChildScrollView(
              padding: const EdgeInsets.fromLTRB(24, 22, 24, 20),
              child: switch (_step) {
                0 => _appearanceStep(context),
                1 => _durationStep(context),
                2 => _reminderStep(context),
                3 => _guideStep(context),
                _ => _permissionStep(context, _step - 4),
              },
            ),
          ),
          SafeArea(
            top: false,
            minimum: const EdgeInsets.fromLTRB(24, 0, 24, 16),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                SizedBox(
                  width: double.infinity,
                  height: 56,
                  child: FilledButton(
                    onPressed: _next,
                    child: Text(_step == 3
                        ? 'Bắt đầu dùng'
                        : _step == 6
                            ? 'Xong, bắt đầu'
                            : _step >= 4
                                ? 'Cho phép'
                                : 'Tiếp theo'),
                  ),
                ),
                if (_step == 3 || _step >= 4)
                  TextButton(
                    onPressed: _step == 3 ? _openHome : _next,
                    child: const Text('Để sau'),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _appearanceStep(BuildContext context) => Column(
        children: [
          const _MelbelMascot(),
          const SizedBox(height: 20),
          Text('Chọn giao diện',
              style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 6),
          const Text('Chọn màu màn hình bạn thích.'),
          const SizedBox(height: 28),
          _ChoiceCard(
            icon: Icons.light_mode_outlined,
            title: 'Sáng (Trăng ngày)',
            selected: _appearance == 'light',
            onTap: () => _selectAppearance('light'),
          ),
          _ChoiceCard(
            icon: Icons.dark_mode_outlined,
            title: 'Tối (Trăng đêm)',
            selected: _appearance == 'dark',
            onTap: () => _selectAppearance('dark'),
          ),
          _ChoiceCard(
            icon: Icons.brightness_auto_outlined,
            title: 'Tự động theo giờ địa phương',
            selected: _appearance == 'system',
            onTap: () => _selectAppearance('system'),
          ),
          const SizedBox(height: 10),
          Text(
            'Bạn có thể đổi lựa chọn trước khi tiếp tục.',
            textAlign: TextAlign.center,
            style: TextStyle(color: MelbelColors.kTextSecondary),
          ),
        ],
      );

  Widget _durationStep(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Tạo khoảng đệm êm dịu, không áp lực thúc ép.'),
          const SizedBox(height: 20),
          MelbelCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                    'MÔ PHỎNG 1 NHỊP $_focusMinutes phút tập trung + $_breakMinutes phút nghỉ'),
                const SizedBox(height: 12),
                const LinearProgressIndicator(value: .66),
                const SizedBox(height: 8),
                const Text('Dòng chảy êm đềm     •     Thở sâu & nạp lại'),
              ],
            ),
          ),
          const SizedBox(height: 24),
          Text('Thời lượng mỗi phiên tập trung',
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            children: [15, 25, 40]
                .map((minutes) => ChoiceChip(
                      label: Text('$minutes phút'),
                      selected: _focusMinutes == minutes,
                      onSelected: (_) =>
                          setState(() => _focusMinutes = minutes),
                    ))
                .toList(),
          ),
          const SizedBox(height: 24),
          Text('Thời lượng nghỉ ngắn',
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            children: [5, 10]
                .map((minutes) => ChoiceChip(
                      label: Text('$minutes phút'),
                      selected: _breakMinutes == minutes,
                      onSelected: (_) =>
                          setState(() => _breakMinutes = minutes),
                    ))
                .toList(),
          ),
          const SizedBox(height: 14),
          const Text('Áp dụng từ phiên sau.'),
        ],
      );

  Widget _reminderStep(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Chọn cách nhắc nhở đồng hành cùng bạn hôm nay.'),
          const SizedBox(height: 22),
          Text('Tần suất nhắc', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            children: ['Ít', 'Vừa', 'Nhiều']
                .map((label) => ChoiceChip(
                      label: Text(label),
                      selected: _reminderFrequency == label,
                      onSelected: (_) =>
                          setState(() => _reminderFrequency = label),
                    ))
                .toList(),
          ),
          const SizedBox(height: 22),
          Text('Kiểu nhắc', style: Theme.of(context).textTheme.titleMedium),
          Wrap(
            spacing: 8,
            children: ['Hỏi tiến độ', 'Nhắc khích lệ']
                .map((style) => ChoiceChip(
                      label: Text(style),
                      selected: _reminderStyle == style,
                      onSelected: (_) => setState(() => _reminderStyle = style),
                    ))
                .toList(),
          ),
          const SizedBox(height: 12),
          Text('Bộ đệm tâm lý', style: Theme.of(context).textTheme.titleMedium),
          SwitchListTile(
            value: _microTask,
            onChanged: (value) => setState(() => _microTask = value),
            title: const Text('Khởi động 2 phút'),
          ),
          SwitchListTile(
            value: _voiceReadout,
            onChanged: (value) => setState(() => _voiceReadout = value),
            title: const Text('Đọc bằng giọng nói'),
          ),
          SwitchListTile(
            value: _breakReminder,
            onChanged: (value) => setState(() => _breakReminder = value),
            title: const Text('Nhắc nghỉ giải lao'),
          ),
          const _NoticeCard(
            icon: Icons.spa_outlined,
            title: 'Mỗi bước nhỏ đều là tiến bộ.',
            message: 'Các lựa chọn được giữ trong phiên demo trên thiết bị.',
          ),
        ],
      );

  Widget _guideStep(BuildContext context) => Column(
        children: [
          const _MelbelMascot(),
          const SizedBox(height: 12),
          Text('Melbel luôn đồng hành cùng bạn',
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 20),
          const _GuideTile(
              number: '1',
              title: 'Nói việc cần làm',
              detail: 'Nói hoặc gõ nhanh.'),
          const _GuideTile(
              number: '2',
              title: 'Xem và chốt lịch',
              detail: 'Chia nhỏ vừa sức.'),
          const _GuideTile(
              number: '3',
              title: 'Bắt đầu phiên',
              detail: 'Tập trung một việc.'),
        ],
      );

  Widget _permissionStep(BuildContext context, int index) => Column(
        children: [
          Align(
            alignment: Alignment.centerLeft,
            child: Text('BƯỚC ${index + 1} / 3',
                style: TextStyle(color: MelbelColors.kPrimaryContainer)),
          ),
          const SizedBox(height: 72),
          Container(
            width: 144,
            height: 144,
            decoration: BoxDecoration(
              color: MelbelColors.kSurfaceContainer,
              shape: BoxShape.circle,
            ),
            child: Icon(_permissionIcons[index],
                size: 64, color: MelbelColors.kPrimaryContainer),
          ),
          const SizedBox(height: 28),
          Text(_permissionTitles[index],
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 8),
          Text(_permissionSubtitles[index],
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodyLarge),
          const SizedBox(height: 36),
          _NoticeCard(
            icon: _permissionIcons[index],
            title: index == 0
                ? 'Chế độ tĩnh khi tập trung'
                : index == 1
                    ? 'Đúng nhịp phiên làm việc'
                    : 'Tôn trọng không gian riêng',
            message:
                'Màn hình quyền đã được tạo. Việc mở hộp thoại Android sẽ được nối khi phần native sẵn sàng.',
          ),
        ],
      );
}

class _ChoiceCard extends StatelessWidget {
  const _ChoiceCard({
    required this.icon,
    required this.title,
    required this.selected,
    required this.onTap,
  });

  final IconData icon;
  final String title;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(18),
          child: Container(
            padding: const EdgeInsets.all(18),
            decoration: BoxDecoration(
              color: MelbelColors.kSurfaceContainer,
              borderRadius: BorderRadius.circular(18),
              border: Border.all(
                color: selected
                    ? MelbelColors.kPrimaryContainer
                    : MelbelColors.kOutlineVariant,
              ),
            ),
            child: Row(
              children: [
                Icon(icon, color: MelbelColors.kPrimaryContainer),
                const SizedBox(width: 14),
                Expanded(child: Text(title)),
                Icon(selected
                    ? Icons.radio_button_checked
                    : Icons.radio_button_off),
              ],
            ),
          ),
        ),
      );
}

class _GuideTile extends StatelessWidget {
  const _GuideTile(
      {required this.number, required this.title, required this.detail});

  final String number;
  final String title;
  final String detail;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: MelbelCard(
          child: Row(
            children: [
              CircleAvatar(
                backgroundColor: MelbelColors.kSecondaryContainer,
                child: Text(number),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title,
                        style: const TextStyle(fontWeight: FontWeight.w600)),
                    const SizedBox(height: 4),
                    Text(detail,
                        style: TextStyle(color: MelbelColors.kTextSecondary)),
                  ],
                ),
              ),
            ],
          ),
        ),
      );
}

class _MelbelMascot extends StatelessWidget {
  const _MelbelMascot();

  @override
  Widget build(BuildContext context) => Container(
        width: 120,
        height: 100,
        decoration: BoxDecoration(
          color: const Color(0xFF1E2029),
          borderRadius: BorderRadius.circular(44),
        ),
        child: const Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            _MascotEye(),
            SizedBox(width: 16),
            _MascotEye(),
          ],
        ),
      );
}

class _MascotEye extends StatelessWidget {
  const _MascotEye();

  @override
  Widget build(BuildContext context) => Container(
        width: 12,
        height: 18,
        decoration: const BoxDecoration(
          color: Color(0xFFE9D8A6),
          borderRadius: BorderRadius.all(Radius.circular(50)),
        ),
      );
}

class _NoticeCard extends StatelessWidget {
  const _NoticeCard({
    required this.icon,
    required this.title,
    required this.message,
  });

  final IconData icon;
  final String title;
  final String message;

  @override
  Widget build(BuildContext context) => MelbelCard(
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, color: MelbelColors.kAmber),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title,
                      style: const TextStyle(fontWeight: FontWeight.w600)),
                  const SizedBox(height: 4),
                  Text(message,
                      style: TextStyle(color: MelbelColors.kOnSurfaceVariant)),
                ],
              ),
            ),
          ],
        ),
      );
}
