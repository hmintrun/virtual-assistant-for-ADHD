# Local compatibility patch

Based on flutter_dnd 0.1.4+1, with the upstream MIT license retained.

- Reuse the host Android Gradle Plugin instead of declaring AGP 3.5.0.
- Remove the obsolete jcenter repository declarations.
- Declare the Android namespace in Gradle and remove the manifest package attribute.
- Compile with Android API 36; use minimum Android API 21.
- Remove the obsolete Flutter v1 registration API and use getBinaryMessenger.
- Allow Dart 3 in the package SDK constraint.
- ProPlan VA (07/11/2026, kai): add `setAppNotificationPolicy` (Android 15+ only) so the
  implicit DND rule created by the app can let calls and alarms through. No-op below
  Android 15 to avoid overwriting the user's global DND policy.

The Dart API and DND logic are retained. Full app compilation and device behavior
must be checked in the consuming project.

Extract the ZIP into the mobile directory so this package is at
mobile/packages/flutter_dnd. Replace the flutter_dnd dependency in the app's
pubspec.yaml with:

```yaml
  flutter_dnd:
    path: packages/flutter_dnd
```

Then run flutter pub get and rebuild the app.
