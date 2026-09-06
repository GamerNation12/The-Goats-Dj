import { useCallback, useEffect, useState } from 'react';
import { Home, Trophy, Users, MessageSquare, Shield, Settings } from 'lucide-react';
import { Toaster } from 'react-hot-toast';
import logoUrl from './assets/logo.png';
import { POLL_MS } from './lib/config';
import { api } from './lib/api';
import { canonicalUsername, clearToken, decodeUser, discordAvatar, getToken } from './lib/auth';
import type { JwtUser, RecentTrack, SpotifyNowPlaying, LeaderboardEntry, UserStats } from './lib/types';
import LoginScreen from './components/LoginScreen';
import UpdateBanner from './components/UpdateBanner';
import PlayerBar from './components/PlayerBar';
import DashboardPage from './pages/DashboardPage';
import LeaderboardPage from './pages/LeaderboardPage';
import FriendsPage from './pages/FriendsPage';
import MessagesPage from './pages/MessagesPage';
import AdminPage from './pages/AdminPage';
import SettingsPage from './pages/SettingsPage';

type Tab = 'dashboard' | 'leaderboard' | 'friends' | 'messages' | 'admin' | 'settings';

export default function App() {
  const [token, setToken] = useState<string | null>(() => getToken());
  const [user, setUser] = useState<JwtUser | null>(null);
  const [tab, setTab] = useState<Tab>('dashboard');
  const [isAdmin, setIsAdmin] = useState(false);
  const [online, setOnline] = useState(true);
  const [np, setNp] = useState<SpotifyNowPlaying | null>(null);
  const [lastfmTrack, setLastfmTrack] = useState<RecentTrack | null>(null);

  // Auth gate: listen for token delivered by Electron auth server
  useEffect(() => {
    const onStorage = () => {
      const t = getToken();
      setToken(t);
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  useEffect(() => {
    if (!token) {
      setUser(null);
      return;
    }
    const u = decodeUser(token);
    if (!u) {
      clearToken();
      setToken(null);
      return;
    }
    setUser(u);
  }, [token]);

  const refreshLight = useCallback(async () => {
    if (!token || !user) return;
    if (localStorage.getItem('ds_polling') === 'off') return;
    try {
      const username = canonicalUsername(user.name);
      const [profile, sp] = await Promise.allSettled([
        api.getProfile(username, token),
        api.spotifyNowPlaying(token),
      ]);
      if (profile.status === 'fulfilled') {
        const stats = (profile.value.stats || {}) as UserStats;
        setLastfmTrack(stats.recentTracks?.[0] || null);
        setOnline(true);
        const label = stats.recentTracks?.[0]?.nowPlaying
          ? `${stats.recentTracks[0].name} — ${stats.recentTracks[0].artist}`
          : '';
        (window as unknown as { djscratch?: { reportNowPlaying: (s: string) => void } }).djscratch?.reportNowPlaying(
          localStorage.getItem('ds_rpc') === 'off' ? '' : label
        );
      }
      if (sp.status === 'fulfilled' && !(sp.value as { error?: string }).error) {
        setNp(sp.value as SpotifyNowPlaying);
      }
      if (profile.status === 'fulfilled') setOnline(true);
    } catch {
      setOnline(false);
    }
  }, [token, user]);

  useEffect(() => {
    if (!token || !user) return;
    api
      .checkAdmin(token)
      .then((r) => setIsAdmin(r.role === 'admin' || r.role === 'owner'))
      .catch(() => setIsAdmin(false));
    refreshLight();
    const id = setInterval(refreshLight, POLL_MS);
    return () => clearInterval(id);
  }, [token, user, refreshLight]);

  const logout = () => {
    clearToken();
    setToken(null);
    setUser(null);
  };

  if (!token || !user) return <LoginScreen />;

  const username = canonicalUsername(user.name);
  const nav: { id: Tab; label: string; icon: React.ReactNode; hidden?: boolean }[] = [
    { id: 'dashboard', label: 'Dashboard', icon: <Home size={18} /> },
    { id: 'leaderboard', label: 'Leaderboard', icon: <Trophy size={18} /> },
    { id: 'friends', label: 'Friends', icon: <Users size={18} /> },
    { id: 'messages', label: 'Messages', icon: <MessageSquare size={18} /> },
    { id: 'admin', label: 'Admin', icon: <Shield size={18} />, hidden: !isAdmin },
    { id: 'settings', label: 'Settings', icon: <Settings size={18} /> },
  ];

  return (
    <div className="relative flex h-screen bg-[#09090b] text-white overflow-hidden">
      <Toaster position="bottom-center" toastOptions={{ style: { background: '#18181b', color: '#fff' } }} />
      <div className="absolute top-0 left-0 right-[140px] h-10 z-[100] app-region-drag pointer-events-none" />
      {lastfmTrack?.image && (
        <div
          className="absolute inset-0 z-0 opacity-30 blur-[120px] scale-125 pointer-events-none transition-all duration-1000"
          style={{ backgroundImage: `url(${lastfmTrack.image})`, backgroundSize: 'cover', backgroundPosition: 'center' }}
        />
      )}
      <div className="absolute inset-0 bg-gradient-to-b from-black/20 via-transparent to-black/80 z-0 pointer-events-none" />

      <aside className="w-64 border-r border-white/10 bg-zinc-950/60 backdrop-blur-2xl p-4 flex flex-col pt-12 relative z-10">
        <div className="flex items-center gap-3 mb-8 px-2">
          <img src={logoUrl} alt="" className="w-10 h-10 rounded-xl object-cover" />
          <div>
            <div className="font-black text-lg leading-none">DJ Scratch</div>
            <div className="text-[11px] text-zinc-500 mt-1 flex items-center gap-1.5">
              <span className={`w-1.5 h-1.5 rounded-full ${online ? 'bg-emerald-400' : 'bg-red-400'}`} />
              {online ? 'Connected' : 'Offline'}
            </div>
          </div>
        </div>
        <nav className="flex-1 space-y-1.5">
          {nav
            .filter((n) => !n.hidden)
            .map((n) => (
              <button
                key={n.id}
                onClick={() => setTab(n.id)}
                className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl font-semibold transition-all ${
                  tab === n.id ? 'bg-indigo-500/20 text-indigo-200 border border-indigo-500/30' : 'text-zinc-400 hover:text-white hover:bg-white/5'
                }`}
              >
                {n.icon}
                {n.label}
              </button>
            ))}
        </nav>
        <div className="mt-auto border-t border-white/10 pt-4 px-2 flex items-center gap-3">
          {discordAvatar(user) && <img src={discordAvatar(user)} className="w-10 h-10 rounded-full" alt="" />}
          <div className="text-sm font-bold truncate flex-1">{username}</div>
          <button onClick={logout} className="text-zinc-500 hover:text-red-300 p-2" title="Logout">⎋</button>
        </div>
      </aside>

      <div className="flex-1 flex flex-col relative z-10 pt-10">
        <UpdateBanner />
        <main className="flex-1 overflow-y-auto p-8">
          {tab === 'dashboard' && <DashboardPage token={token} username={username} />}
          {tab === 'leaderboard' && <LeaderboardPage token={token} />}
          {tab === 'friends' && <FriendsPage token={token} />}
          {tab === 'messages' && <MessagesPage token={token} user={user} />}
          {tab === 'admin' && <AdminPage token={token} />}
          {tab === 'settings' && <SettingsPage token={token} user={user} onLogout={logout} />}
        </main>
        <PlayerBar token={token} lastfmTrack={lastfmTrack} spotify={np} refresh={refreshLight} />
      </div>
    </div>
  );
}

export type { LeaderboardEntry };
