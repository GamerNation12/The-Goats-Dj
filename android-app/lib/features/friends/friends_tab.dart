import 'dart:async';
import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:lucide_icons/lucide_icons.dart';
import '../../core/api_client.dart';
import '../../core/auth_store.dart';
import '../../core/taste.dart';
import '../../widgets/states.dart';

class FriendsTab extends StatefulWidget {
  const FriendsTab({super.key});
  @override
  State<FriendsTab> createState() => _FriendsTabState();
}

class _FriendsTabState extends State<FriendsTab> {
  bool _loading = true;
  final _name = TextEditingController();
  List<Map> _friends = [];
  final Map<String, Map<String, dynamic>> _live = {};
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _name.dispose();
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final token = await AuthStore.readToken();
      final data = await ApiClient(token).getJson('/api/friends');
      if (!mounted) return;
      setState(() { _friends = ((data['friends'] as List?) ?? []).cast<Map>(); _loading = false; });
      _startLivePoll();
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e'), backgroundColor: Colors.redAccent));
    }
  }

  void _startLivePoll() {
    _poll?.cancel();
    _fetchLive();
    _poll = Timer.periodic(const Duration(seconds: 30), (_) => _fetchLive());
  }

  Future<void> _fetchLive() async {
    final accepted = _friends.where((f) => f['status'] == 'accepted').toList();
    if (accepted.isEmpty) return;
    try {
      final token = await AuthStore.readToken();
      final api = ApiClient(token);
      final results = await Future.wait(accepted.map((f) async {
        try {
          final data = await api.getJson('/api/u/${Uri.encodeComponent('${f['friend_username']}')}');
          final recents = ((data['stats'] as Map?)?['recentTracks'] as List?) ?? [];
          if (recents.isEmpty) return null;
          final t = (recents.first as Map).cast<String, dynamic>();
          return MapEntry('${f['friend_id']}', {
            'track': '${t['name'] ?? 'Unknown'}',
            'artist': '${t['artist'] ?? ''}',
            'image': t['image'],
            'live': t['nowPlaying'] == true,
          });
        } catch (_) {
          return null;
        }
      }));
      if (!mounted) return;
      setState(() {
        for (final r in results) {
          if (r != null) _live[r.key] = r.value;
        }
      });
    } catch (_) {}
  }

  Future<void> _openMatch(Map f) async {
    final name = '${f['display_name'] ?? f['friend_username']}';
    showDialog(
      context: context,
      builder: (_) => const Center(child: CircularProgressIndicator(color: Color(0xFF0AB5CD))),
      barrierDismissible: false,
    );
    try {
      final token = await AuthStore.readToken();
      final me = token == null ? null : AuthStore.decode(token);
      final myName = AuthStore.canonicalName('${me?['name'] ?? ''}');
      if (myName.isEmpty) throw ApiException('Sign in again to compare.', 401);
      final api = ApiClient(token);
      final mine = await api.getJson('/api/u/${Uri.encodeComponent(myName)}?period=1month');
      final theirs = await api.getJson('/api/u/${Uri.encodeComponent('${f['friend_username']}')}?period=1month');
      final r = tasteMatch(
        ((mine['stats'] as Map?)?['topArtists'] as List?) ?? [],
        ((theirs['stats'] as Map?)?['topArtists'] as List?) ?? [],
      );
      if (!mounted) return;
      Navigator.pop(context);
      showDialog(
        context: context,
        builder: (_) => AlertDialog(
          backgroundColor: const Color(0xFF0F172A),
          title: Text('You × $name', style: GoogleFonts.outfit(fontWeight: FontWeight.bold)),
          content: SizedBox(
            width: double.maxFinite,
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              Text('${r.score}%', style: GoogleFonts.outfit(fontSize: 44, fontWeight: FontWeight.w800, color: const Color(0xFF0AB5CD))),
              Text(tasteLabel(r.score), style: GoogleFonts.inter(color: Colors.white70)),
              const SizedBox(height: 4),
              Text('top artists · last month', style: GoogleFonts.inter(fontSize: 11, color: Colors.white38)),
              const SizedBox(height: 12),
              if (r.shared.isEmpty) Text('No shared top artists yet.', style: GoogleFonts.inter(color: Colors.white54))
              else Flexible(
                child: ListView.builder(
                  shrinkWrap: true,
                  itemCount: r.shared.length,
                  itemBuilder: (_, i) {
                    final a = r.shared[i];
                    return Padding(
                      padding: const EdgeInsets.only(bottom: 6),
                      child: Row(children: [
                        Expanded(child: Text(a.name, style: GoogleFonts.inter(fontWeight: FontWeight.bold))),
                        Text('${a.mine} · ${a.theirs}', style: GoogleFonts.inter(fontSize: 12, color: Colors.white54)),
                      ]),
                    );
                  },
                ),
              ),
            ]),
          ),
          actions: [TextButton(onPressed: () => Navigator.pop(context), child: const Text('Close'))],
        ),
      );
    } catch (e) {
      if (!mounted) return;
      Navigator.pop(context);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e'), backgroundColor: Colors.redAccent));
    }
  }

  Future<void> _act(String action, {String? targetId, String? targetUsername}) async {
    try {
      final token = await AuthStore.readToken();
      await ApiClient(token).postJson('/api/friends', {
        'action': action,
        if (targetId != null) 'targetId': targetId,
        if (targetUsername != null) 'targetUsername': targetUsername,
      });
      _name.clear();
      await _load();
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e'), backgroundColor: Colors.redAccent));
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Scaffold(backgroundColor: Color(0xFF030712), body: LoadingView(label: 'Loading friends…'));
    final incoming = _friends.where((f) => f['status'] == 'pending' && f['direction'] == 'incoming').toList();
    final accepted = _friends.where((f) => f['status'] == 'accepted').toList();
    return Scaffold(
      backgroundColor: const Color(0xFF030712),
      appBar: AppBar(backgroundColor: Colors.transparent, elevation: 0, title: Text('Friends', style: GoogleFonts.outfit(fontWeight: FontWeight.w700))),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(padding: const EdgeInsets.all(20), children: [
          Row(children: [
            Expanded(
              child: TextField(controller: _name, style: GoogleFonts.inter(color: Colors.white),
                  decoration: InputDecoration(hintText: 'Discord username', hintStyle: GoogleFonts.inter(color: Colors.white38),
                      filled: true, fillColor: Colors.white.withOpacity(0.05),
                      border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide.none))),
            ),
            const SizedBox(width: 10),
            ElevatedButton(
              onPressed: () => _act('request', targetUsername: _name.text.trim()),
              style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF0AB5CD), foregroundColor: Colors.white, shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14))),
              child: const Icon(LucideIcons.userPlus),
            ),
          ]),
          if (incoming.isNotEmpty) ...[
            const SizedBox(height: 20),
            Text('Requests (${incoming.length})', style: GoogleFonts.outfit(fontWeight: FontWeight.bold)),
            const SizedBox(height: 10),
            ...incoming.map((f) => _row(
                  '${f['display_name'] ?? f['friend_username']}',
                  actions: [
                    TextButton(onPressed: () => _act('accept', targetId: '${f['friend_id']}'), child: const Text('Accept')),
                    TextButton(onPressed: () => _act('reject', targetId: '${f['friend_id']}'), child: const Text('Decline', style: TextStyle(color: Colors.redAccent))),
                  ],
                )),
          ],
          const SizedBox(height: 20),
          Text('Live now', style: GoogleFonts.outfit(fontWeight: FontWeight.bold)),
          const SizedBox(height: 10),
          if (accepted.isEmpty) const EmptyView(title: 'No friends yet', hint: 'Send a request above.')
          else ...accepted.map((f) {
            final entry = _live['${f['friend_id']}'];
            return Container(
              margin: const EdgeInsets.only(bottom: 10),
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(color: Colors.white.withOpacity(0.04), borderRadius: BorderRadius.circular(16), border: Border.all(color: Colors.white.withOpacity(0.06))),
              child: Row(children: [
                const Icon(LucideIcons.radio, color: Color(0xFF22C55E), size: 20),
                const SizedBox(width: 12),
                Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(entry != null ? '${entry['track']}' : '${f['display_name'] ?? f['friend_username']}', style: GoogleFonts.inter(fontWeight: FontWeight.bold), maxLines: 1, overflow: TextOverflow.ellipsis),
                  Text(entry != null ? '${entry['artist']}' : 'Loading…', style: GoogleFonts.inter(color: Colors.white54, fontSize: 12), maxLines: 1, overflow: TextOverflow.ellipsis),
                ])),
                if (entry != null && entry['live'] == true)
                  Container(padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                      decoration: BoxDecoration(color: const Color(0xFF22C55E), borderRadius: BorderRadius.circular(20)),
                      child: const Text('LIVE', style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold))),
              ]),
            );
          }),
          const SizedBox(height: 20),
          Text('Your friends (${accepted.length})', style: GoogleFonts.outfit(fontWeight: FontWeight.bold)),
          const SizedBox(height: 10),
          if (accepted.isEmpty) const EmptyView(title: 'No friends yet', hint: 'Send a request above.')
          else ...accepted.map((f) => _row(
                '${f['display_name'] ?? f['friend_username']}',
                sub: '@${f['friend_username']}',
                actions: [
                  IconButton(icon: const Icon(LucideIcons.sparkles, color: Color(0xFF0AB5CD), size: 18), tooltip: 'Taste match', onPressed: () => _openMatch(f)),
                  IconButton(icon: const Icon(LucideIcons.trash2, color: Colors.redAccent, size: 18), onPressed: () => _act('remove', targetId: '${f['friend_id']}')),
                ],
              )),
        ]),
      ),
    );
  }

  Widget _row(String title, {String? sub, List<Widget> actions = const []}) {
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(color: Colors.white.withOpacity(0.04), borderRadius: BorderRadius.circular(16), border: Border.all(color: Colors.white.withOpacity(0.06))),
      child: Row(children: [
        CircleAvatar(backgroundColor: const Color(0xFF0AB5CD).withOpacity(0.2), child: Text(title.isEmpty ? '?' : title[0].toUpperCase())),
        const SizedBox(width: 12),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title, style: GoogleFonts.inter(fontWeight: FontWeight.bold)),
          if (sub != null) Text(sub, style: GoogleFonts.inter(color: Colors.white54, fontSize: 12)),
        ])),
        ...actions,
      ]),
    );
  }
}
