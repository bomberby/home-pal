import json
import re
from pathlib import Path

SPOTIFY_SECRETS_PATH = Path('env/secrets/spotify.json')
SPOTIFY_TOKEN_PATH = Path('env/secrets/spotify_token.json')
SPOTIFY_SCOPE = 'user-modify-playback-state user-read-playback-state'


class SpotifyService:
    _sp = None

    @classmethod
    def _get_client(cls):
        if cls._sp is None:
            try:
                import spotipy
                from spotipy.oauth2 import SpotifyOAuth, CacheFileHandler

                if not SPOTIFY_SECRETS_PATH.exists():
                    print("[Spotify] env/secrets/spotify.json not found")
                    return None
                if not SPOTIFY_TOKEN_PATH.exists():
                    return None  # not authenticated yet — user must visit /spotify/auth

                creds = json.loads(SPOTIFY_SECRETS_PATH.read_text())
                handler = CacheFileHandler(cache_path=str(SPOTIFY_TOKEN_PATH))
                auth = SpotifyOAuth(
                    client_id=creds['client_id'],
                    client_secret=creds['client_secret'],
                    redirect_uri=creds['redirect_uri'],
                    scope=SPOTIFY_SCOPE,
                    cache_handler=handler,
                    open_browser=False,
                )
                cls._sp = spotipy.Spotify(auth_manager=auth)
            except Exception as e:
                print(f"[Spotify] Init error: {e}")
                return None
        return cls._sp

    @classmethod
    def _build_auth(cls):
        """Build a fresh SpotifyOAuth manager (used for auth flow, not playback)."""
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth, CacheFileHandler

        creds = json.loads(SPOTIFY_SECRETS_PATH.read_text())
        handler = CacheFileHandler(cache_path=str(SPOTIFY_TOKEN_PATH))
        return SpotifyOAuth(
            client_id=creds['client_id'],
            client_secret=creds['client_secret'],
            redirect_uri=creds['redirect_uri'],
            scope=SPOTIFY_SCOPE,
            cache_handler=handler,
            open_browser=False,
        )

    @classmethod
    def get_auth_url(cls) -> str | None:
        try:
            if not SPOTIFY_SECRETS_PATH.exists():
                return None
            return cls._build_auth().get_authorize_url()
        except Exception as e:
            print(f"[Spotify] Auth URL error: {e}")
            return None

    @classmethod
    def handle_callback(cls, code: str) -> bool:
        try:
            cls._build_auth().get_access_token(code)
            cls._sp = None  # reset so next call picks up the new token
            return True
        except Exception as e:
            print(f"[Spotify] Callback error: {e}")
            return False

    @classmethod
    def is_authenticated(cls) -> bool:
        return SPOTIFY_TOKEN_PATH.exists()

    # --- Device resolution ---

    @classmethod
    def _resolve_device_id(cls) -> str | None:
        """Return the active device ID, or the first available device if none is active.
        Passing an explicit device_id avoids 404 'No active device' errors after inactivity."""
        sp = cls._get_client()
        if not sp:
            return None
        try:
            devices = (sp.devices() or {}).get('devices', [])
            if not devices:
                return None
            active = next((d for d in devices if d.get('is_active')), None)
            device = active or devices[0]
            print(f"[Spotify] Device: {device['name']} (active={device.get('is_active')})")
            return device['id']
        except Exception as e:
            print(f"[Spotify] Device lookup failed: {e}")
            return None

    # --- Playback controls ---

    @classmethod
    def play(cls) -> str:
        sp = cls._get_client()
        if not sp:
            return "Spotify is not connected. Visit /spotify/auth to set it up."
        try:
            sp.start_playback(device_id=cls._resolve_device_id())
            return "Music playing."
        except Exception as e:
            return f"Couldn't resume playback: {e}"

    @classmethod
    def pause(cls) -> str:
        sp = cls._get_client()
        if not sp:
            return "Spotify is not connected."
        try:
            sp.pause_playback()
            return "Music paused."
        except Exception as e:
            return f"Couldn't pause: {e}"

    @classmethod
    def skip(cls) -> str:
        sp = cls._get_client()
        if not sp:
            return "Spotify is not connected."
        try:
            sp.next_track()
            return "Skipped to next track."
        except Exception as e:
            return f"Couldn't skip: {e}"

    @classmethod
    def previous(cls) -> str:
        sp = cls._get_client()
        if not sp:
            return "Spotify is not connected."
        try:
            sp.previous_track()
            return "Going back to previous track."
        except Exception as e:
            return f"Couldn't go back: {e}"

    # Matches "X by Y" — track name followed by artist name
    _BY_PATTERN = re.compile(r'^(.+?)\s+by\s+(.+)$', re.IGNORECASE)
    # Matches "X feat Y" / "X ft Y"
    _FEAT_PATTERN = re.compile(r'^(.+?)\s+(?:feat|ft)\.?\s+(.+)$', re.IGNORECASE)

    @classmethod
    def play_search(cls, query: str) -> str:
        sp = cls._get_client()
        if not sp:
            return "Spotify is not connected. Visit /spotify/auth to set it up."
        try:
            words = set(query.lower().split())
            search_query = query
            search_order = None

            # Album intent: "play album X" / "play X album" / "play X by Y album"
            if 'album' in words or 'discography' in words:
                # Strip the intent keywords so they don't pollute the search
                clean = re.sub(r'\b(album|discography)\b', '', query, flags=re.IGNORECASE).strip()
                m = cls._BY_PATTERN.match(clean)
                if m:
                    album_name, artist = m.group(1).strip(), m.group(2).strip()
                    search_query = f'album:"{album_name}" artist:"{artist}"'
                    search_order = ('albums',)
                else:
                    search_query = clean
                    search_order = ('albums', 'artists')

            # "X by Y" → precise track+artist field filter
            elif cls._BY_PATTERN.match(query):
                m = cls._BY_PATTERN.match(query)
                track, artist = m.group(1).strip(), m.group(2).strip()
                search_query = f'track:"{track}" artist:"{artist}"'
                search_order = ('tracks',)

            # "X feat Y" → track search
            elif cls._FEAT_PATTERN.match(query):
                search_order = ('tracks', 'artists', 'playlists')

            # Playlist/genre keywords
            elif words.intersection({'playlist', 'mix', 'radio', 'lofi', 'ambient', 'chill'}):
                search_order = ('playlists', 'artists', 'tracks')

            # Short query → likely an artist name
            elif len(words) <= 3:
                search_order = ('artists', 'tracks', 'playlists')

            else:
                search_order = ('tracks', 'artists', 'playlists')

            device_id = cls._resolve_device_id()
            results = sp.search(q=search_query, limit=5, type='track,artist,album,playlist')
            print(f"[Spotify] Search '{search_query}' → order: {search_order}")
            ql = query.lower()

            for result_type in search_order:
                raw_items = (results or {}).get(result_type, {}) or {}
                items = [i for i in raw_items.get('items', []) if i]  # filter None placeholders
                if not items:
                    continue
                # Prefer an item whose name closely matches the original query
                item = next(
                    (i for i in items if ql in i['name'].lower() or i['name'].lower() in ql),
                    items[0],
                )
                name = item['name']
                uri = item['uri']
                artists = ', '.join(a['name'] for a in item.get('artists', []))
                if result_type == 'tracks':
                    sp.start_playback(device_id=device_id, uris=[uri])
                    print(f"[Spotify] Playing track: {name} — {artists} ({uri})")
                    return f"Playing {name} by {artists}."
                elif result_type == 'albums':
                    sp.start_playback(device_id=device_id, context_uri=uri)
                    print(f"[Spotify] Playing album: {name} — {artists} ({uri})")
                    return f"Playing album {name} by {artists}."
                elif result_type == 'artists':
                    sp.start_playback(device_id=device_id, context_uri=uri)
                    print(f"[Spotify] Playing artist: {name} ({uri})")
                    return f"Playing {name}."
                else:
                    owner = (item.get('owner') or {}).get('display_name', '')
                    sp.start_playback(device_id=device_id, context_uri=uri)
                    print(f"[Spotify] Playing playlist: {name} by {owner} ({uri})")
                    return f"Playing playlist {name}."

            return f"Nothing found for '{query}'."
        except Exception as e:
            return f"Couldn't play '{query}': {e}"

    @classmethod
    def now_playing(cls) -> str:
        sp = cls._get_client()
        if not sp:
            return "Spotify is not connected."
        try:
            playback = sp.current_playback()
            if not playback or not playback.get('is_playing'):
                return "Nothing is playing right now."
            item = playback.get('item')
            if not item:
                return "Something is playing but I can't read what it is."
            name = item['name']
            artists = ', '.join(a['name'] for a in item.get('artists', []))
            progress_ms = playback.get('progress_ms', 0)
            duration_ms = item.get('duration_ms', 0)
            progress = f"{progress_ms // 60000}:{(progress_ms % 60000) // 1000:02d}"
            duration = f"{duration_ms // 60000}:{(duration_ms % 60000) // 1000:02d}"
            return f"Now playing: {name} by {artists} ({progress} / {duration})."
        except Exception as e:
            return f"Couldn't get playback info: {e}"

    @classmethod
    def set_volume(cls, percent: int) -> str:
        sp = cls._get_client()
        if not sp:
            return "Spotify is not connected."
        try:
            percent = max(0, min(100, percent))
            sp.volume(percent)
            return f"Volume set to {percent}%."
        except Exception as e:
            return f"Couldn't set volume: {e}"

    @classmethod
    def get_current_volume(cls) -> int | None:
        sp = cls._get_client()
        if not sp:
            return None
        try:
            playback = sp.current_playback()
            if playback and playback.get('device'):
                return playback['device'].get('volume_percent')
            return None
        except Exception:
            return None

    # --- Natural language helpers ---

    @staticmethod
    def extract_search_query(query: str) -> str | None:
        """Extract what to play from 'play X' queries."""
        match = re.search(
            r'\bplay\s+(?:some\s+)?(.+?)(?:\s+(?:please|now|for me|on spotify))?$',
            query,
            re.IGNORECASE,
        )
        if not match:
            return None
        extracted = match.group(1).strip()
        # Ignore generic filler phrases
        if extracted.lower() in ('music', 'something', 'a song', 'songs', 'spotify', 'anything'):
            return None
        return extracted

    @staticmethod
    def extract_volume_percent(query: str) -> int | None:
        """Extract an explicit volume number from 'set volume to 50' etc."""
        match = re.search(r'\b(\d{1,3})\s*(?:%|percent)?\b', query)
        if match:
            return int(match.group(1))
        return None
