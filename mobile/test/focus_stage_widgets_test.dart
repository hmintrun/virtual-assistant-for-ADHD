import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:proplan_va/widgets/countdown_timer_widget.dart';
import 'package:proplan_va/widgets/focus_stage_widgets.dart';

void main() {
  testWidgets(
      'focus countdown stays readable without live ticking announcements',
      (tester) async {
    for (final time in ['24:59', '24:58']) {
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
        body: MelbelFocusTimerRing(
            timeText: time,
            progress: 0.9,
            subtitle: 'Đang tập trung',
            isOffline: true),
      )));
      final node = tester
          .getSemantics(find.bySemanticsLabel('Đang tập trung · Ngoại tuyến'));
      expect(
          node, containsSemantics(value: 'Còn lại $time', isLiveRegion: false));
    }
  });

  testWidgets(
      'releasing capture while the microphone starts still saves the audio',
      (tester) async {
    final started = Completer<void>();
    final calls = <String>[];
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: FocusHoldToRecord(
      isRecording: false,
      onStart: () {
        calls.add('start');
        return started.future;
      },
      onStop: () async {
        calls.add('save');
      },
      onCancel: () async {
        calls.add('cancel');
      },
    ))));
    final gesture = await tester
        .startGesture(tester.getCenter(find.byIcon(Icons.mic_none_rounded)));
    await tester.pump(const Duration(milliseconds: 600));
    await gesture.up();
    expect(calls, ['start']);
    started.complete();
    await tester.pump();
    expect(calls, ['start', 'save']);
  });

  testWidgets('keyboard capture can start and save without a held gesture',
      (tester) async {
    var recording = false;
    final calls = <String>[];
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: StatefulBuilder(
      builder: (context, setState) => FocusHoldToRecord(
        isRecording: recording,
        onStart: () async {
          calls.add('start');
          setState(() => recording = true);
        },
        onStop: () async {
          calls.add('save');
          setState(() => recording = false);
        },
        onCancel: () async {
          calls.add('cancel');
        },
      ),
    ))));
    await tester.sendKeyEvent(LogicalKeyboardKey.tab);
    await tester.pump();
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pumpAndSettle();
    expect(calls, ['start']);
    await tester.sendKeyEvent(LogicalKeyboardKey.space);
    await tester.pumpAndSettle();
    expect(calls, ['start', 'save']);
  });
}
