import os
import logging
import subprocess
import yt_dlp
import asyncio
import threading
from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes, CallbackQueryHandler

# --- CONFIGURATION ---
TOKEN = '8231888674:AAEH-yK_-7S_tBJNliNyedAaNw7GCJMTaU8'
SAVE_PATH = 'downloads'
if not os.path.exists(SAVE_PATH):
    os.makedirs(SAVE_PATH)

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def check_ffmpeg():
    try:
        subprocess.run(['ffmpeg', '-version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except FileNotFoundError:
        return False

HAS_FFMPEG = check_ffmpeg()

# --- DOWNLOADING LOGIC ---

def get_video_info(url):
    ydl_opts = {'quiet': True, 'noplaylist': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return {
            'title': info.get('title', 'Unknown Title'),
            'thumbnail': info.get('thumbnail', ''),
            'duration': f"{info.get('duration', 0) // 60}:{info.get('duration', 0) % 60:02d}",
        }

def download_media(url, mode, quality):
    ydl_opts = {
        'outtmpl': f'{SAVE_PATH}/%(title)s.%(ext)s',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
    }
    
    if mode == 'audio':
        if HAS_FFMPEG:
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': quality,
                }],
            })
        else:
            # FALLBACK: If no ffmpeg, just download best audio file as is (often .m4a or .webm)
            ydl_opts['format'] = 'bestaudio/best'
    else:
        if HAS_FFMPEG:
            ydl_opts['format'] = f'bestvideo[height<={quality}]+bestaudio/best/best[height<={quality}]'
        else:
            ydl_opts['format'] = f'best[height<={quality}]/best'

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        
        # Check if post-processor changed the name (e.g. to .mp3)
        if mode == 'audio' and HAS_FFMPEG:
            base = os.path.splitext(filename)[0]
            if os.path.exists(base + '.mp3'):
                return base + '.mp3'
        
        # Handle cases where filename extension doesn't match info['ext']
        if not os.path.exists(filename):
            base = os.path.splitext(filename)[0]
            for ext in ['mp4', 'mkv', 'webm', 'm4a', 'mp3']:
                alt_path = f"{base}.{ext}"
                if os.path.exists(alt_path):
                    return alt_path
                    
        return filename

# --- FLASK APP (WEB INTERFACE) ---

app = Flask(__name__)
CORS(app)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.json
    url = data.get('url')
    try:
        info = get_video_info(url)
        return jsonify(info)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/download')
def web_download():
    mode = request.args.get('mode')
    quality = request.args.get('quality')
    url = request.args.get('url')
    
    try:
        file_path = download_media(url, mode, quality)
        if os.path.exists(file_path):
            return send_file(os.path.abspath(file_path), as_attachment=True)
        return "File not found", 404
    except Exception as e:
        return str(e), 500

def run_flask():
    app.run(port=5000, host='0.0.0.0', use_reloader=False)

# --- TELEGRAM BOT LOGIC ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Premium Downloader Bot!\nSend a link to start.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "youtube.com" in url or "youtu.be" in url:
        keyboard = [[
            InlineKeyboardButton("🎬 Video", callback_data=f"v_menu|{url}"),
            InlineKeyboardButton("🎵 Audio", callback_data=f"a_menu|{url}")
        ]]
        await update.message.reply_text("Choose Format:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data.split('|')
    action = data[0]
    url = data[-1]
    await query.answer()

    if action == "v_menu":
        keyboard = [
            [InlineKeyboardButton("360p", callback_data=f"dl|video|360|{url}"),
             InlineKeyboardButton("720p", callback_data=f"dl|video|720|{url}"),
             InlineKeyboardButton("1080p", callback_data=f"dl|video|1080|{url}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"back|{url}")]
        ]
        await query.edit_message_text("Select Video Quality:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "a_menu":
        keyboard = [
            [InlineKeyboardButton("Standard Quality", callback_data=f"dl|audio|128|{url}")],
            [InlineKeyboardButton("High Quality", callback_data=f"dl|audio|320|{url}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"back|{url}")]
        ]
        await query.edit_message_text("Select Audio Quality:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "back":
        keyboard = [[InlineKeyboardButton("🎬 Video", callback_data=f"v_menu|{url}"), InlineKeyboardButton("🎵 Audio", callback_data=f"a_menu|{url}")]]
        await query.edit_message_text("Choose Format:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "dl":
        mode = data[1]
        quality = data[2]
        status = await query.edit_message_text(f"⏳ Downloading {mode}...")
        try:
            if mode == 'audio' and not HAS_FFMPEG:
                await query.message.reply_text("⚠️ FFmpeg is missing. Downloading as best available audio format (m4a/webm) instead of MP3.")
            
            file_path = await asyncio.to_thread(download_media, url, mode, quality)
            
            if os.path.exists(file_path):
                file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
                if file_size_mb > 50:
                    await status.edit_text(f"⚠️ File too large for Telegram ({file_size_mb:.1f}MB).")
                    return

                await status.edit_text("📤 Uploading...")
                with open(file_path, 'rb') as f:
                    if mode == 'audio':
                        await query.message.reply_audio(audio=f)
                    else:
                        await query.message.reply_video(video=f)
                await status.delete()
        except Exception as e:
            await query.message.reply_text(f"❌ Error: {str(e)}")

def run_bot():
    bot_app = ApplicationBuilder().token(TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    bot_app.run_polling()

if __name__ == '__main__':
    # Start Flask thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    # Start Bot (main thread)
    print("Web Server running at http://localhost:5000")
    print("Telegram Bot is active...")
    run_bot()