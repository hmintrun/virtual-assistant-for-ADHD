import 'package:audioplayers/audioplayers.dart';

/// Lightweight, ADHD-I-friendly sound controller for Melbel's meow cue.
///
/// Uses a single shared [AudioPlayer] with a cooldown throttle so rapid
/// petting strokes never overlap or cause frame drops, and swallows platform
/// or browser autoplay exceptions cleanly (including in `flutter test`).
class MelbelSoundService {
  MelbelSoundService._();

  static final MelbelSoundService instance = MelbelSoundService._();

  static const String _meowAsset = 'audio/melbel_meow.ogg';
  static const Duration _minInterval = Duration(milliseconds: 550);

  AudioPlayer? _player;
  DateTime? _lastPlayedAt;

  /// Plays Melbel's gentle meow sound effect at a calm volume.
  ///
  /// Set [bypassThrottle] to `true` for one-shot events like app launch.
  Future<void> playMeow({
    double volume = 0.58,
    bool bypassThrottle = false,
  }) async {
    final now = DateTime.now();
    if (!bypassThrottle &&
        _lastPlayedAt != null &&
        now.difference(_lastPlayedAt!) < _minInterval) {
      return;
    }
    _lastPlayedAt = now;

    try {
      final player = _player ??= AudioPlayer();
      await player.stop();
      await player.setVolume(volume.clamp(0.0, 1.0));
      await player.play(AssetSource(_meowAsset));
    } catch (_) {
      // Ignore MissingPluginException in widget tests or browser autoplay blocks.
    }
  }
}
