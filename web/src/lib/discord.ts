// Resolve a Discord avatar URL from a /users API payload.
// Falls back to Discord's own default avatar so callers never get null
// just because the user has no custom pfp.
export function discordAvatarUrl(userId: string, dData: any): string | null {
  try {
    if (dData?.avatar) {
      return `https://cdn.discordapp.com/avatars/${userId}/${dData.avatar}.png?size=256`;
    }
    const disc = dData?.discriminator;
    if (disc && disc !== "0") {
      const n = parseInt(disc, 10);
      if (Number.isFinite(n)) return `https://cdn.discordapp.com/embed/avatars/${n % 5}.png`;
    }
    return `https://cdn.discordapp.com/embed/avatars/${Number((BigInt(userId) >> BigInt(22)) % BigInt(6))}.png`;
  } catch {
    return null;
  }
}

export async function sendDiscordDM(userId: string, content: string, components?: any[], embeds?: any[]) {
  const token = process.env.DISCORD_TOKEN;
  if (!token) return false;

  try {
    // 1. Create DM channel
    const dmRes = await fetch("https://discord.com/api/v10/users/@me/channels", {
      method: "POST",
      headers: {
        "Authorization": `Bot ${token}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ recipient_id: userId })
    });

    if (!dmRes.ok) return false;
    const dmData = await dmRes.json();
    const channelId = dmData.id;

    // 2. Send Message
    const msgRes = await fetch(`https://discord.com/api/v10/channels/${channelId}/messages`, {
      method: "POST",
      headers: {
        "Authorization": `Bot ${token}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ content, components, embeds })
    });

    return msgRes.ok;
  } catch (error) {
    console.error("Failed to send Discord DM:", error);
    return false;
  }
}
