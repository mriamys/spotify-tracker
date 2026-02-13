import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
import os

load_dotenv()

# Используем "Safe" плейлист или создадим новый
NEW_PLAYLIST_NAME = "Spotify Tracker 2026"

def main():
    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=os.getenv("SPOTIPY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
        scope="user-follow-read playlist-modify-public playlist-modify-private",
        open_browser=False,
        cache_handler=spotipy.cache_handler.CacheFileHandler(cache_path=".cache")
    ))

    print("\n--- ТЕСТ НОВОГО API (FEBRUARY 2026 FIX) ---")
    
    try:
        user_id = sp.current_user()['id']
        print(f"👤 Пользователь: {user_id}")

        # 1. Создаем плейлист через /me/playlists (это работает)
        print("🔨 Создаю плейлист...")
        payload = {"name": NEW_PLAYLIST_NAME, "public": False}
        res = sp._post("me/playlists", payload=payload)
        new_playlist_id = res['id']
        print(f"✅ Плейлист создан! ID: {new_playlist_id}")

        # 2. ДОБАВЛЯЕМ ТРЕК ЧЕРЕЗ НОВЫЙ АДРЕС /items
        print("🧪 Пробую добавить трек через /items ...")
        test_track = "spotify:track:4cOdK2wGLETKBW3PvgPWqT" # Never Gonna Give You Up
        
        # !!! ВОТ ОНО - ИСПРАВЛЕНИЕ !!!
        # Старый адрес: playlists/{id}/tracks (удален)
        # Новый адрес:  playlists/{id}/items
        url = f"playlists/{new_playlist_id}/items"
        
        sp._post(url, payload={"uris": [test_track]})
        
        print(f"✅ УСПЕХ! Трек добавлен. Ошибка 403 побеждена.")
        print("\n" + "="*50)
        print("СКОПИРУЙ ЭТОТ ID В .env:")
        print(f"{new_playlist_id}")
        print("="*50)

    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")

if __name__ == "__main__":
    main()