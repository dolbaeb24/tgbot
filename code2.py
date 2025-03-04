import os
import logging
import spotipy
import asyncio
from datetime import time
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, CallbackContext, JobQueue
from telegram import ReplyKeyboardMarkup
from telegram.ext import MessageHandler, filters


# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    level=logging.INFO)

# Подключение к Spotify API
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=os.getenv("SPOTIPY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
    scope="user-read-currently-playing user-read-recently-played user-top-read"
))

last_track_id = None  # Глобальная переменная для отслеживания последнего трека
track_count = {}  # Словарь для хранения количества треков по chat_id
track_history = {}  # Словарь для хранения прослушанных треков по chat_id


async def check_now_playing(context: CallbackContext):
    """Функция проверяет текущий трек и отправляет сообщение пользователю"""
    global last_track_id
    job = context.job
    chat_id = job.chat_id  # Динамически берём ID из контекста задачи

    try:
        current_track = sp.currently_playing()

        if current_track and current_track["is_playing"]:
            track_id = current_track["item"]["id"]

            if track_id == last_track_id:
                return  # Если трек не изменился, ничего не делаем
            

            last_track_id = track_id  # Обновляем ID последнего трека

            # Получаем информацию о треке
            track_name = current_track["item"]["name"]
            artist_name = ", ".join(artist["name"] for artist in current_track["item"]["artists"])
            album_name = current_track["item"]["album"]["name"]
            track_url = current_track["item"]["external_urls"]["spotify"]
            cover_url = current_track["item"]["album"]["images"][0]["url"]

            # Обновляем историю прослушанных треков
            if chat_id not in track_history:
                track_history[chat_id] = []
            track_history[chat_id].append((track_name, artist_name))

            # Создаём сообщение
            message = (
                f"🎵 *Сейчас играет:*\n"
                f"🎧 *Трек:* [{track_name}]({track_url})\n"
                f"🎤 *Исполнитель:* {artist_name}\n"
                f"💿 *Альбом:* {album_name}"
            )

            # Отправляем сообщение в Telegram
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
    """Запуск бота и добавление задачи на отслеживание текущего трека"""
    chat_id = update.effective_chat.id
    job_queue = context.application.job_queue  

    if not job_queue:
        logging.error("JobQueue не инициализирован!")
        return

    existing_jobs = job_queue.get_jobs_by_name(str(chat_id))
    for job in existing_jobs:
        job.schedule_removal()

    job_queue.run_repeating(check_now_playing, interval=10, first=5, chat_id=chat_id, name=str(chat_id))

    await update.message.reply_text(
        "👋 Привет! Я твой *Spotify-бот* 🎵\n"
        "Я буду следить за твоей музыкой и отправлять уведомления! 🎶",
        parse_mode="Markdown",
        reply_markup=reply_menu()  # Добавляем клавиатуру с кнопкой "Меню"
    )




def get_top_tracks():
    """Получение топ-10 треков"""
    results = sp.current_user_top_tracks(limit=10, time_range='medium_term')
    tracks = [f"{idx+1}. *{item['name']}* - {item['artists'][0]['name']}"
              for idx, item in enumerate(results['items'])]
    return "\n".join(tracks) if tracks else "❌ Не удалось получить топ треков."


def get_top_artists():
    """Получение топ-10 артистов"""
    results = sp.current_user_top_artists(limit=10, time_range='medium_term')
    artists = [f"{idx+1}. *{item['name']}*" for idx, item in enumerate(results['items'])]
    return "\n".join(artists) if artists else "❌ Не удалось получить топ артистов."


async def button_handler(update: Update, context: CallbackContext):
    """Обработка кнопок"""
    query = update.callback_query
    await query.answer()

    if query.data == "stats":
        tracks = get_top_tracks()
        artists = get_top_artists()
        text = f"📊 *Твоя статистика Spotify:*\n\n🎶 *Топ-10 треков:*\n{tracks}\n\n🎤 *Топ-10 артистов:*\n{artists}"
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=back_menu())

    elif query.data == "top_tracks":
        tracks = get_top_tracks()
        await query.edit_message_text(f"🎶 *Твои топ-10 треков:*\n{tracks}", parse_mode="Markdown", reply_markup=back_menu())

    elif query.data == "top_artists":
        artists = get_top_artists()
        await query.edit_message_text(f"🎤 *Твои топ-10 артистов:*\n{artists}", parse_mode="Markdown", reply_markup=back_menu())
    
    elif query.data == "track_count":
        await query.edit_message_text(f"🎵 Сегодня вы прослушали {track_count.get(query.message.chat_id, 0)} треков! 🎵", reply_markup=back_menu())

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

def reply_menu():
    """Создает reply-клавиатуру с кнопкой для вызова меню"""
    return ReplyKeyboardMarkup(
        [["📋 Меню"]],  # Одна кнопка внизу чата
        resize_keyboard=True, one_time_keyboard=False
    )

async def show_menu(update: Update, context: CallbackContext):
    """Показывает главное меню при нажатии кнопки '📋 Меню'"""
    await update.message.reply_text(
        "📋 *Главное меню:*\n\n"
        "Выбери действие 👇",
        parse_mode="Markdown",
        reply_markup=main_menu()  # Используем инлайн-кнопки
    )


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

    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.Text("📋 Меню"), show_menu))


    logging.info("Бот запущен!")
    application.run_polling()


if __name__ == "__main__":
    main()
