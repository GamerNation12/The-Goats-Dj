import { useEffect, useState } from 'react';
import { toast } from 'react-hot-toast';
import { api } from '../lib/api';
import { canonicalUsername, decodeUser } from '../lib/auth';
import { tasteLabel, tasteMatch } from '../lib/taste';
import type { Friend, RecentTrack } from '../lib/types';
import { Card, Empty, Spinner } from '../components/ui';

interface LiveEntry {
  track: string;
  artist: string;
  image?: string;
  live: boolean;
  when?: string | null;
}

export default function FriendsPage({ token }: { token: string | null }) {
  const [friends, setFriends] = useState<Friend[]>([]);
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [live, setLive] = useState<Record<string, LiveEntry>>({});
  const [matchFriend, setMatchFriend] = useState<Friend | null>(null);
  const [matchData, setMatchData] = useState<{ score: number; shared: { name: string; mine: number; theirs: number }[]; label: string } | null>(null);
  const [matchLoading, setMatchLoading] = useState(false);
  const [matchError, setMatchError] = useState('');

  const load = async () => {
    if (!token) return;
    setLoading(true);
    try {
      const data = await api.getFriends(token);
      setFriends((data.friends || []) as Friend[]);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load friends');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Live feed: what accepted friends are playing (refreshes every 30s).
  useEffect(() => {
    const acceptedList = friends.filter((f) => f.status === 'accepted');
    if (!token || acceptedList.length === 0) return;
    let cancelled = false;
    const fetchLive = async () => {
      const results = await Promise.allSettled(
        acceptedList.map(async (f) => {
          const data = await api.getProfile(f.friend_username, token);
          const t = ((data.stats || {}) as { recentTracks?: RecentTrack[] }).recentTracks?.[0];
          if (!t) throw new Error('no tracks');
          return { id: f.friend_id, entry: { track: t.name, artist: t.artist, image: t.image, live: !!t.nowPlaying, when: t.date } as LiveEntry };
        })
      );
      if (cancelled) return;
      const next: Record<string, LiveEntry> = {};
      for (const r of results) {
        if (r.status === 'fulfilled') next[r.value.id] = r.value.entry;
      }
      setLive(next);
    };
    fetchLive();
    const id = setInterval(fetchLive, 30000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [friends, token]);

  const openMatch = async (f: Friend) => {
    if (!token) return;
    setMatchFriend(f);
    setMatchData(null);
    setMatchError('');
    setMatchLoading(true);
    try {
      const me = decodeUser(token);
      if (!me) throw new Error('Sign in again to compare taste.');
      const mineRes = await api.getProfile(canonicalUsername(me.name), token, '1month');
      const theirsRes = await api.getProfile(f.friend_username, token, '1month');
      const r = tasteMatch(
        ((mineRes.stats || {}) as { topArtists?: { name: string; playcount?: number | string }[] }).topArtists || [],
        ((theirsRes.stats || {}) as { topArtists?: { name: string; playcount?: number | string }[] }).topArtists || []
      );
      setMatchData({ score: r.score, shared: r.shared, label: tasteLabel(r.score) });
    } catch (e) {
      setMatchError(e instanceof Error ? e.message : 'Could not compare taste.');
    } finally {
      setMatchLoading(false);
    }
  };

  const act = async (action: string, targetId?: string, targetUsername?: string) => {
    if (!token) return;
    setBusy(true);
    try {
      const res = await api.friendAction(token, { action, targetId, targetUsername });
      if (res.success) {
        toast.success('Done');
        setName('');
        load();
      } else toast.error(res.error || 'Failed');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed');
    } finally {
      setBusy(false);
    }
  };

  const incoming = friends.filter((f) => f.status === 'pending' && f.direction === 'incoming');
  const outgoing = friends.filter((f) => f.status === 'pending' && f.direction === 'outgoing');
  const accepted = friends.filter((f) => f.status === 'accepted');

  if (loading) return <Spinner label="Loading friends…" />;

  return (
    <div className="max-w-4xl mx-auto space-y-6 pb-28 animate-fade-in">
      <h1 className="text-4xl font-black tracking-tight">Friends</h1>
      <Card className="p-6">
        <h2 className="font-bold mb-3">Add a friend</h2>
        <div className="flex gap-3">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Discord username"
            className="flex-1 bg-black/50 border border-white/10 rounded-xl px-4 py-3 focus:outline-none focus:border-indigo-500"
          />
          <button disabled={!name || busy} onClick={() => act('request', undefined, name)} className="px-6 py-3 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 font-bold">
            Send
          </button>
        </div>
      </Card>

      {(incoming.length > 0 || outgoing.length > 0) && (
        <Card className="p-6">
          <h2 className="font-bold mb-3">Requests ({incoming.length + outgoing.length})</h2>
          <div className="space-y-2">
            {incoming.map((f) => (
              <div key={f.friend_id} className="flex items-center justify-between bg-black/30 p-3 rounded-2xl border border-white/5">
                <span className="font-bold">{f.display_name || f.friend_username}</span>
                <div className="flex gap-2">
                  <button onClick={() => act('accept', f.friend_id)} className="px-4 py-2 rounded-xl bg-emerald-500/20 text-emerald-300 font-bold">Accept</button>
                  <button onClick={() => act('reject', f.friend_id)} className="px-4 py-2 rounded-xl bg-red-500/20 text-red-300 font-bold">Decline</button>
                </div>
              </div>
            ))}
            {outgoing.map((f) => (
              <div key={f.friend_id} className="flex items-center justify-between bg-black/30 p-3 rounded-2xl border border-white/5">
                <span className="font-bold">{f.display_name || f.friend_username} <span className="text-zinc-500 text-sm">· sent</span></span>
                <button onClick={() => act('remove', f.friend_id)} className="px-4 py-2 rounded-xl bg-white/5 font-bold">Cancel</button>
              </div>
            ))}
          </div>
        </Card>
      )}

      {accepted.length > 0 && (
        <Card className="p-6">
          <h2 className="font-bold mb-3">Live now</h2>
          <div className="space-y-2">
            {accepted.map((f) => {
              const entry = live[f.friend_id];
              return (
                <div key={f.friend_id} className="flex items-center gap-3 bg-black/30 p-3 rounded-2xl border border-white/5">
                  <div className="w-11 h-11 rounded-xl bg-zinc-800 overflow-hidden shrink-0">
                    {entry?.image ? <img src={entry.image} alt="" className="w-full h-full object-cover" /> : <div className="w-full h-full flex items-center justify-center">🎵</div>}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-bold truncate">{entry ? entry.track : (f.display_name || f.friend_username)}</div>
                    <div className="text-xs text-zinc-500 truncate">{entry ? `${entry.artist} · ${f.display_name || f.friend_username}` : 'Loading…'}</div>
                  </div>
                  {entry?.live ? (
                    <span className="shrink-0 text-[10px] uppercase font-black tracking-widest bg-green-500/15 text-green-300 border border-green-500/30 px-2.5 py-1 rounded-full">Live</span>
                  ) : entry?.when ? (
                    <span className="shrink-0 text-xs text-zinc-500">
                      {new Date(Number(entry.when) * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}
                    </span>
                  ) : null}
                </div>
              );
            })}
          </div>
        </Card>
      )}

      <Card className="p-6">
        <h2 className="font-bold mb-3">Your friends ({accepted.length})</h2>
        {accepted.length === 0 ? (
          <Empty title="No friends yet" hint="Send a request above to get started." />
        ) : (
          <div className="space-y-2">
            {accepted.map((f) => (
              <div key={f.friend_id} className="flex items-center justify-between bg-black/30 p-3 rounded-2xl border border-white/5">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center font-black">
                    {(f.display_name || f.friend_username).charAt(0).toUpperCase()}
                  </div>
                  <div>
                    <div className="font-bold">{f.display_name || f.friend_username}</div>
                    <div className="text-xs text-zinc-500">@{f.friend_username}</div>
                  </div>
                </div>
                <div className="flex gap-2">
                  <button onClick={() => openMatch(f)} className="px-4 py-2 rounded-xl bg-indigo-500/15 text-indigo-200 border border-indigo-500/30 font-bold hover:bg-indigo-500/25">Match</button>
                  <button onClick={() => act('remove', f.friend_id)} className="px-4 py-2 rounded-xl bg-red-500/10 text-red-300 font-bold">Remove</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {matchFriend && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70" onClick={() => setMatchFriend(null)}>
          <div className="bg-zinc-900 border border-white/10 rounded-3xl max-w-lg w-full p-8 max-h-[85vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-5">
              <h3 className="text-xl font-black">You × {matchFriend.display_name || matchFriend.friend_username}</h3>
              <button onClick={() => setMatchFriend(null)} className="p-2 hover:bg-white/10 rounded-xl text-zinc-400">✕</button>
            </div>
            {matchLoading ? (
              <Spinner label="Comparing top artists…" />
            ) : matchError ? (
              <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/30 text-red-200 text-sm">{matchError}</div>
            ) : matchData ? (
              <>
                <div className="text-center mb-5 bg-indigo-500/10 border border-indigo-500/20 rounded-2xl p-5">
                  <div className="text-4xl font-black text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 to-purple-400">{matchData.score}%</div>
                  <div className="text-indigo-200 font-bold text-sm mt-1">{matchData.label} · last month</div>
                </div>
                <div className="space-y-2">
                  {matchData.shared.map((a) => (
                    <div key={a.name} className="flex items-center justify-between bg-black/30 p-3 rounded-xl border border-white/5">
                      <span className="font-bold truncate flex-1">{a.name}</span>
                      <span className="text-xs text-zinc-500 shrink-0 ml-3">{a.mine.toLocaleString()} · {a.theirs.toLocaleString()} plays</span>
                    </div>
                  ))}
                  {matchData.shared.length === 0 && <Empty title="No shared top artists yet" />}
                </div>
              </>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}
