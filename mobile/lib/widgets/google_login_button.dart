import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/auth_service.dart';
import 'google_web_button_stub.dart'
    if (dart.library.js_interop) 'google_web_button.dart';

class GoogleLoginButton extends StatelessWidget {
  const GoogleLoginButton({super.key});

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthService>();
    if (!auth.configured) {
      return const Text('Bản build này cần cấu hình Google Client ID.');
    }
    if (auth.busy) return const LinearProgressIndicator();
    if (!kIsWeb) {
      return OutlinedButton(
        onPressed: auth.signIn,
        child: const Text('Tiếp tục với Google'),
      );
    }
    return FutureBuilder<void>(
      future: auth.initializeGoogle(),
      builder: (context, snapshot) {
        if (snapshot.hasError) {
          return const Text('Chưa tải được nút Google. Hãy tải lại trang.');
        }
        if (snapshot.connectionState != ConnectionState.done) {
          return const LinearProgressIndicator();
        }
        return Center(child: googleWebButton());
      },
    );
  }
}
