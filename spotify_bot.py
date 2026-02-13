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
RUN_TIME = "03:00"  # Время запуска (раз в сутки)
SAFE_DELAY = 5      # Увеличил паузу для безопасности
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
        "last_checked_date": "2000-01-01",
        "last_run_timestamp": 0
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
    УМНЫЙ ПОИСК (БАЗА):
    Возвращает трек и дату. Тратит 2 запроса!
    """
    try:
        # ЗАПРОС 1
        results = sp.artist_albums(
            artist_id, 
            album_type='album,single', 
            country="UA", 
            limit=5
        )
        items = results['items']
        
        if not items: return None, None

        sorted_releases = sorted(items, key=lambda x: x['release_date'], reverse=True)
        latest_release = sorted_releases[0]
        
        # ЗАПРОС 2
        tracks = sp.album_tracks(latest_release['id'], limit=1)
        if tracks['items']:
            return tracks['items'][0]['uri'], latest_release['release_date']
            
    except Exception as e:
        if hasattr(e, 'http_status') and e.http_status == 429: raise e
    return None, None

def run_daily_safe_scan():
    state = load_state()
    
    # 1. Проверка: запускались ли сегодня?
    last_run = datetime.fromtimestamp(state.get("last_run_timestamp", 0))
    if last_run.date() == datetime.now().date() and state["initial_scan_done"]:
        print(f"[{datetime.now().strftime('%H:%M')}] ✋ Лимит на сегодня исчерпан (бот уже работал). Жду {RUN_TIME}.")
        return

    sp = get_spotify_client()
    print(f"\n[{datetime.now().strftime('%H:%M')}] 🚀 Ежедневный запуск (Лимит ~100)...")

    try:
        results = sp.current_user_followed_artists(limit=50)
        artists = results['artists']['items']
        while results['artists']['cursors']['after']:
            results = sp.current_user_followed_artists(limit=50, after=results['artists']['cursors']['after'])
            artists.extend(results['artists']['items'])
        
        print(f"   Подписок: {len(artists)}")
        
        requests_today = 0
        limit_reached = False

        # === РЕЖИМ 1: ПЕРВИЧНОЕ ЗАПОЛНЕНИЕ ===
        if not state["initial_scan_done"]:
            start_index = state["last_processed_index"]
            print(f"   📢 Продолжаю базу с {start_index+1}-го артиста.")
            latest_global_date = state["last_checked_date"]
            
            for i in range(start_index, len(artists)):
                # ПРОВЕРКА ЛИМИТА
                if requests_today >= 95:
                    print("\n   🛑 Дневной лимит (95 запросов) достигнут. Пауза до завтра.")
                    limit_reached = True
                    break

                artist = artists[i]
                print(f"   [{i+1}/{len(artists)}] {artist['name']}...", end="\r")
                
                # Тратим 2 запроса
                track_uri, release_date = get_latest_track_smart(sp, artist['id'])
                requests_today += 2
                
                if track_uri:
                    add_tracks_direct(sp, [track_uri])
                    if release_date > latest_global_date:
                        latest_global_date = release_date
                
                state["last_processed_index"] = i + 1
                state["last_checked_date"] = latest_global_date
                save_state(state)
                time.sleep(SAFE_DELAY)

            if not limit_reached:
                print("\n   ✅ База собрана! Завтра начнем искать новинки.")
                state["initial_scan_done"] = True
                state["last_processed_index"] = 0
                # Ставим метку, что на сегодня всё
                state["last_run_timestamp"] = datetime.now().timestamp()
                save_state(state)
            else:
                # Если уперлись в лимит, метку времени НЕ ставим, 
                # но так как requests_today > 95, он сам остановится в начале
                state["last_run_timestamp"] = datetime.now().timestamp()
                save_state(state)

        # === РЕЖИМ 2: НОВИНКИ (Single + Album) ===
        else:
            print(f"   📢 Ищу новинки (свежее {state['last_checked_date']})...")
            last_date = state["last_checked_date"]
            new_max_date = last_date
            found_tracks = []
            
            for i, artist in enumerate(artists):
                # ПРОВЕРКА ЛИМИТА
                if requests_today >= 95:
                    print("\n   ⚠️ Лимит 95 запросов. Останавливаю поиск на сегодня.")
                    break

                try:
                    # 1 ЗАПРОС
                    albums = sp.artist_albums(
                        artist['id'], 
                        limit=5, 
                        album_type='album,single', 
                        country="UA"
                    )
                    requests_today += 1
                    
                    for album in albums['items']:
                        if album['release_date'] > last_date:
                            print(f"   🔥 НОВИНКА: {artist['name']} - {album['name']}")
                            
                            # Качаем треки (Доп. запрос)
                            tracks = sp.album_tracks(album['id'], limit=50)
                            requests_today += 1
                            
                            for t in tracks['items']: 
                                found_tracks.append(t['uri'])
                            
                            if album['release_date'] > new_max_date:
                                new_max_date = album['release_date']
                    time.sleep(SAFE_DELAY)
                except Exception as e:
                    if handle_rate_limit(e): break

            if found_tracks:
                unique = list(set(found_tracks))
                print(f"   Заливаю {len(unique)} новых треков...")
                add_tracks_direct(sp, unique)
                state["last_checked_date"] = new_max_date
            else:
                print(f"   Новинок нет. Потрачено запросов: {requests_today}")
            
            # Записываем, что на сегодня работа выполнена
            state["last_run_timestamp"] = datetime.now().timestamp()
            save_state(state)

    except Exception as e:
        if not handle_rate_limit(e):
            print(f"\n❌ Ошибка: {e}")

if __name__ == "__main__":
    print(f"🤖 Бот запущен (Safe Mode: 1 раз в сутки в {RUN_TIME})")
    
    # Пробуем запустить сразу при старте (если сегодня еще не работал)
    run_daily_safe_scan()
    
    # Ставим в расписание
    schedule.every().day.at(RUN_TIME).do(run_daily_safe_scan)
    
    while True:
        schedule.run_pending()
        time.sleep(60)