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
# Пауза между запросами (сек), чтобы не злить Spotify
SAFE_DELAY = 2 
# =============================================

load_dotenv()

# Проверка ключей
if not os.getenv("SPOTIPY_CLIENT_ID") or not os.getenv("PLAYLIST_ID"):
    print("❌ ОШИБКА: Проверь файл .env (CLIENT_ID или PLAYLIST_ID пусты)")
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
    # Начальное состояние
    return {
        "initial_scan_done": False,       # Завершен ли первый проход?
        "last_processed_index": 0,        # На каком артисте остановились
        "last_checked_date": "2000-01-01" # Дата последней проверки новинок
    }

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

def add_tracks_direct(sp, track_uris):
    """Добавляет треки через новый API (/items)"""
    if not track_uris: return
    try:
        # Разбиваем на пачки по 50
        for i in range(0, len(track_uris), 50):
            chunk = track_uris[i:i+50]
            url = f"playlists/{PLAYLIST_ID}/items"
            sp._post(url, payload={"uris": chunk})
            print(f"   ✅ Добавлено {len(chunk)} треков в плейлист.")
    except Exception as e:
        print(f"   ❌ Ошибка добавления: {e}")

def handle_rate_limit(e):
    """Умная обработка лимитов"""
    if hasattr(e, 'http_status') and e.http_status == 429:
        retry_after = int(e.headers.get('Retry-After', 60)) + 5
        print(f"\n⚠️ ЛИМИТ ЗАПРОСОВ! Spotify просит подождать {retry_after} сек.")
        print("   💤 Сплю...")
        time.sleep(retry_after)
        return True
    return False

def get_latest_track(sp, artist_id):
    """Получает 1 последний трек артиста"""
    try:
        # Ищем альбомы (Украина)
        albums = sp.artist_albums(artist_id, album_type='album,single', country="UA", limit=1)
        if albums['items']:
            latest_album = albums['items'][0]
            tracks = sp.album_tracks(latest_album['id'], limit=1)
            if tracks['items']:
                return tracks['items'][0]['uri'], latest_album['release_date']
    except Exception as e:
        # Если словили лимит внутри функции - пробрасываем наверх
        if hasattr(e, 'http_status') and e.http_status == 429:
            raise e
        print(f"   Ошибка трека: {e}")
    return None, None

def run_smart_scan():
    """Основная логика: Либо докачивает старое, либо ищет новое"""
    state = load_state()
    sp = get_spotify_client()
    
    print(f"\n[{datetime.now().strftime('%H:%M')}] 🚀 Запуск сканирования...")

    try:
        # 1. Получаем всех подписки
        results = sp.current_user_followed_artists(limit=50)
        artists = results['artists']['items']
        while results['artists']['cursors']['after']:
            results = sp.current_user_followed_artists(limit=50, after=results['artists']['cursors']['after'])
            artists.extend(results['artists']['items'])
        
        print(f"   Всего подписок: {len(artists)}")

        # === РЕЖИМ 1: ПЕРВИЧНОЕ ЗАПОЛНЕНИЕ ===
        if not state["initial_scan_done"]:
            start_index = state["last_processed_index"]
            print(f"   📢 РЕЖИМ: Первое заполнение. Продолжаю с {start_index+1}-го артиста.")
            
            latest_global_date = state["last_checked_date"]
            
            for i in range(start_index, len(artists)):
                artist = artists[i]
                print(f"   [{i+1}/{len(artists)}] {artist['name']}...", end="\r")
                
                track_uri, release_date = get_latest_track(sp, artist['id'])
                
                if track_uri:
                    # Сразу добавляем, чтобы не потерять при сбое
                    add_tracks_direct(sp, [track_uri])
                    if release_date > latest_global_date:
                        latest_global_date = release_date
                
                # Сохраняем прогресс ПОСЛЕ КАЖДОГО успешного шага
                state["last_processed_index"] = i + 1
                state["last_checked_date"] = latest_global_date
                save_state(state)
                
                time.sleep(SAFE_DELAY) # Бережем лимиты

            # Если дошли до конца без ошибок
            print("\n   ✅ Первичное заполнение завершено!")
            state["initial_scan_done"] = True
            state["last_processed_index"] = 0
            save_state(state)

        # === РЕЖИМ 2: ПРОВЕРКА НОВИНОК ===
        else:
            print(f"   📢 РЕЖИМ: Поиск новинок (свежее {state['last_checked_date']})")
            last_date = state["last_checked_date"]
            new_max_date = last_date
            found_tracks = []
            
            for i, artist in enumerate(artists):
                # Для новинок проверяем быстрее (только дату)
                try:
                    albums = sp.artist_albums(artist['id'], limit=2, country="UA")
                    for album in albums['items']:
                        if album['release_date'] > last_date:
                            print(f"   🔥 НОВИНКА: {artist['name']} - {album['name']}")
                            tracks = sp.album_tracks(album['id'], limit=5)
                            for t in tracks['items']: found_tracks.append(t['uri'])
                            
                            if album['release_date'] > new_max_date:
                                new_max_date = album['release_date']
                    time.sleep(0.5) # Маленькая пауза
                except Exception as e:
                    if handle_rate_limit(e): 
                        # Если лимит, просто выходим из функции, сохранимся и продолжим в след раз
                        return 

            if found_tracks:
                unique_tracks = list(set(found_tracks))
                print(f"   Добавляю {len(unique_tracks)} новых треков...")
                add_tracks_direct(sp, unique_tracks)
                state["last_checked_date"] = new_max_date
                save_state(state)
            else:
                print("   Новинок не найдено.")

    except Exception as e:
        if handle_rate_limit(e):
            pass # Уже обработали сон
        else:
            print(f"\n❌ Критическая ошибка: {e}")

if __name__ == "__main__":
    print("🤖 Бот запущен (v3.0 Smart Resume)")
    
    # Запускаем сразу при старте
    run_smart_scan()

    # Планировщик на будущее
    schedule.every().day.at("09:00").do(run_smart_scan)
    schedule.every().day.at("21:00").do(run_smart_scan)
    
    # Каждые 6 часов тоже проверим, на всякий случай (умный режим не спамит)
    schedule.every(6).hours.do(run_smart_scan)

    while True:
        schedule.run_pending()
        time.sleep(60)