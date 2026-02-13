import os
import time
import schedule
import json
import sys
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from datetime import datetime
from dotenv import load_dotenv

# ================= НАСТРОЙКИ =================
STATE_FILE = "bot_state.json"
SAFE_DELAY = 2 
# =============================================

load_dotenv()

if not os.getenv("SPOTIPY_CLIENT_ID") or not os.getenv("PLAYLIST_ID"):
    print("❌ ОШИБКА: Проверь файл .env")
    sys.exit(1)

CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")
PLAYLIST_ID = os.getenv("PLAYLIST_ID")

SCOPE = "user-follow-read playlist-modify-public playlist-modify-private"

def get_spotify_client():
    auth_manager = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        open_browser=False,
        cache_handler=spotipy.cache_handler.CacheFileHandler(cache_path=".cache")
    )
    return spotipy.Spotify(auth_manager=auth_manager)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {
        "initial_scan_done": False,
        "last_processed_index": 0,
        "last_checked_date": "2000-01-01"
    }

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

def add_tracks_direct(sp, track_uris):
    if not track_uris: return
    try:
        # Пачками по 50 (лимит API)
        for i in range(0, len(track_uris), 50):
            chunk = track_uris[i:i+50]
            url = f"playlists/{PLAYLIST_ID}/items"
            sp._post(url, payload={"uris": chunk})
            print(f"   ✅ Добавлено {len(chunk)} треков.")
    except Exception as e:
        print(f"   ❌ Ошибка добавления: {e}")

def handle_rate_limit(e):
    if hasattr(e, 'http_status') and e.http_status == 429:
        retry_after = int(e.headers.get('Retry-After', 60)) + 5
        print(f"\n⚠️ ЛИМИТ! Spotify просит подождать {retry_after} сек.")
        print("   💤 Сплю (не выключай меня)...")
        time.sleep(retry_after)
        return True
    return False

def get_latest_track_smart(sp, artist_id):
    """
    УМНЫЙ ПОИСК ДЛЯ БАЗЫ:
    1. Запрашивает 5 последних релизов (И альбомы, И синглы).
    2. Сортирует их по дате.
    3. Возвращает самый свежий.
    """
    try:
        # ЗАПРОС: include_groups='album,single' критически важен!
        results = sp.artist_albums(
            artist_id, 
            album_type='album,single', 
            country="UA", 
            limit=5
        )
        items = results['items']
        
        if not items:
            return None, None

        # Сортировка Python (надежнее, чем доверять порядку Spotify)
        sorted_releases = sorted(items, key=lambda x: x['release_date'], reverse=True)
        latest_release = sorted_releases[0]
        
        # Берем 1 трек для базы
        tracks = sp.album_tracks(latest_release['id'], limit=1)
        if tracks['items']:
            return tracks['items'][0]['uri'], latest_release['release_date']
            
    except Exception as e:
        if hasattr(e, 'http_status') and e.http_status == 429: raise e
    return None, None

def run_smart_scan():
    state = load_state()
    sp = get_spotify_client()
    
    print(f"\n[{datetime.now().strftime('%H:%M')}] 🚀 Умное сканирование...")

    try:
        results = sp.current_user_followed_artists(limit=50)
        artists = results['artists']['items']
        while results['artists']['cursors']['after']:
            results = sp.current_user_followed_artists(limit=50, after=results['artists']['cursors']['after'])
            artists.extend(results['artists']['items'])
        
        print(f"   Подписок: {len(artists)}")

        # === РЕЖИМ 1: ПЕРВИЧНОЕ ЗАПОЛНЕНИЕ (Smart Sort) ===
        if not state["initial_scan_done"]:
            start_index = state["last_processed_index"]
            print(f"   📢 Продолжаю базу с {start_index+1}-го артиста.")
            
            latest_global_date = state["last_checked_date"]
            
            for i in range(start_index, len(artists)):
                artist = artists[i]
                print(f"   [{i+1}/{len(artists)}] {artist['name']}...", end="\r")
                
                # Используем УМНЫЙ поиск (видит синглы)
                track_uri, release_date = get_latest_track_smart(sp, artist['id'])
                
                if track_uri:
                    add_tracks_direct(sp, [track_uri])
                    if release_date > latest_global_date:
                        latest_global_date = release_date
                
                state["last_processed_index"] = i + 1
                state["last_checked_date"] = latest_global_date
                save_state(state)
                time.sleep(SAFE_DELAY)

            print("\n   ✅ База собрана! Перехожу в режим новинок.")
            state["initial_scan_done"] = True
            state["last_processed_index"] = 0
            save_state(state)

        # === РЕЖИМ 2: НОВИНКИ (Full Album + Singles) ===
        else:
            print(f"   📢 Ищу новинки (свежее {state['last_checked_date']})...")
            last_date = state["last_checked_date"]
            new_max_date = last_date
            found_tracks = []
            
            for i, artist in enumerate(artists):
                try:
                    # ЗАПРОС: Ищем 5 последних релизов (И альбомы, И синглы)
                    albums = sp.artist_albums(
                        artist['id'], 
                        limit=5, 
                        album_type='album,single', # <-- ВАЖНО
                        country="UA"
                    )
                    
                    for album in albums['items']:
                        if album['release_date'] > last_date:
                            print(f"   🔥 НОВИНКА: {artist['name']} - {album['name']}")
                            
                            # Скачиваем ВЕСЬ релиз (до 50 треков)
                            tracks = sp.album_tracks(album['id'], limit=50)
                            
                            for t in tracks['items']: 
                                found_tracks.append(t['uri'])
                            
                            if album['release_date'] > new_max_date:
                                new_max_date = album['release_date']
                    time.sleep(0.5)
                except Exception as e:
                    if handle_rate_limit(e): return 

            if found_tracks:
                unique = list(set(found_tracks))
                print(f"   Заливаю {len(unique)} новых треков...")
                add_tracks_direct(sp, unique)
                state["last_checked_date"] = new_max_date
                save_state(state)
            else:
                print("   Новинок нет.")

    except Exception as e:
        if not handle_rate_limit(e):
            print(f"\n❌ Ошибка: {e}")

if __name__ == "__main__":
    print("🤖 Бот запущен (v4.0 Final: Singles + Albums)")
    run_smart_scan()
    schedule.every().day.at("09:00").do(run_smart_scan)
    schedule.every().day.at("21:00").do(run_smart_scan)
    schedule.every(6).hours.do(run_smart_scan)
    while True:
        schedule.run_pending()
        time.sleep(60)