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
FIRST_RUN_MODE = True  
DATABASE_FILE = "bot_data.json"
# =============================================

load_dotenv()

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

def get_all_followed_artists(sp):
    artists = []
    try:
        results = sp.current_user_followed_artists(limit=50)
        artists.extend(results['artists']['items'])
        while results['artists']['cursors']['after']:
            results = sp.current_user_followed_artists(
                limit=50, 
                after=results['artists']['cursors']['after']
            )
            artists.extend(results['artists']['items'])
    except Exception as e:
        print(f"Ошибка получения подписок: {e}")
    return artists

def get_latest_track_for_artist(sp, artist_id):
    try:
        albums = sp.artist_albums(artist_id, album_type='album,single', country="UA", limit=5)
        if not albums['items']:
            return None, None
        
        sorted_albums = sorted(albums['items'], key=lambda x: x['release_date'], reverse=True)
        latest_album = sorted_albums[0]
        
        tracks = sp.album_tracks(latest_album['id'], limit=1)
        if tracks['items']:
            return tracks['items'][0]['uri'], latest_album['release_date']
    except Exception as e:
        print(f"Ошибка поиска трека: {e}")
    return None, None

def add_tracks_to_playlist_safe(sp, playlist_id, track_uris):
    """
    Безопасное добавление через новый API
    """
    try:
        # Используем новый эндпоинт playlists/{id}/tracks
        # Библиотека может использовать старый, поэтому вызываем напрямую
        sp.playlist_add_items(playlist_id, track_uris)
    except spotipy.exceptions.SpotifyException as e:
        # Если 404/403 - пробуем совсем прямой метод, если библиотека старая
        print(f"Стандартный метод сбойнул ({e}), пробую прямой запрос...")
        url = f"playlists/{playlist_id}/tracks"
        sp._post(url, payload={"uris": track_uris})

def initial_fill_playlist():
    print("\n=== ЗАПУЩЕН БЕЗОПАСНЫЙ РЕЖИМ (NEW API 2026) ===")
    
    sp = get_spotify_client()
    artists = get_all_followed_artists(sp)
    print(f"Всего подписок: {len(artists)}")
    
    tracks_to_add = []
    latest_global_date = "2000-01-01"
    
    for i, artist in enumerate(artists):
        print(f"[{i+1}/{len(artists)}] {artist['name']}...", end="\r")
        track_uri, release_date = get_latest_track_for_artist(sp, artist['id'])
        if track_uri:
            tracks_to_add.append(track_uri)
            if release_date > latest_global_date:
                latest_global_date = release_date
        time.sleep(1.5) # Пауза от бана

    print(f"\nНайдено треков: {len(tracks_to_add)}")
    
    if tracks_to_add:
        unique_uris = list(set(tracks_to_add))
        for i in range(0, len(unique_uris), 20):
            batch = unique_uris[i:i+20]
            try:
                add_tracks_to_playlist_safe(sp, PLAYLIST_ID, batch)
                print(f"Добавлено {i}-{i+len(batch)} треков")
                time.sleep(2)
            except Exception as e:
                print(f"Ошибка добавления: {e}")
        
        save_data(latest_global_date)
        print(f"\n✅ Готово! Дата обновлена: {latest_global_date}")
        sys.exit(0)
    else:
        print("Треков не найдено.")

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
            albums = sp.artist_albums(artist['id'], limit=5, country="UA")
            for album in albums['items']:
                if album['release_date'] > last_date:
                    print(f"НОВИНКА: {artist['name']} - {album['name']}")
                    tracks = sp.album_tracks(album['id'])
                    for track in tracks['items']:
                        new_tracks.append(track['uri'])
                    if album['release_date'] > new_max_date:
                        new_max_date = album['release_date']
            time.sleep(0.5)

        if new_tracks:
            unique = list(set(new_tracks))
            for i in range(0, len(unique), 50):
                add_tracks_to_playlist_safe(sp, PLAYLIST_ID, unique[i:i+50])
            save_data(new_max_date)
            print(f"Добавлено {len(unique)} треков.")
        else:
            print("Новинок нет.")
            
    except Exception as e:
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    if FIRST_RUN_MODE:
        initial_fill_playlist()
    else:
        check_new_releases()
        schedule.every().day.at("09:00").do(check_new_releases)
        schedule.every().day.at("21:00").do(check_new_releases)
        while True:
            schedule.run_pending()
            time.sleep(60)