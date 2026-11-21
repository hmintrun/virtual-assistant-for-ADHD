// Màn hình kết nối Google Calendar: cấp quyền → chọn lịch đọc giờ bận → xem giờ bận 7 ngày tới.
// Giờ bận chỉ là dữ liệu đầu vào cho thuật toán Python xếp lịch (AGENTS.md mục 3.4);
// màn hình này KHÔNG tự tạo/dời phiên nào.
import 'dart:async';

import 'package:app_links/app_links.dart';
import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:url_launcher/url_launcher.dart';

import '../core/api_client.dart';
import '../core/theme.dart';
import '../services/calendar_api.dart';
import '../widgets/melbel_ui.dart';

class CalendarConnectScreen extends StatefulWidget {
  const CalendarConnectScreen({super.key, this.api});

  /// Cho phép test truyền API giả vào.
  final CalendarApi? api;

  @override
  State<CalendarConnectScreen> createState() => _CalendarConnectScreenState();
}

class _CalendarConnectScreenState extends State<CalendarConnectScreen>
    with WidgetsBindingObserver {
  static const Duration _busyWindow = Duration(days: 7);

  late final CalendarApi _api = widget.api ?? CalendarApi();
  final AppLinks _appLinks = AppLinks();
  StreamSubscription<Uri>? _linkSub;

  bool _loading = true;
  bool _working = false;
  bool _waitingForBrowser = false;
  String? _error;
  CalendarConnection? _connection;
  List<CalendarItem> _calendars = const [];
  Set<String> _selectedReadIds = {};
  List<CalendarBusyEvent> _busyEvents = const [];

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    // Nhận proplan://calendar/connected?connection_id=... khi trình duyệt trả về app.
    _linkSub = _appLinks.uriLinkStream.listen(_onDeepLink);
    _refresh();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _linkSub?.cancel();
    super.dispose();
  }

  /// Dự phòng: user tự bấm Back từ trình duyệt (không có deep link) → vẫn
  /// kiểm tra lại xem kết nối đã được tạo chưa.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && _waitingForBrowser) {
      _waitingForBrowser = false;
      _refresh();
    }
  }

  void _onDeepLink(Uri uri) {
    if (uri.scheme != 'proplan' || uri.host != 'calendar') return;
    _waitingForBrowser = false;
    _refresh(preferredConnectionId: uri.queryParameters['connection_id']);
  }

  Future<void> _refresh({String? preferredConnectionId}) async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      // Kết nối đã ngắt thì coi như chưa kết nối; needs_reconnect (quyền bị
      // thu hồi/hết hạn) vẫn hiện để user biết tình trạng và kết nối lại.
      final connections = (await _api.listConnections())
          .where((c) => c.state != 'disconnected')
          .toList();
      CalendarConnection? connection;
      if (connections.isNotEmpty) {
        connection = connections.firstWhere(
          (c) => c.connectionId == preferredConnectionId,
          orElse: () => connections.firstWhere(
            (c) => c.state == 'connected',
            orElse: () => connections.last,
          ),
        );
      }
      var calendars = const <CalendarItem>[];
      if (connection != null && connection.state == 'connected') {
        calendars = await _api.listCalendars(connection.connectionId);
      }
      if (!mounted) return;
      setState(() {
        _connection = connection;
        _calendars = calendars;
        _selectedReadIds = {...?connection?.readCalendarIds};
      });
      await _loadBusyEvents();
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _connect() async {
    setState(() {
      _working = true;
      _error = null;
    });
    try {
      final authUrl = await _api.startAuthorization();
      _waitingForBrowser = true;
      final opened =
          await launchUrl(authUrl, mode: LaunchMode.externalApplication);
      if (!opened && mounted) {
        _waitingForBrowser = false;
        setState(() => _error = 'Chưa mở được trình duyệt. Thử lại nhé.');
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _working = false);
    }
  }

  Future<void> _saveSelection() async {
    final connection = _connection;
    if (connection == null) return;
    setState(() {
      _working = true;
      _error = null;
    });
    try {
      final updated = await _api.saveSelection(
        connectionId: connection.connectionId,
        readCalendarIds: _selectedReadIds.toList(),
        writeCalendarId: _pickWriteCalendarId(),
      );
      if (!mounted) return;
      setState(() => _connection = updated);
      await _loadBusyEvents();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Đã lưu lịch dùng để đọc giờ bận.')),
        );
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _working = false);
    }
  }

  /// Ngắt ngay, không hỏi lại (AGENTS.md mục 4: không dùng hộp thoại xác nhận).
  /// Lựa chọn lịch vẫn được Backend giữ, kết nối lại là dùng tiếp.
  Future<void> _disconnect() async {
    final connection = _connection;
    if (connection == null) return;
    setState(() {
      _working = true;
      _error = null;
    });
    try {
      await _api.disconnect(connection.connectionId);
      if (!mounted) return;
      setState(() {
        _connection = null;
        _calendars = const [];
        _busyEvents = const [];
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          duration: const Duration(seconds: 5),
          content: const Text(
            'Đã ngắt Google Calendar. Hoàn tác sẽ mở Google để cấp lại quyền.',
          ),
          action: SnackBarAction(
            label: 'Hoàn tác',
            onPressed: _undoDisconnect,
          ),
        ),
      );
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _working = false);
    }
  }

  void _undoDisconnect() {
    unawaited(_connect());
  }

  /// Lịch ghi phiên Focus sau khi duyệt: giữ lựa chọn cũ nếu có, không thì
  /// lịch chính (primary) có quyền ghi.
  String? _pickWriteCalendarId() {
    final current = _connection?.writeCalendarId;
    if (current != null) return current;
    final writable = _calendars.where((c) => c.canWrite).toList();
    if (writable.isEmpty) return null;
    return writable
        .firstWhere((c) => c.primary, orElse: () => writable.first)
        .id;
  }

  Future<void> _loadBusyEvents() async {
    final connection = _connection;
    if (connection == null ||
        connection.state != 'connected' ||
        connection.readCalendarIds.isEmpty) {
      if (mounted) setState(() => _busyEvents = const []);
      return;
    }
    final from = DateTime.now();
    final to = from.add(_busyWindow);
    final all = <CalendarBusyEvent>[];
    for (final calendarId in connection.readCalendarIds) {
      all.addAll(await _api.listBusyEvents(
        connectionId: connection.connectionId,
        googleCalendarId: calendarId,
        from: from,
        to: to,
      ));
    }
    all.sort((a, b) => a.startAt.compareTo(b.startAt));
    if (mounted) setState(() => _busyEvents = all);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: const MelbelAppBar(
        title: 'Google Calendar',
        showBack: true,
        showGlobalActions: false,
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(20),
              children: [
                if (_error != null) ...[
                  MelbelCard(
                    child: Text(
                      _error!,
                      style: TextStyle(color: MelbelColors.kOnSurfaceVariant),
                    ),
                  ),
                  const SizedBox(height: 12),
                ],
                if (_connection == null)
                  ..._buildNotConnected(context)
                else
                  ..._buildConnected(context, _connection!),
              ],
            ),
    );
  }

  List<Widget> _buildNotConnected(BuildContext context) => [
        Text(
          'Cho ProPlan đọc giờ bận từ Google Calendar để không xếp phiên '
          'tập trung trùng giờ học, giờ họp của bạn.',
          style: Theme.of(context).textTheme.bodyMedium,
        ),
        const SizedBox(height: 8),
        Text(
          'ProPlan chỉ ghi phiên lên lịch sau khi bạn duyệt kế hoạch.',
          style: Theme.of(context)
              .textTheme
              .bodySmall
              ?.copyWith(color: MelbelColors.kTextSecondary),
        ),
        const SizedBox(height: 20),
        PrimaryCta(
          label: 'Kết nối Google Calendar',
          icon: Icons.link,
          onPressed: _working ? null : _connect,
        ),
      ];

  List<Widget> _buildConnected(
    BuildContext context,
    CalendarConnection connection,
  ) {
    final textTheme = Theme.of(context).textTheme;
    if (connection.state != 'connected') {
      // Quyền bị thu hồi/hết hạn: báo tình trạng nguồn, cho kết nối lại.
      // Trong lúc đó lập kế hoạch vẫn dùng được giờ rảnh nhập tay.
      return [
        Text('Mất quyền đọc lịch: ${connection.accountRef}',
            style: textTheme.titleMedium),
        const SizedBox(height: 8),
        Text(
          'ProPlan chưa đọc được giờ bận từ tài khoản này. Khi lập kế hoạch, '
          'bạn có thể dùng giờ rảnh nhập tay.',
          style: textTheme.bodySmall
              ?.copyWith(color: MelbelColors.kTextSecondary),
        ),
        const SizedBox(height: 20),
        PrimaryCta(
          label: 'Kết nối lại Google Calendar',
          icon: Icons.refresh,
          onPressed: _working ? null : _connect,
        ),
        TextButton(
          onPressed: _working ? null : _disconnect,
          child: const Text('Ngắt kết nối'),
        ),
      ];
    }
    return [
      Text('Đã kết nối: ${connection.accountRef}',
          style: textTheme.titleMedium),
      const SizedBox(height: 16),
      Text('Đọc giờ bận từ lịch nào?', style: textTheme.titleSmall),
      const SizedBox(height: 4),
      for (final calendar in _calendars)
        CheckboxListTile(
          contentPadding: EdgeInsets.zero,
          value: _selectedReadIds.contains(calendar.id),
          title: Text(calendar.summary),
          subtitle: calendar.primary ? const Text('Lịch chính') : null,
          onChanged: _working
              ? null
              : (checked) => setState(() {
                    checked == true
                        ? _selectedReadIds.add(calendar.id)
                        : _selectedReadIds.remove(calendar.id);
                  }),
        ),
      const SizedBox(height: 12),
      PrimaryCta(
        label: 'Lưu lịch đọc giờ bận',
        icon: Icons.check,
        onPressed: _working ? null : _saveSelection,
      ),
      const SizedBox(height: 24),
      Text('Giờ bận 7 ngày tới', style: textTheme.titleSmall),
      const SizedBox(height: 8),
      if (connection.readCalendarIds.isEmpty)
        Text('Chọn ít nhất 1 lịch rồi bấm Lưu để xem giờ bận.',
            style: textTheme.bodySmall
                ?.copyWith(color: MelbelColors.kTextSecondary))
      else if (_busyEvents.isEmpty)
        Text('Không có giờ bận nào trong 7 ngày tới.',
            style: textTheme.bodySmall
                ?.copyWith(color: MelbelColors.kTextSecondary))
      else
        for (final event in _busyEvents) _BusyEventRow(event: event),
      const SizedBox(height: 24),
      TextButton.icon(
        onPressed: _working ? null : _disconnect,
        icon: const Icon(Icons.link_off),
        label: const Text('Ngắt kết nối Google Calendar'),
      ),
    ];
  }
}

class _BusyEventRow extends StatelessWidget {
  const _BusyEventRow({required this.event});

  final CalendarBusyEvent event;

  @override
  Widget build(BuildContext context) {
    // Không dùng locale 'vi' của intl vì app chưa initializeDateFormatting.
    const weekdays = ['T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN'];
    String dayOf(DateTime t) =>
        '${weekdays[t.weekday - 1]} ${DateFormat('dd/MM').format(t)}';
    final day = dayOf(event.startAt);
    final sameDay = DateUtils.isSameDay(event.startAt, event.endAt);
    // Sự kiện qua ngày hôm sau: ghi rõ ngày kết thúc, tránh "03:30–02:30".
    final time = sameDay
        ? '${DateFormat.Hm().format(event.startAt)}–'
            '${DateFormat.Hm().format(event.endAt)}'
        : '${DateFormat.Hm().format(event.startAt)} → '
            '${DateFormat.Hm().format(event.endAt)} ${dayOf(event.endAt)}';
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          SizedBox(
            width: 110,
            child: Text('$day\n$time',
                style: Theme.of(context).textTheme.bodySmall),
          ),
          Expanded(child: Text(event.title ?? 'Bận')),
        ],
      ),
    );
  }
}
