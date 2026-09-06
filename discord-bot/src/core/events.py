from src.core.config import Log
import os
import discord
import aiohttp
import json
import asyncio
import urllib.parse
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import asyncpg
import uuid
from ..utils.api import *

FM_TRACK_CACHE = {}

# --- TERMINAL COLOR CODES ---
class Log:
    RESET = '\033[0m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    CYAN = '\033[96m'
    MAGENTA = '\033[95m'

# --- BOT SETUP ---
load_dotenv()
intents = discord.Intents.default()
intents.message_content = True  
intents.presences = False  
intents.members = True    

class CustomTree(app_commands.CommandTree):
    def __init__(self, client):
        super().__init__(
            client, 
            allowed_installs=app_commands.AppInstallationType(guild=True, user=True), 
            allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True)
        )

# --- Prefix cache: avoids a DB hit on EVERY message (biggest latency win) ---
_PREFIX_CACHE: dict = {}  # guild_id -> (prefixes_list, expires_at_monotonic)
_PREFIX_TTL = 300.0  # 5 minutes

def _prefix_cache_get(guild_id: str):
    import time as _t
    entry = _PREFIX_CACHE.get(guild_id)
    if entry and entry[1] > _t.monotonic():
        return entry[0]
    return None

def _prefix_cache_set(guild_id: str, prefixes):
    import time as _t
    _PREFIX_CACHE[guild_id] = (prefixes, _t.monotonic() + _PREFIX_TTL)
    # Bound cache size
    if len(_PREFIX_CACHE) > 5000:
        _PREFIX_CACHE.pop(next(iter(_PREFIX_CACHE)))

def invalidate_prefix_cache(guild_id=None):
    if guild_id is None:
        _PREFIX_CACHE.clear()
    else:
        _PREFIX_CACHE.pop(str(guild_id), None)

async def get_prefix(client, message):
    if getattr(client, 'is_test_bot', False):
        return ",,"
    default_prefix = [',']
    if not message.guild: return default_prefix
    cached = _prefix_cache_get(str(message.guild.id))
    if cached is not None:
        return cached
    from src.core.database import db_pool
    if not db_pool: return default_prefix
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT prefix FROM server_settings WHERE guild_id=$1", str(message.guild.id))
            if row and row['prefix']:
                p = row['prefix']
                prefixes = [p, ','] if p != ',' else default_prefix
            else:
                prefixes = default_prefix
            _prefix_cache_set(str(message.guild.id), prefixes)
            return prefixes
    except Exception: pass
    return default_prefix

bot = commands.Bot(command_prefix=get_prefix, intents=intents, tree_cls=CustomTree, max_messages=50)
bot.is_restarting = False
bot.remove_command('help')

async def add_restarting_user(user_id, channel_id):
    from src.core.database import db_pool
    import json
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT value FROM global_settings WHERE key = 'restart_notifs'")
            users = []
            if row and row['value']:
                try:
                    users = json.loads(row['value'])
                except:
                    pass
            
            entry = {"user_id": user_id, "channel_id": channel_id}
            if entry not in users:
                users.append(entry)
                await conn.execute(
                    "INSERT INTO global_settings (key, value) VALUES ('restart_notifs', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                    json.dumps(users)
                )
    except Exception as e:
        print(f"Failed to save restart notif for {user_id}: {e}")

_TOUCH_STAMPS: dict = {}  # (user_id, kind) -> last monotonic write
_TOUCH_TTL = 600.0  # 10 min: purge cutoffs are 53/60 days, so this loses nothing


def _should_touch(user_id, kind="activity", ttl=_TOUCH_TTL):
    import time as _t
    now = _t.monotonic()
    key = (str(user_id), kind)
    if now - _TOUCH_STAMPS.get(key, 0.0) < ttl:
        return False
    _TOUCH_STAMPS[key] = now
    if len(_TOUCH_STAMPS) > 20000:
        cutoff = now - ttl
        for k in [k for k, v in _TOUCH_STAMPS.items() if v < cutoff]:
            _TOUCH_STAMPS.pop(k, None)
    return True


async def update_user_activity(user_id):
    # Debounced: this fires on EVERY command, but day-granularity is plenty.
    if not _should_touch(user_id, "activity"):
        return
    from src.core.database import db_pool
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO user_settings (user_id, last_active, purge_warning_sent)
                VALUES ($1, CURRENT_TIMESTAMP, FALSE)
                ON CONFLICT (user_id)
                DO UPDATE SET last_active = CURRENT_TIMESTAMP, purge_warning_sent = FALSE
            """, str(user_id))
    except Exception as e:
        pass

async def run_inactive_purge():
    from src.core.database import db_pool
    from datetime import datetime, timedelta, timezone
    if not db_pool:
        return
        
    try:
        async with db_pool.acquire() as conn:
            warning_cutoff = datetime.now(timezone.utc) - timedelta(days=53)
            to_warn = await conn.fetch(
                "SELECT user_id FROM user_settings WHERE last_active <= $1 AND purge_warning_sent = FALSE",
                warning_cutoff
            )
            for row in to_warn:
                uid = row['user_id']
                try:
                    user = await bot.fetch_user(int(uid))
                    if user:
                        await user.send("⚠️ **Account Inactivity Warning**\nYour DJ Scratch data hasn't been used in over 50 days. It will be permanently deleted in 7 days due to inactivity.\n\n*To cancel this deletion, simply run any command like `/fm` or `/stats`!*")
                except Exception:
                    pass
                await conn.execute("UPDATE user_settings SET purge_warning_sent = TRUE WHERE user_id = $1", uid)
                
            delete_cutoff = datetime.now(timezone.utc) - timedelta(days=60)
            to_delete = await conn.fetch(
                "SELECT user_id FROM user_settings WHERE last_active <= $1",
                delete_cutoff
            )
            for row in to_delete:
                uid = row['user_id']
                await conn.execute("DELETE FROM user_settings WHERE user_id = $1", uid)
                await conn.execute("DELETE FROM command_permissions WHERE user_id = $1", uid)
                await conn.execute("DELETE FROM friends WHERE user_id = $1 OR friend_id = $1", uid)
                await conn.execute("DELETE FROM website_logs WHERE user_id = $1", uid)
                await conn.execute("DELETE FROM direct_messages WHERE sender_id = $1 OR receiver_id = $1", uid)
                
            if to_delete:
                print(f"Purged {len(to_delete)} inactive users.")
                
            # Clear completed and denied suggestions and bugs
            res = await conn.execute("DELETE FROM suggestions WHERE status IN ('completed', 'denied')")
            # res usually looks like "DELETE X"
            deleted_count = int(res.split()[1]) if res.startswith("DELETE") else 0
            if deleted_count > 0:
                print(f"Purged {deleted_count} completed/denied suggestions/bugs.")

            # Storage guard: web uploads stuck mid-flow (user never hit finalize,
            # or the worker died) leave raw file bytes in import_chunks forever.
            # 'ready'/'completed' are left alone — only abandoned jobs are purged.
            try:
                stale_jobs = await conn.fetch(
                    "SELECT id FROM import_jobs WHERE status NOT IN ('ready', 'completed')"
                    " AND created_at < NOW() - INTERVAL '7 days'"
                )
                for sj in stale_jobs:
                    await conn.execute("DELETE FROM import_chunks WHERE job_id = $1", sj['id'])
                    await conn.execute("DELETE FROM import_jobs WHERE id = $1", sj['id'])
                if stale_jobs:
                    print(f"Cleaned {len(stale_jobs)} stale import jobs (+ their chunks).")
            except Exception as e:
                # import_jobs/chunks are created manually; if the tables (or
                # created_at) don't exist there is simply nothing to clean.
                if not isinstance(e, (asyncpg.exceptions.UndefinedTableError,
                                      asyncpg.exceptions.UndefinedColumnError)):
                    print(f"Import cleanup skipped: {e}")
                
    except Exception as e:
        print(f"Error in run_inactive_purge: {e}")


async def check_restarting_slash(interaction: discord.Interaction) -> bool:
    asyncio.create_task(update_user_activity(interaction.user.id))
    if getattr(bot, 'is_restarting', False):
        try:
            timestamp = int(bot.is_restarting) if isinstance(bot.is_restarting, float) else int(time.time() + 60)
            reason = getattr(bot, 'restart_reason', 'Maintenance')
            await interaction.channel.send(f"⚠️ {interaction.user.mention}, **Warning:** The bot is restarting <t:{timestamp}:R> because: **{reason}**. Your command might be interrupted! (We will ping you here when it's back online)", delete_after=15)
        except:
            pass
        await add_restarting_user(interaction.user.id, interaction.channel.id)
    return True

@bot.check
async def check_restarting_prefix(ctx) -> bool:
    if not ctx.command: return True
    if ctx.command.name == "cancel": return True
    asyncio.create_task(update_user_activity(ctx.author.id))
    if getattr(bot, 'is_restarting', False):
        try:
            timestamp = int(bot.is_restarting) if isinstance(bot.is_restarting, float) else int(time.time() + 60)
            reason = getattr(bot, 'restart_reason', 'Maintenance')
            await ctx.send(f"⚠️ {ctx.author.mention}, **Warning:** The bot is restarting <t:{timestamp}:R> because: **{reason}**. Your command might be interrupted! (We will ping you here when it's back online)", delete_after=15)
        except:
            pass
        await add_restarting_user(ctx.author.id, ctx.channel.id)
    return True


# === LAST.FM CONFIG (centralized in src.core.config — no hardcoded fallbacks) ===
from src.core.config import LASTFM_API_KEY, OWNER_ID

COOLDOWN_FILE = "avatar_cooldown.txt"
from src.core.theme import Theme
LASTFM_COLOR = Theme.PRIMARY 

# Color cache: get_color() is called on nearly every command.
_COLOR_CACHE: dict = {}
import time as _ctime


async def get_color(user_id):
    from src.core.database import get_user_embed_color
    now = _ctime.monotonic()
    entry = _COLOR_CACHE.get(str(user_id))
    if entry and entry[1] > now:
        return entry[0]
    c = await get_user_embed_color(user_id)
    if c and c != 'album':
        import discord
        try:
            color = discord.Color(int(c.strip('#'), 16))
            _COLOR_CACHE[str(user_id)] = (color, now + 300)
            if len(_COLOR_CACHE) > 2000:
                _COLOR_CACHE.pop(next(iter(_COLOR_CACHE)))
            return color
        except:
            pass
    _COLOR_CACHE[str(user_id)] = (LASTFM_COLOR, now + 60)
    return LASTFM_COLOR

async def get_album_based_color(user_id, image_url=None):
    """Get embed color based on last listened album art if user has feature enabled."""
    from src.core.database import db_pool
    if not db_pool:
        return await get_color(user_id)
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT embed_color FROM user_settings WHERE user_id=$1", str(user_id))
            if row and row['embed_color'] == 'album':
                if not image_url:
                    row2 = await conn.fetchrow(
                        "SELECT t.artist_name, t.album_name FROM listens l JOIN tracks t ON l.track_id=t.id WHERE l.user_id=$1 ORDER BY l.played_at DESC LIMIT 1",
                        str(user_id)
                    )
                    if row2 and row2['artist_name'] and row2['album_name']:
                        image_url = await get_album_image_url(row2['artist_name'], row2['album_name'])
                if image_url:
                    from src.utils.color_extractor import get_album_art_color
                    color_int = await get_album_art_color(image_url)
                    import discord
                    return discord.Color(color_int)
    except Exception:
        pass
    return await get_color(user_id)

_ITUNES_CACHE: dict = {}


async def get_album_image_url(artist, album):
    """Try to fetch album art URL from iTunes Search API (no key required)."""
    import aiohttp
    key = f"{artist.lower()}\x00{album.lower()}"
    now = _ctime.monotonic()
    entry = _ITUNES_CACHE.get(key)
    if entry and entry[1] > now:
        return entry[0]
    try:
        params = {"term": f"{artist} {album}", "media": "music", "entity": "album", "limit": 1}
        session = getattr(bot, 'session', None)
        if session is None or getattr(session, 'closed', True):
            async with aiohttp.ClientSession() as _tmp:
                async with _tmp.get("https://itunes.apple.com/search", params=params, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("results"):
                            url = data["results"][0].get("artworkUrl100", "").replace("100x100bb", "300x300bb")
                            _ITUNES_CACHE[key] = (url, now + 86400)
                            return url
            return None
        async with session.get("https://itunes.apple.com/search", params=params, timeout=aiohttp.ClientTimeout(total=3)) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("results"):
                    url = data["results"][0].get("artworkUrl100", "").replace("100x100bb", "300x300bb")
                    _ITUNES_CACHE[key] = (url, now + 86400)
                    if len(_ITUNES_CACHE) > 2000:
                        _ITUNES_CACHE.pop(next(iter(_ITUNES_CACHE)))
                    return url
    except Exception:
        pass
    return None
    
avatar_cooldown_time = None

PERIOD_MAP = {
    '7d': ('7day', 'Last 7 Days'), '7day': ('7day', 'Last 7 Days'),
    '1m': ('1month', 'Last Month'), '1month': ('1month', 'Last Month'),
    '3m': ('3month', 'Last 3 Months'), '3month': ('3month', 'Last 3 Months'),
    '6m': ('6month', 'Last 6 Months'), '6month': ('6month', 'Last 6 Months'),
    '1y': ('12month', 'Last Year'), '12m': ('12month', 'Last Year'), '12month': ('12month', 'Last Year'),
    'all': ('overall', 'All Time'), 'overall': ('overall', 'All Time'),
    'at': ('overall', 'All Time')
}

def get_period_data(input_period):
    if not input_period: return 'overall', 'All Time'
    input_lower = input_period.lower()
    if input_lower.isdigit() and len(input_lower) == 4:
        return input_lower, f"Year {input_lower}"
    return PERIOD_MAP.get(input_lower, ('overall', 'All Time'))

def get_medal(index):
    return f"` {index+1}. `"

# --- SUGGESTION VIEW & MODAL ---
class SuggestionFeedbackModal(discord.ui.Modal, title="Admin Feedback"):
    feedback = discord.ui.TextInput(
        label="Feedback Message",
        style=discord.TextStyle.long,
        placeholder="Type your reply to the user here... (Optional)",
        required=False
    )

    def __init__(self, action_status: str, action_color: discord.Color, action_emoji: str, db_status: str, is_bug: bool = False):
        super().__init__()
        self.action_status = action_status
        self.action_color = action_color
        self.action_emoji = action_emoji
        self.db_status = db_status
        self.is_bug = is_bug

    async def on_submit(self, interaction: discord.Interaction):
        embed = interaction.message.embeds[0]
        try:
            suggester_id = int(embed.author.name.split('(')[-1].strip(')'))
        except:
            suggester_id = None
            
        feedback_text = self.feedback.value

        # Update Database
        global db_pool
        if db_pool and suggester_id:
            import asyncpg
            if isinstance(db_pool, asyncpg.pool.Pool):
                async with db_pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE suggestions SET status = $1, admin_feedback = $2, updated_at = CURRENT_TIMESTAMP WHERE user_id = $3 AND description = $4",
                        self.db_status, feedback_text, str(suggester_id), embed.description
                    )

        # Update Message
        embed.color = self.action_color
        embed.add_field(name="Status", value=f"{self.action_emoji} **{self.action_status}**", inline=False)
        if feedback_text:
            embed.add_field(name="Your Reply", value=feedback_text, inline=False)
            
        view = discord.ui.View() # Empty view removes buttons
        await interaction.response.edit_message(embed=embed, view=view)

        # DM User
        if suggester_id:
            try:
                suggester = await interaction.client.fetch_user(suggester_id)
                item_type = "bug report" if self.is_bug else "suggestion"
                desc_lines = [f"Your {item_type} has been marked as **{self.action_status.upper()}**.", f"", f"**Your Report:**" if self.is_bug else f"**Your Idea:**", embed.description]
                title = f"{self.action_emoji} Bug Report Update" if self.is_bug else f"{self.action_emoji} Suggestion Update"
                notify_embed = Theme.get_embed(title=title, description=chr(10).join(desc_lines), color=self.action_color)
                if feedback_text:
                    notify_embed.add_field(name="Developer Reply", value=feedback_text, inline=False)
                notify_embed.set_footer(text="DJ Scratch Feedback System")
                await suggester.send(embed=notify_embed)
                print(f"Notified user about {item_type}: {self.action_status}")
            except:
                pass
        try:
            log_title = "Bug Report Updated (Admin)" if self.is_bug else "Suggestion Updated (Admin)"
            log_embed = Theme.get_embed(title=log_title, description=embed.description, color=self.action_color)
            log_embed.add_field(name="Status", value=f"{self.action_emoji} **{self.action_status}**", inline=False)
            if feedback_text:
                log_embed.add_field(name="Reply", value=feedback_text, inline=False)
            log_embed.set_footer(text=f"User ID: {suggester_id or 'Unknown'}")
            await log_to_channel("website-log", log_embed)
        except: pass

class SuggestionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="sugg_approve")
    async def approve_btn(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.send_modal(SuggestionFeedbackModal("Approved", discord.Color.green(), "🟢", "approved"))

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="sugg_deny")
    async def deny_btn(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.send_modal(SuggestionFeedbackModal("Denied", discord.Color.red(), "🔴", "denied"))
        
    @discord.ui.button(label="Released", style=discord.ButtonStyle.primary, custom_id="sugg_released")
    async def released_btn(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.send_modal(SuggestionFeedbackModal("Update Released", discord.Color.blurple(), "🚀", "completed"))

class BugReportView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Fixed", style=discord.ButtonStyle.success, custom_id="bug_fixed")
    async def fixed_btn(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.send_modal(SuggestionFeedbackModal("Fixed", discord.Color.green(), "🛠️", "completed", is_bug=True))

    @discord.ui.button(label="Not a Bug", style=discord.ButtonStyle.danger, custom_id="bug_not_a_bug")
    async def not_a_bug_btn(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.send_modal(SuggestionFeedbackModal("Not a Bug", discord.Color.red(), "❌", "denied", is_bug=True))
        
    @discord.ui.button(label="Investigating", style=discord.ButtonStyle.primary, custom_id="bug_investigating")
    async def investigating_btn(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.send_modal(SuggestionFeedbackModal("Investigating", discord.Color.blurple(), "🔍", "approved", is_bug=True))

def make_fast_session():
    # Shared connector: connection reuse + DNS cache = much faster API calls.
    connector = aiohttp.TCPConnector(limit=100, limit_per_host=20, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=8, connect=3, sock_read=5)
    return aiohttp.ClientSession(connector=connector, timeout=timeout)

async def setup_hook():
    if getattr(bot, 'session', None) is None or getattr(bot.session, 'closed', True):
        bot.session = make_fast_session()
    bot.add_view(SuggestionView())
    bot.add_view(BugReportView())
    try:
        from src.commands.settings import SettingsView
        bot.add_view(SettingsView())
    except Exception as e:
        print("Failed to add SettingsView:", e)
    global db_pool
    db_url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    if db_url:
        try:
            if "pooler.supabase.com" in db_url and ":5432" in db_url:
                db_url = db_url.replace(":5432", ":6543")
            db_pool = await asyncpg.create_pool(dsn=db_url, ssl="require", min_size=1, max_size=5, statement_cache_size=0)
            
            import src.core.database as db_module
            db_module.db_pool = db_pool
            await db_module.init_name_cache()
            
            print(f"{Log.GREEN}>>> Connected to Postgres DB{Log.RESET}")
            async with db_pool.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_settings (
                        user_id VARCHAR(255) PRIMARY KEY,
                        fm_mode VARCHAR(50) DEFAULT 'full',
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                try:
                    await conn.execute("ALTER TABLE user_settings ADD CONSTRAINT user_settings_user_id_key UNIQUE (user_id)")
                except Exception:
                    pass
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS website_logs (
                        id SERIAL PRIMARY KEY,
                        user_id TEXT,
                        username TEXT,
                        action TEXT,
                        details TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                try:
                    await conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS show_features BOOLEAN DEFAULT FALSE")
                except Exception as e:
                    pass
                    
                try:
                    await conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS data_source VARCHAR(20) DEFAULT 'combined'")
                except Exception as e:
                    pass
                    
                try:
                    await conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS private_mode BOOLEAN DEFAULT FALSE")
                except Exception as e:
                    pass
                    
                try:
                    await conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS timezone TEXT DEFAULT 'UTC'")
                except Exception as e:
                    pass
                    
                try:
                    await conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS lastfm_username TEXT")
                except Exception as e:
                    pass
                    
                try:
                    await conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS show_track_playcount BOOLEAN DEFAULT TRUE")
                except Exception as e:
                    pass
                    
                try:
                    await conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS update_notifs BOOLEAN DEFAULT TRUE")
                except Exception as e:
                    pass
                    
                try:
                    await conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS last_update_seen TEXT DEFAULT ''")
                except Exception as e:
                    pass

                try:
                    await conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS spotify_refresh_token TEXT")
                except Exception as e:
                    pass

                try:
                    await conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS last_active TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP")
                except Exception as e:
                    pass

                try:
                    await conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS purge_warning_sent BOOLEAN DEFAULT FALSE")
                except Exception: pass
                
                try:
                    await conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP")
                except Exception: pass
                    
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS global_settings (
                        key VARCHAR(255) PRIMARY KEY,
                        value TEXT
                    )
                    """
                )
                
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS suggestions (
                        id SERIAL PRIMARY KEY,
                        user_id VARCHAR(255) NOT NULL,
                        username VARCHAR(255) NOT NULL,
                        title VARCHAR(255) NOT NULL,
                        description TEXT NOT NULL,
                        status VARCHAR(50) DEFAULT 'pending',
                        admin_feedback TEXT,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS bot_actions (
                        id SERIAL PRIMARY KEY,
                        action_type VARCHAR(50) NOT NULL,
                        status VARCHAR(20) DEFAULT 'PENDING',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS command_usage (
                        command_name VARCHAR(100) PRIMARY KEY,
                        usage_count INT DEFAULT 0
                    )
                    """
                )
                
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS server_crowns (
                        guild_id VARCHAR(255),
                        user_id VARCHAR(255),
                        artist_name VARCHAR(255),
                        plays INT,
                        PRIMARY KEY (guild_id, artist_name)
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS crown_history (
                        id SERIAL PRIMARY KEY,
                        guild_id VARCHAR(255),
                        artist_name VARCHAR(255),
                        previous_user_id VARCHAR(255),
                        new_user_id VARCHAR(255),
                        plays INT,
                        stolen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                
                try:
                    await conn.execute("ALTER TABLE server_crowns ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP")
                except Exception as e:
                    print(f"{Log.RED}>>> Failed to add claimed_at column to server_crowns: {e}{Log.RESET}")
                
                try:
                    await conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS lastfm_username VARCHAR(255)")
                except Exception as e:
                    print(f"{Log.RED}>>> Failed to add lastfm_username column: {e}{Log.RESET}")
                    
                try:
                    await conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS timezone VARCHAR(50) DEFAULT 'UTC'")
                except Exception as e:
                    print(f"{Log.RED}>>> Failed to add timezone column: {e}{Log.RESET}")

                try:
                    await conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS show_track_playcount BOOLEAN DEFAULT TRUE")
                except Exception as e:
                    print(f"{Log.RED}>>> Failed to add show_track_playcount column: {e}{Log.RESET}")
                
                try:
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS server_settings (
                            guild_id TEXT PRIMARY KEY,
                            prefix TEXT DEFAULT ','
                        )
                    """)
                except Exception as e:
                    print(f"{Log.RED}>>> Failed to create server_settings table: {e}{Log.RESET}")

                # Hot-path indexes (IF NOT EXISTS = safe to run every boot).
                # listens(user_id, played_at) powers /fm, tops, streaks, whoknows.
                for _idx_sql in (
                    "CREATE INDEX IF NOT EXISTS idx_listens_user_played ON listens (user_id, played_at DESC)",
                    "CREATE INDEX IF NOT EXISTS idx_listens_track ON listens (track_id)",
                    "CREATE INDEX IF NOT EXISTS idx_tracks_names ON tracks (artist_name, track_name, album_name)",
                    "CREATE INDEX IF NOT EXISTS idx_server_crowns_guild ON server_crowns (guild_id)",
                ):
                    try:
                        await conn.execute(_idx_sql)
                    except Exception:
                        pass

                # One-time migration
                if os.path.exists("lastfm_users.json"):
                    try:
                        with open("lastfm_users.json", "r") as f:
                            old_users = json.load(f)
                        for uid, uname in old_users.items():
                            await conn.execute(
                                "INSERT INTO user_settings (user_id, lastfm_username) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET lastfm_username = EXCLUDED.lastfm_username",
                                str(uid), uname
                            )
                        os.rename("lastfm_users.json", "lastfm_users.json.bak")
                        print(f"{Log.GREEN}>>> Migrated lastfm_users.json to Postgres!{Log.RESET}")
                    except Exception as e:
                        print(f"{Log.RED}>>> Failed to migrate JSON: {e}{Log.RESET}")

                print(f"{Log.GREEN}>>> Ensured user_settings table exists{Log.RESET}")
            bot.db_pool = db_pool
            bot.get_avatar_cooldown = get_avatar_cooldown
            bot.get_user_fm_mode = get_user_fm_mode
            bot.process_fm = process_fm
            bot.process_top_artists = process_top_artists
            bot.process_top_tracks = process_top_tracks
            bot.process_artist_tracks = process_artist_tracks
            bot.process_recent = process_recent
            bot.process_judge = process_judge
            bot.process_receipt = process_receipt
            bot.process_profile = process_profile
            bot.process_whoknows = process_whoknows
            bot.process_whoknowstrack = process_whoknowstrack
            bot.process_whoknowsalbum = process_whoknowsalbum
            bot.process_taste = process_taste
            bot.process_suggestion = process_suggestion
            bot.get_help_embed = get_help_embed
            bot.process_crowns = process_crowns
            bot.process_crownseeder = process_crownseeder
            bot.process_killallcrowns = process_killallcrowns
            bot.process_chart = process_chart
            bot.process_artist_chart = process_artist_chart
            bot.process_streak = process_streak
            bot.process_streak_history = process_streak_history
            
            from src.core.server_leaderboards import process_server_artists, process_server_albums, process_server_tracks
            bot.process_server_artists = process_server_artists
            bot.process_server_albums = process_server_albums
            bot.process_server_tracks = process_server_tracks
            
            from src.core.global_whoknows import process_global_whoknows, process_global_whoknowstrack, process_global_whoknowsalbum
            bot.process_global_whoknows = process_global_whoknows
            bot.process_global_whoknowstrack = process_global_whoknowstrack
            bot.process_global_whoknowsalbum = process_global_whoknowsalbum
            bot.handle_discord_import = handle_discord_import
            bot.PurgeConfirmView = PurgeConfirmView
            bot.add_custom_reactions = add_custom_reactions
            bot.save_user = save_user

            cogs = ['cogs.admin', 'src.commands.admin_ipc', 'src.commands.lastfm', 'src.commands.importer', 'src.commands.settings', 'src.commands.info', 'src.commands.games', 'src.commands.spotify_remote', 'src.commands.social', 'src.commands.status']
            for cog in cogs:
                try:
                    await bot.load_extension(cog)
                    print(f"{Log.GREEN}>>> Loaded {cog}{Log.RESET}")
                except Exception as e:
                    print(f"{Log.RED}>>> Failed to load {cog}: {e}{Log.RESET}")
                    
            if getattr(bot, 'is_test_bot', False):
                test_dir = os.path.join(os.path.dirname(__file__), "..", "test_commands")
                if os.path.exists(test_dir):
                    for filename in os.listdir(test_dir):
                        if filename.endswith('.py') and not filename.startswith('__'):
                            base_name = filename[:-3]
                            test_cog = f"src.test_commands.{base_name}"
                            
                            # Check if this overrides an existing command
                            possible_overrides = [f"src.commands.{base_name}", f"cogs.{base_name}"]
                            for original_cog in possible_overrides:
                                if original_cog in bot.extensions:
                                    try:
                                        await bot.unload_extension(original_cog)
                                        print(f"{Log.YELLOW}>>> Unloaded original {original_cog} for test override{Log.RESET}")
                                    except Exception as e:
                                        pass
                            
                            try:
                                await bot.load_extension(test_cog)
                                print(f"{Log.MAGENTA}>>> Loaded TEST feature {test_cog}{Log.RESET}")
                            except Exception as e:
                                print(f"{Log.RED}>>> Failed to load TEST feature {test_cog}: {e}{Log.RESET}")

            
        except Exception as e:
            print(f"{Log.RED}>>> Failed to connect to DB: {e}{Log.RESET}")
    else:
        print(f"{Log.RED}>>> No DATABASE_URL or POSTGRES_URL set — DB disabled{Log.RESET}")
bot.setup_hook = setup_hook

db_pool = None







import discord
import json
import ijson
import zipfile
import io
import uuid
import csv
from datetime import datetime
from .database import *
def parse_apple_music_csv(file_obj, user):
    import re
    reader = csv.DictReader(file_obj)
    for row in reader:
        artist = row.get("Container Artist Name") or row.get("Artist Name")
        title = row.get("Song Name")
        album = row.get("Album Name") or ""
        played_at_raw = row.get("Event Start Timestamp")
        play_dur = row.get("Play Duration Milliseconds")
        media_dur = row.get("Media Duration In Milliseconds")
        
        if not artist or not title or not played_at_raw:
            continue

        album = re.sub(r'(?i)\s*-\s*EP$', '', album)
        album = re.sub(r'(?i)\s*-\s*Single$', '', album)
        album = album.strip()
            
        ms_played = 0
        try:
            if play_dur:
                ms_played = int(play_dur)
                if ms_played < 30000:
                    continue
                if media_dur and ms_played <= 240000:
                    media_len = int(media_dur)
                    if ms_played <= (media_len / 2):
                        continue
        except:
            pass
            
        try:
            cleaned_time = played_at_raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned_time)
            end_dt = dt + timedelta(milliseconds=ms_played)
            yield (str(user.id), artist, title, album, end_dt, ms_played)
        except Exception as e:
            continue

def stream_parse_spotify_json(file_obj):
    import ijson
    try:
        for track in ijson.items(file_obj, 'item'):
            yield track
    except Exception as e:
        print(f"Error parsing JSON stream: {e}")
def parse_single_spotify_track(user, track):
    import re
    artist = track.get("master_metadata_album_artist_name")
    title = track.get("master_metadata_track_name")
    album = track.get("master_metadata_album_album_name") or ""
    played_at_raw = track.get("ts")
    ms_played = track.get("ms_played") or 0
    spotify_uri = track.get("spotify_track_uri")

    if not artist or not title or not played_at_raw or ms_played < 30000:
        return None

    album = re.sub(r'(?i)\s*-\s*EP$', '', album)
    album = re.sub(r'(?i)\s*-\s*Single$', '', album)
    album = album.strip()

    try:
        cleaned_time = played_at_raw.replace("Z", "+00:00")
        if " " in cleaned_time and "T" not in cleaned_time:
            parts = cleaned_time.split(":")
            if len(parts) == 2:
                cleaned_time = cleaned_time + ":00"
        try:
            dt = datetime.fromisoformat(cleaned_time)
        except:
            try:
                dt = datetime.strptime(cleaned_time, "%Y-%m-%d %H:%M:%S")
            except:
                dt = datetime.strptime(cleaned_time, "%Y-%m-%d %H:%M")
        return (str(user.id), artist, title, album, dt, ms_played, spotify_uri)
    except:
        return None
async def insert_tracks_in_db(valid_tracks):
    if not valid_tracks:
        return 0

    valid_tracks.sort(key=lambda x: x[4])
    
    filtered_tracks = []
    last_end = None
    
    for t in valid_tracks:
        user_id, artist, title, album, end_dt, ms_played, spotify_uri = t
        start_dt = end_dt - timedelta(milliseconds=ms_played)
        
        if last_end is None or start_dt >= (last_end - timedelta(seconds=15)):
            filtered_tracks.append((user_id, artist, title, album, end_dt, ms_played, spotify_uri))
            last_end = end_dt
        else:
            pass
            
    if not filtered_tracks:
        return 0

    chunk_size = 1000
    inserted_count = 0
    for i in range(0, len(filtered_tracks), chunk_size):
        chunk = filtered_tracks[i:i + chunk_size]
        try:
            async with db_pool.acquire() as conn:
                async with conn.transaction():
                    unique_tracks = list({(c[1], c[2], c[3] or '') for c in chunk})
                    await conn.executemany(
                        """
                        INSERT INTO tracks (artist_name, track_name, album_name)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (artist_name, track_name, album_name) DO NOTHING
                        """,
                        unique_tracks
                    )
                    
                    await conn.executemany(
                        """
                        INSERT INTO listens (user_id, track_id, played_at, ms_played, spotify_uri)
                        SELECT $1, t.id, $5, $6, $7
                        FROM tracks t
                        WHERE t.artist_name = $2 AND t.track_name = $3 AND t.album_name = COALESCE($4, '')
                        ON CONFLICT (user_id, track_id, played_at) DO NOTHING
                        """,
                        chunk
                    )
                inserted_count += len(chunk)
                print(f"    [IMPORT PROGRESS] Inserted chunk... ({inserted_count} valid non-overlapping tracks so far)")
        except Exception as e:
            print(f"{Log.RED}>>> Error inserting database chunk: {e}{Log.RESET}")
    return inserted_count
async def process_discord_import_in_background(user, temp_filepath, is_zip, response_target):
    import zipfile
    import os
    import gc
    import io

    processed_count = 0
    try:
        # Ensure user exists in imported_users table
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO imported_users (id, username)
                    VALUES ($1, $2)
                    ON CONFLICT (id) DO UPDATE SET username = EXCLUDED.username
                    """,
                    str(user.id), format_name(user)
                )
        except Exception as e:
            print(f"{Log.RED}>>> Error ensuring imported_user: {e}{Log.RESET}")



        all_valid_tracks = []
        
        async def flush_tracks():
            nonlocal processed_count
            if all_valid_tracks:
                processed_count += await insert_tracks_in_db(all_valid_tracks)
                all_valid_tracks.clear()
                gc.collect()
        # Parse and process
        if not is_zip:
            if temp_filepath.lower().endswith(".csv"):
                with open(temp_filepath, "r", encoding="utf-8", errors="ignore") as f:
                    for idx, parsed in enumerate(parse_apple_music_csv(f, user)):
                            all_valid_tracks.append(parsed)
                            if len(all_valid_tracks) >= 25000:
                                await flush_tracks()
                            if idx % 1000 == 0:
                                await asyncio.sleep(0)
            else:
                # Process single JSON file from disk using our zero-RAM streaming parser
                with open(temp_filepath, "rb") as f:
                    for idx, track in enumerate(stream_parse_spotify_json(f)):
                        parsed = parse_single_spotify_track(user, track)
                        if parsed:
                            all_valid_tracks.append(parsed)
                        if len(all_valid_tracks) >= 25000:
                            await flush_tracks()
                        if idx % 1000 == 0:
                            await asyncio.sleep(0)
        else:
            # Process ZIP file entry by entry from disk using our zero-RAM streaming parser
            with zipfile.ZipFile(temp_filepath) as z:
                # fmbot logic: Reject Account Data packages which contain Userdata and lack album names
                if any("userdata" in name.lower() for name in z.namelist()):
                    try:
                        os.remove(temp_filepath)
                    except: pass
                    
                    embed = Theme.get_embed(
                        title="❌ Invalid Export Package",
                        description="You uploaded the **Account Data** package, which is missing album names and contains duplicates.\\n\\nPlease go to Spotify Privacy settings and request the **Extended streaming history** instead.",
                        color=discord.Color.red(),

                    )
                    await user.send(embed=embed)
                    return

                if any(name.endswith("Apple Music Play Activity.csv") for name in z.namelist()):
                    for filename in z.namelist():
                        if filename.endswith("Apple Music Play Activity.csv"):
                            with z.open(filename) as f:
                                text_stream = io.TextIOWrapper(f, encoding="utf-8", errors="ignore")
                                for idx, parsed in enumerate(parse_apple_music_csv(text_stream, user)):
                                    all_valid_tracks.append(parsed)
                                    if len(all_valid_tracks) >= 25000:
                                        await flush_tracks()
                                    if idx % 1000 == 0:
                                        await asyncio.sleep(0)
                elif any(name.endswith("Apple_Media_Services.zip") for name in z.namelist()):
                    inner_zip_name = next(name for name in z.namelist() if name.endswith("Apple_Media_Services.zip"))
                    with z.open(inner_zip_name) as inner_f:
                        with zipfile.ZipFile(io.BytesIO(inner_f.read())) as inner_z:
                            for filename in inner_z.namelist():
                                if filename.endswith("Apple Music Play Activity.csv"):
                                    with inner_z.open(filename) as f:
                                        text_stream = io.TextIOWrapper(f, encoding="utf-8", errors="ignore")
                                        for idx, parsed in enumerate(parse_apple_music_csv(text_stream, user)):
                                            all_valid_tracks.append(parsed)
                                            if len(all_valid_tracks) >= 25000:
                                                await flush_tracks()
                                            if idx % 1000 == 0:
                                                await asyncio.sleep(0)
                else:
                    for filename in z.namelist():
                        if filename.endswith(".json") and any(x in filename for x in ["StreamingHistory", "endsong", "Streaming_History"]):
                            try:
                                with z.open(filename) as f:
                                    for idx, track in enumerate(stream_parse_spotify_json(f)):
                                        parsed = parse_single_spotify_track(user, track)
                                        if parsed:
                                            all_valid_tracks.append(parsed)
                                        if len(all_valid_tracks) >= 25000:
                                            await flush_tracks()
                                        if idx % 1000 == 0:
                                            await asyncio.sleep(0)
                            except Exception as e:
                                print(f"{Log.RED}>>> Error processing {filename} inside zip: {e}{Log.RESET}")

        await flush_tracks()

        # Delete temp file
        try:
            os.remove(temp_filepath)
        except: pass

        # Send DM when finished
        embed = Theme.get_embed(
            title="✅ Spotify Import Complete!",
            description=(
                f"Hey **{format_name(user)}**, your Spotify history has finished importing!\n\n"
                f"• **{processed_count:,}** tracks processed successfully.\n\n"
                f"You can now use bot commands like `/profile` or `/topartists`!"
            ),
            color=0x2ecc71,

        )
        await user.send(embed=embed)

    except Exception as e:
        print(f"{Log.RED}>>> Error in background import process: {e}{Log.RESET}")
        try:
            os.remove(temp_filepath)
        except: pass
        try:
            await user.send(f"❌ An error occurred during the background import of your Spotify data: {e}")
        except: pass
async def handle_discord_import(user, attachment, response_target):
    try:
        is_zip = attachment.filename.endswith(".zip")
        ext = 'zip' if is_zip else ('csv' if attachment.filename.endswith('.csv') else 'json')
        temp_filepath = f"temp_import_{user.id}_{attachment.id}.{ext}"
        
        # Save attachment directly to disk in streamed mode
        await attachment.save(temp_filepath)
        
        # Add to import queue instead of processing immediately
        await import_queue.put((user, temp_filepath, is_zip, response_target))
        queue_pos = import_queue.qsize()
        
        await response_target(f"✅ File received successfully! You are currently position **#{queue_pos}** in the import queue. The bot will process your history in the background and DM you when finished.")
    except Exception as e:
        print(f"{Log.RED}>>> Error in handle_discord_import saving file: {e}{Log.RESET}")
        await response_target("❌ An error occurred while receiving your file.")
async def handle_discord_import_link(user, link, response_target):
    try:
        is_zip = link.lower().endswith(".zip") or "zip" in link.lower()
        ext = 'zip' if is_zip else ('csv' if '.csv' in link.lower() else 'json')
        temp_filepath = f"temp_import_{user.id}_link.{ext}"
        
        await response_target("⏳ Downloading file from link... (This may take a moment for large files)")
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(link) as resp:
                if resp.status != 200:
                    await response_target("❌ Failed to download from the provided link. Please ensure it is a direct download link.")
                    return
                with open(temp_filepath, 'wb') as f:
                    while True:
                        chunk = await resp.content.read(65536)
                        if not chunk: break
                        f.write(chunk)
        
        await import_queue.put((user, temp_filepath, is_zip, response_target))
        queue_pos = import_queue.qsize()
        
        await response_target(f"✅ Link downloaded successfully! You are currently position **#{queue_pos}** in the import queue. The bot will DM you when finished.")
        
    except Exception as e:
        print(f"{Log.RED}>>> Error in handle_discord_import_link: {e}{Log.RESET}")
        await response_target("❌ An error occurred while downloading or processing the link.")
import_queue = asyncio.Queue()

async def import_worker():
    while True:
        user, temp_filepath, is_zip, response_target = await import_queue.get()
        print(f"{Log.CYAN}>>> [IMPORT QUEUE] Starting import for {format_name(user)} ({user.id}). Items left in queue: {import_queue.qsize()}{Log.RESET}")
        
        try:
            log_channel = bot.get_channel(1517288950522187947)
            if log_channel:
                await log_channel.send(f"📥 **{format_name(user)}** (`{user.id}`) data is currently importing. Items left in queue: **{import_queue.qsize()}**")
        except Exception as e:
            pass

        try:
            await process_discord_import_in_background(user, temp_filepath, is_zip, response_target)
        except Exception as e:
            print(f"{Log.CYAN}>>> [IMPORT QUEUE] Error processing import for {format_name(user)}: {e}{Log.RESET}")
            try:
                log_channel = bot.get_channel(1517288950522187947)
                if log_channel:
                    await log_channel.send(f"❌ Error importing data for **{format_name(user)}** (`{user.id}`): {e}")
            except Exception:
                pass
        finally:
            import_queue.task_done()
            print(f"{Log.CYAN}>>> [IMPORT QUEUE] Finished import task for {format_name(user)}.{Log.RESET}")
            
            try:
                log_channel = bot.get_channel(1517288950522187947)
                if log_channel:
                    await log_channel.send(f"✅ Finished import task for **{format_name(user)}** (`{user.id}`).")
            except Exception:
                pass

async def web_import_worker():
    import tempfile
    import os
    import asyncio
    from .database import db_pool
    
    while True:
        try:
            if db_pool:
                async with db_pool.acquire() as conn:
                    records = await conn.fetch("SELECT * FROM import_jobs WHERE status = 'ready' ORDER BY created_at ASC")
                    for record in records:
                        job_id = record['id']
                        user_id_str = record['user_id']
                        filename = record['filename']
                        
                        await conn.execute("UPDATE import_jobs SET status = 'processing' WHERE id = $1", job_id)
                        
                        user = bot.get_user(int(user_id_str))
                        if user:
                            try:
                                await user.send(f"📥 Your web dashboard upload `{filename}` has been received! You've been added to the import queue.")
                            except: pass
                        
                        temp_dir = tempfile.gettempdir()
                        temp_filepath = os.path.join(temp_dir, f"web_import_{job_id}_{filename}")
                        
                        chunks = await conn.fetch("SELECT data FROM import_chunks WHERE job_id = $1 ORDER BY chunk_index ASC", job_id)
                        with open(temp_filepath, 'wb') as f:
                            for chunk in chunks:
                                f.write(chunk['data'])
                        
                        await conn.execute("DELETE FROM import_chunks WHERE job_id = $1", job_id)
                        await conn.execute("UPDATE import_jobs SET status = 'completed' WHERE id = $1", job_id)
                        
                        is_zip = filename.lower().endswith(".zip")
                        if user:
                            await import_queue.put((user, temp_filepath, is_zip, None))
                        else:
                            os.remove(temp_filepath)
                            
        except Exception as e:
            print(f"{Log.RED}>>> Error in web_import_worker: {e}{Log.RESET}")
            
        await asyncio.sleep(10)


async def spotify_track_length_scanner():
    """Background task to sync track lengths from Spotify API and delete invalid scrobbles."""
    from .database import db_pool
    from src.utils.spotify import fetch_spotify_track_durations
    total_processed = 0
    was_processing = False
    while True:
        try:
            if db_pool: # Dummy comment to trigger webhook
                async with db_pool.acquire() as conn:
                    # Fetch up to 2500 unique tracks to process
                    rows = await conn.fetch("SELECT ctid, spotify_uri, ms_played FROM listens WHERE spotify_uri IS NOT NULL AND spotify_uri NOT LIKE 'VALID_%' LIMIT 2500")
                    if rows:
                        was_processing = True
                        uris = [row['spotify_uri'] for row in rows]
                        
                        # Split URIs into chunks of 50 for Spotify API
                        chunks = [uris[i:i + 50] for i in range(0, len(uris), 50)]
                        
                        durations = {}
                        sem = asyncio.Semaphore(5)
                        
                        async def process_chunk(chunk):
                            async with sem:
                                res = await fetch_spotify_track_durations(chunk, user_id=str(OWNER_ID))
                                await asyncio.sleep(0.15)
                                return res

                        tasks = [process_chunk(chunk) for chunk in chunks]
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                        
                        hit_rate_limit = False
                        for res in results:
                            if isinstance(res, dict):
                                durations.update(res)
                            elif res is None:
                                hit_rate_limit = True
                                
                        delete_ctids = []
                        update_ctids = []
                        
                        for row in rows:
                            uri = row['spotify_uri']
                            ctid = row['ctid']
                            ms_played = row['ms_played']
                            
                            if uri in durations:
                                duration_ms = durations[uri]
                                # Apply 50% rule or 4 minutes
                                if ms_played < duration_ms / 2 and ms_played < 240000:
                                    delete_ctids.append(ctid)
                                else:
                                    update_ctids.append(ctid)
                            # If not found or chunk failed, we leave it to retry later
                        
                        if delete_ctids:
                            await conn.execute("DELETE FROM listens WHERE ctid = ANY($1::tid[])", delete_ctids)
                        if update_ctids:
                            await conn.execute("UPDATE listens SET spotify_uri = 'VALID_' || spotify_uri WHERE ctid = ANY($1::tid[])", update_ctids)
                            
                        processed_this_batch = len(delete_ctids) + len(update_ctids)
                        if processed_this_batch > 0:
                            total_processed += processed_this_batch
                            total_remaining = await conn.fetchval("SELECT count(*) FROM listens WHERE spotify_uri IS NOT NULL AND spotify_uri NOT LIKE 'VALID_%'")
                            print(f"{Log.CYAN}>>> [BACKGROUND SCANNER] Processed {processed_this_batch} tracks. Total this session: {total_processed} | Remaining globally: {total_remaining}{Log.RESET}")
                        
                        if hit_rate_limit:
                            from src.utils.spotify import _spotify_rate_limit_until
                            if _spotify_rate_limit_until and datetime.now() < _spotify_rate_limit_until:
                                wait_secs = int((_spotify_rate_limit_until - datetime.now()).total_seconds()) + 1
                                print(f"{Log.YELLOW}>>> [BACKGROUND SCANNER] Spotify API rate-limited (429). Pausing for {wait_secs}s to let limit reset...{Log.RESET}")
                                await asyncio.sleep(wait_secs)
                            else:
                                print(f"{Log.YELLOW}>>> [BACKGROUND SCANNER] Spotify API request or auth refresh failed. Re-link Spotify if this persists. Pausing for 30s...{Log.RESET}")
                                await asyncio.sleep(30)
                        else:
                            await asyncio.sleep(0.1)
                    else:
                        if was_processing:
                            print(f"{Log.GREEN}>>> [BACKGROUND SCANNER] Queue completely empty! All tracks have been validated and filtered.{Log.RESET}")
                            was_processing = False
                        await asyncio.sleep(60)
            else:
                await asyncio.sleep(60)
        except Exception as e:
            print(f"{Log.RED}>>> Error in spotify_track_length_scanner: {e}{Log.RESET}")
            await asyncio.sleep(60)

@bot.event
async def on_ready():
    print(r"""
========================================================================
    ____      __   _____                 __       __  
   / __ \    / /  / ___/______________ _/ /______/ /_ 
  / / / /_  / /   \__ \/ ___/ ___/ __ `/ __/ ___/ __ \
 / /_/ / /_/ /   ___/ / /__/ /  / /_/ / /_/ /__/ / / /
/_____/\____/   /____/\___/_/   \__,_/\__/\___/_/ /_/ 
========================================================================""")
    print(f"{Log.GREEN}[OK] ONLINE AS: {bot.user}{Log.RESET}")
    total_servers = len(bot.guilds)
    total_members = sum(g.member_count for g in bot.guilds if g.member_count)
    print(f"{Log.GREEN}[OK] CONNECTED TO: {total_servers} servers | {total_members} members{Log.RESET}")
    print(f"{Log.GREEN}[OK] SYNCED COMMANDS: {len(bot.tree.get_commands())} global commands{Log.RESET}")
    print(f"{Log.YELLOW}! NOTE: Slash commands do not auto-sync. Run ',sync' in Discord if needed.{Log.RESET}")
    


    if getattr(bot, 'is_test_bot', False):
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.playing, name="Use the main bot: DJ Scratch!"))
        print(f"{Log.GREEN}>>> Set test bot promotional status.{Log.RESET}")
    else:
        from .database import db_pool
        if db_pool:
            try:
                async with db_pool.acquire() as conn:
                    row = await conn.fetchrow("SELECT value FROM global_settings WHERE key = 'bot_status'")
                    if row and row['value']:
                        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=row['value']))
                        print(f"{Log.GREEN}>>> Restored bot status to: {row['value']}{Log.RESET}")
            except Exception as e:
                print(f"{Log.RED}>>> Failed to load bot status from DB: {e}{Log.RESET}")

    bot.loop.create_task(import_worker())
    bot.loop.create_task(web_import_worker())
    bot.loop.create_task(spotify_track_length_scanner())

@bot.check
async def global_test_bot_check(ctx):
    if getattr(bot, 'is_test_bot', False):
        if ctx.author.id != OWNER_ID:
            raise commands.CheckFailure("This is the test bot. Only the developer can use it!")
    return True


async def global_test_bot_interaction_check(interaction: discord.Interaction):
    if getattr(bot, 'is_test_bot', False):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ This is the beta test bot. Only the developer can use it!", ephemeral=True)
            return False
    return True
@bot.event
async def on_guild_join(guild):
    print(f"JOINED GUILD: {guild.name} ({guild.id}) - {guild.member_count} members")
    
    try:
        target_channel = guild.system_channel
        if not target_channel or not target_channel.permissions_for(guild.me).send_messages:
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    target_channel = channel
                    break
                    
        if target_channel:
            info_cog = bot.get_cog("InfoCog")
            if info_cog:
                await info_cog.send_guide(target_channel)
    except Exception as e:
        print(f"Failed to send guide in {guild.name}: {e}")
        
    try:
        owner = await bot.fetch_user(OWNER_ID)
        embed = Theme.get_embed(
            title="📥 Joined New Server!",
            description=f"**Name:** {guild.name}\n**ID:** `{guild.id}`\n**Members:** {guild.member_count}\n**Owner:** {guild.owner if guild.owner else 'Unknown'}",
            color=discord.Color.green()
        )
        if guild.icon: embed.set_thumbnail(url=guild.icon.url)
        await owner.send(embed=embed)
        await log_to_channel("guild-join", embed)
    except Exception as e: print(f"{Log.RED}>>> Failed to notify owner of guild join: {e}{Log.RESET}")

@bot.event
async def on_guild_remove(guild):
    print(f"LEFT GUILD: {guild.name} ({guild.id})")
    try:
        owner = await bot.fetch_user(OWNER_ID)
        embed = Theme.get_embed(
            title="📤 Left Server",
            description=f"**Name:** {guild.name}\n**ID:** `{guild.id}`",
            color=discord.Color.red()
        )
        if guild.icon: embed.set_thumbnail(url=guild.icon.url)
        await owner.send(embed=embed)
        await log_to_channel("guild-leave", embed)
    except Exception as e: print(f"{Log.RED}>>> Failed to notify owner of guild leave: {e}{Log.RESET}")

# --- HELPER: LOG TO CHANNEL ---
async def log_to_channel(channel_name: str, embed: discord.Embed):
    try:
        await bot.wait_until_ready()
        
        # Hardcoded specific log channels from owner
        channel_ids = {
            "guild-join": 1527127384535334954,
            "guild-leave": 1527127384535334955,
            "errors": 1527127384535334956
        }
        
        if channel_name in channel_ids:
            channel = bot.get_channel(channel_ids[channel_name])
            if channel:
                await channel.send(embed=embed)
                return

        # Fallback to older string search behavior (e.g. for website-log)
        target_guild_id = os.getenv("LOG_GUILD_ID")
        
        for guild in bot.guilds:
            if target_guild_id and str(guild.id) != target_guild_id:
                continue
            if not target_guild_id and str(guild.id) != "1360772594122358834":
                continue
                
            channel = discord.utils.get(guild.text_channels, name=channel_name)
            if channel:
                await channel.send(embed=embed)
                return
    except Exception as e:
        print(f"{Log.RED}>>> Failed to log to {channel_name}: {e}{Log.RESET}")

LAST_ERROR_TRACEBACK = None

# --- HELPER: ERROR DM ---
def _describe_error_source(source):
    """Build (user_line, location_line) for an error DM from a Context or Interaction."""
    user_line = "unknown user"
    location_line = "unknown location"
    try:
        user = getattr(source, 'author', None) or getattr(source, 'user', None)
        if user is not None:
            name = getattr(user, 'display_name', None) or getattr(user, 'name', '?')
            user_line = f"{user.mention} `{name}` (`{getattr(user, 'id', '?')}`)"
    except Exception:
        pass
    try:
        guild = getattr(source, 'guild', None)
        channel = getattr(source, 'channel', None)
        if guild is not None:
            location_line = f"**{guild.name}** (`{guild.id}`)"
        else:
            location_line = "DMs"
        if channel is not None:
            ch_name = getattr(channel, 'name', None)
            if ch_name:
                location_line += f" in #{ch_name} (`{channel.id}`)"
            else:
                location_line += f" (`{channel.id}`)"
        # What they actually ran / clicked.
        message = getattr(source, 'message', None)
        if message is not None and getattr(message, 'content', None):
            location_line += f"\n﹒`{message.content[:200]}`"
        jump = getattr(message, 'jump_url', None)
        if jump:
            location_line += f" ([jump]({jump}))"
    except Exception:
        pass
    return user_line, location_line


async def notify_owner(ctx, err, source=None):
    import traceback
    import io
    global LAST_ERROR_TRACEBACK
    print(f"ERROR in {ctx}: {err}")
    try:
        await bot.wait_until_ready()
        owner = await bot.fetch_user(OWNER_ID)
        tick = chr(96)
        code_block = tick + tick + tick

        err_to_trace = getattr(err, 'original', err)
        tb = "".join(traceback.format_exception(type(err_to_trace), err_to_trace, err_to_trace.__traceback__))
        LAST_ERROR_TRACEBACK = tb

        embed = Theme.get_embed(title="⚠️ Bot Error", color=discord.Color.red())

        if source is not None:
            user_line, location_line = _describe_error_source(source)
            embed.add_field(name="👤 User", value=user_line, inline=False)
            embed.add_field(name="📍 Where", value=location_line, inline=False)

        if len(tb) > 3800:
            embed.description = f"An error occurred in **{str(ctx)}**:\n*Traceback too long, attaching as file.*"
            file = discord.File(io.BytesIO(tb.encode('utf-8')), filename="traceback.txt")
            await owner.send(embed=embed, file=file)
            await log_to_channel("errors", embed)
        else:
            embed.description = f"An error occurred in **{str(ctx)}**:\n{code_block}py\n{tb}\n{code_block}"
            await owner.send(embed=embed)
            await log_to_channel("errors", embed)
    except Exception as e: print(f"FAILED to notify owner: {e}")

@bot.event
async def on_command(ctx):
    location = f"Server: {ctx.guild.name} | Channel: #{ctx.channel.name}" if ctx.guild else "DM"
    msg_info = f"[MsgID: {ctx.message.id} | Time: {ctx.message.created_at.strftime('%H:%M:%S')}]"
    print(f"{Log.CYAN}>>> [PREFIX COMMAND] {msg_info} {ctx.author} ran '{ctx.message.content}' in {location}{Log.RESET}")
    
    from .database import db_pool
    # Display names barely change — sync at most every 10 min, not per command.
    if db_pool and _should_touch(ctx.author.id, "names"):
        try:
            async with db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO user_settings (user_id, discord_username, display_name)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id) DO UPDATE SET
                        discord_username = EXCLUDED.discord_username,
                        display_name = EXCLUDED.display_name
                """, str(ctx.author.id), ctx.author.name, ctx.author.display_name)
        except Exception:
            pass

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound): return
    
    if isinstance(error, commands.NotOwner):
        return await ctx.send("❌ You do not have permission to use this command.")
    if isinstance(error, commands.CheckFailure):
        return await ctx.send("❌ You do not have permission to use this command.")
    
    # Handle common user-facing errors
    usage = f"`{ctx.prefix}{ctx.command.name} {ctx.command.signature}`" if ctx.command else ""
    
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send(f"⚠️ You're missing a required piece of information: `{error.param.name}`.\n**The right way to use this is:** {usage}")
    elif isinstance(error, commands.CommandOnCooldown):
        return await ctx.send(f"⏳ Whoa there, slow down! You can use this command again in **{error.retry_after:.1f} seconds**.")
    elif isinstance(error, commands.BadArgument):
        return await ctx.send(f"⚠️ I couldn't understand one of your inputs. Please make sure you're typing it correctly!\n**The right way to use this is:** {usage}")
    elif isinstance(error, commands.MissingPermissions):
        return await ctx.send("🚫 You don't have the required permissions to use this command.")
    elif isinstance(error, commands.BotMissingPermissions):
        return await ctx.send("🚫 I don't have the required permissions to perform this action here.")
    elif isinstance(error, commands.MemberNotFound):
        return await ctx.send(f"⚠️ I couldn't find that user. Make sure you typed their name correctly.\n**The right way to use this is:** {usage}")
    elif isinstance(error, commands.CommandInvokeError) and isinstance(error.original, discord.HTTPException) and error.original.code == 200000:
        return await ctx.send("❌ The response was blocked by AutoMod. This usually happens if your username or requested data contains a blocked word.")
        
    await notify_owner(f"{ctx.prefix}{ctx.invoked_with}", error, source=ctx)
    try: await ctx.send("Whoops! Something went wrong behind the scenes. The developer has been notified. If you need help, join our support server: https://discord.gg/53sxaVWn92")
    except: pass

@bot.tree.error
async def on_app_command_error_tree(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    msg = None
    if isinstance(error, discord.app_commands.CommandOnCooldown):
        msg = f"⏳ Whoa there, slow down! You can use this command again in **{error.retry_after:.1f} seconds**."
    elif isinstance(error, discord.app_commands.CheckFailure):
        msg = "❌ You do not have permission to use this command."
    elif isinstance(error, discord.app_commands.MissingPermissions):
        msg = "🚫 You don't have the required permissions to use this command."
    elif isinstance(error, discord.app_commands.BotMissingPermissions):
        msg = "🚫 I don't have the required permissions to perform this action here."
    elif isinstance(error, discord.app_commands.CommandInvokeError) and isinstance(error.original, discord.HTTPException) and error.original.code == 200000:
        msg = "❌ The response was blocked by AutoMod. This usually happens if your username or requested data contains a blocked word."
        
    if msg:
        if not interaction.response.is_done():
            try: await interaction.response.send_message(msg, ephemeral=True)
            except: pass
        else:
            try: await interaction.followup.send(msg, ephemeral=True)
            except: pass
        return

    cmd_name = interaction.command.name if interaction.command else "unknown"
    await notify_owner(f"/{cmd_name}", error, source=interaction)
    
    fallback_msg = "Whoops! Something went wrong behind the scenes. The developer has been notified. If you need help, join our support server: https://discord.gg/53sxaVWn92"
    if not interaction.response.is_done(): 
        try: await interaction.response.send_message(fallback_msg, ephemeral=True)
        except: pass
    else:
        try: await interaction.followup.send(fallback_msg, ephemeral=True)
        except: pass


async def check_if_banned(interaction: discord.Interaction) -> bool:
    from .database import db_pool
    if not db_pool: return True
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT is_banned, ban_reason FROM user_settings WHERE user_id=$1",
                str(interaction.user.id)
            )
            if row and row.get('is_banned'):
                reason = row.get('ban_reason') or "No reason provided."
                try:
                    await interaction.response.send_message(
                        f"❌ **You are banned from using DJ Scratch.**\n\n**Reason:** {reason}\n*If you believe this is a mistake, please contact GamerNation12.*",
                        ephemeral=True
                    )
                except:
                    pass
                return False
    except Exception as e:
        print(f"{Log.RED}>>> Error checking ban status: {e}{Log.RESET}")
    return True

@bot.check
async def global_ban_check_prefix(ctx) -> bool:
    from .database import db_pool
    if not db_pool: return True
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT is_banned, ban_reason FROM user_settings WHERE user_id=$1",
                str(ctx.author.id)
            )
            if row and row.get('is_banned'):
                reason = row.get('ban_reason') or "No reason provided."
                try:
                    await ctx.send(f"❌ **You are banned from using DJ Scratch.**\n\n**Reason:** {reason}\n*If you believe this is a mistake, please contact GamerNation12.*")
                except:
                    pass
                return False
    except Exception as e:
        print(f"{Log.RED}>>> Error checking ban status: {e}{Log.RESET}")
    return True



async def global_disabled_command_check_slash(interaction: discord.Interaction) -> bool:
    if interaction.type != discord.InteractionType.application_command:
        return True
    if not interaction.command: return True
    
    from src.core.database import is_command_disabled, has_command_permission
    reason = await is_command_disabled(interaction.command.name)
    if reason:
        if await has_command_permission(str(interaction.user.id), interaction.command.name):
            return True
        embed = Theme.get_embed(
            title="🔒 Command Locked",
            description=f"This command has been disabled by the owner.\n\n**Reason:** {reason}",
            color=discord.Color.red()
        )
        try:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except:
            pass
        return False
    return True


async def check_if_logged_in(interaction: discord.Interaction) -> bool:
    if interaction.type != discord.InteractionType.application_command:
        return True
    
    if interaction.user.id == OWNER_ID:
        return True
    
    # Allow specific commands without login
    allowed_commands = ["login", "logout", "help", "suggest", "bug", "cd", "privacy", "ping", "status", "updates", "guide", "start", "tutorial", "howto"]
    if interaction.command and interaction.command.name in allowed_commands:
        return True
        
    username = await get_lastfm_username(interaction.user.id)
    if not username:
        embed = Theme.get_embed(
            title="⚠️ Account Not Linked",
            description="You need to log into the updated website to use this command!\n\n🔗 **[Login Here](https://dj-scratch.vercel.app/)** or use `/login` to link your Last.fm account.\n*Need help? Run `/guide` to learn how to start!*",
            color=discord.Color.red()
        )
        try:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except:
            pass
        return False
    return True


@bot.check
async def global_disabled_command_check_prefix(ctx) -> bool:
    if not ctx.command: return True
    from src.core.database import is_command_disabled, has_command_permission
    reason = await is_command_disabled(ctx.command.name)
    if reason:
        if await has_command_permission(str(ctx.author.id), ctx.command.name):
            return True
        embed = Theme.get_embed(
            title="🔒 Command Locked",
            description=f"This command has been disabled by the owner.\n\n**Reason:** {reason}",
            color=discord.Color.red()
        )
        try:
            await ctx.send(embed=embed)
        except:
            pass
        return False
    return True

@bot.check
async def global_login_check_prefix(ctx) -> bool:
    if ctx.author.id == OWNER_ID:
        return True
        
    allowed_commands = ["login", "logout", "help", "suggest", "bug", "cd", "cd2", "privacy", "ping", "status", "updates", "sync", "guide", "start", "tutorial", "howto"]
    if ctx.command and ctx.command.name in allowed_commands:
        return True
        
    username = await get_lastfm_username(ctx.author.id)
    if not username:
        embed = Theme.get_embed(
            title="⚠️ Account Not Linked",
            description="You need to log into the updated website to use this command!\n\n🔗 **[Login Here](https://dj-scratch.vercel.app/)** or use `,login` to link your Last.fm account.\n*Need help? Run `,guide` to learn how to start!*",
            color=discord.Color.red()
        )
        try:
            await ctx.send(embed=embed)
        except:
            pass
        return False
    return True

@bot.event
async def on_app_command_completion(interaction: discord.Interaction, command: discord.app_commands.Command | discord.app_commands.ContextMenu):
    location = f"Server: {interaction.guild.name} | Channel: #{interaction.channel.name}" if interaction.guild else "DM"
    int_info = f"[IntID: {interaction.id} | Time: {interaction.created_at.strftime('%H:%M:%S')}]"
    print(f"{Log.CYAN}>>> [SLASH COMMAND] {int_info} {interaction.user} ran '/{command.name}' in {location}{Log.RESET}")
    
    import time
    if not hasattr(bot, 'active_users_dict'):
        bot.active_users_dict = {}
    bot.active_users_dict[interaction.user.id] = time.time()
    
    global db_pool
    if not db_pool: return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO command_usage (command_name, usage_count)
                VALUES ($1, 1)
                ON CONFLICT (command_name) DO UPDATE SET usage_count = command_usage.usage_count + 1
                """,
                command.name
            )
    except Exception as e:
        print(f"{Log.RED}>>> Failed to track command usage: {e}{Log.RESET}")

# --- HELPER: AVATAR COOLDOWN ---
async def get_avatar_cooldown():
    global db_pool
    if not db_pool: return 0
    now = datetime.utcnow()
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM global_settings WHERE key = 'avatar_cooldown'")
        if row and row['value']:
            try:
                last_update = datetime.fromisoformat(row['value'])
                diff = (now - last_update).total_seconds()
                if diff < 300: return int(300 - diff)
            except: pass
    return 0


async def add_custom_reactions(message):
    try:
        await message.add_reaction("<a:mc_Fire:1423825520516141138>")
        await message.add_reaction("<a:Jamming:1441565477313970259>")
    except: pass

# --- HELPER: DATABASE MANAGEMENT ---
# Full-table scans used by stats/whoknows paths — cache 90s, grows with user base.
_LOAD_TABLE_CACHE: dict = {}  # name -> (data, expires_monotonic)
_LOAD_TABLE_TTL = 90.0


def _load_table_get(name):
    import time as _t
    e = _LOAD_TABLE_CACHE.get(name)
    if e and e[1] > _t.monotonic():
        return e[0]
    return None


def _load_table_set(name, data):
    import time as _t
    _LOAD_TABLE_CACHE[name] = (data, _t.monotonic() + _LOAD_TABLE_TTL)


def invalidate_load_tables():
    _LOAD_TABLE_CACHE.clear()


async def load_users():
    cached = _load_table_get('users')
    if cached is not None:
        return cached
    global db_pool
    if not db_pool: return {}
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id, lastfm_username FROM user_settings WHERE lastfm_username IS NOT NULL")
        data = {r['user_id']: r['lastfm_username'] for r in rows}
        _load_table_set('users', data)
        return data

async def load_display_names():
    cached = _load_table_get('display_names')
    if cached is not None:
        return cached
    global db_pool
    if not db_pool: return {}
    async with db_pool.acquire() as conn:
        try:
            rows = await conn.fetch("SELECT user_id, display_name FROM user_settings WHERE display_name IS NOT NULL")
            data = {r['user_id']: r['display_name'] for r in rows}
            _load_table_set('display_names', data)
            return data
        except Exception:
            return {}

async def save_user(uid, username):
    global db_pool
    if not db_pool:
        print(f"No database connection available to save user!")
        return
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_settings (user_id, lastfm_username) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET lastfm_username = EXCLUDED.lastfm_username",
            str(uid), username
        )
    try:
        from src.core.database import invalidate_user_cache
        invalidate_user_cache(uid)
    except Exception:
        pass
    try:
        invalidate_load_tables()
    except Exception:
        pass
    print(f"{Log.GREEN}>>> Saved Last.fm user to Postgres: {username} ({uid}){Log.RESET}")

async def get_lastfm_username(uid):
    if bot and bot.user and str(uid) == str(bot.user.id):
        return "DJ-Scratch"

    # Fast path: bundle cache (populated by get_user_bundle, 2-min TTL).
    try:
        from src.core.database import _bundle_get, _LASTFM_USER_CACHE, _LASTFM_USER_TTL, _time as _dbtime
        b = _bundle_get(str(uid))
        if b is not None:
            return b.get('lastfm_username') or None
        e = _LASTFM_USER_CACHE.get(str(uid))
        if e and e[1] > _dbtime.monotonic():
            return e[0]
    except Exception:
        pass

    global db_pool
    if not db_pool: return None
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT lastfm_username FROM user_settings WHERE user_id = $1", str(uid))
        uname = row['lastfm_username'] if row and row['lastfm_username'] else None
        try:
            from src.core.database import _LASTFM_USER_CACHE as _c, _LASTFM_USER_TTL as _ttl, _time as _t
            _c[str(uid)] = (uname, _t.monotonic() + _ttl)
        except Exception:
            pass
        return uname

# --- LAST.FM API FETCHERS ---


class FMDetailsView(discord.ui.View):
    def __init__(self, bot_instance, artist, img, is_p, cd, user, spotify_url, song, original_msg=None):
        super().__init__(timeout=None)
        self.bot_instance = bot_instance
        self.artist = artist
        self.img = img
        self.user = user
        self.song = song
        self.original_msg = original_msg
        
        if spotify_url:
            self.add_item(discord.ui.Button(label="", url=spotify_url, emoji="<a:movingnotes:1476084305229910159>", style=discord.ButtonStyle.link))
            
        if song and artist:
            custom_lyric = f"fm_lyrics:{artist[:40]}:{song[:40]}"
            btn_lyrics = discord.ui.Button(label="", emoji="<:lyrics:1543145005357604895>", style=discord.ButtonStyle.secondary, custom_id=custom_lyric)
            self.add_item(btn_lyrics)
            
        if is_p and img and cd <= 0:
            custom_prev = f"fm_preview:{artist[:80]}"
            btn2 = discord.ui.Button(label="", emoji="🖼️", style=discord.ButtonStyle.primary, custom_id=custom_prev)
            self.add_item(btn2)

    async def show_lyrics(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        from src.core.lyrics import fetch_lyrics
        session = getattr(self.bot_instance, 'session', None)
        lyrics_data = await fetch_lyrics(session, self.artist, self.song)
        if lyrics_data and lyrics_data.get("plain"):
            desc = lyrics_data.get("plain")
            if len(desc) > 4096:
                desc = desc[:4093] + "..."
            embed = Theme.get_embed(title=f"Lyrics for {self.song} by {self.artist}", description=desc, color=Theme.PRIMARY)
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send("Could not find lyrics for this track.", ephemeral=True)

    async def preview_avatar(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        preview_embed = Theme.get_embed(
            title="Bot Avatar Preview", 
            description=f"This is how the bot will look if you apply the album art for **{self.artist}**.", 
            color=LASTFM_COLOR
        )
        preview_embed.set_author(name=format_name(self.user), icon_url=self.img)
        
        from src.utils.images import get_circular_pfp_file
        pfp_file = await get_circular_pfp_file(self.img)
        
        apply_view = ApplyAvatarView(self.bot_instance, self.artist, self.img, original_msg=self.original_msg, original_user=self.user, track=self.song)
        
        if pfp_file:
            preview_embed.set_image(url="attachment://pfp_preview.png")
            await interaction.followup.send(file=pfp_file, embed=preview_embed, view=apply_view, ephemeral=True)
        else:
            preview_embed.set_image(url=self.img)
            await interaction.followup.send(embed=preview_embed, view=apply_view, ephemeral=True)

class FMActionsView(discord.ui.View):
    def __init__(self, bot_instance, artist, img, is_p=False, cd=0, user=None, spotify_url=None, song=None, current_mode="full", track_data=None):
        super().__init__(timeout=None)
        self.bot_instance = bot_instance
        self.artist = artist
        self.img = img
        self.user = user
        self.song = song
        self.spotify_url = spotify_url
        self.is_p = is_p
        self.cd = cd
        self.current_mode = current_mode
        self.track_data = track_data
        
        user_id = str(user.id) if user else "None"
        
        unique_id = uuid.uuid4().hex[:8]
        if unique_id and track_data is not None:
            if isinstance(track_data, dict) and 'raw_data' in track_data:
                FM_TRACK_CACHE[unique_id] = track_data
            else:
                FM_TRACK_CACHE[unique_id] = track_data
            if len(FM_TRACK_CACHE) > 1000:
                for k in list(FM_TRACK_CACHE.keys())[:100]:
                    FM_TRACK_CACHE.pop(k, None)
                    
        if current_mode == "compact":
            btn_down = discord.ui.Button(label="", emoji="<:Down:1528249702338789407>", style=discord.ButtonStyle.secondary, custom_id=f"fm_down:{user_id}:{current_mode}:{unique_id}")
            self.add_item(btn_down)
        elif current_mode == "full":
            btn_up = discord.ui.Button(label="", emoji="<:Up:1528249701164646410>", style=discord.ButtonStyle.secondary, custom_id=f"fm_up:{user_id}:{current_mode}:{unique_id}")
            self.add_item(btn_up)
            
            btn_down = discord.ui.Button(label="", emoji="<:Down:1528249702338789407>", style=discord.ButtonStyle.secondary, custom_id=f"fm_down:{user_id}:{current_mode}:{unique_id}")
            self.add_item(btn_down)
        elif current_mode == "stats":
            btn_up = discord.ui.Button(label="", emoji="<:Up:1528249701164646410>", style=discord.ButtonStyle.secondary, custom_id=f"fm_up:{user_id}:{current_mode}:{unique_id}")
            self.add_item(btn_up)
            
        if spotify_url and current_mode != "compact":
            self.add_item(discord.ui.Button(label="", url=spotify_url, emoji="<a:movingnotes:1476084305229910159>", style=discord.ButtonStyle.link))
            
        if song and artist and current_mode != "compact":
            custom_lyric = f"fm_lyrics:{artist[:40]}:{song[:40]}"
            btn_lyrics = discord.ui.Button(label="", emoji="<:lyrics:1543145005357604895>", style=discord.ButtonStyle.secondary, custom_id=custom_lyric)
            self.add_item(btn_lyrics)
            
        if is_p and img and cd <= 0 and current_mode != "compact":
            user_id_str = str(self.user.id) if self.user else "None"
            custom_prev = f"fm_preview:{user_id_str}:{unique_id}:{artist[:80]}"
            btn2 = discord.ui.Button(label="", emoji="🖼️", style=discord.ButtonStyle.primary, custom_id=custom_prev)
            self.add_item(btn2)

    async def go_down(self, interaction: discord.Interaction):
        await interaction.response.defer()
        new_mode = "full" if self.current_mode == "compact" else "stats"
        result, _ = await process_fm(interaction, self.user, mode=new_mode, track_data=self.track_data)
        if result:
            content = result.get('content')
            if not interaction.response.is_done():
                await interaction.response.edit_message(content=content, embed=result.get('embed'), view=result.get('view'))
            else:
                await interaction.edit_original_response(content=content, embed=result.get('embed'), view=result.get('view'))

    async def go_up(self, interaction: discord.Interaction):
        await interaction.response.defer()
        new_mode = "full" if self.current_mode == "stats" else "compact"
        result, _ = await process_fm(interaction, self.user, mode=new_mode, track_data=self.track_data)
        if result:
            content = result.get('content')
            if not interaction.response.is_done():
                await interaction.response.edit_message(content=content, embed=result.get('embed'), view=result.get('view'))
            else:
                await interaction.edit_original_response(content=content, embed=result.get('embed'), view=result.get('view'))

    async def show_lyrics(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        from src.core.lyrics import fetch_lyrics
        session = getattr(self.bot_instance, 'session', None)
        lyrics = await fetch_lyrics(session, self.artist, self.song)
        if lyrics:
            if len(lyrics) > 4096:
                lyrics = lyrics[:4093] + "..."
            embed = Theme.get_embed(title=f"Lyrics for {self.song} by {self.artist}", description=lyrics, color=LASTFM_COLOR)
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send("Could not find lyrics for this track.", ephemeral=True)

    async def preview_avatar(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        preview_embed = Theme.get_embed(
            title="Bot Avatar Preview", 
            description=f"This is how the bot will look if you apply the album art for **{self.artist}**.", 
            color=LASTFM_COLOR
        )
        preview_embed.set_author(name=format_name(self.user), icon_url=self.img)
        
        from src.utils.images import get_circular_pfp_file
        pfp_file = await get_circular_pfp_file(self.img)
        
        apply_view = ApplyAvatarView(self.bot_instance, self.artist, self.img, original_msg=interaction.message, original_user=self.user, track=self.song)
        
        if pfp_file:
            preview_embed.set_image(url="attachment://pfp_preview.png")
            await interaction.followup.send(file=pfp_file, embed=preview_embed, view=apply_view, ephemeral=True)
        else:
            preview_embed.set_image(url=self.img)
            await interaction.followup.send(embed=preview_embed, view=apply_view, ephemeral=True)

async def update_bot_avatar_and_status(bot_instance, artist, img, track=None, album=None):
    try:
        cd = await get_avatar_cooldown()
        if cd > 0:
            return False, cd

        async with bot_instance.session.get(img) as resp:
            if resp.status == 200:
                image_bytes = await resp.read()
                await bot_instance.user.edit(avatar=image_bytes)
                
                activity = discord.Activity(type=discord.ActivityType.listening, name=artist)
                await bot_instance.change_presence(activity=activity)
                
                from src.core.database import db_pool
                if db_pool:
                    now = datetime.utcnow()
                    async with db_pool.acquire() as conn:
                        await conn.execute("INSERT INTO global_settings (key, value) VALUES ('avatar_cooldown', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", now.isoformat())
                        await conn.execute("INSERT INTO global_settings (key, value) VALUES ('bot_status', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", artist)
                        if track:
                            await conn.execute("INSERT INTO global_settings (key, value) VALUES ('bot_track', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", track)
                        if album:
                            await conn.execute("INSERT INTO global_settings (key, value) VALUES ('bot_album', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", album)
                return True, 300
    except Exception as e:
        print(f"{Log.RED}>>> Error updating bot avatar: {e}{Log.RESET}")
    return False, 0

class ApplyAvatarView(discord.ui.View):
    def __init__(self, bot_instance, artist, img, original_msg=None, original_user=None, track=None, album=None, track_data=None):
        super().__init__(timeout=180)
        self.bot_instance = bot_instance
        self.artist = artist
        self.img = img
        self.original_msg = original_msg
        self.original_user = original_user
        self.track = track
        self.album = album
        self.track_data = track_data
        
    @discord.ui.button(label="Set as Bot Avatar", emoji="✅", style=discord.ButtonStyle.success)
    async def apply_avatar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        changed, cd = await update_bot_avatar_and_status(self.bot_instance, self.artist, self.img, self.track, self.album)
        if changed:
            scr_res = False
            if self.track:
                from src.utils.api import scrobble_bot_track
                scr_res = await scrobble_bot_track(self.bot_instance.session, self.artist, self.track, self.album)
            
            debug_info = f"msg:{bool(self.original_msg)} usr:{bool(self.original_user)} scr:{scr_res}"
            if getattr(self.bot_instance, 'is_test_bot', False):
                embed = Theme.get_success_embed(
                    title="Avatar Updated", 
                    description=f"Successfully applied **{self.artist}** as the bot avatar!"
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send(f"✅ Avatar updated successfully!", ephemeral=True)
            self.stop()
            
            if self.original_msg and self.original_user:
                try:
                    await self.original_msg.delete()
                except Exception as e:
                    if interaction.guild:
                        await interaction.followup.send(f"⚠️ Could not delete old msg: {e}", ephemeral=True)
                
                try:
                    mode = await get_user_fm_mode(self.original_user.id)
                    result, is_p = await process_fm(interaction, self.original_user, mode=mode or "full", track_data=self.track_data)
                    
                    channel = self.original_msg.channel if self.original_msg else interaction.channel
                    if result and channel:
                        if isinstance(result, dict):
                            try:
                                new_msg = await interaction.followup.send(**result, ephemeral=False, wait=True)
                            except Exception:
                                new_msg = await channel.send(**result)
                            if is_p:
                                await add_custom_reactions(new_msg)
                        else:
                            await channel.send(result)
                    else:
                        if interaction.guild:
                            await interaction.followup.send(f"⚠️ Could not send new msg. Result: {bool(result)}, Channel: {bool(channel)}", ephemeral=True)
                except Exception as e:
                    if interaction.guild:
                        await interaction.followup.send(f"⚠️ Error resending fm: {e}", ephemeral=True)
        else:
            if cd > 0:
                m, s = divmod(cd, 60)
                await interaction.followup.send(f"⏳ Avatar is on cooldown. Please wait {m}m {s}s.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Failed to update avatar. It might already be set.", ephemeral=True)

async def get_settings_embed(user_id, user):
    mode = await get_user_fm_mode(user_id)
    feats = await get_user_show_features(user_id)
    d_source = await get_user_data_source(user_id)
    embed = Theme.get_embed(title=f"⚙️ Settings for {format_name(user)}", color=LASTFM_COLOR)
    embed.add_field(name="/fm Display Mode", value=f"`{mode}`", inline=True)
    embed.add_field(name="Featured Artists", value=f"`{'ON' if feats else 'OFF'}`", inline=True)
    
    source_label = "Imported Only" if d_source == 'imported_only' else ("Last.fm Only" if d_source == 'lastfm_only' else "Last.fm + Imported")
    embed.add_field(name="Data Source", value=f"`{source_label}`", inline=True)
    
    embed.set_footer(text="Use the dropdown below to change your settings.")
    return embed

class SettingsDropdown(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Compact Text Mode", description="1-line plain text for /fm", emoji="📝", value="fm_compact"),
            discord.SelectOption(label="Full Embed Mode", description="Detailed embed for /fm", emoji="🖼️", value="fm_full"),
            discord.SelectOption(label="Stats View Mode", description="stats.fm style embed for /fm", emoji="📊", value="fm_stats"),
            discord.SelectOption(label="Enable Featured Artists", description="Show featured artists in /fm", emoji="🎤", value="feat_on"),
            discord.SelectOption(label="Disable Featured Artists", description="Hide featured artists in /fm", emoji="🚫", value="feat_off"),
            discord.SelectOption(label="Data: Combined", description="Use Last.fm + Imported Data", emoji="🔄", value="ds_combined"),
            discord.SelectOption(label="Data: Imported Only", description="Use strictly your Imported Data", emoji="📦", value="ds_imported_only"),
        ]
        super().__init__(placeholder="Select a setting to change...", min_values=1, max_values=1, options=options, custom_id="settings_dropdown")

    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        if val.startswith("fm_"):
            mode = val.split("_")[1]
            await set_user_fm_mode(interaction.user.id, mode)
        elif val.startswith("feat_"):
            on = (val == "feat_on")
            await set_user_show_features(interaction.user.id, on)
        elif val.startswith("ds_"):
            source = val[3:]
            await set_user_data_source(interaction.user.id, source)
            
        embed = await get_settings_embed(interaction.user.id, interaction.user)
        await interaction.response.edit_message(embed=embed, view=self.view)

class SettingsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(SettingsDropdown())

_FEATURES_CACHE: dict = {}
_FEATURES_TTL = 21600.0  # 6h (short: a wrong guess must not stick around for a day)

_FEAT_RE = r"(?:[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+|(?:\s+-\s+|\s+)(?:feat\.?|ft\.?|featuring)\s+)([^\]\)]+?)(?:[\)\]]|$)"
# Version words that mean "different recording" — never borrow artists across these.
_VERSION_WORDS = ("remix", "live", "acoustic", "demo", "version", "edit", "mix", "cover", "instrumental")


def _norm_name(x):
    import re, unicodedata
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', x or '').encode('ASCII', 'ignore').decode('utf-8').lower())


def _strip_leading_the(n):
    return n[3:] if n.startswith('the') and len(n) > 5 else n


def _same_track(a, b):
    """Fuzzy title match with a version guard (remix vs original must NOT match)."""
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if len(na) < 3 or len(nb) < 3:
        return False
    if na not in nb and nb not in na:
        return False
    if {w for w in _VERSION_WORDS if w in na} != {w for w in _VERSION_WORDS if w in nb}:
        return False
    return True


def _same_artist_strict(a, b):
    """Main-artist identification: normalized equality (ignoring a leading 'the')."""
    na, nb = _strip_leading_the(_norm_name(a)), _strip_leading_the(_norm_name(b))
    return bool(na) and na == nb


def _same_artist_loose(a, b):
    if _same_artist_strict(a, b):
        return True
    na, nb = _norm_name(a), _norm_name(b)
    # Min length kills short-name false positives ("em" in "eminem", "al" in "alice").
    if len(na) < 4 or len(nb) < 4:
        return False
    return na in nb or nb in na


def _already_listed(existing_norms, n):
    if not n:
        return True
    for e in existing_norms:
        if n == e:
            return True
        if len(n) >= 4 and len(e) >= 4 and (n in e or e in n):
            return True
    return False


def _extract_title_features(artist, song):
    """Pull (feat. X) / - feat. X out of the title. Most reliable signal, no network."""
    import re
    m = re.search(_FEAT_RE, song, flags=re.IGNORECASE)
    if not m:
        return artist, song
    features = m.group(1).strip(" ,&-").strip()
    if not features or len(features) > 60:
        return artist, song  # absurdly long capture = regex misfire, ignore it
    song = (song[:m.start()] + song[m.end():]).strip()
    song = re.sub(r'\s{2,}', ' ', song).strip(" -")
    artist = f"{artist}, {features}"
    return artist, song


def _merge_spotify_artists(artist, song, original_artist, s_artists, s_track_name):
    """Merge Spotify's artist list. Returns (artist, song, resolved).

    resolved=False means the Spotify data couldn't be trusted (wrong track or
    main artist not identifiable) — caller should fall through, not guess.
    """
    if not s_artists:
        return artist, song, False
    # Don't trust artists from the wrong Spotify track (same-name covers/remixes).
    if s_track_name and not _same_track(song, s_track_name):
        return artist, song, False
    main_idx = next((i for i, a in enumerate(s_artists) if _same_artist_strict(original_artist, a)), None)
    if main_idx is None:
        main_idx = next((i for i, a in enumerate(s_artists) if _same_artist_loose(original_artist, a)), None)
    if main_idx is None:
        return artist, song, False
    existing = {_norm_name(p) for p in artist.split(",")}
    extras = [a.strip() for i, a in enumerate(s_artists)
              if i != main_idx and a and a.strip() and not _already_listed(existing, _norm_name(a))]
    if extras:
        artist = f"{artist}, {', '.join(extras)}"
    return artist, song, True


async def apply_features(session, artist, song, s_artists=None, s_track_name=None):
    """Feat. detection: title regex + verified Spotify artists + guarded iTunes fallback.

    Conservative by design: a missed feature just shows the plain artist name,
    but a wrong feature shows someone else's name — so when in doubt, don't add.
    """
    if not artist or not song:
        return artist, song

    import re
    original_artist = artist
    sp_sig = ""
    if s_track_name:
        sp_sig += "\x01" + s_track_name.lower()
    if s_artists:
        sp_sig += "\x02" + "|".join(a.lower() for a in s_artists[:4])
    cache_key = f"{original_artist.lower()}\x00{song.lower()}\x00{sp_sig}"
    now = _ctime.monotonic()
    entry = _FEATURES_CACHE.get(cache_key)
    if entry and entry[1] > now:
        return entry[0]

    def _store(result):
        _FEATURES_CACHE[cache_key] = (result, now + _FEATURES_TTL)
        if len(_FEATURES_CACHE) > 2000:
            _FEATURES_CACHE.pop(next(iter(_FEATURES_CACHE)))
        return result

    # 1. Title regex (instant, most reliable).
    artist, song = _extract_title_features(artist, song)

    # 2. Spotify artists already fetched in process_fm — instant, no extra call.
    if s_artists:
        artist, song, resolved = _merge_spotify_artists(artist, song, original_artist, s_artists, s_track_name)
        if resolved:
            return _store((artist, song))
        # Unresolved: fall through to iTunes instead of guessing.

    # 3. iTunes fallback, tightly guarded.
    async def _itunes_lookup():
        try:
            import aiohttp as _aio
            url = f"https://itunes.apple.com/search?term={urllib.parse.quote(original_artist + ' ' + song)}&entity=song&limit=3"
            if session is None or getattr(session, 'closed', True):
                return None
            async with session.get(url, timeout=_aio.ClientTimeout(total=1.5)) as r:
                if r.status == 200:
                    return await r.json(content_type=None)
        except Exception:
            pass
        return None

    try:
        data = await asyncio.wait_for(_itunes_lookup(), timeout=1.6)
        if data and data.get('resultCount', 0) > 0:
            for result in data['results'][:3]:
                it_artist = result.get('artistName', '') or ''
                it_track = result.get('trackName', '') or ''
                if not _same_track(song, it_track):
                    continue
                # Split collab string, but only trust it if the main artist is
                # positively identifiable among the parts (kills "Mumford & Sons"
                # -> phantom "Sons" feature, and "Al" matching "Alice Cooper").
                parts = [p.strip() for p in re.split(
                    r',|\s+&\s+|\s+feat\.?\s+|\s+ft\.?\s+|\s+featuring\s+|\s+with\s+',
                    it_artist, flags=re.IGNORECASE)]
                parts = [p for p in parts if p]
                if not parts or not any(_same_artist_strict(original_artist, p) for p in parts):
                    continue
                existing = {_norm_name(p) for p in artist.split(",")}
                api_features = []
                m2 = re.search(_FEAT_RE, it_track, flags=re.IGNORECASE)
                if m2:
                    for f in re.split(r',|&', m2.group(1).strip()):
                        f = f.strip()
                        if f and not _already_listed(existing, _norm_name(f)):
                            api_features.append(f)
                            existing.add(_norm_name(f))
                for p in parts:
                    if _same_artist_strict(original_artist, p):
                        continue  # the main artist, not a feature
                    if not _already_listed(existing, _norm_name(p)):
                        api_features.append(p)
                        existing.add(_norm_name(p))
                if api_features:
                    artist = f"{artist}, {', '.join(api_features)}"
                    break
    except Exception:
        pass

    return _store((artist, song))

# --- CORE LOGIC ---
import discord
from datetime import datetime, timedelta

from src.core.database import format_name




async def process_fm(ctx_int, user, mode="full", track_data=None):
    bot_instance = getattr(ctx_int, 'client', getattr(ctx_int, 'bot', bot))
    session = getattr(bot_instance, 'session', None)

    username = await get_lastfm_username(user.id)
    if not username: return {"embed": Theme.get_error_embed(description=f"**{user.name}** hasn't linked a Last.fm account! Link it with `/login`")}, False
    
    is_cached = False
    if track_data is not None and isinstance(track_data, dict) and 'raw_data' in track_data:
        data = track_data['raw_data']
        is_cached = True
    elif track_data is not None:
        data = track_data
    else:
        data = await fetch_now_playing(username, 2)

    if isinstance(data, dict) and 'error' in data:
        err_msg = data.get('message', 'Unknown error')
        return {"embed": Theme.get_error_embed(description=f"Last.fm API Error: {err_msg}\\n\\n*Note: This is an issue with Last.fm's servers, not DJ Scratch. Please try again later.*")}, False
        
    if not data or 'recenttracks' not in data or not data['recenttracks']['track']: 
        return {"embed": Theme.get_error_embed(description="Could not find recent tracks.")}, False
    
    try:
        tracks = data['recenttracks']['track']
        t = tracks[0]
        artist, song, album, img = t['artist']['#text'], t['name'], t['album']['#text'], t['image'][3]['#text']
        
        raw_artist, raw_song = artist, song

        # ONE cached DB query instead of 2-3 round-trips per /fm.
        from src.core.database import get_user_bundle
        try:
            _bundle = await get_user_bundle(user.id)
            show_features = _bundle.get('show_features', False)
            show_playcount = _bundle.get('show_track_playcount', True)
            if show_playcount is None:
                show_playcount = True
        except Exception:
            show_features, show_playcount = False, True
        
        spotify_url = None
        track_plays = -1
        
        if is_cached:
            p = track_data['processed']
            artist = p['artist']
            song = p['song']
            img = p['img']
            spotify_url = p['spotify_url']
            track_plays = p['track_plays']
            t_info = p.get('t_info')
            
            # If we switch to stats mode but track_plays wasn't fetched previously, we need to fetch it
            if mode == "stats" and track_plays == -1:
                t_info = await fetch_track_info(username, raw_artist, raw_song)
                if t_info and 'track' in t_info and 'userplaycount' in t_info['track']:
                    track_plays = int(t_info['track']['userplaycount'])
            else:
                # If we switch to stats mode and track_plays was fetched but t_info wasn't cached somehow
                if mode == "stats" and t_info is None:
                    t_info = await fetch_track_info(username, raw_artist, raw_song)
        else:
            # Run independent DB and API tasks concurrently
            async def get_spotify_data():
                from src.core.spotify import get_spotify_track_info, get_user_spotify_access_token
                u_token = await get_user_spotify_access_token(session, str(user.id))
                s_inf = await get_spotify_track_info(session, artist, song, user_token=u_token)
                if not s_inf and u_token:
                    s_inf = await get_spotify_track_info(session, artist, song)
                return s_inf
    
            async def get_track_data(show_pc, m):
                if show_pc or m == "stats":
                    return await fetch_track_info(username, raw_artist, raw_song)
                return None
    

            # Gather API data
            spotify_task = asyncio.create_task(get_spotify_data())
            track_info_task = asyncio.create_task(get_track_data(show_playcount, mode))
    
            s_info = await spotify_task
            t_info = await track_info_task
    
            s_artists = None
            s_track_name = None

            if s_info:
                spotify_url = s_info.get("spotify_url")
                s_img = s_info.get("image_url")
                if s_img and (not img or "2a96cbd8b46e442fc41c2b86b821562f" in img):
                    img = s_img
                s_artists = s_info.get("artists")
                s_track_name = s_info.get("name")
    
            async def do_deezer():
                if not img or "2a96cbd8b46e442fc41c2b86b821562f" in img:
                    try:
                        from src.utils.api import fetch_deezer_track_image
                        deezer_img = await fetch_deezer_track_image(session, song, artist)
                        if deezer_img: return deezer_img
                    except Exception as e:
                        print(f"Deezer fallback error: {e}")
                return img
                
            async def do_features():
                if show_features:
                    return await apply_features(session, artist, song, s_artists, s_track_name)
                return artist, song
                
            img, (artist, song) = await asyncio.gather(do_deezer(), do_features())
                
            if t_info and 'track' in t_info and 'userplaycount' in t_info['track']:
                track_plays = int(t_info['track']['userplaycount'])
                
        track_url = t.get('url', f"https://www.last.fm/music/{urllib.parse.quote(raw_artist)}/_/{urllib.parse.quote(raw_song)}")
        is_p = t.get('@attr', {}).get('nowplaying') == 'true'
        status = "Now Playing" if is_p else "Last Played"
        
        user_color = await get_color(user.id)
        color = user_color if is_p else discord.Color.dark_gray()

        if is_p:
            cd = await get_avatar_cooldown()
        else:
            cd = 0


        if mode == "compact":
            if is_p:
                content = f"<a:movingnotes:1476084305229910159> **{format_name(user)}** is listening to **[{song}](<{track_url}>)** by **{artist}**"
            else:
                content = f"🎧 **{format_name(user)}** was listening to **[{song}](<{track_url}>)** by **{artist}**"
                content += "\n*(⚠️ Scrobbles frozen? Run `,outofsync`)*"
            
            desc_lines = [f"**[{song}]({track_url})**", f"by **{artist}**", f"*{album}*"]
            if show_playcount and track_plays != -1:
                if track_plays == 0 and is_p:
                    desc_lines.append("\n🎧 **First time listening!**")
                else:
                    desc_lines.append(f"\n🔢 **{track_plays}** plays")
            
            desc = chr(10).join(desc_lines)
            embed = Theme.get_embed(description=desc, color=color)
            embed.set_author(name=f"{format_name(user)}'s {status}", icon_url=user.display_avatar.url)
            if img: embed.set_thumbnail(url=img)
            
            footer_text = f"Scrobbling as {'DJ Scratch' if username.lower() == 'dj-scratch' else username} | Scrobbles frozen? Run ,outofsync"
            if cd > 0:
                m, s = divmod(int(cd), 60)
                footer_text += f" • Avatar CD: {m}m {s}s"
                
            embed.set_footer(text=footer_text)
            
            view = FMActionsView(bot_instance, raw_artist, img, is_p=is_p, cd=cd, user=user, spotify_url=spotify_url, song=raw_song, current_mode="compact", track_data={'raw_data': data, 'processed': {'artist': artist, 'song': song, 'img': img, 'spotify_url': spotify_url, 'track_plays': track_plays, 't_info': t_info if 't_info' in locals() else None}})
            return {"content": content, "view": view}, is_p

        if mode == "stats":
            desc_lines = [f"**[{song}]({track_url})**", f"**{artist}** • *{album}*"]
            
            if len(tracks) > 1:
                prev_t = tracks[1]
                p_artist, p_song, p_album = prev_t['artist']['#text'], prev_t['name'], prev_t['album']['#text']
                
                if show_features:
                    p_artist, p_song = await apply_features(session, p_artist, p_song)
                
                p_url = prev_t.get('url', f"https://www.last.fm/music/{urllib.parse.quote(p_artist)}/_/{urllib.parse.quote(p_song)}")
                desc_lines.extend(["", "Previous:", f"**[{p_song}]({p_url})**", f"**{p_artist}** • *{p_album}*"])
            
            if show_playcount and track_plays != -1:
                if track_plays == 0 and is_p:
                    desc_lines.append("\n🎧 **First time listening!**")
                else:
                    desc_lines.append(f"\n🔢 **{track_plays}** plays")
            
            embed = Theme.get_embed(description=chr(10).join(desc_lines), color=color)
            embed.set_author(name=f"Now playing for {format_name(user)}" if is_p else f"Last played by {format_name(user)}")
            if img: embed.set_thumbnail(url=img)
            
            a_info_task = asyncio.create_task(fetch_artist_info(username, raw_artist))
            
            guild = getattr(ctx_int, 'guild', None)
            crown_task = None
            if guild:
                users_db = await load_users()
                display_names = await load_display_names()
                member_ids = {str(m.id) for m in guild.members}
                linked = {uid: lname for uid, lname in users_db.items() if uid in member_ids}
                # Cap: 1 Last.fm call per member was killing /fm stats in big servers.
                # Check at most 25 members with a concurrency limit + overall timeout.
                if linked:
                    async def fetch_crown():
                        import asyncio as _aio
                        items = list(linked.items())[:25]
                        sem = _aio.Semaphore(8)

                        async def _one(lname):
                            async with sem:
                                try:
                                    return await _aio.wait_for(fetch_artist_playcount(session, lname, raw_artist), timeout=4)
                                except Exception:
                                    return 0

                        try:
                            results = await _aio.wait_for(_aio.gather(*[_one(ln) for _, ln in items], return_exceptions=True), timeout=6)
                        except Exception:
                            return None

                        def get_name(uid, lname):
                            custom_name = display_names.get(uid)
                            if custom_name: return custom_name
                            try:
                                member = guild.get_member(int(uid))
                            except Exception:
                                member = None
                            return member.display_name if member else lname

                        lb = []
                        for (uid, lname), pc in zip(items, results or []):
                            if isinstance(pc, Exception) or not pc:
                                continue
                            lb.append({"name": get_name(uid, lname), "plays": pc})
                        if not lb: return None
                        lb.sort(key=lambda x: x['plays'], reverse=True)
                        return lb[0]
                    crown_task = asyncio.create_task(fetch_crown())

            try:
                a_info = await asyncio.wait_for(a_info_task, timeout=5)
            except Exception:
                try:
                    a_info_task.cancel()
                except Exception:
                    pass
                a_info = None
            crown_winner = None
            if crown_task:
                try:
                    crown_winner = await asyncio.wait_for(crown_task, timeout=7)
                except Exception:
                    try:
                        crown_task.cancel()
                    except Exception:
                        pass
                    crown_winner = None

            footer_parts = []
            if a_info and 'artist' in a_info and 'tags' in a_info['artist'] and 'tag' in a_info['artist']['tags']:
                tags = [tag['name'].lower() for tag in a_info['artist']['tags']['tag'][:4]]
                if tags: footer_parts.append(" - ".join(tags))
                
            stats_line = []
            if t_info and 'track' in t_info and t_info['track'].get('userloved') == '1':
                stats_line.append("❤️ Loved track")
            
            if a_info and 'artist' in a_info and 'stats' in a_info['artist']:
                pc = a_info['artist']['stats'].get('userplaycount', 0)
                stats_line.append(f"{pc} artist scrobbles")
                
            if crown_winner:
                stats_line.append(f"👑 {crown_winner['name']} ({crown_winner['plays']} plays)")
            
            if stats_line:
                footer_parts.append(" • ".join(stats_line))
                
            disp_u = 'DJ Scratch' if username.lower() == 'dj-scratch' else username
            if not is_p:
                footer_parts.append("Scrobbles frozen? Run ,outofsync")
                embed.set_footer(text=chr(10).join(footer_parts) if footer_parts else f"Scrobbling as {disp_u} | Scrobbles frozen? Run ,outofsync")
            else:
                embed.set_footer(text=chr(10).join(footer_parts) if footer_parts else f"Scrobbling as {disp_u}")
            
            view = FMActionsView(bot_instance, raw_artist, img, is_p=is_p, cd=cd, user=user, spotify_url=spotify_url, song=raw_song, current_mode="stats", track_data={'raw_data': data, 'processed': {'artist': artist, 'song': song, 'img': img, 'spotify_url': spotify_url, 'track_plays': track_plays, 't_info': t_info if 't_info' in locals() else None}})
            result = {"embed": embed, "view": view}
            return result, is_p

        desc_lines = [f"**[{song}]({track_url})**", f"by **{artist}**", f"*{album}*"]
        if show_playcount and track_plays != -1:
            if track_plays == 0 and is_p:
                desc_lines.append("\n🎧 **First time listening!**")
            else:
                desc_lines.append(f"\n🔢 **{track_plays}** plays")
                
        desc = chr(10).join(desc_lines)
        embed = Theme.get_embed(description=desc, color=color)
        embed.set_author(name=f"{format_name(user)}'s {status}", icon_url=user.display_avatar.url)
        if img: embed.set_thumbnail(url=img)
        
        if not is_p:
            footer_text = f"Scrobbling as {'DJ Scratch' if username.lower() == 'dj-scratch' else username} | Scrobbles frozen? Run ,outofsync"
        else:
            footer_text = f"Scrobbling as {'DJ Scratch' if username.lower() == 'dj-scratch' else username}"
        if cd > 0:
            mins, secs = divmod(cd, 60)
            footer_text += f" • Avatar CD: {mins}m {secs}s"
        embed.set_footer(text=footer_text)
        
        view = FMActionsView(bot_instance, raw_artist, img, is_p=is_p, cd=cd, user=user, spotify_url=spotify_url, song=raw_song, current_mode="full", track_data={'raw_data': data, 'processed': {'artist': artist, 'song': song, 'img': img, 'spotify_url': spotify_url, 'track_plays': track_plays, 't_info': t_info if 't_info' in locals() else None}})
        result = {"embed": embed, "view": view}
        return result, is_p
    except Exception as e: 
        print(f"parsing error: {e}")
        return {"embed": Theme.get_error_embed(description="Error formatting track.")}, False
async def process_top_artists(user, input_period=None):
    username = await get_lastfm_username(user.id)
    api_p, disp_p = get_period_data(input_period)
    
    d_source = await get_user_data_source(user.id)


    combined = {}
    original_names = {}
    if username and d_source != 'imported_only':
        if not (api_p.isdigit() and len(api_p) == 4):
            data = await fetch_top_artists(username, api_p, 1000)
            if data and 'topartists' in data:
                for a in data['topartists']['artist']:
                    key = a['name'].lower()
                    combined[key] = int(a['playcount'])
                    original_names[key] = a['name']

    local_data = {}
    if d_source != 'lastfm_only':
        local_data = await get_local_top_artists(user.id, 100000, api_p, before_dt=None)

    if not username and not local_data:
        return Theme.get_error_embed(description=f"**{user.name}** hasn't linked a Last.fm account! Link it with `/login` or import history on the web portal."), None, None

    for artist, count in local_data.items():
        key = artist.lower()
        if key in combined:
            combined[key] = max(combined[key], count)
        else:
            combined[key] = count
            original_names[key] = artist

    sorted_artists = sorted([(original_names[k], count) for k, count in combined.items()], key=lambda x: x[1], reverse=True)
    if not sorted_artists: return Theme.get_error_embed(description="No artist data found."), None, None

    view = TopItemsPaginator(user, sorted_artists, disp_p, username if d_source != 'imported_only' else None, 'ta')
    embed = view.generate_embed()
    return embed, view, None
async def process_top_tracks(user, input_period=None):
    username = await get_lastfm_username(user.id)
    api_p, disp_p = get_period_data(input_period)

    d_source = await get_user_data_source(user.id)


    combined = {}
    original_names = {}
    if username and d_source != 'imported_only':
        if not (api_p.isdigit() and len(api_p) == 4):
            data = await fetch_top_tracks(username, api_p, 1000)
            if data and 'toptracks' in data:
                for t in data['toptracks']['track']:
                    k = (t['name'].lower(), t['artist']['name'].lower())
                    combined[k] = int(t['playcount'])
                    original_names[k] = (t['name'], t['artist']['name'])

    local_tracks = []
    if d_source != 'lastfm_only':
        local_tracks = await get_local_top_tracks(user.id, 100000, api_p, before_dt=None)

    if not username and not local_tracks:
        return Theme.get_error_embed(description=f"**{user.name}** hasn't linked a Last.fm account! Link it with `/login` or import history on the web portal."), None, None

    for track_name, artist_name, plays in local_tracks:
        k = (track_name.lower(), artist_name.lower())
        if k in combined:
            combined[k] = max(combined[k], plays)
        else:
            combined[k] = plays
            original_names[k] = (track_name, artist_name)

    sorted_tracks = sorted([(original_names[k][0], original_names[k][1], count) for k, count in combined.items()], key=lambda x: x[2], reverse=True)
    if not sorted_tracks: return Theme.get_error_embed(description="No track data found."), None, None

    view = TopItemsPaginator(user, sorted_tracks, disp_p, username if d_source != 'imported_only' else None, 'tt')
    embed = view.generate_embed()
    return embed, view, None

async def process_top_albums(user, input_period=None):
    from .database import get_local_top_albums
    from src.utils.api import fetch_top_albums
    
    username = await get_lastfm_username(user.id)
    api_p, disp_p = get_period_data(input_period)

    d_source = await get_user_data_source(user.id)

    combined = {}
    original_names = {}
    if username and d_source != 'imported_only':
        if not (api_p.isdigit() and len(api_p) == 4):
            data = await fetch_top_albums(username, api_p, 1000)
            if data and 'topalbums' in data:
                for a in data['topalbums']['album']:
                    artist_name = a['artist']['name'] if isinstance(a.get('artist'), dict) else (a.get('artist') or "Unknown")
                    k = (a['name'].lower(), artist_name.lower())
                    combined[k] = int(a['playcount'])
                    original_names[k] = (a['name'], artist_name)

    local_albums = []
    if d_source != 'lastfm_only':
        local_albums = await get_local_top_albums(user.id, 100000, api_p, before_dt=None)

    if not username and not local_albums:
        return Theme.get_error_embed(description=f"**{user.name}** hasn't linked a Last.fm account! Link it with `/login` or import history on the web portal."), None, None

    for album_name, artist_name, plays in local_albums:
        k = (album_name.lower(), artist_name.lower())
        if k in combined:
            combined[k] = max(combined[k], plays)
        else:
            combined[k] = plays
            original_names[k] = (album_name, artist_name)

    sorted_albums = sorted([(original_names[k][0], original_names[k][1], count) for k, count in combined.items()], key=lambda x: x[2], reverse=True)
    if not sorted_albums: return Theme.get_error_embed(description="No album data found."), None, None

    view = TopItemsPaginator(user, sorted_albums, disp_p, username if d_source != 'imported_only' else None, 'tab')
    embed = view.generate_embed()
    return embed, view, None

class TopItemsPaginator(discord.ui.View):
    def __init__(self, user, sorted_items, disp_p, username, cmd_type='tt'):
        super().__init__(timeout=180)
        self.user = user
        self.sorted_items = sorted_items
        self.disp_p = disp_p
        self.username = username
        self.cmd_type = cmd_type
        self.current_page = 0
        self.items_per_page = 10
        self.max_pages = max(1, (len(sorted_items) + self.items_per_page - 1) // self.items_per_page)
        self.update_buttons()

    def update_buttons(self):
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.max_pages - 1

    def generate_embed(self):
        start = self.current_page * self.items_per_page
        end = start + self.items_per_page
        page_items = self.sorted_items[start:end]

        if self.cmd_type == 'tt':
            lines = [f"{get_medal(start + idx)} **{a}** — **{t}** `[{c:,}]`" for idx, (t, a, c) in enumerate(page_items)]
            title = f"🏆 {format_name(self.user)}'s Top Tracks ({self.disp_p})"
        elif self.cmd_type == 'tab':
            lines = [f"{get_medal(start + idx)} **{a}** — **{t}** `[{c:,}]`" for idx, (t, a, c) in enumerate(page_items)]
            title = f"🏆 {format_name(self.user)}'s Top Albums ({self.disp_p})"
        else:
            lines = [f"{get_medal(start + idx)} **{name}** `[{count:,}]`" for idx, (name, count) in enumerate(page_items)]
            title = f"🏆 {format_name(self.user)}'s Top Artists ({self.disp_p})"
            
        embed = Theme.get_embed(description=chr(10).join(lines), color=LASTFM_COLOR, user=self.user)
        embed.set_author(name=title, icon_url=self.user.display_avatar.url)
        embed.set_thumbnail(url=self.user.display_avatar.url)
        
        footer_text = f"Page {self.current_page + 1}/{self.max_pages} — {len(self.sorted_items)} items"
        if self.username: footer_text += f"\nScrobbling as {'DJ Scratch' if self.username.lower() == 'dj-scratch' else self.username}"
        else: footer_text += "\nUsing Imported Data"
        embed.set_footer(text=footer_text)
        return embed

    @discord.ui.button(label="", emoji="◀️", style=discord.ButtonStyle.secondary, custom_id="prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn't your menu!", ephemeral=True)
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.generate_embed(), view=self)

    @discord.ui.button(label="", emoji="▶️", style=discord.ButtonStyle.secondary, custom_id="next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn't your menu!", ephemeral=True)
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.generate_embed(), view=self)

    @discord.ui.select(
        placeholder="Select Time Period...",
        options=[
            discord.SelectOption(label="7 Days", value="7day", emoji="🗓️"),
            discord.SelectOption(label="1 Month", value="1month", emoji="📅"),
            discord.SelectOption(label="3 Months", value="3month", emoji="📆"),
            discord.SelectOption(label="6 Months", value="6month", emoji="🕰️"),
            discord.SelectOption(label="1 Year", value="12month", emoji="⏳"),
            discord.SelectOption(label="All Time", value="overall", emoji="♾️"),
        ]
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This menu isn't for you!", ephemeral=True)
        await interaction.response.defer()
        if self.cmd_type == 'ta':
            embed, view, err = await bot.process_top_artists(self.user, select.values[0])
        else:
            embed, view, err = await bot.process_top_tracks(self.user, select.values[0])
        if embed:
            await interaction.message.edit(embed=embed, view=view)
        else:
            await interaction.followup.send(err, ephemeral=True)

class CrownsPaginator(discord.ui.View):
    def __init__(self, user, guild, db_pool, current_sort="plays"):
        super().__init__(timeout=180)
        self.user = user
        self.guild = guild
        self.db_pool = db_pool
        self.current_sort = current_sort
        self.current_page = 0
        self.items_per_page = 10
        self.crowns = []
        self.max_pages = 1

    async def fetch_crowns(self):
        async with self.db_pool.acquire() as conn:
            if self.current_sort == "plays":
                self.crowns = await conn.fetch("SELECT artist_name, plays, claimed_at FROM server_crowns WHERE guild_id = $1 AND user_id = $2 ORDER BY plays DESC", str(self.guild.id), str(self.user.id))
            elif self.current_sort == "recent":
                self.crowns = await conn.fetch("SELECT artist_name, plays, claimed_at FROM server_crowns WHERE guild_id = $1 AND user_id = $2 ORDER BY claimed_at DESC NULLS LAST", str(self.guild.id), str(self.user.id))
            elif self.current_sort == "stolen":
                self.crowns = await conn.fetch("SELECT artist_name, plays, stolen_at as claimed_at, new_user_id FROM crown_history WHERE guild_id = $1 AND previous_user_id = $2 ORDER BY stolen_at DESC NULLS LAST", str(self.guild.id), str(self.user.id))
        
        self.max_pages = max(1, (len(self.crowns) + self.items_per_page - 1) // self.items_per_page)
        if self.current_page >= self.max_pages:
            self.current_page = max(0, self.max_pages - 1)
        self.update_buttons()

    def update_buttons(self):
        self.first_button.disabled = self.current_page == 0
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.max_pages - 1
        self.last_button.disabled = self.current_page >= self.max_pages - 1

    def generate_embed(self):
        from src.core.theme import Theme
        
        start = self.current_page * self.items_per_page
        end = start + self.items_per_page
        page_items = self.crowns[start:end]

        embed = Theme.get_embed(color=LASTFM_COLOR)
        if self.current_sort == "stolen":
            embed.set_author(name=f"Stolen Crowns for {format_name(self.user)} in {self.guild.name}", icon_url=self.user.display_avatar.url)
        else:
            embed.set_author(name=f"Crowns for {format_name(self.user)} in {self.guild.name}", icon_url=self.user.display_avatar.url)
            
        embed.set_thumbnail(url=self.user.display_avatar.url)
        
        lines = []
        import urllib.parse
        for i, r in enumerate(page_items):
            idx = start + i + 1
            artist_url = f"https://last.fm/music/{urllib.parse.quote(r['artist_name'])}"
            if self.current_sort == "stolen":
                stolen_by = self.guild.get_member(int(r['new_user_id']))
                stolen_by_name = stolen_by.display_name if stolen_by else "Unknown"
                if r['claimed_at']:
                    lines.append(f"`{idx}.` **[{r['artist_name']}]({artist_url})** — *{r['plays']:,} plays* — Stolen by {stolen_by_name} <t:{int(r['claimed_at'].timestamp())}:R>")
                else:
                    lines.append(f"`{idx}.` **[{r['artist_name']}]({artist_url})** — *{r['plays']:,} plays* — Stolen by {stolen_by_name}")
            else:
                if r['claimed_at']:
                    lines.append(f"`{idx}.` **[{r['artist_name']}]({artist_url})** — *{r['plays']:,} plays* — Claimed <t:{int(r['claimed_at'].timestamp())}:R>")
                else:
                    lines.append(f"`{idx}.` **[{r['artist_name']}]({artist_url})** — *{r['plays']:,} plays*")

        if not lines:
            if self.current_sort == "stolen":
                lines = ["You haven't had any crowns stolen... yet!"]
            else:
                lines = ["You don't hold any crowns!"]

        embed.description = chr(10).join(lines)
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.max_pages} — {len(self.crowns)} total crowns")
        return embed

    @discord.ui.select(
        placeholder="Select Sort Order...",
        options=[
            discord.SelectOption(label="Active crowns ordered by playcount", value="plays"),
            discord.SelectOption(label="Recently obtained crowns", value="recent"),
            discord.SelectOption(label="Recently stolen crowns", value="stolen")
        ],
        row=0
    )
    async def sort_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn't your menu!", ephemeral=True)
        self.current_sort = select.values[0]
        self.current_page = 0
        for opt in select.options:
            opt.default = (opt.value == self.current_sort)
        await self.fetch_crowns()
        await interaction.response.edit_message(embed=self.generate_embed(), view=self)

    @discord.ui.button(label="", emoji="⏪", style=discord.ButtonStyle.secondary, custom_id="first", row=1)
    async def first_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn't your menu!", ephemeral=True)
        self.current_page = 0
        self.update_buttons()
        await interaction.response.edit_message(embed=self.generate_embed(), view=self)

    @discord.ui.button(label="", emoji="◀️", style=discord.ButtonStyle.secondary, custom_id="prev", row=1)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn't your menu!", ephemeral=True)
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.generate_embed(), view=self)

    @discord.ui.button(label="", emoji="▶️", style=discord.ButtonStyle.secondary, custom_id="next", row=1)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn't your menu!", ephemeral=True)
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.generate_embed(), view=self)

    @discord.ui.button(label="", emoji="⏩", style=discord.ButtonStyle.secondary, custom_id="last", row=1)
    async def last_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn't your menu!", ephemeral=True)
        self.current_page = self.max_pages - 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.generate_embed(), view=self)

class ArtistTracksPaginator(discord.ui.View):
    def __init__(self, user, artist_name, sorted_tracks, total_plays, local_tracks_present):
        super().__init__(timeout=180)
        self.user = user
        self.artist_name = artist_name
        self.sorted_tracks = sorted_tracks
        self.total_plays = total_plays
        self.local_tracks_present = local_tracks_present
        self.current_page = 0
        self.items_per_page = 10
        self.max_pages = max(1, (len(sorted_tracks) + self.items_per_page - 1) // self.items_per_page)
        self.update_buttons()

    def update_buttons(self):
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.max_pages - 1

    def generate_embed(self):
        start = self.current_page * self.items_per_page
        end = start + self.items_per_page
        page_tracks = self.sorted_tracks[start:end]

        lines = [f"{get_medal(start + idx)} **{t}** `[{c:,}]`" for idx, (t, c) in enumerate(page_tracks)]
        
        embed = Theme.get_embed(description=chr(10).join(lines), color=LASTFM_COLOR, user=self.user)
        embed.set_author(name=f"🏆 Your top tracks for '{self.artist_name}'", icon_url=self.user.display_avatar.url)
        
        footer_text = f"Page {self.current_page + 1}/{self.max_pages} — {len(self.sorted_tracks)} different tracks\n{format_name(self.user)} has {self.total_plays:,} total artist plays"
        embed.set_footer(text=footer_text)
        return embed

    @discord.ui.button(label="", emoji="◀️", style=discord.ButtonStyle.secondary, custom_id="prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn't your menu!", ephemeral=True)
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.generate_embed(), view=self)

    @discord.ui.button(label="", emoji="▶️", style=discord.ButtonStyle.secondary, custom_id="next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn't your menu!", ephemeral=True)
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.generate_embed(), view=self)

async def process_artist_tracks(user, artist_name):
    username = await get_lastfm_username(user.id)
    d_source = await get_user_data_source(user.id)

    if not artist_name:
        if not username or d_source == 'imported_only': return Theme.get_error_embed(description="Link account or provide an artist name."), None, None
        np_data = await fetch_now_playing(username, 1)
        try: artist_name = np_data['recenttracks']['track'][0]['artist']['#text']
        except: return Theme.get_error_embed(description="You aren't playing anything right now and didn't provide an artist!"), None, None

    lastfm_tracks = {}
    if username and d_source != 'imported_only':
        tracks = await fetch_user_artist_tracks_lastfm(username, artist_name)
        for t_name, playcount in tracks:
            lastfm_tracks[t_name] = playcount

    local_tracks = []
    if d_source != 'lastfm_only':
        local_tracks = await get_local_artist_top_tracks(user.id, artist_name, 5000, 'overall', before_dt=None)

    if not username and not local_tracks:
        return Theme.get_error_embed(description=f"**{user.name}** hasn't linked a Last.fm account! Link it with `/login` or import history on the web portal."), None, None

    combined = dict(lastfm_tracks)
    for track_name, plays in local_tracks:
        combined[track_name] = max(combined.get(track_name, 0), plays)

    sorted_tracks = sorted(combined.items(), key=lambda x: x[1], reverse=True)
    if not sorted_tracks: return Theme.get_error_embed(description=f"No track data found for **{artist_name}**."), None, None

    total_plays = sum(combined.values())
    
    # Optionally get accurate total plays from API if Last.fm is linked
    if username:
        bot_instance = bot
        session = getattr(bot_instance, 'session', None)
        api_plays = await fetch_artist_playcount(session, username, artist_name)
        if api_plays > total_plays: total_plays = api_plays
        elif local_tracks:
            # Add imported data from before registration
            local_artist_plays = sum(p for _, p in local_tracks)
            total_plays = api_plays + local_artist_plays

    view = ArtistTracksPaginator(user, artist_name, sorted_tracks, total_plays, bool(local_tracks))
    embed = view.generate_embed()
    
    return embed, view, None

async def process_recent(user):
    bot_instance = bot
    session = getattr(bot_instance, 'session', None)

    username = await get_lastfm_username(user.id)
    d_source = await get_user_data_source(user.id)
    if username and d_source != 'imported_only':
        data = await fetch_now_playing(username, 10)
        if data and 'recenttracks' in data and 'track' in data['recenttracks'] and data['recenttracks']['track']:
            lines = []
            for i, t in enumerate(data['recenttracks']['track'][:10]):
                is_np = i == 0 and t.get('@attr', {}).get('nowplaying') == 'true'
                prefix = "🎶" if is_np else f"` {i+1}. `"
                track_name = t.get('name', 'Unknown Track')
                artist_name = t.get('artist', {}).get('#text', 'Unknown Artist')
                # Fetch timestamp
                ts = ""
                if not is_np and 'date' in t and 'uts' in t['date']:
                    ts = f" <t:{t['date']['uts']}:R>"
                
                track_formatted = f"**{track_name}**{ts}"
                lines.append(f"{prefix} {track_formatted} — *{artist_name}*")
                
            embed = Theme.get_embed(description=chr(10).join(lines), color=LASTFM_COLOR)
            embed.set_author(name=f"{format_name(user)}'s Recent Tracks", icon_url=user.display_avatar.url)
            
            # Use album art for thumbnail if available
            first_track = data['recenttracks']['track'][0]
            thumbnail_url = user.display_avatar.url
            if 'image' in first_track and len(first_track['image']) > 0:
                # get the largest image
                img_url = first_track['image'][-1].get('#text')
                if img_url:
                    thumbnail_url = img_url
                    
            embed.set_thumbnail(url=thumbnail_url)
            embed.set_footer(text=f"Scrobbling as {'DJ Scratch' if username.lower() == 'dj-scratch' else username}")
            return embed, None
    # Fallback to local DB
    if d_source != 'lastfm_only':
        local = await get_local_recent_tracks(user.id, 10)
        if local:
            lines = [f"` {i+1}. ` **{t}**" + (f" <t:{int(ts.timestamp())}:R>" if ts else "") + f" — *{a}*" for i, (t, a, ts) in enumerate(local)]
            embed = Theme.get_embed(description=chr(10).join(lines), color=LASTFM_COLOR)
            embed.set_author(name=f"{format_name(user)}'s Recent Tracks *(Imported)*", icon_url=user.display_avatar.url)
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.set_footer(text=f"Requested by {format_name(user)} • Using Imported Data", icon_url=user.display_avatar.url)
            return embed, None
    if not username:
        return Theme.get_error_embed(description=f"**{user.name}** hasn't linked a Last.fm account! Link it with `/login` or import history on the web portal."), None
    return Theme.get_error_embed(description=f"No recent tracks found for **{user.name}**."), None


async def process_judge(user):
    username = await get_lastfm_username(user.id)
    
    d_source = await get_user_data_source(user.id)
    # 1. Gather Top 14 Artists
    artists_dict = {}
    if username and d_source != 'imported_only':
        data = await fetch_top_artists(username, 'overall', 50)
        if data and 'topartists' in data:
            for a in data['topartists']['artist']:
                artists_dict[a['name']] = int(a['playcount'])
    
    local_artists = {}
    if d_source != 'lastfm_only':
        local_artists = await get_local_top_artists(user.id, 50, 'overall')
    for a, c in local_artists.items():
        artists_dict[a] = max(artists_dict.get(a, 0), c)
        
    top_artists = sorted(artists_dict.items(), key=lambda x: x[1], reverse=True)[:14]
    
    # 2. Gather Top 16 Tracks
    tracks_dict = {}
    if username:
        data = await fetch_top_tracks(username, 'overall', 50)
        if data and 'toptracks' in data:
            for t in data['toptracks']['track']:
                key = (t['name'], t['artist']['name'])
                tracks_dict[key] = int(t['playcount'])
                
    local_tracks = await get_local_top_tracks(user.id, 50, 'overall')
    for t_name, a_name, plays in local_tracks:
        key = (t_name, a_name)
        tracks_dict[key] = tracks_dict.get(key, 0) + plays

    top_tracks = sorted(tracks_dict.items(), key=lambda x: x[1], reverse=True)[:16]

    if not top_artists and not top_tracks:
        if not username:
            return Theme.get_error_embed(description=f"**{user.name}** hasn't linked a Last.fm account! Link it with `/login` or import history on the web portal to use the AI Judge."), None
        return Theme.get_error_embed(description=f"Not enough data to judge **{user.name}**."), None

    # Format the data exactly like fmbot
    artist_lines = [f"{a[:40]} - {c} plays" for a, c in top_artists]
    track_lines = [f"{a[:40]} - {t[:50]} - {c} plays" for (t, a), c in top_tracks]
    
    user_data = "My top artists:\n" + "\n".join(artist_lines) + "\n\nMy top tracks:\n" + "\n".join(track_lines)

    try:
        system_prompt = (
            "You are an incredibly witty, brutally creative, and oddly specific AI music critic. "
            "Roast my music taste based on my all-time top artists and top tracks. "
            "Your roast must be structured as 3-4 short paragraphs. "
            "DO NOT just list the music. Instead, weave the artists and tracks into highly specific, hilarious, and absurd situational analogies (e.g., 'a leather jacket bought at an airport gift shop', 'a dad-rock support group', 'the metal uncle who wandered into the wrong family reunion'). "
            "Group artists together that share a vibe, or hilariously contrast ones that cause severe emotional whiplash. "
            "Format artist and track names in *italics*. "
            "Keep the tone sarcastic, punchy, vivid, and under 1500 characters. "
            "Conclude with a final, devastating one-liner summarizing my musical identity."
        )
        
        api_key = os.getenv("GROQ_API_KEY", "").strip().strip("'").strip('"')
        
        if not api_key:
            return Theme.get_error_embed(description="Please get a free Groq API key from console.groq.com/keys and put it in your .env as GROQ_API_KEY!"), None

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_data}
            ]
        }
        
        bot_instance = bot
        session = getattr(bot_instance, 'session', None)
        local_session = False
        if session is None:
            import aiohttp
            session = aiohttp.ClientSession()
            local_session = True

        roast_text = ""
        try:
            import aiohttp
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    roast_text = data['choices'][0]['message']['content']
                else:
                    err_text = await resp.text()
                    print(f"Groq API Error {resp.status}: {err_text}")
                    roast_text = "<:404:882220605783560222> OpenAI API error - please try again"
        except Exception as e:
            print(f"Groq API Request Error: {e}")
            roast_text = "<:404:882220605783560222> OpenAI API error - please try again"
        finally:
            if local_session:
                await session.close()
        
        roast_text = f"> {roast_text.replace(chr(10), chr(10) + '> ')}" if roast_text else ""
        embed = Theme.get_embed(
            description=roast_text,
            color=0xFF7A01,

        )
        embed.set_author(name=f"{format_name(user)}'s .fmbot AI judgement - Roast 🔥", icon_url=user.display_avatar.url)
        embed.set_footer(text="Powered by Groq")
        return embed, None
    except Exception as e:
        print(f"Judge API Error: {e}")
        return Theme.get_error_embed(description="An error occurred while contacting the AI Judge. Try again later."), None

async def process_profile(user):
    bot_instance = bot
    session = getattr(bot_instance, 'session', None)

    username = await get_lastfm_username(user.id)
    local_total = await get_local_total_plays(user.id)

    from src.core.database import get_user_created_at
    created_at = await get_user_created_at(user.id)
    
    d_source = await get_user_data_source(user.id)

    if not username and d_source == 'lastfm_only':
        return Theme.get_error_embed(description=f"**{user.name}** hasn't linked a Last.fm account! Link it with `/login` or import history on the web portal."), None, None

    class ProfileLinksView(discord.ui.View):
        def __init__(self, username, lastfm_url):
            super().__init__(timeout=None)
            safe_name = urllib.parse.quote(format_name(user).replace(' ', '-'))
            self.add_item(discord.ui.Button(label="DJ Scratch Profile", style=discord.ButtonStyle.link, url=f"https://dj-scratch.vercel.app/{safe_name}"))
            if lastfm_url:
                self.add_item(discord.ui.Button(label="Last.fm Profile", style=discord.ButtonStyle.link, url=lastfm_url))

    view = None

    embed = Theme.get_embed(color=LASTFM_COLOR)
    embed.set_author(name=f"{format_name(user)}'s Profile", icon_url=user.display_avatar.url)

    if username:
        data = await fetch_user_profile(username)
        if data:
            if 'error' in data or 'user' not in data:
                return Theme.get_error_embed(description=f"Last.fm Error: {data.get('message', 'User not found on Last.fm.')}"), None, None
            info = data['user']
            embed.title = f"{info['name']}'s DJ Scratch Profile"
            safe_name = urllib.parse.quote(format_name(user).replace(' ', '-'))
            embed.url = f"https://dj-scratch.vercel.app/{safe_name}"
            lastfm_plays = int(info['playcount'])
            view = ProfileLinksView(username, info['url'])
            
            # Smart De-duplication of duplicate plays:
            if d_source == 'imported_only':
                total = local_total
                embed.add_field(name="📦 Imported Plays", value=f"**{local_total:,}**", inline=True)
            elif d_source == 'lastfm_only':
                total = lastfm_plays
                embed.add_field(name="🎧 Last.fm Scrobbles", value=f"**{lastfm_plays:,}**", inline=True)
                embed.add_field(name="🎵 Total Plays", value=f"**{total:,}**", inline=True)
            else:
                total = max(lastfm_plays, local_total)
                embed.add_field(name="🎧 Last.fm Scrobbles", value=f"**{lastfm_plays:,}**", inline=True)
                if local_total > 0:
                    embed.add_field(name="📦 Imported Plays", value=f"**{local_total:,}**", inline=True)
                    embed.add_field(name="🎵 Total Plays", value=f"**{total:,}**", inline=True)
            
            country = info.get('country', 'Not Set')
            embed.add_field(name="🌍 Country", value=country if country and country != "None" else "Not set", inline=True)
            if info['image'][3]['#text']: embed.set_thumbnail(url=info['image'][3]['#text'])
            
            if local_total > 0 and d_source != 'imported_only':
                overlap = (lastfm_plays + local_total) - total
                embed.set_footer(text=f"Filtered {overlap:,} duplicate scrobbles using MAX deduplication.")
            
            if created_at:
                embed.add_field(name="📅 Joined", value=discord.utils.format_dt(created_at, style='D'), inline=True)
                
    elif local_total > 0:
        embed.add_field(name="📦 Imported Plays", value=f"**{local_total:,}**", inline=True)
        embed.add_field(name="ℹ️ Last.fm", value="Not linked — use `/login`", inline=True)
        if created_at:
            embed.add_field(name="📅 Joined", value=discord.utils.format_dt(created_at, style='D'), inline=True)

    return embed, view, None

async def get_all_valid_users(guild):
    users_db = await load_users()
    valid = {uid: lname for uid, lname in users_db.items() if uid in [str(m.id) for m in guild.members]}
    
    from src.core.database import db_fetch
    imported_ids = await db_fetch("SELECT id FROM imported_users")
    for row in imported_ids:
        uid = row['id']
        if uid not in valid and guild.get_member(int(uid)):
            valid[uid] = None
    return valid

async def get_combined_playcount(session, uid, lname, artist, track=None, album=None):
    from src.core.database import get_user_data_source, get_local_artist_playcount, get_local_track_playcount, get_local_album_playcount
    from src.utils.api import fetch_artist_playcount, fetch_track_playcount, fetch_album_playcount
    d_source = await get_user_data_source(uid)
    
    lastfm = 0
    local = 0
    
    if d_source != 'imported_only' and lname:
        if track: lastfm = await fetch_track_playcount(session, lname, artist, track)
        elif album: lastfm = await fetch_album_playcount(session, lname, artist, album)
        else: lastfm = await fetch_artist_playcount(session, lname, artist)
        
    if d_source != 'lastfm_only':
        if track: local = await get_local_track_playcount(uid, artist, track)
        elif album: local = await get_local_album_playcount(uid, artist, album)
        else: local = await get_local_artist_playcount(uid, artist)
        
    return max(lastfm, local)

async def get_combined_top_artists(uid, lname, limit=100):
    from src.core.database import get_user_data_source, get_local_top_artists
    from src.utils.api import fetch_top_artists
    d_source = await get_user_data_source(uid)
    
    combined = {}
    if d_source != 'imported_only' and lname:
        data = await fetch_top_artists(lname, 'overall', limit)
        if data and 'topartists' in data and data['topartists']['artist']:
            for a in data['topartists']['artist']:
                combined[a['name'].lower()] = {'name': a['name'], 'plays': int(a['playcount'])}
                
    if d_source != 'lastfm_only':
        local_data = await get_local_top_artists(uid, limit * 2, 'overall', before_dt=None)
        for artist, count in local_data.items():
            key = artist.lower()
            if key in combined:
                combined[key]['plays'] = max(combined[key]['plays'], count)
            else:
                combined[key] = {'name': artist, 'plays': count}
                
    sorted_artists = sorted(list(combined.values()), key=lambda x: x['plays'], reverse=True)[:limit]
    return sorted_artists

async def get_combined_top_albums(uid, lname, limit=100, period='overall'):
    from src.core.database import get_user_data_source, get_local_top_albums
    from src.utils.api import fetch_top_albums
    d_source = await get_user_data_source(uid)
    
    combined = {}
    if d_source != 'imported_only' and lname:
        data = await fetch_top_albums(lname, period, limit)
        if data and 'topalbums' in data and data['topalbums']['album']:
            for a in data['topalbums']['album']:
                key = f"{a['artist']['name']} - {a['name']}".lower()
                
                # Fetch image if available
                img_url = None
                if 'image' in a:
                    for img in a['image']:
                        if img['size'] in ('extralarge', 'large'):
                            img_url = img['#text']
                            if img_url: break
                            
                combined[key] = {
                    'artist': a['artist']['name'],
                    'name': a['name'],
                    'plays': int(a['playcount']),
                    'image': img_url
                }
                
    if d_source != 'lastfm_only':
        local_data = await get_local_top_albums(uid, limit * 2, period, before_dt=None)
        # local_data format: [(album, artist, count)]
        for album, artist, count in local_data:
            key = f"{artist} - {album}".lower()
            if key in combined:
                combined[key]['plays'] = max(combined[key]['plays'], count)
            else:
                combined[key] = {'artist': artist, 'name': album, 'plays': count, 'image': None}
                
    sorted_albums = sorted(list(combined.values()), key=lambda x: x['plays'], reverse=True)[:limit]
    return sorted_albums
async def process_whoknows(guild, user, artist_name):
    bot_instance = bot
    session = getattr(bot_instance, 'session', None)
    if not guild: return Theme.get_error_embed(description="Must be used in a server."), None
    
    linked = await get_all_valid_users(guild)
    if not linked: return Theme.get_error_embed(description="No one in this server has linked their account or imported data."), None
    
    if not artist_name:
        username = await get_lastfm_username(user.id)
        if not username: return Theme.get_error_embed(description="Link account or provide an artist name."), None
        np_data = await fetch_now_playing(username, 1)
        try: artist_name = np_data['recenttracks']['track'][0]['artist']['#text']
        except: return Theme.get_error_embed(description="You aren't playing anything right now!"), None

    lb = []
    display_names = await load_display_names()
    tasks = [(uid, lname, get_combined_playcount(session, uid, lname, artist_name)) for uid, lname in linked.items()]
    results = await asyncio.gather(*(t[2] for t in tasks))
    for idx, pc in enumerate(results):
        if pc > 0:
            uid = tasks[idx][0]
            custom_name = display_names.get(uid)
            if custom_name:
                name = custom_name
            else:
                member = guild.get_member(int(uid))
                name = member.display_name if member else tasks[idx][1]
            lb.append({"name": name, "plays": pc, "uid": uid})

    if not lb: return Theme.get_error_embed(description=f"No one here listens to **{artist_name}**."), None
    lb = sorted(lb, key=lambda x: x['plays'], reverse=True)
    
    if lb[0]['plays'] >= 30:
        top_uid = lb[0]['uid']
        global db_pool
        if db_pool:
            import asyncpg
            if isinstance(db_pool, asyncpg.pool.Pool):
                async with db_pool.acquire() as conn:
                    existing = await conn.fetchrow("SELECT user_id FROM server_crowns WHERE guild_id = $1 AND artist_name = $2", str(guild.id), artist_name)
                    if existing and existing['user_id'] != str(top_uid):
                        await conn.execute('''
                            INSERT INTO crown_history (guild_id, artist_name, previous_user_id, new_user_id, plays)
                            VALUES ($1, $2, $3, $4, $5)
                        ''', str(guild.id), artist_name, existing['user_id'], str(top_uid), lb[0]['plays'])
                        
                        await conn.execute('''
                            UPDATE server_crowns SET user_id = $1, plays = $2, claimed_at = CURRENT_TIMESTAMP
                            WHERE guild_id = $3 AND artist_name = $4
                        ''', str(top_uid), lb[0]['plays'], str(guild.id), artist_name)
                    else:
                        await conn.execute('''
                            INSERT INTO server_crowns (guild_id, user_id, artist_name, plays)
                            VALUES ($1, $2, $3, $4)
                            ON CONFLICT (guild_id, artist_name) DO UPDATE 
                            SET plays = EXCLUDED.plays
                        ''', str(guild.id), str(top_uid), artist_name, lb[0]['plays'])

    lines = [f"{get_medal(i)} **{u['name']}** — **{u['plays']:,}** plays" for i, u in enumerate(lb[:15])]
    user_color = await get_color(user.id)
    embed = Theme.get_embed(description=chr(10).join(lines), color=user_color)
    embed.set_author(name=f"Who knows {artist_name} in {guild.name}?", icon_url=guild.icon.url if guild.icon else None)
    embed.set_thumbnail(url=user.display_avatar.url)
    
    footer_text = f"Requested by {format_name(user)}"
    if lb[0]['name'] == format_name(user): footer_text = "👑 You hold the crown! • " + footer_text
    embed.set_footer(text=footer_text)
    return embed, None

async def process_whoknowstrack(guild, user, query):
    bot_instance = bot
    session = getattr(bot_instance, 'session', None)
    if not guild: return Theme.get_error_embed(description="Must be used in a server."), None
    
    linked = await get_all_valid_users(guild)
    if not linked: return Theme.get_error_embed(description="No one in this server has linked their account or imported data."), None
    
    artist_name = None
    track_name = None
    if not query:
        username = await get_lastfm_username(user.id)
        if not username: return Theme.get_error_embed(description="Link account or provide an `Artist - Track`."), None
        np_data = await fetch_now_playing(username, 1)
        try:
            track = np_data['recenttracks']['track'][0]
            artist_name = track['artist']['#text']
            track_name = track['name']
        except: return Theme.get_error_embed(description="You aren't playing anything right now!"), None
    else:
        parts = query.split(' - ', 1)
        if len(parts) != 2:
            return Theme.get_error_embed(description="Please provide `Artist - Track` or be playing a track."), None
        artist_name, track_name = parts[0].strip(), parts[1].strip()

    lb = []
    display_names = await load_display_names()
    tasks = [(uid, lname, get_combined_playcount(session, uid, lname, artist_name, track=track_name)) for uid, lname in linked.items()]
    results = await asyncio.gather(*(t[2] for t in tasks))
    for idx, pc in enumerate(results):
        if pc > 0:
            uid = tasks[idx][0]
            custom_name = display_names.get(uid)
            if custom_name:
                name = custom_name
            else:
                member = guild.get_member(int(uid))
                name = member.display_name if member else tasks[idx][1]
            lb.append({"name": name, "plays": pc, "uid": uid})

    if not lb: return Theme.get_error_embed(description=f"No one here listens to **{artist_name} - {track_name}**."), None
    lb = sorted(lb, key=lambda x: x['plays'], reverse=True)
    
    lines = [f"{get_medal(i)} **{u['name']}** — **{u['plays']:,}** plays" for i, u in enumerate(lb[:15])]
    embed = Theme.get_embed(description=chr(10).join(lines), color=LASTFM_COLOR)
    embed.set_author(name=f"Who knows {artist_name} - {track_name} in {guild.name}?", icon_url=guild.icon.url if guild.icon else None)
    embed.set_thumbnail(url=user.display_avatar.url)
    
    footer_text = f"Requested by {format_name(user)}"
    if lb[0]['name'] == format_name(user): footer_text = "👑 You hold the crown! • " + footer_text
    embed.set_footer(text=footer_text)
    return embed, None

async def process_whoknowsalbum(guild, user, query):
    bot_instance = bot
    session = getattr(bot_instance, 'session', None)
    if not guild: return Theme.get_error_embed(description="Must be used in a server."), None
    
    linked = await get_all_valid_users(guild)
    if not linked: return Theme.get_error_embed(description="No one in this server has linked their account or imported data."), None
    
    artist_name = None
    album_name = None
    if not query:
        username = await get_lastfm_username(user.id)
        if not username: return Theme.get_error_embed(description="Link account or provide an `Artist - Album`."), None
        np_data = await fetch_now_playing(username, 1)
        try:
            track = np_data['recenttracks']['track'][0]
            artist_name = track['artist']['#text']
            album_name = track['album']['#text']
            if not album_name:
                return Theme.get_error_embed(description="The current track has no album tagged!"), None
        except: return Theme.get_error_embed(description="You aren't playing anything right now!"), None
    else:
        parts = query.split(' - ', 1)
        if len(parts) != 2:
            return Theme.get_error_embed(description="Please provide `Artist - Album` or be playing a track with an album."), None
        artist_name, album_name = parts[0].strip(), parts[1].strip()

    lb = []
    display_names = await load_display_names()
    tasks = [(uid, lname, get_combined_playcount(session, uid, lname, artist_name, album=album_name)) for uid, lname in linked.items()]
    results = await asyncio.gather(*(t[2] for t in tasks))
    for idx, pc in enumerate(results):
        if pc > 0:
            uid = tasks[idx][0]
            custom_name = display_names.get(uid)
            if custom_name:
                name = custom_name
            else:
                member = guild.get_member(int(uid))
                name = member.display_name if member else tasks[idx][1]
            lb.append({"name": name, "plays": pc, "uid": uid})

    if not lb: return Theme.get_error_embed(description=f"No one here listens to **{artist_name} - {album_name}**."), None
    lb = sorted(lb, key=lambda x: x['plays'], reverse=True)
    
    lines = [f"{get_medal(i)} **{u['name']}** — **{u['plays']:,}** plays" for i, u in enumerate(lb[:15])]
    embed = Theme.get_embed(description=chr(10).join(lines), color=LASTFM_COLOR)
    embed.set_author(name=f"Who knows {artist_name} - {album_name} in {guild.name}?", icon_url=guild.icon.url if guild.icon else None)
    embed.set_thumbnail(url=user.display_avatar.url)
    
    footer_text = f"Requested by {format_name(user)}"
    if lb[0]['name'] == format_name(user): footer_text = "👑 You hold the crown! • " + footer_text
    embed.set_footer(text=footer_text)
    return embed, None

async def process_taste(guild, user1, user2):
    if not user2:
        return Theme.get_error_embed(description="You must specify a user to compare taste with."), None
    
    if user1.id == user2.id:
        return Theme.get_error_embed(description="You can't compare taste with yourself!"), None
        
    username1 = await get_lastfm_username(user1.id)
    username1 = await get_lastfm_username(user1.id)
    username2 = await get_lastfm_username(user2.id)
    
    from src.core.database import get_local_total_plays
    if not username1 and (await get_local_total_plays(user1.id)) == 0:
        return Theme.get_error_embed(description=f"{format_name(user1)} has not linked their Last.fm account or imported data."), None
    if not username2 and (await get_local_total_plays(user2.id)) == 0:
        return Theme.get_error_embed(description=f"{format_name(user2)} has not linked their Last.fm account or imported data."), None

    tasks = [
        get_combined_top_artists(user1.id, username1, 100),
        get_combined_top_artists(user2.id, username2, 100)
    ]
    results = await asyncio.gather(*tasks)
    
    if not results[0]:
        return Theme.get_error_embed(description=f"Could not fetch top artists for {format_name(user1)}."), None
    if not results[1]:
        return Theme.get_error_embed(description=f"Could not fetch top artists for {format_name(user2)}."), None
        
    artists1 = {a['name'].lower(): (int(a['plays']), i) for i, a in enumerate(results[0])}
    artists2 = {a['name'].lower(): (int(a['plays']), i) for i, a in enumerate(results[1])}
    
    common = []
    score = 0
    for name, (pc1, rank1) in artists1.items():
        if name in artists2:
            pc2, rank2 = artists2[name]
            common.append({'name': results[0][rank1]['name'], 'pc1': pc1, 'pc2': pc2})
            score += (100 - abs(rank1 - rank2)) * (min(pc1, pc2))
            
    common = sorted(common, key=lambda x: x['pc1'] + x['pc2'], reverse=True)
    
    if score == 0: percentage = 0
    else: percentage = min(100, round((score / 15000) * 100))
    
    desc = f"**{len(common)}** matches (**{percentage}%**) out of top **100** overall\n\n"
    if common:
        u1_name = format_name(user1)[:15]
        u2_name = format_name(user2)[:15]
        
        max_artist_len = 19
        u1_width = max(len(u1_name), 6)
        u2_width = max(len(u2_name), 6)
        
        table = f"```\nArtist{' ' * (max_artist_len - 6)} {u1_name:>{u1_width}}   {u2_name:<{u2_width}}\n"
        table += "-" * (max_artist_len + u1_width + u2_width + 4) + "\n"
        
        for a in common[:15]:
            artist_name = a['name'][:max_artist_len-1].ljust(max_artist_len)
            
            if a['pc1'] > a['pc2']: comp = ">"
            elif a['pc1'] < a['pc2']: comp = "<"
            else: comp = "="
            
            pc1_str = str(a['pc1']).rjust(u1_width)
            pc2_str = str(a['pc2'])
            
            table += f"{artist_name} {pc1_str} {comp} {pc2_str}\n"
            
        table += "```"
        desc += table
    else:
        desc += "You have no common artists in your top 100!"
        
    embed = Theme.get_embed(description=desc, color=LASTFM_COLOR)
    embed.set_author(name=f"Top artist comparison — {format_name(user1)} vs {format_name(user2)}")
    return embed, None

async def _resolve_live_entry(fid, lname):
    """Fetch one friend's current/recent track. Returns a dict or None on failure."""
    from src.utils.api import fetch_now_playing
    try:
        data = await fetch_now_playing(lname, 1)
        tracks = (data or {}).get('recenttracks', {}).get('track', [])
        if not tracks:
            return None
        t = tracks[0]
        artist = t.get('artist', {})
        entry = {
            'track': t.get('name', 'Unknown'),
            'artist': artist.get('#text', '') if isinstance(artist, dict) else str(artist),
            'live': isinstance(t.get('@attr'), dict) and t['@attr'].get('nowplaying') == 'true',
            'uts': None,
        }
        date = t.get('date') or {}
        if isinstance(date, dict) and date.get('uts'):
            try:
                entry['uts'] = int(date['uts'])
            except (TypeError, ValueError):
                pass
        return entry
    except Exception:
        return None

async def process_live(user):
    """What your DJ Scratch friends are playing right now."""
    from src.core.database import get_friends, get_local_total_plays
    friends = await get_friends(user.id)
    accepted = [f for f in (friends or []) if f.get('status') == 'accepted' and str(f.get('id')) != str(user.id)]
    if not accepted:
        if (await get_local_total_plays(user.id)) == 0 and not await get_lastfm_username(user.id):
            return Theme.get_error_embed(description="Link Last.fm with `/login` and add friends with `/social addfriend` first!"), None
        return Theme.get_error_embed(description="You have no DJ Scratch friends yet! Add some with `/social addfriend` or on the website."), None

    accepted = accepted[:10]
    lnames = await asyncio.gather(*[get_lastfm_username(f['id']) for f in accepted])
    coros, order = [], []
    for i, (f, lname) in enumerate(zip(accepted, lnames)):
        if lname:
            coros.append(_resolve_live_entry(f['id'], lname))
            order.append(i)
    resolved = await asyncio.gather(*coros) if coros else []
    entries: list = [None] * len(accepted)
    for i, e in zip(order, resolved):
        entries[i] = e

    # Best-effort Discord display names (falls back to Last.fm username).
    names = []
    for f, lname in zip(accepted, lnames):
        try:
            du = await bot.fetch_user(int(f['id']))
            names.append(format_name(du))
        except Exception:
            names.append(lname or "Unknown")

    live_lines, recent_lines = [], []
    for name, entry in zip(names, entries):
        if not entry:
            continue
        label = f"**{name}** — {entry['track']} — {entry['artist']}"
        if entry['live']:
            live_lines.append(f"🟢 {label}")
        else:
            suffix = ""
            if entry['uts']:
                try:
                    suffix = f" (<t:{entry['uts']}:R>)"
                except Exception:
                    pass
            recent_lines.append(f"⚪ {label}{suffix}")

    if not live_lines and not recent_lines:
        return Theme.get_error_embed(description="None of your friends have scrobbled anything visible right now."), None

    desc = ""
    if live_lines:
        desc += "**Live now**\n" + "\n".join(live_lines[:10]) + "\n\n"
    if recent_lines:
        desc += "**Recently played**\n" + "\n".join(recent_lines[:10])
    embed = Theme.get_embed(description=desc.strip(), color=LASTFM_COLOR)
    embed.set_author(name=f"Friends live — {format_name(user)}")
    return embed, None

async def process_insights(user):
    """24h plays, milestone progress and top-artist share."""
    import math
    import time as _time
    from src.utils.api import fetch_recent_tracks, fetch_user_profile, fetch_top_artists
    username = await get_lastfm_username(user.id)
    local_total = await get_local_total_plays(user.id)
    if not username and local_total == 0:
        return Theme.get_error_embed(description=f"{format_name(user)} has not linked their Last.fm account or imported data."), None

    plays_24h = 0
    total = local_total
    top_name, top_plays = None, 0
    if username:
        try:
            data = await fetch_recent_tracks(username, 200)
            tracks = (data or {}).get('recenttracks', {}).get('track', []) or []
            cutoff = _time.time() - 24 * 60 * 60
            for t in tracks:
                if isinstance(t.get('@attr'), dict) and t['@attr'].get('nowplaying') == 'true':
                    continue
                date = t.get('date') or {}
                uts = date.get('uts') if isinstance(date, dict) else None
                try:
                    if uts and int(uts) >= cutoff:
                        plays_24h += 1
                except (TypeError, ValueError):
                    continue
        except Exception:
            pass
        try:
            info = await fetch_user_profile(username)
            if info and 'user' in info:
                total = max(total, int(info['user'].get('playcount', 0)))
        except Exception:
            pass
        try:
            tops = await fetch_top_artists(username, 'overall', 5)
            artists = (tops or {}).get('topartists', {}).get('artist', []) or []
            if artists:
                top_name = artists[0].get('name', 'Unknown')
                top_plays = int(artists[0].get('playcount', 0))
        except Exception:
            pass

    milestone = 10 if total < 10 else 10 ** math.ceil(math.log10(total + 1))
    pct = min(100.0, (total / milestone) * 100) if milestone else 0.0
    filled = max(0, min(10, round(pct / 10)))
    bar = "█" * filled + "░" * (10 - filled)
    share = (top_plays / total * 100) if total > 0 and top_plays else 0.0

    desc = (
        f"⏱️ **Last 24 hours:** {plays_24h:,} plays\n"
        f"🎧 **Total scrobbles:** {total:,}\n"
        f"🎯 **Next milestone:** {milestone:,} ({milestone - total:,} to go)\n"
        f"`{bar}` {pct:.1f}%\n"
    )
    if top_name:
        desc += f"⭐ **Top artist:** {top_name} — {top_plays:,} plays ({share:.1f}% of total)"
    embed = Theme.get_embed(description=desc, color=LASTFM_COLOR)
    embed.set_author(name=f"Listening insights — {format_name(user)}")
    return embed, None

async def process_share(user):
    """Shareable DJ Scratch profile link card."""
    username = await get_lastfm_username(user.id)
    safe_name = urllib.parse.quote(format_name(user).replace(' ', '-'))
    profile_url = f"https://dj-scratch.vercel.app/{safe_name}"

    class ShareLinksView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            self.add_item(discord.ui.Button(label="DJ Scratch Profile", style=discord.ButtonStyle.link, url=profile_url))
            if username:
                self.add_item(discord.ui.Button(label="Last.fm Profile", style=discord.ButtonStyle.link, url=f"https://www.last.fm/user/{username}"))

    desc = (
        f"Send your friends here to see your stats, tops and recents:\n\n"
        f"🔗 {profile_url}"
    )
    embed = Theme.get_embed(description=desc, color=LASTFM_COLOR)
    embed.set_author(name=f"Share {format_name(user)}'s profile")
    return embed, ShareLinksView()
async def process_suggestion(ctx_int, user, suggestion_text, is_bug=False):
    try:
        title = "Bug Report" if is_bug else "Bot Suggestion"
        description = suggestion_text
        
        global db_pool
        if db_pool:
            import asyncpg
            if isinstance(db_pool, asyncpg.pool.Pool):
                async with db_pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO suggestions (user_id, username, title, description) VALUES ($1, $2, $3, $4)",
                        str(user.id), str(format_name(user)), title, description
                    )
            else:
                print(f"DB pool not found or wrong type, skipping DB insert.")

        owner = await bot.fetch_user(OWNER_ID)
        
        embed_title = "🐛 New Bug Report" if is_bug else "💡 New Bot Suggestion"
        embed_color = discord.Color.red() if is_bug else discord.Color.gold()
        
        embed = Theme.get_embed(title=embed_title, description=suggestion_text, color=embed_color)
        embed.set_author(name=f"{format_name(user)} ({user.id})", icon_url=user.display_avatar.url)
        guild_name = ctx_int.guild.name if getattr(ctx_int, 'guild', None) else "DMs / User App"
        embed.set_footer(text=f"Sent from: {guild_name} | Saved to Dashboard")
        view_to_send = BugReportView() if is_bug else SuggestionView()
        await owner.send(embed=embed, view=view_to_send)
        print(f"{Log.GREEN}>>> New {'bug report' if is_bug else 'suggestion'} forwarded to owner & DB.{Log.RESET}")
        
        confirm_text = "✅ Bug report saved to your Dashboard & sent directly to the developer!" if is_bug else "✅ Suggestion saved to your Dashboard & sent directly to the developer!"
        confirm = Theme.get_embed(description=confirm_text, color=discord.Color.green())
        
        if isinstance(ctx_int, discord.Interaction): await ctx_int.response.send_message(embed=confirm, ephemeral=True)
        else: await ctx_int.send(embed=confirm)
    except Exception as e:
        print(f"Suggestion/Bug report error: {e}")
async def process_crowns(guild, user):
    if not guild: return Theme.get_error_embed(description="Must be used in a server."), None
    
    global db_pool
    if not db_pool:
        return Theme.get_error_embed(description="Database not connected."), None
        
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT artist_name FROM server_crowns WHERE guild_id = $1 AND user_id = $2 LIMIT 1", str(guild.id), str(user.id))
        
    if not rows:
        return Theme.get_error_embed(description="You don't hold any seeded crowns in this server! Check with `/whoknows` (or `,whoknows`) on your top artists or ask an admin to run `/crownseeder` (or `,crownseeder`)."), None
        
    view = CrownsPaginator(user, guild, db_pool)
    await view.fetch_crowns()
    return view.generate_embed(), view

async def process_crownseeder(guild, user):
    if not guild: return Theme.get_error_embed(description="Must be used in a server.")
    
    global db_pool
    if not db_pool:
        return Theme.get_error_embed(description="Database not connected.")
        
    users_db = await load_users()
    linked = {uid: lname for uid, lname in users_db.items() if uid in [str(m.id) for m in guild.members]}
    if not linked: return Theme.get_error_embed(description="No one in this server has linked their account.")
    
    artist_plays = {}
    
    for uid, lname in linked.items():
        data = await fetch_top_artists(lname, 'overall', 1000)
        if data and 'topartists' in data and 'artist' in data['topartists']:
            for artist in data['topartists']['artist']:
                name = artist['name']
                plays = int(artist['playcount'])
                if name not in artist_plays:
                    artist_plays[name] = {}
                artist_plays[name][uid] = plays
        await asyncio.sleep(0.5)
        
    new_crowns = []
    for artist, users_plays in artist_plays.items():
        if not users_plays: continue
        top_uid = max(users_plays, key=users_plays.get)
        top_plays = users_plays[top_uid]
        if top_plays >= 30:
            new_crowns.append((str(guild.id), str(top_uid), artist, top_plays))
            
    if not new_crowns:
        embed = Theme.get_embed(description="Seeded 0 crowns. No one has >= 30 plays on any artist.", color=discord.Color.gold())
        embed.set_author(name="Crownseeder", icon_url=guild.icon.url if guild.icon else None)
        return embed
        
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetch("SELECT artist_name, user_id FROM server_crowns WHERE guild_id = $1", str(guild.id))
            existing_map = {r['artist_name']: r['user_id'] for r in existing}
            
            history_inserts = []
            inserts = []
            updates = []
            new_artist_names = set()
            
            for g_id, u_id, artist, plays in new_crowns:
                new_artist_names.add(artist)
                if artist not in existing_map:
                    inserts.append((g_id, u_id, artist, plays))
                else:
                    old_u_id = existing_map[artist]
                    if old_u_id != u_id:
                        history_inserts.append((g_id, artist, old_u_id, u_id, plays))
                        inserts.append((g_id, u_id, artist, plays))
                    else:
                        updates.append((plays, g_id, artist))
                        
            to_delete = [artist for artist in existing_map if artist not in new_artist_names]
            
            if to_delete:
                for chunk in [to_delete[i:i + 100] for i in range(0, len(to_delete), 100)]:
                    await conn.executemany("DELETE FROM server_crowns WHERE guild_id = $1 AND artist_name = $2", [(str(guild.id), a) for a in chunk])
            
            if history_inserts:
                for chunk in [history_inserts[i:i + 100] for i in range(0, len(history_inserts), 100)]:
                    await conn.executemany('''
                        INSERT INTO crown_history (guild_id, artist_name, previous_user_id, new_user_id, plays)
                        VALUES ($1, $2, $3, $4, $5)
                    ''', chunk)
                    
            if inserts:
                for chunk in [inserts[i:i + 100] for i in range(0, len(inserts), 100)]:
                    await conn.executemany('''
                        INSERT INTO server_crowns (guild_id, user_id, artist_name, plays)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (guild_id, artist_name) DO UPDATE 
                        SET user_id = EXCLUDED.user_id, plays = EXCLUDED.plays, claimed_at = CURRENT_TIMESTAMP
                    ''', chunk)
                    
            if updates:
                for chunk in [updates[i:i + 100] for i in range(0, len(updates), 100)]:
                    await conn.executemany('''
                        UPDATE server_crowns SET plays = $1 WHERE guild_id = $2 AND artist_name = $3
                    ''', chunk)
                
    embed = Theme.get_embed(description=f"✅ Seeded **{len(new_crowns)}** crowns for your server.\n\nIf you would like to remove crowns, use:\n- `/killallcrowns` (or `,killallcrowns`)\n- `,killallseededcrowns` (Only seeded crowns)", color=discord.Color.green())
    embed.set_author(name="Crownseeder", icon_url=guild.icon.url if guild.icon else None)
    embed.set_footer(text=f"Crownseeder initiated by {format_name(user)}")
    return embed

async def process_killallcrowns(guild, user):
    if not guild: return Theme.get_error_embed(description="Must be used in a server.")
    
    global db_pool
    if not db_pool:
        return Theme.get_error_embed(description="Database not connected.")
        
    async with db_pool.acquire() as conn:
        deleted = await conn.execute("DELETE FROM server_crowns WHERE guild_id = $1", str(guild.id))
        
    try:
        count = int(deleted.split()[-1])
    except:
        count = 0
        
    embed = Theme.get_embed(description=f"🗑️ Removed **{count}** crowns from the server.", color=discord.Color.red())
    return embed







# ---------------------------------------------------------------------------
# Help menu (redesigned): home page + category dropdown + prev/next navigation.
# Command metadata is curated so every entry shows what it does AND how to use it.
# ---------------------------------------------------------------------------
HELP_COMMAND_META = {
    # Now playing / personal stats
    "fm": ("Now playing / last played track", "/fm [@user] • `,fm [@user]` • `,fm1` compact"),
    "ta": ("Your top artists", "/ta [period] [@user] • `,ta all`"),
    "tt": ("Your top tracks", "/tt [period] [@user] • `,tt 7day`"),
    "rt": ("Your recent tracks", "/rt [@user] • `,rt`"),
    "at": ("Your top tracks for one artist", "/at <artist> • `,at Taylor Swift`"),
    "profile": ("Your listening profile card", "/profile [@user] • `,profile`"),
    "topalbums": ("Your top albums", "/topalbums [period] • `,topalbums`"),
    "chart": ("Album collage chart (3x3–5x5)", "/chart [size] [period] • `,chart 3x3`"),
    "artistchart": ("Artist collage chart", "/artistchart [size] [period]"),
    "taste": ("Compare music taste with someone", "/taste [@user] • `,t`"),
    "live": ("What your friends are playing now", "/live • `,live`"),
    "insights": ("24h plays, milestone & top-artist share", "/insights • `,insights`"),
    "share": ("Shareable link to your profile", "/share • `,share`"),
    "streak": ("Current play streak for an artist", "/streak [artist]"),
    "streakhistory": ("Past streaks (25+ plays)", "/streakhistory"),
    # Server
    "whoknows": ("Who in this server plays an artist most", "/whoknows <artist> • `,wk <artist>`"),
    "whoknowstrack": ("Who plays a specific track most", "/whoknowstrack <track> • `,wkt`"),
    "whoknowsalbum": ("Who plays a specific album most", "/whoknowsalbum <album> • `,wka`"),
    "globalwhoknows": ("Who globally plays an artist most", "/globalwhoknows <artist>"),
    "globalwhoknowstrack": ("Who globally plays a track most", "/globalwhoknowstrack <track>"),
    "globalwhoknowsalbum": ("Who globally plays an album most", "/globalwhoknowsalbum <album>"),
    "crowns": ("Your server crowns (most plays per artist)", "/crowns • `,crowns`"),
    "crownseeder": ("Seed crowns for the server (Admin)", "/crownseeder"),
    "killallcrowns": ("Remove all server crowns (Admin)", "/killallcrowns"),
    "serverartists": ("Server-wide top artists", "/serverartists [period]"),
    "serveralbums": ("Server-wide top albums", "/serveralbums [period]"),
    "servertracks": ("Server-wide top tracks", "/servertracks [period]"),
    # Account
    "login": ("Link your Last.fm account", "/login • `,login`"),
    "logout": ("Unlink Last.fm or Spotify", "/logout [spotify] • `,logout spotify`"),
    "privacy": ("Toggle private mode", "/privacy"),
    "import": ("Import Spotify / Apple Music history", "/import + attach ZIP"),
    "settings": ("Your display + /fm settings", "/settings"),
    "cd": ("Check bot-avatar cooldown + preview", "`,cd`"),
    "cd2": ("Preview avatar from last (not current) song", "`,cd2`"),
    # Fun / utility
    "guess": ("Guess-the-song game", "/guess"),
    "scramble": ("Unscramble the artist/track", "/scramble"),
    "judge": ("AI roasts your music taste", "/judge"),
    "receipt": ("Top-tracks receipt image", "/receipt"),
    "server": ("Server info card", "/server"),
    "status": ("Bot uptime / ping / stats", "/status"),
    "updates": ("Latest bot updates", "/updates"),
    "guide": ("Quick-start guide", "/guide"),
    "premium": ("Premium preview (coming soon)", "/premium"),
    "dms": ("Friend DMs inbox", "/dms"),
    "remote": ("Spotify remote panel with live controls", "`,rc` • `,rc disconnect` • `,prev` • `,rq`"),
    "previous": ("Previous Spotify track", "`,prev`"),
    "spotify": ("Spotify link for current track or search", "`,sp [query]`"),
    "spotifyalbum": ("Spotify link for an album", "`,spab [album]`"),
    "spotifyartist": ("Spotify link for an artist", "`,spa [artist]`"),
    "social": ("Friends & social commands", "/social"),
    "deletedata": ("Delete your imported data", "/deletedata"),
    "suggest": ("Send an idea to the dev", "/suggest <idea> • `,suggest`"),
    "bug": ("Report a bug to the dev", "/bug <what happened>"),
}

HELP_CATEGORIES = {
    "home": {
        "label": "🏠 Home", "emoji": "🏠", "title": "DJ Scratch — Help",
        "tagline": "Your Last.fm + Spotify stats bot. Pick a category below or flip pages.",
        "commands": [],
    },
    "nowplaying": {
        "label": "🎧 Now Playing & Stats", "emoji": "🎧", "title": "🎧 Now Playing & Stats",
        "tagline": "What you're spinning right now and your personal stats.",
        "commands": ["fm", "profile", "taste", "live", "insights", "share", "streak", "streakhistory", "chart", "artistchart"],
    },
    "tops": {
        "label": "📊 Tops & History", "emoji": "📊", "title": "📊 Tops & History",
        "tagline": "Top artists, tracks, albums and recent history. Periods: `7day 1month 3month 6month 12month overall` (or `,ta 7d`).",
        "commands": ["ta", "tt", "topalbums", "rt", "at", "serverartists", "serveralbums", "servertracks"],
    },
    "server": {
        "label": "👑 Server & Crowns", "emoji": "👑", "title": "👑 Server & Crowns",
        "tagline": "Battle your server for crowns and whoknows titles.",
        "commands": ["whoknows", "whoknowstrack", "whoknowsalbum", "globalwhoknows", "globalwhoknowstrack", "globalwhoknowsalbum", "crowns", "crownseeder", "killallcrowns"],
    },
    "account": {
        "label": "🔗 Account & Setup", "emoji": "🔗", "title": "🔗 Account & Setup",
        "tagline": "Link Last.fm, import history, and tune your settings.",
        "commands": ["login", "logout", "import", "settings", "privacy", "cd", "cd2"],
    },
    "fun": {
        "label": "🎮 Fun & Utility", "emoji": "🎮", "title": "🎮 Fun & Utility",
        "tagline": "Games, AI, and handy extras.",
        "commands": ["remote", "previous", "spotify", "spotifyalbum", "spotifyartist", "guess", "scramble", "judge", "receipt", "server", "status", "updates", "guide", "premium", "dms", "social", "deletedata", "suggest", "bug"],
    },
}

_HELP_AUTO_CACHE: dict = {}  # bot_id -> (cmd_info_dict, expires)


def _get_auto_cmd_info(bot):
    """Fallback descriptions from registered slash/prefix commands (cached 10 min)."""
    import time as _t
    bid = str(getattr(getattr(bot, 'user', None), 'id', 'nobot'))
    e = _HELP_AUTO_CACHE.get(bid)
    if e and e[1] > _t.monotonic():
        return e[0]
    info = {}
    try:
        slash_map = {c.name: c for c in bot.tree.get_commands()
                     if not isinstance(c, discord.app_commands.ContextMenu)}
    except Exception:
        slash_map = {}
    try:
        for cmd in bot.commands:
            s = slash_map.get(cmd.name)
            desc = (s.description if s else None) or (cmd.help or "No description.")
            # e.g. "/fm • also `,np`, `,n`"
            aliases = (" • also " + ", ".join(f"`,{a}`" for a in cmd.aliases)) if cmd.aliases else ""
            info[cmd.name] = (desc, f"/{cmd.name}{aliases}")
    except Exception:
        pass
    try:
        for name, s in slash_map.items():
            if name not in info and not isinstance(s, discord.app_commands.Group):
                info[name] = (s.description or "No description.", f"/{name}")
    except Exception:
        pass
    _HELP_AUTO_CACHE[bid] = (info, _t.monotonic() + 600)
    return info


class HelpCategorySelect(discord.ui.Select):
    def __init__(self, view_ref):
        self.view_ref = view_ref
        options = [
            discord.SelectOption(label=v["label"].replace(f"{v['emoji']} ", ""), emoji=v["emoji"],
                                 value=key, description=v["tagline"][:100])
            for key, v in HELP_CATEGORIES.items()
            if not v.get("admin")
        ]
        if view_ref.is_admin:
            options.append(discord.SelectOption(label="Admin", emoji="🔒", value="admin",
                                                description="Restricted commands"))
        super().__init__(placeholder="Jump to a category…", min_values=1, max_values=1,
                         options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        v = self.view_ref
        if interaction.user.id != v.user.id:
            return await interaction.response.send_message("This menu isn't for you — run `,help` yourself!", ephemeral=True)
        v.current_page = v.page_keys.index(self.values[0])
        v.sync_controls()
        await interaction.response.edit_message(embed=v.pages[v.current_page], view=v)


class HelpPaginationView(discord.ui.View):
    def __init__(self, user, bot, is_admin=False):
        super().__init__(timeout=180)
        self.user = user
        self.bot = bot
        self.is_admin = is_admin
        self.page_keys = list(HELP_CATEGORIES.keys()) + (["admin"] if is_admin else [])
        self.pages = self.build_pages()
        self.current_page = 0
        self.select = HelpCategorySelect(self)
        self.add_item(self.select)
        self.sync_controls()

    def _cmd_field(self, name, auto_info):
        meta = HELP_COMMAND_META.get(name)
        if meta:
            desc, usage = meta
        elif name in auto_info:
            desc, usage = auto_info[name]
        else:
            desc, usage = "No description.", f"/{name}"
        # Field name shows slash + prefix; value shows what + how.
        return (f"`/{name}`", f"{desc}\n﹒{usage}")

    def build_pages(self):
        from src.core.theme import Theme
        import discord

        auto_info = _get_auto_cmd_info(self.bot)
        try:
            avatar = self.bot.user.display_avatar.url if self.bot.user else None
        except Exception:
            avatar = None
        pages = []

        for key in self.page_keys:
            if key == "admin":
                embed = Theme.get_embed(user=self.user, title="🔒 Admin Commands",
                                        description="*Restricted — you can see this because you're staff.*")
                if avatar:
                    embed.set_thumbnail(url=avatar)
                for cmd_name in ["wipedata", "cleanduplicates", "stats", "restart", "sync", "resetcd"]:
                    if cmd_name in auto_info:
                        desc, usage = auto_info[cmd_name]
                    else:
                        desc, usage = "Restricted admin command.", f"/{cmd_name}"
                    embed.add_field(name=f"`/{cmd_name}`", value=f"{desc}\n﹒{usage}", inline=False)
                embed.set_footer(text=f"Page {len(pages)+1} of {len(self.page_keys)} • ,help • Only you can use these buttons")
                pages.append(embed)
                continue

            cat = HELP_CATEGORIES[key]
            if key == "home":
                embed = Theme.get_embed(
                    user=self.user,
                    title="🎧 DJ Scratch — Help",
                    description=(
                        "Track your **Last.fm + Spotify** listening, battle your server for **crowns**, "
                        "and flex your stats.\n\n"
                        "**🚀 Get started in 30 seconds**\n"
                        "**1.** `/login` — link your Last.fm\n"
                        "**2.** Play music on Spotify / Apple Music (connected to Last.fm)\n"
                        "**3.** `,fm` or `/fm` — show what's playing\n\n"
                        "**📚 Categories** — use the dropdown above or ⬅️ ➡️ to browse:\n"
                        + "\n".join(f"{v['emoji']} **{v['label'].replace(v['emoji']+' ', '')}** — {v['tagline'][:80]}"
                                    for k, v in HELP_CATEGORIES.items() if k != "home")
                        + "\n\n*Tip: most commands work as both `/slash` and `,prefix` (e.g. `/fm` = `,fm`).*"
                    ),
                )
                if avatar:
                    embed.set_thumbnail(url=avatar)
                embed.set_footer(text=f"Page 1 of {len(self.page_keys)} • ,help or /help • Pick a category above 👆")
                pages.append(embed)
                continue

            embed = Theme.get_embed(user=self.user, title=cat["title"], description=f"*{cat['tagline']}*")
            if avatar:
                embed.set_thumbnail(url=avatar)
            for cmd_name in cat["commands"]:
                fname, fvalue = self._cmd_field(cmd_name, auto_info)
                embed.add_field(name=fname, value=fvalue, inline=True)
            embed.set_footer(text=f"Page {len(pages)+1} of {len(self.page_keys)} • ,help or /help • Use the dropdown to jump 👆")
            pages.append(embed)

        return pages

    def sync_controls(self):
        self.prev_btn.disabled = self.current_page <= 0
        self.next_btn.disabled = self.current_page >= len(self.pages) - 1
        self.home_btn.disabled = self.current_page == 0
        # Keep dropdown in sync
        try:
            for opt in self.select.options:
                opt.default = (opt.value == self.page_keys[self.current_page])
        except Exception:
            pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            try:
                await interaction.response.send_message("This menu isn't for you — run `,help` yourself!", ephemeral=True)
            except Exception:
                pass
            return False
        return True

    async def on_timeout(self):
        try:
            for child in self.children:
                child.disabled = True
        except Exception:
            pass

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        self.sync_controls()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(emoji="🏠", style=discord.ButtonStyle.primary, row=1)
    async def home_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = 0
        self.sync_controls()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.secondary, row=1)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        self.sync_controls()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

async def get_help_embed(user, bot):
    from src.core.theme import Theme
    from src.core.database import has_any_command_permission
    from src.core.config import OWNER_ID

    try:
        is_admin = user.id == OWNER_ID or await has_any_command_permission(str(user.id))
    except Exception:
        is_admin = False

    view = HelpPaginationView(user, bot, is_admin)
    embed = view.pages[0]
    return embed, view
# --- ADMIN COMMAND ---



# --- SLASH COMMANDS ---











# --- PREFIX COMMAND ---






















class PurgeConfirmView(discord.ui.View):
    def __init__(self, user):
        super().__init__(timeout=30)
        self.user = user
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("❌ This confirmation is not for you!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.confirmed = True
        self.stop()
        
        deleted_count = 0
        if db_pool:
            try:
                async with db_pool.acquire() as conn:
                    row = await conn.fetchrow("SELECT COUNT(*) FROM listens WHERE user_id=$1", str(self.user.id))
                    deleted_count = row[0] if row else 0
                    await conn.execute("DELETE FROM listens WHERE user_id=$1", str(self.user.id))
                    await conn.execute("DELETE FROM imported_users WHERE id=$1", str(self.user.id))
            except Exception as e:
                print(f"{Log.RED}>>> Error purging user data from DB: {e}{Log.RESET}")
        
        embed = Theme.get_embed(
            title="🗑️ Data Successfully Deleted",
            description=(
                f"Your data has been fully purged from the database:\n\n"
                f"• **{deleted_count:,}** imported listens deleted.\n\n"
                f"All your data has been completely and permanently erased!"
            ),
            color=discord.Color.red(),

        )
        await interaction.edit_original_response(embed=embed, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        embed = Theme.get_embed(
            description="❌ **Purge cancelled.** Your data remains completely safe.",
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(embed=embed, view=None)




@app_commands.describe(layout="Choose your default layout")
@app_commands.choices(layout=[
    app_commands.Choice(name="Compact Text (fm1)", value="compact"),
    app_commands.Choice(name="Full Embed (fm2)", value="full"),
])
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def set_custom_fm_slash(interaction: discord.Interaction, layout: app_commands.Choice[str]):
    print(f"[/setcustomfm] Triggered by {format_name(interaction.user)}")
    if not db_pool:
        await interaction.response.send_message("❌ Database is currently offline.", ephemeral=True)
        return

    success = await set_user_fm_mode(interaction.user.id, layout.value)
    if success:
        display = "Compact Text (fm1)" if layout.value == "compact" else "Full Embed (fm2)"
        await interaction.response.send_message(f"✅ Your default `/fm` response is now set to **{display}**!", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Failed to save setting to the database.", ephemeral=True)




# --- AUTO-TRIGGER & REACTIONS ---
@bot.event
async def on_message(message):
    if message.author == bot.user: return
    
    import re
    import asyncio
    if not message.author.bot and re.match(r'^\.[a-zA-Z]', message.content):
        async def delayed_delete():
            await asyncio.sleep(5)  # 5-second delay
            try:
                await message.delete()
            except discord.Forbidden:
                print(f"Failed to auto-delete message from {message.author}: Missing permissions")
            except discord.HTTPException as e:
                print(f"Failed to auto-delete message from {message.author}: HTTP Exception {e}")
                
        asyncio.create_task(delayed_delete())
            
    content_lower = message.content.lower()
    is_stats_bot = (message.author.name == "stats.fm")
    has_phrase = ("is currently listening to" in content_lower)

    if is_stats_bot or has_phrase:
        await add_custom_reactions(message)

    await bot.process_commands(message)

async def process_receipt(user, period='overall', limit=10):
    from ..utils.images import generate_receipt_image
    from ..utils.api import fetch_top_tracks
    import discord
    
    username = await get_lastfm_username(user.id)
    if not username:
        return Theme.get_error_embed(description=f"You have not linked your Last.fm account! Use `/login` to link it."), None, None
        
    data = await fetch_top_tracks(username, period, limit)
    if not data or 'toptracks' not in data or not data['toptracks']['track']:
        return Theme.get_error_embed(description="Could not fetch top tracks for the receipt."), None, None
        
    tracks_raw = data['toptracks']['track']
    tracks = []
    for t in tracks_raw:
        tracks.append((t['name'], t['artist']['name'], int(t['playcount'])))
        
    buf = generate_receipt_image(username, period, tracks)
    file = discord.File(buf, filename="receipt.png")
    
    embed = Theme.get_embed(title=f"🧾 {format_name(user)}'s Top Tracks Receipt", color=LASTFM_COLOR)
    embed.set_image(url="attachment://receipt.png")
    
    return embed, file, None
# --- UPDATE NOTIFICATIONS ---
CACHED_GLOBAL_UPDATE_VERSION = None
CACHED_GLOBAL_UPDATE_MESSAGE = None

async def check_update_notification(user_id: int, send_message_func):
    try:
        from src.core.database import get_global_update_version, get_user_bundle, set_user_last_update_seen
        global CACHED_GLOBAL_UPDATE_VERSION

        if CACHED_GLOBAL_UPDATE_VERSION is None:
            CACHED_GLOBAL_UPDATE_VERSION = await get_global_update_version()

        current_version = CACHED_GLOBAL_UPDATE_VERSION
        if not current_version:
            return

        # ONE cached read instead of 2-3 DB round-trips per command.
        bundle = await get_user_bundle(user_id)
        if not bundle.get('update_notifs', True):
            return

        last_seen = bundle.get('last_update_seen', '') or ''
        if last_seen != current_version:
            await set_user_last_update_seen(user_id, current_version)
            await send_message_func()
    except Exception as e:
        print(f"Silently caught error in update notification check: {e}")

class DismissUpdateView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=60.0)
        self.user_id = user_id

    @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.secondary, emoji="🗑️")
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id == self.user_id:
            try:
                await interaction.message.delete()
            except:
                pass
        else:
            await interaction.response.send_message("Only the person who triggered this update can dismiss it.", ephemeral=True)

@bot.listen('on_command_completion')
async def update_notif_prefix(ctx):
    from src.core.database import get_global_update_message
    async def send_msg():
        global CACHED_GLOBAL_UPDATE_MESSAGE
        try:
            if CACHED_GLOBAL_UPDATE_MESSAGE is None:
                CACHED_GLOBAL_UPDATE_MESSAGE = await get_global_update_message()
            msg = CACHED_GLOBAL_UPDATE_MESSAGE
            version = CACHED_GLOBAL_UPDATE_VERSION if 'CACHED_GLOBAL_UPDATE_VERSION' in globals() else ""
            
            embed = Theme.get_embed(
                title=f"🎉 DJ Scratch Update `{version}`",
                description=msg, 
                color=0x10b981
            )
            embed.set_footer(text="You can disable these update notifications in /settings")
            view = DismissUpdateView(ctx.author.id)
            await ctx.send(f"<@{ctx.author.id}>", embed=embed, view=view, delete_after=60.0)
        except Exception:
            pass
    await check_update_notification(ctx.author.id, send_msg)

@bot.listen('on_app_command_completion')
async def update_notif_slash(interaction, command):
    from src.core.database import get_global_update_message
    async def send_msg():
        global CACHED_GLOBAL_UPDATE_MESSAGE
        try:
            if CACHED_GLOBAL_UPDATE_MESSAGE is None:
                CACHED_GLOBAL_UPDATE_MESSAGE = await get_global_update_message()
            msg = CACHED_GLOBAL_UPDATE_MESSAGE
            version = CACHED_GLOBAL_UPDATE_VERSION if 'CACHED_GLOBAL_UPDATE_VERSION' in globals() else ""
            
            embed = Theme.get_embed(
                title=f"🎉 DJ Scratch Update `{version}`",
                description=msg, 
                color=0x10b981
            )
            embed.set_footer(text="You can disable these update notifications in /settings")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            pass
    await check_update_notification(interaction.user.id, send_msg)

class DirectMessageReplyModal(discord.ui.Modal, title="Reply via DM"):
    reply_content = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.long,
        placeholder="Type your message here...",
        required=True
    )

    def __init__(self, target_id: str):
        super().__init__()
        self.target_id = target_id

    async def on_submit(self, interaction: discord.Interaction):
        sender_id = str(interaction.user.id)
        content = self.reply_content.value
        
        global db_pool
        pool = db_pool
        if not pool:
            await interaction.response.send_message("Database connection error.", ephemeral=True)
            return

        async with pool.acquire() as conn:
            # Check if they are friends
            is_friend = await conn.fetchval(
                "SELECT status FROM friends WHERE user_id = $1 AND friend_id = $2 AND status = 'accepted'",
                sender_id, self.target_id
            )
            if not is_friend:
                await interaction.response.send_message("You must be friends to send messages.", ephemeral=True)
                return

            await conn.execute(
                "INSERT INTO direct_messages (sender_id, receiver_id, content) VALUES ($1, $2, $3)",
                sender_id, self.target_id, content
            )
            
            try:
                target_user = await bot.fetch_user(int(self.target_id))
                sender_name = await conn.fetchval("SELECT display_name FROM user_settings WHERE user_id = $1", sender_id)
                if not sender_name:
                    sender_name = interaction.user.name
                    
                view = discord.ui.View()
                btn = discord.ui.Button(label="Open Web Dashboard", style=discord.ButtonStyle.link, url="https://the-goats-dj.vercel.app/messages")
                view.add_item(btn)
                
                embed = Theme.get_embed(
                    title="💬 New Direct Message",
                    description=content,
                    color=discord.Color.blurple(),
                    timestamp=discord.utils.utcnow()
                )
                embed.set_author(
                    name=sender_name,
                    icon_url=interaction.user.display_avatar.url
                )
                embed.set_footer(text="DJ Scratch • Activity DM")
                
                await target_user.send("*(To reply, launch the DJ Scratch Activity using the 🚀 icon below, or click the button)*", embed=embed, view=view)
                await interaction.response.send_message("Reply sent successfully!", ephemeral=True)
            except Exception as e:
                print(e)
                await interaction.response.send_message("Reply saved, but failed to DM the user on Discord.", ephemeral=True)

@bot.listen('on_interaction')
async def on_interaction(interaction: discord.Interaction):
    from .database import db_pool
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO user_settings (user_id, discord_username, display_name) 
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id) DO UPDATE SET 
                        discord_username = EXCLUDED.discord_username,
                        display_name = EXCLUDED.display_name
                """, str(interaction.user.id), interaction.user.name, interaction.user.display_name)
        except Exception:
            pass

    if interaction.type == discord.InteractionType.component:
        custom_id = interaction.data.get("custom_id", "")
        
        if custom_id.startswith("accept_friend_"):
            sender_id = custom_id.replace("accept_friend_", "")
            receiver_id = str(interaction.user.id)
            
            pool = db_pool
            if not pool:
                await interaction.response.send_message("Database connection error.", ephemeral=True)
                return
                
            async with pool.acquire() as conn:
                existing = await conn.fetchval(
                    "SELECT status FROM friends WHERE user_id = $1 AND friend_id = $2",
                    sender_id, receiver_id
                )
                if existing == 'pending':
                    await conn.execute("UPDATE friends SET status='accepted' WHERE user_id=$1 AND friend_id=$2", sender_id, receiver_id)
                    await conn.execute(
                        "INSERT INTO friends (user_id, friend_id, status) VALUES ($1, $2, 'accepted') ON CONFLICT (user_id, friend_id) DO UPDATE SET status='accepted'",
                        receiver_id, sender_id
                    )
                    
                    try:
                        await interaction.response.edit_message(content=f"✅ You accepted the friend request from <@{sender_id}>!", view=None)
                    except:
                        await interaction.response.send_message(f"✅ You accepted the friend request from <@{sender_id}>!", ephemeral=True)
                        
                    try:
                        sender_user = await bot.fetch_user(int(sender_id))
                        await sender_user.send(f"**{interaction.user.name}** accepted your friend request on DJ Scratch!")
                    except:
                        pass
                else:
                    await interaction.response.send_message("This friend request is no longer valid or already accepted.", ephemeral=True)
                    
        elif custom_id.startswith("reply_dm_"):
            target_id = custom_id.replace("reply_dm_", "")
            await interaction.response.send_modal(DirectMessageReplyModal(target_id=target_id))
            
        elif custom_id.startswith("spotify_"):
            parts = custom_id.split(":")
            if len(parts) == 2:
                action, owner_id = parts[0], parts[1]
                if str(interaction.user.id) != owner_id:
                    await interaction.response.send_message("This is not your remote!", ephemeral=True)
                    return
                
                await interaction.response.defer()
                app_url = os.getenv("NEXT_PUBLIC_APP_URL", "https://dj-scratch.vercel.app")
                
                from src.core.spotify import (
                    spotify_skip_to_previous, spotify_pause_playback,
                    spotify_play_track, spotify_skip_to_next,
                    spotify_like_track, get_currently_playing_track,
                    get_spotify_queue, is_track_liked
                )
                
                async with aiohttp.ClientSession() as session:
                    res = False
                    if action == "spotify_prev":
                        res = await spotify_skip_to_previous(session, owner_id)
                    elif action == "spotify_pause":
                        res = await spotify_pause_playback(session, owner_id)
                        if res is not True:
                            res = await spotify_play_track(session, owner_id)
                    elif action == "spotify_next":
                        res = await spotify_skip_to_next(session, owner_id)
                    elif action == "spotify_like":
                        track = await get_currently_playing_track(session, owner_id)
                        if track and track != "no_token":
                            res = await spotify_like_track(session, owner_id, track['id'])
                    elif action == "spotify_repeat":
                        res = True
                    elif action == "spotify_refresh":
                        res = True  # fall through to re-fetch + re-render below
                        
                    if res == "no_token":
                        return await interaction.followup.send(f"You need to link your Spotify account first! [Connect here]({app_url}/api/auth/spotify?user_id={interaction.user.id})", ephemeral=True)
                    elif res is not True:
                        return await interaction.followup.send(_pretty_spotify_error(res), ephemeral=True)
                        
                    await asyncio.sleep(1)
                    
                    track = await get_currently_playing_track(session, owner_id)
                    if track and track != "no_token":
                        action_label = "Now playing"
                        if action == "spotify_pause": action_label = "Paused"
                        elif action == "spotify_prev": action_label = "Previous"
                        elif action == "spotify_next": action_label = "Skipped"
                        elif action == "spotify_like": action_label = "Liked"
                        elif action == "spotify_refresh": action_label = "Refreshed"

                        queue, liked = await asyncio.gather(
                            get_spotify_queue(session, owner_id),
                            is_track_liked(session, owner_id, track.get("id")),
                        )
                        if queue == "no_token":
                            queue = []

                        from src.commands.spotify_remote import get_spotify_remote_layout, _pretty_spotify_error
                        view = get_spotify_remote_layout(track, owner_id, action_label, queue=queue, liked=liked)
                        await interaction.message.edit(embeds=[], view=view)
                        
        elif custom_id.startswith("fm_up:") or custom_id.startswith("fm_down:"):
            parts = custom_id.split(":")
            if len(parts) >= 3:
                action = parts[0]
                user_id_str = parts[1]
                current_mode = parts[2]
                unique_id = parts[3] if len(parts) > 3 else None
                
                try:
                    target_user = await bot.fetch_user(int(user_id_str))
                except:
                    target_user = interaction.user
                    
                new_mode = "full"
                if action == "fm_up":
                    new_mode = "full" if current_mode == "stats" else "compact"
                else:
                    new_mode = "full" if current_mode == "compact" else "stats"
                    
                await interaction.response.defer()
                cached_data = FM_TRACK_CACHE.get(unique_id) if unique_id else None
                if not cached_data:
                    if not interaction.response.is_done():
                        await interaction.followup.send("⚠️ This message is too old to interact with (the bot restarted or cache cleared). Please run `,fm` again!", ephemeral=True)
                    return
                result, _ = await process_fm(interaction, target_user, mode=new_mode, track_data=cached_data)
                if result:
                    content = result.get('content')
                    if not interaction.response.is_done():
                        await interaction.response.edit_message(content=content, embed=result.get('embed'), view=result.get('view'))
                    else:
                        await interaction.edit_original_response(content=content, embed=result.get('embed'), view=result.get('view'))
                        
        elif custom_id.startswith("fm_lyrics:"):
            parts = custom_id.split(":")
            if len(parts) >= 3:
                artist = parts[1]
                song = ":".join(parts[2:])
                
                await interaction.response.defer(ephemeral=True)
                from src.core.lyrics import fetch_lyrics
                session = getattr(bot, 'session', None)
                if not session:
                    session = aiohttp.ClientSession()
                    bot.session = session
                lyrics_data = await fetch_lyrics(session, artist, song)
                if lyrics_data and lyrics_data.get("plain"):
                    desc = lyrics_data.get("plain")
                    if len(desc) > 4096:
                        desc = desc[:4093] + "..."
                    embed = Theme.get_embed(title=f"Lyrics for {song} by {artist}", description=desc, color=Theme.PRIMARY)
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.followup.send("Could not find lyrics for this track.", ephemeral=True)
                    
        elif custom_id.startswith("fm_preview:"):
            parts = custom_id.split(":")
            if len(parts) >= 2:
                # Handle old format (fm_preview:artist), new format (fm_preview:user_id:artist), and newest (fm_preview:user_id:unique_id:artist)
                unique_id = None
                if len(parts) >= 4 and parts[1].isdigit() and len(parts[2]) == 8:
                    target_user_id = parts[1]
                    unique_id = parts[2]
                    artist = ":".join(parts[3:])
                elif len(parts) >= 3 and parts[1].isdigit():
                    target_user_id = parts[1]
                    artist = ":".join(parts[2:])
                else:
                    target_user_id = None
                    artist = ":".join(parts[1:])
                    
                target_user = None
                if target_user_id:
                    target_user = bot.get_user(int(target_user_id))
                    if not target_user:
                        try:
                            target_user = await bot.fetch_user(int(target_user_id))
                        except:
                            pass
                if not target_user:
                    target_user = interaction.user

                img_url = None
                if interaction.message.embeds and len(interaction.message.embeds) > 0:
                    embed = interaction.message.embeds[0]
                    if embed.thumbnail and embed.thumbnail.url:
                        img_url = embed.thumbnail.url
                    elif embed.image and embed.image.url:
                        img_url = embed.image.url
                        
                if not img_url:
                    await interaction.response.send_message(f"Please re-run the `/fm` command to preview the avatar for **{artist}** (Image not found).", ephemeral=True)
                    return
                
                await interaction.response.defer(ephemeral=True)
                
                preview_embed = Theme.get_embed(
                    title="Bot Avatar Preview", 
                    description=f"This is how the bot will look if you apply the album art for **{artist}**.", 
                    color=LASTFM_COLOR
                )
                from src.core.database import format_name
                preview_embed.set_author(name=format_name(target_user), icon_url=img_url)
                
                from src.utils.images import get_circular_pfp_file
                pfp_file = await get_circular_pfp_file(img_url)
                
                track_data = None
                if unique_id:
                    track_data = FM_TRACK_CACHE.get(unique_id)
                
                apply_view = ApplyAvatarView(bot, artist, img_url, original_msg=interaction.message, original_user=target_user, track=None, track_data=track_data)
                
                if pfp_file:
                    preview_embed.set_image(url="attachment://pfp_preview.png")
                    await interaction.followup.send(file=pfp_file, embed=preview_embed, view=apply_view, ephemeral=True)
                else:
                    preview_embed.set_image(url=img_url)
                    await interaction.followup.send(embed=preview_embed, view=apply_view, ephemeral=True)

async def process_chart(user, target_user, size: str = '3x3', period: str = 'overall'):
    import re
    from src.core.database import get_local_total_plays
    
    # Parse size
    match = re.match(r'^(\d+)x(\d+)$', size.lower())
    if not match:
        return Theme.get_error_embed(description="Invalid size. Try `3x3`, `5x5`, etc. Max is `10x10`."), None
        
    cols, rows = int(match.group(1)), int(match.group(2))
    if cols * rows > 100 or cols > 10 or rows > 10:
        return Theme.get_error_embed(description="Grid is too large! Maximum is `10x10`."), None
        
    target_uid = target_user.id if target_user else user.id
    username = await get_lastfm_username(target_uid)
    
    if not username and (await get_local_total_plays(target_uid)) == 0:
        return Theme.get_error_embed(description=f"{format_name(target_user or user)} has not linked their Last.fm account or imported data."), None
        
    limit = cols * rows
    
    status_embed = Theme.get_embed(description=f"🎨 Generating {size} album chart for **{format_name(target_user or user)}**... Please wait.")
    
    async def generate_chart_task():
        try:
            albums = await get_combined_top_albums(target_uid, username, limit, period)
            if not albums:
                return Theme.get_error_embed(description="No albums found for this period."), None, None
                
            from src.utils.image_generator import generate_chart
            
            items = []
            for a in albums:
                items.append({
                    'image_url': a.get('image'),
                    'fallback_artist': a['artist'],
                    'fallback_album': a['name'],
                    'primary_text': a['name'],
                    'secondary_text': f"{a['artist']} • {a['plays']} plays"
                })
                
            buffer = await generate_chart(items, cols, rows, show_text=True)
            
            file = discord.File(fp=buffer, filename="chart.jpg")
            embed = Theme.get_embed(color=LASTFM_COLOR)
            embed.set_author(name=f"{format_name(target_user or user)}'s {period} top albums", icon_url=(target_user or user).display_avatar.url)
            embed.set_image(url="attachment://chart.jpg")
            
            return embed, file, None
        except Exception as e:
            import traceback
            trace = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            err_embed = Theme.get_error_embed(description=f"An internal error occurred while generating the chart:\n```py\n{trace[-1000:]}\n```")
            return err_embed, None, None
        
    return status_embed, generate_chart_task

async def process_artist_chart(user, target_user, size: str = '3x3', period: str = 'overall'):
    import re
    from src.core.database import get_local_total_plays
    
    # Parse size
    match = re.match(r'^(\d+)x(\d+)$', size.lower())
    if not match:
        return Theme.get_error_embed(description="Invalid size. Try `3x3`, `5x5`, etc. Max is `10x10`."), None
        
    cols, rows = int(match.group(1)), int(match.group(2))
    if cols * rows > 100 or cols > 10 or rows > 10:
        return Theme.get_error_embed(description="Grid is too large! Maximum is `10x10`."), None
        
    target_uid = target_user.id if target_user else user.id
    username = await get_lastfm_username(target_uid)
    
    if not username and (await get_local_total_plays(target_uid)) == 0:
        return Theme.get_error_embed(description=f"{format_name(target_user or user)} has not linked their Last.fm account or imported data."), None
        
    limit = cols * rows
    
    status_embed = Theme.get_embed(description=f"🎨 Generating {size} artist chart for **{format_name(target_user or user)}**... Please wait.")
    
    async def generate_chart_task():
        artists = await get_combined_top_artists(target_uid, username, limit)
        if not artists:
            return Theme.get_error_embed(description="No artists found for this period."), None, None
            
        from src.utils.image_generator import generate_chart
        
        items = []
        for a in artists:
            items.append({
                'image_url': None, # Fallback, no Last.fm artist images available currently without another API
                'primary_text': a['name'],
                'secondary_text': f"{a['plays']} plays"
            })
            
        buffer = await generate_chart(items, cols, rows, show_text=True)
        
        file = discord.File(fp=buffer, filename="artist_chart.jpg")
        embed = Theme.get_embed(color=LASTFM_COLOR)
        embed.set_author(name=f"{format_name(target_user or user)}'s {period} top artists", icon_url=(target_user or user).display_avatar.url)
        embed.set_image(url="attachment://artist_chart.jpg")
        
        return embed, file, None
        
    return status_embed, generate_chart_task

async def process_streak(user, query: str = None):
    from src.core.database import get_streak, get_user_data_source, format_name
    from src.utils.api import fetch_recent_tracks, fetch_now_playing
    
    username = await get_lastfm_username(user.id)
    d_source = await get_user_data_source(user.id)
    
    artist_name = None
    if not query:
        if not username:
            return Theme.get_error_embed(description="Link account or provide an artist name."), None
        np_data = await fetch_now_playing(username, 1)
        try:
            artist_name = np_data['recenttracks']['track'][0]['artist']['#text']
        except:
            return Theme.get_error_embed(description="You aren't playing anything right now!"), None
    else:
        artist_name = query

    streak = 0
    if d_source != 'lastfm_only':
        streak = await get_streak(str(user.id), artist_name)
    
    if d_source != 'imported_only' and username:
        # Check API streak
        api_streak = 0
        page = 1
        limit = 200
        found_break = False
        
        while not found_break:
            data = await fetch_recent_tracks(username, limit, page)
            if not data or 'recenttracks' not in data or not data['recenttracks']['track']:
                break
                
            tracks = data['recenttracks']['track']
            for t in tracks:
                if t['artist']['#text'].lower() == artist_name.lower():
                    api_streak += 1
                else:
                    found_break = True
                    break
                    
            if len(tracks) < limit:
                break
            page += 1
            
        streak = max(streak, api_streak)
        
    if streak == 0:
        embed = Theme.get_embed(description="No active streak found.\nTry scrobbling multiple of the same artist, album, track or genre in a row to get started.", color=LASTFM_COLOR)
        embed.set_author(name=f"Streak overview for {format_name(user)}", icon_url=user.display_avatar.url)
        return embed, None
        
    desc = f"`Artist:` **{artist_name}** - {streak} plays\n\nOnly streaks with 25 plays or higher are saved."
    embed = Theme.get_embed(description=desc, color=LASTFM_COLOR)
    embed.set_author(name=f"Streak overview for {format_name(user)}", icon_url=user.display_avatar.url)
    return embed, None


class StreakHistoryPaginator(discord.ui.View):
    def __init__(self, user, history):
        super().__init__(timeout=60.0)
        self.user = user
        self.history = history
        self.current_page = 0
        self.items_per_page = 15
        self.max_pages = max(1, (len(history) + self.items_per_page - 1) // self.items_per_page)
        
        self.first_button.disabled = True
        self.prev_button.disabled = True
        self.next_button.disabled = self.max_pages <= 1
        self.last_button.disabled = self.max_pages <= 1

    def generate_embed(self):
        from src.core.database import format_name
        
        start = self.current_page * self.items_per_page
        end = start + self.items_per_page
        page_items = self.history[start:end]
        
        desc = ""
        for i, streak in enumerate(page_items, start=start+1):
            artist = streak['artist_name']
            count = streak['streak_length']
            s_time = streak['started_at'].strftime("%B %d, %Y %I:%M %p") if streak['started_at'] else "Unknown"
            
            desc += f"`{i}` **{artist}** - **{count}** plays\n└ *Started: {s_time}*\n"
            
        if not desc:
            desc = "No streak history found."
            
        embed = Theme.get_embed(description=desc, color=LASTFM_COLOR)
        embed.set_author(name=f"{format_name(self.user)}'s Streak History", icon_url=self.user.display_avatar.url)
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.max_pages} • Total: {len(self.history)}")
        return embed

    async def update_message(self, interaction: discord.Interaction):
        self.first_button.disabled = self.current_page == 0
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.max_pages - 1
        self.last_button.disabled = self.current_page >= self.max_pages - 1
        await interaction.response.edit_message(embed=self.generate_embed(), view=self)

    @discord.ui.button(label="", emoji="⏪", style=discord.ButtonStyle.secondary, custom_id="first")
    async def first_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn't your menu!", ephemeral=True)
        self.current_page = 0
        await self.update_message(interaction)

    @discord.ui.button(label="", emoji="◀️", style=discord.ButtonStyle.secondary, custom_id="prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn't your menu!", ephemeral=True)
        self.current_page -= 1
        await self.update_message(interaction)

    @discord.ui.button(label="", emoji="▶️", style=discord.ButtonStyle.secondary, custom_id="next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn't your menu!", ephemeral=True)
        self.current_page += 1
        await self.update_message(interaction)

    @discord.ui.button(label="", emoji="⏩", style=discord.ButtonStyle.secondary, custom_id="last")
    async def last_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn't your menu!", ephemeral=True)
        self.current_page = self.max_pages - 1
        await self.update_message(interaction)


async def process_streak_history(user):
    from src.core.database import get_streak_history, format_name
    
    history = await get_streak_history(user.id)
    if not history:
        embed = Theme.get_embed(description=f"You don't have any past streaks >= 25 plays recorded.", color=LASTFM_COLOR)
        embed.set_author(name=f"{format_name(user)}'s Streak History", icon_url=user.display_avatar.url)
        return embed, None
        
    view = StreakHistoryPaginator(user, history)
    embed = view.generate_embed()
    return embed, view
