import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
import os
import json

load_dotenv()

# Название плейлиста
NEW_PLAYLIST_NAME = "Spotify Tracker Music"

# Авторизация
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=os.getenv("SPOTIPY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
    scope="user-follow-read playlist-modify-public playlist-modify-private",
    open_browser=False,
    cache_handler=spotipy.cache_handler.CacheFileHandler(cache_path=".cache")
))

print("\n--- СОЗДАНИЕ ПЛЕЙЛИСТА (НОВЫЙ МЕТОД 2026) ---")

try:
    user = sp.current_user()
    print(f"Авторизован как: {user['display_name']}")

    # ПАРАМЕТРЫ ПЛЕЙЛИСТА
    payload = {
        "name": NEW_PLAYLIST_NAME,
        "public": True,
        "description": "Created via Spotify Tracker Bot"
    }

    # !!! ХАК ДЛЯ НОВОГО API !!!
    # Вместо sp.user_playlist_create(...) мы отправляем запрос напрямую
    # на новый адрес "me/playlists"
    result = sp._post("me/playlists", payload=payload)
    
    print("\n" + "="*40)
    print(f"✅ ПОБЕДА! Плейлист создан!")
    print("="*40)
    print(f"НОВЫЙ PLAYLIST_ID: {result['id']}")
    print("="*40)
    print("СКОПИРУЙ ЭТОТ ID В ФАЙЛ .env !!!")
    print("="*40 + "\n")

except Exception as e:
    print(f"\n❌ ОШИБКА: {e}")