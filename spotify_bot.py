import requests
import spotipy
import time
import schedule
import json
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

# Загружаем переменные из .env файла (он должен лежать рядом)
load_dotenv()

# ================= НАСТРОЙКИ ИЗ ENV =================
SP_DC_COOKIE = os.getenv("SP_DC_COOKIE")
PLAYLIST_ID = os.getenv("PLAYLIST_ID")
DATABASE_FILE = "bot_data.json"

# Проверка, что ключи на месте
if not SP_DC_COOKIE or not PLAYLIST_ID:
    print("ОШИБКА: Не найдены переменные SP_DC_COOKIE или PLAYLIST_ID в .env файле!")
    sys.exit(1)
# ====================================================

def get_token_from_cookie(sp_dc):
    url = "https://open.spotify.com/get_access_token?reason=transport&productType=web_player"
    headers = {
        "Cookie": f"sp_dc={sp_dc}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()["accessToken"]
    except Exception as e:
        print(f"Ошибка получения токена: {e}")
        return None

def get_auth_client():
    token = get_token_from_cookie(SP_DC_COOKIE)
    if not token:
        return None
    return spotipy.Spotify(auth=token)

def load_data():
    if os.path.exists(DATABASE_FILE):
        with open(DATABASE_FILE, 'r') as f:
            return json.load(f)
    return {"last_checked_date": "2000-01-01"}

def save_data(date_str):
    with open(DATABASE_FILE, 'w') as f:
        json.dump({"last_checked_date": date_str}, f)

def check_new_releases():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Начинаю проверку...")
    sp = get_auth_client()
    if not sp:
        print("Не удалось авторизоваться. Проверь cookie.")
        return

    data = load_data()
    last_date = data["last_checked_date"]
    new_tracks_uris = []
    latest_release_date = last_date
    
    try:
        results = sp.current_user_followed_artists(limit=50)
        artists = results['artists']['items']
        
        for artist in artists:
            # country="UA" — важно, чтобы трек был доступен в твоем регионе
            albums = sp.artist_albums(artist['id'], limit=5, country="UA")
            
            for album in albums['items']:
                release_date = album['release_date']
                if release_date > last_date:
                    print(f"Найдено новое: {artist['name']} - {album['name']} ({release_date})")
                    tracks = sp.album_tracks(album['id'])
                    for track in tracks['items']:
                        new_tracks_uris.append(track['uri'])
                    
                    if release_date > latest_release_date:
                        latest_release_date = release_date

        if new_tracks_uris:
            unique_uris = list(set(new_tracks_uris))
            # Разбиваем на пачки по 100 треков (ограничение API)
            for i in range(0, len(unique_uris), 100):
                sp.playlist_add_items(PLAYLIST_ID, unique_uris[i:i+100])
            
            print(f"Добавлено {len(unique_uris)} треков!")
            save_data(latest_release_date)
        else:
            print("Новых релизов нет.")
            
    except Exception as e:
        print(f"Произошла ошибка: {e}")

# ================= ЗАПУСК =================
if __name__ == "__main__":
    print("Бот запущен. Ожидаю расписания...")
    
    # Можно раскомментировать для теста при запуске:
    # check_new_releases()

    schedule.every().day.at("09:00").do(check_new_releases)
    schedule.every().day.at("21:00").do(check_new_releases)

    while True:
        schedule.run_pending()
        time.sleep(60)