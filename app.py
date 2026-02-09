import os
import logging
import subprocess
import yt_dlp
import asyncio
import threading
import re
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
    ydl_opts = {
        'quiet': True, 
        'noplaylist': True,
        'no_warnings': True,
        'extract_flat': True,
    }
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

app = Flask(__name__, template_folder='.')
CORS(app)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.json
    url = data.get('url')
    if not url: return jsonify({'error': 'No URL'}), 400
    try:
        return jsonify(get_video_info(url))
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/download')
def web_download():
    mode = request.args.get('mode')
    quality = request.args.get('quality')
    url = request.args.get('url')
    try:
        file_path = download_media(url, mode, quality)
        return send_file(os.path.abspath(file_path), as_attachment=True)
    except Exception as e:
        return str(e), 500

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(port=port, host='0.0.0.0', use_reloader=False)

# --- TELEGRAM BOT LOGIC ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Welcome! Send me a link to download.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    
    url = re.search(r'(https?://\S+)', update.message.text)
    if url:
        link = url.group(1)
        context.user_data['last_url'] = link
        keyboard = [[
            InlineKeyboardButton("🎬 Video", callback_data="v_menu"),
            InlineKeyboardButton("🎵 Audio", callback_data="a_menu")
        ]]
        await update.message.reply_text(
            f"✅ Link Detected!\nChoose format:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text("❌ Please send a valid link.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    action = query.data.split('|')[0]
    url = context.user_data.get('last_url')
    
    if not url:
        await query.answer("Please send the link again.", show_alert=True)
        return

    await query.answer()

    if action == "v_menu":
        keyboard = [
            [InlineKeyboardButton("360p", callback_data="dl|video|360"),
             InlineKeyboardButton("720p", callback_data="dl|video|720"),
             InlineKeyboardButton("1080p", callback_data="dl|video|1080")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back")]
        ]
        await query.edit_message_text("Select Video Quality:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif action == "a_menu":
        keyboard = [
            [InlineKeyboardButton("Standard (128k)", callback_data="dl|audio|128")],
            [InlineKeyboardButton("High (320k)", callback_data="dl|audio|320")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back")]
        ]
        await query.edit_message_text("Select Audio Quality:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif action == "back":
        keyboard = [[InlineKeyboardButton("🎬 Video", callback_data="v_menu"), InlineKeyboardButton("🎵 Audio", callback_data="a_menu")]]
        await query.edit_message_text("Choose Format:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif action.startswith("dl"):
        _, mode, quality = query.data.split('|')
        status = await query.edit_message_text(f"⏳ Downloading {mode}...")
        try:
            file_path = await asyncio.to_thread(download_media, url, mode, quality)
            if os.path.exists(file_path):
                await status.edit_text("📤 Uploading...")
                with open(file_path, 'rb') as f:
                    if mode == 'audio': await query.message.reply_audio(audio=f)
                    else: await query.message.reply_video(video=f)
                await status.delete()
        except Exception as e:
            await query.message.reply_text(f"❌ Error: {str(e)}")

def run_bot():
    bot_app = ApplicationBuilder().token(TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    print("🤖 Telegram Bot: ACTIVE")
    bot_app.run_polling()

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    run_bot()
