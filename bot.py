import os
import logging
import asyncio
import subprocess
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from analyzer import analyze_call_audio
from sheets_manager import SheetsManager
import config 

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Initialize Sheets Manager
sheets_manager = SheetsManager(config.GOOGLE_CREDS_FILE, config.GOOGLE_SHEET_NAME)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот для анализа звонков.\n"
        "Отправь мне аудио, и я проанализирую его, включая интонацию и эмоции (используя GPT-4o Audio).\n"
        "Результаты будут сохранены в Google Таблицу."
    )

def convert_to_mp3(input_path, output_path):
    """Converts audio to mp3 using ffmpeg (via subprocess or pydub if installed)."""
    # Simple ffmpeg call. Ensure ffmpeg is installed on the system.
    try:
        command = ['ffmpeg', '-i', input_path, '-ac', '1', '-b:a', '64k', output_path, '-y']
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        logging.error(f"FFmpeg conversion error: {e}")
        return False

# State management
user_queues = {} # {user_id: [ (update, context, file_id, filename) ]}
user_active_flags = {} # {user_id: bool}

async def process_audio_file(update: Update, context: ContextTypes.DEFAULT_TYPE, file_id, original_name_from_msg, row_index=None):
    """
    Core logic to download, convert/prepare, analyze and save.
    """
    user_id = update.effective_user.id
    message_id = update.effective_message.message_id
    
    # Needs to get file object again because it might have timed out if we queued for long? 
    # Usually file_id is persistent enough.
    try:
        file_obj = await context.bot.get_file(file_id)
    except Exception as e:
        await update.effective_message.reply_text("❌ Ошибка получения файла (истек срок действия?).")
        return

    # Safe filename for filesystem
    safe_filename = f"temp_{user_id}_{message_id}_{original_name_from_msg.replace(' ', '_')}"
    temp_path = safe_filename
    mp3_path = f"{safe_filename}.mp3"

    status_msg = await update.effective_message.reply_text(f"🎧 Скачиваю ({original_name_from_msg}) и готовлю к анализу...")

    try:
        # 1. Download
        await file_obj.download_to_drive(temp_path)
        
        target_path = temp_path
        target_format = "mp3" # Default expectation
        
        original_ext = os.path.splitext(original_name_from_msg)[1].lower().replace('.', '')
        if original_ext in ['ogg', 'oga']:
             target_format = "ogg" 
        elif original_ext in ['mp3', 'wav', 'flac']:
             target_format = original_ext
        else:
             target_format = "mp3" 

        # 2. Try to Convert if needed/possible
        loop = asyncio.get_event_loop()
        conversion_success = await loop.run_in_executor(None, convert_to_mp3, temp_path, mp3_path)
        
        if conversion_success:
             target_path = mp3_path
             target_format = "mp3"
        else:
             logging.warning("FFmpeg not found or failed. Trying to send original file.")

        # 3. Analyze
        await status_msg.edit_text(f"🧠 ИИ анализирует звонок ({target_format})...")
        
        analysis_result = await loop.run_in_executor(None, analyze_call_audio, target_path, target_format)
        
        if not analysis_result:
            await status_msg.edit_text("❌ Не удалось проанализировать аудио.")
        else:
            # 4. Save
            await status_msg.edit_text("📊 Сохраняю результаты...")
            
            manager_name = update.effective_user.first_name
            if update.effective_user.last_name:
                manager_name += f" {update.effective_user.last_name}"
            
            sheets_manager.add_evaluation(original_name_from_msg, analysis_result, manager_name_telegram=manager_name, row_index=row_index)
            
            summary = analysis_result.get('summary_text', 'Нет резюме')
            speech_comment = analysis_result.get('speech_comment', '-')
            
            msg = (
                f"✅ **Готово!**\n\n"
                f"🗣 **Речь и интонация**: {speech_comment}\n\n"
                f"📝 **Итог**: {summary}\n\n"
                f"🏆 **Балл**: {analysis_result.get('total_score')}"
            )
            await update.effective_message.reply_text(msg, parse_mode='Markdown')

    except Exception as e:
        logging.error(f"Error in process_audio_file: {e}")
        await update.effective_message.reply_text("❌ Произошла ошибка при обработке.")
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)
        if os.path.exists(mp3_path): os.remove(mp3_path)

async def process_next_in_queue(user_id, application_context=None):
    """
    Processes the next item in the user's queue.
    If a duplicate is found, it asks the user and STOPS (returns).
    The callback handler will then have to call this function again.
    """
    if user_id not in user_queues or not user_queues[user_id]:
        user_active_flags[user_id] = False
        return

    user_active_flags[user_id] = True
    
    # Peek at or Pop the next item? 
    # If we pop now, and duplicate check fails, we lose it? 
    # We should pop only if we are sure we are processing or discarding.
    # But for "duplicate wait", the item is theoretically "under processing".
    # So let's pop. If duplicate, we store it in user_data context to retry later.
    
    current_item = user_queues[user_id].pop(0)
    update, context, file_id, filename = current_item
    
    # Check for duplicates
    try:
        existing_row = sheets_manager.find_row_by_filename(filename)
    except Exception as e:
        await update.message.reply_text("⚠️ Ошибка проверки дублей. Продолжаю...")
        existing_row = None
    
    if existing_row:
        # Found duplicate -> Stop chain, Ask User
        logging.info(f"Duplicate found for {filename}. Suspending queue.")
        
        keyboard = [[
            InlineKeyboardButton("Да, перезаписать", callback_data="overwrite_yes"),
            InlineKeyboardButton("Нет, отмена", callback_data="overwrite_no")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Save state for callback
        # We need to save the specific params to retry this specific item
        context.user_data['pending_item'] = (update, context, file_id, filename, existing_row)
        
        # Note: We do NOT call process_next_in_queue recursively here. We wait for Callback.
        # flag remains True? No, strictly speaking we are "idle waiting". 
        # But if we set it False, handle_audio might trigger parallel process?
        # Actually handle_audio appends. If active=True, it does nothing.
        # So we leave active=True so handle_audio doesn't interfere.
        
        await update.message.reply_text(
            f"⚠️ Файл \"{filename}\" уже есть в таблице (строка {existing_row}).\n"
            "Хотите перезаписать?",
            reply_markup=reply_markup
        )
        return

    # If not duplicate, process immediately
    await process_audio_file(update, context, file_id, filename)
    
    # After finishing, proceed to next
    await process_next_in_queue(user_id)


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    # Validation
    if not os.path.exists(config.GOOGLE_CREDS_FILE):
         await update.message.reply_text("⚠️ Ошибка: credentials.json не найден.")
         return
    if not config.OPENAI_API_KEY or "ВСТАВИТЬ" in config.OPENAI_API_KEY:
         await update.message.reply_text("⚠️ Ошибка: API Key не настроен.")
         return

    # Determine file info
    original_name = None
    file_id = None
    
    if update.message.voice:
        file_id = update.message.voice.file_id
        original_name = f"Voice_{user_id}_{update.message.message_id}.ogg"
    elif update.message.audio:
        file_id = update.message.audio.file_id
        original_name = update.message.audio.file_name
    elif update.message.document:
         file_id = update.message.document.file_id
         original_name = update.message.document.file_name
    else:
        return

    if not original_name:
         original_name = f"call_{user_id}_{update.message.message_id}.mp3"

    # Add to queue
    if user_id not in user_queues:
        user_queues[user_id] = []
    
    user_queues[user_id].append((update, context, file_id, original_name))
    
    # If not currently processing, start processing
    if not user_active_flags.get(user_id, False):
        await process_next_in_queue(user_id)
    else:
        await update.message.reply_text(f"⏳ Файл \"{original_name}\" добавлен в очередь...")


async def handle_overwrite_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    try:
        await query.answer()
    except: pass

    data = query.data
    pending_item = context.user_data.get('pending_item')
    
    if not pending_item:
        await query.edit_message_text("⚠️ Данные устарели.")
        user_active_flags[user_id] = False # Reset to be safe
        await process_next_in_queue(user_id) # Try to resume if anything left
        return

    orig_update, orig_context, file_id, filename, row_index = pending_item
    
    if data == "overwrite_no":
        await query.edit_message_text(f"❌ Пропуск файла \"{filename}\".")
        # Just continue to next
    elif data == "overwrite_yes":
        await query.edit_message_text(f"🔄 Обрабатываю \"{filename}\" (перезапись)...")
        await process_audio_file(orig_update, orig_context, file_id, filename, row_index=row_index)
    
    # Cleanup
    context.user_data.pop('pending_item', None)
    
    # RESUME QUEUE
    await process_next_in_queue(user_id)

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responds to any text message with instructions."""
    """Responds to any text message with instructions."""
    await update.message.reply_text(
        "Извините, я не умею работать с текстовыми сообщениями. 😔\n\n"
        "Пожалуйста, отправьте мне аудиофайл или голосовое сообщение со звонком, чтобы я мог его проанализировать."
    )

if __name__ == '__main__':
    if not config.TELEGRAM_TOKEN or "ВСТАВИТЬ" in config.TELEGRAM_TOKEN:
        print("Telegram Token not set in config.py or .env")
    else:
        application = ApplicationBuilder().token(config.TELEGRAM_TOKEN).build()
        
        application.add_handler(CommandHandler('start', start))
        application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.Document.AUDIO, handle_audio))
        application.add_handler(CallbackQueryHandler(handle_overwrite_callback, pattern='^overwrite_'))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
        
        print("Bot is running...")
        application.run_polling()
