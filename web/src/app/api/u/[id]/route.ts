import { NextResponse } from "next/server";
import postgres from "postgres";
import { discordAvatarUrl } from "@/lib/discord";

const DB_URL = process.env.DATABASE_URL || process.env.POSTGRES_URL;
const LASTFM_API_KEY = process.env.LASTFM_API_KEY || "eee299142ac5fe73e5eb5dcd1c29bcae";
const DISCORD_TOKEN = process.env.DISCORD_TOKEN || process.env.BOT_TOKEN;

export const revalidate = 60; // Cache for 60 seconds

async function getDeezerArtistImage(artistName: string) {
  try {
    const res = await fetch(`https://api.deezer.com/search/artist?q=${encodeURIComponent(artistName)}`, {
      next: { revalidate: 86400 } // Cache artist image for 24 hours
    });
    if (!res.ok) return { url: null, error: `Search HTTP ${res.status}` };
    const data = await res.json();
    if (data.data?.length > 0) {
      const artist = data.data[0];
      if (artist.picture_xl || artist.picture_big || artist.picture) {
        return { url: artist.picture_xl || artist.picture_big || artist.picture, error: null };
      }
    }
    return { url: null, error: "Not found" };
  } catch (e: any) {
    return { url: null, error: e.message || String(e) };
  }
}

async function getDeezerTrackImage(trackName: string, artistName: string) {
  try {
    const res = await fetch(`https://api.deezer.com/search/track?q=${encodeURIComponent(trackName + " " + artistName)}`, {
      next: { revalidate: 86400 } // Cache track image for 24 hours
    });
    if (!res.ok) return { url: null, error: `Search HTTP ${res.status}` };
    const data = await res.json();
    if (data.data?.length > 0) {
      const track = data.data[0];
      if (track.album?.cover_xl || track.album?.cover_big || track.album?.cover) {
        return { url: track.album?.cover_xl || track.album?.cover_big || track.album?.cover, error: null };
      }
    }
    return { url: null, error: "Not found" };
  } catch (e: any) {
    return { url: null, error: e.message || String(e) };
  }
}

export async function GET(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id: rawUserId } = await params;
  const userId = decodeURIComponent(rawUserId);

  // Listening period for top stats. Last.fm natively supports these values;
  // imported (DB) data is filtered by played_at cutoff for the same ranges.
  const VALID_PERIODS = ["7day", "1month", "3month", "6month", "12month", "overall"] as const;
  const PERIOD_DAYS: Record<string, number | null> = {
    "7day": 7,
    "1month": 30,
    "3month": 91,
    "6month": 182,
    "12month": 365,
    "overall": null,
  };
  let period: (typeof VALID_PERIODS)[number] = "overall";
  try {
    const p = new URL(req.url).searchParams.get("period");
    if (p && (VALID_PERIODS as readonly string[]).includes(p)) period = p as typeof period;
  } catch { /* default overall */ }
  const cutoffDays = PERIOD_DAYS[period];
  const cutoffDate = cutoffDays != null ? new Date(Date.now() - cutoffDays * 24 * 60 * 60 * 1000) : null;

  try {
    const sql = postgres(DB_URL!);
    
    let rows;
    try {
      rows = await sql`
        SELECT user_id, lastfm_username, private_mode, data_source, discord_username, display_name, is_banned, ban_reason 
        FROM user_settings 
        WHERE REPLACE(REPLACE(discord_username, ' ', ''), '-', '') ILIKE REPLACE(REPLACE(${userId}, ' ', ''), '-', '') 
           OR REPLACE(REPLACE(lastfm_username, ' ', ''), '-', '') ILIKE REPLACE(REPLACE(${userId}, ' ', ''), '-', '') 
           OR REPLACE(REPLACE(display_name, ' ', ''), '-', '') ILIKE REPLACE(REPLACE(${userId}, ' ', ''), '-', '')
      `;
    } catch (e: any) {
      if (e.message?.includes('column "discord_username" does not exist') || e.code === '42703') {
        await sql`ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS discord_username TEXT`;
        await sql`
          UPDATE user_settings 
          SET discord_username = (
            SELECT username FROM website_logs 
            WHERE website_logs.user_id = user_settings.user_id 
            AND website_logs.action = 'Website Login'
            ORDER BY timestamp DESC LIMIT 1
          ) 
          WHERE discord_username IS NULL
        `;
        rows = await sql`
          SELECT user_id, lastfm_username, private_mode, data_source, discord_username, display_name, is_banned, ban_reason 
          FROM user_settings 
          WHERE REPLACE(REPLACE(discord_username, ' ', ''), '-', '') ILIKE REPLACE(REPLACE(${userId}, ' ', ''), '-', '') 
             OR REPLACE(REPLACE(lastfm_username, ' ', ''), '-', '') ILIKE REPLACE(REPLACE(${userId}, ' ', ''), '-', '') 
             OR REPLACE(REPLACE(display_name, ' ', ''), '-', '') ILIKE REPLACE(REPLACE(${userId}, ' ', ''), '-', '')
        `;
      } else {
        throw e;
      }
    }

    if (rows.length === 0) {
      return NextResponse.json({ error: "User not found or has not set up the bot." }, { status: 404 });
    }

    const publicRows = rows.filter(r => !r.private_mode);

    if (publicRows.length === 0) {
      return NextResponse.json({ error: "This profile is private." }, { status: 403 });
    }

    if (publicRows[0].is_banned) {
      return NextResponse.json({ error: `This user is banned. Reason: ${publicRows[0].ban_reason || 'No reason provided'}` }, { status: 403 });
    }

    const lastfm_username = publicRows[0].lastfm_username;
    const data_source = publicRows[0].data_source || 'combined';
    const uId = publicRows[0].user_id;

    const listensCheck = await sql`SELECT 1 FROM listens WHERE user_id = ${uId} LIMIT 1`;
    const hasImported = listensCheck.length > 0;

    if (!lastfm_username && !hasImported) {
      return NextResponse.json({ error: "This user has no data." }, { status: 404 });
    }

    // Fetch Discord Info
    let discordUsers: any[] = [];
    await Promise.all(publicRows.map(async (r) => {
      let discordUser = { name: "Unknown User", avatar: null as string | null };
      try {
        const discordRes = await fetch(`https://discord.com/api/v10/users/${r.user_id}`, {
          headers: { Authorization: `Bot ${DISCORD_TOKEN}` },
          next: { revalidate: 3600 } 
        });
        if (discordRes.ok) {
          const dData = await discordRes.json();
          discordUser.name = dData.global_name || dData.username;
          discordUser.avatar = discordAvatarUrl(r.user_id, dData);
        }
      } catch (e) {
        console.error("Failed to fetch discord user:", e);
      }
      discordUsers.push(discordUser);
    }));

    if (publicRows[0].display_name) {
      discordUsers[0].name = publicRows[0].display_name;
    }

    let importedData = { playcount: 0, topArtists: [] as any[], recentTracks: [] as any[], topTracks: [] as any[] };
    let lastfmData = { playcount: 0, topArtists: [] as any[], recentTracks: [] as any[], topTracks: [] as any[], topAlbums: [] as any[] };
    let debugLogs: any[] = [];

    // Fetch Imported Data (optionally scoped to the requested period)
    if (hasImported && data_source !== 'lastfm_only') {
      try {
        const [playcountRes, topArtistsRes, topTracksRes, recentTracksRes] = await Promise.all([
          cutoffDate
            ? sql`SELECT COUNT(*) as count FROM listens WHERE user_id = ${uId} AND played_at >= ${cutoffDate}`
            : sql`SELECT COUNT(*) as count FROM listens WHERE user_id = ${uId}`,
          cutoffDate
            ? sql`SELECT t.artist_name, COUNT(*) as playcount FROM listens l JOIN tracks t ON l.track_id = t.id WHERE l.user_id = ${uId} AND l.played_at >= ${cutoffDate} GROUP BY t.artist_name ORDER BY playcount DESC LIMIT 50`
            : sql`SELECT t.artist_name, COUNT(*) as playcount FROM listens l JOIN tracks t ON l.track_id = t.id WHERE l.user_id = ${uId} GROUP BY t.artist_name ORDER BY playcount DESC LIMIT 50`,
          cutoffDate
            ? sql`SELECT t.track_name, t.artist_name, COUNT(*) as playcount FROM listens l JOIN tracks t ON l.track_id = t.id WHERE l.user_id = ${uId} AND l.played_at >= ${cutoffDate} GROUP BY t.track_name, t.artist_name ORDER BY playcount DESC LIMIT 50`
            : sql`SELECT t.track_name, t.artist_name, COUNT(*) as playcount FROM listens l JOIN tracks t ON l.track_id = t.id WHERE l.user_id = ${uId} GROUP BY t.track_name, t.artist_name ORDER BY playcount DESC LIMIT 50`,
          sql`SELECT t.track_name, t.artist_name, l.played_at FROM listens l JOIN tracks t ON l.track_id = t.id WHERE l.user_id = ${uId} ORDER BY l.played_at DESC LIMIT 50`
        ]);

        importedData.playcount = parseInt(playcountRes[0]?.count || "0", 10);
        importedData.topArtists = topArtistsRes.map(row => ({ name: row.artist_name, playcount: parseInt(row.playcount, 10), url: `https://www.last.fm/music/${encodeURIComponent(row.artist_name)}`, image: null }));
        importedData.topTracks = topTracksRes.map(row => ({ name: row.track_name, artist: row.artist_name, playcount: parseInt(row.playcount, 10), url: `https://www.last.fm/music/${encodeURIComponent(row.artist_name)}/_/${encodeURIComponent(row.track_name)}`, image: null }));
        importedData.recentTracks = recentTracksRes.map(row => ({ name: row.track_name, artist: row.artist_name, album: null, url: `https://www.last.fm/music/${encodeURIComponent(row.artist_name)}/_/${encodeURIComponent(row.track_name)}`, image: null, nowPlaying: false, date: Math.floor(new Date(row.played_at).getTime() / 1000).toString() }));
      } catch (e) {
        console.error("Imported plays fetch error:", e);
      }
    }

    // Fetch Lastfm Data (tops scoped to the requested period; recents always latest)
    if (lastfm_username && data_source !== 'imported_only') {
      try {
        const periodParam = period === "overall" ? "" : `&period=${period}`;
        const [infoRes, artistRes, recentRes, tracksRes, albumsRes] = await Promise.all([
          fetch(`http://ws.audioscrobbler.com/2.0/?method=user.getinfo&user=${lastfm_username}&api_key=${LASTFM_API_KEY}&format=json`),
          fetch(`http://ws.audioscrobbler.com/2.0/?method=user.gettopartists&user=${lastfm_username}&api_key=${LASTFM_API_KEY}&format=json&limit=12${periodParam}`),
          fetch(`http://ws.audioscrobbler.com/2.0/?method=user.getrecenttracks&user=${lastfm_username}&api_key=${LASTFM_API_KEY}&format=json&limit=10`),
          fetch(`http://ws.audioscrobbler.com/2.0/?method=user.gettoptracks&user=${lastfm_username}&api_key=${LASTFM_API_KEY}&format=json&limit=5${periodParam}`),
          fetch(`http://ws.audioscrobbler.com/2.0/?method=user.gettopalbums&user=${lastfm_username}&api_key=${LASTFM_API_KEY}&format=json&limit=6${periodParam}`)
        ]);

        const infoData = await infoRes.json();
        const artistData = await artistRes.json();
        const recentData = await recentRes.json();
        const tracksData = await tracksRes.json();
        const albumsData = await albumsRes.json();

        if (!infoData.error) lastfmData.playcount = parseInt(infoData.user.playcount || "0", 10);

        if (!artistData.error && artistData.topartists?.artist) {
          const artistsList = artistData.topartists.artist;
          for (const a of artistsList) {
            let imageUrl = a.image?.find((i: any) => i.size === "extralarge")?.["#text"] || null;
            if (imageUrl && (imageUrl.includes("2a96cbd8b46e442fc41c2b86b821562f") || imageUrl.includes("36bb9b7f5efbb0bb01f454bb86a0e603"))) imageUrl = null;
            lastfmData.topArtists.push({ name: a.name, playcount: parseInt(a.playcount, 10), url: a.url, image: imageUrl });
          }
        }

        if (!recentData.error && recentData.recenttracks?.track) {
          const tracks = Array.isArray(recentData.recenttracks.track) ? recentData.recenttracks.track : [recentData.recenttracks.track];
          for (const t of tracks) {
            let imageUrl = t.image?.find((i: any) => i.size === "extralarge" || i.size === "large")?.["#text"] || null;
            if (imageUrl && (imageUrl.includes("2a96cbd8b46e442fc41c2b86b821562f") || imageUrl.includes("36bb9b7f5efbb0bb01f454bb86a0e603"))) imageUrl = null;
            lastfmData.recentTracks.push({ name: t.name, artist: t.artist?.["#text"] || t.artist?.name, album: t.album?.["#text"], url: t.url, image: imageUrl, nowPlaying: t["@attr"]?.nowplaying === "true", date: t.date?.uts || null });
          }
        }

        if (!tracksData.error && tracksData.toptracks?.track) {
          const topTracksList = Array.isArray(tracksData.toptracks.track) ? tracksData.toptracks.track : [tracksData.toptracks.track];
          for (const t of topTracksList) {
            let imageUrl = t.image?.find((i: any) => i.size === "extralarge" || i.size === "large")?.["#text"] || null;
            if (imageUrl && (imageUrl.includes("2a96cbd8b46e442fc41c2b86b821562f") || imageUrl.includes("36bb9b7f5efbb0bb01f454bb86a0e603"))) imageUrl = null;
            lastfmData.topTracks.push({ name: t.name, artist: t.artist?.name, playcount: parseInt(t.playcount, 10), url: t.url, image: imageUrl });
          }
        }

        if (!albumsData.error && albumsData.topalbums?.album) {
          const topAlbums = Array.isArray(albumsData.topalbums.album) ? albumsData.topalbums.album : [albumsData.topalbums.album];
          const albumMap = new Map();
          for (const a of topAlbums) {
            let imageUrl = a.image?.find((i: any) => i.size === "extralarge")?.["#text"] || null;
            if (imageUrl && (imageUrl.includes("2a96cbd8b46e442fc41c2b86b821562f") || imageUrl.includes("36bb9b7f5efbb0bb01f454bb86a0e603"))) imageUrl = null;
            const key = `${a.name.toLowerCase()}|${a.artist?.name?.toLowerCase() || ""}`;
            if (!albumMap.has(key)) {
              albumMap.set(key, { name: a.name, artist: a.artist?.name, playcount: parseInt(a.playcount, 10), url: a.url, image: imageUrl });
            } else {
              albumMap.get(key).playcount = Math.max(albumMap.get(key).playcount, parseInt(a.playcount, 10));
            }
          }
          lastfmData.topAlbums = Array.from(albumMap.values());
        }
      } catch (e) {
        console.error("Last.fm fetch error:", e);
      }
    }

    let finalStats = { playcount: 0, topArtists: [] as any[], recentTracks: [] as any[], topTracks: [] as any[], topAlbums: [] as any[] };

    if (data_source === 'imported_only') {
      finalStats = { ...importedData, topAlbums: [] };
    } else if (data_source === 'lastfm_only') {
      finalStats = lastfmData;
    } else {
      finalStats.playcount = Math.max(lastfmData.playcount, importedData.playcount);
      finalStats.topAlbums = lastfmData.topAlbums;

      let artistMap = new Map();
      for (const a of lastfmData.topArtists) { artistMap.set(a.name.toLowerCase(), { ...a }); }
      for (const a of importedData.topArtists) {
        const key = a.name.toLowerCase();
        if (artistMap.has(key)) {
          artistMap.get(key).playcount = Math.max(artistMap.get(key).playcount, a.playcount);
        } else {
          artistMap.set(key, { ...a });
        }
      }
      finalStats.topArtists = Array.from(artistMap.values()).sort((a, b) => b.playcount - a.playcount).slice(0, 12);

      let trackMap = new Map();
      for (const t of lastfmData.topTracks) {
        const key = `${t.name.toLowerCase()}|${t.artist.toLowerCase()}`;
        trackMap.set(key, { ...t });
      }
      for (const t of importedData.topTracks) {
        const key = `${t.name.toLowerCase()}|${t.artist.toLowerCase()}`;
        if (trackMap.has(key)) {
          trackMap.get(key).playcount = Math.max(trackMap.get(key).playcount, t.playcount);
        } else {
          trackMap.set(key, { ...t });
        }
      }
      finalStats.topTracks = Array.from(trackMap.values()).sort((a, b) => b.playcount - a.playcount).slice(0, 5);

      let combinedRecents = [...lastfmData.recentTracks, ...importedData.recentTracks];
      combinedRecents.sort((a, b) => {
        if (a.nowPlaying) return -1;
        if (b.nowPlaying) return 1;
        return parseInt(b.date || "0") - parseInt(a.date || "0");
      });
      finalStats.recentTracks = combinedRecents.slice(0, 10);
    }

    // Fetch missing images
    for (const a of finalStats.topArtists) {
      if (!a.image) {
        const imgRes = await getDeezerArtistImage(a.name);
        a.image = imgRes?.url || null;
      }
    }
    for (const t of finalStats.recentTracks) {
      if (!t.image) {
        const imgRes = await getDeezerTrackImage(t.name, t.artist || "");
        t.image = imgRes?.url || null;
      }
    }
    for (const t of finalStats.topTracks) {
      if (!t.image) {
        const imgRes = await getDeezerTrackImage(t.name, t.artist || "");
        t.image = imgRes?.url || null;
      }
    }
    for (const a of finalStats.topAlbums) {
      if (!a.image) {
        const imgRes = await getDeezerTrackImage(a.name, a.artist || "");
        a.image = imgRes?.url || null;
      }
    }

    return NextResponse.json({
      success: true,
      period,
      lastfm_username: lastfm_username,
      users: discordUsers,
      stats: finalStats,
      _debug: debugLogs
    });

  } catch (error) {
    console.error("Public profile error:", error);
    return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
  }
}
