import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../core/api_client.dart';
import '../providers/theme_provider.dart';
import '../services/alarm_service.dart';
import '../services/auth_service.dart';
import '../widgets/google_login_button.dart';
import '../services/dnd_service.dart';
import '../widgets/melbel_ui.dart';

class LoginScreen extends StatelessWidget {
  const LoginScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthService>();
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(28, 24, 28, 28),
          child: Column(
            children: [
              const Spacer(),
              const MelbelEntrance(child: MelbelMascot()),
              const SizedBox(height: 32),
              Text(
                'ProPlan VA',
                style: Theme.of(context).textTheme.headlineMedium?.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
              ),
              const SizedBox(height: 10),
              Text(
                'Lên kế hoạch cùng Melbel',
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.titleMedium?.copyWith(
                      color: MelbelColors.kSecondary,
                    ),
              ),
              const SizedBox(height: 8),
              Text(
                'Chia nhỏ công việc và bắt đầu từng bước.',
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                      color: MelbelColors.kTextSecondary,
                    ),
              ),
              const Spacer(flex: 2),
              const GoogleLoginButton(),
              if (auth.error != null) ...[
                const SizedBox(height: 12),
                Text(auth.error!, textAlign: TextAlign.center),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// D-04–D-10. Nhịp làm việc và mốc nhắc được lưu qua change request.
class SetupFlowScreen extends StatefulWidget {
  const SetupFlowScreen({super.key});

  @override
  State<SetupFlowScreen> createState() => _SetupFlowScreenState();
}

class _SetupFlowScreenState extends State<SetupFlowScreen>
    with WidgetsBindingObserver {
  int _step = 0;
  String _appearance = 'light';
  int _focusMinutes = 25;
  int _breakMinutes = 5;
  String _reminderFrequency = 'Vừa';
  String _reminderStyle = 'Hỏi tiến độ';
  bool _microTask = true;
  bool _voiceReadout = true;
  bool _breakReminder = true;
  bool _requestingPermission = false;
  bool _savingPreferences = false;
  bool _loadingPreferences = true;
  bool _preferencesLoaded = false;
  String? _preferencesError;
  Map<String, dynamic>? _serverPreferences;
  final _api = ApiClient();
  bool _waitingForDndSettings = false;

  bool get _isAndroid =>
      !kIsWeb && defaultTargetPlatform == TargetPlatform.android;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _appearance = context.read<ThemeProvider>().appearance;
    unawaited(_restoreSetup());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state != AppLifecycleState.resumed || !_waitingForDndSettings) return;
    _waitingForDndSettings = false;
    if (_step == 4) _handlePermission(checkDndOnly: true);
  }

  Future<void> _handlePermission({bool checkDndOnly = false}) async {
    final step = _step;
    if (step == 3 && !_isAndroid) {
      _openHome();
      return;
    }
    if (step < 4 || !_isAndroid) {
      _next();
      return;
    }
    if (_requestingPermission) return;

    setState(() => _requestingPermission = true);
    try {
      final granted = switch (step) {
        4 => await DndService.instance.hasPermission,
        5 => await AlarmService.instance.requestExactAlarmPermission(),
        _ => await AlarmService.instance.requestNotificationPermission(),
      };
      if (!mounted || _step != step) return;
      if (granted) {
        _next();
      } else if (step == 4 && !checkDndOnly) {
        _waitingForDndSettings = true;
        DndService.instance.openPermissionSettings();
      } else {
        _showPermissionMessage('Chưa cấp quyền. Bạn có thể chọn Để sau.');
      }
    } catch (_) {
      if (mounted && _step == step) {
        _showPermissionMessage('Chưa mở được quyền. Bạn có thể chọn Để sau.');
      }
    } finally {
      if (mounted) setState(() => _requestingPermission = false);
    }
  }

  void _showPermissionMessage(String message) {
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> _selectAppearance(String value) async {
    setState(() => _appearance = value);
    await context.read<ThemeProvider>().setAppearance(value);
    _persistProgress();
  }

  Future<void> _restoreSetup() async {
    final auth = context.read<AuthService>();
    final savedProgress = await auth.loadOnboardingProgress();
    try {
      final preferences = await _api.getData('/preferences');
      if (!mounted) return;
      _serverPreferences = preferences;
      if (savedProgress == null) {
        final work = preferences['work_interval_seconds'];
        final rest = preferences['rest_interval_seconds'];
        if (work is num && work > 0) _focusMinutes = (work / 60).round();
        if (rest is num && rest >= 0) _breakMinutes = (rest / 60).round();
        final offsets = preferences['reminder_offsets'];
        if (offsets is List) {
          _reminderFrequency = offsets.length <= 1
              ? 'Ít'
              : offsets.length >= 3
                  ? 'Nhiều'
                  : 'Vừa';
        }
        final microTask = preferences['micro_task_seconds'];
        if (microTask is num) _microTask = microTask > 0;
        if (preferences['reminder_style'] == 'encouraging') {
          _reminderStyle = 'Nhắc khích lệ';
        } else if (preferences['reminder_style'] == 'gentle') {
          _reminderStyle = 'Hỏi tiến độ';
        }
      }
      _preferencesLoaded = true;
      _preferencesError = null;
    } on ApiException catch (error) {
      _preferencesError = error.message;
    } catch (_) {
      _preferencesError = 'Chưa tải được cài đặt đã lưu.';
    }

    if (!mounted) return;
    if (savedProgress != null) {
      final step = savedProgress['step'];
      final focus = savedProgress['focus_minutes'];
      final rest = savedProgress['break_minutes'];
      if (step is num) _step = step.toInt().clamp(0, 6).toInt();
      if (focus is num) _focusMinutes = focus.toInt();
      if (rest is num) _breakMinutes = rest.toInt();
      if (savedProgress['appearance'] is String) {
        _appearance = savedProgress['appearance'] as String;
      }
      if (savedProgress['reminder_frequency'] is String) {
        _reminderFrequency = savedProgress['reminder_frequency'] as String;
      }
      if (savedProgress['reminder_style'] is String) {
        _reminderStyle = savedProgress['reminder_style'] as String;
      }
      _microTask = savedProgress['micro_task'] as bool? ?? _microTask;
      _voiceReadout = savedProgress['voice_readout'] as bool? ?? _voiceReadout;
      _breakReminder =
          savedProgress['break_reminder'] as bool? ?? _breakReminder;
      if (_appearance != context.read<ThemeProvider>().appearance) {
        await context.read<ThemeProvider>().setAppearance(_appearance);
      }
    }
    if (!mounted) return;
    setState(() => _loadingPreferences = false);
  }

  void _persistProgress() {
    unawaited(context.read<AuthService>().saveOnboardingProgress({
      'step': _step,
      'appearance': _appearance,
      'focus_minutes': _focusMinutes,
      'break_minutes': _breakMinutes,
      'reminder_frequency': _reminderFrequency,
      'reminder_style': _reminderStyle,
      'micro_task': _microTask,
      'voice_readout': _voiceReadout,
      'break_reminder': _breakReminder,
    }));
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
      _persistProgress();
    } else {
      unawaited(_openHome());
    }
  }

  void _back() {
    if (_step == 0) {
      Navigator.maybePop(context);
    } else {
      setState(() => _step--);
      _persistProgress();
    }
  }

  Future<void> _openHome() async {
    if (_savingPreferences || _loadingPreferences) return;
    final auth = context.read<AuthService>();
    setState(() => _savingPreferences = true);
    try {
      if (!_preferencesLoaded || _serverPreferences == null) {
        await _restoreSetup();
      }
      if (!mounted || !_preferencesLoaded || _serverPreferences == null) {
        if (mounted) {
          _showPermissionMessage(
              'Chưa tải được cài đặt đã lưu. Hãy thử tải lại trước khi tiếp tục.');
        }
        return;
      }
      final preferences = _serverPreferences!;
      final revision = preferences['effective_revision'] ??
          preferences['revision'];
      final offsets = switch (_reminderFrequency) {
        'Ít' => [300],
        'Nhiều' => [900, 600, 300],
        _ => [600, 300],
      };
      final preferenceChanges = <String, dynamic>{};
      if (preferences['work_interval_seconds'] != _focusMinutes * 60) {
        preferenceChanges['work_interval_seconds'] = _focusMinutes * 60;
      }
      if (preferences['rest_interval_seconds'] != _breakMinutes * 60) {
        preferenceChanges['rest_interval_seconds'] = _breakMinutes * 60;
      }
      final currentOffsets = preferences['reminder_offsets'];
      if (currentOffsets is! List || !listEquals(currentOffsets, offsets)) {
        preferenceChanges['reminder_offsets'] = offsets;
      }
      final desiredStyle = _reminderStyle == 'Nhắc khích lệ'
          ? 'encouraging'
          : 'gentle';
      if (preferences['reminder_style'] != desiredStyle) {
        preferenceChanges['reminder_style'] = desiredStyle;
      }
      if (preferenceChanges.isNotEmpty) {
        final payload = {
          'expected_preferences_revision': revision,
          'changes': {'preferences': preferenceChanges},
        };
        await _api.postData(
          '/settings/change-requests',
          data: payload,
          idempotencyKey: ApiClient.stableIdempotencyKey(
              'onboarding_preferences', payload.toString()),
        );
      }
      await auth.completeOnboarding();
    } on ApiException catch (error) {
      if (error.code == 'REVISION_CONFLICT') unawaited(_restoreSetup());
      if (mounted)
        _showPermissionMessage('Chưa lưu thiết lập: ${error.message}');
      return;
    } finally {
      if (mounted) setState(() => _savingPreferences = false);
    }
    if (!mounted) return;
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
      appBar: MelbelAppBar(
          title: _title,
          showBack: true,
          onBack: _requestingPermission ? () {} : _back),
      body: Column(
        children: [
          if (_loadingPreferences) const LinearProgressIndicator(),
          if (!_loadingPreferences && !_preferencesLoaded)
            ListTile(
              leading: const Icon(Icons.cloud_off_outlined),
              title: const Text('Chưa tải được cài đặt đã lưu'),
              subtitle: Text(_preferencesError ??
                  'Tải lại để giữ nguyên thiết lập tài khoản.'),
              trailing: TextButton(
                onPressed: _savingPreferences
                    ? null
                    : () => unawaited(_restoreSetup()),
                child: const Text('Thử lại'),
              ),
            ),
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
                    onPressed: _requestingPermission || _savingPreferences
                        ? null
                        : () => _handlePermission(),
                    child: Text(_step == 3
                        ? (_isAndroid ? 'Thiết lập quyền' : 'Vào trang chủ')
                        : _step >= 4 && _isAndroid
                            ? 'Cho phép'
                            : 'Tiếp theo'),
                  ),
                ),
                if ((_step == 3 && _isAndroid) || _step >= 4)
                  TextButton(
                    onPressed: _requestingPermission || _savingPreferences
                        ? null
                        : _step == 3
                            ? _openHome
                            : _next,
                    child: Text(_step == 3 ? 'Vào trang chủ' : 'Để sau'),
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
          const MelbelMascot(),
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
                      onSelected: (_) {
                        setState(() => _focusMinutes = minutes);
                        _persistProgress();
                      },
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
                      onSelected: (_) {
                        setState(() => _breakMinutes = minutes);
                        _persistProgress();
                      },
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
                      onSelected: (_) {
                        setState(() => _reminderFrequency = label);
                        _persistProgress();
                      },
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
                      onSelected: (_) {
                        setState(() => _reminderStyle = style);
                        _persistProgress();
                      },
                    ))
                .toList(),
          ),
          const SizedBox(height: 12),
          Text('Bộ đệm tâm lý', style: Theme.of(context).textTheme.titleMedium),
          SwitchListTile(
            value: _microTask,
            onChanged: (value) {
              setState(() => _microTask = value);
              _persistProgress();
            },
            title: const Text('Khởi động 2 phút'),
          ),
          SwitchListTile(
            value: _voiceReadout,
            onChanged: (value) {
              setState(() => _voiceReadout = value);
              _persistProgress();
            },
            title: const Text('Đọc bằng giọng nói'),
          ),
          SwitchListTile(
            value: _breakReminder,
            onChanged: (value) {
              setState(() => _breakReminder = value);
              _persistProgress();
            },
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
          const MelbelMascot(),
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
            message: !_isAndroid
                ? 'Quyền này dùng trên ứng dụng Android.'
                : switch (index) {
                    0 => 'Bật quyền cho ProPlan VA trong màn hình hệ thống.',
                    1 => 'Android có thể yêu cầu cho phép đặt báo thức.',
                    _ => 'Cho phép ProPlan VA gửi lời nhắc.',
                  },
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
