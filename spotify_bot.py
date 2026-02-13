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
SAFE_DELAY = 3      # Пауза между запросами (секунды)
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
        "last_run_timestamp": 0,
        "artists_processed": {}  # {artist_id: last_release_date}
    }

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

def add_tracks_direct(sp, track_uris):
    """Добавляет треки в плейлист пачками по 50"""
    if not track_uris: 
        return
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
    """Обрабатывает ошибку rate limit от Spotify"""
    if hasattr(e, 'http_status') and e.http_status == 429:
        retry_after = int(e.headers.get('Retry-After', 60)) + 5
        print(f"\n⚠️ ЛИМИТ! Spotify просит подождать {retry_after} сек.")
        print("   💤 Сплю (не выключай меня)...")
        time.sleep(retry_after)
        return True
    return False

def get_artist_releases(sp, artist_id, limit_per_type=20):
    """
    Получает релизы артиста (альбомы и синглы).
    
    ИСПРАВЛЕНО:
    - Используем include_groups вместо album_type
    - limit=10 (максимум для artist_albums API)
    - Отдельные запросы для 'album' и 'single'
    - Пагинация для получения нужного количества релизов
    """
    all_releases = []
    
    for release_type in ['album', 'single']:
        offset = 0
        type_releases = []
        
        while len(type_releases) < limit_per_type:
            try:
                # КРИТИЧНО: limit максимум 10 для artist_albums!
                results = sp.artist_albums(
                    artist_id,
                    include_groups=release_type,  # ← ПРАВИЛЬНЫЙ ПАРАМЕТР!
                    country="UA",
                    limit=10,  # ← МАКСИМУМ 10!
                    offset=offset
                )
                
                items = results.get('items', [])
                if not items:
                    break
                
                type_releases.extend(items)
                
                # Если больше нет страниц
                if results.get('next') is None:
                    break
                
                offset += 10
                
            except Exception as e:
                if hasattr(e, 'http_status') and e.http_status == 429:
                    raise e  # Пробрасываем rate limit наверх
                print(f"      ⚠️ Ошибка при получении {release_type}: {e}")
                break
        
        all_releases.extend(type_releases[:limit_per_type])
    
    return all_releases

def get_latest_track_smart(sp, artist_id):
    """
    ПЕРВИЧНОЕ ЗАПОЛНЕНИЕ: возвращает ТОЛЬКО 1 трек из последнего релиза.
    
    Возвращает: (track_uri, release_date) или (None, None)
    """
    try:
        # Получаем последние релизы
        releases = get_artist_releases(sp, artist_id, limit_per_type=10)
        
        if not releases:
            return None, None
        
        # Сортируем по дате (самые свежие первыми)
        sorted_releases = sorted(
            releases, 
            key=lambda x: x.get('release_date', '0000-00-00'), 
            reverse=True
        )
        
        latest = sorted_releases[0]
        release_date = latest.get('release_date', '0000-00-00')
        
        # Получаем ТОЛЬКО ПЕРВЫЙ трек из релиза
        tracks = sp.album_tracks(latest['id'], limit=1)
        
        if tracks['items']:
            return tracks['items'][0]['uri'], release_date
        
        return None, None
        
    except Exception as e:
        if hasattr(e, 'http_status') and e.http_status == 429:
            raise e
        return None, None

def run_daily_safe_scan():
    """
    Основная функция бота - ежедневное сканирование.
    
    КАК РАБОТАЕТ:
    
    1. ПЕРВЫЙ ЗАПУСК (initial_scan_done=false):
       - Добавляет ПО 1 ТРЕКУ из последнего релиза каждого артиста
       - Это "база" от которой отталкиваться
       - Если влетит в лимит - продолжит на следующий день
    
    2. МОНИТОРИНГ (initial_scan_done=true):
       - Проверяет ОДИН РАЗ В СУТКИ всех артистов
       - Если находит новый релиз (альбом/сингл/EP) - добавляет ВСЕ треки
       - Да, если в альбоме 30 треков - добавит все 30!
       - Если влетит в лимит - продолжит завтра с того места
    """
    state = load_state()
    
    # ═══════════════════════════════════════════════════════
    # ЖЕСТКИЙ ЛИМИТ: ОДИН РАЗ В 24 ЧАСА
    # ═══════════════════════════════════════════════════════
    last_run = datetime.fromtimestamp(state.get("last_run_timestamp", 0))
    if last_run.date() == datetime.now().date() and state["initial_scan_done"]:
        print(f"[{datetime.now().strftime('%H:%M')}] ✋ Лимит на сегодня исчерпан (бот уже работал сегодня).")
        print(f"   ⏰ Следующий запуск в {RUN_TIME}")
        return
    
    sp = get_spotify_client()
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🚀 Запуск сканирования...")

    try:
        # Получаем список всех артистов
        print("   📡 Загружаю список подписок...")
        results = sp.current_user_followed_artists(limit=50)
        artists = results['artists']['items']
        
        while results['artists']['cursors']['after']:
            results = sp.current_user_followed_artists(
                limit=50, 
                after=results['artists']['cursors']['after']
            )
            artists.extend(results['artists']['items'])
        
        print(f"   ✅ Подписок: {len(artists)}")
        
        requests_count = 0
        limit_reached = False

        # ═══════════════════════════════════════════════════════
        # РЕЖИМ 1: ПЕРВИЧНОЕ ЗАПОЛНЕНИЕ (ТОЛЬКО 1 ТРЕК)
        # ═══════════════════════════════════════════════════════
        if not state["initial_scan_done"]:
            print(f"\n   📢 ПЕРВЫЙ ЗАПУСК: Добавляю по 1 треку из последнего релиза...")
            start_index = state["last_processed_index"]
            latest_global_date = state["last_checked_date"]
            
            for i in range(start_index, len(artists)):
                # Защита от превышения лимитов
                if requests_count >= 95:
                    print(f"\n   🛑 Дневной лимит (95 запросов) достигнут.")
                    print(f"   💾 Сохраняю прогресс: {i}/{len(artists)} артистов обработано")
                    limit_reached = True
                    break

                artist = artists[i]
                print(f"   [{i+1}/{len(artists)}] {artist['name'][:30]}...", end=" ")
                
                try:
                    # Получаем 1 трек из последнего релиза
                    track_uri, release_date = get_latest_track_smart(sp, artist['id'])
                    requests_count += 4  # ~4 запроса на артиста
                    
                    if track_uri and release_date:
                        add_tracks_direct(sp, [track_uri])
                        state["artists_processed"][artist['id']] = release_date
                        
                        if release_date > latest_global_date:
                            latest_global_date = release_date
                        
                        print(f"✅ [{release_date}]")
                    else:
                        print("⚠️ нет релизов")
                    
                    state["last_processed_index"] = i + 1
                    state["last_checked_date"] = latest_global_date
                    save_state(state)
                    
                    time.sleep(SAFE_DELAY)
                    
                except Exception as e:
                    if handle_rate_limit(e):
                        limit_reached = True
                        break
                    print(f"❌ {e}")

            if not limit_reached:
                # Первичное сканирование ПОЛНОСТЬЮ завершено
                print(f"\n   ✅ База собрана! Завтра начнем искать новинки.")
                state["initial_scan_done"] = True
                state["last_processed_index"] = 0
                state["last_run_timestamp"] = datetime.now().timestamp()
                save_state(state)
            else:
                # Влетели в лимит - продолжим завтра
                state["last_run_timestamp"] = datetime.now().timestamp()
                save_state(state)

        # ═══════════════════════════════════════════════════════
        # РЕЖИМ 2: МОНИТОРИНГ НОВИНОК (ВСЕ ТРЕКИ ИЗ НОВЫХ РЕЛИЗОВ)
        # ═══════════════════════════════════════════════════════
        else:
            print(f"\n   📢 МОНИТОРИНГ: Ищу новинки (свежее {state['last_checked_date']})...")
            last_global_date = state["last_checked_date"]
            new_max_date = last_global_date
            new_tracks = []
            
            for i, artist in enumerate(artists):
                # Защита от превышения лимитов
                if requests_count >= 95:
                    print(f"\n   ⚠️ Лимит 95 запросов. Останавливаю до завтра.")
                    break

                artist_id = artist['id']
                artist_last_date = state["artists_processed"].get(artist_id, "2000-01-01")
                
                print(f"   [{i+1}/{len(artists)}] {artist['name'][:30]}...", end=" ")
                
                try:
                    # Получаем релизы артиста
                    releases = get_artist_releases(sp, artist_id, limit_per_type=5)
                    requests_count += 2  # 2 запроса (album + single)
                    
                    found_new = False
                    for release in releases:
                        release_date = release.get('release_date', '0000-00-00')
                        
                        # Проверяем: это новый релиз?
                        if release_date > artist_last_date:
                            release_type = release.get('album_type', 'release')
                            print(f"\n      🔥 НОВИНКА: {release['name']} [{release_type}] [{release_date}]")
                            
                            # Получаем ВСЕ ТРЕКИ из нового релиза
                            tracks = sp.album_tracks(release['id'], limit=50)
                            requests_count += 1
                            
                            track_count = len(tracks['items'])
                            print(f"         ➕ Добавляю {track_count} треков из релиза")
                            
                            for t in tracks['items']:
                                new_tracks.append(t['uri'])
                            
                            found_new = True
                            
                            # Обновляем дату артиста
                            if release_date > state["artists_processed"].get(artist_id, "2000-01-01"):
                                state["artists_processed"][artist_id] = release_date
                            
                            # Обновляем глобальную максимальную дату
                            if release_date > new_max_date:
                                new_max_date = release_date
                    
                    if not found_new:
                        print("—")
                    
                    time.sleep(SAFE_DELAY)
                    
                except Exception as e:
                    if handle_rate_limit(e):
                        break
                    print(f"❌ {e}")

            # Итоги
            print(f"\n   📊 ИТОГО:")
            print(f"   • Запросов использовано: {requests_count}")
            print(f"   • Новых треков найдено: {len(new_tracks)}")
            
            if new_tracks:
                unique_tracks = list(set(new_tracks))
                print(f"   ⬆️ Добавляю {len(unique_tracks)} уникальных треков в плейлист...")
                add_tracks_direct(sp, unique_tracks)
                state["last_checked_date"] = new_max_date
            else:
                print(f"   💤 Новинок нет")
            
            # Записываем метку времени (работа на сегодня выполнена)
            state["last_run_timestamp"] = datetime.now().timestamp()
            save_state(state)

    except Exception as e:
        if not handle_rate_limit(e):
            print(f"\n❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    print(f"🤖 Spotify Bot запущен!")
    print(f"⏰ Расписание: каждый день в {RUN_TIME}")
    print(f"📁 Файл состояния: {STATE_FILE}")
    print(f"🎵 Плейлист ID: {PLAYLIST_ID}")
    print(f"\n{'═'*60}")
    print(f"КАК РАБОТАЕТ БОТ:")
    print(f"{'═'*60}")
    print(f"1️⃣  ПЕРВЫЙ ЗАПУСК:")
    print(f"   • Добавляет ПО 1 ТРЕКУ из последнего релиза каждого артиста")
    print(f"   • Создает 'базу' для отслеживания новинок")
    print(f"   • Если влетит в лимит - продолжит завтра")
    print(f"")
    print(f"2️⃣  ЕЖЕДНЕВНЫЙ МОНИТОРИНГ:")
    print(f"   • Проверяет ОДИН РАЗ В СУТКИ всех артистов")
    print(f"   • Если находит новый релиз - добавляет ВСЕ треки")
    print(f"   • Да, если альбом на 30 треков - добавит все 30!")
    print(f"   • Если влетит в лимит - продолжит на следующий день")
    print(f"")
    print(f"⚠️  ЖЕСТКИЙ ЛИМИТ: Один запуск в 24 часа (защита от Spotify)")
    print(f"{'═'*60}\n")
    
    # Запускаем сразу при старте (если сегодня еще не работал)
    run_daily_safe_scan()
    
    # Ставим в расписание
    schedule.every().day.at(RUN_TIME).do(run_daily_safe_scan)
    
    print(f"\n⏳ Ожидаю следующего запуска...")
    while True:
        schedule.run_pending()
        time.sleep(60)
