import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:lucide_icons/lucide_icons.dart';
import '../../core/api_client.dart';
import '../../core/auth_store.dart';
import '../../widgets/states.dart';
import '../../core/config.dart';

class DashboardTab extends StatefulWidget {
  const DashboardTab({super.key});
  @override
  State<DashboardTab> createState() => _DashboardTabState();
}

class _DashboardTabState extends State<DashboardTab> {
  bool _loading = true;
  String _error = '';
  String _period = 'overall';
  String _query = '';
  Map<String, dynamic>? _stats;
  Map<String, dynamic>? _user;

  @override
  void initState() {
    super.initState();
    _load();
    // Silent background refresh keeps recents live without a spinner flash.
    _poll = Timer.periodic(const Duration(seconds: 30), (_) {
      if (mounted) _load(silent: true);
    });
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Timer? _poll;

  Future<void> _load({bool silent = false}) async {
    if (!silent) setState(() { _loading = true; _error = ''; });
    try {
      final token = await AuthStore.readToken();
      _user = token == null ? null : AuthStore.decode(token);
      final name = AuthStore.canonicalName((_user?['name'] ?? '') as String);
      if (name.isEmpty) throw ApiException('Not signed in', 401);
      final api = ApiClient(token);
      final data = await api.getJson('/api/u/${Uri.encodeComponent(name)}?period=$_period');
      if (data['error'] != null) throw ApiException(data['error'].toString(), 400);
      if (!mounted) return;
      setState(() { _stats = (data['stats'] as Map?)?.cast<String, dynamic>(); _loading = false; _error = ''; });
    } on ApiException catch (e) {
      if (!mounted) return;
      // Silent polls never wipe good data with an error screen.
      if (silent) return;
      setState(() { _error = e.message; _loading = false; });
    } catch (_) {
      if (!mounted) return;
      if (silent) return;
      setState(() { _error = 'Connection error.'; _loading = false; });
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Scaffold(backgroundColor: Color(0xFF030712), body: LoadingView());
    if (_error.isNotEmpty) {
      return Scaffold(backgroundColor: const Color(0xFF030712), body: ErrorView(message: _error, onRetry: _load));
    }
    final recents = ((_stats?['recentTracks'] as List?) ?? []).cast<Map>().where((t) {
      if (_query.isEmpty) return true;
      return '${t['name']} ${t['artist']}'.toLowerCase().contains(_query.toLowerCase());
    }).toList();
    final tops = ((_stats?['topArtists'] as List?) ?? []).cast<Map>();

    return Scaffold(
      backgroundColor: const Color(0xFF030712),
      body: SafeArea(
        child: RefreshIndicator(
          onRefresh: _load,
          color: const Color(0xFF0AB5CD),
          child: ListView(padding: const EdgeInsets.all(20), children: [
            Row(children: [
              if ((_user?['image'] ?? _user?['avatar']) != null)
                CircleAvatar(
                  backgroundImage: _user!['image'] != null
                      ? CachedNetworkImageProvider(_user!['image'] as String)
                      : null,
                  radius: 22,
                ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('Welcome back,', style: GoogleFonts.inter(fontSize: 12, color: Colors.white54)),
                  Text(AuthStore.canonicalName((_user?['name'] ?? 'DJ') as String),
                      style: GoogleFonts.outfit(fontSize: 22, fontWeight: FontWeight.w700)),
                ]),
              ),
              DropdownButton<String>(
                value: _period,
                dropdownColor: const Color(0xFF0F172A),
                style: GoogleFonts.inter(color: Colors.white, fontSize: 13),
                items: AppConfig.periods.map((p) => DropdownMenuItem(value: p, child: Text(p))).toList(),
                onChanged: (v) { if (v != null) { setState(() => _period = v); _load(); } },
              ),
              IconButton(
                icon: const Icon(LucideIcons.share2, color: Colors.white54, size: 20),
                tooltip: 'Copy profile link',
                onPressed: () async {
                  final name = AuthStore.canonicalName((_user?['name'] ?? '') as String);
                  if (name.isEmpty) return;
                  await Clipboard.setData(ClipboardData(text: '${AppConfig.apiBase}/${Uri.encodeComponent(name)}'));
                  if (context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('Profile link copied!'), backgroundColor: Color(0xFF0AB5CD)),
                    );
                  }
                },
              ),
            ]),
            const SizedBox(height: 16),
            Row(children: [
              Expanded(
                child: TextField(
                  onChanged: (v) => setState(() => _query = v),
                  style: GoogleFonts.inter(color: Colors.white),
                  decoration: InputDecoration(
                    hintText: 'Filter recents…',
                    hintStyle: GoogleFonts.inter(color: Colors.white38),
                    prefixIcon: const Icon(LucideIcons.search, color: Colors.white38, size: 18),
                    filled: true,
                    fillColor: Colors.white.withOpacity(0.05),
                    border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide.none),
                  ),
                ),
              ),
            ]),
            const SizedBox(height: 20),
            _statHero(),
            const SizedBox(height: 12),
            _insightsRow(),
            const SizedBox(height: 24),
            const SectionHeader(title: 'Recent tracks'),
            const SizedBox(height: 12),
            if (recents.isEmpty) const EmptyView(title: 'No tracks found')
            else ...recents.take(8).map(_recentRow),
            const SizedBox(height: 24),
            const SectionHeader(title: 'Top artists'),
            const SizedBox(height: 12),
            SizedBox(
              height: 210,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                itemCount: tops.length.clamp(0, 10),
                separatorBuilder: (_, __) => const SizedBox(width: 12),
                itemBuilder: (_, i) => _topCard(tops[i]),
              ),
            ),
          ]),
        ),
      ),
    );
  }

  Widget _statHero() {
    final plays = _stats?['playcount'];
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        gradient: LinearGradient(colors: [const Color(0xFF0AB5CD).withOpacity(0.25), Colors.white.withOpacity(0.03)]),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: const Color(0xFF0AB5CD).withOpacity(0.35)),
      ),
      child: Row(children: [
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('TOTAL SCROBBLES', style: GoogleFonts.inter(fontSize: 11, color: Colors.white60, fontWeight: FontWeight.bold)),
          const SizedBox(height: 6),
          Text('${plays ?? '—'}', style: GoogleFonts.outfit(fontSize: 32, fontWeight: FontWeight.w800)),
        ])),
        const Icon(LucideIcons.barChart3, color: Color(0xFF0AB5CD), size: 40),
      ]),
    );
  }

  Widget _insightsRow() {
    final all = ((_stats?['recentTracks'] as List?) ?? []).cast<Map>();
    final nowMs = DateTime.now().millisecondsSinceEpoch;
    final plays24h = all.where((t) {
      final uts = int.tryParse('${t['date'] ?? ''}') ?? 0;
      return t['nowPlaying'] != true && uts > 0 && nowMs - uts * 1000 < 24 * 60 * 60 * 1000;
    }).length;
    final uniqueArtists = all.map((t) => '${t['artist'] ?? ''}'.toLowerCase()).where((s) => s.isNotEmpty).toSet().length;
    final tops = ((_stats?['topArtists'] as List?) ?? []).cast<Map>();
    final total = int.tryParse('${_stats?['playcount'] ?? 0}') ?? 0;
    final topPlays = tops.isEmpty ? 0 : (int.tryParse('${tops.first['playcount'] ?? 0}') ?? 0);
    final share = total > 0 ? (topPlays / total * 100).clamp(0, 100) : 0.0;
    final next = total < 10 ? 10 : _nextPow10(total);

    Widget tile(String label, String value, String sub) {
      return Expanded(
        child: Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: Colors.white.withOpacity(0.04),
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: Colors.white.withOpacity(0.07)),
          ),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(label, style: GoogleFonts.inter(fontSize: 10, color: Colors.white54, fontWeight: FontWeight.bold)),
            const SizedBox(height: 6),
            Text(value, style: GoogleFonts.outfit(fontSize: 22, fontWeight: FontWeight.w800)),
            Text(sub, style: GoogleFonts.inter(fontSize: 11, color: Colors.white54), maxLines: 1, overflow: TextOverflow.ellipsis),
          ]),
        ),
      );
    }

    return Column(children: [
      Row(children: [
        tile('24H PLAYS', '$plays24h', 'scrobbles'),
        const SizedBox(width: 10),
        tile('ROTATION', '$uniqueArtists', 'artists'),
        const SizedBox(width: 10),
        tile('TOP SHARE', '${share.toStringAsFixed(1)}%', tops.isEmpty ? '—' : '${tops.first['name']}'),
      ]),
      const SizedBox(height: 10),
      Container(
        width: double.infinity,
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: Colors.white.withOpacity(0.04),
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: Colors.white.withOpacity(0.07)),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('${(next - total)} PLAYS TO ${(next)}', style: GoogleFonts.inter(fontSize: 10, color: Colors.white54, fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
              value: (total / next).clamp(0.0, 1.0),
              minHeight: 6,
              backgroundColor: Colors.white.withOpacity(0.1),
              valueColor: const AlwaysStoppedAnimation(Color(0xFF0AB5CD)),
            ),
          ),
        ]),
      ),
    ]);
  }

  int _nextPow10(int total) {
    var p = 10;
    while (p <= total) {
      p *= 10;
    }
    return p;
  }

  Widget _recentRow(Map t) {
    final playing = t['nowPlaying'] == true;
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: playing ? const Color(0xFF0AB5CD).withOpacity(0.1) : Colors.white.withOpacity(0.03),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: playing ? const Color(0xFF0AB5CD).withOpacity(0.4) : Colors.white.withOpacity(0.06)),
      ),
      child: Row(children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(10),
          child: t['image'] != null
              ? CachedNetworkImage(imageUrl: t['image'] as String, width: 52, height: 52, fit: BoxFit.cover,
                  errorWidget: (_, __, ___) => Container(width: 52, height: 52, color: Colors.white10, child: const Icon(LucideIcons.music, color: Colors.white54)))
              : Container(width: 52, height: 52, color: Colors.white10, child: const Icon(LucideIcons.music, color: Colors.white54)),
        ),
        const SizedBox(width: 12),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('${t['name'] ?? 'Unknown'}', style: GoogleFonts.inter(fontWeight: FontWeight.bold), maxLines: 1, overflow: TextOverflow.ellipsis),
          Text('${t['artist'] ?? ''}', style: GoogleFonts.inter(color: Colors.white54, fontSize: 13), maxLines: 1, overflow: TextOverflow.ellipsis),
        ])),
        if (playing)
          Container(padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
              decoration: BoxDecoration(color: const Color(0xFF0AB5CD), borderRadius: BorderRadius.circular(20)),
              child: const Text('LIVE', style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold))),
      ]),
    );
  }

  Widget _topCard(Map item) {
    return Container(
      width: 140,
      decoration: BoxDecoration(color: Colors.white.withOpacity(0.04), borderRadius: BorderRadius.circular(16), border: Border.all(color: Colors.white.withOpacity(0.08))),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        ClipRRect(
          borderRadius: const BorderRadius.vertical(top: Radius.circular(16)),
          child: item['image'] != null
              ? CachedNetworkImage(imageUrl: item['image'] as String, height: 120, width: 140, fit: BoxFit.cover,
                  errorWidget: (_, __, ___) => Container(height: 120, color: Colors.white10, child: const Icon(LucideIcons.music, color: Colors.white54)))
              : Container(height: 120, color: Colors.white10, child: const Icon(LucideIcons.music, color: Colors.white54)),
        ),
        Padding(
          padding: const EdgeInsets.all(10),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('${item['name'] ?? ''}', style: GoogleFonts.inter(fontWeight: FontWeight.bold, fontSize: 13), maxLines: 1, overflow: TextOverflow.ellipsis),
            Text('${item['playcount'] ?? ''} plays', style: GoogleFonts.inter(color: const Color(0xFF0AB5CD), fontSize: 12)),
          ]),
        ),
      ]),
    );
  }
}
