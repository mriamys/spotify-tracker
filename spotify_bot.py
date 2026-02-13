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
# Поставь False, если уже заполнил плейлист и ждешь только новинки
FIRST_RUN_MODE = False  
DATABASE_FILE = "bot_data.json"
# =============================================

load_dotenv()

# Проверяем, что ключи загрузились
if not os.getenv("SPOTIPY_CLIENT_ID"):
    print("❌ ОШИБКА: Не найдены ключи в .env")
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

def load_data():
    if os.path.exists(DATABASE_FILE):
        with open(DATABASE_FILE, 'r') as f:
            return json.load(f)
    return {"last_checked_date": "2000-01-01"}

def save_data(date_str):
    with open(DATABASE_FILE, 'w') as f:
        json.dump({"last_checked_date": date_str}, f)

def add_tracks_2026(sp, playlist_id, track_uris):
    """
    Добавление треков с учетом изменений API от февраля 2026.
    Использует endpoint /items вместо устаревшего /tracks.
    """
    if not track_uris: return

    print(f"   > Добавляю {len(track_uris)} треков...")
    
    # Разбиваем на пачки по 50 штук
    for i in range(0, len(track_uris), 50):
        chunk = track_uris[i:i+50]
        try:
            # ПРЯМОЙ ЗАПРОС НА НОВЫЙ URL
            url = f"playlists/{playlist_id}/items"
            sp._post(url, payload={"uris": chunk})
            print(f"     ✅ Пачка {i+1}-{i+len(chunk)} добавлена.")
        except Exception as e:
            print(f"     ❌ Ошибка добавления: {e}")

def get_latest_track_for_artist(sp, artist_id):
    try:
        albums = sp.artist_albums(artist_id, album_type='album,single', country="UA", limit=1)
        if not albums['items']: return None, None
        
        latest_album = albums['items'][0]
        tracks = sp.album_tracks(latest_album['id'], limit=1)
        if tracks['items']:
            return tracks['items'][0]['uri'], latest_album['release_date']
    except:
        pass
    return None, None

def get_all_followed_artists(sp):
    artists = []
    try:
        results = sp.current_user_followed_artists(limit=50)
        artists.extend(results['artists']['items'])
        while results['artists']['cursors']['after']:
            results = sp.current_user_followed_artists(limit=50, after=results['artists']['cursors']['after'])
            artists.extend(results['artists']['items'])
    except Exception as e:
        print(f"Ошибка получения подписок: {e}")
    return artists

def initial_fill_playlist():
    print("\n=== ЗАПУСК: ПЕРВИЧНОЕ ЗАПОЛНЕНИЕ (API 2026) ===")
    sp = get_spotify_client()
    artists = get_all_followed_artists(sp)
    print(f"Всего подписок: {len(artists)}")
    
    tracks_to_add = []
    latest_global_date = "2000-01-01"
    
    for i, artist in enumerate(artists):
        print(f"[{i+1}/{len(artists)}] {artist['name']}...", end="\r")
        uri, date = get_latest_track_for_artist(sp, artist['id'])
        if uri:
            tracks_to_add.append(uri)
            if date > latest_global_date: latest_global_date = date
        time.sleep(0.5) # Бережем лимиты

    print(f"\nНайдено треков: {len(tracks_to_add)}")
    add_tracks_2026(sp, PLAYLIST_ID, tracks_to_add)
    
    save_data(latest_global_date)
    print(f"\n✅ Готово! Дата обновлена: {latest_global_date}")

def check_new_releases():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Проверка новинок...")
    try:
        sp = get_spotify_client()
        data = load_data()
        last_date = data["last_checked_date"]
        
        artists = get_all_followed_artists(sp)
        new_tracks = []
        new_max_date = last_date
        
        for artist in artists:
            albums = sp.artist_albums(artist['id'], limit=2, country="UA")
            for album in albums['items']:
                if album['release_date'] > last_date:
                    print(f"🔥 НОВИНКА: {artist['name']} - {album['name']}")
                    tracks = sp.album_tracks(album['id'], limit=5)
                    for t in tracks['items']: new_tracks.append(t['uri'])
                    if album['release_date'] > new_max_date: new_max_date = album['release_date']
            time.sleep(0.1)

        if new_tracks:
            add_tracks_2026(sp, PLAYLIST_ID, list(set(new_tracks)))
            save_data(new_max_date)
        else:
            print("Новинок нет.")
            
    except Exception as e:
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    # Убедись, что используешь ключи от приложения "Tracker 4" (где добавлена почта)
    if FIRST_RUN_MODE:
        initial_fill_playlist()
    else:
        print("Бот работает. Расписание: 09:00 и 21:00.")
        check_new_releases()
        schedule.every().day.at("09:00").do(check_new_releases)
        schedule.every().day.at("21:00").do(check_new_releases)
        while True:
            schedule.run_pending()
            time.sleep(60)