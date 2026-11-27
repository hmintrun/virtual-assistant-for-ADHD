import 'package:flutter/material.dart';

/// W3C COGA 5-second Undo SnackBar replacing cognitive-heavy confirm dialogs.
void showMelbelUndoSnackBar(
  BuildContext context, {
  required String message,
  required VoidCallback onUndo,
  String undoLabel = 'Hoàn tác',
  Duration duration = const Duration(seconds: 5),
}) {
  final messenger = ScaffoldMessenger.maybeOf(context);
  if (messenger == null) return;
  messenger
    ..clearSnackBars()
    ..showSnackBar(
      SnackBar(
        duration: duration,
        showCloseIcon: true,
        content: Text(message),
        action: SnackBarAction(
          label: undoLabel,
          onPressed: onUndo,
        ),
      ),
    );
}
