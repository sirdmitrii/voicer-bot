
import os
import logging
import asyncio
import subprocess
import json
import base64
import re
from datetime import datetime

# ==========================================
# 📦 EXTERNAL DEPENDENCIES
# ==========================================
# Ensure these are installed:
# pip install python-telegram-bot openai gspread oauth2client python-dotenv requests
# ffmpeg must be installed on the system (PATH)
# ==========================================

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
    from openai import OpenAI
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
except ImportError as e:
    print("❌ ERROR: Missing dependencies.")
    print(f"Details: {e}")
    print("Please install: pip install python-telegram-bot openai gspread oauth2client python-dotenv requests")
    exit(1)

# ==========================================
# 🔑 CONFIGURATION & CREDENTIALS
# ==========================================

# ==========================================
# 🔑 CONFIGURATION & CREDENTIALS
# ==========================================

CREDENTIALS_FILE = "credentials.json"

try:
    with open(CREDENTIALS_FILE, 'r', encoding='utf-8') as f:
        creds_data = json.load(f)
except FileNotFoundError:
    print(f"❌ ERROR: {CREDENTIALS_FILE} not found!")
    print("Please create credentials.json with your API keys and Google Service Account data.")
    exit(1)

# Telegram Bot Token
TELEGRAM_TOKEN = creds_data.get("telegram_token")

# OpenAI API Key
OPENAI_API_KEY = creds_data.get("openai_api_key")

# Google Sheet Name
GOOGLE_SHEET_NAME = creds_data.get("google_sheet_name")

# Google Service Account Credentials
# We use the whole json object as credentials dict for gspread, 
# but we need to ensure it has the standard service account fields.
GOOGLE_CREDENTIALS_DICT = creds_data

# ==========================================
# 🛠 LOGGING SETUP
# ==========================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ==========================================
# 🧠 AI PROMPTS & ANALYZER
# ==========================================

EVALUATION_PROMPT = """
You are an expert Quality Assurance specialist for sales calls. Your task is to analyze the audio of a sales call and evaluate the manager's performance based on the specific criteria below.

IMPORTANT:
1.  **Strict Scoring**: For each category, you MUST assign one of the specific ALLOWED SCORES listed. Do NOT assign intermediate scores (e.g., do not give 8 if only 0, 5, 10 are allowed).
2.  **Manager Name**: Extract the manager's name from the audio (usually at the start). If not found, return "Unknown".
3.  **Transcription**: Provide a verbatim transcription of the call in Russian.

### EVALUATION CRITERIA:

1. **Greeting** (Max 10 points)
   *   **10 points**: Greeting according to script: manager names themselves, the company, and asks for client's name if unknown.
   *   **5 points**: Incomplete greeting: missing one element (manager name, title, or company), or failed to ask client's name.
   *   **0 points**: No greeting, no company name, or no request for client's name.
   *   **ALLOWED SCORES**: 0, 5, 10

2. **Needs Analysis** (Max 20 points)
   *   **20 points**: Manager asks open, closed, clarifying questions per script, waits for answers, uses active listening.
   *   **10 points**: Not all script questions asked; does not wait for answers; lacks active listening.
   *   **0 points**: Needs analysis absent; no relevant questions asked.
   *   **ALLOWED SCORES**: 0, 10, 20

3. **Presentation** (Max 20 points)
   *   **20 points**: Follows partner requirements. Offer based on needs, not overloaded, uses FAB (Features-Advantages-Benefits) technique.
   *   **10 points**: Partial script presentation, not based on FAB.
   *   **0 points**: No presentation. Merely stating product availability or company description is NOT a presentation.
   *   **"n/a"**: No objective need for presentation in conversation.
   *   **ALLOWED SCORES**: 0, 10, 20, "n/a"

4. **Closing** (Max 10 points)
   *   **10 points**: Uses closing phrases linked to specific action and timeframe.
   *   **5 points**: Only farewell phrases without deadlines or specific actions.
   *   **0 points**: No closing/Call to Action phrases and no farewell.
   *   **"n/a"**: Connection lost.
   *   **ALLOWED SCORES**: 0, 5, 10, "n/a"

5. **Summary & Next Steps** (Max 10 points)
   *   **10 points**: Summarized communication, voiced all agreements and next steps agreed upon.
   *   **5 points**: Only one action: either summarized OR voiced next step.
   *   **0 points**: No summary, just said goodbye.
   *   **"n/a"**: Connection lost.
   *   **ALLOWED SCORES**: 0, 5, 10, "n/a"

6. **Objection Handling** (Max 20 points)
   *   **20 points**: Handled ALL objections using algorithm: Join (conditional agreement) -> Clarifying questions -> FAB arguments -> Call to action.
   *   **10 points**: Handling not by algorithm: missing steps, weak arguments, or incomplete handling.
   *   **0 points**: No objection handling attempted despite objections present.
   *   **"n/a"**: No objections raised by client.
   *   **ALLOWED SCORES**: 0, 10, 20, "n/a"

7. **Speech Quality** (Max 10 points)
   *   **10 points**: No errors OR one minor error (e.g., single use of "just a sec").
   *   **5 points**: Several errors (2-3) or one critical error (filler words, lack of confidence, etc.).
   *   **0 points**: Many significant errors (>3), such as: unclear diction, lack of empathy, monotone, awkward pauses, filler words, negative tone, specific dialect errors, excessive diminutives.
   *   **ALLOWED SCORES**: 0, 5, 10

### OUTPUT FORMAT (JSON ONLY):
{
  "manager_name": "Name or Unknown",
  "transcription_text": "Full transcription...",
  "greeting_score": 10,
  "greeting_comment": "Explanation...",
  "needs_analysis_score": 20,
  "needs_analysis_comment": "Explanation...",
  "presentation_score": "n/a",
  "presentation_comment": "Reason...",
  "closing_score": 5,
  "closing_comment": "Explanation...",
  "summary_score": 10,
  "summary_comment": "Explanation...",
  "objection_handling_score": 20,
  "objection_handling_comment": "Explanation...",
  "speech_score": 5,
  "speech_comment": "Explanation...",
  "total_score": 75,
  "summary_text": "General conclusion and recommendations."
}
"""

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

def encode_audio(audio_path):
    with open(audio_path, "rb") as audio_file:
        return base64.b64encode(audio_file.read()).decode('utf-8')

def analyze_call_audio(audio_path, audio_format="mp3"):
    """
    Analyzes audio file directly using GPT-4o-audio-preview.
    """
    try:
        base64_audio = encode_audio(audio_path)
        
        logging.info(f"Sending audio to OpenAI. Path: {audio_path}, Format: {audio_format}, Size: {len(base64_audio)} bytes base64")

        response = client.chat.completions.create(
            model="gpt-4o-audio-preview", 
            modalities=["text"],
            messages=[
                {
                    "role": "system", 
                    "content": EVALUATION_PROMPT + "\nВАЖНО: Верни ТОЛЬКО чистый JSON без markdown форматирования (без ```json)."
                },
                {
                    "role": "user",
                    "content": [
                        { 
                            "type": "text", 
                            "text": "Проанализируй этот звонок." 
                        },
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": base64_audio,
                                "format": audio_format
                            }
                        }
                    ]
                }
            ]
        )
        
        content = response.choices[0].message.content
        logging.info("OpenAI response received.")
        
        # Clean up potential markdown code blocks
        if content.startswith("```json"):
            content = content.replace("```json", "").replace("```", "")
        elif content.startswith("```"):
            content = content.replace("```", "")
            
        content = content.strip()

        data = json.loads(content)

        # Recalculate total_score in case GPT summed incorrectly
        scores = [
            data.get('greeting_score', 0),
            data.get('needs_analysis_score', 0),
            data.get('speech_score', 0)
        ]
        for key in ['presentation_score', 'closing_score', 'summary_score', 'objection_handling_score']:
            score = data.get(key)
            if score != "n/a" and score is not None:
                scores.append(int(score))
        data['total_score'] = sum(scores)

        return data
        
    except Exception as e:
        logging.error(f"CRITICAL ERROR in analyze_call_audio: {type(e).__name__}: {e}", exc_info=True)
        return None

# ==========================================
# 📊 SHEETS MANAGER
# ==========================================

# Define the scope
SCOPE = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']

class SheetsManager:
    def __init__(self, credentials_dict, sheet_name):
        self.credentials_dict = credentials_dict
        self.sheet_name = sheet_name
        self.client = None
        self.sheet = None

    def connect(self):
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_dict(self.credentials_dict, SCOPE)
            self.client = gspread.authorize(creds)
            try:
                self.sheet = self.client.open(self.sheet_name).sheet1
                logging.info(f"Successfully connected to Google Sheet: {self.sheet_name}")
                return True
            except gspread.SpreadsheetNotFound:
                logging.error(f"Spreadsheet '{self.sheet_name}' not found. Check name and permissions.")
                return False
        except Exception as e:
            logging.error(f"Error connecting to Google Sheets: {e}", exc_info=True)
            return False

    def init_headers(self):
        """Creates headers if the sheet is empty."""
        if not self.sheet:
             if not self.connect(): return
        
        headers = [
            "Менеджер", "Звонок", "Дата звонка", "Дата прослушки",
            "Приветствие", "Выяснение всех необходимых вопросов", "Презентация продукта",
            "Закрытие", "Подведение итогов, фиксация следующего шага", "Работа с возражениями",
            "Характеристика речи", "Балл за звонок", "Транскрибация звонка", "Комментарий, рекомендации"
        ]
        
        try:
            val = self.sheet.acell('A1').value
            if not val:
                logging.info("Sheet is empty, adding headers.")
                self.sheet.append_row(headers)
            else:
                logging.info("Headers already exist.")
        except Exception as e:
            logging.error(f"Error checking/writing headers: {e}")

    def find_row_by_filename(self, filename):
        if not self.sheet:
             if not self.connect(): 
                 raise Exception("Could not connect to Google Sheets")
        try:
            # Column 2 is "Звонок" / Filename
            filenames = self.sheet.col_values(2)
            if filename in filenames:
                return filenames.index(filename) + 1
            return None
        except Exception as e:
            logging.error(f"Error searching for file: {e}")
            raise e

    def add_evaluation(self, filename, data, manager_name_telegram="-", row_index=None):
        logging.info("Preparing to save to sheets...")
        
        if not self.sheet:
            if not self.connect():
                logging.error("Cannot add/update row: No connection to sheet.")
                return False
        
        if not row_index:
            self.init_headers() 
        
        current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        date_match = re.search(r'(\d{4}[-._]\d{2}[-._]\d{2})|(\d{2}[-._]\d{2}[-._]\d{4})', filename)
        if date_match:
            call_date = date_match.group(0)
        else:
            call_date = current_time_str.split(' ')[0]

        manager_audio = data.get("manager_name")
        final_manager_name = manager_audio if manager_audio and manager_audio.lower() != "unknown" else manager_name_telegram

        def score_cell(key):
            s = data.get(key)
            return s if s is not None else "n/a"

        def collect_comment(section_name, comment_key):
            c = data.get(comment_key)
            if c and c != "None" and c != "-":
                return f"{section_name}: {c}"
            return None

        comments_list = [
            collect_comment("Приветствие", "greeting_comment"),
            collect_comment("Выявление", "needs_analysis_comment"),
            collect_comment("Презентация", "presentation_comment"),
            collect_comment("Закрытие", "closing_comment"),
            collect_comment("Итоги", "summary_comment"),
            collect_comment("Возражения", "objection_handling_comment"),
            collect_comment("Речь", "speech_comment"),
        ]
        summary_text = data.get("summary_text", "")
        if summary_text:
             comments_list.append(f"\nОБЩЕЕ: {summary_text}")

        final_comment = "\n".join([c for c in comments_list if c])

        row = [
            final_manager_name, filename, call_date, current_time_str,
            score_cell("greeting_score"), score_cell("needs_analysis_score"),
            score_cell("presentation_score"), score_cell("closing_score"),
            score_cell("summary_score"), score_cell("objection_handling_score"),
            score_cell("speech_score"), data.get("total_score"),
            data.get("transcription_text", "Текст не распознан"), final_comment
        ]
        
        cleaned_row = [str(x) if x is not None else "-" for x in row]
        
        try:
            if row_index:
                logging.info(f"Updating existing row {row_index}...")
                self.sheet.update(f"A{row_index}:N{row_index}", [cleaned_row])
            else:
                self.sheet.append_row(cleaned_row)
            
            logging.info(f"Successfully saved row for {filename}")
            return True
        except Exception as e:
            logging.error(f"Error saving to Google Sheets: {e}", exc_info=True)
            return False

# ==========================================
# 🤖 BOT LOGIC
# ==========================================

# Initialize Sheets Manager
sheets_manager = SheetsManager(GOOGLE_CREDENTIALS_DICT, GOOGLE_SHEET_NAME)

# State management
user_queues = {} # {user_id: [ (update, context, file_id, filename) ]}
user_active_flags = {} # {user_id: bool}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот для анализа звонков.\n"
        "Отправь мне аудио, и я проанализирую его, включая интонацию и эмоции (используя GPT-4o Audio).\n"
        "Результаты будут сохранены в Google Таблицу."
    )

def convert_to_mp3(input_path, output_path):
    try:
        command = ['ffmpeg', '-i', input_path, '-ac', '1', '-b:a', '64k', output_path, '-y']
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        logging.error(f"FFmpeg conversion error: {e}")
        return False

async def process_audio_file(update: Update, context: ContextTypes.DEFAULT_TYPE, file_id, original_name_from_msg, row_index=None):
    user_id = update.effective_user.id
    message_id = update.effective_message.message_id
    
    try:
        file_obj = await context.bot.get_file(file_id)
    except Exception as e:
        await update.effective_message.reply_text("❌ Ошибка получения файла (истек срок действия?).")
        return

    safe_filename = f"temp_{user_id}_{message_id}_{original_name_from_msg.replace(' ', '_')}"
    temp_path = safe_filename
    mp3_path = f"{safe_filename}.mp3"

    status_msg = await update.effective_message.reply_text(f"🎧 Скачиваю ({original_name_from_msg}) и готовлю к анализу...")

    try:
        await file_obj.download_to_drive(temp_path)
        
        target_path = temp_path
        target_format = "mp3" 
        
        original_ext = os.path.splitext(original_name_from_msg)[1].lower().replace('.', '')
        if original_ext in ['ogg', 'oga']:
             target_format = "ogg" 
        elif original_ext in ['mp3', 'wav', 'flac']:
             target_format = original_ext
        else:
             target_format = "mp3" 

        loop = asyncio.get_event_loop()
        conversion_success = await loop.run_in_executor(None, convert_to_mp3, temp_path, mp3_path)
        
        if conversion_success:
             target_path = mp3_path
             target_format = "mp3"
        else:
             logging.warning("FFmpeg not found or failed. Trying to send original file.")

        await status_msg.edit_text(f"🧠 ИИ анализирует звонок ({target_format})...")
        
        analysis_result = await loop.run_in_executor(None, analyze_call_audio, target_path, target_format)
        
        if not analysis_result:
            await status_msg.edit_text("❌ Не удалось проанализировать аудио.")
        else:
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
    if user_id not in user_queues or not user_queues[user_id]:
        user_active_flags[user_id] = False
        return

    user_active_flags[user_id] = True
    
    current_item = user_queues[user_id].pop(0)
    update, context, file_id, filename = current_item
    
    try:
        existing_row = sheets_manager.find_row_by_filename(filename)
    except Exception as e:
        await update.message.reply_text("⚠️ Ошибка проверки дублей. Продолжаю...")
        existing_row = None
    
    if existing_row:
        logging.info(f"Duplicate found for {filename}. Suspending queue.")
        
        keyboard = [[
            InlineKeyboardButton("Да, перезаписать", callback_data="overwrite_yes"),
            InlineKeyboardButton("Нет, отмена", callback_data="overwrite_no")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        context.user_data['pending_item'] = (update, context, file_id, filename, existing_row)
        
        await update.message.reply_text(
            f"⚠️ Файл \"{filename}\" уже есть в таблице (строка {existing_row}).\n"
            "Хотите перезаписать?",
            reply_markup=reply_markup
        )
        return

    await process_audio_file(update, context, file_id, filename)
    await process_next_in_queue(user_id)

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
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

    if user_id not in user_queues:
        user_queues[user_id] = []
    
    user_queues[user_id].append((update, context, file_id, original_name))
    
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
        user_active_flags[user_id] = False
        await process_next_in_queue(user_id)
        return

    orig_update, orig_context, file_id, filename, row_index = pending_item
    
    if data == "overwrite_no":
        await query.edit_message_text(f"❌ Пропуск файла \"{filename}\".")
    elif data == "overwrite_yes":
        await query.edit_message_text(f"🔄 Обрабатываю \"{filename}\" (перезапись)...")
        await process_audio_file(orig_update, orig_context, file_id, filename, row_index=row_index)
    
    context.user_data.pop('pending_item', None)
    await process_next_in_queue(user_id)

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Извините, я не умею работать с текстовыми сообщениями. 😔\n\n"
        "Пожалуйста, отправьте мне аудиофайл или голосовое сообщение со звонком, чтобы я мог его проанализировать."
    )

if __name__ == '__main__':
    if not TELEGRAM_TOKEN or "ВСТАВИТЬ" in TELEGRAM_TOKEN:
        print("❌ Telegram Token not set.")
    else:
        application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        
        application.add_handler(CommandHandler('start', start))
        application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.Document.AUDIO, handle_audio))
        application.add_handler(CallbackQueryHandler(handle_overwrite_callback, pattern='^overwrite_'))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
        
        print("🤖 Bot is running from single file...")
        application.run_polling()
