import 'dart:async';

import 'package:flutter/foundation.dart';
import '../services/google_calendar_api.dart';
import '../services/auth_service.dart';
import '../providers/focus_provider.dart';
import '../providers/streak_provider.dart';
import '../providers/task_provider.dart';
import '../widgets/google_login_button.dart';
import '../core/api_client.dart';
import '../services/audio_recorder_service.dart';
import '../services/schedule_service.dart';
import '../services/voice_playback_service.dart';
import '../services/voice_service.dart';
import '../services/voice_two_tier_service.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/responsive_layout.dart';
import '../core/theme.dart';
import '../providers/theme_provider.dart';
import '../widgets/melbel_ui.dart';
import '../widgets/shimmer_loading.dart';
import '../widgets/streak_widgets.dart';
import 'onboarding_screens.dart';

/// Supporting Help flow attached to the global help icon.
class HelpScreen extends StatelessWidget {
  const HelpScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    return MelbelPageBackdrop(
      showTopCloud: true,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: const MelbelAppBar(
          title: 'Trợ giúp',
          showBack: true,
          showGlobalActions: false,
          showTitleRays: true,
        ),
        body: ListView(
          padding: MelbelLayout.pageInsets(context, top: 10, bottom: 20),
          children: [
            const MelbelSectionBanner(
              title: 'Các thao tác thường dùng',
              art: MelbelSectionArt.happyMelbel,
            ),
            const SizedBox(height: 10),
            const _HelpRow(
              icon: Icons.mic_none_rounded,
              title: 'Cất suy nghĩ',
              subtitle: 'Trong phiên, dùng mic để ghi lại ý vừa nghĩ tới.',
              useHoney: false,
              showRays: true,
            ),
            const _HelpRow(
              icon: Icons.nightlight_outlined,
              title: 'Phiên tập trung',
              subtitle: 'Theo dõi phiên làm việc hiện tại.',
              useHoney: true,
              showRays: true,
            ),
            _HelpRow(
              icon: Icons.pets_rounded,
              title: 'Nhịp tập trung (Streak)',
              subtitle:
                  'Giữ 3 ngày liên tiếp (≥ 2 phút/ngày) để mở chuỗi, nhận Khiên ngủ nướng và tiến hoá Melbel.',
              useHoney: false,
              showRays: true,
              onTap: () => showMelbelStreakGuide(
                context,
                streak: StreakProvider.maybeOf(context, listen: false),
                showOpenStreakCta: true,
              ),
            ),
            const _HelpRow(
              icon: Icons.calendar_month_outlined,
              title: 'Duyệt thay đổi lịch',
              subtitle: 'Melbel đề xuất lịch mới để bạn duyệt.',
              useHoney: true,
              showRays: true,
            ),
            const SizedBox(height: 8),
            const MelbelSectionBanner(
              title: 'Câu hỏi thường gặp',
              art: MelbelSectionArt.sprout,
            ),
            const SizedBox(height: 10),
            const _Faq(
              question: 'Chuỗi tập trung (Streak) hoạt động thế nào?',
              answer:
                  'Chỉ cần hoàn thành ít nhất 2 phút tập trung mỗi ngày trong 3 ngày liên tiếp để mở chuỗi. Sau khi mở, lỡ 1 ngày sẽ tự bật Khiên ngủ nướng hoặc mở 24h nối lại nhịp; nghỉ dài vẫn giữ nguyên kỷ lục và tổng ngày tháng này.',
            ),
            const _Faq(
              question: 'Nếu tôi trễ giờ hẹn thì sao?',
              answer:
                  'Mở phiên khi bạn sẵn sàng. Nếu cần đổi lịch, bạn sẽ xem đề xuất trước khi duyệt.',
            ),
            const _Faq(
              question: 'Dữ liệu của tôi được lưu ở đâu?',
              answer:
                  'Suy nghĩ vừa cất được lưu trên thiết bị. Mở Tài khoản → Suy nghĩ đã cất để xem lại.',
            ),
            const _Faq(
              question: 'Tôi có thể bỏ qua quyền hệ thống không?',
              answer:
                  'Có. Bạn có thể dùng app và bật quyền sau trong cài đặt Android.',
            ),
            const SizedBox(height: 8),
            SecondaryCta(
              label: 'Quay lại',
              height: 50,
              backgroundColor: palette.sageSoft,
              onPressed: () => Navigator.maybePop(context),
            ),
          ],
        ),
      ),
    );
  }
}

/// Blank profile state for use without the authentication and profile APIs.
class AccountScreen extends StatefulWidget {
  const AccountScreen({super.key});

  @override
  State<AccountScreen> createState() => _AccountScreenState();
}

class _AccountScreenState extends State<AccountScreen> {
  Future<Map<String, dynamic>> _connection = ApiClient().checkHealth();
  Future<List<Map<String, dynamic>>> _calendarConnections =
      GoogleCalendarApi(ApiClient()).connections();

  Future<void> _openGoogleCalendar() async {
    await Navigator.pushNamed(context, '/google-calendar');
    if (!mounted) return;
    setState(() =>
        _calendarConnections = GoogleCalendarApi(ApiClient()).connections());
  }

  String _calendarStatus(AsyncSnapshot<List<Map<String, dynamic>>> snapshot) {
    if (snapshot.connectionState != ConnectionState.done)
      return 'Đang kiểm tra…';
    if (snapshot.hasError) return 'Chưa kiểm tra được kết nối';
    final connections = snapshot.data ?? const [];
    for (final connection in connections) {
      if (connection['connection_state'] == 'connected') {
        final account = connection['account_ref'];
        return account is String && account.isNotEmpty
            ? 'Đã liên kết · $account'
            : 'Đã liên kết';
      }
    }
    return connections.any((item) => item['connection_state'] != 'disconnected')
        ? 'Cần kết nối lại'
        : 'Chưa liên kết';
  }

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthService>();
    final focus = context.watch<FocusProvider>();
    final streak = StreakProvider.maybeOf(context);
    final canSignOut = !focus.isActiveRun &&
        !focus.isRecording &&
        !focus.runCommandBusy &&
        !focus.hasUnfinishedServerRun &&
        !context.watch<TaskProvider>().isApiBusy;
    final palette = context.melbel;
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final heroBg = isDark ? const Color(0xFF332947) : const Color(0xFFF1E8FD);

    return MelbelPageBackdrop(
      showTopCloud: true,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: const MelbelAppBar(
          title: 'Không gian cá nhân',
          showBack: true,
          showGlobalActions: false,
          showTitleRays: true,
        ),
        body: ListView(
          padding: MelbelLayout.pageInsets(context, top: 12, bottom: 24),
          children: [
            Container(
              clipBehavior: Clip.antiAlias,
              decoration: BoxDecoration(
                color: heroBg,
                borderRadius: BorderRadius.circular(28),
                border: isDark
                    ? Border.all(
                        color: palette.hairline.withValues(alpha: 0.55),
                        width: 1.0,
                      )
                    : null,
                boxShadow: isDark
                    ? [
                        BoxShadow(
                          color: Colors.black.withValues(alpha: 0.22),
                          blurRadius: 18,
                          offset: const Offset(0, 6),
                        ),
                      ]
                    : [
                        BoxShadow(
                          color:
                              const Color(0xFF534070).withValues(alpha: 0.06),
                          blurRadius: 20,
                          offset: const Offset(0, 7),
                        ),
                      ],
              ),
              child: Stack(
                children: [
                  Positioned(
                    right: 0,
                    bottom: 0,
                    width: 132,
                    height: 92,
                    child: IgnorePointer(
                      child: RepaintBoundary(
                        child: CustomPaint(
                          painter: _ProfileBannerDecorPainter(isDark: isDark),
                        ),
                      ),
                    ),
                  ),
                  Padding(
                    padding: const EdgeInsets.fromLTRB(18, 12, 22, 12),
                    child: Row(
                      children: [
                        Stack(
                          clipBehavior: Clip.none,
                          children: [
                            MelbelCatEarAvatar(
                              photoUrl: auth.photoUrl,
                              displayName: auth.user?['display_name'] as String?,
                              size: 68,
                            ),
                            Positioned(
                              top: 2,
                              right: -8,
                              child: IgnorePointer(
                                child: Transform.rotate(
                                  angle: 0.32,
                                  child: MelbelDoodleRays(
                                    size: 16,
                                    color: isDark
                                        ? const Color(0xFFFFF7E0)
                                        : const Color(0xFF4A3866),
                                  ),
                                ),
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(width: 16),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                auth.user?['display_name'] as String? ??
                                    'Tài khoản của bạn',
                                style: theme.textTheme.titleLarge?.copyWith(
                                  fontSize: 20,
                                  fontWeight: FontWeight.w800,
                                  letterSpacing: -0.3,
                                  color: palette.onSurface,
                                ),
                              ),
                              if (auth.user?['email'] case final String email)
                                ...[
                                  const SizedBox(height: 4),
                                  Text(
                                    email,
                                    style: theme.textTheme.bodySmall?.copyWith(
                                      color: palette.onSurfaceVariant,
                                    ),
                                  ),
                                ],
                              if (AuthService.isDev && auth.user == null) ...[
                                const SizedBox(height: 8),
                                const MelbelStatusPill(
                                  label: 'Tài khoản phát triển',
                                  icon: Icons.verified_user_outlined,
                                  tone: MelbelCardTone.amber,
                                ),
                              ],
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),
            MelbelCard(
              borderRadius: 26,
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Column(
                children: [
                  _GroupedAccountTile(
                    icon: Icons.settings_outlined,
                    title: 'Cài đặt trợ lý',
                    useHoney: false,
                    onTap: () => Navigator.of(context).pushNamed('/settings'),
                  ),
                  Divider(
                    height: 1,
                    indent: 68,
                    endIndent: 16,
                    color: palette.hairline.withValues(alpha: 0.5),
                  ),
                  _GroupedAccountTile(
                    icon: Icons.inbox_outlined,
                    title: 'Suy nghĩ đã cất',
                    useHoney: true,
                    onTap: () => Navigator.of(context).pushNamed('/thoughts'),
                  ),
                  Divider(
                    height: 1,
                    indent: 68,
                    endIndent: 16,
                    color: palette.hairline.withValues(alpha: 0.5),
                  ),
                  FutureBuilder<List<Map<String, dynamic>>>(
                    future: _calendarConnections,
                    builder: (context, snapshot) => _GroupedAccountTile(
                      icon: Icons.cloud_sync_outlined,
                      title: 'Google Calendar',
                      subtitle: _calendarStatus(snapshot),
                      useHoney: false,
                      onTap: _openGoogleCalendar,
                    ),
                  ),
                  Divider(
                    height: 1,
                    indent: 68,
                    endIndent: 16,
                    color: palette.hairline.withValues(alpha: 0.5),
                  ),
                  FutureBuilder<Map<String, dynamic>>(
                    future: _connection,
                    builder: (context, snapshot) {
                      final dbOk = snapshot.data?['database'] == 'ok';
                      final subtitle =
                          snapshot.connectionState != ConnectionState.done
                              ? 'Đang kiểm tra…'
                              : snapshot.hasError
                                  ? 'Chưa kết nối được'
                                  : dbOk
                                      ? 'Đã kết nối · CSDL ổn định'
                                      : 'Đã kết nối';
                      return _GroupedAccountTile(
                        icon: Icons.cloud_outlined,
                        title: 'Kết nối máy chủ',
                        subtitle: subtitle,
                        useHoney: true,
                        onTap: () => setState(
                          () => _connection = ApiClient().checkHealth(),
                        ),
                      );
                    },
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            const _AppearanceSettingsPanel(),
            const SizedBox(height: 12),
            MelbelDisclosure(
                title: 'Nhật ký của bạn',
                icon: Icons.history_rounded,
                children: [
                  _HelpRow(
                    icon: Icons.pets_rounded,
                    title: 'Chuỗi ngày',
                    subtitle: streak == null
                        ? '— · Chưa có dữ liệu'
                        : streak.isUnlocked
                            ? '${streak.effectiveDisplayStreak} ngày liên tiếp · Kỷ lục ${streak.longestStreak} ngày'
                            : 'Khởi động ${streak.effectiveDisplayStreak}/${StreakProvider.unlockDays} ngày · Chạm để xem hướng dẫn',
                    onTap: streak == null
                        ? () => showMelbelStreakGuide(context)
                        : () => showMelbelStreakSheet(context, streak),
                  ),
                  const _HelpRow(
                    icon: Icons.hourglass_top,
                    title: 'Thời gian tập trung',
                    subtitle: '— · Chưa có dữ liệu',
                    useHoney: true,
                  ),
                  const _HelpRow(
                    icon: Icons.all_inbox_outlined,
                    title: 'Hòm thư nháp',
                    subtitle: '— · Chưa có dữ liệu tài khoản',
                  ),
                ]),
            const SizedBox(height: 14),
            if (auth.needsSignIn) ...[
              const Text('Hãy đăng nhập lại bằng tài khoản hiện tại để đồng bộ.'),
              const GoogleLoginButton(),
              const SizedBox(height: 10),
            ],
            if (auth.error != null) ...[
              Text(auth.error!),
              const SizedBox(height: 10),
            ],
            if (!AuthService.isDev) ...[
              SecondaryCta(
                label: 'Đăng xuất',
                icon: Icons.logout_rounded,
                onPressed: canSignOut && !auth.busy ? auth.signOut : null,
              ),
              if (!canSignOut) ...[
                const SizedBox(height: 8),
                const Text(
                  'Hãy kết thúc phiên hoặc đợi thao tác hiện tại hoàn tất trước khi đăng xuất.',
                ),
              ],
              const SizedBox(height: 10),
            ],
            PrimaryCta(
              label: 'Mở thiết lập ban đầu',
              icon: Icons.tune_rounded,
              height: 56,
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => const SetupFlowScreen(),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _AppearanceSettingsPanel extends StatelessWidget {
  const _AppearanceSettingsPanel();

  @override
  Widget build(BuildContext context) {
    final theme = context.watch<ThemeProvider>();
    final palette = context.melbel;
    return MelbelDisclosure(
      title: 'Giao diện',
      icon: Icons.contrast_rounded,
      summary: switch (theme.appearance) {
        'light' => 'Sáng',
        'dark' => 'Tối',
        _ => 'Tự động theo giờ địa phương',
      },
      children: [
        for (final option in const [
          ('light', 'Sáng', Icons.light_mode_outlined),
          ('dark', 'Tối', Icons.dark_mode_outlined),
          ('system', 'Theo giờ địa phương', Icons.brightness_auto_outlined),
        ])
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: MelbelCard(
              tone: theme.appearance == option.$1
                  ? MelbelCardTone.sage
                  : MelbelCardTone.neutral,
              borderColor: theme.appearance == option.$1
                  ? palette.sage
                  : palette.hairline,
              borderWidth: theme.appearance == option.$1 ? 1.5 : 1.0,
              padding: EdgeInsets.zero,
              child: ListTile(
                leading: Icon(
                  option.$3,
                  color: theme.appearance == option.$1
                      ? palette.sage
                      : palette.onSurfaceVariant,
                ),
                title: Text(option.$2),
                selected: theme.appearance == option.$1,
                selectedColor: palette.onSageSoft,
                trailing: Icon(
                  theme.appearance == option.$1
                      ? Icons.radio_button_checked_rounded
                      : Icons.radio_button_unchecked_rounded,
                  color: theme.appearance == option.$1
                      ? palette.sage
                      : palette.controlOutline,
                ),
                onTap: () => theme.setAppearance(option.$1),
              ),
            ),
          ),
        Text(
          'Tự động chuyển tối từ 18:00 đến 06:00.',
          style: Theme.of(context).textTheme.bodySmall,
        ),
      ],
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
  final _stt = TwoTierSttService.fromVoiceService(VoiceTranscriptionService());
  Uf1VoiceRecorderService? _recorder;
  VoicePlaybackService? _voice;
  Future<List<Map<String, dynamic>>>? _result;
  bool _listening = false;
  bool _starting = false;
  bool _transcribing = false;
  bool _useOnDevice = false;
  bool _speaking = false;
  String? _hint;

  VoicePlaybackService _ensureVoice() =>
      _voice ??= VoicePlaybackService(cache: kIsWeb ? MemoryTtsAudioCache() : null);

  @override
  void dispose() {
    _stt.cancel();
    if (_voice != null) {
      unawaited(_voice!.dispose().catchError((_) {}));
    }
    if (_recorder != null) {
      unawaited(_recorder!.dispose().catchError((_) {}));
    }
    _controller.dispose();
    _speech.dispose();
    super.dispose();
  }

  Future<void> _stopPlaybackIfAny() async {
    if (_voice != null) {
      await _voice!.stop();
    }
    if (mounted && _speaking) {
      setState(() => _speaking = false);
    }
  }

  Future<void> _toggleMic() async {
    if (_starting || _transcribing) return;
    await _stopPlaybackIfAny();

    final useNative =
        !kIsWeb && defaultTargetPlatform == TargetPlatform.android && !_useOnDevice;
    if (useNative) {
      final recorder = _recorder ??= Uf1VoiceRecorderService();
      recorder.onAutoStopped = (rec) {
        if (mounted) unawaited(_transcribeRecording(rec));
      };
      recorder.onAutoStopFailed = (_) {
        if (!mounted) return;
        setState(() {
          _listening = false;
          _useOnDevice = true;
          _hint = 'Chuyển sang nghe trực tiếp trên máy. Chạm mic để nói lại.';
        });
      };
      if (_listening) {
        setState(() {
          _starting = true;
          _listening = false;
        });
        try {
          final recording = await recorder.stopAndFinalize();
          if (mounted) {
            await _transcribeRecording(recording);
          } else {
            await recorder.deleteRecording(recording.path);
          }
        } catch (_) {
          if (mounted) {
            setState(() {
              _useOnDevice = true;
              _hint = 'Chưa thu được ghi âm. Bạn có thể chạm mic để nói trên máy hoặc nhập chữ.';
            });
          }
        } finally {
          if (mounted) setState(() => _starting = false);
        }
        return;
      }

      setState(() {
        _starting = true;
        _listening = true;
        _hint = null;
      });
      try {
        await recorder.start(maxDuration: const Duration(seconds: 25));
      } catch (_) {
        if (mounted) {
          setState(() {
            _listening = false;
            _useOnDevice = true;
            _hint = 'Chuyển sang nghe trực tiếp trên máy. Chạm mic để nói.';
          });
        }
      } finally {
        if (mounted) setState(() => _starting = false);
      }
      return;
    }

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
        onWords: (words, isFinal) {
          if (!mounted || !_listening) return;
          setState(() => _controller.text = words);
          if (isFinal && words.trim().isNotEmpty) {
            done();
            _lookup();
          }
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

  Future<void> _transcribeRecording(ThoughtRecording recording) async {
    final recorder = _recorder;
    setState(() {
      _listening = false;
      _transcribing = true;
      _hint = null;
    });
    try {
      final transcript = await _stt.transcribeTwoTier(
        recording.path,
        recordingId: recording.path,
      );
      if (!mounted) return;
      _controller.text = transcript.text;
      if (transcript.text.trim().isNotEmpty) _lookup();
    } on SttCancelled {
      if (mounted) setState(() => _hint = null);
    } on SttNeedsOnDeviceFallback catch (e) {
      if (mounted) {
        setState(() {
          _useOnDevice = true;
          _hint = e.userMessage;
        });
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _hint = e.message);
    } catch (_) {
      if (mounted) setState(() => _hint = 'Chưa chuyển được lời nói. Bạn có thể nhập chữ.');
    } finally {
      if (recorder != null) {
        await recorder.deleteRecording(recording.path);
      }
      if (mounted) setState(() => _transcribing = false);
    }
  }

  Future<void> _speakSessionsSummary(
    BuildContext context,
    List<Map<String, dynamic>> sessions,
  ) async {
    if (_speaking || (_voice?.isSpeaking ?? false)) {
      await _stopPlaybackIfAny();
      return;
    }
    if (sessions.isEmpty) return;
    final first = sessions.first;
    final firstTitle = (first['step_title'] as String?)?.trim().isNotEmpty == true
        ? (first['step_title'] as String).trim()
        : 'phiên tập trung';
    final firstStart = _time(context, first['scheduled_start']);
    final summary =
        'Ngày này bạn có ${sessions.length} phiên trong lịch. Phiên đầu tiên là $firstTitle lúc $firstStart.';
    setState(() => _speaking = true);
    try {
      await _ensureVoice().speakText(summary);
    } finally {
      if (mounted) setState(() => _speaking = false);
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
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    return MelbelPageBackdrop(
      showTopCloud: true,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: const MelbelAppBar(
          title: 'Hỏi về lịch',
          showBack: true,
          showGlobalActions: false,
          showTitleRays: true,
        ),
        body: ListView(
          padding: MelbelLayout.pageInsets(context, top: 12, bottom: 24),
          children: [
            MelbelCard(
              borderRadius: 28,
              padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 24),
              child: Column(
                children: [
                  SizedBox.square(
                    dimension: 120,
                    child: Stack(
                      alignment: Alignment.center,
                      clipBehavior: Clip.none,
                      children: [
                        Positioned(
                          top: 4,
                          right: 6,
                          child: IgnorePointer(
                            child: Transform.rotate(
                              angle: 0.34,
                              child: MelbelDoodleRays(
                                size: 17,
                                color: _listening
                                    ? palette.amber
                                    : (isDark
                                        ? const Color(0xFFB49EE8)
                                        : const Color(0xFF9C84D9)),
                              ),
                            ),
                          ),
                        ),
                        DecoratedBox(
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            color: _listening
                                ? palette.amberSoft
                                : palette.sageSoft,
                          ),
                          child: const SizedBox.square(dimension: 108),
                        ),
                        DecoratedBox(
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            color: _listening ? palette.amber : palette.sage,
                          ),
                          child: IconButton.filled(
                            tooltip:
                                _listening ? 'Dừng nghe' : 'Bắt đầu nói',
                            iconSize: 32,
                            style: IconButton.styleFrom(
                              minimumSize: const Size.square(76),
                              backgroundColor: Colors.transparent,
                              foregroundColor: palette.onSage,
                            ),
                            onPressed:
                                _starting || _transcribing ? null : _toggleMic,
                            icon: Icon(
                              _listening
                                  ? Icons.stop_rounded
                                  : Icons.mic_rounded,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 10),
                  Text(
                    _transcribing
                        ? 'Đang chuyển giọng nói…'
                        : _listening
                            ? 'Đang lắng nghe…'
                            : 'Chạm để hỏi lịch',
                    style: theme.textTheme.titleMedium?.copyWith(
                      fontWeight: FontWeight.w700,
                      color: palette.onSurface,
                    ),
                  ),
                  if (_transcribing) ...[
                    const SizedBox(height: 6),
                    TextButton(
                      onPressed: _stt.cancel,
                      child: const Text('Hủy'),
                    ),
                  ],
                  const SizedBox(height: 16),
                  TextField(
                    controller: _controller,
                    decoration: const InputDecoration(
                      labelText: 'Hỏi lịch theo ngày',
                      hintText: 'Ngày mai tôi có lịch lúc mấy giờ?',
                    ),
                    textInputAction: TextInputAction.search,
                    onSubmitted: (_) {
                      if (!_listening && _controller.text.trim().isNotEmpty) {
                        _lookup();
                      }
                    },
                    onChanged: (_) => setState(() {
                      _result = null;
                      _hint = null;
                    }),
                  ),
                  const SizedBox(height: 14),
                  PrimaryCta(
                    label: 'Xem lịch ngày này',
                    onPressed: _listening || _controller.text.trim().isEmpty
                        ? null
                        : _lookup,
                  ),
                ],
              ),
            ),
            if (_hint != null) ...[
              const SizedBox(height: 12),
              MelbelCard(
                borderRadius: 20,
                tone: MelbelCardTone.amber,
                padding: const EdgeInsets.all(14),
                child: Semantics(
                  liveRegion: true,
                  child: Text(_hint!),
                ),
              ),
            ],
            if (_result != null) ...[
              const SizedBox(height: 14),
              FutureBuilder<List<Map<String, dynamic>>>(
                future: _result,
                builder: (context, snapshot) {
                  if (snapshot.connectionState != ConnectionState.done) {
                    return const Padding(
                      padding: EdgeInsets.only(top: 4),
                      child: MelbelLoadingSkeleton(
                        cardCount: 1,
                        caption: 'Đang tra cứu lịch...',
                      ),
                    );
                  }
                  if (snapshot.hasError) {
                    return const MelbelCard(
                      borderRadius: 22,
                      tone: MelbelCardTone.amber,
                      child: Text('Chưa tải được lịch. Hãy thử lại.'),
                    );
                  }
                  final sessions = snapshot.data ?? const [];
                  if (sessions.isEmpty) {
                    return const MelbelCard(
                      borderRadius: 22,
                      child: Text('Ngày này chưa có phiên trong lịch.'),
                    );
                  }
                  return MelbelCard(
                    borderRadius: 24,
                    padding: const EdgeInsets.symmetric(vertical: 4),
                    child: Column(
                      children: [
                        for (final session in sessions)
                          ListTile(
                            leading: Container(
                              width: 38,
                              height: 38,
                              decoration: BoxDecoration(
                                color: palette.sageSoft,
                                borderRadius: BorderRadius.circular(12),
                              ),
                              child: Icon(
                                Icons.schedule_rounded,
                                size: 20,
                                color: palette.sage,
                              ),
                            ),
                            title: Text(
                              session['step_title'] as String? ??
                                  'Phiên làm việc',
                              style: theme.textTheme.titleSmall?.copyWith(
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                            subtitle: Text(
                              '${_time(context, session['scheduled_start'])} – ${_time(context, session['scheduled_end'])}',
                            ),
                          ),
                        Padding(
                          padding: const EdgeInsets.fromLTRB(12, 4, 12, 8),
                          child: Align(
                            alignment: Alignment.centerRight,
                            child: TextButton.icon(
                              onPressed: () =>
                                  _speakSessionsSummary(context, sessions),
                              icon: Icon(
                                _speaking
                                    ? Icons.volume_off_rounded
                                    : Icons.volume_up_rounded,
                              ),
                              label: Text(
                                _speaking
                                    ? 'Dừng đọc'
                                    : 'Nghe Melbel đọc lịch',
                              ),
                            ),
                          ),
                        ),
                      ],
                    ),
                  );
                },
              ),
            ],
          ],
        ),
      ),
    );
  }

  String _time(BuildContext context, Object? value) {
    final date = DateTime.tryParse(value as String? ?? '')?.toLocal();
    if (date == null) return '—';
    return MaterialLocalizations.of(context).formatTimeOfDay(
        TimeOfDay.fromDateTime(date),
        alwaysUse24HourFormat: true);
  }
}

class _HelpRow extends StatelessWidget {
  const _HelpRow({
    required this.icon,
    required this.title,
    required this.subtitle,
    this.useHoney = false,
    this.showRays = false,
    this.onTap,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final bool useHoney;
  final bool showRays;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final iconBg = useHoney ? palette.amberSoft : palette.sageSoft;
    final iconColor = useHoney
        ? (isDark ? const Color(0xFFF0C652) : const Color(0xFFC99B30))
        : (isDark ? palette.onSageSoft : palette.sage);

    final badge = Container(
      width: 42,
      height: 42,
      decoration: BoxDecoration(
        color: iconBg,
        borderRadius: BorderRadius.circular(14),
      ),
      child: Icon(icon, color: iconColor, size: 20),
    );

    final rowContent = Padding(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      child: Row(
        children: [
          if (showRays)
            Stack(
              clipBehavior: Clip.none,
              children: [
                badge,
                Positioned(
                  top: -4,
                  right: -4,
                  child: IgnorePointer(
                    child: Transform.rotate(
                      angle: 0.35,
                      child: MelbelDoodleRays(
                        size: 13,
                        color: isDark
                            ? const Color(0xFFF3CA52)
                            : const Color(0xFFF0C24D),
                      ),
                    ),
                  ),
                ),
              ],
            )
          else
            badge,
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: theme.textTheme.titleSmall?.copyWith(
                    fontSize: 15.5,
                    fontWeight: FontWeight.w700,
                    color: palette.onSurface,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  subtitle,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: palette.onSurfaceVariant,
                    height: 1.3,
                  ),
                ),
              ],
            ),
          ),
          if (onTap != null) ...[
            const SizedBox(width: 8),
            Icon(
              Icons.chevron_right_rounded,
              color: palette.onSurfaceVariant,
            ),
          ],
        ],
      ),
    );

    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: MelbelCard(
        borderRadius: 22,
        padding: EdgeInsets.zero,
        child: onTap == null
            ? rowContent
            : InkWell(
                borderRadius: BorderRadius.circular(22),
                onTap: onTap,
                child: rowContent,
              ),
      ),
    );
  }
}

class _Faq extends StatelessWidget {
  const _Faq({required this.question, required this.answer});
  final String question;
  final String answer;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final palette = context.melbel;
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: MelbelCard(
        borderRadius: 20,
        padding: EdgeInsets.zero,
        child: Theme(
          data: theme.copyWith(dividerColor: Colors.transparent),
          child: ExpansionTile(
            dense: true,
            visualDensity: VisualDensity.compact,
            minTileHeight: 46,
            tilePadding:
                const EdgeInsets.symmetric(horizontal: 16, vertical: 2),
            title: Text(
              question,
              style: theme.textTheme.titleSmall?.copyWith(
                fontSize: 15,
                fontWeight: FontWeight.w700,
                color: palette.onSurface,
              ),
            ),
            childrenPadding: const EdgeInsets.fromLTRB(18, 0, 18, 16),
            children: [
              Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  answer,
                  style: theme.textTheme.bodyMedium?.copyWith(
                    color: palette.onSurfaceVariant,
                    height: 1.4,
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

class _GroupedAccountTile extends StatelessWidget {
  const _GroupedAccountTile({
    required this.icon,
    required this.title,
    required this.useHoney,
    this.subtitle,
    this.onTap,
  });

  final IconData icon;
  final String title;
  final bool useHoney;
  final String? subtitle;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final iconColor = useHoney
        ? (isDark ? const Color(0xFFF0C652) : const Color(0xFFC99B30))
        : (isDark ? palette.onSageSoft : palette.sage);

    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      leading: Container(
        width: 40,
        height: 40,
        decoration: BoxDecoration(
          color: useHoney ? palette.amberSoft : palette.sageSoft,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Icon(icon, size: 20, color: iconColor),
      ),
      title: Text(
        title,
        style: theme.textTheme.titleSmall?.copyWith(
          fontSize: 16,
          color: palette.onSurface,
          fontWeight: FontWeight.w700,
        ),
      ),
      subtitle: subtitle == null
          ? null
          : Text(
              subtitle!,
              style: theme.textTheme.bodySmall?.copyWith(
                color: palette.onSurfaceVariant,
              ),
            ),
      trailing: onTap == null
          ? null
          : Icon(
              Icons.chevron_right_rounded,
              color: palette.onSurfaceVariant,
            ),
      onTap: onTap,
    );
  }
}

class _ProfileBannerDecorPainter extends CustomPainter {
  const _ProfileBannerDecorPainter({required this.isDark});

  final bool isDark;

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width;
    final h = size.height;

    final cloudBack = Paint()
      ..color = isDark
          ? const Color(0xFF4F3E72).withValues(alpha: 0.88)
          : const Color(0xFFD5C3F4).withValues(alpha: 0.78);
    final cloudFront = Paint()
      ..color = isDark
          ? const Color(0xFF63508C).withValues(alpha: 0.85)
          : const Color(0xFFE3D5FB).withValues(alpha: 0.86);

    canvas.drawCircle(Offset(w * 0.34, h * 0.96), 24, cloudBack);
    canvas.drawCircle(Offset(w * 0.64, h * 0.84), 32, cloudBack);
    canvas.drawCircle(Offset(w * 0.94, h * 0.72), 34, cloudBack);
    canvas.drawCircle(Offset(w * 0.48, h * 1.04), 24, cloudFront);
    canvas.drawCircle(Offset(w * 0.80, h * 0.95), 28, cloudFront);

    // Sage green sprout leaves
    final leafLight = Paint()
      ..color = isDark ? const Color(0xFFA4BA8D) : const Color(0xFFB5C798);
    final leafDark = Paint()
      ..color = isDark ? const Color(0xFF8CA476) : const Color(0xFF9EB482);

    void drawLeaf(
      Offset base,
      double angle,
      double len,
      double halfW,
      Paint paint,
    ) {
      canvas.save();
      canvas.translate(base.dx, base.dy);
      canvas.rotate(angle);
      final p = Path()
        ..moveTo(0, 0)
        ..quadraticBezierTo(-halfW, -len * 0.45, 0, -len)
        ..quadraticBezierTo(halfW, -len * 0.45, 0, 0)
        ..close();
      canvas.drawPath(p, paint);
      canvas.restore();
    }

    drawLeaf(Offset(w * 0.68, h * 0.82), -0.52, 32, 8.5, leafLight);
    drawLeaf(Offset(w * 0.70, h * 0.82), 0.56, 26, 7.0, leafDark);

    // Gold 4-point sparkle star above sprout
    final starColor =
        isDark ? const Color(0xFFF3CA52) : const Color(0xFFF3C854);
    final cx = w * 0.62;
    final cy = h * 0.25;
    const r = 7.5;
    final starPath = Path()
      ..moveTo(cx, cy - r)
      ..quadraticBezierTo(cx, cy, cx + r, cy)
      ..quadraticBezierTo(cx, cy, cx, cy + r)
      ..quadraticBezierTo(cx, cy, cx - r, cy)
      ..quadraticBezierTo(cx, cy, cx, cy - r)
      ..close();
    canvas.drawPath(starPath, Paint()..color = starColor);
  }

  @override
  bool shouldRepaint(_ProfileBannerDecorPainter oldDelegate) =>
      oldDelegate.isDark != isDark;
}
