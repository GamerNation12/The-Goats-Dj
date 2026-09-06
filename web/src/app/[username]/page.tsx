"use client";
import { fetchApi } from "@/lib/fetchApi";
import { useSession } from "@/app/providers";
import { useState, useEffect, use } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "react-hot-toast";
import NowPlayingWidget from "@/components/NowPlayingWidget";
import TrackModal from "@/components/TrackModal";
import AdminClient from "@/app/admin/AdminClient";
import Link from "next/link";

export default function CombinedProfileDashboard({ params }: { params: Promise<{ username: string }> }) {
  const resolvedParams = use(params);
  const usernameParam = decodeURIComponent(resolvedParams.username || "");
  const { data: session, status } = useSession();
  const router = useRouter();
  const searchParams = useSearchParams();
  
  const displayUsername = session?.user?.name === "gamernation12" ? "GamerNation12" : session?.user?.name;
  const isOwner = status === "authenticated" && displayUsername && displayUsername === usernameParam;

  const [selectedTrack, setSelectedTrack] = useState<any>(null);

  // Active Tab
  const [activeTab, setActiveTab] = useState<"profile" | "settings" | "suggestions" | "import" | "admin">("profile");

  // Admin role: controls visibility of the Admin Console tab.
  // AdminClient re-verifies server-side; this just hides the button.
  const [adminRole, setAdminRole] = useState<string | null>(null);

  useEffect(() => {
    const tab = searchParams.get("tab");
    if (tab === "import" || tab === "suggestions" || tab === "settings" || tab === "profile" || tab === "admin") {
      setActiveTab(tab);
    }
  }, [searchParams]);

  useEffect(() => {
    if (status !== "authenticated" || !isOwner) {
      setAdminRole(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await fetchApi("/api/admin/check");
        const data = await res.json();
        if (cancelled) return;
        if (res.ok && data.role) {
          setAdminRole(data.role);
        } else {
          setAdminRole(null);
          setActiveTab((prev) => (prev === "admin" ? "profile" : prev));
        }
      } catch {
        if (!cancelled) {
          setAdminRole(null);
          setActiveTab((prev) => (prev === "admin" ? "profile" : prev));
        }
      }
    })();
    return () => { cancelled = true; };
  }, [status, isOwner]);

  // --- Import State ---
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importProgress, setImportProgress] = useState(0);
  const [importStatus, setImportStatus] = useState<"idle" | "uploading" | "complete" | "error">("idle");
  const [importError, setImportError] = useState<string | null>(null);
  const [importLocked, setImportLocked] = useState(false);
  const [importLockReason, setImportLockReason] = useState<string | null>(null);
  const [spotifyLinked, setSpotifyLinked] = useState<boolean | null>(null);
  const [spotifyBusy, setSpotifyBusy] = useState(false);

  // Reflect the bot owner's import lock (disabled_commands) in the UI.
  // Server routes enforce it regardless; this just avoids a doomed upload.
  useEffect(() => {
    if (activeTab !== "import" || !isOwner) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetchApi("/api/import/status");
        const data = await res.json();
        if (!cancelled && res.ok) {
          setImportLocked(!!data.locked);
          setImportLockReason(data.reason ?? null);
        }
      } catch {
        /* fail open: the API routes still enforce the lock */
      }
    })();
    return () => { cancelled = true; };
  }, [activeTab, isOwner]);

  const CHUNK_SIZE = 2 * 1024 * 1024; // 2MB

  // Spotify link status for the Connected Accounts card.
  useEffect(() => {
    if (activeTab !== "settings" || !isOwner) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetchApi("/api/spotify/status");
        const data = await res.json();
        if (!cancelled && res.ok) setSpotifyLinked(!!data.linked);
      } catch {
        /* unknown: actions stay hidden */
      }
    })();
    return () => { cancelled = true; };
  }, [activeTab, isOwner]);

  const handleSpotifyDisconnect = async () => {
    setSpotifyBusy(true);
    try {
      const res = await fetchApi("/api/spotify/disconnect", { method: "POST" });
      if (res.ok) setSpotifyLinked(false);
    } catch {
      /* keep current state on failure */
    } finally {
      setSpotifyBusy(false);
    }
  };

  const handleImportUpload = async () => {
    if (!importFile) return;
    if (importLocked) {
      setImportError(importLockReason ? `Imports are currently disabled. ${importLockReason}` : "Imports are currently disabled by the bot owner.");
      setImportStatus("error");
      return;
    }
    setImportStatus("uploading");
    setImportProgress(0);
    setImportError(null);

    const totalChunks = Math.ceil(importFile.size / CHUNK_SIZE);
    
    try {
      const initRes = await fetchApi("/api/import/init", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: importFile.name, totalChunks })
      });
      const initData = await initRes.json();
      if (!initRes.ok) throw new Error(initData.error || "Failed to initialize upload");
      
      const jobId = initData.jobId;

      for (let i = 0; i < totalChunks; i++) {
        const start = i * CHUNK_SIZE;
        const end = Math.min(start + CHUNK_SIZE, importFile.size);
        const chunk = importFile.slice(start, end);

        const formData = new FormData();
        formData.append("jobId", jobId);
        formData.append("chunkIndex", i.toString());
        formData.append("chunk", chunk);

        const chunkRes = await fetchApi("/api/import/chunk", {
          method: "POST",
          body: formData
        });

        if (!chunkRes.ok) throw new Error("Failed to upload chunk " + i);

        setImportProgress(Math.round(((i + 1) / totalChunks) * 100));
      }

      const finRes = await fetchApi("/api/import/finalize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ jobId })
      });

      if (!finRes.ok) throw new Error("Failed to finalize upload");

      setImportStatus("complete");
    } catch (err: any) {
      console.error(err);
      setImportError(err.message);
      setImportStatus("error");
    }
  };

  // --- Public Profile State ---
  const [profile, setProfile] = useState<any>(null);
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileError, setProfileError] = useState<string | null>(null);

  // --- Dashboard Settings State ---
  const [fmMode, setFmMode] = useState<"compact" | "full" | "stats">("full");
  const [showFeatures, setShowFeatures] = useState<boolean>(false);
  const [privateMode, setPrivateMode] = useState<boolean>(false);
  const [dataSource, setDataSource] = useState<"combined" | "imported_only" | "lastfm_only">("combined");
  const [timezone, setTimezone] = useState<string>("UTC");
  const [showTrackPlaycount, setShowTrackPlaycount] = useState<boolean>(false);
  const [displayName, setDisplayName] = useState<string>("");
  
  const [unsavedFmMode, setUnsavedFmMode] = useState<"compact" | "full" | "stats">("full");
  const [unsavedShowFeatures, setUnsavedShowFeatures] = useState<boolean>(false);
  const [unsavedPrivateMode, setUnsavedPrivateMode] = useState<boolean>(false);
  const [unsavedDataSource, setUnsavedDataSource] = useState<"combined" | "imported_only" | "lastfm_only">("combined");
  const [unsavedTimezone, setUnsavedTimezone] = useState<string>("UTC");
  const [unsavedShowTrackPlaycount, setUnsavedShowTrackPlaycount] = useState<boolean>(false);
  const [unsavedDisplayName, setUnsavedDisplayName] = useState<string>("");
  
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);

  // --- Suggestions & Stats ---
  const [suggestions, setSuggestions] = useState<any[]>([]);
  const [newSuggestionTitle, setNewSuggestionTitle] = useState("");
  const [newSuggestionDesc, setNewSuggestionDesc] = useState("");
  const [submittingSuggestion, setSubmittingSuggestion] = useState(false);
  const [suggestionsLoading, setSuggestionsLoading] = useState(true);

  const [userStats, setUserStats] = useState<any>(null);
  const [userStatsLoading, setUserStatsLoading] = useState(true);
  const [botStatus, setBotStatus] = useState<string | null>(null);

  // Listening period for top stats (Top Artists / Albums / Tracks).
  // Recents + insights always reflect the latest activity.
  const PERIODS = [
    { value: "7day", label: "7D" },
    { value: "1month", label: "1M" },
    { value: "3month", label: "3M" },
    { value: "6month", label: "6M" },
    { value: "12month", label: "1Y" },
    { value: "overall", label: "All" },
  ];
  const [period, setPeriod] = useState("overall");
  const PERIOD_LONG: Record<string, string> = {
    "7day": "last 7 days",
    "1month": "last month",
    "3month": "last 3 months",
    "6month": "last 6 months",
    "12month": "last year",
    "overall": "of all time",
  };
  const periodLong = PERIOD_LONG[period] || "of all time";

  // Fetch Public Profile Data
  useEffect(() => {
    let isMounted = true;
    setProfileLoading(true);
    
    const fetchProfile = async () => {
      try {
        const res = await fetchApi(`/api/u/${encodeURIComponent(usernameParam)}?period=${period}&t=${Date.now()}`);
        const data = await res.json();
        if (isMounted) {
          if (data.error) {
            setProfileError(data.error);
          } else {
            setProfileError(null);
            setProfile(data);
          }
        }
      } catch (err) {
        if (isMounted) setProfileError("Failed to load profile.");
      } finally {
        if (isMounted) setProfileLoading(false);
      }
    };

    fetchProfile();
    const intervalId = setInterval(fetchProfile, 5000); // Poll every 5 seconds

    return () => {
      isMounted = false;
      clearInterval(intervalId);
    };
  }, [usernameParam, period]);

  // Fetch Dashboard Data (only if owner)
  useEffect(() => {
    if (isOwner) {
      fetchApi("/api/settings")
        .then((res) => res.json())
        .then((data) => {
          if (data) {
            if (data.fmMode) { setFmMode(data.fmMode); setUnsavedFmMode(data.fmMode); }
            if (data.showFeatures !== undefined) { setShowFeatures(data.showFeatures); setUnsavedShowFeatures(data.showFeatures); }
            if (data.privateMode !== undefined) { setPrivateMode(data.privateMode); setUnsavedPrivateMode(data.privateMode); }
            if (data.dataSource) { setDataSource(data.dataSource); setUnsavedDataSource(data.dataSource); }
            if (data.timezone) { setTimezone(data.timezone); setUnsavedTimezone(data.timezone); }
            if (data.showTrackPlaycount !== undefined) { setShowTrackPlaycount(data.showTrackPlaycount); setUnsavedShowTrackPlaycount(data.showTrackPlaycount); }
            if (data.displayName !== undefined) { setDisplayName(data.displayName); setUnsavedDisplayName(data.displayName); }
          }
        })
        .catch(console.error);

      fetchSuggestions();

      fetchApi("/api/user-stats")
        .then((res) => res.json())
        .then((data) => {
          if (data.success) setUserStats(data.stats);
        })
        .catch(console.error)
        .finally(() => setUserStatsLoading(false));

      fetchApi("/api/bot-status")
        .then((res) => res.json())
        .then((data) => {
          if (data.status) setBotStatus(data.status);
        })
        .catch(console.error);
    }
  }, [isOwner]);

  const fetchSuggestions = async () => {
    setSuggestionsLoading(true);
    try {
      const res = await fetchApi("/api/suggestions");
      if (res.ok) {
        const data = await res.json();
        setSuggestions(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setSuggestionsLoading(false);
    }
  };

  useEffect(() => {
    if (
      unsavedFmMode !== fmMode ||
      unsavedShowFeatures !== showFeatures ||
      unsavedPrivateMode !== privateMode ||
      unsavedDataSource !== dataSource ||
      unsavedTimezone !== timezone ||
      unsavedShowTrackPlaycount !== showTrackPlaycount ||
      unsavedDisplayName !== displayName
    ) {
      setHasUnsavedChanges(true);
    } else {
      setHasUnsavedChanges(false);
    }
  }, [unsavedFmMode, unsavedShowFeatures, unsavedPrivateMode, unsavedDataSource, unsavedTimezone, unsavedShowTrackPlaycount, unsavedDisplayName, fmMode, showFeatures, privateMode, dataSource, timezone, showTrackPlaycount, displayName]);

  const saveSettings = async () => {
    setSavingSettings(true);
    try {
      const res = await fetchApi("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          fmMode: unsavedFmMode,
          showFeatures: unsavedShowFeatures,
          privateMode: unsavedPrivateMode,
          dataSource: unsavedDataSource,
          timezone: unsavedTimezone,
          showTrackPlaycount: unsavedShowTrackPlaycount,
          displayName: unsavedDisplayName
        }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.fmMode) setFmMode(data.fmMode);
        if (data.showFeatures !== undefined) setShowFeatures(data.showFeatures);
        if (data.privateMode !== undefined) setPrivateMode(data.privateMode);
        if (data.dataSource) setDataSource(data.dataSource);
        if (data.timezone) setTimezone(data.timezone);
        if (data.showTrackPlaycount !== undefined) setShowTrackPlaycount(data.showTrackPlaycount);
        if (data.displayName !== undefined) setDisplayName(data.displayName);
        setHasUnsavedChanges(false);
        toast.success("Settings saved successfully!");
      }
    } catch (err) {
      console.error("Error saving settings:", err);
      toast.error("Failed to save settings.");
    } finally {
      setSavingSettings(false);
    }
  };

  const submitSuggestion = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newSuggestionTitle || !newSuggestionDesc) return;
    setSubmittingSuggestion(true);
    try {
      const res = await fetchApi("/api/suggestions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: newSuggestionTitle, description: newSuggestionDesc })
      });
      if (res.ok) {
        setNewSuggestionTitle("");
        setNewSuggestionDesc("");
        fetchSuggestions();
        toast.success("Suggestion submitted!");
      }
    } catch (e) {
      console.error(e);
      toast.error("Failed to submit suggestion.");
    } finally {
      setSubmittingSuggestion(false);
    }
  };

  const ToggleSwitch = ({ checked, onChange }: { checked: boolean, onChange: () => void }) => (
    <button 
      onClick={onChange}
      className={`relative inline-flex h-7 w-12 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-all duration-300 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/50 ${checked ? 'bg-indigo-500 shadow-[0_0_15px_rgba(99,102,241,0.5)]' : 'bg-zinc-800 hover:bg-zinc-700'}`}
    >
      <span className="sr-only">Toggle</span>
      <span className={`pointer-events-none inline-block h-6 w-6 transform rounded-full bg-white shadow-md ring-0 transition duration-300 ease-out ${checked ? 'translate-x-5 scale-105' : 'translate-x-0'}`} />
    </button>
  );

  const CustomSelect = ({ value, options, onChange }: { value: string, options: {label: string, value: string}[], onChange: (val: string) => void }) => {
    const [isOpen, setIsOpen] = useState(false);
    const selectedOption = options.find(o => o.value === value) || options[0];

    return (
      <div className="relative w-full">
        <button
          type="button"
          onClick={() => setIsOpen(!isOpen)}
          className={`w-full flex items-center justify-between gap-3 bg-zinc-900/50 hover:bg-white/5 border rounded-xl px-4 py-3 text-white font-semibold focus:outline-none transition-all shadow-sm ${isOpen ? 'border-indigo-500/50 shadow-[0_0_15px_rgba(99,102,241,0.1)]' : 'border-white/10 hover:border-white/20'}`}
        >
          <span className="truncate">{selectedOption.label}</span>
          <span className={`shrink-0 text-zinc-500 text-[10px] transition-transform duration-300 ${isOpen ? 'rotate-180 text-indigo-400' : ''}`}>▼</span>
        </button>
        
        {isOpen && (
          <>
            <div className="fixed inset-0 z-40" onClick={() => setIsOpen(false)}></div>
            <div className="absolute z-50 w-full mt-2 bg-zinc-900/95 backdrop-blur-xl border border-white/10 rounded-xl overflow-y-auto overflow-x-hidden max-h-60 shadow-[0_10px_40px_rgba(0,0,0,0.5)] animate-fade-in-up styled-scrollbar">
              {options.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => {
                    onChange(opt.value);
                    setIsOpen(false);
                  }}
                  className={`w-full text-left px-4 py-3 truncate hover:bg-indigo-500/20 transition-colors ${value === opt.value ? 'text-indigo-400 font-bold bg-indigo-500/10' : 'text-zinc-300 font-medium'}`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    );
  };

  const getStatusBadge = (status: string, title?: string) => {
    const isBug = title?.toLowerCase().includes("bug");
    switch(status) {
      case 'approved': 
        if (isBug) return <span className="px-2 py-1 bg-blue-500/20 text-blue-400 border border-blue-500/30 rounded-lg text-xs font-bold uppercase tracking-wider">Investigating</span>;
        return <span className="px-2 py-1 bg-green-500/20 text-green-400 border border-green-500/30 rounded-lg text-xs font-bold uppercase tracking-wider">Approved</span>;
      case 'denied': 
        if (isBug) return <span className="px-2 py-1 bg-red-500/20 text-red-400 border border-red-500/30 rounded-lg text-xs font-bold uppercase tracking-wider">Not a Bug</span>;
        return <span className="px-2 py-1 bg-red-500/20 text-red-400 border border-red-500/30 rounded-lg text-xs font-bold uppercase tracking-wider">Denied</span>;
      case 'completed': 
        if (isBug) return <span className="px-2 py-1 bg-green-500/20 text-green-400 border border-green-500/30 rounded-lg text-xs font-bold uppercase tracking-wider">Fixed</span>;
        return <span className="px-2 py-1 bg-indigo-500/20 text-indigo-400 border border-indigo-500/30 rounded-lg text-xs font-bold uppercase tracking-wider">Update Released</span>;
      default: 
        return <span className="px-2 py-1 bg-yellow-500/20 text-yellow-400 border border-yellow-500/30 rounded-lg text-xs font-bold uppercase tracking-wider">Pending</span>;
    }
  };

  // Listening insights derived from the latest profile payload.
  // Recents are always the latest activity, so these stay live on every poll.
  const insights = (() => {
    const stats = profile?.stats;
    const recents: any[] = Array.isArray(stats?.recentTracks) ? stats.recentTracks : [];
    const now = Date.now();
    const plays24h = recents.filter((t) => !t.nowPlaying && t.date && now - parseInt(t.date) * 1000 < 24 * 60 * 60 * 1000).length;
    const uniqueArtists = new Set(recents.map((t) => (t.artist || "").toLowerCase()).filter(Boolean)).size;
    const nowPlaying = recents.find((t) => t.nowPlaying) || null;
    const topArtist = stats?.topArtists?.[0] || null;
    const total = Number(stats?.playcount || 0);
    const share = topArtist && total > 0 ? Math.min(100, (Number(topArtist.playcount || 0) / total) * 100) : 0;
    const nextMilestone = total < 10 ? 10 : Math.pow(10, Math.ceil(Math.log10(total + 1)));
    const milestonePct = Math.min(100, (total / nextMilestone) * 100);
    return { plays24h, uniqueArtists, nowPlaying, topArtist, total, share, nextMilestone, milestonePct };
  })();

  if (profileLoading || status === "loading") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#09090b]">
        <div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
      </div>
    );
  }

  // If the profile does not exist or is private, and the user is NOT the owner
  if (profileError && !isOwner) {
    let icon = "🔒";
    let title = "Profile Unavailable";
    
    const errorLower = profileError.toLowerCase();
    
    const isFatalError = errorLower.includes("private") || errorLower.includes("not found");
    if (!profile || isFatalError) {
      if (errorLower.includes("not found")) {
        icon = "👻";
        title = "User Not Found";
      } else if (errorLower.includes("server error") || errorLower.includes("failed to load")) {
        icon = "⚠️";
        title = "Server Error";
      } else if (errorLower.includes("private")) {
        icon = "🔒";
        title = "Private Profile";
      }

      return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-[#09090b] text-white p-4 relative overflow-hidden">
        {/* Background Glow */}
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-96 h-96 bg-indigo-500/10 rounded-full blur-[100px] pointer-events-none" />
        
        <div className="bg-zinc-900/40 backdrop-blur-2xl border border-white/10 p-10 rounded-[2rem] max-w-md w-full text-center shadow-2xl relative z-10 animate-fade-in-up">
          <div className="text-7xl mb-6 drop-shadow-[0_10px_20px_rgba(0,0,0,0.5)] transform transition-transform hover:scale-110">{icon}</div>
          <h2 className="text-3xl font-black mb-3 tracking-tight text-transparent bg-clip-text bg-gradient-to-br from-white to-zinc-400">{title}</h2>
          <p className="text-zinc-400 text-base mb-8 leading-relaxed font-medium">
            {profileError === "Internal Server Error" 
              ? "We hit a snag while trying to load this profile. Please try again later."
              : profileError}
          </p>
          <Link href="/" className="px-8 py-3.5 bg-white text-black hover:bg-zinc-200 rounded-xl font-bold transition-all inline-block shadow-[0_0_20px_rgba(255,255,255,0.1)] hover:shadow-[0_0_30px_rgba(255,255,255,0.2)] hover:scale-105 active:scale-95">
            Return Home
          </Link>
        </div>
      </div>
    );
    }
  }

  return (
    <div className="min-h-screen bg-[#09090b] text-white font-sans selection:bg-indigo-500/30 overflow-x-hidden relative pb-32">
      {/* Background Blurs */}
      <div className="fixed top-0 left-1/4 w-1/2 h-[500px] bg-indigo-600/10 rounded-full blur-[160px] pointer-events-none z-0" />
      <div className="fixed bottom-0 right-1/4 w-1/3 h-[400px] bg-purple-600/10 rounded-full blur-[160px] pointer-events-none z-0" />
      
      <main className="container mx-auto px-4 sm:px-6 lg:px-8 pt-32 pb-16 relative z-10 max-w-7xl animate-fade-in-up">
        
        {/* HERO BANNER (Modified to handle both owner view and public view) */}
        <div className="bg-zinc-950/40 backdrop-blur-3xl border border-white/10 rounded-3xl p-6 md:p-8 shadow-2xl relative overflow-hidden mb-8 text-center md:text-left flex flex-col md:flex-row items-center justify-between gap-8">
          <div className="absolute inset-0 bg-gradient-to-r from-indigo-500/5 to-purple-500/5 pointer-events-none" />
          
          <div className="relative flex flex-col md:flex-row items-center md:items-start text-center md:text-left gap-4 md:gap-6">
            <div className="relative">
              <div className="absolute inset-0 bg-indigo-500 rounded-full blur-xl opacity-50 animate-pulse"></div>
              <span className="block w-24 h-24 md:w-32 md:h-32 rounded-full border-4 border-zinc-900/50 shadow-2xl relative z-10 overflow-hidden bg-zinc-800 shrink-0">
                <span className="absolute inset-0 flex items-center justify-center text-3xl md:text-5xl font-black text-indigo-300">
                  {(displayUsername || "?").charAt(0).toUpperCase()}
                </span>
                <img
                  src={profile?.users?.[0]?.avatar || session?.user?.image || "/logo.png"}
                  alt="Avatar"
                  className="absolute inset-0 w-full h-full object-cover"
                  onError={(e) => { const t = e.currentTarget; if (!t.src.endsWith("/logo.png")) t.src = "/logo.png"; }}
                />
              </span>
            </div>
            <div className="flex flex-col justify-center h-full">
              {isOwner ? (
                <>
                  <h1 className="text-3xl md:text-5xl font-black bg-clip-text text-transparent bg-gradient-to-r from-white to-zinc-400 mb-2">
                    Welcome back, <span className="text-indigo-400">{displayUsername}</span>
                  </h1>
                  <p className="text-zinc-400 text-lg">Manage your integration, preferences, and account data.</p>
                </>
              ) : (
                <>
                  <h1 className="text-3xl md:text-5xl font-black tracking-tight text-white mb-2">{profile?.users?.[0]?.name || usernameParam}</h1>
                  <p className="text-indigo-400 text-sm font-semibold uppercase tracking-widest mb-4">DJ Scratch Profile</p>
                  <a 
                    href={`https://www.last.fm/user/${profile?.lastfm_username || usernameParam}`} 
                    target="_blank" 
                    rel="noreferrer" 
                    className="inline-flex items-center gap-2 px-5 py-2 bg-[#ba0000] hover:bg-[#d50000] text-white text-sm font-bold rounded-xl transition-all shadow-lg hover:shadow-[#ba0000]/30 w-fit"
                  >
                    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M16.5 13.932c-1.353 0-2.457-1.127-2.457-2.52 0-1.391 1.104-2.518 2.457-2.518 1.35 0 2.454 1.127 2.454 2.518 0 1.393-1.104 2.52-2.454 2.52m-.032-7.593c-2.316 0-4.227 1.636-4.707 3.821h-.041l-.988-3.666H7.135l1.631 5.922c-.655 1.579-2.029 2.584-3.665 2.584-1.634 0-2.906-1.154-2.906-2.616 0-1.464 1.258-2.619 2.906-2.619.673 0 1.298.243 1.834.717l2.179-2.81c-1.077-.962-2.502-1.554-4.013-1.554-3.088 0-5.022 2.306-5.022 5.253 0 2.946 2.052 5.266 5.022 5.266 2.873 0 5.158-2.051 5.626-4.664h.043c.535 2.529 2.766 4.664 5.728 4.664 3.09 0 5.568-2.519 5.568-5.656 0-3.136-2.478-5.645-5.568-5.645"/></svg>
                    View on Last.fm
                  </a>
                </>
              )}
            </div>
          </div>
          
          {/* Right Side Stats/Widgets */}
          {isOwner ? (
            <div className="w-full md:w-auto md:min-w-[300px]">
              <NowPlayingWidget />
            </div>
          ) : (
            profile?.stats?.playcount > 0 && (
              <div className="bg-zinc-900/50 border border-white/10 px-6 sm:px-8 py-3 sm:py-4 rounded-2xl flex items-center justify-center gap-4 hover:border-indigo-500/30 transition-colors">
                 <div className="text-zinc-400 text-xs font-bold uppercase tracking-wider">Total Scrobbles</div>
                 <div className="text-3xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 to-purple-400">
                   {profile.stats.playcount.toLocaleString()}
                 </div>
              </div>
            )
          )}
        </div>

        {/* TAB SWITCHER (Only visible to owner) */}
        {isOwner && (
          <div className="flex items-center justify-center mb-8">
            <div className="bg-zinc-900/50 backdrop-blur-md p-1.5 rounded-2xl border border-white/5 flex flex-col sm:flex-row w-full sm:w-auto gap-2">
              <button 
                onClick={() => setActiveTab("profile")}
                className={`px-3 sm:px-6 py-2 sm:py-2.5 rounded-xl text-xs sm:text-sm font-bold transition-all duration-300 flex items-center gap-2 ${activeTab === 'profile' ? 'bg-indigo-500/20 text-indigo-300 shadow-lg border border-indigo-500/20' : 'text-zinc-400 hover:text-white hover:bg-white/5'}`}
              >
                <span>🌍</span> Public Profile
              </button>
              <button 
                onClick={() => setActiveTab("settings")}
                className={`px-3 sm:px-6 py-2 sm:py-2.5 rounded-xl text-xs sm:text-sm font-bold transition-all duration-300 flex items-center gap-2 ${activeTab === 'settings' ? 'bg-white/10 text-white shadow-lg' : 'text-zinc-400 hover:text-white hover:bg-white/5'}`}
              >
                <span>⚙️</span> Preferences
              </button>
              <button 
                onClick={() => setActiveTab("suggestions")}
                className={`px-3 sm:px-6 py-2 sm:py-2.5 rounded-xl text-xs sm:text-sm font-bold transition-all duration-300 flex items-center gap-2 ${activeTab === 'suggestions' ? 'bg-emerald-500/20 text-emerald-300 shadow-lg border border-emerald-500/20' : 'text-zinc-400 hover:text-white hover:bg-white/5'}`}
              >
                <span>💡</span> Feedback & Ideas
              </button>
              <button
                onClick={() => setActiveTab('import')}
                className={`px-3 sm:px-6 py-2 sm:py-2.5 rounded-xl text-xs sm:text-sm font-bold transition-all duration-300 flex items-center gap-2 ${activeTab === 'import' ? 'bg-amber-500/20 text-amber-300 shadow-lg border border-amber-500/20' : 'text-zinc-400 hover:text-white hover:bg-white/5'}`}
              >
                <span>📥</span> Import
              </button>
              {adminRole && (
              <button
                onClick={() => setActiveTab('admin')}
                className={`px-3 sm:px-6 py-2 sm:py-2.5 rounded-xl text-xs sm:text-sm font-bold transition-all duration-300 flex items-center gap-2 ${activeTab === 'admin' ? 'bg-red-500/20 text-red-300 shadow-lg border border-red-500/20' : 'text-zinc-400 hover:text-white hover:bg-white/5'}`}
              >
                <span>🛡️</span> Admin
              </button>
              )}
            </div>
          </div>
        )}

        {/* --- PROFILE TAB --- */}
        {(!isOwner || activeTab === "profile") && profile && !profileError && (
          <>
          {/* Period selector for Top Artists / Albums / Tracks */}
          <div className="flex flex-wrap items-center gap-2 mb-6">
            <span className="text-xs font-bold uppercase tracking-widest text-zinc-500 mr-1">Tops:</span>
            {PERIODS.map((p) => (
              <button
                key={p.value}
                onClick={() => setPeriod(p.value)}
                className={`px-4 py-1.5 rounded-full text-xs font-bold transition-all ${
                  period === p.value
                    ? "bg-indigo-500 text-white shadow-[0_0_15px_rgba(99,102,241,0.4)]"
                    : "bg-white/5 text-zinc-400 hover:text-white hover:bg-white/10 border border-white/5"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>

          {/* Listening insights */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
            <div className="bg-zinc-950/40 backdrop-blur-3xl border border-white/5 rounded-3xl p-5 shadow-xl">
              <div className="text-zinc-500 text-[11px] font-bold uppercase tracking-widest mb-1">Last 24 hours</div>
              <div className="text-3xl font-black text-white">{insights.plays24h}</div>
              <div className="text-xs text-zinc-400 mt-1">plays scrobbled</div>
            </div>
            <div className="bg-zinc-950/40 backdrop-blur-3xl border border-white/5 rounded-3xl p-5 shadow-xl">
              <div className="text-zinc-500 text-[11px] font-bold uppercase tracking-widest mb-1">In rotation</div>
              <div className="text-3xl font-black text-white">{insights.uniqueArtists}</div>
              <div className="text-xs text-zinc-400 mt-1">artists in recents</div>
            </div>
            <div className="bg-zinc-950/40 backdrop-blur-3xl border border-white/5 rounded-3xl p-5 shadow-xl">
              <div className="text-zinc-500 text-[11px] font-bold uppercase tracking-widest mb-1">Top artist share</div>
              <div className="text-3xl font-black text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 to-purple-400">
                {insights.share.toFixed(1)}%
              </div>
              <div className="text-xs text-zinc-400 mt-1 truncate">{insights.topArtist?.name || "—"}</div>
            </div>
            <div className="bg-zinc-950/40 backdrop-blur-3xl border border-white/5 rounded-3xl p-5 shadow-xl">
              <div className="text-zinc-500 text-[11px] font-bold uppercase tracking-widest mb-1">
                Next milestone · {insights.nextMilestone.toLocaleString()}
              </div>
              <div className="text-3xl font-black text-white">{(insights.nextMilestone - insights.total).toLocaleString()}</div>
              <div className="text-xs text-zinc-400 mt-1 mb-2">plays to go</div>
              <div className="h-1.5 w-full bg-white/5 rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-indigo-500 to-purple-500 rounded-full transition-all"
                  style={{ width: `${insights.milestonePct}%` }}
                />
              </div>
            </div>
          </div>

          {insights.nowPlaying && (
            <div className="mb-8 bg-green-500/5 border border-green-500/20 rounded-2xl px-5 py-3.5 flex items-center gap-4">
              <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse shrink-0" />
              <p className="text-sm text-zinc-300 truncate">
                <span className="font-bold text-white">{insights.nowPlaying.name}</span>
                <span className="text-zinc-500"> — {insights.nowPlaying.artist}</span>
              </p>
              <span className="ml-auto text-[10px] font-black uppercase tracking-widest text-green-400 shrink-0">Now playing</span>
            </div>
          )}

          <div className="grid lg:grid-cols-2 gap-8 items-start animate-fade-in">
            {/* Top Artists Grid */}
            <div className="bg-zinc-950/40 backdrop-blur-3xl border border-white/5 rounded-3xl overflow-hidden shadow-2xl relative">
               <div className="px-6 sm:px-8 py-5 border-b border-white/5 bg-white/[0.01]">
                 <h3 className="text-xl font-bold flex items-center gap-2">⭐ Top Artists</h3>
                  <p className="text-zinc-400 text-sm mt-1">{isOwner ? `Your most listened artists ${periodLong}.` : `Their most listened artists ${periodLong}.`}</p>
               </div>
               <div className="p-4 sm:p-6 grid grid-cols-1 sm:grid-cols-2 gap-4">
                 {profile.stats?.topArtists?.length > 0 ? profile.stats.topArtists.map((artist: any, i: number) => (
                   <a 
                     key={i} 
                     href={artist.url} 
                     target="_blank"
                     rel="noreferrer"
                     className="bg-zinc-900/30 hover:bg-zinc-800/50 border border-white/5 hover:border-indigo-500/30 rounded-2xl p-4 flex flex-col items-center text-center transition-all group"
                   >
                     <div className="w-16 h-16 rounded-full bg-zinc-800 mb-3 overflow-hidden shadow-lg group-hover:scale-105 transition-transform">
                        {artist.image && !artist.image.includes("2a96cbd8b46e442fc41c2b86b821562f") ? (
                          <img src={artist.image} alt={artist.name} className="w-full h-full object-cover" />
                        ) : (
                          <div className="w-full h-full flex items-center justify-center text-2xl bg-zinc-800">🎤</div>
                        )}
                     </div>
                     <div className="font-bold text-sm text-white group-hover:text-indigo-400 transition-colors line-clamp-1 w-full">{artist.name}</div>
                     <div className="text-xs text-zinc-500 font-medium mt-1">{parseInt(artist.playcount).toLocaleString()} plays</div>
                   </a>
                 )) : (
                   <div className="col-span-2 text-center py-8 text-zinc-500">No top artists found.</div>
                 )}
               </div>
            </div>

            {/* Recent Tracks List */}
            <div className="bg-zinc-950/40 backdrop-blur-3xl border border-white/5 rounded-3xl overflow-hidden shadow-2xl relative">
               <div className="px-8 py-6 border-b border-white/5 bg-white/[0.01]">
                 <h3 className="text-xl font-bold flex items-center gap-2">🎧 Recent Tracks</h3>
                 <p className="text-zinc-400 text-sm mt-1">{isOwner ? "What you've been listening to lately." : "What they've been listening to lately."}</p>
               </div>
               <div className="divide-y divide-white/5">
                 {profile.stats?.recentTracks?.length > 0 ? profile.stats.recentTracks.map((track: any, i: number) => (
                   <button 
                     key={i} 
                     onClick={() => setSelectedTrack(track)}
                     className="w-full text-left flex items-center gap-4 p-5 hover:bg-white/[0.02] transition-colors group"
                   >
                     <div className="w-12 h-12 rounded-lg bg-zinc-800 shrink-0 overflow-hidden shadow-md">
                       {track.image ? (
                         <img src={track.image} alt="Album Art" className="w-full h-full object-cover" />
                       ) : (
                         <div className="w-full h-full flex items-center justify-center text-xl">🎵</div>
                       )}
                     </div>
                     <div className="flex-1 min-w-0">
                       <div className="font-bold text-sm text-white truncate group-hover:text-indigo-400 transition-colors flex items-center gap-2">
                         {track.name}
                         {track.nowPlaying && (
                           <span className="shrink-0 flex items-center gap-1 bg-green-500/10 text-green-400 px-2 py-0.5 rounded text-[10px] uppercase font-bold tracking-wider border border-green-500/20">
                             <span className="w-1.5 h-1.5 bg-green-500 rounded-full animate-pulse" />
                             Playing
                           </span>
                         )}
                       </div>
                       <div className="text-xs text-zinc-400 truncate mt-1">{track.artist}</div>
                     </div>
                     {!track.nowPlaying && track.date && (
                       <div className="text-[10px] text-zinc-500 whitespace-nowrap shrink-0">
                         {new Date(parseInt(track.date) * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}
                       </div>
                     )}
                   </button>
                 )) : (
                   <div className="text-center py-8 text-zinc-500">No recent tracks found.</div>
                 )}
               </div>
            </div>

            {/* Top Albums Grid */}
            <div className="bg-zinc-950/40 backdrop-blur-3xl border border-white/5 rounded-3xl overflow-hidden shadow-2xl relative">
               <div className="px-6 sm:px-8 py-5 border-b border-white/5 bg-white/[0.01]">
                 <h3 className="text-xl font-bold flex items-center gap-2"><img src="https://cdn.discordapp.com/emojis/1527125818713837701.gif" alt="VinylRecord" className="w-6 h-6 inline-block" /> Top Albums</h3>
                  <p className="text-zinc-400 text-sm mt-1">{isOwner ? `Your most listened albums ${periodLong}.` : `Their most listened albums ${periodLong}.`}</p>
               </div>
               <div className="p-4 sm:p-6 grid grid-cols-1 sm:grid-cols-2 gap-4">
                 {profile.stats?.topAlbums?.length > 0 ? profile.stats.topAlbums.map((album: any, i: number) => (
                   <a 
                     key={i} 
                     href={album.url} 
                     target="_blank"
                     rel="noreferrer"
                     className="bg-zinc-900/30 hover:bg-zinc-800/50 border border-white/5 hover:border-indigo-500/30 rounded-2xl p-4 flex flex-col items-center text-center transition-all group"
                   >
                     <div className="w-16 h-16 rounded-xl bg-zinc-800 mb-3 overflow-hidden shadow-lg group-hover:scale-105 transition-transform">
                        {album.image ? (
                          <img src={album.image} alt={album.name} className="w-full h-full object-cover" />
                        ) : (
                          <div className="w-full h-full flex items-center justify-center bg-zinc-800"><img src="https://cdn.discordapp.com/emojis/1527125818713837701.gif" alt="VinylRecord" className="w-8 h-8 opacity-50" /></div>
                        )}
                     </div>
                     <div className="font-bold text-sm text-white group-hover:text-indigo-400 transition-colors line-clamp-1 w-full">{album.name}</div>
                     <div className="text-xs text-zinc-400 truncate mt-0.5 w-full">{album.artist}</div>
                     <div className="text-[10px] text-zinc-500 font-medium mt-1 uppercase tracking-wider">{parseInt(album.playcount).toLocaleString()} plays</div>
                   </a>
                 )) : (
                   <div className="col-span-2 text-center py-8 text-zinc-500">No top albums found.</div>
                 )}
               </div>
            </div>

            {/* Top Tracks List */}
            <div className="bg-zinc-950/40 backdrop-blur-3xl border border-white/5 rounded-3xl overflow-hidden shadow-2xl relative">
               <div className="px-8 py-6 border-b border-white/5 bg-white/[0.01]">
                 <h3 className="text-xl font-bold flex items-center gap-2">🔥 Top Tracks</h3>
                  <p className="text-zinc-400 text-sm mt-1">{isOwner ? `Your most played songs ${periodLong}.` : `Their most played songs ${periodLong}.`}</p>
               </div>
               <div className="divide-y divide-white/5">
                 {profile.stats?.topTracks?.length > 0 ? profile.stats.topTracks.map((track: any, i: number) => (
                   <button 
                     key={i} 
                     onClick={() => setSelectedTrack(track)}
                     className="w-full text-left flex items-center gap-4 p-5 hover:bg-white/[0.02] transition-colors group"
                   >
                     <div className="w-12 h-12 rounded-lg bg-zinc-800 shrink-0 overflow-hidden shadow-md">
                       {track.image ? (
                         <img src={track.image} alt="Album Art" className="w-full h-full object-cover" />
                       ) : (
                         <div className="w-full h-full flex items-center justify-center text-xl">🎵</div>
                       )}
                     </div>
                     <div className="flex-1 min-w-0">
                       <div className="font-bold text-sm text-white truncate group-hover:text-indigo-400 transition-colors flex items-center gap-2">
                         {track.name}
                       </div>
                       <div className="text-xs text-zinc-400 truncate mt-1">{track.artist}</div>
                     </div>
                     <div className="text-xs font-bold text-indigo-400 whitespace-nowrap shrink-0">
                       {parseInt(track.playcount).toLocaleString()}
                       <span className="text-[10px] text-zinc-500 uppercase tracking-widest ml-1 font-normal">plays</span>
                     </div>
                   </button>
                 )) : (
                   <div className="text-center py-8 text-zinc-500">No top tracks found.</div>
                 )}
               </div>
             </div>
           </div>
          </>
         )}

         {isOwner && activeTab === "profile" && profileError && (
          <div className="text-center p-8 text-zinc-500 bg-zinc-900/30 rounded-3xl border border-white/5 border-dashed">
            {profileError}
          </div>
        )}

        {/* --- SETTINGS TAB --- */}
        {isOwner && activeTab === "settings" && (
          <div className="grid lg:grid-cols-2 gap-8 items-start animate-fade-in">
            {/* LEFT COLUMN */}
            <div className="space-y-8">
              {/* Display Layout Card */}
              <div className="bg-zinc-950/40 backdrop-blur-3xl border border-white/5 rounded-3xl overflow-hidden shadow-2xl relative">
                <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-indigo-500 to-purple-500 opacity-20" />
                <div className="px-8 py-6 border-b border-white/5 bg-white/[0.01]">
                  <h3 className="text-xl font-bold">Display Layout</h3>
                  <p className="text-zinc-400 text-sm mt-1">Select the default style for your Last.fm embeds.</p>
                </div>
                
                <div className="p-8">
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                    {[
                      { id: "compact", icon: "📝", title: "Compact", desc: "1-line minimal view" },
                      { id: "full", icon: "🖼️", title: "Full Embed", desc: "Detailed visual view" },
                      { id: "stats", icon: "📊", title: "Stats View", desc: "Statistics & charts" }
                    ].map((mode) => (
                      <button
                        key={mode.id}
                        onClick={() => setUnsavedFmMode(mode.id as any)}
                        className={`relative flex flex-col items-start p-6 rounded-2xl border transition-all duration-300 text-left group ${
                          unsavedFmMode === mode.id
                            ? "bg-indigo-500/10 border-indigo-500 shadow-[0_0_30px_rgba(99,102,241,0.15)] sm:scale-105 z-10"
                            : "bg-zinc-900/30 border-white/5 hover:border-white/20 hover:bg-zinc-900/50"
                        }`}
                      >
                        <div className={`text-2xl mb-4 transition-transform duration-300 ${unsavedFmMode === mode.id ? 'scale-110' : 'group-hover:scale-110'}`}>{mode.icon}</div>
                        <div className={`text-lg font-bold ${unsavedFmMode === mode.id ? 'text-indigo-300' : 'text-white'}`}>{mode.title}</div>
                        <div className="text-sm text-zinc-500 mt-1">{mode.desc}</div>
                        
                        <div className={`absolute top-4 right-4 w-5 h-5 rounded-full border-2 flex items-center justify-center transition-colors ${unsavedFmMode === mode.id ? 'border-indigo-500' : 'border-zinc-700'}`}>
                          {unsavedFmMode === mode.id && <div className="w-2.5 h-2.5 bg-indigo-500 rounded-full animate-fade-in"></div>}
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {/* Data Source & Timezone */}
              <div className="bg-zinc-950/40 backdrop-blur-3xl border border-white/5 rounded-3xl shadow-2xl relative z-20">
                <div className="px-8 py-6 border-b border-white/5 bg-white/[0.01] rounded-t-3xl">
                  <h3 className="text-xl font-bold">Localization</h3>
                  <p className="text-zinc-400 text-sm mt-1">Configure where the bot pulls stats and resets.</p>
                </div>
                
                <div className="divide-y divide-white/5">
                  <div className="p-8 flex flex-col gap-4">
                    <div>
                      <div className="text-lg font-semibold text-white mb-1">Active Data Source</div>
                      <div className="text-sm text-zinc-400">Choose between Last.fm, imported Spotify data, or combined.</div>
                    </div>
                    <CustomSelect 
                      value={unsavedDataSource} 
                      onChange={(val) => setUnsavedDataSource(val as any)}
                      options={[
                        { value: "combined", label: "Combined" },
                        { value: "lastfm_only", label: "Last.fm Only" },
                        { value: "imported_only", label: "Imported Only" }
                      ]}
                    />
                  </div>

                  <div className="p-8 flex flex-col gap-4">
                    <div>
                      <div className="text-lg font-semibold text-white mb-1">Timezone</div>
                      <div className="text-sm text-zinc-400">Set your local timezone so daily resets are accurate.</div>
                    </div>
                    <CustomSelect 
                      value={unsavedTimezone} 
                      onChange={(val) => setUnsavedTimezone(val)}
                      options={[
                        { value: "UTC", label: "UTC (Default)" },
                        { value: "America/New_York", label: "America/New_York" },
                        { value: "America/Chicago", label: "America/Chicago" },
                        { value: "America/Denver", label: "America/Denver" },
                        { value: "America/Los_Angeles", label: "America/Los_Angeles" },
                        { value: "Europe/London", label: "Europe/London" },
                        { value: "Europe/Berlin", label: "Europe/Berlin" },
                        { value: "Asia/Tokyo", label: "Asia/Tokyo" },
                        { value: "Australia/Sydney", label: "Australia/Sydney" }
                      ]}
                    />
                  </div>
                </div>
              </div>

              {/* Connected Accounts */}
              <div className="bg-zinc-950/40 backdrop-blur-3xl border border-white/5 rounded-3xl overflow-hidden shadow-2xl relative">
                <div className="px-8 py-6 border-b border-white/5 bg-white/[0.01]">
                  <h3 className="text-xl font-bold">Connected Accounts</h3>
                  <p className="text-zinc-400 text-sm mt-1">Music services linked to your Discord account.</p>
                </div>
                <div className="divide-y divide-white/5">
                  <div className="p-8 flex items-start sm:items-center justify-between gap-6">
                    <div className="flex-1">
                      <div className="text-lg font-semibold text-white mb-1">Last.fm</div>
                      <div className="text-sm text-zinc-400">
                        {profile?.lastfm_username ? <>Linked as <span className="text-white font-medium">{profile.lastfm_username}</span></> : "Not linked"}
                      </div>
                    </div>
                    {!profile?.lastfm_username && (
                      <span className="shrink-0 text-xs text-zinc-500">Link with /login in Discord</span>
                    )}
                  </div>
                  <div className="p-8 flex items-start sm:items-center justify-between gap-6">
                    <div className="flex-1">
                      <div className="text-lg font-semibold text-white mb-1">Spotify</div>
                      <div className="text-sm text-zinc-400">
                        {spotifyLinked === null ? "Checking…" : spotifyLinked ? "Linked — remote control, likes, and Music dashboard ready" : "Not linked"}
                      </div>
                    </div>
                    <div className="shrink-0">
                      {spotifyLinked === true ? (
                        <button
                          onClick={handleSpotifyDisconnect}
                          disabled={spotifyBusy}
                          className="px-5 py-2.5 bg-red-500/10 hover:bg-red-500/20 border border-red-500/20 text-red-300 text-sm font-bold rounded-xl transition-all disabled:opacity-50"
                        >
                          {spotifyBusy ? "Working…" : "Disconnect"}
                        </button>
                      ) : spotifyLinked === false && session?.user?.id ? (
                        <a
                          href={`/api/auth/spotify/login?discord_id=${session.user.id}`}
                          className="inline-block px-5 py-2.5 bg-green-500/15 hover:bg-green-500/25 border border-green-500/25 text-green-300 text-sm font-bold rounded-xl transition-all"
                        >
                          Connect
                        </a>
                      ) : null}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* RIGHT COLUMN */}
            <div className="space-y-8">
              {/* Profile Details Card */}
              <div className="bg-zinc-950/40 backdrop-blur-3xl border border-white/5 rounded-3xl overflow-hidden shadow-2xl relative">
                <div className="px-8 py-6 border-b border-white/5 bg-white/[0.01]">
                  <h3 className="text-xl font-bold">Profile Details</h3>
                  <p className="text-zinc-400 text-sm mt-1">Customize how you appear on the dashboard.</p>
                </div>
                <div className="p-8">
                  <label className="block text-lg font-semibold text-white mb-2">Custom Display Name</label>
                  <p className="text-sm text-zinc-400 mb-4">Overrides your Discord/Last.fm username on your public profile URL and page.</p>
                  <input
                    type="text"
                    placeholder={usernameParam}
                    value={unsavedDisplayName}
                    onChange={(e) => setUnsavedDisplayName(e.target.value)}
                    className="w-full bg-zinc-900 border border-white/10 rounded-lg px-4 py-3 text-white placeholder-zinc-600 focus:outline-none focus:border-indigo-500 transition-colors"
                  />
                </div>
              </div>

              {/* Feature Toggles Card */}
              <div className="bg-zinc-950/40 backdrop-blur-3xl border border-white/5 rounded-3xl overflow-hidden shadow-2xl relative">
                <div className="px-8 py-6 border-b border-white/5 bg-white/[0.01]">
                  <h3 className="text-xl font-bold">Feature & Privacy Toggles</h3>
                  <p className="text-zinc-400 text-sm mt-1">Fine-tune exactly how the bot behaves.</p>
                </div>
                
                <div className="divide-y divide-white/5">
                  <div className="p-8 flex items-start sm:items-center justify-between gap-6 hover:bg-white/[0.02] transition-colors">
                    <div className="flex-1">
                      <div className="text-lg font-semibold text-white mb-1">Show Track Playcounts</div>
                      <div className="text-sm text-zinc-400">
                        Display your personal playcount for the current track right in the Now Playing embed.
                      </div>
                    </div>
                    <div className="shrink-0 pt-1 sm:pt-0">
                      <ToggleSwitch checked={unsavedShowTrackPlaycount} onChange={() => setUnsavedShowTrackPlaycount(!unsavedShowTrackPlaycount)} />
                    </div>
                  </div>

                  <div className="p-8 flex items-start sm:items-center justify-between gap-6 hover:bg-white/[0.02] transition-colors">
                    <div className="flex-1">
                      <div className="text-lg font-semibold text-white mb-1">Show Featured Artists</div>
                      <div className="text-sm text-zinc-400">
                        Automatically extract featured artists from the track name and display them distinctly.
                      </div>
                    </div>
                    <div className="shrink-0 pt-1 sm:pt-0">
                      <ToggleSwitch checked={unsavedShowFeatures} onChange={() => setUnsavedShowFeatures(!unsavedShowFeatures)} />
                    </div>
                  </div>

                  <div className="p-8 flex items-start sm:items-center justify-between gap-6 hover:bg-white/[0.02] transition-colors">
                    <div className="flex-1">
                      <div className="text-lg font-semibold text-white mb-1 flex items-center gap-3">
                        Private Mode 
                        <span className="text-[10px] uppercase font-bold bg-amber-500/10 text-amber-500 px-2.5 py-0.5 rounded-full border border-amber-500/20 shadow-[0_0_10px_rgba(245,158,11,0.1)]">Privacy</span>
                      </div>
                      <div className="text-sm text-zinc-400">
                        Hide your Last.fm username and profile link from bot embeds and disable public profile.
                      </div>
                    </div>
                    <div className="shrink-0 pt-1 sm:pt-0">
                      <ToggleSwitch checked={unsavedPrivateMode} onChange={() => setUnsavedPrivateMode(!unsavedPrivateMode)} />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* --- SUGGESTIONS TAB --- */}
        {isOwner && activeTab === "suggestions" && (
          <div className="max-w-3xl mx-auto space-y-8 animate-fade-in">
            {/* Submit Suggestion Card */}
            <div className="bg-zinc-950/40 backdrop-blur-3xl border border-white/5 rounded-3xl overflow-hidden shadow-2xl relative">
              <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-emerald-500 to-teal-500 opacity-20" />
              <div className="px-8 py-6 border-b border-white/5 bg-white/[0.01]">
                <h3 className="text-xl font-bold flex items-center gap-2">💡 Submit Feedback</h3>
                <p className="text-zinc-400 text-sm mt-1">Have an idea for the bot or dashboard? Let the developer know.</p>
              </div>
              <form onSubmit={submitSuggestion} className="p-8 space-y-6">
                <div>
                  <label className="block text-sm font-semibold text-zinc-300 mb-2">Idea Title</label>
                  <input 
                    required
                    type="text" 
                    value={newSuggestionTitle}
                    onChange={(e) => setNewSuggestionTitle(e.target.value)}
                    placeholder="e.g. Add a new embed layout"
                    className="w-full bg-zinc-900/50 border border-white/10 rounded-xl px-4 py-3 text-white focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-all placeholder:text-zinc-600 shadow-inner"
                  />
                </div>
                <div>
                  <label className="block text-sm font-semibold text-zinc-300 mb-2">Description</label>
                  <textarea 
                    required
                    value={newSuggestionDesc}
                    onChange={(e) => setNewSuggestionDesc(e.target.value)}
                    placeholder="Describe how it should work..."
                    rows={4}
                    className="w-full bg-zinc-900/50 border border-white/10 rounded-xl px-4 py-3 text-white focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-all placeholder:text-zinc-600 shadow-inner"
                  />
                </div>
                <button 
                  type="submit"
                  disabled={submittingSuggestion}
                  className="px-6 py-3 bg-emerald-500 hover:bg-emerald-600 text-white font-bold rounded-xl transition-all shadow-[0_0_20px_rgba(16,185,129,0.3)] hover:shadow-[0_0_25px_rgba(16,185,129,0.5)] disabled:opacity-50 disabled:shadow-none flex items-center gap-2"
                >
                  {submittingSuggestion ? "Submitting..." : "Send Suggestion"}
                </button>
              </form>
            </div>

            {/* My Suggestions List */}
            <div className="bg-zinc-950/40 backdrop-blur-3xl border border-white/5 rounded-3xl overflow-hidden shadow-2xl relative">
              <div className="px-8 py-6 border-b border-white/5 bg-white/[0.01]">
                <h3 className="text-xl font-bold">My Submissions</h3>
                <p className="text-zinc-400 text-sm mt-1">Track the status of your ideas submitted from Discord or the Web.</p>
              </div>
              <div className="divide-y divide-white/5">
                {suggestionsLoading ? (
                  <div className="p-8 text-center text-zinc-500">Loading...</div>
                ) : suggestions.length === 0 ? (
                  <div className="p-8 text-center text-zinc-500 italic bg-white/[0.01]">You haven't submitted any suggestions yet.</div>
                ) : (
                  suggestions.map((s) => (
                    <div key={s.id} className="p-8 hover:bg-white/[0.02] transition-colors">
                      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4 mb-4">
                        <div>
                          <h4 className="text-lg font-bold text-white">{s.title}</h4>
                          <p className="text-xs text-zinc-500 mt-1">{new Date(s.created_at).toLocaleString()}</p>
                        </div>
                        <div className="shrink-0">{getStatusBadge(s.status, s.title)}</div>
                      </div>
                      <p className="text-zinc-400 text-sm mb-5 leading-relaxed bg-zinc-900/30 p-5 rounded-2xl border border-white/5">
                        {s.description}
                      </p>
                      {s.admin_feedback && (
                        <div className="bg-indigo-500/10 border border-indigo-500/20 p-5 rounded-2xl relative shadow-[0_0_15px_rgba(99,102,241,0.05)]">
                          <div className="absolute -left-1.5 top-6 w-3 h-3 bg-[#13131c] rotate-45 border-l border-t border-indigo-500/20"></div>
                          <h5 className="text-indigo-400 text-xs font-bold uppercase tracking-widest mb-2">Developer Reply</h5>
                          <p className="text-indigo-100 text-sm leading-relaxed">{s.admin_feedback}</p>
                        </div>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        )}
        {/* --- IMPORT TAB --- */}
        {isOwner && activeTab === "import" && (
          <div className="bg-zinc-900/50 backdrop-blur-xl border border-white/10 rounded-3xl p-6 sm:p-8 animate-fade-in-up">
            <h2 className="text-2xl font-black text-white mb-2 flex items-center gap-3">
              <span className="text-amber-400">📥</span> Import Data
            </h2>
            <p className="text-zinc-400 mb-8 max-w-2xl">
              Upload your Spotify Extended Streaming History or Apple Music Play Activity here. The data will be chunked and securely processed by the bot in the background.
            </p>

            {importLocked && (
              <div className="mb-6 p-6 bg-red-500/10 border border-red-500/20 rounded-2xl flex items-center gap-4">
                <div className="w-12 h-12 bg-red-500/20 rounded-full flex items-center justify-center text-2xl shrink-0">🔒</div>
                <div>
                  <h3 className="text-red-400 font-bold text-lg mb-1">Imports Disabled</h3>
                  <p className="text-red-300/80 text-sm">{importLockReason || "The bot owner has temporarily disabled history imports."}</p>
                </div>
              </div>
            )}

            <div className="space-y-6">
              <div className="p-6 bg-zinc-900 border border-white/5 rounded-2xl">
                <input
                  type="file"
                  accept=".zip,.json,.csv"
                  disabled={importLocked}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) setImportFile(file);
                  }}
                  className="block w-full text-sm text-zinc-400 file:mr-4 file:py-3 file:px-6 file:rounded-xl file:border-0 file:text-sm file:font-bold file:bg-amber-500/20 file:text-amber-300 hover:file:bg-amber-500/30 transition-colors"
                />
                {importFile && (
                  <p className="mt-4 text-sm text-zinc-300">
                    Selected file: <span className="font-bold text-white">{importFile.name}</span> ({(importFile.size / 1024 / 1024).toFixed(2)} MB)
                  </p>
                )}
                
                <div className="mt-6">
                  <button
                    onClick={handleImportUpload}
                    disabled={!importFile || importStatus === "uploading" || importStatus === "complete" || importLocked}
                    className={`w-full py-4 rounded-xl font-bold text-lg transition-all duration-300 ${!importFile || importStatus === "uploading" || importStatus === "complete" || importLocked ? "bg-zinc-800 text-zinc-500 cursor-not-allowed" : "bg-amber-500 hover:bg-amber-600 text-black shadow-[0_0_20px_rgba(245,158,11,0.3)]"}`}
                  >
                    {importLocked ? "Imports Disabled" : importStatus === "uploading" ? "Uploading..." : "Start Import"}
                  </button>
                </div>
              </div>

              {importStatus === "uploading" && (
                <div className="p-6 bg-amber-500/10 border border-amber-500/20 rounded-2xl animate-pulse">
                  <p className="text-amber-300 font-bold mb-2 flex justify-between">
                    <span>⏳ Uploading... Please keep this tab open.</span>
                    <span>{importProgress}%</span>
                  </p>
                  <div className="w-full bg-black/50 rounded-full h-3 overflow-hidden">
                    <div className="bg-amber-500 h-3 rounded-full transition-all duration-300" style={{ width: `${importProgress}%` }}></div>
                  </div>
                </div>
              )}

              {importStatus === "complete" && (
                <div className="p-6 bg-green-500/10 border border-green-500/20 rounded-2xl flex items-center gap-4 animate-fade-in-up">
                  <div className="w-12 h-12 bg-green-500/20 rounded-full flex items-center justify-center text-2xl shrink-0">✅</div>
                  <div>
                    <h3 className="text-green-400 font-bold text-lg mb-1">Upload Complete!</h3>
                    <p className="text-green-300/80 text-sm">You can now safely close this website. The Discord bot is downloading and processing your data in the background and will DM you when it's finished.</p>
                  </div>
                </div>
              )}

              {importStatus === "error" && (
                <div className="p-6 bg-red-500/10 border border-red-500/20 rounded-2xl flex items-center gap-4">
                  <div className="w-12 h-12 bg-red-500/20 rounded-full flex items-center justify-center text-2xl shrink-0">❌</div>
                  <div>
                    <h3 className="text-red-400 font-bold text-lg mb-1">Upload Failed</h3>
                    <p className="text-red-300/80 text-sm">{importError}</p>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
        {/* --- ADMIN TAB (owner dashboard + admin role only) --- */}
        {isOwner && adminRole && activeTab === "admin" && (
          <div className="bg-zinc-900/50 backdrop-blur-xl border border-white/10 rounded-3xl p-6 sm:p-8 animate-fade-in">
            <AdminClient embedded />
          </div>
        )}
      </main>

      {/* Floating Save Pill (Only for owner when settings change) */}
      {isOwner && (
        <div className={`fixed bottom-8 left-1/2 -translate-x-1/2 transition-all duration-500 z-50 ${hasUnsavedChanges ? 'translate-y-0 opacity-100' : 'translate-y-24 opacity-0 pointer-events-none'}`}>
          <div className="bg-zinc-900/95 backdrop-blur-3xl border border-indigo-500/40 p-3 pr-4 rounded-full shadow-[0_10px_50px_rgba(99,102,241,0.3)] flex items-center gap-6">
            <div className="flex items-center gap-3 pl-4">
              <div className="w-2 h-2 bg-indigo-500 rounded-full animate-pulse shadow-[0_0_8px_rgba(99,102,241,0.8)]"></div>
              <div className="text-indigo-100 text-sm font-bold tracking-wide">UNSAVED CHANGES</div>
            </div>
            <div className="flex gap-2">
              <button 
                onClick={() => {
                  setUnsavedFmMode(fmMode);
                  setUnsavedShowFeatures(showFeatures);
                  setUnsavedPrivateMode(privateMode);
                  setUnsavedDataSource(dataSource);
                  setUnsavedTimezone(timezone);
                  setUnsavedShowTrackPlaycount(showTrackPlaycount);
                  setUnsavedDisplayName(displayName);
                }}
                className="px-4 py-2 text-xs font-bold text-zinc-400 hover:text-white hover:bg-white/10 rounded-full transition-all"
              >
                DISCARD
              </button>
              <button 
                onClick={saveSettings}
                disabled={savingSettings}
                className="px-6 py-2 bg-indigo-500 hover:bg-indigo-600 text-white text-xs font-bold rounded-full transition-all shadow-[0_0_20px_rgba(99,102,241,0.4)] hover:shadow-[0_0_25px_rgba(99,102,241,0.6)] disabled:opacity-50 disabled:shadow-none"
              >
                {savingSettings ? "SAVING..." : "SAVE"}
              </button>
            </div>
          </div>
        </div>
      )}
      {/* Track Info Modal */}
      <TrackModal
        isOpen={!!selectedTrack}
        onClose={() => setSelectedTrack(null)}
        trackName={selectedTrack?.name}
        artistName={selectedTrack?.artist}
        username={profile?.lastfm_username || usernameParam}
        fallbackImage={selectedTrack?.image}
      />
    </div>
  );
}
