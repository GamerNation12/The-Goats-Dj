import { NextResponse } from "next/server";
import postgres from "postgres";
import { discordAvatarUrl } from "@/lib/discord";

const DB_URL = process.env.DATABASE_URL || process.env.POSTGRES_URL;
const LASTFM_API_KEY = process.env.LASTFM_API_KEY || "eee299142ac5fe73e5eb5dcd1c29bcae";
const DISCORD_TOKEN = process.env.DISCORD_TOKEN || process.env.BOT_TOKEN;

export const revalidate = 300; // Cache for 5 minutes

export async function GET() {
  try {
    const sql = postgres(DB_URL!);
    
    // Fetch all public users (plus stored avatars as fallback when Discord rate-limits us)
    const rows = await sql`
      SELECT us.user_id, us.lastfm_username, us.discord_username, us.display_name, us.data_source, iu.avatar_url
      FROM user_settings us
      LEFT JOIN imported_users iu ON iu.id = us.user_id
      WHERE us.private_mode = FALSE 
      AND us.discord_username IS NOT NULL
    `;

    if (rows.length === 0) {
      return NextResponse.json({ success: true, leaderboard: [] });
    }

    // Fetch all imported plays grouped by user
    let importedPlaysMap = new Map<string, number>();
    try {
      const importedPlaysRes = await sql`SELECT user_id, COUNT(*) as count FROM listens GROUP BY user_id`;
      for (const r of importedPlaysRes) {
        importedPlaysMap.set(r.user_id, parseInt(r.count, 10));
      }
    } catch (e) {
      console.error("Failed to fetch imported plays:", e);
    }

    const leaderboard: any[] = [];

    // Fetch Last.fm and Discord Data concurrently
    await Promise.all(rows.map(async (r) => {
      let playcount = 0;
      let discordName = r.display_name || r.discord_username;
      let discordAvatar = null;
      const dataSource = r.data_source || 'combined';

      const hasImported = importedPlaysMap.has(r.user_id);
      if (!r.lastfm_username && !hasImported) return; // Skip users with zero data

      try {
        const lastfmFetchUrl = (r.lastfm_username && dataSource !== 'imported_only') 
          ? `http://ws.audioscrobbler.com/2.0/?method=user.getinfo&user=${r.lastfm_username}&api_key=${LASTFM_API_KEY}&format=json` 
          : null;

        const promises: any[] = [
          fetch(`https://discord.com/api/v10/users/${r.user_id}`, {
            headers: { Authorization: `Bot ${DISCORD_TOKEN}` },
            next: { revalidate: 3600 } 
          })
        ];

        if (lastfmFetchUrl) {
          promises.push(fetch(lastfmFetchUrl));
        }

        const [discordRes, lastfmRes] = await Promise.all(promises);

        let lastfmPlaycount = 0;
        if (lastfmRes && lastfmRes.ok) {
          const lData = await lastfmRes.json();
          if (!lData.error && lData.user) {
            lastfmPlaycount = parseInt(lData.user.playcount || "0", 10);
          }
        }

        const importedCount = importedPlaysMap.get(r.user_id) || 0;

        if (dataSource === 'imported_only') {
          playcount = importedCount;
        } else if (dataSource === 'lastfm_only') {
          playcount = lastfmPlaycount;
        } else {
          playcount = Math.max(lastfmPlaycount, importedCount);
        }

        if (discordRes.ok) {
          const dData = await discordRes.json();
          discordName = r.display_name || dData.global_name || dData.username || r.discord_username;
          discordAvatar = discordAvatarUrl(r.user_id, dData) || r.avatar_url || null;
        } else {
          // Discord rate-limited us — fall back to the stored avatar, not the site logo.
          discordAvatar = r.avatar_url || null;
        }
      } catch (e) {
        console.error(`Failed to fetch data for user ${r.user_id}:`, e);
      }

      if (playcount > 0) {
        leaderboard.push({
          userId: r.user_id,
          username: discordName,
          avatar: discordAvatar,
          lastfm_username: r.lastfm_username || "Local Import",
          playcount: playcount
        });
      }
    }));

    // Sort by playcount descending
    leaderboard.sort((a, b) => b.playcount - a.playcount);

    return NextResponse.json({
      success: true,
      leaderboard: leaderboard
    });

  } catch (error) {
    console.error("Leaderboard error:", error);
    return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
  }
}
