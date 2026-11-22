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

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  String _whisper = 'Chia nhỏ công việc và bắt đầu từng bước.';

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthService>();
    final palette = context.melbel;
    final textTheme = Theme.of(context).textTheme;

    return MelbelPageBackdrop(
      showTopCloud: true,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        body: SafeArea(
          child: CustomScrollView(
            slivers: [
              SliverPadding(
                padding: const EdgeInsets.fromLTRB(28, 24, 28, 28),
                sliver: SliverFillRemaining(
                  hasScrollBody: false,
                  child: Column(
                    children: [
                      const Spacer(),
                      MelbelEntrance(
                        child: MelbelCushionAvatar(
                          width: 108,
                          height: 96,
                          onPetted: (whisper) =>
                              setState(() => _whisper = whisper),
                        ),
                      ),
                      const SizedBox(height: 12),
                      ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 320),
                        child: MelbelChatBubble(
                          tail: MelbelBubbleTail.top,
                          centered: true,
                          senderLabel: 'Melbel',
                          badge: 'Trợ lý VA',
                          message: _whisper,
                          footnote: 'Chạm hoặc vuốt nhẹ Melbel',
                          tone: MelbelCardTone.amber,
                        ),
                      ),
                      const SizedBox(height: 22),
                      Text(
                        'ProPlan VA',
                        style: textTheme.headlineMedium?.copyWith(
                          fontWeight: FontWeight.w800,
                          letterSpacing: -0.5,
                          color: palette.onSurface,
                        ),
                      ),
                      const Spacer(flex: 2),
                      const GoogleLoginButton(),
                      if (auth.error != null) ...[
                        const SizedBox(height: 12),
                        Text(
                          auth.error!,
                          textAlign: TextAlign.center,
                          style: textTheme.bodySmall?.copyWith(
                            color: palette.amberInk,
                          ),
                        ),
                      ],
                    ],
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

  AuthService? _maybeAuth() {
    try {
      return Provider.of<AuthService>(context, listen: false);
    } catch (_) {
      return null;
    }
  }

  Future<void> _restoreSetup() async {
    final auth = _maybeAuth();
    if (auth == null) {
      if (mounted) setState(() => _loadingPreferences = false);
      return;
    }
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
    final auth = _maybeAuth();
    if (auth == null) return;
    unawaited(auth.saveOnboardingProgress({
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
    final auth = _maybeAuth();
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
      await auth?.completeOnboarding();
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
    final palette = context.melbel;
    final theme = Theme.of(context);
    final reducedMotion = MediaQuery.disableAnimationsOf(context);
    final totalBars = _step < 4 ? 4 : 3;
    final activeBars = _step < 4 ? _step + 1 : _step - 3;

    return MelbelPageBackdrop(
      showTopCloud: false,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: MelbelAppBar(
          title: _title,
          showBack: true,
          showTitleRays: true,
          onBack: _requestingPermission ? () {} : _back,
        ),
        body: Column(
          children: [
            if (_loadingPreferences)
              Padding(
                padding: const EdgeInsets.fromLTRB(24, 8, 24, 0),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(999),
                  child: const LinearProgressIndicator(minHeight: 5),
                ),
              ),
            if (!_loadingPreferences && !_preferencesLoaded)
              Padding(
                padding: const EdgeInsets.fromLTRB(24, 10, 24, 0),
                child: MelbelCard(
                  borderRadius: 20,
                  tone: MelbelCardTone.amber,
                  padding: const EdgeInsets.symmetric(
                    horizontal: 12,
                    vertical: 4,
                  ),
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(
                      Icons.cloud_off_outlined,
                      color: palette.amberInk,
                    ),
                    title: const Text('Chưa tải được cài đặt đã lưu'),
                    subtitle: Text(
                      _preferencesError ??
                          'Tải lại để giữ nguyên thiết lập tài khoản.',
                    ),
                    trailing: TextButton(
                      onPressed: _savingPreferences
                          ? null
                          : () => unawaited(_restoreSetup()),
                      child: const Text('Thử lại'),
                    ),
                  ),
                ),
              ),
            Padding(
              padding: const EdgeInsets.fromLTRB(24, 12, 24, 0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 12,
                      vertical: 5,
                    ),
                    decoration: BoxDecoration(
                      color: palette.sageSoft,
                      borderRadius: BorderRadius.circular(999),
                    ),
                    child: Text(
                      _step < 4
                          ? 'Thiết lập · Bước ${_step + 1}/4'
                          : 'Quyền Android · Bước ${_step - 3}/3',
                      style: theme.textTheme.labelLarge?.copyWith(
                        fontWeight: FontWeight.w700,
                        color: palette.onSageSoft,
                      ),
                    ),
                  ),
                  const SizedBox(height: 10),
                  MelbelStepProgressBar(
                    totalSteps: totalBars,
                    currentStep: activeBars,
                  ),
                ],
              ),
            ),
            Expanded(
              child: AnimatedSwitcher(
                duration: reducedMotion
                    ? Duration.zero
                    : const Duration(milliseconds: 240),
                switchInCurve: Curves.easeOutCubic,
                switchOutCurve: Curves.easeInCubic,
                layoutBuilder: (currentChild, previousChildren) => Stack(
                  alignment: Alignment.topCenter,
                  children: [
                    ...previousChildren,
                    if (currentChild != null) currentChild,
                  ],
                ),
                transitionBuilder: (child, animation) {
                  if (reducedMotion) return child;
                  return FadeTransition(
                    opacity: animation,
                    child: SlideTransition(
                      position: Tween<Offset>(
                        begin: const Offset(0.03, 0),
                        end: Offset.zero,
                      ).animate(animation),
                      child: child,
                    ),
                  );
                },
                child: SingleChildScrollView(
                  key: ValueKey(_step),
                  padding: const EdgeInsets.fromLTRB(24, 20, 24, 20),
                  child: switch (_step) {
                    0 => _appearanceStep(context),
                    1 => _durationStep(context),
                    2 => _reminderStep(context),
                    3 => _guideStep(context),
                    _ => _permissionStep(context, _step - 4),
                  },
                ),
              ),
            ),
            SafeArea(
              top: false,
              minimum: const EdgeInsets.fromLTRB(24, 0, 24, 16),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  PrimaryCta(
                    label: switch (_step) {
                      0 => 'Chọn nhịp làm việc',
                      1 => 'Chọn cách nhắc',
                      2 => 'Xem hướng dẫn',
                      3 => _isAndroid ? 'Thiết lập quyền' : 'Vào trang chủ',
                      4 => 'Cho phép Không làm phiền',
                      5 => 'Cho phép báo thức',
                      _ => 'Cho phép thông báo',
                    },
                    onPressed: _requestingPermission || _savingPreferences
                        ? null
                        : () => _handlePermission(),
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
      ),
    );
  }

  Widget _appearanceStep(BuildContext context) => Column(
        children: [
          const MelbelCushionAvatar(width: 96, height: 84),
          const SizedBox(height: 18),
          Text('Chọn giao diện',
              style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 6),
          const SizedBox(height: 28),
          _ChoiceCard(
            icon: Icons.light_mode_outlined,
            title: 'Sáng',
            selected: _appearance == 'light',
            onTap: () => _selectAppearance('light'),
          ),
          _ChoiceCard(
            icon: Icons.dark_mode_outlined,
            title: 'Tối',
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
            'Tự động bật chế độ tối từ 18:00 đến 06:00.',
            textAlign: TextAlign.center,
            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                  color: context.melbel.onSurfaceVariant,
                ),
          ),
        ],
      );

  Widget _durationStep(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          MelbelCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                    '$_focusMinutes phút tập trung · $_breakMinutes phút nghỉ'),
                const SizedBox(height: 12),
                MelbelAnimatedProgressBar(
                  value: _focusMinutes / (_focusMinutes + _breakMinutes),
                  height: 8,
                  semanticsLabel: 'Tỷ lệ tập trung và nghỉ',
                ),
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
          Text('Nhắc trước phiên',
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            children: ['Ít', 'Vừa', 'Nhiều']
                .map((label) => ChoiceChip(
                      label: Text(switch (label) {
                        'Ít' => '5 phút',
                        'Vừa' => '10 và 5 phút',
                        _ => '15, 10 và 5 phút',
                      }),
                      selected: _reminderFrequency == label,
                      onSelected: (_) {
                        setState(() => _reminderFrequency = label);
                        _persistProgress();
                      },
                    ))
                .toList(),
          ),
          const SizedBox(height: 22),
          ExpansionTile(
            tilePadding: EdgeInsets.zero,
            title: const Text('Tùy chọn bổ sung'),
            subtitle: const Text('Áp dụng trong màn thiết lập này'),
            children: [
              Align(
                  alignment: Alignment.centerLeft,
                  child: Text('Kiểu nhắc',
                      style: Theme.of(context).textTheme.titleMedium)),
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
            ],
          ),
          const SizedBox(height: 12),
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

  Widget _permissionStep(BuildContext context, int index) {
    final palette = context.melbel;
    return Column(
      children: [
        const SizedBox(height: 16),
        Container(
          width: 136,
          height: 136,
          decoration: BoxDecoration(
            color: palette.sageSoft,
            shape: BoxShape.circle,
          ),
          child: Icon(_permissionIcons[index], size: 60, color: palette.sage),
        ),
        const SizedBox(height: 28),
        Text(
          _permissionTitles[index],
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.headlineSmall,
        ),
        const SizedBox(height: 8),
        Text(
          _permissionSubtitles[index],
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                color: palette.onSurfaceVariant,
              ),
        ),
        const SizedBox(height: 32),
        _NoticeCard(
          icon: _permissionIcons[index],
          title: 'Quyền trên thiết bị',
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
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Semantics(
        checked: selected,
        inMutuallyExclusiveGroup: true,
        child: MelbelCard(
          borderRadius: 24,
          tone: selected ? MelbelCardTone.sage : MelbelCardTone.neutral,
          borderColor: selected
              ? palette.sage.withValues(alpha: 0.72)
              : (isDark ? palette.hairline : Colors.transparent),
          borderWidth: selected ? 1.5 : (isDark ? 1.0 : 0.0),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          onTap: onTap,
          child: Row(
            children: [
              Container(
                width: 40,
                height: 40,
                decoration: BoxDecoration(
                  color: selected
                      ? palette.sage.withValues(alpha: 0.18)
                      : palette.sageSoft,
                  borderRadius: BorderRadius.circular(14),
                ),
                child: Icon(
                  icon,
                  size: 20,
                  color: selected ? palette.sage : palette.onSurfaceVariant,
                ),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Text(
                  title,
                  style: theme.textTheme.titleSmall?.copyWith(
                    fontSize: 16,
                    fontWeight: FontWeight.w700,
                    color: selected ? palette.onSageSoft : palette.onSurface,
                  ),
                ),
              ),
              Icon(
                selected
                    ? Icons.radio_button_checked_rounded
                    : Icons.radio_button_off_rounded,
                color: selected ? palette.sage : palette.controlOutline,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _GuideTile extends StatelessWidget {
  const _GuideTile({
    required this.number,
    required this.title,
    required this.detail,
  });

  final String number;
  final String title;
  final String detail;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final theme = Theme.of(context);
    final useHoney = number == '2';
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: MelbelCard(
        borderRadius: 24,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        child: Row(
          children: [
            Container(
              width: 40,
              height: 40,
              decoration: BoxDecoration(
                color: useHoney ? palette.amberSoft : palette.sageSoft,
                borderRadius: BorderRadius.circular(14),
              ),
              alignment: Alignment.center,
              child: Text(
                number,
                style: theme.textTheme.titleSmall?.copyWith(
                  fontWeight: FontWeight.w800,
                  color: useHoney ? palette.amber : palette.onSageSoft,
                ),
              ),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style: theme.textTheme.titleSmall?.copyWith(
                      fontSize: 16,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 3),
                  Text(
                    detail,
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: palette.onSurfaceVariant,
                      height: 1.35,
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
  Widget build(BuildContext context) {
    final palette = context.melbel;
    return MelbelCard(
      tone: MelbelCardTone.amber,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, color: palette.amberInk),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: Theme.of(context).textTheme.titleSmall?.copyWith(
                        color: palette.onAmberSoft,
                      ),
                ),
                const SizedBox(height: 4),
                Text(
                  message,
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: palette.onAmberSoft,
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
