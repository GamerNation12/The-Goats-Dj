"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { UserPlus, Check, X, MessageSquare, Trash2, Radio, Sparkles } from "lucide-react";
import toast from "react-hot-toast";
import { tasteMatch, tasteLabel } from "@/lib/taste";

function ownUsernameFromToken(): string | null {
  try {
    const token = localStorage.getItem("discord_jwt");
    if (!token) return null;
    const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const u = JSON.parse(atob(part));
    const name = u?.name as string | undefined;
    if (!name) return null;
    return name === "gamernation12" ? "GamerNation12" : name;
  } catch {
    return null;
  }
}

interface LiveEntry {
  track: string;
  artist: string;
  image?: string;
  live: boolean;
  when?: string;
}

export default function FriendsPage() {
  const router = useRouter();
  const [friends, setFriends] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [targetUsername, setTargetUsername] = useState("");
  const [live, setLive] = useState<Record<string, LiveEntry>>({});
  const [matchFriend, setMatchFriend] = useState<any | null>(null);
  const [matchData, setMatchData] = useState<{ score: number; shared: any[]; label: string } | null>(null);
  const [matchLoading, setMatchLoading] = useState(false);
  const [matchError, setMatchError] = useState<string | null>(null);

  const fetchFriends = async () => {
    const token = localStorage.getItem("discord_jwt");
    if (!token) {
      window.location.href = "/api/auth/login";
      return;
    }
    try {
      const res = await fetch("/api/friends", {
        headers: { Authorization: `Bearer ${token}` }
      });
      const data = await res.json();
      if (data.friends) {
        setFriends(data.friends);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchFriends();
  }, []);

  // Live feed: what accepted friends are playing right now.
  useEffect(() => {
    const accepted = friends.filter(f => f.status === 'accepted');
    if (accepted.length === 0) return;
    const token = localStorage.getItem("discord_jwt");
    let cancelled = false;

    const fetchLive = async () => {
      const results = await Promise.allSettled(
        accepted.map(async (f) => {
          const res = await fetch(`/api/u/${encodeURIComponent(f.friend_username)}?t=${Date.now()}`, {
            headers: { Authorization: `Bearer ${token}` }
          });
          if (!res.ok) throw new Error("unavailable");
          const data = await res.json();
          const t = data?.stats?.recentTracks?.[0];
          if (!t) throw new Error("no tracks");
          return { id: f.friend_id, entry: { track: t.name, artist: t.artist, image: t.image, live: !!t.nowPlaying, when: t.date } as LiveEntry };
        })
      );
      if (cancelled) return;
      const next: Record<string, LiveEntry> = {};
      for (const r of results) {
        if (r.status === "fulfilled") next[r.value.id] = r.value.entry;
      }
      setLive(next);
    };

    fetchLive();
    const id = setInterval(fetchLive, 30000);
    return () => { cancelled = true; clearInterval(id); };
  }, [friends]);

  const openMatch = async (f: any) => {
    setMatchFriend(f);
    setMatchData(null);
    setMatchError(null);
    setMatchLoading(true);
    try {
      const me = ownUsernameFromToken();
      if (!me) throw new Error("Sign in again to compare taste.");
      const token = localStorage.getItem("discord_jwt");
      const headers = { Authorization: `Bearer ${token}` };
      const [mineRes, theirsRes] = await Promise.all([
        fetch(`/api/u/${encodeURIComponent(me)}?period=1month&t=${Date.now()}`, { headers }),
        fetch(`/api/u/${encodeURIComponent(f.friend_username)}?period=1month&t=${Date.now()}`, { headers }),
      ]);
      if (!mineRes.ok) throw new Error("Could not load your stats.");
      if (!theirsRes.ok) throw new Error(`${f.display_name || f.friend_username} has no public stats.`);
      const mine = await mineRes.json();
      const theirs = await theirsRes.json();
      const r = tasteMatch(mine?.stats?.topArtists || [], theirs?.stats?.topArtists || []);
      setMatchData({ score: r.score, shared: r.shared, label: tasteLabel(r.score) });
    } catch (e: any) {
      setMatchError(e.message || "Could not compare taste.");
    } finally {
      setMatchLoading(false);
    }
  };

  const handleAction = async (action: string, targetId?: string, targetUsernameStr?: string) => {
    const token = localStorage.getItem("discord_jwt");
    try {
      const res = await fetch("/api/friends", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`
        },
        body: JSON.stringify({ action, targetId, targetUsername: targetUsernameStr })
      });
      const data = await res.json();
      if (data.success) {
        toast.success(`Successfully ${action === 'request' ? 'sent request' : action + 'ed'}`);
        setTargetUsername("");
        fetchFriends();
      } else {
        console.error(data);
        toast.error(data.details || data.error || "An error occurred");
      }
    } catch (err) {
      toast.error("Failed to perform action");
    }
  };

  const pendingIncoming = friends.filter(f => f.status === 'pending' && f.direction === 'incoming');
  const pendingOutgoing = friends.filter(f => f.status === 'pending' && f.direction === 'outgoing');
  const acceptedFriends = friends.filter(f => f.status === 'accepted');

  if (loading) {
    return (
      <div className="min-h-screen bg-[#09090b] flex items-center justify-center pt-20">
        <div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#09090b] text-white pt-24 px-4 sm:px-6 lg:px-8 pb-10">
      <div className="max-w-4xl mx-auto space-y-8 animate-fade-in-up">
        
        {/* Add Friend Section */}
        <div className="bg-zinc-900/50 backdrop-blur-xl border border-white/5 rounded-2xl p-6 shadow-xl">
          <h2 className="text-xl font-semibold mb-4 flex items-center gap-2">
            <UserPlus className="w-5 h-5 text-indigo-400" />
            Add a Friend
          </h2>
          <div className="flex flex-col sm:flex-row gap-4">
            <input
              type="text"
              value={targetUsername}
              onChange={(e) => setTargetUsername(e.target.value)}
              placeholder="Discord Username"
              className="flex-1 bg-black/50 border border-white/10 rounded-xl px-4 py-3 focus:outline-none focus:border-indigo-500 transition-colors"
            />
            <button
              onClick={() => handleAction("request", undefined, targetUsername)}
              disabled={!targetUsername}
              className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 px-6 py-3 rounded-xl font-medium transition-colors shadow-lg shadow-indigo-500/20"
            >
              Send Request
            </button>
          </div>
        </div>

        {/* Live Now — what friends are playing */}
        {acceptedFriends.length > 0 && (
          <div className="bg-zinc-900/50 backdrop-blur-xl border border-white/5 rounded-2xl p-6 shadow-xl">
            <h2 className="text-xl font-semibold mb-4 flex items-center gap-2">
              <Radio className="w-5 h-5 text-green-400" />
              Live Now
            </h2>
            <div className="space-y-3">
              {acceptedFriends.map(f => {
                const entry = live[f.friend_id];
                return (
                  <div key={f.friend_id} className="flex items-center gap-4 bg-black/30 p-3 rounded-xl border border-white/5">
                    <div className="w-11 h-11 rounded-lg bg-zinc-800 overflow-hidden shrink-0">
                      {entry?.image ? <img src={entry.image} alt="" className="w-full h-full object-cover" /> : <div className="w-full h-full flex items-center justify-center">🎵</div>}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="font-medium truncate">{entry ? entry.track : (f.display_name || f.friend_username)}</p>
                      <p className="text-sm text-zinc-500 truncate">
                        {entry ? `${entry.artist} · ${f.display_name || f.friend_username}` : "Loading…"}
                      </p>
                    </div>
                    {entry?.live ? (
                      <span className="shrink-0 flex items-center gap-1.5 bg-green-500/10 text-green-400 px-2.5 py-1 rounded-full text-[10px] uppercase font-bold tracking-wider border border-green-500/20">
                        <span className="w-1.5 h-1.5 bg-green-500 rounded-full animate-pulse" /> Live
                      </span>
                    ) : entry?.when ? (
                      <span className="shrink-0 text-xs text-zinc-500">
                        {new Date(parseInt(entry.when) * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}
                      </span>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Pending Requests */}
        {(pendingIncoming.length > 0 || pendingOutgoing.length > 0) && (
          <div className="bg-zinc-900/50 backdrop-blur-xl border border-white/5 rounded-2xl p-6 shadow-xl">
            <h2 className="text-xl font-semibold mb-4">Pending Requests</h2>
            <div className="space-y-4">
              {pendingIncoming.map(f => (
                <div key={f.friend_id} className="flex flex-col sm:flex-row sm:items-center justify-between bg-black/30 p-4 rounded-xl border border-white/5 gap-4">
                  <div>
                    <p className="font-medium text-lg">{f.display_name || f.friend_username}</p>
                    <p className="text-sm text-zinc-400">wants to be your friend</p>
                  </div>
                  <div className="flex gap-2">
                    <button onClick={() => handleAction("accept", f.friend_id)} className="flex-1 sm:flex-none flex items-center justify-center gap-2 px-4 py-2 bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 rounded-lg transition-colors">
                      <Check className="w-5 h-5" /> Accept
                    </button>
                    <button onClick={() => handleAction("reject", f.friend_id)} className="flex-1 sm:flex-none flex items-center justify-center gap-2 px-4 py-2 bg-red-500/20 text-red-400 hover:bg-red-500/30 rounded-lg transition-colors">
                      <X className="w-5 h-5" /> Decline
                    </button>
                  </div>
                </div>
              ))}
              {pendingOutgoing.map(f => (
                <div key={f.friend_id} className="flex items-center justify-between bg-black/30 p-4 rounded-xl border border-white/5">
                  <div>
                    <p className="font-medium">{f.display_name || f.friend_username}</p>
                    <p className="text-sm text-zinc-400">Request sent</p>
                  </div>
                  <button onClick={() => handleAction("remove", f.friend_id)} className="px-4 py-2 bg-red-500/10 text-red-400 hover:bg-red-500/20 rounded-lg text-sm transition-colors">
                    Cancel
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Friends List */}
        <div className="bg-zinc-900/50 backdrop-blur-xl border border-white/5 rounded-2xl p-6 shadow-xl">
          <h2 className="text-xl font-semibold mb-4">Your Friends</h2>
          {acceptedFriends.length === 0 ? (
            <div className="text-center py-12 bg-black/20 rounded-xl border border-dashed border-white/10">
              <UserPlus className="w-12 h-12 text-zinc-600 mx-auto mb-3" />
              <p className="text-zinc-400">No friends yet. Start adding some!</p>
            </div>
          ) : (
            <div className="grid gap-3">
              {acceptedFriends.map(f => (
                <div key={f.friend_id} className="flex items-center justify-between bg-black/30 p-4 rounded-xl border border-white/5 hover:border-white/10 transition-colors group">
                  <div className="flex items-center gap-4">
                    <div className="w-12 h-12 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white font-bold text-lg shadow-lg">
                      {(f.display_name || f.friend_username).charAt(0).toUpperCase()}
                    </div>
                    <div>
                      <p className="font-medium text-lg group-hover:text-indigo-300 transition-colors">{f.display_name || f.friend_username}</p>
                      <p className="text-sm text-zinc-500">@{f.friend_username}</p>
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <button onClick={() => openMatch(f)} className="p-2.5 bg-indigo-500/10 text-indigo-300 hover:bg-indigo-500/20 hover:scale-105 rounded-xl transition-all" title="Compare taste">
                      <Sparkles className="w-5 h-5" />
                    </button>
                    <button onClick={() => {
                      if(confirm("Are you sure you want to remove this friend?")) {
                        handleAction("remove", f.friend_id);
                      }
                    }} className="p-2.5 bg-red-500/10 text-red-400 hover:bg-red-500/20 hover:scale-105 rounded-xl transition-all" title="Remove Friend">
                      <Trash2 className="w-5 h-5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Taste Match Modal */}
        {matchFriend && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm" onClick={() => setMatchFriend(null)}>
            <div className="bg-zinc-900 border border-white/10 rounded-3xl max-w-lg w-full p-8 shadow-2xl animate-fade-in-up max-h-[85vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between mb-6">
                <h3 className="text-2xl font-black flex items-center gap-2">
                  <Sparkles className="w-6 h-6 text-indigo-400" />
                  You × {matchFriend.display_name || matchFriend.friend_username}
                </h3>
                <button onClick={() => setMatchFriend(null)} className="p-2 hover:bg-white/10 rounded-xl text-zinc-400 hover:text-white transition-colors">
                  <X className="w-5 h-5" />
                </button>
              </div>
              {matchLoading ? (
                <div className="flex flex-col items-center py-10">
                  <div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin mb-4"></div>
                  <p className="text-zinc-400 text-sm">Comparing top artists…</p>
                </div>
              ) : matchError ? (
                <div className="bg-red-500/10 border border-red-500/20 text-red-300 p-4 rounded-xl text-sm">{matchError}</div>
              ) : matchData ? (
                <>
                  <div className="text-center mb-6 bg-indigo-500/10 border border-indigo-500/20 rounded-2xl p-6">
                    <div className="text-5xl font-black text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 to-purple-400">{matchData.score}%</div>
                    <div className="text-indigo-300 font-bold mt-1">{matchData.label}</div>
                    <div className="text-xs text-zinc-500 mt-2">based on top artists · last month</div>
                  </div>
                  {matchData.shared.length > 0 ? (
                    <div className="space-y-2">
                      {matchData.shared.map((a: any) => (
                        <div key={a.name} className="flex items-center justify-between bg-black/30 p-3 rounded-xl border border-white/5">
                          <span className="font-bold truncate flex-1">{a.name}</span>
                          <span className="text-xs text-zinc-400 shrink-0 ml-3">
                            {Number(a.mine).toLocaleString()} · {Number(a.theirs).toLocaleString()} plays
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-center text-zinc-500">No shared top artists yet.</p>
                  )}
                </>
              ) : null}
            </div>
          </div>
        )}

      </div>
    </div>
  );
}
