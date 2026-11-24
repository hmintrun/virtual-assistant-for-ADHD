class ApiSession {
  ApiSession(this.ownerId, {this.token});

  static ApiSession? current;

  final String ownerId;
  String? token;
  bool active = true;
  Future<bool> Function()? renew;
  void Function()? onUnauthorized;
}
