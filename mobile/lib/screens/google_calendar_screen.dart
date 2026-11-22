import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../core/api_client.dart';
import '../core/responsive_layout.dart';
import '../core/theme.dart';
import '../services/google_calendar_api.dart';
import '../widgets/melbel_ui.dart';

class GoogleCalendarScreen extends StatefulWidget {
  const GoogleCalendarScreen({super.key});

  @override
  State<GoogleCalendarScreen> createState() => _GoogleCalendarScreenState();
}

class _GoogleCalendarScreenState extends State<GoogleCalendarScreen>
    with WidgetsBindingObserver {
  final _api = GoogleCalendarApi(ApiClient());
  List<Map<String, dynamic>> _connections = [];
  bool _loading = false;
  bool _pendingConsent = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _load();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && _pendingConsent) _load();
  }

  Future<void> _perform(Future<void> Function() operation) async {
    if (_loading) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      await operation();
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } catch (_) {
      if (mounted)
        setState(() => _error = 'Chưa mở được Google Calendar. Hãy thử lại.');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _readConnections() async {
    final items = await _api.connections();
    if (!mounted) return;
    setState(() {
      _connections = items
          .where((item) => item['connection_state'] != 'disconnected')
          .toList();
      if (_connections.any((item) => item['connection_state'] == 'connected')) {
        _pendingConsent = false;
      }
    });
  }

  Future<void> _load() => _perform(_readConnections);

  Future<void> _connect() => _perform(() async {
        final uri = await _api.authorize(
            returnTarget: kIsWeb
                ? Uri.base
                    .replace(query: '', fragment: '/google-calendar')
                    .toString()
                : null);
        if (!mounted) return;
        setState(() => _pendingConsent = true);
        if (!await launchUrl(uri,
            mode: LaunchMode.externalApplication, webOnlyWindowName: '_self')) {
          if (mounted) setState(() => _pendingConsent = false);
          throw const ApiException('Chưa mở được trang cấp quyền Google.');
        }
      });

  Future<void> _open(Map<String, dynamic> connection) async {
    final disconnected = await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) =>
            _GoogleCalendarAccount(api: _api, connection: connection),
      ),
    );
    if (!mounted) return;
    await _load();
    if (disconnected == true && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        duration: const Duration(seconds: 5),
        content: const Text(
            'Đã ngắt Google Calendar. Hoàn tác sẽ mở Google để cấp lại quyền.'),
        action: SnackBarAction(
          label: 'Hoàn tác',
          onPressed: () => unawaited(_connect()),
        ),
      ));
    }
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.melbel;
    return MelbelPageBackdrop(
      showTopCloud: false,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: const MelbelAppBar(
          title: 'Google Calendar',
          showBack: true,
          showTitleRays: true,
        ),
        body: SafeArea(
          child: ListView(
            padding: MelbelLayout.pageInsets(context, top: 16, bottom: 28),
            children: [
              if (_loading)
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(999),
                    child: const LinearProgressIndicator(minHeight: 6),
                  ),
                ),
              if (_error != null) ...[
                MelbelCard(
                  borderRadius: 22,
                  tone: MelbelCardTone.amber,
                  child: Row(
                    children: [
                      Icon(Icons.cloud_off_outlined, color: palette.amber),
                      const SizedBox(width: 10),
                      Expanded(child: Text(_error!)),
                    ],
                  ),
                ),
                const SizedBox(height: 12),
              ],
              if (_pendingConsent) ...[
                const MelbelCard(
                  borderRadius: 22,
                  tone: MelbelCardTone.sage,
                  child: Text(
                    'Sau khi cấp quyền trên Google, quay lại và tải lại kết nối.',
                  ),
                ),
                const SizedBox(height: 12),
              ],
              if (!_loading && _error == null && _connections.isEmpty) ...[
                const MelbelSectionBanner(
                  title: 'Đồng bộ lịch Google',
                  art: MelbelSectionArt.sprout,
                ),
                const SizedBox(height: 12),
                const MelbelCard(
                  borderRadius: 24,
                  child: Text(
                    'Kết nối Google Calendar để xem lịch bận và đồng bộ các phiên đã chốt.',
                  ),
                ),
                const SizedBox(height: 12),
              ],
              for (final connection in _connections)
                Padding(
                  padding: const EdgeInsets.only(bottom: 10),
                  child: MelbelCard(
                    borderRadius: 24,
                    padding: EdgeInsets.zero,
                    child: ListTile(
                      contentPadding: const EdgeInsets.symmetric(
                        horizontal: 16,
                        vertical: 6,
                      ),
                      leading: Container(
                        width: 40,
                        height: 40,
                        decoration: BoxDecoration(
                          color: palette.sageSoft,
                          borderRadius: BorderRadius.circular(14),
                        ),
                        child: Icon(
                          Icons.calendar_month_outlined,
                          color: palette.sage,
                        ),
                      ),
                      title: Text(
                        connection['account_ref'] as String? ?? 'Google Calendar',
                        style: Theme.of(context)
                            .textTheme
                            .titleSmall
                            ?.copyWith(fontWeight: FontWeight.w700),
                      ),
                      subtitle: Text(
                        connection['connection_state'] != 'connected'
                            ? 'Cần cấp lại quyền'
                            : connection['write_calendar_id'] == null
                                ? 'Chưa chọn lịch ghi phiên'
                                : 'Đã chọn lịch ghi phiên',
                      ),
                      trailing: Icon(
                        Icons.chevron_right_rounded,
                        color: palette.onSurfaceVariant,
                      ),
                      onTap: _loading
                          ? null
                          : connection['connection_state'] == 'connected'
                              ? () => _open(connection)
                              : _connect,
                    ),
                  ),
                ),
              const SizedBox(height: 8),
              if (_pendingConsent) ...[
                PrimaryCta(
                  label: 'Kiểm tra quyền đã cấp',
                  onPressed: _loading ? null : _load,
                ),
                const SizedBox(height: 10),
                SecondaryCta(
                  label: 'Mở lại trang cấp quyền',
                  onPressed: _loading ? null : _connect,
                ),
              ] else ...[
                PrimaryCta(
                  label: _connections.isEmpty
                      ? 'Liên kết Google Calendar'
                      : 'Liên kết thêm tài khoản',
                  onPressed: _loading ? null : _connect,
                ),
                const SizedBox(height: 10),
                SecondaryCta(
                  label: 'Tải lại kết nối',
                  onPressed: _loading ? null : _load,
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _GoogleCalendarAccount extends StatefulWidget {
  const _GoogleCalendarAccount({required this.api, required this.connection});
  final GoogleCalendarApi api;
  final Map<String, dynamic> connection;

  @override
  State<_GoogleCalendarAccount> createState() => _GoogleCalendarAccountState();
}

class _GoogleCalendarAccountState extends State<_GoogleCalendarAccount> {
  String get _id => widget.connection['connection_id'] as String;
  List<Map<String, dynamic>> _calendars = [];
  List<Map<String, dynamic>> _events = [];
  String? _selected;
  late String? _writeId = widget.connection['write_calendar_id'] as String?;
  String? _error;
  bool _busy = false;
  bool _loadedEvents = false;
  late DateTime _day;

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    _day = DateTime(now.year, now.month, now.day);
    _loadCalendars();
  }

  Future<void> _perform(Future<void> Function() operation) async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await operation();
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } catch (_) {
      if (mounted)
        setState(() => _error = 'Chưa tải được dữ liệu lịch. Hãy thử lại.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _loadCalendars() => _perform(_readCalendars);

  Future<void> _readCalendars() async {
    final items = await widget.api.calendars(_id);
    if (!mounted) return;
    setState(() {
      _calendars = items;
      _writeId = items
          .where((item) => item['selected_for_write'] == true)
          .firstOrNull?['id'] as String?;
      if (!items.any((item) => item['id'] == _selected)) {
        _selected = items.firstOrNull?['id'] as String?;
      }
    });
    await _readDay();
  }

  Future<void> _readDay() async {
    final calendarId = _selected;
    if (calendarId == null) return;
    final end = DateTime(_day.year, _day.month, _day.day + 1);
    final events = await widget.api.events(_id, calendarId, _day, end);
    if (mounted)
      setState(() {
        _events = events;
        _loadedEvents = true;
      });
  }

  Future<void> _pickDay() async {
    final day = await showDatePicker(
        context: context,
        initialDate: _day,
        firstDate: DateTime(2000),
        lastDate: DateTime(2100));
    if (mounted && day != null) _showDay(day: day);
  }

  Future<void> _showDay({String? calendarId, DateTime? day}) {
    setState(() {
      _selected = calendarId ?? _selected;
      _day = day ?? _day;
      _loadedEvents = false;
      _events = [];
    });
    return _perform(_readDay);
  }

  Future<void> _chooseWriteCalendar() async {
    final choice = await showModalBottomSheet<(String?, String)>(
      context: context,
      showDragHandle: true,
      builder: (sheet) => SafeArea(
          child: ListView(
        shrinkWrap: true,
        children: [
          const ListTile(title: Text('Chọn lịch ghi phiên')),
          for (final item in _calendars)
            if (item['access_role'] == 'owner' ||
                item['access_role'] == 'writer')
              ListTile(
                title: Text(item['summary'] as String? ?? 'Lịch Google'),
                onTap: () => Navigator.pop(sheet, (
                  item['id'] as String,
                  item['summary'] as String? ?? 'Lịch Google',
                )),
              ),
          ListTile(
            leading: const Icon(Icons.add),
            title: const Text('Lịch riêng ProPlan VA Focus'),
            onTap: () => Navigator.pop(sheet, (null, 'ProPlan VA Focus')),
          ),
        ],
      )),
    );
    if (!mounted || choice == null || choice.$1 == _writeId && _writeId != null)
      return;
    await _perform(() async {
      final readIds = [
        for (final id
            in (widget.connection['read_calendar_ids'] as List? ?? []))
          if (id is String) id,
      ];
      final preview = await widget.api.previewWriteCalendar(
        _id,
        readIds,
        choice.$1,
        createCalendar: choice.$1 == null,
      );
      if (!mounted) return;
      final apply = await showModalBottomSheet<bool>(
        context: context,
        builder: (sheet) => SafeArea(
            child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text('Ghi phiên vào ${choice.$2}',
                  style: Theme.of(sheet).textTheme.titleLarge),
              const SizedBox(height: 8),
              const Text('Các sự kiện đã đồng bộ sẽ ở lại lịch cũ.'),
              const SizedBox(height: 16),
              PrimaryCta(
                  label: 'Xác nhận lịch ghi',
                  onPressed: () => Navigator.pop(sheet, true)),
              TextButton(
                  onPressed: () => Navigator.pop(sheet),
                  child: const Text('Quay lại')),
            ],
          ),
        )),
      );
      if (apply != true || !mounted) return;
      await widget.api.confirmPreview(preview);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
        content: Text('Đã cập nhật lịch ghi phiên.'),
      ));
      await _readCalendars();
    });
  }

  String _when(BuildContext context, Object? value) {
    final time = value is String ? DateTime.tryParse(value)?.toLocal() : null;
    if (time == null) return '—';
    final labels = MaterialLocalizations.of(context);
    return '${labels.formatShortDate(time)} ${labels.formatTimeOfDay(TimeOfDay.fromDateTime(time), alwaysUse24HourFormat: true)}';
  }

  @override
  Widget build(BuildContext context) {
    final write =
        _calendars.where((item) => item['id'] == _writeId).firstOrNull;
    return MelbelPageBackdrop(
      showTopCloud: false,
      showBottomClouds: true,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: const MelbelAppBar(
          title: 'Lịch đã liên kết',
          showBack: true,
          showTitleRays: true,
        ),
        body: SafeArea(
          child: ListView(
            padding: const EdgeInsets.all(20),
            children: [
              if (_busy)
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(999),
                    child: const LinearProgressIndicator(minHeight: 6),
                  ),
                ),
              if (_error != null) ...[
                MelbelCard(
                  borderRadius: 22,
                  tone: MelbelCardTone.amber,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(_error!),
                      const SizedBox(height: 6),
                      TextButton(
                        onPressed: _busy ? null : _loadCalendars,
                        child: const Text('Thử lại'),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 12),
              ],
              if (_calendars.isNotEmpty) ...[
                const MelbelSectionBanner(
                  title: 'Sự kiện theo ngày',
                  art: MelbelSectionArt.sprout,
                ),
                const SizedBox(height: 12),
                MelbelCard(
                  borderRadius: 22,
                  padding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 6,
                  ),
                  child: DropdownButton<String>(
                    isExpanded: true,
                    underline: const SizedBox.shrink(),
                    value: _selected,
                    items: [
                      for (final calendar in _calendars)
                        DropdownMenuItem(
                          value: calendar['id'] as String,
                          child: Text(
                            calendar['summary'] as String? ?? 'Lịch Google',
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                    ],
                    onChanged:
                        _busy ? null : (value) => _showDay(calendarId: value),
                  ),
                ),
                const SizedBox(height: 8),
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton.icon(
                    icon: const Icon(Icons.today_outlined),
                    label: Text(
                      MaterialLocalizations.of(context).formatFullDate(_day),
                    ),
                    onPressed: _busy ? null : _pickDay,
                  ),
                ),
                if (_loadedEvents && _events.isEmpty && _error == null)
                  const MelbelCard(
                    borderRadius: 22,
                    child: Text('Không có sự kiện trong ngày đã chọn.'),
                  ),
                for (final event in _events)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: MelbelCard(
                      borderRadius: 22,
                      padding: EdgeInsets.zero,
                      child: ListTile(
                        title: Text(
                          event['title'] as String? ?? 'Sự kiện không tiêu đề',
                          style: Theme.of(context)
                              .textTheme
                              .titleSmall
                              ?.copyWith(fontWeight: FontWeight.w700),
                        ),
                        subtitle: Text(
                          '${_when(context, event['start_at'])} – ${_when(context, event['end_at'])}',
                        ),
                      ),
                    ),
                  ),
              ],
              const SizedBox(height: 16),
              MelbelCard(
                borderRadius: 22,
                tone: MelbelCardTone.sage,
                child: Text(
                  _writeId == null
                      ? 'Chưa chọn lịch ghi phiên'
                      : 'Lịch ghi phiên: ${write?['summary'] ?? _writeId}',
                ),
              ),
              const SizedBox(height: 12),
              SecondaryCta(
                label: 'Chọn lịch ghi',
                onPressed: _busy ? null : _chooseWriteCalendar,
              ),
              const SizedBox(height: 8),
              SecondaryCta(
                label: 'Đồng bộ ngay',
                icon: Icons.sync_rounded,
                onPressed: _busy
                    ? null
                    : () {
                        final messenger = ScaffoldMessenger.of(context);
                        unawaited(_perform(() async {
                          await widget.api.refreshConnection(_id);
                          await _readDay();
                          messenger.showSnackBar(
                            const SnackBar(
                              content: Text('Đã đồng bộ lịch Google.'),
                              behavior: SnackBarBehavior.floating,
                            ),
                          );
                        }));
                      },
              ),
              const SizedBox(height: 6),
              TextButton(
                onPressed: _busy
                    ? null
                    : () => _perform(() async {
                          await widget.api.disconnect(_id);
                          if (mounted) Navigator.pop(context, true);
                        }),
                child: const Text('Ngắt kết nối'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
