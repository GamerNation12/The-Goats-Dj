import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../core/api_client.dart';
import '../../core/auth_store.dart';
import '../../core/config.dart';
import '../../widgets/states.dart';
import '../../widgets/update_flow.dart';
import '../auth/login_screen.dart';

class SettingsTab extends StatefulWidget {
  const SettingsTab({super.key});
  @override
  State<SettingsTab> createState() => _SettingsTabState();
}

class _SettingsTabState extends State<SettingsTab> {
  String _version = '';
  bool _loading = true;
  bool _saving = false;

  final _displayName = TextEditingController();
  final _timezone = TextEditingController();
  String _dataSource = 'combined';
  String _fmMode = 'full';
  bool _privateMode = false;
  bool _showTrackPlaycount = false;
  bool _showFeatures = false;

  bool? _spLinked;
  bool _spBusy = false;

  @override
  void initState() {
    super.initState();
    PackageInfo.fromPlatform().then((p) {
      if (mounted) setState(() => _version = '${p.version}+${p.buildNumber}');
    });
    _load();
  }

  @override
  void dispose() {
    _displayName.dispose();
    _timezone.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final token = await AuthStore.readToken();
      final api = ApiClient(token);
      final s = await api.getJson('/api/settings');
      if (!mounted) return;
      setState(() {
        _displayName.text = '${s['displayName'] ?? ''}';
        _dataSource = '${s['dataSource'] ?? 'combined'}';
        _fmMode = '${s['fmMode'] ?? 'full'}';
        _timezone.text = '${s['timezone'] ?? 'UTC'}';
        _privateMode = s['privateMode'] == true;
        _showTrackPlaycount = s['showTrackPlaycount'] == true;
        _showFeatures = s['showFeatures'] == true;
        _loading = false;
      });
      try {
        final st = await api.getJson('/api/spotify/status');
        if (mounted) setState(() => _spLinked = st['linked'] == true);
      } catch (_) {}
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not load settings: $e'), backgroundColor: Colors.redAccent),
      );
    }
  }

  Future<void> _save() async {
    if (_saving) return;
    setState(() => _saving = true);
    try {
      final token = await AuthStore.readToken();
      await ApiClient(token).postJson('/api/settings', {
        'displayName': _displayName.text,
        'dataSource': _dataSource,
        'fmMode': _fmMode,
        'timezone': _timezone.text.trim().isEmpty ? 'UTC' : _timezone.text.trim(),
        'privateMode': _privateMode,
        'showTrackPlaycount': _showTrackPlaycount,
        'showFeatures': _showFeatures,
      });
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Preferences saved — applies to bot, site and apps.'), backgroundColor: Color(0xFF0AB5CD)),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Save failed: $e'), backgroundColor: Colors.redAccent),
        );
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _toggleSpotify() async {
    if (_spBusy) return;
    setState(() => _spBusy = true);
    try {
      final token = await AuthStore.readToken();
      final api = ApiClient(token);
      if (_spLinked == true) {
        await api.postJson('/api/spotify/disconnect', {});
        if (mounted) setState(() => _spLinked = false);
      } else {
        final me = token == null ? null : AuthStore.decode(token);
        final id = me?['id'];
        final q = id == null ? '' : '?discord_id=$id';
        await launchUrl(Uri.parse('${AppConfig.apiBase}/api/auth/spotify/login$q'), mode: LaunchMode.externalApplication);
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('$e'), backgroundColor: Colors.redAccent),
        );
      }
    } finally {
      if (mounted) setState(() => _spBusy = false);
    }
  }

  Future<void> _logout() async {
    await AuthStore.clear();
    if (mounted) {
      Navigator.of(context).pushAndRemoveUntil(MaterialPageRoute(builder: (_) => const LoginScreen()), (_) => false);
    }
  }

  InputDecoration _field(String hint) => InputDecoration(
        hintText: hint,
        hintStyle: GoogleFonts.inter(color: Colors.white38),
        filled: true,
        fillColor: Colors.white.withOpacity(0.05),
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide.none),
      );

  Widget _toggle(String title, String sub, bool value, void Function(bool) onChanged) {
    return SwitchListTile(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      tileColor: Colors.white.withOpacity(0.04),
      title: Text(title, style: GoogleFonts.inter(fontWeight: FontWeight.bold)),
      subtitle: Text(sub, style: GoogleFonts.inter(color: Colors.white54, fontSize: 12)),
      value: value,
      activeColor: const Color(0xFF22C55E),
      onChanged: onChanged,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF030712),
      appBar: AppBar(backgroundColor: Colors.transparent, elevation: 0, title: Text('Settings', style: GoogleFonts.outfit(fontWeight: FontWeight.w700))),
      body: _loading
          ? const LoadingView(label: 'Loading preferences…')
          : ListView(padding: const EdgeInsets.all(20), children: [
              Text('PROFILE PREFERENCES', style: GoogleFonts.inter(fontSize: 11, color: Colors.white38, fontWeight: FontWeight.bold)),
              const SizedBox(height: 10),
              TextField(controller: _displayName, style: GoogleFonts.inter(color: Colors.white), decoration: _field('Display name (empty = Discord name)')),
              const SizedBox(height: 10),
              DropdownButtonFormField<String>(
                value: _dataSource,
                dropdownColor: const Color(0xFF0F172A),
                style: GoogleFonts.inter(color: Colors.white),
                decoration: _field('Data source'),
                items: const [
                  DropdownMenuItem(value: 'combined', child: Text('Combined')),
                  DropdownMenuItem(value: 'lastfm_only', child: Text('Last.fm only')),
                  DropdownMenuItem(value: 'imported_only', child: Text('Imported only')),
                ],
                onChanged: (v) { if (v != null) setState(() => _dataSource = v); },
              ),
              const SizedBox(height: 10),
              DropdownButtonFormField<String>(
                value: _fmMode,
                dropdownColor: const Color(0xFF0F172A),
                style: GoogleFonts.inter(color: Colors.white),
                decoration: _field('/fm layout'),
                items: const [
                  DropdownMenuItem(value: 'full', child: Text('Full embed')),
                  DropdownMenuItem(value: 'compact', child: Text('Compact')),
                  DropdownMenuItem(value: 'stats', child: Text('Stats')),
                ],
                onChanged: (v) { if (v != null) setState(() => _fmMode = v); },
              ),
              const SizedBox(height: 10),
              TextField(controller: _timezone, style: GoogleFonts.inter(color: Colors.white), decoration: _field('Timezone (e.g. UTC)')),
              const SizedBox(height: 10),
              _toggle('Private profile', 'Hide your stats from others', _privateMode, (v) => setState(() => _privateMode = v)),
              _toggle('Track playcounts', 'Show play counts on track rows', _showTrackPlaycount, (v) => setState(() => _showTrackPlaycount = v)),
              _toggle('Showcase features', 'Feature top stats on your profile', _showFeatures, (v) => setState(() => _showFeatures = v)),
              const SizedBox(height: 12),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: _saving ? null : _save,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF0AB5CD),
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                  ),
                  child: Text(_saving ? 'Saving…' : 'Save preferences', style: GoogleFonts.inter(fontWeight: FontWeight.bold)),
                ),
              ),
              const SizedBox(height: 24),
              Text('SPOTIFY', style: GoogleFonts.inter(fontSize: 11, color: Colors.white38, fontWeight: FontWeight.bold)),
              const SizedBox(height: 10),
              ListTile(
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                tileColor: Colors.white.withOpacity(0.04),
                title: Text(_spLinked == null ? 'Status unknown' : (_spLinked! ? 'Linked ✓' : 'Not linked'),
                    style: GoogleFonts.inter(fontWeight: FontWeight.bold, color: _spLinked == true ? const Color(0xFF22C55E) : Colors.white)),
                trailing: _spBusy
                    ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Color(0xFF0AB5CD)))
                    : TextButton(onPressed: _toggleSpotify, child: Text(_spLinked == true ? 'Disconnect' : 'Link')),
                onTap: _spBusy ? null : _toggleSpotify,
              ),
              const SizedBox(height: 24),
              Text('DJ Scratch v$_version', style: GoogleFonts.inter(color: Colors.white54)),
              const SizedBox(height: 10),
              ListTile(
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                tileColor: Colors.white.withOpacity(0.04),
                title: Text('Check for updates', style: GoogleFonts.inter(fontWeight: FontWeight.bold)),
                subtitle: Text('Downloads and installs in-app when available',
                    style: GoogleFonts.inter(color: Colors.white54, fontSize: 12)),
                trailing: const Icon(Icons.system_update, color: Color(0xFF0AB5CD)),
                onTap: () => runUpdateFlow(context),
              ),
              const SizedBox(height: 10),
              ListTile(
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                tileColor: Colors.white.withOpacity(0.04),
                title: Text('Open website', style: GoogleFonts.inter()),
                trailing: const Icon(Icons.open_in_new, color: Colors.white54),
                onTap: () => launchUrl(Uri.parse(AppConfig.apiBase), mode: LaunchMode.externalApplication),
              ),
              const SizedBox(height: 10),
              ListTile(
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                tileColor: Colors.redAccent.withOpacity(0.1),
                title: Text('Log out', style: GoogleFonts.inter(color: Colors.redAccent, fontWeight: FontWeight.bold)),
                trailing: const Icon(Icons.logout, color: Colors.redAccent),
                onTap: _logout,
              ),
            ]),
    );
  }
}
