import tkinter as tk
import time
import random
import requests
import threading
import speech_recognition as sr
import subprocess
import os
import re
import json
import sys
import socket
import atexit
import http.server
import socketserver
import urllib.parse
from datetime import datetime, timedelta

# --- PATH SETUP ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "alisa.json")
REMINDERS_FILE = os.path.join(SCRIPT_DIR, "reminders.json")
latest_emergency_message = None

MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12
}

COMMAND_LIST_TEXT = (
    "Доступные голосовые команды:\n"
    "• Алиса, будильник на [время]\n"
    "• Алиса, выключи будильник\n"
    "• Алиса, который час\n"
    "• Алиса, какой сегодня день недели\n"
    "• Алиса, какое сегодня число\n"
    "• Алиса, введи/отмени режим тревоги (или 'все хорошо')\n"
    "• Алиса, включи свет / выключи свет\n"
    "• Алиса, включи часы / покажи часы\n"
    "• Алиса, выключить компьютер\n"
    "• Алиса, включи экран / выключи экран\n"
    "• Алиса, добавь ежедневное напоминание [текст] в [час] часов\n"
    "• Алиса, как сказать [фраза] / переведи [фраза]\n"
    "• Алиса, напиши [Имя] [текст]\n"
    "• Алиса, напомни [текст] [дата] в [время]\n"
    "• Алиса, напомни [текст] через [X] минут/часов\n"
    "• Алиса, обновись\n"
    "• Алиса, погода / сегодня / завтра / неделя\n"
    "• Алиса, покажи / прочитай сообщения\n"
    "• Алиса, покажи последние сообщения\n"
    "• Алиса, список команд\n"
    "• Алиса, список напоминаний\n"
    "• Алиса, удали напоминание [номер]\n"
    "• Алиса, [ваш вопрос для ИИ]"
)


class AlisaApp:
    def __init__(self, root):
        self.is_running = True
        self.root = root
        self.root.title("Smart Clock")
        self.root.attributes('-fullscreen', True)
        self.root.configure(background='black')
        self.root.config(cursor="none")

        subprocess.run(["xset", "s", "off"])
        subprocess.run(["xset", "-dpms"])
        subprocess.run(["xset", "dpms", "0", "0", "0"])
        subprocess.run(["xset", "s", "noblank"])
        subprocess.run(["xset", "s", "0", "0"])

        self.load_config()

        self.conversation_history = []
        self.unread_messages = []  
        self.read_messages = []    
        self.last_update_id = 0    
 
        self.last_activity_time = time.time()     
        self.display_mode = "clock"
        self.load_messages()
        
        self.richten_ui_and_state()
        self.setup_ui()
        self.update_unread_alarm_label()

        self.current_weather_text = "Погода еще не загружена"
        self.today_hourly_forecast = "Почасовой прогноз на сегодня загружается..."
        self.tomorrow_hourly_forecast = "Почасовой прогноз на завтра загружается..."
        self.weekly_forecast_text = "Прогноз на неделю загружается..."

        self.alarm_time = None
        self.alarm_triggered_today = False
        self.alarm_active = False
        self.alarm_stop_event = threading.Event()
        self.active_audio_process = None
        self.ambient_noise_threshold = 300  # Will be dynamically calibrated

        self.light_timer = None
        self.weather_timer = None
        self.countdown_seconds = 0

        self.reminders = []
        self.load_reminders()
        
        atexit.register(self.stop_all_audio)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.bind('<Escape>', lambda e: self.on_closing())

        self.update_display()
        self.fetch_weather()
        self.shift_position()
        self.restore_pending_reminders()

        threading.Thread(target=self.idle_checker_loop, daemon=True).start()
        threading.Thread(target=self.voice_listener_loop, daemon=True).start()
        threading.Thread(target=self.telegram_polling_loop, daemon=True).start()
        threading.Thread(target=self.start_local_web_server, daemon=True).start()
        self.root.after(5000, lambda: self.check_for_updates(manual=False))

    def richten_ui_and_state(self):
        pass

    def load_config(self):
        default_config = {
            "DEVICE_NAME": socket.gethostname(), 
            "GROQ_API_KEY": "gsk_YOUR_GROQ_API_KEY_HERE",
            "WEATHER_API_KEY": "YOUR_WEATHER_API_KEY",
            "CITY": "Boston",
            "TELEGRAM_BOT_TOKEN": "",
            "CHAT_CHANNEL_ID": "",
            "EMERGENCY_CHANNEL_ID": "",
            "CONTACTS": {},
            "VOSK_MODEL_PATH": os.path.join(SCRIPT_DIR, "vosk-model-small-ru-0.22"),
            "GITHUB_RAW_URL": "https://raw.githubusercontent.com/mlurye69/alisa/main/alisa.py",
            "CHIME_SOUND_PATH": "Sounds/alarm-clock-1.mp3"
        }

        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    default_config.update(loaded)
            except Exception as e:
                print(f"Ошибка чтения alisa.json: {e}")

        self.device_name = default_config.get("DEVICE_NAME", socket.gethostname())        
        self.groq_api_key = default_config["GROQ_API_KEY"]
        self.weather_api_key = default_config["WEATHER_API_KEY"]
        
        self.city = default_config.get("CITY", "")
        if not self.city or self.city == "Boston":
            detected_city = self.get_city_by_ip()
            if detected_city:
                self.city = detected_city
        
        self.emergency_channel_id = default_config.get("EMERGENCY_CHANNEL_ID", "")
        self.telegram_channel_id = default_config.get("CHANNEL_ID", "")
        self.telegram_bot_token = default_config.get("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = default_config.get("TELEGRAM_CHAT_ID", "")
        self.contacts = default_config.get("CONTACTS", {})
        
        self.model_path = default_config.get("VOSK_MODEL_PATH", os.path.join(SCRIPT_DIR, "vosk-model-small-ru-0.22"))
        self.github_raw_url = default_config.get("GITHUB_RAW_URL", "")
        
        raw_chime = default_config.get("CHIME_SOUND_PATH", "Sounds/alarm-clock-1.mp3")
        self.chime_sound_path = raw_chime if os.path.isabs(raw_chime) else os.path.join(SCRIPT_DIR, raw_chime)

    def idle_checker_loop(self):
        while self.is_running:
            time.sleep(60)
            if time.time() - self.last_activity_time >= 900:
                try:
                    dpms_check = subprocess.run(["xset", "q"], capture_output=True, text=True)
                    if "Monitor is On" in dpms_check.stdout:
                        self.turn_screen_off()
                except Exception as e:
                    print(f"Idle check error: {e}")

    def handle_webhook_update(self, data):
        msg = data.get("channel_post") or data.get("message", {})
        chat = msg.get("chat", {})
        chat_id = str(chat.get("id", ""))
        
        if chat_id != str(self.emergency_channel_id) and chat_id != str(getattr(self, 'chat_channel_id', '')):
            return
            
        text = msg.get("text")
        if text:
            sender_name = chat.get("title") or chat.get("first_name", "Канал")
            incoming_data = {
                "sender": sender_name,
                "chat_id": chat_id,
                "text": text,
                "time": time.strftime('%H:%M')
            }
            self.unread_messages.append(incoming_data)
            self.save_messages()
            self.root.after(0, self.update_unread_alarm_label)
            self.speak_russian("Новое экстренное сообщение.")

    def start_local_web_server(self, port=8080):
        class AlisaHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                global latest_emergency_message
                if self.path.split('?')[0].rstrip('/') == '/check_emergency':
                    response_data = {"status": "active", "emergency": latest_emergency_message} if latest_emergency_message else {"status": "cleared"}
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response_data).encode('utf-8'))
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(content_length)
                try:
                    data = json.loads(post_data.decode('utf-8'))
                    if "channel_post" in data or "message" in data:
                        self.server.app_instance.handle_webhook_update(data)
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"OK")
                except Exception as e:
                    print(f"Webhook error: {e}")
                    self.send_response(500)
                    self.end_headers()

            def log_message(self, format, *args):
                return

        class AlisaHTTPServer(socketserver.TCPServer):
            allow_reuse_address = True

        try:
            server = AlisaHTTPServer(("", port), AlisaHTTPRequestHandler)
            server.app_instance = self
            server.serve_forever()
        except Exception as e:
            print(f"Web server error: {e}")

    def load_reminders(self):
        if os.path.exists(REMINDERS_FILE):
            try:
                with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.reminders = data
                        self.reminders.sort(key=lambda x: x["timestamp"])
                        for rem in self.reminders:
                            if rem.get("is_alarm"):
                                self.alarm_time = rem.get("alarm_time_str")
                                if self.alarm_time and hasattr(self, 'alarm_display_label'):
                                    self.alarm_display_label.config(text=f"⏰ Будильник: {self.alarm_time}")
                                break
                    else:
                        self.reminders = []
            except Exception as e:
                print(f"Error loading reminders: {e}")
                self.reminders = []

    def save_reminders(self):
        try:
            with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.reminders, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Error saving reminders: {e}")

    def get_formatted_reminders_display(self):
        display_reminders = [r for r in self.reminders if not r.get("is_alarm")]
        if not display_reminders:
            return "Активных напоминаний нет.", "У вас нет активных напоминаний."

        daily_reminders = [r for r in display_reminders if r.get("daily")]
        one_off_reminders = [r for r in display_reminders if not r.get("daily")]

        daily_reminders.sort(key=lambda x: datetime.fromtimestamp(x["timestamp"]).strftime('%H:%M'))
        one_off_reminders.sort(key=lambda x: x["timestamp"])

        lines = []
        idx = 1
        if daily_reminders:
            lines.append("--- Daily Reminders ---")
            for rem in daily_reminders:
                dt_str = datetime.fromtimestamp(rem["timestamp"]).strftime('%H:%M')
                lines.append(f"{idx}. {rem['text']} ({dt_str})")
                idx += 1
        if one_off_reminders:
            if daily_reminders:
                lines.append("")
            lines.append("--- Reminders ---")
            for rem in one_off_reminders:
                dt_str = datetime.fromtimestamp(rem["timestamp"]).strftime('%d.%m %H:%M')
                lines.append(f"{idx}. {rem['text']} ({dt_str})")
                idx += 1
        return "\n".join(lines), "Показываю активные напоминания."

    def restore_pending_reminders(self):
        now = datetime.now().timestamp()
        valid_reminders = []
        for rem in self.reminders:
            target_time = rem.get("timestamp", 0)
            if rem.get("is_alarm"):
                if target_time <= now:
                    dt = datetime.fromtimestamp(target_time)
                    rem["timestamp"] = (dt + timedelta(days=1)).timestamp()
                valid_reminders.append(rem)
                continue
            if target_time > now:
                valid_reminders.append(rem)
                threading.Timer(target_time - now, self.trigger_reminder, args=[rem]).start()
            elif rem.get("daily", False):
                dt = datetime.fromtimestamp(target_time)
                next_dt = dt.replace(year=datetime.now().year, month=datetime.now().month, day=datetime.now().day)
                if next_dt.timestamp() <= now:
                    next_dt += timedelta(days=1)
                rem["timestamp"] = next_dt.timestamp()
                valid_reminders.append(rem)
                threading.Timer(next_dt.timestamp() - now, self.trigger_reminder, args=[rem]).start()
        self.reminders = valid_reminders
        self.reminders.sort(key=lambda x: x["timestamp"])
        self.save_reminders()

    def add_reminder(self, text, delay_seconds, daily=False):
        target_timestamp = datetime.now().timestamp() + delay_seconds
        for rem in self.reminders:
            if rem["text"].lower() == text.lower() and rem.get("daily") == daily:
                return

        reminder = {"text": text, "timestamp": target_timestamp, "daily": daily}
        self.reminders.append(reminder)
        self.reminders.sort(key=lambda x: x["timestamp"])
        self.save_reminders()
        
        if self.display_mode == "info" and ("напоминаний" in self.date_label.cget("text").lower() or "список" in self.date_label.cget("text").lower()):
            self.refresh_reminder_info_screen()
        
        self.root.after(0, lambda: threading.Timer(delay_seconds, self.trigger_reminder, args=[reminder]).start())

    def trigger_reminder(self, reminder):
        if reminder not in self.reminders:
            return
        self.speak_russian(f"Напоминание: {reminder['text']}")
        if reminder.get("daily", False):
            dt = datetime.fromtimestamp(reminder["timestamp"])
            next_dt = dt + timedelta(days=1)
            reminder["timestamp"] = next_dt.timestamp()
            self.reminders.sort(key=lambda x: x["timestamp"])
            self.save_reminders()
            threading.Timer(next_dt.timestamp() - datetime.now().timestamp(), self.trigger_reminder, args=[reminder]).start()
        else:
            self.reminders = [r for r in self.reminders if r != reminder]
            self.save_reminders()

    def remove_reminder_by_index(self, index_1_based):
        try:
            display_reminders = sorted([r for r in self.reminders if not r.get("is_alarm")], key=lambda x: x["timestamp"])
            idx = index_1_based - 1
            if 0 <= idx < len(display_reminders):
                self.reminders.remove(display_reminders[idx])
                self.save_reminders()
                if self.display_mode == "info":
                    self.refresh_reminder_info_screen()
                self.speak_russian(f"Напоминание {index_1_based} удалено.")
            else:
                self.speak_russian("Такого номера напоминания нет.")
        except Exception as e:
            print(f"Error removing reminder: {e}")
            self.speak_russian("Не удалось удалить напоминание.")

    def refresh_reminder_info_screen(self):
        texts, _ = self.get_formatted_reminders_display()
        has_reminders = bool([r for r in self.reminders if not r.get("is_alarm")])
        msg = texts if has_reminders else "Активных напоминаний нет."
        self.weather_desc_label.config(text=msg, font=('Helvetica', 20, 'bold'), fg='lightseagreen')
        self.weather_temp_label.config(text="")

    def send_telegram_alert(self, message):
        if not self.telegram_bot_token or not self.emergency_channel_id:
            return
        try:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            requests.post(url, json={"chat_id": self.emergency_channel_id, "text": message, "parse_mode": "Markdown"}, timeout=5)
        except Exception as e:
            print(f"Telegram alert error: {e}")

    def send_telegram_message_to_chat(self, target_key, message):
        target_chat_id = None
        contact_display_name = target_key
        if target_key in self.contacts:
            contact_info = self.contacts[target_key]
            if isinstance(contact_info, dict):
                target_chat_id = contact_info.get("CHANNEL_ID")
                contact_display_name = contact_info.get("name", target_key)
            elif isinstance(contact_info, str):
                target_chat_id = contact_info
        if not target_chat_id:
            target_chat_id = target_key if target_key else self.telegram_chat_id

        if not self.telegram_bot_token or not target_chat_id:
            return False
            
        try:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            response = requests.post(url, json={"chat_id": target_chat_id, "text": message, "parse_mode": "Markdown"}, timeout=5)
            if response.status_code == 200:
                self.read_messages.append({
                    "sender": f"Вы (Алиса -> {contact_display_name})",
                    "chat_id": target_chat_id,
                    "text": message,
                    "time": time.strftime('%H:%M')
                })
                self.save_messages()
                return True
            return False
        except Exception as e:
            print(f"Send message error: {e}")
            return False

    def get_last_telegram_messages_display(self, limit=5):
        if not self.telegram_bot_token:
            return "Telegram не настроен.", "Токен Telegram не задан."
        all_live_msgs = []
        try:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/getUpdates?allowed_updates=[\"message\",\"channel_post\"]"
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                for result in data.get("result", []):
                    msg = result.get("message") or result.get("channel_post", {})
                    chat = msg.get("chat", {})
                    text = msg.get("text")
                    if text:
                        timestamp = msg.get("date", time.time())
                        all_live_msgs.append({
                            "sender": chat.get("title") or chat.get("first_name", "Контакт"),
                            "time": datetime.fromtimestamp(timestamp).strftime('%H:%M'),
                            "timestamp": timestamp,
                            "text": text
                        })
        except Exception as e:
            print(f"Error fetching live history: {e}")

        if not all_live_msgs:
            return "В Telegram нет доступных для просмотра сообщений в буфере.", "В Telegram нет сообщений."
            
        all_live_msgs.sort(key=lambda x: x["timestamp"])
        last_msgs = all_live_msgs[-limit:]
        lines = [f"--- Telegram (последние {len(last_msgs)}) ---"]
        for idx, m in enumerate(last_msgs, 1):
            lines.append(f"{idx}. {m['sender']} ({m['time']}): {m['text']}")
        return "\n".join(lines), f"Показываю последние {len(last_msgs)} сообщений из Телеграма."

    def load_messages(self):
        messages_file = os.path.join(SCRIPT_DIR, "messages.json")
        if os.path.exists(messages_file):
            try:
                with open(messages_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.unread_messages = data.get("unread", [])
                        self.read_messages = data.get("read", [])
            except Exception as e:
                print(f"Error loading messages: {e}")

    def save_messages(self):
        messages_file = os.path.join(SCRIPT_DIR, "messages.json")
        try:
            with open(messages_file, "w", encoding="utf-8") as f:
                json.dump({"unread": self.unread_messages, "read": self.read_messages}, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Error saving messages: {e}")

    def trigger_emergency_mode(self):
        global latest_emergency_message
        if hasattr(self, 'emergency_chirp_timer') and self.emergency_chirp_timer:
            try: self.emergency_chirp_timer.cancel()
            except: pass

        alert_text = f"⚠️ **ТРЕВОГА НА УМНЫХ ЧАСАХ АЛИСЫ!**\n\n🖥️ **Устройство:** `{self.device_name}`\n\nПользователь запросил помощь. Звоните 911!"
        latest_emergency_message = alert_text
        threading.Thread(target=self.send_telegram_alert, args=(alert_text,), daemon=True).start()
        
        self.display_mode = "emergency"
        if self.weather_timer and self.weather_timer.is_alive():
            self.weather_timer.cancel()

        self.frame.place(x=40, y=40, relwidth=0.9, relheight=0.9, anchor='nw')
        self.date_label.config(text="Режим тревоги", fg="#ff69b4", font=('Helvetica', 45, 'bold'))
        self.time_label.config(text="", font=('Helvetica', 10))
        self.alarm_display_label.config(text="")
        self.weather_desc_label.config(text="⚠️ ВНИМАНИЕ: ТРЕВОГА! ⚠️\n\nЭкстренное уведомление отправлено близким.\n\n🚨 ЗВОНИТЕ 911", font=('Helvetica', 28, 'bold'), fg="#ff69b4")
        self.weather_temp_label.config(text="")
        self.trigger_emergency_voice_reminder(first_run=True)

    def trigger_emergency_voice_reminder(self, first_run=False):
        if self.display_mode != "emergency":
            return
        self.speak_russian(f"Внимание! Включен режим тревоги на компьютере {self.device_name}. Немедленно звоните 911!")
        self.emergency_chirp_timer = threading.Timer(60.0, self.trigger_emergency_voice_reminder)
        self.emergency_chirp_timer.start()

    def fetch_telegram_channel_history(self, chat_id, limit=5):
        all_known = list(self.unread_messages) + list(self.read_messages)
        user_msgs = [m for m in all_known if str(m.get("chat_id")) == str(chat_id)]
        user_msgs.sort(key=lambda x: x.get("time", ""), reverse=False)
        return user_msgs[-limit:] if user_msgs else []

    def get_city_by_ip(self):
        try:
            response = requests.get("https://ipinfo.io/json", timeout=5)
            if response.status_code == 200:
                return response.json().get("city")
        except Exception:
            pass
        return "Boston"

    def setup_ui(self):
        self.frame = tk.Frame(self.root, bg='black')
        self.frame.place(relx=0.05, rely=0.05, relwidth=0.9, relheight=0.9)

        self.init_status_label = tk.Label(self.frame, text="Алиса инициализируется, подождите...", font=('Helvetica', 26, 'bold'), fg='gold', bg='black', justify='left', anchor='w')
        self.init_status_label.pack(anchor='w', pady=(0, 10))

        self.weather_desc_label = tk.Label(self.frame, font=('Helvetica', 22, 'bold'), fg='lightseagreen', bg='black', justify='left', anchor='w', wraplength=1200)
        self.weather_desc_label.pack(anchor='w', pady=(0, 2))

        self.weather_temp_label = tk.Label(self.frame, font=('Helvetica', 28, 'bold'), fg='lightseagreen', bg='black', justify='left', anchor='w', wraplength=1200)
        self.weather_temp_label.pack(anchor='w', pady=(0, 10))

        self.date_label = tk.Label(self.frame, font=('Helvetica', 32, 'bold'), fg='mediumseagreen', bg='black', anchor='w', wraplength=1200)
        self.date_label.pack(anchor='w', pady=(0, 15))

        self.time_label = tk.Label(self.frame, font=('Helvetica', 110, 'bold'), fg='mediumseagreen', bg='black', anchor='w')
        self.time_label.pack(anchor='w', pady=(0, 0))

        self.alarm_display_label = tk.Label(self.frame, text="", font=('Helvetica', 24, 'bold'), fg='mediumpurple', bg='black', anchor='w')
        self.alarm_display_label.pack(anchor='w', pady=(10, 0))

    def turn_light_on(self):
        self.display_mode = "light"
        if self.light_timer and self.light_timer.is_alive():
            self.light_timer.cancel()
        for widget in [self.weather_desc_label, self.weather_temp_label, self.date_label, self.time_label, self.alarm_display_label]:
            widget.pack_forget()
        self.root.configure(background='white')
        self.frame.configure(background='white')
        self.light_timer = threading.Timer(600.0, self.turn_light_off)
        self.light_timer.start()

    def turn_light_off(self):
        if self.light_timer and self.light_timer.is_alive():
            self.light_timer.cancel()
        self.display_mode = "clock"
        self.root.configure(background='black')
        self.frame.configure(background='black')
        for widget, pady in [(self.weather_desc_label, (0, 2)), (self.weather_temp_label, (0, 10)), (self.date_label, (0, 15)), (self.time_label, (0, 0)), (self.alarm_display_label, (10, 0))]:
            widget.pack(anchor='w', pady=pady)
        self.frame.place(x=40, y=40, relwidth=0.9, relheight=0.9, anchor='nw')

    def show_info_screen(self, title_text, body_text, announcement):
        self.display_mode = "info"
        if self.weather_timer and self.weather_timer.is_alive():
            self.weather_timer.cancel()
        self.frame.place(x=40, y=40, relwidth=0.9, relheight=0.9, anchor='nw')
        self.date_label.config(text=title_text, fg='mediumseagreen')
        self.time_label.config(text="", font=('Helvetica', 10))
        self.alarm_display_label.config(text="")
        self.weather_desc_label.config(text=body_text, font=('Helvetica', 20, 'bold'), fg='lightseagreen')
        self.weather_temp_label.config(text="")
        self.speak_russian(announcement)
        self.countdown_seconds = 300
        self.update_countdown_timer()

    def shutdown_computer(self):
        self.speak_russian("Выключаю компьютер.")
        try:
            subprocess.Popen(["systemctl", "poweroff"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"Shutdown error: {e}")

    def update_countdown_timer(self):
        if self.display_mode == "clock":
            return
        if self.countdown_seconds > 0:
            mins, secs = divmod(self.countdown_seconds, 60)
            current_title = self.date_label.cget("text").split(" (")[0]
            self.date_label.config(text=f"{current_title} (Закроется через {mins:02d}:{secs:02d})")
            self.countdown_seconds -= 1
            self.weather_timer = threading.Timer(1.0, self.update_countdown_timer)
            self.weather_timer.start()
        else:
            self.restore_clock_mode()

    def restore_clock_mode(self):
        self.display_mode = "clock"
        self.time_label.config(font=('Helvetica', 110, 'bold'))
        self.weather_desc_label.config(font=('Helvetica', 22, 'bold'), fg='lightseagreen')
        self.weather_temp_label.config(font=('Helvetica', 26, 'bold'), fg='lightseagreen')
        self.date_label.config(fg='mediumseagreen')
        if self.unread_messages:
            self.alarm_display_label.config(text=f"💬 Новых сообщений: {len(self.unread_messages)}")
        elif self.alarm_time:
            self.alarm_display_label.config(text=f"⏰ Будильник: {self.alarm_time}")
        else:
            self.alarm_display_label.config(text="")
        self.frame.place(relx=0.05, rely=0.05, relwidth=0.9, relheight=0.9, anchor='nw')

    def stop_all_audio(self):
        self.alarm_active = False
        self.alarm_stop_event.set()
        if self.active_audio_process:
            try:
                self.active_audio_process.terminate()
                self.active_audio_process.wait(timeout=0.5)
            except Exception:
                try: self.active_audio_process.kill()
                except: pass
            self.active_audio_process = None

    def speak_russian(self, text, lang='ru'):
        self.stop_all_audio()
        os.system("pkill -9 mpg123 2>/dev/null")
        os.system("pkill -9 play 2>/dev/null")
        os.system("pkill -9 aplay 2>/dev/null")
        os.system("pkill -9 paplay 2>/dev/null")
        time.sleep(0.15)

        def speech_worker():
            try:
                from gtts import gTTS
                audio_file = os.path.join(SCRIPT_DIR, "response.mp3")
                tts = gTTS(text=text, lang=lang, slow=False)
                tts.save(audio_file)
                self.active_audio_process = subprocess.Popen(["mpg123", "-q", audio_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.active_audio_process.wait()
                if os.path.exists(audio_file):
                    os.remove(audio_file)
                self.active_audio_process = None
            except Exception as e:
                print(f"Speech error: {e}")

        threading.Thread(target=speech_worker, daemon=True).start()

    def update_display(self):
        try:
            if self.display_mode == "clock":
                days_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
                months_ru = ["", "Января", "Февраля", "Марта", "Апреля", "Мая", "Июня", "Июля", "Августа", "Сентября", "Октября", "Ноября", "Декабря"]
                t = time.localtime()
                date_str = f"{days_ru[t.tm_wday]}, {t.tm_mday} {months_ru[t.tm_mon]} {t.tm_year}"
                current_hm = time.strftime('%H:%M')
                
                self.date_label.config(text=date_str)
                self.time_label.config(text=time.strftime('%H:%M:%S'), font=('Helvetica', 110, 'bold'))
                
                parts = self.current_weather_text.split(", ")
                city_prefix = f"Погода в {self.city}:"
                weather_full_text = f"{city_prefix} {parts[0]}, {parts[1]}" if len(parts) >= 2 else f"{city_prefix} {self.current_weather_text}"

                self.weather_desc_label.config(text=weather_full_text, font=('Helvetica', 22, 'bold'), fg='lightseagreen', wraplength=1200)
                self.weather_temp_label.config(text="", font=('Helvetica', 22, 'bold'))
                
                if self.alarm_time and current_hm == self.alarm_time and not self.alarm_triggered_today:
                    self.alarm_triggered_today = True
                    threading.Thread(target=self.trigger_alarm, daemon=True).start()
                elif current_hm != self.alarm_time:
                    self.alarm_triggered_today = False
        except Exception as e:
            print(f"Display update error: {e}")
        finally:
            self.root.after(1000, self.update_display)

    def shift_position(self):
        if self.display_mode in ["clock", "emergency"]:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.frame.place(x=random.randint(20, max(20, sw - 850)), y=random.randint(20, max(20, sh - 500)), relwidth=0.9, relheight=0.9, anchor='nw')
        self.root.after(30000, self.shift_position)

    def trigger_alarm(self):
        self.alarm_active = True
        self.alarm_stop_event.clear()
        for _ in range(2):
            if not self.alarm_active or self.alarm_stop_event.is_set():
                break
            try:
                if os.path.exists(self.chime_sound_path):
                    self.active_audio_process = subprocess.Popen(["mpg123", "-q", self.chime_sound_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    self.active_audio_process = subprocess.Popen(["play", "-q", "-n", "synth", "0.3", "bell", "523"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.active_audio_process.wait()
            except Exception:
                pass
            for _ in range(50):
                if not self.alarm_active or self.alarm_stop_event.is_set():
                    break
                time.sleep(0.1)
        self.alarm_active = False
        for rem in self.reminders:
            if rem.get("is_alarm"):
                rem["timestamp"] = (datetime.fromtimestamp(rem["timestamp"]) + timedelta(days=1)).timestamp()
                self.save_reminders()
                break

    def fetch_weather(self):
        if self.weather_api_key != "YOUR_WEATHER_API_KEY":
            try:
                url_curr = f"https://api.openweathermap.org/data/2.5/weather?q={self.city}&appid={self.weather_api_key}&units=metric&lang=ru"
                resp = requests.get(url_curr, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    self.current_weather_text = f"{data['weather'][0]['description'].capitalize()}, {round(data['main']['temp'])}°C"

                url_fore = f"https://api.openweathermap.org/data/2.5/forecast?q={self.city}&appid={self.weather_api_key}&units=metric&lang=ru"
                resp_f = requests.get(url_fore, timeout=10)
                if resp_f.status_code == 200:
                    fdata = resp_f.json()
                    today_d = datetime.now().strftime('%Y-%m-%d')
                    tom_d = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
                    t_lines, tom_lines, w_lines, seen = [], [], [], set()
                    days_full = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
                    
                    for item in fdata.get("list", []):
                        i_date, i_time = item["dt_txt"].split()
                        line = f"{i_time[:5]}: {item['weather'][0]['description']}, {round(item['main']['temp'])}°C"
                        if i_date == today_d: t_lines.append(line)
                        elif i_date == tom_d: tom_lines.append(line)
                        if i_date not in seen and "12:00" in i_time:
                            seen.add(i_date)
                            dt_o = datetime.strptime(i_date, '%Y-%m-%d')
                            w_lines.append(f"{days_full[dt_o.weekday()]} : {item['weather'][0]['description']}, {round(item['main']['temp'])}°C")
                    
                    self.today_hourly_forecast = "\n".join(t_lines) if t_lines else "Недоступно."
                    self.tomorrow_hourly_forecast = "\n".join(tom_lines) if tom_lines else "Недоступно."
                    self.weekly_forecast_text = "\n".join(w_lines) if w_lines else "Недоступно."
            except Exception as e:
                print(f"Weather error: {e}")
        self.root.after(600000, self.fetch_weather)

    def turn_screen_off(self):
        try:
            env = os.environ.copy()
            if ":0" not in env.get("DISPLAY", ""): env["DISPLAY"] = ":0"
            subprocess.Popen(["xset", "dpms", "force", "off"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"Screen off error: {e}")

    def turn_screen_on(self):
        try:
            env = os.environ.copy()
            if ":0" not in env.get("DISPLAY", ""): env["DISPLAY"] = ":0"
            subprocess.Popen(["xset", "dpms", "force", "on"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"Screen on error: {e}")

    def ask_ai(self, prompt):
        if self.groq_api_key == "gsk_YOUR_GROQ_API_KEY_HERE":
            return "Пожалуйста, настройте API ключ Groq в коде."
        try:
            t = time.localtime()
            days_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
            months_ru = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
            curr_date = f"{days_ru[t.tm_wday]}, {t.tm_mday} {months_ru[t.tm_mon]} {t.tm_year} года"
            
            system_prompt = (
                f"Ты голосовой ассистент на умных часах. "
                f"Текущая дата: {curr_date}. Город: {self.city}. "
                f"Отвечай кратко, по делу, исключительно на русском языке, максимум в 1-2 предложениях."
            )

            self.conversation_history.append({"role": "user", "content": prompt})
            if len(self.conversation_history) > 20:
                self.conversation_history = self.conversation_history[-20:]

            messages_payload = [{"role": "system", "content": system_prompt}] + self.conversation_history
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.groq_api_key}", "Content-Type": "application/json"},
                json={"model": "qwen/qwen3.8-27b", "messages": messages_payload, "temperature": 0.7, "max_tokens": 150},
                timeout=10
            )
            if response.status_code == 200:
                answer = response.json()["choices"][0]["message"]["content"]
                if answer:
                    cleaned = answer.strip()
                    self.conversation_history.append({"role": "assistant", "content": cleaned})
                    return cleaned
        except Exception as e:
            print(f"Groq API error: {e}")
        return "Не удалось связаться с искусственным интеллектом."

    def telegram_polling_loop(self):
        while self.is_running:
            if not self.telegram_bot_token:
                time.sleep(10)
                continue
            try:
                url = f"https://api.telegram.org/bot{self.telegram_bot_token}/getUpdates?offset={self.last_update_id + 1}&timeout=30"
                response = requests.get(url, timeout=35)
                if response.status_code == 200:
                    for result in response.json().get("result", []):
                        self.last_update_id = result["update_id"]
                        msg = result.get("message") or result.get("channel_post", {})
                        chat = msg.get("chat", {})
                        chat_id = str(chat.get("id", ""))
                        
                        sender_name = "Неизвестный"
                        if chat_id in self.contacts:
                            c_info = self.contacts[chat_id]
                            sender_name = c_info.get("name", "Пользователь") if isinstance(c_info, dict) else chat_id
                        elif chat.get("title"):
                            sender_name = chat.get("title")
                        elif chat.get("first_name"):
                            sender_name = chat.get("first_name")
                            
                        text = msg.get("text")
                        if text:
                            incoming_data = {"sender": sender_name, "chat_id": chat_id, "text": text, "time": time.strftime('%H:%M')}
                            self.unread_messages.append(incoming_data)
                            self.save_messages()
                            self.root.after(0, self.update_unread_alarm_label)
                            self.speak_russian(f"Новое сообщение от {sender_name}.")
            except Exception as e:
                print(f"Telegram polling error: {e}")
            time.sleep(2)

    def update_unread_alarm_label(self):
        if self.display_mode == "clock" and self.unread_messages:
            self.alarm_display_label.config(text=f"💬 Новых сообщений: {len(self.unread_messages)}")

    def get_formatted_messages_display(self):
        all_msgs = list(self.unread_messages) + list(self.read_messages)
        if not all_msgs:
            return "Сообщений нет.", "У вас нет сообщений."
        lines = ["--- Входящие сообщения ---"]
        for idx, m in enumerate(all_msgs, 1):
            status_tag = "" if m in self.unread_messages else " (прочитано)"
            lines.append(f"{idx}. {m['sender']} ({m['time']}){status_tag}: {m['text']}")
        unread_count = len(self.unread_messages)
        announcement = f"У вас {unread_count} новых сообщений." if unread_count > 0 else "Показываю историю сообщений."
        return "\n".join(lines), announcement

    def check_for_updates(self, manual=False):
        if not self.github_raw_url:
            if manual: self.speak_russian("URL обновления не настроен.")
            return
        def update_worker():
            if manual:
                self.speak_russian("Проверяю обновления.")
                time.sleep(2.0)
            try:
                resp = requests.get(self.github_raw_url, timeout=10)
                if resp.status_code == 200:
                    remote = resp.text
                    script_path = os.path.abspath(__file__)
                    with open(script_path, "r", encoding="utf-8") as f:
                        local = f.read()
                    if remote.strip() != local.strip():
                        with open(script_path, "w", encoding="utf-8") as f:
                            f.write(remote)
                        self.speak_russian("Обновление загружено. Перезапуск.")
                        time.sleep(2.0)
                        os.execv(sys.executable, ['python'] + sys.argv)
                    elif manual:
                        self.speak_russian("Обновлений не найдено.")
            except Exception:
                if manual: self.speak_russian("Ошибка соединения с сетью.")
        threading.Thread(target=update_worker, daemon=True).start()

    def voice_listener_loop(self):
        import pyaudio
        from vosk import Model, KaldiRecognizer
        import audioop

        RATE, CHUNK, CHANNELS = 16000, 4000, 1
        p = pyaudio.PyAudio()

        print("Ожидание аудиоподсистемы...")
        attempts = 0
        while self.is_running and attempts < 30:
            try:
                test_stream = p.open(format=pyaudio.paInt16, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
                test_stream.stop_stream()
                test_stream.close()
                break
            except Exception:
                attempts += 1
                time.sleep(1)

        if attempts >= 30:
            p.terminate()
            os.execv(sys.executable, ['python'] + sys.argv)

        if not os.path.exists(self.model_path):
            self.root.after(0, lambda: self.init_status_label.config(text="Ошибка: Модель Vosk не найдена!", fg="red"))
            p.terminate()
            return

        try:
            vosk_model = Model(self.model_path)
        except Exception as e:
            print(f"Vosk model load error: {e}")
            p.terminate()
            return

        # Adaptive Ambient Noise Calibration
        print("Калибровка уровня шума окружающей среды...")
        try:
            calib_stream = p.open(format=pyaudio.paInt16, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
            rms_samples = []
            for _ in range(25):  
                data = calib_stream.read(CHUNK, exception_on_overflow=False)
                if data:
                    rms_samples.append(audioop.rms(data, 2))
            calib_stream.stop_stream()
            calib_stream.close()
            if rms_samples:
                avg_noise = sum(rms_samples) / len(rms_samples)
                self.ambient_noise_threshold = max(200, int(avg_noise * 1.5))
                print(f"Калибровка шума завершена. Порог установлен на: {self.ambient_noise_threshold}")
        except Exception as e:
            print(f"Calibration warning: {e}")

        def clear_init():
            self.init_status_label.pack_forget()
        self.root.after(0, clear_init)

        r = sr.Recognizer()

        while self.is_running:
            stream = None
            try:
                rec = KaldiRecognizer(vosk_model, RATE)
                rec.SetWords(False)
                stream = p.open(format=pyaudio.paInt16, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)

                while self.is_running:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    if data and rec.AcceptWaveform(data):
                        txt = json.loads(rec.Result()).get("text", "").lower()
                        if any(p_str in txt for p_str in ["алиса тревога", "алиса мне плохо"]):
                            if self.display_mode != "emergency":
                                self.last_activity_time = time.time()
                                self.turn_screen_on()
                                self.trigger_emergency_mode()
                            break
                        if "алиса" in txt or "alisa" in txt:
                            self.last_activity_time = time.time()
                            self.turn_screen_on()
                            self.speak_russian("Слушаю.")
                            break

                if not self.is_running: break

                frames, silent_chunks = [], 0
                silence_limit = int((RATE / CHUNK) * 3)

                while self.is_running:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    if not data: break
                    frames.append(data)
                    if audioop.rms(data, 2) < self.ambient_noise_threshold:
                        silent_chunks += 1
                    else:
                        silent_chunks = 0
                    if silent_chunks > silence_limit:
                        break

                if stream:
                    stream.stop_stream()
                    stream.close()
                    stream = None

                if frames and self.is_running:
                    try:
                        query = r.recognize_google(sr.AudioData(b''.join(frames), RATE, 2), language="ru-RU").lower()
                    except Exception:
                        continue
                    
                    print(f"Распознано: {query}")

                    # Wake screen check
                    try:
                        dpms_check = subprocess.run(["xset", "q"], capture_output=True, text=True)
                        if "Monitor is Off" in dpms_check.stdout:
                            self.turn_screen_on()
                            time.sleep(0.5)
                    except Exception:
                        pass

                    # 1. Emergency Commands
                    if any(phrase in query for phrase in ["отмена тревоги", "отмени тревогу", "все хорошо", "всё в порядке"]):
                        self.speak_russian("Режим тревоги отменен.")
                        global latest_emergency_message
                        latest_emergency_message = None
                        threading.Thread(target=self.send_telegram_alert, args=("✅ Тревога отменена пользователем.",), daemon=True).start()
                        self.restore_clock_mode()
                        continue
                    elif any(phrase in query for phrase in ["тревога", "мне плохо"]):
                        if self.display_mode != "emergency":
                            self.trigger_emergency_mode()
                        continue

                    # 2. Alarm Commands
                    if "будильник" in query and any(w in query for w in ["выключи", "отмени", "удали", "сотри"]):
                        self.alarm_time = None
                        self.alarm_display_label.config(text="")
                        self.reminders = [item for item in self.reminders if not item.get("is_alarm")]
                        self.save_reminders()
                        self.speak_russian("Будильник выключен.")
                    elif "будильник" in query:
                        match_a = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(?:час(?:а|ов)?)?\s*(вечера|дня|утра)?", query)
                        if match_a:
                            h = int(match_a.group(1))
                            m = int(match_a.group(2)) if match_a.group(2) else 0
                            mod = match_a.group(3)
                            if mod in ["вечера", "дня"] and h < 12: h += 12
                            elif mod == "утра" and h == 12: h = 0
                            
                            self.alarm_time = f"{h:02d}:{m:02d}"
                            self.alarm_triggered_today = False
                            self.alarm_display_label.config(text=f"⏰ Будильник: {self.alarm_time}")
                            
                            now_dt = datetime.now()
                            target_dt = now_dt.replace(hour=h, minute=m, second=0, microsecond=0)
                            if target_dt.timestamp() <= now_dt.timestamp(): target_dt += timedelta(days=1)
                            
                            self.reminders = [item for item in self.reminders if not item.get("is_alarm")]
                            alarm_reminder = {
                                "text": f"Будильник на {self.alarm_time}",
                                "timestamp": target_dt.timestamp(),
                                "daily": True,
                                "is_alarm": True,
                                "alarm_time_str": self.alarm_time
                            }
                            self.reminders.append(alarm_reminder)
                            self.save_reminders()
                            self.speak_russian(f"Будильник установлен на {h:02d}:{m:02d}.")
                        else:
                            self.speak_russian("Не поняла время будильника.")
                    
                    # 3. Reminders & Tasks
                    elif any(w in query for w in ["напомни", "напоминание"]):
                        # Match delay format (e.g. "напомни взять таблетки через 5 минут" or "добавь напоминание взять таблетки через 5 минут")
                        match_delay = re.search(r"(?:напомни|напоминание|добавь\s+напоминание|установи\s+напоминание)\s+(.*?)\s+через\s+(\d+)\s*(минут|минуту|минуты|час|часа|часов)", query)
                        if match_delay:
                            text_reminder = match_delay.group(1).strip()
                            amount = int(match_delay.group(2))
                            unit = match_delay.group(3)
                            delay_seconds = amount * 60 if "мин" in unit else amount * 3600
                            self.add_reminder(text_reminder, delay_seconds, daily=False)
                            self.speak_russian("Напоминание установлено.")
                        else:
                            match_time = re.search(r"напомни\s+(.*?)\s+(?:в|на)?\s*(\d{1,2})(?::(\d{2}))?\s*(?:час(?:а|ов)?)?\s*(вечера|дня|утра)?", query)
                            if match_time:
                                full_match_text = match_time.group(1).strip()
                                hour = int(match_time.group(2))
                                minute = int(match_time.group(3)) if match_time.group(3) else 0
                                modifier = match_time.group(4)
                                
                                if modifier in ["вечера", "дня"] and hour < 12:
                                    hour += 12
                                elif modifier == "утра" and hour == 12:
                                    hour = 0
                                    
                                time_suffix = f" в {hour:02d}:{minute:02d}"
                                clean_base_text = re.sub(r"\s+(?:в|на)?\s*\d{1,2}(?::\d{2})?\s*(?:час(?:а|ов)?)?\s*(?:утра|дня|вечера)?$", "", full_match_text).strip()
                                text_reminder = clean_base_text + time_suffix
                                
                                now = datetime.now()
                                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                                if target.timestamp() <= now.timestamp():
                                    target += timedelta(days=1)
                                    
                                delay_seconds = target.timestamp() - now.timestamp()
                                self.add_reminder(text_reminder, delay_seconds, daily=False)
                                self.speak_russian("Напоминание установлено.")
                            else:
                                self.speak_russian("Не поняла время напоминания.")
                    elif any(w in query for w in ["ежедневное", "каждый день", "ежедневно"]):
                        try:
                            match_daily = re.search(r"(?:ежедневное\s+напоминание|напоминание|ежедневное|каждый\s+день|ежедневно)\s+(.+?)\s+(?:в|на)?\s*(\d{1,2})(?::(\d{2}))?\s*(?:час(?:а|ов)?)?\s*(вечера|дня|утра)?", query)
                            if match_daily:
                                full_match_text = match_daily.group(1).strip()
                                hour = int(match_daily.group(2))
                                minute = int(match_daily.group(3)) if match_daily.group(3) else 0
                                modifier = match_daily.group(4)
                                
                                if modifier in ["вечера", "дня"] and hour < 12:
                                    hour += 12
                                elif modifier == "утра" and hour == 12:
                                    hour = 0

                                time_suffix = f" в {hour:02d}:{minute:02d}"
                                clean_base_text = re.sub(r"\s+(?:в|на)?\s*\d{1,2}(?::\d{2})?\s*(?:час(?:а|ов)?)?\s*(?:утра|дня|вечера)?$", "", full_match_text).strip()
                                text_reminder = clean_base_text + time_suffix
                                
                                now = datetime.now()
                                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                                if target.timestamp() <= now.timestamp():
                                    target += timedelta(days=1)
                                    
                                delay_seconds = target.timestamp() - now.timestamp()
                                self.add_reminder(text_reminder, delay_seconds, daily=True)
                                self.speak_russian("Ежедневное напоминание установлено.")
                            else:
                                self.speak_russian("Не поняла текст или время ежедневного напоминания.")
                        except Exception as ex:
                            print(f"ОШИБКА в блоке ежедневных напоминаний: {ex}")
                            self.speak_russian("Произошла ошибка при установке напоминания.")
                    
                    # 4. Messages & Telegram
                    elif any(w in query for w in ["пошли сообщение", "отправь сообщение", "отправь меседж", "пошли смс", "напиши"]):
                        remainder = re.sub(r"^(?:пошли сообщение|отправь сообщение|отправь меседж|пошли смс|напиши)\s+", "", query, flags=re.IGNORECASE).strip()
                        parts = remainder.split(maxsplit=1)
                        
                        if len(parts) >= 2:
                            raw_target = parts[0].strip().lower()
                            msg_text = parts[1].strip()
                            
                            found_chat_id = None
                            for key, details in self.contacts.items():
                                if isinstance(details, str):
                                    aliases = [key.lower()]
                                    actual_chat_id = details
                                    if raw_target in aliases or raw_target == key.lower():
                                        found_chat_id = actual_chat_id
                                        break
                                elif isinstance(details, dict):
                                    aliases = [a.lower() for a in details.get("aliases", [])]
                                    actual_chat_id = key
                                    if raw_target in aliases:
                                        found_chat_id = actual_chat_id
                                        break
                            
                            if found_chat_id:
                                success = self.send_telegram_message_to_chat(found_chat_id, f"💬 Сообщение от Алисы:\n\n{msg_text}")
                                if success:
                                    self.speak_russian("Сообщение отправлено.")
                                else:
                                    self.speak_russian("Не удалось отправить сообщение.")
                            else:
                                self.speak_russian(f"Не нашла контакт {raw_target}.")
                        else:
                            self.speak_russian("Не поняла кому и какое сообщение отправить.")
                    elif any(w in query for w in ["покажи", "прочитай", "прочти", "последнее", "последние"]) and any(w in query for w in ["сообщен", "меседж", "сообщения", "сообщение", "сообчени"]):
                        # --- GENERIC CONFIG-DRIVEN MESSAGE & CONTACT HANDLER ---
                        target_contact_name = None
                        target_chat_ids = []
                        is_read_command = any(w in query for w in ["прочитай", "прочти"])
                        
                        # 1. Dynamically scan self.contacts for any matching alias or name mentioned in query
                        for contact_key, details in self.contacts.items():
                            contact_aliases = [str(contact_key).lower()]
                            resolved_name = str(contact_key)
                            chat_id_candidates = [str(contact_key)]
                            
                            if isinstance(details, dict):
                                resolved_name = details.get("name", str(contact_key))
                                if "aliases" in details and isinstance(details["aliases"], list):
                                    contact_aliases.extend([str(a).lower() for a in details["aliases"]])
                                if details.get("CHANNEL_ID"):
                                    chat_id_candidates.append(str(details.get("CHANNEL_ID")))
                                if details.get("chat_id"):
                                    chat_id_candidates.append(str(details.get("chat_id")))
                            elif isinstance(details, str):
                                chat_id_candidates.append(str(details))

                            matched = False
                            for alias in contact_aliases:
                                stem = alias[:-1] if alias.endswith(('а', 'я', 'е', 'у', 'ы', 'о')) else alias
                                if alias in query or (len(stem) > 2 and stem in query):
                                    matched = True
                                    break
                            
                            if resolved_name.lower() in query or resolved_name.lower()[:-1] in query:
                                matched = True

                            if matched or ("от" in query and resolved_name.lower() in query):
                                target_contact_name = resolved_name
                                target_chat_ids = chat_id_candidates
                                break

                        # 2. Fallback check if name follows "от"
                        if not target_contact_name and "от" in query:
                            parts_q = query.split("от")
                            if len(parts_q) > 1:
                                potential_name = parts_q[1].strip().split()[0].lower()
                                for contact_key, details in self.contacts.items():
                                    r_name = details.get("name", "").lower() if isinstance(details, dict) else str(contact_key).lower()
                                    if potential_name in r_name or potential_name in str(contact_key).lower():
                                        target_contact_name = details.get("name") if isinstance(details, dict) else str(contact_key)
                                        if isinstance(details, dict):
                                            if details.get("CHANNEL_ID"): chat_id_candidates.append(str(details.get("CHANNEL_ID")))
                                            if details.get("chat_id"): chat_id_candidates.append(str(details.get("chat_id")))
                                        break
                                if not target_contact_name:
                                    target_contact_name = potential_name.capitalize()

                        all_known = list(self.unread_messages) + list(self.read_messages)
                        
                        if target_contact_name or target_chat_ids:
                            # --- BRANCH A: Specific Contact Messages ---
                            contact_msgs = []
                            matched_chat_ids = set(target_chat_ids)
                            
                            for ck, cd in self.contacts.items():
                                if isinstance(cd, dict):
                                    for k_sub in ["CHANNEL_ID", "chat_id"]:
                                        if cd.get(k_sub):
                                            matched_chat_ids.add(str(cd.get(k_sub)))

                            for m in all_known:
                                m_chat_id = str(m.get("chat_id", ""))
                                sender_str = str(m.get("sender", "")).lower()
                                
                                match_found = False
                                if m_chat_id in matched_chat_ids:
                                    match_found = True
                                if target_contact_name and target_contact_name.lower() in sender_str:
                                    match_found = True
                                    
                                if match_found:
                                    contact_msgs.append(m)

                            if not contact_msgs and target_chat_ids:
                                contact_msgs = self.fetch_telegram_channel_history(target_chat_ids[-1], limit=10)

                            if contact_msgs:
                                target_msgs = contact_msgs[-10:] if len(contact_msgs) >= 10 else contact_msgs
                                
                                lines = [f"--- Сообщения от {target_contact_name or 'контакта'} ---"]
                                
                                # Track which of these displayed messages are currently unread
                                unread_texts_in_target = {m.get("text") for m in self.unread_messages}
                                newly_read_spoken_parts = []

                                for idx, m in enumerate(target_msgs, 1):
                                    is_unread_item = m.get("text") in unread_texts_in_target or m in self.unread_messages
                                    status_tag = "" if not is_unread_item else " (новое)"
                                    lines.append(f"{idx}. ({m['time']}){status_tag}: {m['text']}")
                                    
                                    if is_read_command and is_unread_item:
                                        newly_read_spoken_parts.append(f"От {m['sender']}: {m['text']}")

                                # Mark viewed messages as read and save
                                viewed_texts = {m.get("text") for m in target_msgs}
                                remaining_unread = []
                                for m in self.unread_messages:
                                    if m.get("text") in viewed_texts or str(m.get("chat_id")) in matched_chat_ids:
                                        self.read_messages.append(m)
                                    else:
                                        remaining_unread.append(m)
                                self.unread_messages = remaining_unread
                                self.save_messages()
                                
                                if self.display_mode == "clock":
                                    if self.unread_messages:
                                        self.alarm_display_label.config(text=f"💬 Новых сообщений: {len(self.unread_messages)}")
                                    elif self.alarm_time:
                                        self.alarm_display_label.config(text=f"⏰ Будильник: {self.alarm_time}")
                                    else:
                                        self.alarm_display_label.config(text="")

                                disp_name = target_contact_name or "контакта"
                                self.show_info_screen(f"Сообщения: {disp_name}", "\n".join(lines), "")
                                
                                # Speak ONLY if explicitly asked to read AND there are unread messages from this contact
                                if is_read_command:
                                    if newly_read_spoken_parts:
                                        self.speak_russian(". ".join(newly_read_spoken_parts))
                                    else:
                                        self.speak_russian("Новых непрочитанных сообщений от этого контакта нет.")
                            else:
                                self.speak_russian(f"Нет сохраненных сообщений от этого контакта.")
                        
                        elif "покажи" in query or "последнее" in query or "последние" in query:
                            # --- BRANCH B: General display screen (Silent) ---
                            texts, _ = self.get_formatted_messages_display()
                            self.show_info_screen("Все сообщения", texts, "")
                        
                        else:
                            # --- BRANCH C: Read unread messages aloud ---
                            if not self.unread_messages:
                                self.speak_russian("У вас нет новых входящих сообщений.")
                            else:
                                target_read = list(self.unread_messages)
                                read_summary_parts = []
                                for m in target_read:
                                    read_summary_parts.append(f"От {m['sender']}: {m['text']}")
                                
                                self.speak_russian(". ".join(read_summary_parts))
                                
                                if self.display_mode == "clock":
                                    if self.alarm_time:
                                        self.alarm_display_label.config(text=f"⏰ Будильник: {self.alarm_time}")
                                    else:
                                        self.alarm_display_label.config(text="")

                            while self.unread_messages:
                                self.read_messages.append(self.unread_messages.pop(0))
                            self.save_messages()
                    elif any(w in query for w in ["удали", "сотри", "убери"]) and any(w in query for w in ["напоминание", "напоминания"]):
                        number_words = {
                            "один": 1, "одну": 1, "первое": 1, "первый": 1,
                            "два": 2, "две": 2, "второе": 2, "второй": 2,
                            "три": 3, "третье": 3, "третий": 3,
                            "четыре": 4, "четвертое": 4, "четвертый": 4,
                            "пять": 5, "пятое": 5, "пятый": 5,
                            "шесть": 6, "шестое": 6, "шестой": 6,
                            "семь": 7, "седьмое": 7, "седьмой": 7,
                            "восемь": 8, "восьмое": 8, "восьмой": 8,
                            "девять": 9, "девятое": 9, "девятый": 9,
                            "десять": 10, "десятое": 10, "десятый": 10
                        }
                        
                        rem_num = None
                        # 1. Try finding numeric digits first
                        match_num = re.search(r"(?:удали|удалить|сотри|убери)\s+(?:напоминание\s+)?(?:номер\s+)?(\d+)", query)
                        if match_num:
                            rem_num = int(match_num.group(1))
                        else:
                            # 2. Check for written number words using word boundaries or exact checks
                            words_in_query = query.split()
                            for word, val in number_words.items():
                                if word in words_in_query:
                                    rem_num = val
                                    break
                                    
                        if rem_num:
                            self.remove_reminder_by_index(rem_num)
                        else:
                            self.speak_russian("Укажите номер напоминания для удаления.")

                    # 5. General Commands & AI Fallback
                    elif any(w in query for w in ["список команд", "команды", "справка"]):
                        self.show_info_screen("Справочник команд", COMMAND_LIST_TEXT, "Показываю список доступных команд.")
                    elif any(w in query for w in ["погода", "погоды", "прогноз"]):
                        if "завтра" in query:
                            self.show_info_screen("Прогноз на завтра", self.tomorrow_hourly_forecast, "Показываю прогноз на завтра.")
                        elif "неделя" in query or "неделю" in query:
                            self.show_info_screen("Прогноз на неделю", self.weekly_forecast_text, "Прогноз на неделю.")
                        else:
                            self.show_info_screen(f"Погода ({self.city})", self.current_weather_text, f"Погода в городе {self.city}: {self.current_weather_text}")
                    elif "который час" in query or any(w in query for w in ["сколько","сейчас"]) and any(w in query for w in ["времени","время"]):
                        current_time_str = time.strftime('%H:%M')
                        self.speak_russian(f"Сейчас {current_time_str}")
                    
                    # --- NEW: Day of week query ---
                    elif any(w in query for w in ["день недели", "какой сегодня день"]) or ("какой" in query and "день" in query):
                        days_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
                        t = time.localtime()
                        current_day_name = days_ru[t.tm_wday]
                        self.speak_russian(f"Сегодня {current_day_name.lower()}")
                    
                    # --- NEW: Date query ---
                    elif any(w in query for w in ["какое сегодня число", "сегодня какое число", "какая дата", "сегодняшняя дата"]):
                        days_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
                        months_ru = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
                        t = time.localtime()
                        date_speech = f"Сегодня {days_ru[t.tm_wday]}, {t.tm_mday} {months_ru[t.tm_mon]} {t.tm_year} года"
                        self.speak_russian(date_speech)
                    elif any(w in query for w in ["напоминания", "список напоминаний"]):
                        texts, announcement = self.get_formatted_reminders_display()
                        self.show_info_screen("Активные напоминания", texts, announcement)
                    elif any(w in query for w in ["включи часы", "покажи часы"]):
                        self.restore_clock_mode()
                        self.speak_russian("Включаю часы.")
                    elif any(w in query for w in ["включи свет"]):
                        self.turn_light_on()
                        self.speak_russian("Включаю свет.")
                    elif any(w in query for w in ["выключи свет"]):
                        self.turn_light_off()
                        self.speak_russian("Выключаю свет.")
                    elif any(w in query for w in ["выключить компьютер", "выключи компьютер"]):
                        self.shutdown_computer()
                    elif any(w in query for w in ["выключи экран"]):
                        self.turn_screen_off()
                        self.speak_russian("Экран выключен.")
                    elif any(w in query for w in ["включи экран"]):
                        self.turn_screen_on()
                        self.speak_russian("Экран включен.")
                    elif "обновись" in query:
                        self.check_for_updates(manual=True)
                    else:
                        ai_answer = self.ask_ai(query)
                        self.speak_russian(ai_answer)

            except Exception as e:
                print(f"Listener loop exception: {e}")
                if stream:
                    try: stream.stop_stream(); stream.close()
                    except: pass
                time.sleep(1)
        p.terminate()

    def on_closing(self):
        self.is_running = False
        self.stop_all_audio()
        if self.weather_timer and self.weather_timer.is_alive():
            self.weather_timer.cancel()
        try: self.root.destroy()
        except: pass
        os._exit(0)

if __name__ == "__main__":
    root = tk.Tk()
    app = AlisaApp(root)
    root.mainloop()
