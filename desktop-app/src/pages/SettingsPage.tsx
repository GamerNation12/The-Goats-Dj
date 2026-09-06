import { useEffect, useState } from 'react';
import { toast } from 'react-hot-toast';
import { API_BASE, APP_VERSION } from '../lib/config';
import { api } from '../lib/api';
import { manualCheck } from '../lib/updater';
import type { JwtUser } from '../lib/types';
import { Card, Spinner } from '../components/ui';

function Toggle({ value, onChange, title, hint }: { value: boolean; onChange: (v: boolean) => void; title: string; hint: string }) {
  return (
    <label className="flex items-center justify-between p-4 bg-black/20 rounded-2xl border border-white/5 cursor-pointer">
      <div>
        <div className="font-bold text-sm">{title}</div>
        <div className="text-zinc-400 text-xs mt-1">{hint}</div>
      </div>
      <input type="checkbox" className="hidden" checked={value} onChange={(e) => onChange(e.target.checked)} />
      <div className={`w-12 h-6 rounded-full p-1 transition-colors ${value ? 'bg-emerald-500' : 'bg-zinc-700'}`}>
        <div className={`w-4 h-4 bg-white rounded-full transition-transform ${value ? 'translate-x-6' : ''}`} />
      </div>
    </label>
  );
}

const inputCls = 'w-full bg-black/40 border border-white/10 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-indigo-500';

export default function SettingsPage({ token, user, onLogout }: { token: string | null; user: JwtUser; onLogout: () => void }) {
  const [polling, setPolling] = useState(localStorage.getItem('ds_polling') !== 'off');
  const [rpc, setRpc] = useState(localStorage.getItem('ds_rpc') !== 'off');

  // Server-synced profile preferences (same as website Preferences tab).
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [displayName, setDisplayName] = useState('');
  const [dataSource, setDataSource] = useState('combined');
  const [fmMode, setFmMode] = useState('full');
  const [timezone, setTimezone] = useState('UTC');
  const [privateMode, setPrivateMode] = useState(false);
  const [showTrackPlaycount, setShowTrackPlaycount] = useState(false);
  const [showFeatures, setShowFeatures] = useState(false);

  // Spotify link state.
  const [spLinked, setSpLinked] = useState<boolean | null>(null);
  const [spBusy, setSpBusy] = useState(false);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    localStorage.setItem('ds_polling', polling ? 'on' : 'off');
  }, [polling]);
  useEffect(() => {
    localStorage.setItem('ds_rpc', rpc ? 'on' : 'off');
  }, [rpc]);

  useEffect(() => {
    (async () => {
      if (!token) return;
      setLoading(true);
      try {
        const s = await api.getSettings(token);
        if (s.displayName !== undefined) setDisplayName(s.displayName || '');
        if (s.dataSource) setDataSource(s.dataSource);
        if (s.fmMode) setFmMode(s.fmMode);
        if (s.timezone) setTimezone(s.timezone);
        setPrivateMode(!!s.privateMode);
        setShowTrackPlaycount(!!s.showTrackPlaycount);
        setShowFeatures(!!s.showFeatures);
      } catch (e) {
        toast.error(e instanceof Error ? e.message : 'Failed to load settings');
      } finally {
        setLoading(false);
      }
      try {
        const st = await api.spotifyStatus(token);
        setSpLinked(!!st.linked);
      } catch {
        setSpLinked(null);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async () => {
    if (!token || saving) return;
    setSaving(true);
    try {
      await api.saveSettings(token, {
        displayName,
        dataSource,
        fmMode,
        timezone: timezone.trim() || 'UTC',
        privateMode,
        showTrackPlaycount,
        showFeatures,
      });
      toast.success('Preferences saved — applies to bot, site and apps.');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const disconnectSpotify = async () => {
    if (!token || spBusy) return;
    setSpBusy(true);
    try {
      await api.spotifyDisconnect(token);
      setSpLinked(false);
      toast.success('Spotify disconnected.');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Disconnect failed');
    } finally {
      setSpBusy(false);
    }
  };

  const linkSpotify = () => {
    const q = user.id ? `?discord_id=${encodeURIComponent(String(user.id))}` : '';
    window.open(`${API_BASE}/api/auth/spotify/login${q}`, '_blank', 'noopener');
  };

  return (
    <div className="max-w-3xl mx-auto pb-20 animate-fade-in">
      <h1 className="text-4xl font-black mb-8">Settings</h1>
      <div className="space-y-5">
        <Card className="p-6 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="font-bold">Profile preferences</h2>
            <span className="text-[11px] text-zinc-500 font-semibold">Syncs with bot · site · apps</span>
          </div>
          {loading ? (
            <Spinner label="Loading preferences…" />
          ) : (
            <>
              <div>
                <label className="text-xs font-bold uppercase tracking-widest text-zinc-500">Display name</label>
                <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="Leave empty to use Discord name" className={`${inputCls} mt-2`} maxLength={32} />
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="text-xs font-bold uppercase tracking-widest text-zinc-500">Data source</label>
                  <select value={dataSource} onChange={(e) => setDataSource(e.target.value)} className={`${inputCls} mt-2`}>
                    <option value="combined">Combined</option>
                    <option value="lastfm_only">Last.fm only</option>
                    <option value="imported_only">Imported only</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs font-bold uppercase tracking-widest text-zinc-500">/fm layout</label>
                  <select value={fmMode} onChange={(e) => setFmMode(e.target.value)} className={`${inputCls} mt-2`}>
                    <option value="full">Full embed</option>
                    <option value="compact">Compact</option>
                    <option value="stats">Stats</option>
                  </select>
                </div>
              </div>
              <div>
                <label className="text-xs font-bold uppercase tracking-widest text-zinc-500">Timezone</label>
                <input value={timezone} onChange={(e) => setTimezone(e.target.value)} placeholder="UTC" className={`${inputCls} mt-2`} maxLength={64} />
              </div>
              <Toggle value={privateMode} onChange={setPrivateMode} title="Private profile" hint="Hide your stats from other people." />
              <Toggle value={showTrackPlaycount} onChange={setShowTrackPlaycount} title="Show track playcounts" hint="Display play counts on track rows." />
              <Toggle value={showFeatures} onChange={setShowFeatures} title="Showcase features" hint="Feature your top stats on your profile." />
              <button onClick={save} disabled={saving} className="w-full py-3 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 font-bold">
                {saving ? 'Saving…' : 'Save preferences'}
              </button>
            </>
          )}
        </Card>

        <Card className="p-6 space-y-4">
          <h2 className="font-bold">Spotify</h2>
          <div className="flex items-center justify-between p-4 bg-black/20 rounded-2xl border border-white/5">
            <div className="text-sm">
              {spLinked == null ? <span className="text-zinc-500">Status unknown.</span> : spLinked ? <span className="text-emerald-300 font-bold">Linked ✓</span> : <span className="text-zinc-400">Not linked.</span>}
            </div>
            {spLinked ? (
              <button onClick={disconnectSpotify} disabled={spBusy} className="px-5 py-2 rounded-xl bg-red-500/10 text-red-300 border border-red-500/30 font-bold text-sm disabled:opacity-50">
                Disconnect
              </button>
            ) : (
              <button onClick={linkSpotify} className="px-5 py-2 rounded-xl bg-green-500/15 text-green-300 border border-green-500/30 font-bold text-sm">
                Link Spotify
              </button>
            )}
          </div>
          <p className="text-xs text-zinc-500">Linking unlocks playback controls and the Player tab. After approving in your browser, come back here.</p>
        </Card>

        <Card className="p-6 space-y-4">
          <h2 className="font-bold">This app</h2>
          <Toggle value={polling} onChange={setPolling} title="Live auto-refresh" hint="Poll stats / Spotify every 15s." />
          <Toggle value={rpc} onChange={setRpc} title="Discord Rich Presence" hint="Show current track in Discord." />
        </Card>

        <Card className="p-6">
          <h2 className="font-bold mb-2">App</h2>
          <p className="text-sm text-zinc-400">Version v{APP_VERSION} · API {API_BASE}</p>
          <div className="flex gap-2 mt-4">
            <button
              onClick={() => {
                setChecking(true);
                toast.loading('Checking for updates…', { id: 'upd-check' });
                manualCheck({
                  onAvailable: () => {
                    setChecking(false);
                    toast.success('Update found — downloading now.', { id: 'upd-check' });
                  },
                  onUpToDate: () => {
                    setChecking(false);
                    toast.success("You're up to date.", { id: 'upd-check' });
                  },
                  onError: (m) => {
                    setChecking(false);
                    toast.error(m, { id: 'upd-check' });
                  },
                });
              }}
              disabled={checking}
              className="px-5 py-2.5 rounded-xl bg-white/5 border border-white/10 font-bold hover:bg-white/10 disabled:opacity-50"
            >
              {checking ? 'Checking…' : 'Check for updates'}
            </button>
            <button onClick={onLogout} className="px-5 py-2.5 rounded-xl bg-red-500/10 text-red-300 border border-red-500/30 font-bold hover:bg-red-500/20">
              Log out
            </button>
          </div>
        </Card>
      </div>
    </div>
  );
}
