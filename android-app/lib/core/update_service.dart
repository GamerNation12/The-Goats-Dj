import 'dart:io';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:package_info_plus/package_info_plus.dart';
import 'package:path_provider/path_provider.dart';
import 'config.dart';

/// One-tap in-app updater: checks the release pubspec for a newer build,
/// downloads the APK with progress, then hands it to the system installer
/// (no browser / manual APK hunting).
class UpdateService {
  static const _channel = MethodChannel('djscratch/update');

  /// Remote build number from the release pubspec, or null when unreachable.
  static Future<int?> remoteBuildNumber() async {
    try {
      final res = await http.get(Uri.parse(AppConfig.updatePubspecUrl));
      if (res.statusCode != 200) return null;
      final m = RegExp(r'version:\s*\d+\.\d+\.\d+\+(\d+)').firstMatch(res.body);
      return m == null ? null : int.parse(m.group(1)!);
    } catch (_) {
      return null;
    }
  }

  static Future<bool> isUpdateAvailable() async {
    final remote = await remoteBuildNumber();
    if (remote == null) return false;
    final info = await PackageInfo.fromPlatform();
    final local = int.tryParse(info.buildNumber) ?? 0;
    return remote > local;
  }

  /// Downloads the release APK into the app cache, reporting 0.0–1.0 progress.
  static Future<File> downloadApk({void Function(double progress)? onProgress}) async {
    final client = http.Client();
    try {
      final req = http.Request('GET', Uri.parse(AppConfig.apkUrl));
      final res = await client.send(req);
      if (res.statusCode != 200) {
        throw Exception('Download failed (${res.statusCode})');
      }
      final total = res.contentLength ?? 0;
      final dir = await getTemporaryDirectory();
      final updates = Directory('${dir.path}/updates');
      if (!await updates.exists()) await updates.create(recursive: true);
      final file = File('${updates.path}/DJ-Scratch.apk');
      final sink = file.openWrite();
      var received = 0;
      await for (final chunk in res.stream) {
        received += chunk.length;
        sink.add(chunk);
        if (total > 0) onProgress?.call(received / total);
      }
      await sink.close();
      onProgress?.call(1.0);
      return file;
    } finally {
      client.close();
    }
  }

  /// Opens the Android package installer for [apk]. Returns true if launched.
  static Future<bool> installApk(File apk) async {
    try {
      final ok = await _channel.invokeMethod<bool>('installApk', {'path': apk.path});
      return ok == true;
    } on MissingPluginException {
      // Non-Android / dev builds: channel unavailable.
      return false;
    } catch (_) {
      return false;
    }
  }
}
