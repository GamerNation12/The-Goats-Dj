import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:url_launcher/url_launcher.dart';
import '../core/config.dart';
import '../core/update_service.dart';

/// Shared one-tap update flow: check → prompt → download with progress →
/// system installer. Used by the launch dialog and the Settings check button.
Future<void> runUpdateFlow(BuildContext context, {bool silentWhenCurrent = false}) async {
  bool available = false;
  try {
    available = await UpdateService.isUpdateAvailable();
  } catch (_) {
    if (!silentWhenCurrent || context.mounted) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Could not check for updates.'), backgroundColor: Colors.redAccent),
        );
      }
    }
    return;
  }
  if (!available) {
    if (!silentWhenCurrent && context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("You're up to date."), backgroundColor: Color(0xFF0AB5CD)),
      );
    }
    return;
  }
  if (!context.mounted) return;
  final go = await showDialog<bool>(
    context: context,
    barrierDismissible: false,
    builder: (_) => AlertDialog(
      backgroundColor: const Color(0xFF0F172A),
      title: Text('Update available', style: GoogleFonts.outfit(fontWeight: FontWeight.bold)),
      content: Text('A new DJ Scratch build is ready. Download and install it now?',
          style: GoogleFonts.inter(color: Colors.white70)),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Later')),
        ElevatedButton(onPressed: () => Navigator.pop(context, true), child: const Text('Update now')),
      ],
    ),
  );
  if (go != true || !context.mounted) return;

  final progress = ValueNotifier<double>(0);
  showDialog(
    context: context,
    barrierDismissible: false,
    builder: (_) => AlertDialog(
      backgroundColor: const Color(0xFF0F172A),
      title: Text('Downloading update', style: GoogleFonts.outfit(fontWeight: FontWeight.bold)),
      content: ValueListenableBuilder<double>(
        valueListenable: progress,
        builder: (_, v, __) => Column(mainAxisSize: MainAxisSize.min, children: [
          LinearProgressIndicator(
              value: v > 0 ? v : null, color: const Color(0xFF0AB5CD), backgroundColor: Colors.white10, minHeight: 8),
          const SizedBox(height: 12),
          Text(v > 0 ? '${(v * 100).toStringAsFixed(0)}%' : 'Starting…',
              style: GoogleFonts.inter(color: Colors.white70)),
        ]),
      ),
    ),
  );
  try {
    final apk = await UpdateService.downloadApk(onProgress: (v) => progress.value = v);
    if (!context.mounted) return;
    Navigator.pop(context);
    final launched = await UpdateService.installApk(apk);
    if (!launched && context.mounted) {
      await launchUrl(Uri.parse(AppConfig.apkUrl), mode: LaunchMode.externalApplication);
    }
  } catch (e) {
    if (!context.mounted) return;
    Navigator.pop(context);
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('Update failed: $e'), backgroundColor: Colors.redAccent),
    );
  } finally {
    progress.dispose();
  }
}
