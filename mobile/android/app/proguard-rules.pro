# Keep Flutter engine, plugin registry, and embedding entry points during R8 shrinking.
-keep class io.flutter.app.** { *; }
-keep class io.flutter.plugin.** { *; }
-keep class io.flutter.util.** { *; }
-keep class io.flutter.view.** { *; }
-keep class io.flutter.** { *; }
-keep class io.flutter.plugins.** { *; }
-dontwarn io.flutter.embedding.**
-dontwarn javax.annotation.**

# Keep Gson generic signatures and flutter_local_notifications models for scheduled alarms across reboot.
-keepattributes Signature
-keepattributes EnclosingMethod
-keepattributes InnerClasses
-keep class com.dexterous.** { *; }
-keep class com.google.gson.reflect.TypeToken { *; }
-keep class * extends com.google.gson.reflect.TypeToken
-keep public class * implements java.lang.reflect.Type

# Keep background alarm and DND plugin entry points.
-keep class dev.fluttercommunity.plus.androidalarmmanager.** { *; }
-keep class com.github.sathish76.flutter_dnd.** { *; }
