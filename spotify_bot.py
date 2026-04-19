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
SAFE_DELAY = 5      # Увеличил паузу для надежности
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
    # Отключаем встроенные ретрии спотипая, чтобы он не засыпал на 24 часа внутри себя
    return spotipy.Spotify(auth_manager=auth_manager, retries=0, status_retries=0)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {
        "initial_scan_done": False,
        "last_processed_index": 0,
        "last_checked_date": "2000-01-01",
        "last_run_timestamp": 0,
        "artists_processed": {},  # {artist_id: last_release_date}
        "monitoring_index": 0  # для продолжения мониторинга
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
        
        # Если просят ждать слишком долго (больше часа) - лучше сдаться и попробовать завтра
        if retry_after > 3600:
            print(f"\n🛑 ЖЕСТКИЙ ЛИМИТ 429! Spotify просит подождать {retry_after} сек (отдыхаем до завтра).")
            return "STOP"
            
        print(f"\n⚠️ ЛИМИТ 429! Spotify просит подождать {retry_after} сек.")
        print("   💤 Сплю (не выключай меня)...")
        time.sleep(retry_after)
        return "RETRY"
    return False

def get_artist_releases(sp, artist_id, limit_per_type=10):
    """
    Получает релизы артиста (альбомы и синглы за один запрос).
    """
    all_releases = []
    offset = 0
    
    while len(all_releases) < limit_per_type * 2:
        try:
            # ОПТИМИЗАЦИЯ: запрашиваем всё сразу через запятую
            results = sp.artist_albums(
                artist_id,
                include_groups='album,single',
                country="UA",
                limit=10, # Spotify сейчас ругается на 20+
                offset=offset
            )
            
            items = results.get('items', [])
            if not items:
                break
            
            all_releases.extend(items)
            
            if results.get('next') is None or len(all_releases) >= limit_per_type * 2:
                break
            
            offset += 20
            
        except Exception as e:
            if hasattr(e, 'http_status') and e.http_status == 429:
                raise e
            print(f"      ⚠️ Ошибка при получении релизов: {e}")
            break
    
    return all_releases

def get_latest_track_smart(sp, artist_id):
    """
    ПЕРВИЧНОЕ ЗАПОЛНЕНИЕ: возвращает ТОЛЬКО 1 трек из последнего релиза.
    
    Возвращает: (track_uri, release_date) или (None, None)
    """
    try:
        releases = get_artist_releases(sp, artist_id, limit_per_type=10)
        
        if not releases:
            return None, None
        
        sorted_releases = sorted(
            releases, 
            key=lambda x: x.get('release_date', '0000-00-00'), 
            reverse=True
        )
        
        latest = sorted_releases[0]
        release_date = latest.get('release_date', '0000-00-00')
        
        tracks = sp.album_tracks(latest['id'], limit=1)
        
        if tracks['items']:
            return tracks['items'][0]['uri'], release_date
        
        return None, None
        
    except Exception as e:
        if hasattr(e, 'http_status') and e.http_status == 429:
            raise e
        return None, None

def run_daily_safe_scan():
    """ Основная функция бота """
    state = load_state()
    
    last_run = datetime.fromtimestamp(state.get("last_run_timestamp", 0))
    current_date = datetime.now().date()
    
    if last_run.date() == current_date and state["initial_scan_done"]:
        if state.get("monitoring_index", 0) == 0:
            print(f"[{datetime.now().strftime('%H:%M')}] ✋ Лимит на сегодня исчерпан (бот уже работал сегодня).")
            return
    
    sp = get_spotify_client()
    
    # ПРИНУДИТЕЛЬНОЕ ОБНОВЛЕНИЕ ТОКЕНА
    try:
        print("   🔐 Проверка авторизации...")
        sp.auth_manager.get_access_token(as_dict=False)
    except Exception as e:
        print(f"   ❌ Ошибка обновления токена: {e}")
        return
 
    # Фиксируем запуск ПЕРЕД началом (защита от дублей при сбое)
    if state.get("monitoring_index", 0) == 0:
        state["last_run_timestamp"] = datetime.now().timestamp()
        save_state(state)
 
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
        
        limit_reached = False

        # ═══════════════════════════════════════════════════════
        # РЕЖИМ 1: ПЕРВИЧНОЕ ЗАПОЛНЕНИЕ (ТОЛЬКО 1 ТРЕК)
        # ═══════════════════════════════════════════════════════
        if not state["initial_scan_done"]:
            print(f"\n   📢 ПЕРВЫЙ ЗАПУСК: Добавляю по 1 треку из последнего релиза...")
            start_index = state["last_processed_index"]
            latest_global_date = state["last_checked_date"]
            
            consecutive_errors = 0
            for i in range(start_index, len(artists)):
                artist = artists[i]
                print(f"   [{i+1}/{len(artists)}] {artist['name'][:30]}...", end=" ")
                
                try:
                    # Получаем 1 трек из последнего релиза
                    track_uri, release_date = get_latest_track_smart(sp, artist['id'])
                    
                    if track_uri and release_date:
                        # СРАЗУ добавляем трек в плейлист
                        add_tracks_direct(sp, [track_uri])
                        state["artists_processed"][artist['id']] = release_date
                        
                        if release_date > latest_global_date:
                            latest_global_date = release_date
                        
                        print(f"✅ [{release_date}]")
                    else:
                        print("⚠️ нет релизов")
                    
                    # СОХРАНЯЕМ ПРОГРЕСС после каждого артиста
                    state["last_processed_index"] = i + 1
                    state["last_checked_date"] = latest_global_date
                    save_state(state)
                    
                    consecutive_errors = 0 # Сброс при успехе
                    time.sleep(SAFE_DELAY)
                    
                except Exception as e:
                    consecutive_errors += 1
                    if consecutive_errors > 5:
                        print(f"\n🛑 Слишком много ошибок подряд ({consecutive_errors})! Останавливаюсь.")
                        return
                        
                    res = handle_rate_limit(e)
                    if res == "RETRY":
                        i -= 1
                        continue
                    elif res == "STOP":
                        return
                    print(f"❌ {e}")

            # Первичное сканирование ПОЛНОСТЬЮ завершено
            print(f"\n   ✅ База собрана! Завтра начнем искать новинки.")
            state["initial_scan_done"] = True
            state["last_processed_index"] = 0
            state["last_run_timestamp"] = datetime.now().timestamp()
            save_state(state)

        # ═══════════════════════════════════════════════════════
        # РЕЖИМ 2: МОНИТОРИНГ НОВИНОК (ВСЕ ТРЕКИ ИЗ НОВЫХ РЕЛИЗОВ)
        # ═══════════════════════════════════════════════════════
        else:
            # Проверяем, это новый день или продолжение сегодняшнего
            start_monitoring_index = 0
            if last_run.date() == current_date:
                # Продолжаем с того места, где остановились
                start_monitoring_index = state.get("monitoring_index", 0)
                if start_monitoring_index > 0:
                    print(f"\n   🔄 ПРОДОЛЖАЮ мониторинг с артиста #{start_monitoring_index+1}")
            else:
                # Новый день - начинаем сначала
                state["monitoring_index"] = 0
                start_monitoring_index = 0
                print(f"\n   📢 МОНИТОРИНГ: Ищу новинки (свежее {state['last_checked_date']})...")
            
            last_global_date = state["last_checked_date"]
            new_max_date = last_global_date
            
            consecutive_errors = 0
            i = start_monitoring_index
            while i < len(artists):
                artist = artists[i]
                artist_id = artist['id']
                artist_last_date = state["artists_processed"].get(artist_id, "2000-01-01")
                
                print(f"   [{i+1}/{len(artists)}] {artist['name'][:30]}...", end=" ")
                
                try:
                    # Получаем релизы артиста
                    releases = get_artist_releases(sp, artist_id, limit_per_type=5)
                    
                    found_new = False
                    for release in releases:
                        release_date = release.get('release_date', '0000-00-00')
                        
                        # Проверяем: это новый релиз?
                        if release_date > artist_last_date:
                            release_type = release.get('album_type', 'release')
                            print(f"\n      🔥 НОВИНКА: {release['name']} [{release_type}] [{release_date}]")
                            
                            # Получаем ВСЕ ТРЕКИ из нового релиза
                            tracks = sp.album_tracks(release['id'], limit=50)
                            
                            track_uris = [t['uri'] for t in tracks['items']]
                            track_count = len(track_uris)
                            print(f"         ➕ Добавляю {track_count} треков...")
                            
                            # СРАЗУ добавляем в плейлист!
                            add_tracks_direct(sp, track_uris)
                            
                            found_new = True
                            
                            # Обновляем дату артиста
                            if release_date > state["artists_processed"].get(artist_id, "2000-01-01"):
                                state["artists_processed"][artist_id] = release_date
                            
                            # Обновляем глобальную максимальную дату
                            if release_date > new_max_date:
                                new_max_date = release_date
                            
                            # СОХРАНЯЕМ после каждой находки
                            state["last_checked_date"] = new_max_date
                            save_state(state)
                    
                    if not found_new:
                        print("—")
                    
                    consecutive_errors = 0 # Сброс при успехе
                    time.sleep(SAFE_DELAY)
                    i += 1
                    
                except Exception as e:
                    consecutive_errors += 1
                    if consecutive_errors > 5:
                        print(f"\n🛑 Слишком много ошибок подряд ({consecutive_errors})! Останавливаюсь.")
                        return

                    res = handle_rate_limit(e)
                    if res == "RETRY":
                        state["monitoring_index"] = i
                        state["last_checked_date"] = new_max_date
                        save_state(state)
                        continue
                    elif res == "STOP":
                        return
                    print(f"❌ {e}")
                    i += 1

            # Мониторинг ПОЛНОСТЬЮ завершен
            print(f"\n   ✅ Мониторинг завершен!")
            
            # Сбрасываем индекс мониторинга (на следующий день начнем сначала)
            state["monitoring_index"] = 0
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
    print(f"   • СРАЗУ добавляет трек в плейлист (не ждет конца)")
    print(f"   • Сохраняет прогресс после каждого артиста")
    print(f"   • Если Spotify даст 429 - ждет и продолжает (не останавливается)")
    print(f"")
    print(f"2️⃣  ЕЖЕДНЕВНЫЙ МОНИТОРИНГ:")
    print(f"   • Проверяет ОДИН РАЗ В СУТКИ всех артистов")
    print(f"   • Если находит новый релиз - СРАЗУ добавляет ВСЕ треки")
    print(f"   • Если Spotify даст 429 - ждет и продолжает (не останавливается)")
    print(f"   • Сохраняет прогресс после каждой находки")
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
