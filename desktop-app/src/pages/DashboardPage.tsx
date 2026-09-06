import { useEffect, useState } from 'react';
import { PERIODS, type Period } from '../lib/config';
import { api } from '../lib/api';
import type { UserStats } from '../lib/types';
import { Card, Empty, ErrorBox, SectionTitle, Spinner } from '../components/ui';

export default function DashboardPage({ token, username }: { token: string | null; username: string }) {
  const [stats, setStats] = useState<UserStats | null>(null);
  const [period, setPeriod] = useState<Period>('overall');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = async () => {
    if (!token) return;
    setLoading(true);
    setError('');
    try {
      const data = await api.getProfile(username, token, period);
      setStats((data.stats || {}) as UserStats);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period]);

  const recents = (stats?.recentTracks || []).filter(
    (t) => !query || `${t.name} ${t.artist}`.toLowerCase().includes(query.toLowerCase())
  );

  const insights = (() => {
    const all = stats?.recentTracks || [];
    const now = Date.now();
    const plays24h = all.filter((t) => {
      const uts = Number(t.date || 0);
      return !t.nowPlaying && uts > 0 && now - uts * 1000 < 24 * 60 * 60 * 1000;
    }).length;
    const uniqueArtists = new Set(all.map((t) => (t.artist || '').toLowerCase()).filter(Boolean)).size;
    const topArtist = stats?.topArtists?.[0] || null;
    const total = Number(stats?.playcount || 0);
    const share = topArtist && total > 0 ? Math.min(100, (Number(topArtist.playcount || 0) / total) * 100) : 0;
    const next = total < 10 ? 10 : Math.pow(10, Math.ceil(Math.log10(total + 1)));
    return { plays24h, uniqueArtists, topArtist, total, share, next, pct: Math.min(100, (total / next) * 100) };
  })();

  return (
    <div className="animate-fade-in max-w-5xl mx-auto pb-28">
      <div className="flex flex-wrap items-end justify-between gap-4 mb-8">
        <h1 className="text-4xl font-black tracking-tight">
          Welcome back, <span className="text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 to-purple-400">{username}</span>
        </h1>
        <div className="flex gap-2 items-center">
          <select
            value={period}
            onChange={(e) => setPeriod(e.target.value as Period)}
            className="bg-zinc-900 border border-white/10 rounded-xl px-3 py-2 text-sm font-semibold"
            title="Top stats period"
          >
            {PERIODS.map((p) => (
              <option key={p.value} value={p.value}>{p.label}</option>
            ))}
          </select>
          <button onClick={load} className="px-4 py-2 rounded-xl bg-white/5 border border-white/10 hover:bg-white/10 text-sm font-bold">
            Refresh
          </button>
        </div>
      </div>

      {loading && <Spinner label="Loading your stats…" />}
      {error && <ErrorBox message={error} onRetry={load} />}

      {!loading && !error && stats && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mb-10">
            <Card className="p-7">
              <div className="text-zinc-400 text-xs font-bold uppercase tracking-widest mb-2">Total scrobbles</div>
              <div className="text-4xl font-black">{Number(stats.playcount || 0).toLocaleString()}</div>
            </Card>
            <Card className="p-7">
              <div className="text-zinc-400 text-xs font-bold uppercase tracking-widest mb-2">Top artist ({period})</div>
              <div className="text-2xl font-bold truncate">{stats.topArtists?.[0]?.name || '—'}</div>
            </Card>
            <Card className="p-7">
              <div className="text-zinc-400 text-xs font-bold uppercase tracking-widest mb-2">Top track ({period})</div>
              <div className="text-2xl font-bold truncate">{stats.topTracks?.[0]?.name || '—'}</div>
            </Card>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-10">
            <Card className="p-5">
              <div className="text-zinc-400 text-[11px] font-bold uppercase tracking-widest mb-1">Last 24 hours</div>
              <div className="text-3xl font-black">{insights.plays24h}</div>
              <div className="text-xs text-zinc-500 mt-1">plays scrobbled</div>
            </Card>
            <Card className="p-5">
              <div className="text-zinc-400 text-[11px] font-bold uppercase tracking-widest mb-1">In rotation</div>
              <div className="text-3xl font-black">{insights.uniqueArtists}</div>
              <div className="text-xs text-zinc-500 mt-1">artists in recents</div>
            </Card>
            <Card className="p-5">
              <div className="text-zinc-400 text-[11px] font-bold uppercase tracking-widest mb-1">Top artist share</div>
              <div className="text-3xl font-black text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 to-purple-400">
                {insights.share.toFixed(1)}%
              </div>
              <div className="text-xs text-zinc-500 mt-1 truncate">{insights.topArtist?.name || '—'}</div>
            </Card>
            <Card className="p-5">
              <div className="text-zinc-400 text-[11px] font-bold uppercase tracking-widest mb-1">
                To {insights.next.toLocaleString()}
              </div>
              <div className="text-3xl font-black">{(insights.next - insights.total).toLocaleString()}</div>
              <div className="text-xs text-zinc-500 mt-1 mb-2">plays to go</div>
              <div className="h-1.5 w-full bg-white/5 rounded-full overflow-hidden">
                <div className="h-full bg-gradient-to-r from-indigo-500 to-purple-500 rounded-full" style={{ width: `${insights.pct}%` }} />
              </div>
            </Card>
          </div>

          <div className="flex items-center justify-between mb-5 gap-4">
            <div className="flex-1"><SectionTitle>Recent tracks</SectionTitle></div>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter recents…"
              className="bg-black/40 border border-white/10 rounded-xl px-4 py-2 text-sm w-56 focus:outline-none focus:border-indigo-500"
            />
          </div>

          <Card className="overflow-hidden">
            {recents.length === 0 ? (
              <div className="p-6"><Empty title="No tracks found" hint="Try a different filter or scrobble something." /></div>
            ) : (
              recents.slice(0, 15).map((t, i) => (
                <div key={i} className="flex items-center gap-4 p-4 hover:bg-white/[0.04] border-b border-white/5 last:border-0">
                  <div className="w-12 h-12 rounded-xl bg-zinc-800 overflow-hidden shrink-0">
                    {t.image ? <img src={t.image} className="w-full h-full object-cover" alt="" /> : <div className="w-full h-full flex items-center justify-center">🎵</div>}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-bold truncate flex items-center gap-2">
                      {t.name}
                      {t.nowPlaying && <span className="text-[10px] uppercase font-black tracking-widest bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 px-2 py-0.5 rounded-full">Playing</span>}
                    </div>
                    <div className="text-sm text-zinc-400 truncate">{t.artist}</div>
                  </div>
                </div>
              ))
            )}
          </Card>
        </>
      )}
    </div>
  );
}
