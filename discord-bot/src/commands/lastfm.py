from src.core.theme import Theme
from src.core.config import Log
import discord
from discord.ext import commands
from discord import app_commands

from src.core.database import format_name


def _spotify_login_url(user_id: int, channel_id=None, message_id=None) -> str:
    import os
    app_url = os.getenv("NEXT_PUBLIC_APP_URL", "https://dj-scratch.vercel.app")
    url = f"{app_url}/api/auth/spotify?user_id={user_id}"
    # Passed through Spotify's state so the callback can refresh this message.
    if channel_id and message_id:
        url += f"&channel_id={channel_id}&message_id={message_id}"
    return url


async def _is_spotify_linked(user_id: int) -> bool:
    try:
        from src.core.database import get_user_spotify_refresh_token
        return bool(await get_user_spotify_refresh_token(user_id))
    except Exception:
        return False


def extract_artist_from_message(msg: discord.Message) -> str:
    import re
    
    if msg.embeds:
        extracted = extract_artist_from_embed(msg.embeds[0])
        if extracted: return extracted
        
    if msg.content:
        m = re.search(r'by \*\*([^*]+)\*\*', msg.content)
        if m: return m.group(1).strip()
        
        m = re.search(r'is listening to (?:(?:(?:.*?\]\(<.*?>\))|.*?)) by (.*)', msg.content)
        if m: return m.group(1).strip()
        
    return None

def extract_artist_from_embed(embed: discord.Embed) -> str:
    import re
    
    texts_to_check = []
    if embed.author and embed.author.name:
        texts_to_check.append(embed.author.name)
    if embed.title:
        texts_to_check.append(embed.title)
        
    for text in texts_to_check:
        m = re.search(r'(?i)Who knows (.+?) in ', text)
        if m: return m.group(1).strip()
        
        m = re.search(r"(?i)top \w+ for ['`‘\"]([^'`’”\"]+)['`’”\"]", text)
        if m: return m.group(1).strip()
        
        m = re.search(r"(?i)top \w+ for (.*)", text)
        if m: return m.group(1).strip()
        
        if ' - ' in text and not text.endswith("playing"):
            return text.split(' - ', 1)[0].strip()
            
    # 3. Check Description (Now Playing)
    if embed.description:
        lines = embed.description.split('\n')
        for line in lines:
            line = line.strip()
            
            # Match DJ Scratch and Chuu style: by **Artist**
            m = re.search(r'by \*\*([^*]+)\*\*', line, re.IGNORECASE)
            if m: return m.group(1).strip()
            
            # Match .fmbot and DJ Scratch stats style: **Artist** • *Album* or just **Artist**
            if line.startswith('**') and not line.startswith('**['):
                m = re.search(r'^\*\*([^*]+)\*\*', line)
                if m: return m.group(1).strip()
                
        # fallback to original logic for legacy formats
        if len(lines) >= 2:
            m = re.search(r'\*\*([^*]+)\*\*', lines[1])
            if m:
                return m.group(1).strip()
            
            line2 = lines[1].replace('**', '')
            for separator in [' | ', ' — ', ' - ']:
                if separator in line2:
                    return line2.split(separator)[0].strip()
                    
    # 4. Bulletproof fallback: search the raw embed JSON string
    try:
        raw_embed = str(embed.to_dict())
        m = re.search(r"(?i)top \w+ for ['`‘\"]([^'`’”\"]+)['`’”\"]", raw_embed)
        if m: return m.group(1).strip()
        
        m = re.search(r'(?i)Who knows (.+?) in ', raw_embed)
        if m: return m.group(1).strip()
    except:
        pass
                    
    return None

async def get_target_user(ctx, arg_string: str = None):
    target_user = ctx.author
    cleaned_args = arg_string
    
    if hasattr(ctx.message, 'reference') and ctx.message.reference and ctx.message.reference.message_id:
        try:
            if hasattr(ctx.message.reference, 'resolved') and isinstance(ctx.message.reference.resolved, discord.Message):
                msg = ctx.message.reference.resolved
            elif ctx.message.reference.cached_message:
                msg = ctx.message.reference.cached_message
            else:
                msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                
            if not msg.author.bot:
                target_user = msg.author
            else:
                # If replying to a bot (e.g. .fmbot or DJ Scratch), target user is still ctx.author
                # BUT we want to extract the artist from the embed if no args were provided!
                if (not cleaned_args or not cleaned_args.strip()):
                    extracted = extract_artist_from_message(msg)
                    if extracted:
                        cleaned_args = extracted
        except Exception:
            pass

    if ctx.message.mentions:
        for m in ctx.message.mentions:
            # If the mention is the bot we're replying to, ignore it
            is_reply_target = False
            if hasattr(ctx.message, 'reference') and ctx.message.reference and ctx.message.reference.resolved:
                if ctx.message.reference.resolved.author.id == m.id:
                    is_reply_target = True
                    
            if not is_reply_target and (not m.bot or m.id == ctx.bot.user.id):
                target_user = m
                break

    if cleaned_args and ctx.message.mentions:
        for m in ctx.message.mentions:
            cleaned_args = cleaned_args.replace(f'<@{m.id}>', '').replace(f'<@!{m.id}>', '').strip()

    if cleaned_args:
        first_word = cleaned_args.split()[0]
        if first_word.isdigit() and len(first_word) >= 17:
            try:
                uid = int(first_word)
                u = ctx.guild.get_member(uid) if ctx.guild else None
                if not u:
                    u = await ctx.bot.fetch_user(uid)
                target_user = u
                cleaned_args = cleaned_args[len(first_word):].strip()
            except Exception:
                pass

    if cleaned_args and not cleaned_args.strip():
        cleaned_args = None
            
    return target_user, cleaned_args



class LastFmCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _reply_and_delete(self, ctx, *args, **kwargs):
        kwargs['mention_author'] = False
        try:
            msg = await ctx.reply(*args, **kwargs)
        except Exception:
            kwargs.pop('mention_author', None)
            msg = await ctx.send(*args, **kwargs)
        
        try:
            await ctx.message.delete()
        except Exception:
            pass
        return msg

    @app_commands.command(name="cd", description="Check the bot's avatar cooldown and preview avatar")
    @app_commands.describe(last_song="Fetch the last completed song instead of the currently playing song")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cd_slash(self, interaction: discord.Interaction, last_song: bool = False):
        await interaction.response.defer(ephemeral=True)
        cd = await self.bot.get_avatar_cooldown()
        status_msg = f"⏳ Avatar is on cooldown for **{cd//60}m {cd%60}s**." if cd > 0 else "✅ Avatar is **ready** to be updated!"
        from src.core.events import get_lastfm_username, ApplyAvatarView, LASTFM_COLOR
        from src.utils.api import api_get
        from src.core.config import LASTFM_API_KEY
        try:
            username = await get_lastfm_username(interaction.user.id)
            if username:
                url = f"https://ws.audioscrobbler.com/2.0/?method=user.getrecenttracks&user={username}&api_key={LASTFM_API_KEY}&format=json&limit=2"
                data = await api_get(url)
                if data and 'recenttracks' in data and data['recenttracks']['track']:
                    tracks = data['recenttracks']['track']
                    t = tracks[0]
                    is_p = t.get('@attr', {}).get('nowplaying') == 'true'
                    
                    if last_song:
                        if is_p and len(tracks) > 1:
                            t = tracks[1]
                        is_p = True 
                    
                    if is_p:
                        artist, song, img = t['artist']['#text'], t['name'], t['image'][3]['#text']
                        album = t.get('album', {}).get('#text')
                        
                        try:
                            from src.core.spotify import get_spotify_track_info
                            session = getattr(self.bot, 'session', None)
                            if session:
                                s_info = await get_spotify_track_info(session, artist, song)
                                if s_info and s_info.get("image_url"):
                                    if not img or "2a96cbd8b46e442fc41c2b86b821562f" in img:
                                        img = s_info.get("image_url")
                        except Exception as e:
                            print(f"{Log.RED}>>> Spotify fetch error in cd_slash: {e}{Log.RESET}")

                        if img:
                            title = "Bot Avatar Preview (Last Played)" if last_song else "Bot Avatar Preview"
                            desc = f"Last track: **{song}** by **{artist}**" if last_song else f"Current track: **{song}** by **{artist}**"
                            preview_embed = Theme.get_embed(
                                title=title, 
                                description=desc, 
                                color=LASTFM_COLOR
                            )
                            preview_embed.set_author(name=format_name(interaction.user), icon_url=img)
                            
                            from src.utils.images import get_circular_pfp_file
                            pfp_file = await get_circular_pfp_file(img)
                            
                            view = ApplyAvatarView(self.bot, artist, img, original_user=interaction.user, track=song, album=album)
                            
                            if pfp_file:
                                preview_embed.set_image(url="attachment://pfp_preview.png")
                                msg = await interaction.followup.send(content=status_msg, file=pfp_file, embed=preview_embed, view=view, ephemeral=True, wait=True)
                            else:
                                preview_embed.set_image(url=img)
                                msg = await interaction.followup.send(content=status_msg, embed=preview_embed, view=view, ephemeral=True, wait=True)
                                
                            view.original_msg = msg
                            return
        except Exception as e:
            pass
            
        await interaction.followup.send(content=status_msg, ephemeral=True)

    @app_commands.command(name="privacy", description="Toggle privacy mode to hide your profile from public stats")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def privacy_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.toggle_privacy(interaction.user, interaction.followup.send)

    @commands.command(name="privacy", aliases=["pr", "priv"])
    async def privacy_prefix(self, ctx):
        await self.toggle_privacy(ctx.author, ctx.reply)

    async def toggle_privacy(self, user: discord.User, send_func):
        from src.core.events import db_pool
        if db_pool:
            try:
                async with db_pool.acquire() as conn:
                    row = await conn.fetchrow("SELECT private_mode FROM user_settings WHERE user_id=$1", str(user.id))
                    current = row['private_mode'] if row and 'private_mode' in row else False
                    new_state = not current
                    await conn.execute("""
                        INSERT INTO user_settings (user_id, private_mode) 
                        VALUES ($1, $2) 
                        ON CONFLICT (user_id) 
                        DO UPDATE SET private_mode=$2
                    """, str(user.id), new_state)
                    
                    state_str = "Enabled" if new_state else "Disabled"
                    desc = "Your profile is now hidden from the public dashboard and server stats." if new_state else "Your profile is now visible on the public dashboard and server stats."
                    color = discord.Color.red() if new_state else discord.Color.green()
                    
                    embed = Theme.get_embed(title=f"🔒 Privacy Mode {state_str}", description=desc, color=color)
                    await send_func(embed=embed)
            except Exception as e:
                print(f"Privacy toggle error: {e}")
                await send_func(content="Failed to update privacy settings. Please try again.")
        else:
            await send_func(content="Database is not available right now. Please try again later.")

    @app_commands.command(name="login", description="Securely login and link your Last.fm account")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def login_slash(self, interaction: discord.Interaction):
        # Defer FIRST: Discord only waits 3s for the first reply, and a busy
        # event loop can burn that before we even reach the DB call (10062).
        # Deferring buys 15 minutes; the real message goes via followup.
        await interaction.response.defer(ephemeral=True)
        try:
            from src.core.events import get_lastfm_username
            username = await get_lastfm_username(interaction.user.id)
            spotify_linked = await _is_spotify_linked(interaction.user.id)

            if username and spotify_linked:
                embed = Theme.get_embed(
                    title="✅ All Linked",
                    description=f"Last.fm linked as **{username}**.\n🎵 Spotify linked — remote control and the Music dashboard are ready.",
                    color=discord.Color.green()
                )
                return await interaction.followup.send(embed=embed, ephemeral=True)

            if username:
                embed = Theme.get_embed(
                    title="✅ Last.fm Linked",
                    description=f"You are logged in as **{username}**.\n\n"
                                "🎵 Spotify is **not** linked yet — add it for playback control (`/play`), likes, and the Music dashboard.\n\n"
                                "If you want to switch Last.fm accounts, use `/logout` first.",
                    color=discord.Color.green()
                )
                # Send first so we have a message id: the callback PATCHes this
                # message with fresh status once Spotify is linked.
                msg = await interaction.followup.send(embed=embed, ephemeral=True, wait=True)
                view = discord.ui.View()
                view.add_item(discord.ui.Button(
                    label="Login with Spotify",
                    url=_spotify_login_url(interaction.user.id, interaction.channel_id, msg.id),
                    emoji="🎵"))
                return await msg.edit(embed=embed, view=view)

            desc = ("**DJ Scratch uses Last.fm to track your listening history.**\n\n"
                    "Click a button below to link an account. You will be redirected to authorize the bot.\n\n"
                    "*(Don't have a Last.fm account? You'll need to [create one](https://www.last.fm/join) and link it to your Spotify first!)*")
            if spotify_linked:
                desc += "\n\n🎵 Spotify already linked."
            embed = Theme.get_embed(title="🔗 Connect Your Music", description=desc, color=discord.Color.red())

            import urllib.parse
            from src.core.config import LASTFM_API_KEY as _LASTFM_KEY
            cb_url = f"https://dj-scratch.vercel.app/login-callback/?discord_id={interaction.user.id}&interaction_token={interaction.token}&app_id={interaction.application_id}"
            auth_url = f"https://www.last.fm/api/auth/?api_key={_LASTFM_KEY}&cb={urllib.parse.quote(cb_url)}"

            # Send first so we have a message id: callbacks PATCH this message
            # with fresh status once an account is linked.
            msg = await interaction.followup.send(embed=embed, ephemeral=True, wait=True)
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="Login with Last.fm", url=auth_url, emoji="🔗"))
            view.add_item(discord.ui.Button(
                label="Login with Spotify",
                url=_spotify_login_url(interaction.user.id, interaction.channel_id, msg.id),
                emoji="🎵"))
            await msg.edit(embed=embed, view=view)
        except Exception as e:
            print(f"{Log.RED}>>> /login failed: {e}{Log.RESET}")
            try:
                await interaction.followup.send("❌ Something went wrong. Please try `/login` again.", ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="logout", description="Unlink your Last.fm or Spotify account from the bot")
    @app_commands.describe(service="Which account to unlink (default: Last.fm)")
    @app_commands.choices(service=[
        app_commands.Choice(name="Last.fm", value="lastfm"),
        app_commands.Choice(name="Spotify", value="spotify"),
    ])
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def logout_slash(self, interaction: discord.Interaction, service: app_commands.Choice[str] = None):
        # Same 3-second rule as /login: defer first, answer via followup.
        await interaction.response.defer(ephemeral=True)
        try:
            svc = service.value if service else "lastfm"
            if svc == "spotify":
                return await self._logout_spotify_slash(interaction)
            from src.core.database import unlink_user
            await unlink_user(interaction.user.id)
            embed = Theme.get_embed(
                title="👋 Logged Out",
                description="Your Last.fm account has been successfully unlinked from your Discord account.",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"{Log.RED}>>> /logout failed: {e}{Log.RESET}")
            try:
                await interaction.followup.send("❌ Something went wrong. Please try `/logout` again.", ephemeral=True)
            except Exception:
                pass

    async def _logout_spotify_slash(self, interaction: discord.Interaction):
        from src.core.database import clear_user_spotify
        if not await _is_spotify_linked(interaction.user.id):
            embed = Theme.get_embed(
                title="🎵 Spotify Not Linked",
                description="Your Spotify account isn't linked — nothing to disconnect.",
                color=discord.Color.blue()
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)
        ok = await clear_user_spotify(interaction.user.id)
        if ok:
            embed = Theme.get_embed(
                title="🔓 Spotify Disconnected",
                description="Your Spotify account has been unlinked.\n\nRemote control, likes, and the Music dashboard will stop working until you link it again with `/login`.",
                color=discord.Color.green()
            )
        else:
            embed = Theme.get_embed(
                title="❌ Disconnect Failed",
                description="Could not disconnect Spotify (database offline?). Please try again later.",
                color=discord.Color.red()
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="fm", description="View what you are currently listening to")
    @app_commands.describe(mode="Choose embed style")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Full Embed", value="full"),
        app_commands.Choice(name="Compact (1 line)", value="compact"),
        app_commands.Choice(name="Stats (Detailed)", value="stats"),
    ])
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def fm_slash(self, interaction: discord.Interaction, mode: app_commands.Choice[str] = None):
        await interaction.response.defer()
        if mode is not None:
            m = mode.value
        else:
            m = await self.bot.get_user_fm_mode(interaction.user.id)
            if not m: m = "full"
        
        result, is_p = await self.bot.process_fm(interaction, interaction.user, mode=m)
        if result is None:
            await interaction.edit_original_response(content=is_p)
        elif isinstance(result, dict):
            msg = await interaction.edit_original_response(**result)
            if is_p: await self.bot.add_custom_reactions(msg)

    @app_commands.command(name="topartists", description="View your top played artists")
    @app_commands.describe(period="The time frame to check")
    @app_commands.choices(period=[
        app_commands.Choice(name="7 Days", value="7d"), app_commands.Choice(name="1 Month", value="1m"),
        app_commands.Choice(name="3 Months", value="3m"), app_commands.Choice(name="6 Months", value="6m"),
        app_commands.Choice(name="1 Year", value="1y"), app_commands.Choice(name="All Time", value="all"),
    ])
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def ta_slash(self, interaction: discord.Interaction, period: app_commands.Choice[str] = None):
        await interaction.response.defer()
        embed, view, err = await self.bot.process_top_artists(interaction.user, period.value if period else 'all')
        if embed:
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.edit_original_response(content=err)

    @app_commands.command(name="toptracks", description="View your top played tracks")
    @app_commands.describe(period="The time frame to check")
    @app_commands.choices(period=[
        app_commands.Choice(name="7 Days", value="7d"), app_commands.Choice(name="1 Month", value="1m"),
        app_commands.Choice(name="3 Months", value="3m"), app_commands.Choice(name="6 Months", value="6m"),
        app_commands.Choice(name="1 Year", value="1y"), app_commands.Choice(name="All Time", value="all"),
    ])
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def tt_slash(self, interaction: discord.Interaction, period: app_commands.Choice[str] = None):
        await interaction.response.defer()
        embed, view, err = await self.bot.process_top_tracks(interaction.user, period.value if period else 'all')
        if embed:
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.edit_original_response(content=err)

    @app_commands.command(name="topalbums", description="View your top played albums")
    @app_commands.describe(period="The time frame to check")
    @app_commands.choices(period=[
        app_commands.Choice(name="7 Days", value="7d"), app_commands.Choice(name="1 Month", value="1m"),
        app_commands.Choice(name="3 Months", value="3m"), app_commands.Choice(name="6 Months", value="6m"),
        app_commands.Choice(name="1 Year", value="1y"), app_commands.Choice(name="All Time", value="all"),
    ])
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def tab_slash(self, interaction: discord.Interaction, period: app_commands.Choice[str] = None):
        await interaction.response.defer()
        embed, view, err = await self.bot.process_top_albums(interaction.user, period.value if period else 'all')
        if embed:
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.edit_original_response(content=err)

    @app_commands.command(name="artist", description="Detailed stats about an artist")
    @app_commands.describe(artist="Artist name")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def artist_info_slash(self, interaction: discord.Interaction, artist: str = None):
        await interaction.response.defer()
        from src.core.info import process_artist_info
        embed, err = await process_artist_info(interaction.user, artist)
        if embed: await interaction.edit_original_response(embed=embed)
        else: await interaction.edit_original_response(content=err)

    @app_commands.command(name="album", description="Detailed stats about an album")
    @app_commands.describe(album="Artist - Album")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def album_info_slash(self, interaction: discord.Interaction, album: str = None):
        await interaction.response.defer()
        from src.core.info import process_album_info
        embed, err = await process_album_info(interaction.user, album)
        if embed: await interaction.edit_original_response(embed=embed)
        else: await interaction.edit_original_response(content=err)

    @app_commands.command(name="track", description="Detailed stats about a track")
    @app_commands.describe(track="Artist - Track")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def track_info_slash(self, interaction: discord.Interaction, track: str = None):
        await interaction.response.defer()
        from src.core.info import process_track_info
        embed, err = await process_track_info(interaction.user, track)
        if embed: await interaction.edit_original_response(embed=embed)
        else: await interaction.edit_original_response(content=err)


    @app_commands.command(name="recent", description="View your recent listening history")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def rt_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed, err = await self.bot.process_recent(interaction.user)
        await interaction.edit_original_response(embed=embed) if embed else await interaction.edit_original_response(content=err)

    @app_commands.command(name="artisttracks", description="View your top played tracks for a specific artist")
    @app_commands.describe(artist="The artist to check (leave blank to use current playing artist)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def at_slash(self, interaction: discord.Interaction, artist: str = None):
        await interaction.response.defer()
        embed, view, err = await self.bot.process_artist_tracks(interaction.user, artist)
        if err:
            await interaction.edit_original_response(content=err)
        else:
            await interaction.edit_original_response(embed=embed, view=view)


    @app_commands.command(name="profile", description="View your Last.fm stats")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def profile_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed, view, err = await self.bot.process_profile(interaction.user)
        if embed:
            if view:
                await interaction.edit_original_response(embed=embed, view=view)
            else:
                await interaction.edit_original_response(embed=embed)
        else:
            await interaction.edit_original_response(content=err)

    @app_commands.command(name="whoknows", description="See who in the server listens to an artist most")
    @app_commands.describe(artist="The artist name (leave blank to use your current playing artist)")
    async def whoknows(self, interaction: discord.Interaction, artist: str = None):
        await interaction.response.defer()
        embed, err = await self.bot.process_whoknows(interaction.guild, interaction.user, artist)
        if embed: await interaction.followup.send(embed=embed)
        else: await interaction.followup.send(embed=err)

    @app_commands.command(name="whoknowstrack", description="See who in the server listens to a track most")
    @app_commands.describe(query="Format: Artist - Track (leave blank to use your current playing track)")
    async def whoknowstrack(self, interaction: discord.Interaction, query: str = None):
        await interaction.response.defer()
        embed, err = await self.bot.process_whoknowstrack(interaction.guild, interaction.user, query)
        if embed: await interaction.followup.send(embed=embed)
        else: await interaction.followup.send(embed=err)

    @app_commands.command(name="whoknowsalbum", description="See who in the server listens to an album most")
    @app_commands.describe(query="Format: Artist - Album (leave blank to use your current playing album)")
    async def whoknowsalbum(self, interaction: discord.Interaction, query: str = None):
        await interaction.response.defer()
        embed, err = await self.bot.process_whoknowsalbum(interaction.guild, interaction.user, query)
        if embed: await interaction.followup.send(embed=embed)
        else: await interaction.followup.send(embed=err)

    @app_commands.command(name="taste", description="Compare your music taste with another user")
    @app_commands.describe(user="The user to compare with")
    async def taste(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer()
        embed, err = await self.bot.process_taste(interaction.guild, interaction.user, user)
        if embed: await interaction.followup.send(embed=embed)
        else: await interaction.followup.send(embed=err)

    @app_commands.command(name="live", description="See what your DJ Scratch friends are playing now")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def live_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed, _ = await self.bot.process_live(interaction.user)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="insights", description="24h plays, milestone progress and top-artist share")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def insights_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed, _ = await self.bot.process_insights(interaction.user)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="share", description="Get a shareable link to your DJ Scratch profile")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def share_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed, view = await self.bot.process_share(interaction.user)
        if view:
            await interaction.followup.send(embed=embed, view=view)
        else:
            await interaction.followup.send(embed=embed)

    @app_commands.command(name="suggest", description="Send a suggestion directly to the developer")
    @app_commands.describe(suggestion="Your idea or feedback for the bot")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def suggest_slash(self, interaction: discord.Interaction, suggestion: str):
        await self.bot.process_suggestion(interaction, interaction.user, suggestion)

    @app_commands.command(name="bug", description="Report a bug directly to the developer")
    @app_commands.describe(bug="Describe the bug you found in the bot")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def bug_slash(self, interaction: discord.Interaction, bug: str):
        await self.bot.process_suggestion(interaction, interaction.user, bug, is_bug=True)

    @app_commands.command(name="help", description="View all available commands")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def help_slash(self, interaction: discord.Interaction):
        embed, view = await self.bot.get_help_embed(interaction.user, self.bot)
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="crowns", description="See which of your top artists you have the most plays for")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def crowns_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed, view = await self.bot.process_crowns(interaction.guild, interaction.user)
        await interaction.edit_original_response(embed=embed, view=view)

    @app_commands.command(name="crownseeder", description="Seed crowns for all users in the server (Admin only)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def crownseeder_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed = await self.bot.process_crownseeder(interaction.guild, interaction.user)
        await interaction.edit_original_response(embed=embed)

    @app_commands.command(name="killallcrowns", description="Remove all seeded crowns for the server (Admin only)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def killallcrowns_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed = await self.bot.process_killallcrowns(interaction.guild, interaction.user)
        await interaction.edit_original_response(embed=embed)

    @app_commands.command(name="judge", description="Let an AI judge your recent music taste")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def judge_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed, err = await self.bot.process_judge(interaction.user)
        await interaction.edit_original_response(embed=embed) if embed else await interaction.edit_original_response(content=err)

    @app_commands.command(name="chart", description="Generate a visual chart of your top albums")
    @app_commands.describe(user="The user to view the chart for", size="Size of the grid (e.g. 3x3, 5x5)", period="Time period (7day, 1month, 3month, 6month, 12month, overall)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.choices(period=[
        app_commands.Choice(name="7 Days", value="7day"),
        app_commands.Choice(name="1 Month", value="1month"),
        app_commands.Choice(name="3 Months", value="3month"),
        app_commands.Choice(name="6 Months", value="6month"),
        app_commands.Choice(name="12 Months", value="12month"),
        app_commands.Choice(name="Overall", value="overall"),
    ])
    async def chart_slash(self, interaction: discord.Interaction, user: discord.User = None, size: str = '3x3', period: app_commands.Choice[str] = None):
        period_val = period.value if period else 'overall'
        await interaction.response.defer()
        status_embed, task = await self.bot.process_chart(interaction.user, user, size, period_val)
        if task is None:
            await interaction.edit_original_response(embed=status_embed)
            return
            
        await interaction.edit_original_response(embed=status_embed)
        embed, file, _ = await task()
        await interaction.edit_original_response(embed=embed, attachments=[file] if file else [])

    @app_commands.command(name="artistchart", description="Generate a visual chart of your top artists")
    @app_commands.describe(user="The user to view the chart for", size="Size of the grid (e.g. 3x3, 5x5)", period="Time period (7day, 1month, 3month, 6month, 12month, overall)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.choices(period=[
        app_commands.Choice(name="7 Days", value="7day"),
        app_commands.Choice(name="1 Month", value="1month"),
        app_commands.Choice(name="3 Months", value="3month"),
        app_commands.Choice(name="6 Months", value="6month"),
        app_commands.Choice(name="12 Months", value="12month"),
        app_commands.Choice(name="Overall", value="overall"),
    ])
    async def artistchart_slash(self, interaction: discord.Interaction, user: discord.User = None, size: str = '3x3', period: app_commands.Choice[str] = None):
        period_val = period.value if period else 'overall'
        await interaction.response.defer()
        status_embed, task = await self.bot.process_artist_chart(interaction.user, user, size, period_val)
        if task is None:
            await interaction.edit_original_response(embed=status_embed)
            return
            
        await interaction.edit_original_response(embed=status_embed)
        embed, file, _ = await task()
        await interaction.edit_original_response(embed=embed, attachments=[file] if file else [])

    @app_commands.command(name="streak", description="Check your current consecutive play streak for an artist")
    @app_commands.describe(artist="The artist to check (defaults to currently playing)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def streak_slash(self, interaction: discord.Interaction, artist: str = None):
        await interaction.response.defer()
        embed, err = await self.bot.process_streak(interaction.user, artist)
        await interaction.edit_original_response(embed=embed) if embed else await interaction.edit_original_response(content=err)

    @app_commands.command(name="streakhistory", description="Check your past artist streaks (>= 25 plays)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def streakhistory_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed, view = await self.bot.process_streak_history(interaction.user)
        if view:
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.edit_original_response(embed=embed)
    # --- PREFIX COMMANDS ---

    @commands.command(name="cd", aliases=["cooldown"])
    async def cd_prefix(self, ctx):
        cd = await self.bot.get_avatar_cooldown()
        status_msg = f"⏳ Avatar is on cooldown for **{cd//60}m {cd%60}s**." if cd > 0 else "✅ Avatar is **ready** to be updated!"

        from src.core.events import fetch_now_playing, get_lastfm_username, ApplyAvatarView, LASTFM_COLOR
        try:
            username = await get_lastfm_username(ctx.author.id)
            if username:
                data = await fetch_now_playing(username)
                if data and 'recenttracks' in data and data['recenttracks']['track']:
                    t = data['recenttracks']['track'][0]
                    is_p = t.get('@attr', {}).get('nowplaying') == 'true'
                    if is_p:
                        artist, song, img = t['artist']['#text'], t['name'], t['image'][3]['#text']
                        album = t.get('album', {}).get('#text')
                        
                        try:
                            from src.core.spotify import get_spotify_track_info
                            session = getattr(self.bot, 'session', None)
                            if session:
                                s_info = await get_spotify_track_info(session, artist, song)
                                if s_info and s_info.get("image_url"):
                                    if not img or "2a96cbd8b46e442fc41c2b86b821562f" in img:
                                        img = s_info.get("image_url")
                        except Exception as e:
                            print(f"{Log.RED}>>> Spotify fetch error in cd_prefix: {e}{Log.RESET}")

                        if img:
                            preview_embed = Theme.get_embed(
                                title="Bot Avatar Preview", 
                                description=f"Current track: **{song}** by **{artist}**", 
                                color=LASTFM_COLOR
                            )
                            preview_embed.set_author(name=format_name(ctx.author), icon_url=img)
                            
                            from src.utils.images import get_circular_pfp_file
                            pfp_file = await get_circular_pfp_file(img)
                            
                            view = ApplyAvatarView(self.bot, artist, img, original_user=ctx.author, track=song, album=album)
                            
                            if pfp_file:
                                preview_embed.set_image(url="attachment://pfp_preview.png")
                                msg = await ctx.send(content=status_msg, file=pfp_file, embed=preview_embed, view=view)
                            else:
                                preview_embed.set_image(url=img)
                                msg = await ctx.send(content=status_msg, embed=preview_embed, view=view)
                                
                            view.original_msg = msg
                            return
        except Exception as e:
            pass
            
        await ctx.send(content=status_msg)

    @commands.command(name="cd2", aliases=["c2"])
    async def cd2_prefix(self, ctx):
        cd = await self.bot.get_avatar_cooldown()
        status_msg = f"⏳ Avatar is on cooldown for **{cd//60}m {cd%60}s**." if cd > 0 else "✅ Avatar is **ready** to be updated!"

        from src.core.events import get_lastfm_username, ApplyAvatarView, LASTFM_COLOR
        from src.utils.api import api_get
        from src.core.config import LASTFM_API_KEY
        try:
            username = await get_lastfm_username(ctx.author.id)
            if username:
                # Fetch limit=2 to ensure we get the last completed song even if one is currently playing
                url = f"https://ws.audioscrobbler.com/2.0/?method=user.getrecenttracks&user={username}&api_key={LASTFM_API_KEY}&format=json&limit=2"
                data = await api_get(url)
                if data and 'recenttracks' in data and data['recenttracks']['track']:
                    tracks = data['recenttracks']['track']
                    # Default to the first track
                    t = tracks[0]
                    # If the first track is currently playing and there is a second track, use the second track (last completed)
                    if t.get('@attr', {}).get('nowplaying') == 'true' and len(tracks) > 1:
                        t = tracks[1]
                        
                    artist, song, img = t['artist']['#text'], t['name'], t['image'][3]['#text']
                    album = t.get('album', {}).get('#text')
                    
                    try:
                        from src.core.spotify import get_spotify_track_info
                        session = getattr(self.bot, 'session', None)
                        if session:
                            s_info = await get_spotify_track_info(session, artist, song)
                            if s_info and s_info.get("image_url"):
                                if not img or "2a96cbd8b46e442fc41c2b86b821562f" in img:
                                    img = s_info.get("image_url")
                    except Exception as e:
                        print(f"{Log.RED}>>> Spotify fetch error in cd2_prefix: {e}{Log.RESET}")

                    if img:
                        preview_embed = Theme.get_embed(
                            title="Bot Avatar Preview (Last Played)", 
                            description=f"Last track: **{song}** by **{artist}**", 
                            color=LASTFM_COLOR
                        )
                        preview_embed.set_author(name=format_name(ctx.author), icon_url=img)
                        
                        from src.utils.images import get_circular_pfp_file
                        pfp_file = await get_circular_pfp_file(img)
                        
                        view = ApplyAvatarView(self.bot, artist, img, original_user=ctx.author, track=song, album=album)
                        
                        if pfp_file:
                            preview_embed.set_image(url="attachment://pfp_preview.png")
                            msg = await ctx.send(content=status_msg, file=pfp_file, embed=preview_embed, view=view)
                        else:
                            preview_embed.set_image(url=img)
                            msg = await ctx.send(content=status_msg, embed=preview_embed, view=view)
                            
                        view.original_msg = msg
                        return
        except Exception as e:
            pass
            
        await ctx.send(content=status_msg)

    @commands.command(name="login", aliases=["log", "li"])
    async def login_prefix(self, ctx):
        from src.core.events import get_lastfm_username
        username = await get_lastfm_username(ctx.author.id)
        spotify_linked = await _is_spotify_linked(ctx.author.id)

        if username and spotify_linked:
            embed = Theme.get_embed(
                title="✅ All Linked",
                description=f"Last.fm linked as **{username}**.\n🎵 Spotify linked — remote control and the Music dashboard are ready.",
                color=discord.Color.green()
            )
            return await ctx.send(embed=embed)

        if username:
            embed = Theme.get_embed(
                title="✅ Last.fm Linked",
                description=f"You are logged in as **{username}**.\n\n"
                            "🎵 Spotify is **not** linked yet — add it for playback control (`,play`), likes, and the Music dashboard.\n\n"
                            "If you want to switch Last.fm accounts, use `,logout` first.",
                color=discord.Color.green()
            )
            msg = await ctx.send(embed=embed)
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Login with Spotify",
                url=_spotify_login_url(ctx.author.id, ctx.channel.id, msg.id),
                emoji="🎵"))
            return await msg.edit(embed=embed, view=view)

        desc = ("**DJ Scratch uses Last.fm to track your listening history.**\n\n"
                "Click a button below to link an account. You will be redirected to authorize the bot.\n\n"
                "*(Don't have a Last.fm account? You'll need to [create one](https://www.last.fm/join) and link it to your Spotify first!)*")
        if spotify_linked:
            desc += "\n\n🎵 Spotify already linked."
        embed = Theme.get_embed(title="🔗 Connect Your Music", description=desc, color=discord.Color.red())
        msg = await ctx.send(embed=embed)

        import urllib.parse
        from src.core.config import LASTFM_API_KEY as _LASTFM_KEY
        cb_url = f"https://dj-scratch.vercel.app/login-callback/?discord_id={ctx.author.id}&channel_id={ctx.channel.id}&message_id={msg.id}"
        auth_url = f"https://www.last.fm/api/auth/?api_key={_LASTFM_KEY}&cb={urllib.parse.quote(cb_url)}"

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Login with Last.fm", url=auth_url, emoji="🔗"))
        view.add_item(discord.ui.Button(
            label="Login with Spotify",
            url=_spotify_login_url(ctx.author.id, ctx.channel.id, msg.id),
            emoji="🎵"))
        await msg.edit(view=view)

    @commands.command(name="logout", aliases=["lo"])
    async def logout_prefix(self, ctx, *, service: str = None):
        # `,logout spotify` disconnects Spotify; anything else unlinks Last.fm.
        if service and service.strip().lower() in ("spotify", "sp", "spot"):
            from src.core.database import clear_user_spotify
            if not await _is_spotify_linked(ctx.author.id):
                embed = Theme.get_embed(
                    title="🎵 Spotify Not Linked",
                    description="Your Spotify account isn't linked — nothing to disconnect.",
                    color=discord.Color.blue()
                )
                return await ctx.send(embed=embed)
            ok = await clear_user_spotify(ctx.author.id)
            if ok:
                embed = Theme.get_embed(
                    title="🔓 Spotify Disconnected",
                    description="Your Spotify account has been unlinked.\n\nRemote control, likes, and the Music dashboard will stop working until you link it again with `,login`.",
                    color=discord.Color.green()
                )
            else:
                embed = Theme.get_embed(
                    title="❌ Disconnect Failed",
                    description="Could not disconnect Spotify (database offline?). Please try again later.",
                    color=discord.Color.red()
                )
            return await ctx.send(embed=embed)

        from src.core.database import unlink_user
        await unlink_user(ctx.author.id)
        embed = Theme.get_embed(
            title="👋 Logged Out",
            description="Your Last.fm account has been successfully unlinked from your Discord account.\n\n*To disconnect Spotify instead, use `,logout spotify`.*",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @commands.command(name="fm", aliases=["np", "nowplaying", "fm1", "fm2", "fm3", "np1", "np2", "np3", "n", "fn"])
    async def fm_prefix(self, ctx, *, args: str = None):
        target_user, _ = await get_target_user(ctx, args)
        invoked = ctx.invoked_with
        if invoked in ["fm1", "np1"]: m = "compact"
        elif invoked in ["fm2", "np2"]: m = "full"
        elif invoked in ["fm3", "np3"]: m = "stats"
        else:
            m = await self.bot.get_user_fm_mode(target_user.id)
            if not m: m = "full"
        result, is_p = await self.bot.process_fm(ctx, target_user, mode=m)
        if result is None:
            await self._reply_and_delete(ctx, is_p)
        elif isinstance(result, dict):
            msg = await self._reply_and_delete(ctx, **result)
            if is_p: await self.bot.add_custom_reactions(msg)

    @commands.command(name="ta", aliases=["topartists", "topa", "tart"])
    async def ta_prefix(self, ctx, *, args: str = None):
        target_user, period = await get_target_user(ctx, args)
        if not period: period = 'all'
        embed, view, err = await self.bot.process_top_artists(target_user, period)
        if embed:
            await self._reply_and_delete(ctx, embed=embed, view=view)
        else:
            await self._reply_and_delete(ctx, err)

    @commands.command(name="tt", aliases=["toptracks", "topt", "ttr", "ttracks"])
    async def tt_prefix(self, ctx, *, args: str = None):
        target_user, period = await get_target_user(ctx, args)
        if not period: period = 'all'
        embed, view, err = await self.bot.process_top_tracks(target_user, period)
        if embed:
            await self._reply_and_delete(ctx, embed=embed, view=view)
        else:
            await self._reply_and_delete(ctx, err)

    @commands.command(name="rt", aliases=["recent", "recents", "rtracks", "r"])
    async def rt_prefix(self, ctx, *, args: str = None):
        target_user, _ = await get_target_user(ctx, args)
        embed, err = await self.bot.process_recent(target_user)
        await self._reply_and_delete(ctx, embed=embed) if embed else await self._reply_and_delete(ctx, err)

    @commands.command(name="at", aliases=["artisttracks", "art", "atracks"])
    async def at_prefix(self, ctx, *, args: str = None):
        target_user, artist = await get_target_user(ctx, args)
        embed, view, err = await self.bot.process_artist_tracks(target_user, artist)
        if err:
            await self._reply_and_delete(ctx, err)
        else:
            await self._reply_and_delete(ctx, embed=embed, view=view)


    @commands.command(name="profile", aliases=["prof"])
    async def s_prefix(self, ctx, *, args: str = None):
        target_user, _ = await get_target_user(ctx, args)
        embed, view, err = await self.bot.process_profile(target_user)
        if embed:
            if view:
                await self._reply_and_delete(ctx, embed=embed, view=view)
            else:
                await self._reply_and_delete(ctx, embed=embed)
        else:
            await self._reply_and_delete(ctx, err)

    @commands.command(name="wk", aliases=["whoknows", "who", "w"])
    async def whoknows_prefix(self, ctx, *, args: str = None):
        target_user, artist = await get_target_user(ctx, args)
        embed, err = await self.bot.process_whoknows(ctx.guild, target_user, artist)
        if embed: await self._reply_and_delete(ctx, embed=embed)
        else: await self._reply_and_delete(ctx, err)

    @commands.command(name="wkt", aliases=["whoknowstrack", "wt"])
    async def whoknowstrack_prefix(self, ctx, *, args: str = None):
        target_user, query = await get_target_user(ctx, args)
        embed, err = await self.bot.process_whoknowstrack(ctx.guild, target_user, query)
        if embed: await self._reply_and_delete(ctx, embed=embed)
        else: await self._reply_and_delete(ctx, err)

    @commands.command(name="wka", aliases=["whoknowsalbum", "wa"])
    async def whoknowsalbum_prefix(self, ctx, *, args: str = None):
        target_user, query = await get_target_user(ctx, args)
        embed, err = await self.bot.process_whoknowsalbum(ctx.guild, target_user, query)
        if embed: await self._reply_and_delete(ctx, embed=embed)
        else: await self._reply_and_delete(ctx, err)

    @commands.command(name="taste", aliases=["t"])
    async def taste_prefix(self, ctx, user: discord.Member = None):
        embed, err = await self.bot.process_taste(ctx.guild, ctx.author, user)
        if embed: await self._reply_and_delete(ctx, embed=embed)
        else: await self._reply_and_delete(ctx, err)

    @commands.command(name="live", aliases=["friendslive", "nowlive"])
    async def live_prefix(self, ctx):
        embed, _ = await self.bot.process_live(ctx.author)
        await self._reply_and_delete(ctx, embed=embed)

    @commands.command(name="insights", aliases=["insight", "ins", "stats"])
    async def insights_prefix(self, ctx):
        embed, _ = await self.bot.process_insights(ctx.author)
        await self._reply_and_delete(ctx, embed=embed)

    @commands.command(name="share", aliases=["shareprofile", "link"])
    async def share_prefix(self, ctx):
        embed, view = await self.bot.process_share(ctx.author)
        if view:
            await self._reply_and_delete(ctx, embed=embed, view=view)
        else:
            await self._reply_and_delete(ctx, embed=embed)

    @commands.command(name="suggest", aliases=["suggestion", "su", "sug"])
    async def suggest_prefix(self, ctx, *, suggestion: str = None):
        if not suggestion:
            embed = Theme.get_embed(
                title="❌ Missing Suggestion", 
                description="Please provide a suggestion!\n\n**Usage:** `,suggest <your idea>`",
                color=discord.Color.red()
            )
            return await ctx.send(embed=embed)
        await self.bot.process_suggestion(ctx, ctx.author, suggestion)

    @commands.command(name="bug", aliases=["bugreport", "reportbug"])
    async def bug_prefix(self, ctx, *, bug: str = None):
        if not bug:
            embed = Theme.get_embed(
                title="❌ Missing Bug Report", 
                description="Please describe the bug!\n\n**Usage:** `,bug <description>`",
                color=discord.Color.red()
            )
            return await ctx.send(embed=embed)
        await self.bot.process_suggestion(ctx, ctx.author, bug, is_bug=True)

    @commands.command(name="help", aliases=["h"])
    async def help_prefix(self, ctx):
        embed, view = await self.bot.get_help_embed(ctx.author, self.bot)
        await ctx.send(embed=embed, view=view)

    @commands.command(name="crowns", aliases=["cr", "cw"])
    async def crowns_prefix(self, ctx, *, args: str = None):
        target_user, _ = await get_target_user(ctx, args)
        embed, view = await self.bot.process_crowns(ctx.guild, target_user)
        await self._reply_and_delete(ctx, embed=embed, view=view)

    @commands.command(name="crownseeder")
    @commands.has_permissions(administrator=True)
    async def crownseeder_prefix(self, ctx):
        msg = await ctx.send("⏳ Fetching top artists for all users and seeding crowns... This may take a minute.")
        embed = await self.bot.process_crownseeder(ctx.guild, ctx.author)
        await msg.edit(content=None, embed=embed)
        
    @commands.command(name="killallcrowns", aliases=["killallseededcrowns"])
    @commands.has_permissions(administrator=True)
    async def killallcrowns_prefix(self, ctx):
        embed = await self.bot.process_killallcrowns(ctx.guild, ctx.author)
        await self._reply_and_delete(ctx, embed=embed)

    @commands.command(name="judge", aliases=["roast", "jd", "j"])
    async def judge_prefix(self, ctx, *, args: str = None):
        target_user, _ = await get_target_user(ctx, args)
        embed, err = await self.bot.process_judge(target_user)
        await self._reply_and_delete(ctx, embed=embed) if embed else await self._reply_and_delete(ctx, err)

    @commands.command(name="receipt", aliases=["rec", "re"])
    async def receipt_prefix(self, ctx, *, args: str = None):
        target_user, period = await get_target_user(ctx, args)
        if not period: period = 'overall'
        # Map period aliases
        period_map = {'7d': '7day', '1m': '1month', '3m': '3month', '6m': '6month', '12m': '12month', 'y': '12month', 'all': 'overall'}
        p = period_map.get(period.lower(), period.lower())
            
        embed, file, err = await self.bot.process_receipt(target_user, p, 10)
        if err:
            await self._reply_and_delete(ctx, err)
        else:
            await self._reply_and_delete(ctx, embed=embed, file=file)




    @commands.command(name="topalbums", aliases=["tab", "tal"])
    async def tab_prefix(self, ctx, *, args: str = None):
        target_user = await get_target_user(ctx, args)
        period = None
        if isinstance(target_user, tuple): target_user, period = target_user
        embed, view, err = await self.bot.process_top_albums(target_user, period if period else 'all')
        if embed: await self._reply_and_delete(ctx, embed=embed, view=view)
        else: await self._reply_and_delete(ctx, err)

    @commands.command(name="artist", aliases=["a"])
    async def artist_info_prefix(self, ctx, *, args: str = None):
        from src.core.info import process_artist_info
        embed, err = await process_artist_info(ctx.author, args)
        if embed: await self._reply_and_delete(ctx, embed=embed)
        else: await self._reply_and_delete(ctx, err)

    @commands.command(name="album", aliases=["al"])
    async def album_info_prefix(self, ctx, *, args: str = None):
        from src.core.info import process_album_info
        embed, err = await process_album_info(ctx.author, args)
        if embed: await self._reply_and_delete(ctx, embed=embed)
        else: await self._reply_and_delete(ctx, err)

    @commands.command(name="track", aliases=["tr"])
    async def track_info_prefix(self, ctx, *, args: str = None):
        from src.core.info import process_track_info
        embed, err = await process_track_info(ctx.author, args)
        if embed: await self._reply_and_delete(ctx, embed=embed)
        else: await self._reply_and_delete(ctx, err)

    async def _parse_chart_args(self, ctx, args):
        size = '3x3'
        period = 'overall'
        target = ctx.author
        
        period_map = {
            '7day': '7day', 'w': '7day', 'week': '7day',
            '1month': '1month', 'm': '1month', 'month': '1month',
            '3month': '3month', 'q': '3month', 'quarter': '3month',
            '6month': '6month', 'h': '6month', 'half': '6month',
            '12month': '12month', 'y': '12month', 'year': '12month',
            'overall': 'overall', 'a': 'overall', 'all': 'overall'
        }
        
        for arg in args:
            arg_lower = arg.lower()
            if 'x' in arg_lower and len(arg_lower.split('x')) == 2 and arg_lower.replace('x', '').isdigit():
                size = arg_lower
            elif arg_lower in period_map:
                period = period_map[arg_lower]
            else:
                try:
                    target = await commands.MemberConverter().convert(ctx, arg)
                except commands.MemberNotFound:
                    pass
                    
        return size, period, target

    @commands.command(name="chart", aliases=["c"])
    async def chart_prefix(self, ctx, *args):
        size, period, target = await self._parse_chart_args(ctx, args)
        
        status_embed, task = await self.bot.process_chart(ctx.author, target, size, period)
        if task is None:
            await ctx.send(embed=status_embed)
            return
            
        msg = await ctx.send(embed=status_embed)
        embed, file, _ = await task()
        await msg.edit(embed=embed, attachments=[file] if file else [])

    @commands.command(name="artistchart", aliases=["ac"])
    async def artistchart_prefix(self, ctx, *args):
        size, period, target = await self._parse_chart_args(ctx, args)
        
        status_embed, task = await self.bot.process_artist_chart(ctx.author, target, size, period)
        if task is None:
            await ctx.send(embed=status_embed)
            return
            
        msg = await ctx.send(embed=status_embed)
        embed, file, _ = await task()
        await msg.edit(embed=embed, attachments=[file] if file else [])

    @commands.command(name="streak", aliases=["str"])
    async def streak_prefix(self, ctx, *, artist: str = None):
        embed, err = await self.bot.process_streak(ctx.author, artist)
        if embed: await self._reply_and_delete(ctx, embed=embed)
        else: await self._reply_and_delete(ctx, err)

    @commands.command(name="streakhistory", aliases=["strs"])
    async def streakhistory_prefix(self, ctx):
        embed, view = await self.bot.process_streak_history(ctx.author)
        if view:
            await ctx.reply(embed=embed, view=view, mention_author=False)
        else:
            await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="serverartists", aliases=["sart"])
    async def serverartists_prefix(self, ctx, period: str = 'overall'):
        embed, err = await self.bot.process_server_artists(ctx.guild, ctx.author, period)
        if embed: await self._reply_and_delete(ctx, embed=embed)
        else: await self._reply_and_delete(ctx, err)

    @app_commands.command(name="serverartists", description="View the top artists across the entire server")
    async def serverartists_slash(self, interaction: discord.Interaction, period: str = 'overall'):
        await interaction.response.defer()
        embed, err = await self.bot.process_server_artists(interaction.guild, interaction.user, period)
        if embed: await interaction.followup.send(embed=embed)
        else: await interaction.followup.send(embed=err)

    @commands.command(name="serveralbums", aliases=["salb"])
    async def serveralbums_prefix(self, ctx, period: str = 'overall'):
        embed, err = await self.bot.process_server_albums(ctx.guild, ctx.author, period)
        if embed: await self._reply_and_delete(ctx, embed=embed)
        else: await self._reply_and_delete(ctx, err)

    @app_commands.command(name="serveralbums", description="View the top albums across the entire server")
    async def serveralbums_slash(self, interaction: discord.Interaction, period: str = 'overall'):
        await interaction.response.defer()
        embed, err = await self.bot.process_server_albums(interaction.guild, interaction.user, period)
        if embed: await interaction.followup.send(embed=embed)
        else: await interaction.followup.send(embed=err)

    @commands.command(name="servertracks", aliases=["strk"])
    async def servertracks_prefix(self, ctx, period: str = 'overall'):
        embed, err = await self.bot.process_server_tracks(ctx.guild, ctx.author, period)
        if embed: await self._reply_and_delete(ctx, embed=embed)
        else: await self._reply_and_delete(ctx, err)

    @app_commands.command(name="servertracks", description="View the top tracks across the entire server")
    async def servertracks_slash(self, interaction: discord.Interaction, period: str = 'overall'):
        await interaction.response.defer()
        embed, err = await self.bot.process_server_tracks(interaction.guild, interaction.user, period)
        if embed: await interaction.followup.send(embed=embed)
        else: await interaction.followup.send(embed=err)

    @commands.command(name="globalwhoknows", aliases=["gwk"])
    async def globalwhoknows_prefix(self, ctx, *, artist: str = None):
        embed, err = await self.bot.process_global_whoknows(ctx.author, artist, self.bot)
        if embed: await self._reply_and_delete(ctx, embed=embed)
        else: await self._reply_and_delete(ctx, err)

    @app_commands.command(name="globalwhoknows", description="See who listens to an artist globally across all servers")
    @app_commands.describe(artist="Leave blank to use your currently playing artist")
    async def globalwhoknows_slash(self, interaction: discord.Interaction, artist: str = None):
        await interaction.response.defer()
        embed, err = await self.bot.process_global_whoknows(interaction.user, artist, self.bot)
        if embed: await interaction.followup.send(embed=embed)
        else: await interaction.followup.send(embed=err)

    @commands.command(name="globalwhoknowstrack", aliases=["gwkt"])
    async def globalwhoknowstrack_prefix(self, ctx, *, query: str = None):
        embed, err = await self.bot.process_global_whoknowstrack(ctx.author, query, self.bot)
        if embed: await self._reply_and_delete(ctx, embed=embed)
        else: await self._reply_and_delete(ctx, err)

    @app_commands.command(name="globalwhoknowstrack", description="See who listens to a track globally across all servers")
    @app_commands.describe(query="Format: 'Artist | Track' (or leave blank to use your currently playing track)")
    async def globalwhoknowstrack_slash(self, interaction: discord.Interaction, query: str = None):
        await interaction.response.defer()
        embed, err = await self.bot.process_global_whoknowstrack(interaction.user, query, self.bot)
        if embed: await interaction.followup.send(embed=embed)
        else: await interaction.followup.send(embed=err)

    @commands.command(name="globalwhoknowsalbum", aliases=["gwka"])
    async def globalwhoknowsalbum_prefix(self, ctx, *, query: str = None):
        embed, err = await self.bot.process_global_whoknowsalbum(ctx.author, query, self.bot)
        if embed: await self._reply_and_delete(ctx, embed=embed)
        else: await self._reply_and_delete(ctx, err)

    @app_commands.command(name="globalwhoknowsalbum", description="See who listens to an album globally across all servers")
    @app_commands.describe(query="Format: 'Artist | Album' (or leave blank to use your currently playing album)")
    async def globalwhoknowsalbum_slash(self, interaction: discord.Interaction, query: str = None):
        await interaction.response.defer()
        embed, err = await self.bot.process_global_whoknowsalbum(interaction.user, query, self.bot)
        if embed: await interaction.followup.send(embed=embed)
        else: await interaction.followup.send(embed=err)

async def setup(bot):
    await bot.add_cog(LastFmCog(bot))
