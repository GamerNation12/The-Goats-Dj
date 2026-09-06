import aiohttp
import asyncio
import time
import urllib.parse
import logging
from ..core.config import LASTFM_API_KEY, LASTFM_API_SECRET

from src.core.database import format_name

# --- Shared speed helpers ---
# Limit concurrent Last.fm calls so we don't get rate-limited (429s = slow).
_LASTFM_SEM = asyncio.Semaphore(8)
# Tiny in-memory TTL cache for idempotent GETs (track/artist info, etc.)
_API_CACHE: dict = {}  # url -> (data, expires_monotonic)


def _cache_get(url):
    entry = _API_CACHE.get(url)
    if entry and entry[1] > time.monotonic():
        return entry[0]
    if entry:
        _API_CACHE.pop(url, None)
    return None


def _cache_set(url, data, ttl=300):
    _API_CACHE[url] = (data, time.monotonic() + ttl)
    if len(_API_CACHE) > 2000:
        # evict oldest-ish entries
        for k in list(_API_CACHE.keys())[:200]:
            _API_CACHE.pop(k, None)


def _fast_url(url: str) -> str:
    # https avoids an http->https redirect on Last.fm and is required by some hosts.
    if url.startswith("http://ws.audioscrobbler.com"):
        return "https://" + url[len("http://"):]
    return url


async def api_get(url, max_retries=2, timeout_s=5, cache_ttl=0):
    import aiohttp
    import asyncio
    from ..core.events import bot

    url = _fast_url(url)
    if cache_ttl > 0:
        cached = _cache_get(url)
        if cached is not None:
            return cached

    for attempt in range(max_retries):
        try:
            async with _LASTFM_SEM:
                async with bot.session.get(url, timeout=aiohttp.ClientTimeout(total=timeout_s)) as r:
                    try:
                        data = await r.json()
                    except Exception:
                        data = None
                        logging.error(f"Failed to parse JSON from Last.fm API. Status: {r.status}")

                    if r.status != 200 or (isinstance(data, dict) and 'error' in data):
                        logging.error(f"Last.fm API Error (Attempt {attempt+1}/{max_retries}): {data} for url: {url.replace(LASTFM_API_KEY, 'HIDDEN_KEY')}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(0.3 * (attempt + 1))
                            continue

                    if cache_ttl > 0 and data is not None:
                        _cache_set(url, data, cache_ttl)
                    return data
        except Exception as e:
            logging.error(f"API get failed (Attempt {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(0.3 * (attempt + 1))
                continue
            return None
    return None
async def fetch_now_playing(u, l=1):
    d = await api_get(f"https://ws.audioscrobbler.com/2.0/?method=user.getrecenttracks&user={u}&api_key={LASTFM_API_KEY}&format=json&limit={l}", max_retries=3, timeout_s=3)
    if d and 'recenttracks' in d and 'track' in d['recenttracks'] and isinstance(d['recenttracks']['track'], dict):
        d['recenttracks']['track'] = [d['recenttracks']['track']]
    return d

async def fetch_recent_tracks(u, l=200, page=1):
    d = await api_get(f"https://ws.audioscrobbler.com/2.0/?method=user.getrecenttracks&user={u}&api_key={LASTFM_API_KEY}&format=json&limit={l}&page={page}", max_retries=2, timeout_s=10)
    if d and 'recenttracks' in d and 'track' in d['recenttracks'] and isinstance(d['recenttracks']['track'], dict):
        d['recenttracks']['track'] = [d['recenttracks']['track']]
    return d

async def fetch_top_artists(u, p='overall', l=10):
    d = await api_get(f"https://ws.audioscrobbler.com/2.0/?method=user.gettopartists&user={u}&api_key={LASTFM_API_KEY}&format=json&limit={l}&period={p}")
    if d and 'topartists' in d and 'artist' in d['topartists'] and isinstance(d['topartists']['artist'], dict):
        d['topartists']['artist'] = [d['topartists']['artist']]
    return d

async def fetch_top_albums(u, p='overall', l=10):
    d = await api_get(f"https://ws.audioscrobbler.com/2.0/?method=user.gettopalbums&user={u}&api_key={LASTFM_API_KEY}&format=json&limit={l}&period={p}")
    if d and 'topalbums' in d and 'album' in d['topalbums'] and isinstance(d['topalbums']['album'], dict):
        d['topalbums']['album'] = [d['topalbums']['album']]
    return d

async def fetch_top_tracks(u, p='overall', l=10):
    d = await api_get(f"https://ws.audioscrobbler.com/2.0/?method=user.gettoptracks&user={u}&api_key={LASTFM_API_KEY}&format=json&limit={l}&period={p}")
    if d and 'toptracks' in d and 'track' in d['toptracks'] and isinstance(d['toptracks']['track'], dict):
        d['toptracks']['track'] = [d['toptracks']['track']]
    return d

async def fetch_user_profile(u): return await api_get(f"https://ws.audioscrobbler.com/2.0/?method=user.getinfo&user={u}&api_key={LASTFM_API_KEY}&format=json", cache_ttl=300)
async def fetch_track_info(u, a, t): return await api_get(f"https://ws.audioscrobbler.com/2.0/?method=track.getinfo&api_key={LASTFM_API_KEY}&artist={urllib.parse.quote(a)}&track={urllib.parse.quote(t)}&username={u}&format=json", max_retries=1, timeout_s=2, cache_ttl=300)
async def fetch_artist_info(u, artist): return await api_get(f"https://ws.audioscrobbler.com/2.0/?method=artist.getinfo&artist={urllib.parse.quote(artist)}&username={u}&api_key={LASTFM_API_KEY}&format=json&autocorrect=0", cache_ttl=600)
async def fetch_album_info(u, artist, album): return await api_get(f"https://ws.audioscrobbler.com/2.0/?method=album.getinfo&artist={urllib.parse.quote(artist)}&album={urllib.parse.quote(album)}&username={u}&api_key={LASTFM_API_KEY}&format=json&autocorrect=0", cache_ttl=600)
async def fetch_artist_playcount(session, u, artist):
    try:
        async with _LASTFM_SEM:
            async with session.get(f"https://ws.audioscrobbler.com/2.0/?method=artist.getinfo&artist={urllib.parse.quote(artist)}&username={u}&api_key={LASTFM_API_KEY}&format=json&autocorrect=0", timeout=aiohttp.ClientTimeout(total=4)) as r:
                if r.status == 200:
                    d = await r.json()
                    if 'artist' in d and 'stats' in d['artist']:
                        return int(d['artist']['stats'].get('userplaycount', 0) or 0)
                    return 0
    except Exception:
        pass
    return 0

async def fetch_album_playcount(session, u, artist, album):
    try:
        async with _LASTFM_SEM:
            async with session.get(f"https://ws.audioscrobbler.com/2.0/?method=album.getinfo&artist={urllib.parse.quote(artist)}&album={urllib.parse.quote(album)}&username={u}&api_key={LASTFM_API_KEY}&format=json&autocorrect=0", timeout=aiohttp.ClientTimeout(total=4)) as r:
                if r.status == 200:
                    d = await r.json()
                    if 'album' in d:
                        return int(d['album'].get('userplaycount', 0) or 0)
    except Exception:
        pass
    return 0

async def fetch_track_playcount(session, u, artist, track):
    try:
        async with _LASTFM_SEM:
            async with session.get(f"https://ws.audioscrobbler.com/2.0/?method=track.getinfo&artist={urllib.parse.quote(artist)}&track={urllib.parse.quote(track)}&username={u}&api_key={LASTFM_API_KEY}&format=json&autocorrect=0", timeout=aiohttp.ClientTimeout(total=4)) as r:
                if r.status == 200:
                    d = await r.json()
                    if 'track' in d:
                        return int(d['track'].get('userplaycount', 0) or 0)
    except Exception:
        pass
    return 0

async def fetch_artist_top_tracks_global(artist, limit=50):
    data = await api_get(f"https://ws.audioscrobbler.com/2.0/?method=artist.gettoptracks&artist={urllib.parse.quote(artist)}&api_key={LASTFM_API_KEY}&format=json&limit={limit}", cache_ttl=3600)
    if data and 'toptracks' in data:
        tracks = data['toptracks'].get('track', [])
        if isinstance(tracks, dict):
            tracks = [tracks]
        return [t['name'] for t in tracks if 'name' in t]
    return []

async def fetch_user_artist_tracks_lastfm(u, artist):
    # Fetch global top tracks for the artist (capped — 100 concurrent calls was hammering Last.fm)
    top_tracks = await fetch_artist_top_tracks_global(artist, 50)
    if not top_tracks: return []

    import asyncio
    sem = asyncio.Semaphore(8)

    async def _one(t):
        async with sem:
            try:
                res = await fetch_track_info(u, artist, t)
                if res and 'track' in res:
                    return res
            except Exception:
                pass
            # Spotify-style multi-artist strings ("A, B") never resolve on
            # Last.fm — retry with just the lead artist before giving up.
            if ',' in artist:
                try:
                    return await fetch_track_info(u, artist.split(',')[0].strip(), t)
                except Exception:
                    return None
            return None

    results = await asyncio.gather(*[_one(t) for t in top_tracks], return_exceptions=True)
    
    user_tracks = []
    for res in results:
        if res and 'track' in res:
            t_info = res['track']
            pc = int(t_info.get('userplaycount', 0))
            if pc > 0:
                user_tracks.append((t_info['name'], pc))
                
    # Sort by user playcount descending
    user_tracks.sort(key=lambda x: x[1], reverse=True)
    return user_tracks

async def fetch_deezer_artist_image(session, artist_name):
    url = f"https://api.deezer.com/search/artist?q={urllib.parse.quote(artist_name)}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                data = await r.json()
                if data and 'data' in data and len(data['data']) > 0:
                    artist = data['data'][0]
                    return artist.get('picture_xl') or artist.get('picture_big') or artist.get('picture')
    except Exception as e:
        logging.error(f"Deezer fetch error: {e}")
    return None

async def fetch_deezer_track_image(session, track_name, artist_name):
    url = f"https://api.deezer.com/search/track?q={urllib.parse.quote(track_name + ' ' + artist_name)}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=1.5)) as r:
            if r.status == 200:
                data = await r.json()
                if data and 'data' in data and len(data['data']) > 0:
                    track = data['data'][0]
                    album = track.get('album', {})
                    return album.get('cover_xl') or album.get('cover_big') or album.get('cover')
    except Exception as e:
        logging.error(f"Deezer track fetch error: {e}")
    return None

async def scrobble_bot_track(session, artist, track, album=None):
    import os
    import logging
    BOT_LASTFM_SESSION_KEY = os.getenv("BOT_LASTFM_SESSION_KEY", "").strip().strip('"').strip("'")
    
    global LASTFM_API_KEY, LASTFM_API_SECRET
    api_key = LASTFM_API_KEY.strip().strip('"').strip("'")
    api_secret = LASTFM_API_SECRET.strip().strip('"').strip("'")
    
    if not BOT_LASTFM_SESSION_KEY:
        return "NO_SESSION_KEY"
    if not api_secret:
        return "NO_API_SECRET"
        
    import hashlib
    import time
    timestamp = str(int(time.time()))
    
    # Scrobble
    sc_params = {
        'api_key': api_key,
        'artist[0]': artist,
        'method': 'track.scrobble',
        'sk': BOT_LASTFM_SESSION_KEY,
        'timestamp[0]': timestamp,
        'track[0]': track
    }
    if album:
        sc_params['album[0]'] = album
    
    sc_sig_string = ""
    for k in sorted(sc_params.keys()):
        sc_sig_string += f"{k}{sc_params[k]}"
    sc_sig_string += api_secret
    sc_params['api_sig'] = hashlib.md5(sc_sig_string.encode('utf-8')).hexdigest()
    sc_params['format'] = 'json'

    try:
        # Scrobble
        async with session.post("https://ws.audioscrobbler.com/2.0/", data=sc_params) as r_sc:
            if r_sc.status == 200:
                data = await r_sc.json()
                if 'scrobbles' in data:
                    logging.info(f"Successfully scrobbled {track} by {artist} to bot profile.")
                    return True
                return f"NO_SCROBBLES_IN_DATA_{data}"
            else:
                err_text = await r_sc.text()
                logging.error(f"Bot scrobble failed with status {r_sc.status}: {err_text}")
                return f"HTTP_{r_sc.status}_{err_text[:20]}"
    except Exception as e:
        logging.error(f"Bot scrobble request failed: {e}")
        return f"EXC_{e}"
    return "UNKNOWN_ERROR"

async def fetch_musicbrainz_artist_info(session, artist_name):
    url = f"https://musicbrainz.org/ws/2/artist/?query=artist:{urllib.parse.quote(artist_name)}&fmt=json"
    headers = {"User-Agent": "DJScratchBot/1.0 ( https://github.com/GamerNation12/DJ-Scratch )"}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status == 200:
                data = await r.json()
                if data and 'artists' in data and len(data['artists']) > 0:
                    artist = data['artists'][0]
                    return {
                        "type": artist.get("type"), # 'Group' or 'Person'
                        "country": artist.get("country"), # e.g. 'US', 'GB'
                        "start_date": artist.get("life-span", {}).get("begin")
                    }
    except Exception as e:
        logging.error(f"MusicBrainz fetch error: {e}")
    return None
