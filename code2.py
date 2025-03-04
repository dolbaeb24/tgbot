import os
import logging
import spotipy
import asyncio
import threading
import time
from multiprocessing import Process
from flask import Flask, request
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, CallbackContext, JobQueue
from telegram import ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import MessageHandler, filters

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# Глобальное хранилище токенов пользователей (лучше заменить на БД)
user_tokens = {}

# Flask-сервер для обработки авторизации
app = Flask(__name__)

@app.route("/callback")
def spotify_callback():
    """Обрабатывает редирект от Spotify"""
    auth_manager = SpotifyOAuth(
        client_id=os.getenv("SPOTIPY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
        scope="user-read-currently-playing user-read-recently-played user-top-read"
    )
    
    code = request.args.get("code")
    chat_id = request.args.get("state")

    if code:
        token_info = auth_manager.get_access_token(code, as_dict=True)
        user_tokens[chat_id] = token_info  # Сохраняем токен
        return "✅ Авторизация прошла успешно! Вернитесь в Telegram."
    
    return "❌ Ошибка авторизации."


# Запуск Flask в отдельном потоке
def run_flask():
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False) 

def refresh_spotify_token(chat_id):
    """Обновляет токен Spotify, если он истёк"""
    if chat_id not in user_tokens:
        return None
    
    token_info = user_tokens[chat_id]
    if token_info["expires_at"] - 60 < time.time():  # Если истекает скоро
        auth_manager = SpotifyOAuth(
            client_id=os.getenv("SPOTIPY_CLIENT_ID"),
            client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
            redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
            scope="user-read-currently-playing user-read-recently-played user-top-read"
        )
        token_info = auth_manager.refresh_access_token(token_info["refresh_token"])
        user_tokens[chat_id] = token_info  # Обновляем в глобальной памяти

    return token_info["access_token"]


async def start_auth(update: Update, context: CallbackContext):
    """Отправляет ссылку для авторизации в Spotify"""
    chat_id = str(update.message.chat_id)
    auth_manager = SpotifyOAuth(
        client_id=os.getenv("SPOTIPY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
        scope="user-read-currently-playing user-read-recently-played user-top-read",
        state=chat_id  # Привязываем авторизацию к пользователю
    )
    auth_url = auth_manager.get_authorize_url()
    await update.message.reply_text(f"🎵 Авторизуйтесь в Spotify: [ЖМИ]({auth_url})", parse_mode="Markdown")

last_track_id = {} # Словарь для хранения ID последнего трека по chat_id
track_count = {}  # Словарь для хранения количества треков по chat_id
track_history = {}  # Словарь для хранения прослушанных треков по chat_id


async def check_now_playing(context: CallbackContext):
    """Проверяет текущий трек и отправляет сообщение пользователю"""
    global last_track_id, track_history
    job = context.job
    chat_id = job.data

    try:
        access_token = refresh_spotify_token(chat_id)
        if not access_token:
            return  

        sp = spotipy.Spotify(auth=access_token)
        current_track = sp.currently_playing()

        if current_track and current_track["is_playing"]:
            track_id = current_track["item"]["id"]

            if chat_id in last_track_id and track_id == last_track_id[chat_id]:
                return  

            last_track_id[chat_id] = track_id  

            track_name = current_track["item"]["name"]
            artist_name = ", ".join(artist["name"] for artist in current_track["item"]["artists"])
            cover_url = current_track["item"]["album"]["images"][0]["url"]
            track_url = current_track["item"]["external_urls"]["spotify"]

            # Сохраняем историю треков
            if chat_id not in track_history:
                track_history[chat_id] = []
            track_history[chat_id].append((track_name, artist_name))

            message = (
                f"🎵 *Сейчас играет:*\n"
                f"🎧 *Трек:* [{track_name}]({track_url})\n"
                f"🎤 *Исполнитель:* {artist_name}"
            )

            await context.bot.send_photo(
                chat_id=chat_id,
                photo=cover_url,
                caption=message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("▶ Открыть в Spotify", url=track_url)]
                ])
            )

    except Exception as e:
        logging.error(f"Ошибка при получении текущего трека: {e}")


async def send_daily_report(context: CallbackContext):
    """Отправляет отчет о количестве прослушанных треков за день"""
    for chat_id, tracks in track_history.items():
        if not tracks:
            await context.bot.send_message(chat_id, "📊 Сегодня вы не слушали музыку. 🎵")
            continue

        track_count = len(tracks)  # Количество прослушанных треков
        track_list = "\n".join([f"{idx + 1}. {track} - {artist}" for idx, (track, artist) in enumerate(tracks)])

        message = (
            f"📊 *Ежедневный отчет Spotify* 🎵\n\n"
            f"🔢 *Количество треков:* {track_count}\n\n"
            f"🎶 *Список прослушанных треков:* \n{track_list}"
        )

        await context.bot.send_message(chat_id, message, parse_mode="Markdown")

    track_history.clear()  # Сбрасываем историю прослушанных треков

async def start(update: Update, context: CallbackContext):
    """Запуск бота и отслеживание треков"""
    chat_id = update.effective_chat.id
    job_queue = context.job_queue  

    # Удаляем старые задачи
    for job in job_queue.jobs():
        if job.name == str(chat_id):
            job.schedule_removal()

    # Запускаем новую задачу
    job_queue.run_repeating(check_now_playing, interval=10, first=5, data=chat_id, name=str(chat_id))

    await update.message.reply_text(
        "👋 Привет! Я твой *Spotify-бот* 🎵\n"
        "Я помогу тебе узнать твою музыкальную статистику! 📊\n\n"
        "Выбери действие из меню ниже 👇",
        reply_markup=reply_menu(), parse_mode="Markdown"
    )


async def show_menu(update: Update, context: CallbackContext):
    """Показывает главное меню при нажатии кнопки '📋 Меню'"""
    await update.message.reply_text(
        "📋 *Главное меню:*\n\n"
        "Выбери действие 👇",
        parse_mode="Markdown",
        reply_markup=main_menu()  # Используем инлайн-кнопки
    )

def reply_menu():
    """Создает reply-клавиатуру с кнопкой для вызова меню"""
    return ReplyKeyboardMarkup(
        [["📋 Меню"]],  # Одна кнопка внизу чата
        resize_keyboard=True, one_time_keyboard=False
    )

def get_top_tracks(chat_id):
    if chat_id not in user_tokens:
        return "❌ Авторизуйтесь через /start_auth"
    sp = spotipy.Spotify(auth=user_tokens[chat_id]["access_token"])
    results = sp.current_user_top_tracks(limit=10, time_range='medium_term')
    tracks = [
        f"{idx+1}. *{item['name']}* - {item['artists'][0]['name']}"
        for idx, item in enumerate(results['items'])
    ]
    return "\n".join(tracks) if tracks else "❌ Не удалось получить топ треков."

def get_top_artists(chat_id):
    if chat_id not in user_tokens:
        return "❌ Авторизуйтесь через /start_auth"
    sp = spotipy.Spotify(auth=user_tokens[chat_id]["access_token"])
    results = sp.current_user_top_artists(limit=10, time_range='medium_term')
    artists = [f"{idx+1}. *{item['name']}*" for idx, item in enumerate(results['items'])]
    return "\n".join(artists) if artists else "❌ Не удалось получить топ артистов."



async def button_handler(update: Update, context: CallbackContext):
    """Обработка кнопок"""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id  # Добавлено

    if query.data == "stats":
        tracks = get_top_tracks(chat_id)
        artists = get_top_artists(chat_id)
        text = f"📊 *Твоя статистика Spotify:*\n\n🎶 *Топ-10 треков:*\n{tracks}\n\n🎤 *Топ-10 артистов:*\n{artists}"
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=back_menu())

    elif query.data == "top_tracks":
        tracks = get_top_tracks(chat_id)
        await query.edit_message_text(f"🎶 *Твои топ-10 треков:*\n{tracks}", parse_mode="Markdown", reply_markup=back_menu())

    elif query.data == "top_artists":
        artists = get_top_artists(chat_id)
        await query.edit_message_text(f"🎤 *Твои топ-10 артистов:*\n{artists}", parse_mode="Markdown", reply_markup=back_menu())
    
    elif query.data == "track_count":
        chat_id = query.message.chat.id
        tracks = track_history.get(chat_id, [])

        if not tracks:
            message = "📊 Сегодня вы ещё не слушали музыку. 🎵"
        else:
            track_list = "\n".join([f"🎵 *{track}* — {artist}" for track, artist in tracks])
            message = (
                f"📊 *Сегодняшний отчёт Spotify* 🎶\n\n"
                f"🔢 *Количество треков:* {len(tracks)}\n\n"
                f"🎧 *Список прослушанных треков:*\n{track_list}"
            )

        await query.edit_message_text(message, parse_mode="Markdown", reply_markup=back_menu())

    elif query.data == "about":
        await query.edit_message_text(
            "ℹ️ *О боте*\n\n"
            "Я анализирую твои музыкальные предпочтения в Spotify 🎼\n"
            "🔹 Показываю твои топ-10 треков 🎶\n"
            "🔹 Показываю твоих топ-10 артистов 🎤\n"
            "🔹 Отправляю уведомления о текущем треке 🎧\n\n",
            parse_mode="Markdown", reply_markup=back_menu()
        )

    elif query.data == "back":
        await query.message.edit_text(
            "👋 Привет! Я твой *Spotify-бот* 🎵\n"
            "Я помогу тебе узнать твою музыкальную статистику! 📊\n\n"
            "Выбери действие из меню ниже 👇",
            parse_mode="Markdown",
            reply_markup=main_menu()
        )


def back_menu():
    """Меню с кнопкой 'Назад'"""
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back")]])


def main_menu():
    """Главное меню"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Моя статистика", callback_data="stats")],
        [InlineKeyboardButton("🎶 Топ-10 треков", callback_data="top_tracks"),
         InlineKeyboardButton("🎤 Топ-10 артистов", callback_data="top_artists")],
        [InlineKeyboardButton("🎵 Сегодня прослушано треков", callback_data="track_count")],
        [InlineKeyboardButton("ℹ️ О боте", callback_data="about")]
    ])


def main():
    """Запуск бота"""
    application = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    job_queue = application.job_queue  # Добавляем job_queue

    # Запускаем Flask в отдельном процессе
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("start_auth", start_auth))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.Text("📋 Меню"), show_menu))


    logging.info("Бот запущен!")
    application.run_polling()

    flask_thread.terminate()  # Убиваем Flask при остановке бота


if __name__ == "__main__":
    main()
    while True:  # Держим процесс запущенным
            time.sleep(10)