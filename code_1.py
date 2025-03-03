import os
import logging
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackContext

from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Настройка Spotify API
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=os.getenv("SPOTIPY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
    redirect_uri="http://localhost:8888/callback",
    scope="user-read-recently-played user-top-read"
))

# Телеграм токен
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

async def start(update: Update, context: CallbackContext):
    """Приветственное сообщение"""
    keyboard = [[InlineKeyboardButton("🎧 Текущий трек", callback_data='current_track')],
                [InlineKeyboardButton("📊 Топ-исполнители", callback_data='top_artists')],
                [InlineKeyboardButton("🎵 Топ-треки", callback_data='top_tracks')],
                [InlineKeyboardButton("⏳ Время прослушивания", callback_data='listening_time')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text("Привет! Я бот для статистики Spotify. Выбери команду:", reply_markup=reply_markup)

async def current_track(update: Update, context: CallbackContext):
    """Отправляет информацию о текущем треке"""
    query = update.callback_query
    await query.answer()

    track = sp.current_playback()
    if track and track["is_playing"]:
        track_name = track["item"]["name"]
        artist_name = track["item"]["artists"][0]["name"]
        link = track["item"]["external_urls"]["spotify"]
        await query.message.reply_text(f"🎵 Сейчас играет: [{track_name} - {artist_name}]({link})", parse_mode="Markdown")
    else:
        await query.message.reply_text("❌ Музыка не играет.")

async def top_artists(update: Update, context: CallbackContext):
    """Отправляет топ-исполнителей"""
    query = update.callback_query
    await query.answer()

    top_artists = sp.current_user_top_artists(limit=5, time_range='medium_term')
    text = "📊 Твои топ-5 исполнителей:\n\n"
    for idx, artist in enumerate(top_artists["items"], start=1):
        text += f"{idx}. [{artist['name']}]({artist['external_urls']['spotify']})\n"
    
    await query.message.reply_text(text, parse_mode="Markdown")

async def top_tracks(update: Update, context: CallbackContext):
    """Отправляет топ-треки"""
    query = update.callback_query
    await query.answer()

    top_tracks = sp.current_user_top_tracks(limit=5, time_range='medium_term')
    text = "🎵 Твои топ-5 треков:\n\n"
    for idx, track in enumerate(top_tracks["items"], start=1):
        text += f"{idx}. [{track['name']} - {track['artists'][0]['name']}]({track['external_urls']['spotify']})\n"
    
    await query.message.reply_text(text, parse_mode="Markdown")

async def listening_time(update: Update, context: CallbackContext):
    """Подсчитывает общее время прослушивания за месяц"""
    query = update.callback_query
    await query.answer()

    recently_played = sp.current_user_recently_played(limit=50)
    total_ms = sum([item["track"]["duration_ms"] for item in recently_played["items"]])
    total_minutes = total_ms // 60000
    await query.message.reply_text(f"⏳ Время прослушивания за месяц: {total_minutes} минут.")

def main():
    """Запуск бота"""
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("current", current_track))
    app.add_handler(CommandHandler("topartists", top_artists))
    app.add_handler(CommandHandler("toptracks", top_tracks))
    app.add_handler(CommandHandler("listeningtime", listening_time))

    app.run_polling()

if __name__ == "__main__":
    main()