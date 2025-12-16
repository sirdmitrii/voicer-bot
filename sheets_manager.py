import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# Define the scope
SCOPE = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']

class SheetsManager:
    def __init__(self, credentials_file, sheet_name):
        self.credentials_file = credentials_file
        self.sheet_name = sheet_name
        self.client = None
        self.sheet = None

    def connect(self):
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name(self.credentials_file, SCOPE)
            self.client = gspread.authorize(creds)
            # Try to open by title
            try:
                self.sheet = self.client.open(self.sheet_name).sheet1
                logging.info(f"Successfully connected to Google Sheet: {self.sheet_name}")
                return True
            except gspread.SpreadsheetNotFound:
                logging.error(f"Spreadsheet '{self.sheet_name}' not found. Check name and permissions.")
                # Optional: try opening by key if sheet_name looks like a key, or creating one?
                return False
        except Exception as e:
            logging.error(f"Error connecting to Google Sheets: {e}", exc_info=True)
            return False

    def init_headers(self):
        """Creates headers if the sheet is empty."""
        if not self.sheet:
             if not self.connect(): return
        
        # New requested columns
        headers = [
            "Менеджер",
            "Звонок",       # Имя файла
            "Дата звонка",  # Из метаданных или дата отправки
            "Дата прослушки",
            "Приветствие",
            "Выяснение всех необходимых вопросов",
            "Презентация продукта",
            "Закрытие",
            "Подведение итогов, фиксация следующего шага",
            "Работа с возражениями",
            "Характеристика речи",
            "Балл за звонок",
            "Транскрибация звонка", 
            "Комментарий, рекомендации"
        ]
        
        try:
            # Check if A1 is empty
            val = self.sheet.acell('A1').value
            if not val:
                logging.info("Sheet is empty, adding headers.")
                self.sheet.append_row(headers)
            else:
                logging.info("Headers already exist.")
        except Exception as e:
            logging.error(f"Error checking/writing headers: {e}")

    def find_row_by_filename(self, filename):
        """
        Checks if a file with the given name already exists in the "Звонок" column (Col 2).
        Returns the row index (integer, 1-based) if found, else None.
        Raises Exception if connection or read fails.
        """
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
        """
        Adds a new row with evaluation data OR updates an existing row if row_index is provided.
        """
        logging.info("Preparing to save to sheets...")
        
        if not self.sheet:
            if not self.connect():
                logging.error("Cannot add/update row: No connection to sheet.")
                return False
        
        # Ensure headers exist only if we are appending
        if not row_index:
            self.init_headers() 
        
        current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Try to extract date
        import re
        date_match = re.search(r'(\d{4}[-._]\d{2}[-._]\d{2})|(\d{2}[-._]\d{2}[-._]\d{4})', filename)
        if date_match:
            call_date = date_match.group(0)
        else:
            call_date = current_time_str.split(' ')[0]

        # Manager name
        manager_audio = data.get("manager_name")
        final_manager_name = manager_audio if manager_audio and manager_audio.lower() != "unknown" else manager_name_telegram

        # Helper
        def score_cell(key):
            s = data.get(key)
            return s if s is not None else "n/a"

        # Helper comment
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
            final_manager_name,                             
            filename,                                       
            call_date,                                      
            current_time_str,                               
            score_cell("greeting_score"),
            score_cell("needs_analysis_score"),
            score_cell("presentation_score"),
            score_cell("closing_score"),
            score_cell("summary_score"),
            score_cell("objection_handling_score"),
            score_cell("speech_score"),
            data.get("total_score"),                        
            data.get("transcription_text", "Текст не распознан"), 
            final_comment                                   
        ]
        
        cleaned_row = [str(x) if x is not None else "-" for x in row]
        
        try:
            if row_index:
                # Update existing row
                logging.info(f"Updating existing row {row_index}...")
                # gspread update usage: update(range_name, values=[[]]) or manually cell by cell?
                # simpler: self.sheet.update(f"A{row_index}", [cleaned_row])
                # Note: range format depends on library version. safely using 'update' with range
                self.sheet.update(f"A{row_index}:N{row_index}", [cleaned_row])
            else:
                self.sheet.append_row(cleaned_row)
            
            logging.info(f"Successfully saved row for {filename}")
            return True
        except Exception as e:
            logging.error(f"Error saving to Google Sheets: {e}", exc_info=True)
            return False
