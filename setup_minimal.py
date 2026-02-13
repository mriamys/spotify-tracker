import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
import os
import sys

load_dotenv()

# Имя плейлиста
NEW_PLAYLIST_NAME = "Spotify Tracker (Safe)"

def main():
    # Авторизация
    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=os.getenv("SPOTIPY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
        scope="user-follow-read playlist-modify-public playlist-modify-private",
        open_browser=False,
        cache_handler=spotipy.cache_handler.CacheFileHandler(cache_path=".cache")
    ))

    print("\n--- БЫСТРАЯ НАСТРОЙКА (БЕЗ СПАМА API) ---")
    
    try:
        # 1. Получаем ID юзера
        user_id = sp.current_user()['id']
        print(f"👤 Пользователь: {user_id}")

        # 2. Создаем плейлист (ПРЯМОЙ ЗАПРОС)
        print("🔨 Создаю плейлист...")
        payload = {
            "name": NEW_PLAYLIST_NAME,
            "public": False, 
            "description": "Created by Bot"
        }
        res = sp._post("me/playlists", payload=payload)
        new_playlist_id = res['id']
        print(f"✅ Плейлист создан! ID: {new_playlist_id}")

        # 3. Тест записи (добавим 1 трек, чтобы убедиться, что права работают)
        print("🧪 Проверяю права на запись (добавляю 1 трек)...")
        # Тестовый трек: Never Gonna Give You Up (для проверки)
        test_track = "spotify:track:4cOdK2wGLETKBW3PvgPWqT"
        
        url = f"playlists/{new_playlist_id}/tracks"
        sp._post(url, payload={"uris": [test_track]})
        print("✅ Трек добавлен! Ошибки 403 НЕТ.")

        print("\n" + "="*50)
        print("🎉 ВСЁ ГОТОВО! СКОПИРУЙ ЭТОТ ID В .env:")
        print(f"\n{new_playlist_id}\n")
        print("="*50)

    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")
        if "403" in str(e):
            print("⚠️ Причина: Вы забыли добавить почту в User Management на сайте Spotify!")

if __name__ == "__main__":
    main()