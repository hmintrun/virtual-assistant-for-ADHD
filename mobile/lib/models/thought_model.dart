/// Trạng thái đồng bộ của Thought trong cơ sở dữ liệu SQLite cục bộ.
class LocalThoughtState {
  static const String rawSaved = 'raw_saved';
  static const String synced = 'synced';
  static const String tombstone = 'tombstone';
}

/// Mô hình một ý nghĩ / bản ghi âm thô (UF4) lưu trữ cục bộ trong SQLite (`local_thoughts`).
class LocalThought {
  const LocalThought({
    required this.id,
    required this.ownerId,
    this.title,
    required this.audioPath,
    this.sourceRunId,
    required this.state,
    required this.createdAt,
    required this.updatedAt,
  });

  final String id;
  final String ownerId;
  final String? title;
  final String audioPath;
  final String? sourceRunId;
  final String state;
  final DateTime createdAt;
  final DateTime updatedAt;

  bool get isTombstone => state == LocalThoughtState.tombstone;
  bool get isSynced => state == LocalThoughtState.synced;
  bool get isRawSaved => state == LocalThoughtState.rawSaved;

  LocalThought copyWith({
    String? id,
    String? ownerId,
    String? title,
    String? audioPath,
    String? sourceRunId,
    String? state,
    DateTime? createdAt,
    DateTime? updatedAt,
  }) {
    return LocalThought(
      id: id ?? this.id,
      ownerId: ownerId ?? this.ownerId,
      title: title ?? this.title,
      audioPath: audioPath ?? this.audioPath,
      sourceRunId: sourceRunId ?? this.sourceRunId,
      state: state ?? this.state,
      createdAt: createdAt ?? this.createdAt,
      updatedAt: updatedAt ?? this.updatedAt,
    );
  }

  Map<String, dynamic> toMap() => {
        'id': id,
        'owner_id': ownerId,
        'title': title,
        'audio_path': audioPath,
        'source_run_id': sourceRunId,
        'state': state,
        'created_at': createdAt.toUtc().toIso8601String(),
        'updated_at': updatedAt.toUtc().toIso8601String(),
      };

  factory LocalThought.fromMap(Map<String, dynamic> map) => LocalThought(
        id: map['id'] as String,
        ownerId: map['owner_id'] as String,
        title: map['title'] as String?,
        audioPath: map['audio_path'] as String? ?? '',
        sourceRunId: map['source_run_id'] as String?,
        state: map['state'] as String,
        createdAt: DateTime.parse(map['created_at'] as String).toUtc(),
        updatedAt: DateTime.parse(map['updated_at'] as String).toUtc(),
      );

  @override
  String toString() =>
      'LocalThought(id: $id, ownerId: $ownerId, state: $state, audioPath: $audioPath)';
}
