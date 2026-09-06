import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import '../../core/api_client.dart';
import '../../core/auth_store.dart';
import '../../widgets/update_flow.dart';
import '../dashboard/dashboard_tab.dart';
import '../player/player_tab.dart';
import '../leaderboard/leaderboard_tab.dart';
import '../friends/friends_tab.dart';
import '../messages/messages_tab.dart';
import '../admin/admin_tab.dart';
import '../settings/settings_tab.dart';

class MainScreen extends StatefulWidget {
  const MainScreen({super.key});
  @override
  State<MainScreen> createState() => _MainScreenState();
}

class _MainScreenState extends State<MainScreen> {
  int _index = 0;
  String? _role;
  bool _ready = false;

  @override
  void initState() {
    super.initState();
    _boot();
  }

  Future<void> _boot() async {
    _checkAdmin();
    _checkUpdate();
    if (mounted) setState(() => _ready = true);
  }

  Future<void> _checkAdmin() async {
    try {
      final token = await AuthStore.readToken();
      if (token == null) return;
      final data = await ApiClient(token).getJson('/api/admin/check');
      if (mounted) setState(() => _role = data['role']?.toString());
    } catch (_) {}
  }

  Future<void> _checkUpdate() async {
    // Silent launch check — only prompts when an update is actually available.
    await runUpdateFlow(context, silentWhenCurrent: true);
  }

  @override
  Widget build(BuildContext context) {
    if (!_ready) {
      return const Scaffold(backgroundColor: Color(0xFF030712), body: Center(child: CircularProgressIndicator(color: Color(0xFF0AB5CD))));
    }
    final admin = _role == 'admin' || _role == 'owner';
    final pages = [
      const DashboardTab(),
      const PlayerTab(),
      const LeaderboardTab(),
      const FriendsTab(),
      const MessagesTab(),
      if (admin) const AdminTab(),
      const SettingsTab(),
    ];
    final dests = [
      const NavigationDestination(icon: Icon(LucideIcons.layoutDashboard, color: Colors.white54), selectedIcon: Icon(LucideIcons.layoutDashboard, color: Color(0xFF0AB5CD)), label: 'Home'),
      const NavigationDestination(icon: Icon(LucideIcons.music, color: Colors.white54), selectedIcon: Icon(LucideIcons.music, color: Color(0xFF22C55E)), label: 'Player'),
      const NavigationDestination(icon: Icon(LucideIcons.trophy, color: Colors.white54), selectedIcon: Icon(LucideIcons.trophy, color: Color(0xFF0AB5CD)), label: 'Ranks'),
      const NavigationDestination(icon: Icon(LucideIcons.users, color: Colors.white54), selectedIcon: Icon(LucideIcons.users, color: Color(0xFF0AB5CD)), label: 'Friends'),
      const NavigationDestination(icon: Icon(LucideIcons.messageSquare, color: Colors.white54), selectedIcon: Icon(LucideIcons.messageSquare, color: Color(0xFF0AB5CD)), label: 'Chat'),
      if (admin) const NavigationDestination(icon: Icon(LucideIcons.shield, color: Colors.white54), selectedIcon: Icon(LucideIcons.shield, color: Colors.redAccent), label: 'Admin'),
      const NavigationDestination(icon: Icon(LucideIcons.settings, color: Colors.white54), selectedIcon: Icon(LucideIcons.settings, color: Color(0xFF0AB5CD)), label: 'Settings'),
    ];
    final safeIndex = _index.clamp(0, pages.length - 1);
    return Scaffold(
      backgroundColor: const Color(0xFF030712),
      body: IndexedStack(index: safeIndex, children: pages),
      bottomNavigationBar: NavigationBar(
        selectedIndex: safeIndex,
        onDestinationSelected: (i) => setState(() => _index = i),
        labelBehavior: NavigationDestinationLabelBehavior.onlyShowSelected,
        backgroundColor: const Color(0xFF030712),
        indicatorColor: const Color(0xFF0AB5CD).withOpacity(0.2),
        destinations: dests,
      ),
    );
  }
}
