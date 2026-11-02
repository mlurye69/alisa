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
import atexit
from datetime import datetime, timedelta

# --- PATH SETUP ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "alisa.json")
REMINDERS_FILE = os.path.join(SCRIPT_DIR, "reminders.json")

MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12
}

COMMAND_LIST_TEXT = (
    "Доступные голосовые команды:\n"
    "• Алиса, будильник на [время]\n"
    "• Алиса, введи/отмени режим тревоги\n"
    "• Алиса, включи свет / выключи свет\n"
    "• Алиса, включи часы / покажи часы\n"
    "• Алиса, выключи будильник\n"
    "• Алиса, выключить компьютер\n"
    "• Алиса, выключи экран\n"
    "• Алиса, добавь ежедневное напоминание [текст] в [час] часов\n"
    "• Алиса, как сказать [фраза] / переведи [фраза]\n"
    "• Алиса, напиши [Имя] [текст]\n"
    "• Алиса, напомни [текст] [дата] в [время]\n"
    "• Алиса, напомни [текст] через [X] минут/часов\n"
    "• Алиса, обновись\n"
    "• Алиса, погода / сегодня / завтра / неделя\n"
    "• Алиса, прочти\n"
    "• Алиса, список команд\n"
    "• Алиса, список напоминаний\n"
    "• Алиса, удали напоминание [номер]\n"
    "• Алиса, [ваш вопрос для ИИ]\n"
    "• Алиса, который час"
)


class AlisaApp:
    def __init__(self, root):
        self.is_running = True
        self.root = root
        self.root.title("Smart Clock")
        self.root.attributes('-fullscreen', True)
        self.root.configure(background='black')
        self.root.config(cursor="none")

        self.load_config()
       
        # State variables
        self.current_weather_text = "Погода еще не загружена"
        self.today_hourly_forecast = "Почасовой прогноз на сегодня загружается..."
        self.tomorrow_hourly_forecast = "Почасовой прогноз на завтра загружается..."
        self.weekly_forecast_text = "Прогноз на неделю загружается..."

        self.alarm_time = None
        self.alarm_triggered_today = False
        self.alarm_active = False
        self.alarm_stop_event = threading.Event()
        self.active_audio_process = None

        self.display_mode = "clock"
        self.light_timer = None
        self.weather_timer = None
        self.countdown_seconds = 0

        self.reminders = []
        
        # 1. Setup UI elements first
        self.setup_ui()
        
        # 2. Load reminders and restore saved alarms safely
        self.load_reminders()
        
        # Register exit handler
        atexit.register(self.stop_all_audio)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.bind('<Escape>', lambda e: self.on_closing())

        # Start background tasks
        self.update_display()
        self.fetch_weather()
        self.shift_position()
        self.restore_pending_reminders()

        threading.Thread(target=self.voice_listener_loop, daemon=True).start()
        self.root.after(5000, lambda: self.check_for_updates(manual=False))

    def load_config(self):
        default_config = {
            "GROQ_API_KEY": "gsk_YOUR_GROQ_API_KEY_HERE",
            "WEATHER_API_KEY": "YOUR_WEATHER_API_KEY",
            "CITY": "Boston",
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_CHAT_ID": "",
            "CONTACTS": {},
            "VOSK_MODEL_PATH": os.path.join(SCRIPT_DIR, "vosk-model-small-ru-0.22"),
            "GITHUB_RAW_URL": "",
            "CHIME_SOUND_PATH": "Sounds/alarm-clock-1.mp3"
        }
        
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    default_config.update(loaded)
            except Exception as e:
                print(f"Ошибка чтения alisa.json: {e}")
        
        self.groq_api_key = default_config["GROQ_API_KEY"]
        self.weather_api_key = default_config["WEATHER_API_KEY"]
        self.city = default_config["CITY"] or self.get_city_by_ip()
        
        self.telegram_bot_token = default_config.get("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = default_config.get("TELEGRAM_CHAT_ID", "")
        self.contacts = default_config.get("CONTACTS", {})
        
        self.model_path = default_config.get("VOSK_MODEL_PATH", os.path.join(SCRIPT_DIR, "vosk-model-small-ru-0.22"))
        self.github_raw_url = default_config.get("GITHUB_RAW_URL", "https://raw.githubusercontent.com/mlurye69/alisa/main/alisa.py")
        
        raw_chime = default_config.get("CHIME_SOUND_PATH", "Sounds/alarm-clock-1.mp3")
        self.chime_sound_path = raw_chime if os.path.isabs(raw_chime) else os.path.join(SCRIPT_DIR, raw_chime)

    def load_reminders(self):
        if os.path.exists(REMINDERS_FILE):
            try:
                with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
                    self.reminders = json.load(f)
                    self.reminders.sort(key=lambda x: x["timestamp"])
                    
                    # Check if there's a saved alarm in reminders
                    for rem in self.reminders:
                        if rem.get("is_alarm"):
                            self.alarm_time = rem.get("alarm_time_str")
                            if self.alarm_time:
                                self.alarm_display_label.config(text=f"⏰ Будильник: {self.alarm_time}")
                            break
            except Exception as e:
                print(f"Ошибка загрузки reminders.json: {e}")
                self.reminders = []

    def get_formatted_reminders_display(self):
        # Filter out internal alarms from the standard reminders list view
        display_reminders = [r for r in self.reminders if not r.get("is_alarm")]
        
        if not display_reminders:
            return "Активных напоминаний нет.", "У вас нет активных напоминаний."

        daily_reminders = [r for r in display_reminders if r.get("daily")]
        one_off_reminders = [r for r in display_reminders if not r.get("daily")]

        daily_reminders.sort(key=lambda x: datetime.fromtimestamp(x["timestamp"]).strftime('%H:%M'))
        one_off_reminders.sort(key=lambda x: x["timestamp"])

        combined_list = []
        lines = []
        current_index = 1

        if daily_reminders:
            lines.append("--- Daily Reminders ---")
            for rem in daily_reminders:
                dt_str = datetime.fromtimestamp(rem["timestamp"]).strftime('%H:%M')
                lines.append(f"{current_index}. {rem['text']} ({dt_str})")
                combined_list.append((current_index, rem))
                current_index += 1

        if one_off_reminders:
            if daily_reminders:
                lines.append("")
            lines.append("--- Reminders ---")
            for rem in one_off_reminders:
                dt_str = datetime.fromtimestamp(rem["timestamp"]).strftime('%d.%m %H:%M')
                lines.append(f"{current_index}. {rem['text']} ({dt_str})")
                combined_list.append((current_index, rem))
                current_index += 1

        texts = "\n".join(lines)
        return texts, "Показываю активные напоминания."

    def save_reminders(self):
        try:
            with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.reminders, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Ошибка сохранения reminders.json: {e}")

    def restore_pending_reminders(self):
        now = datetime.now().timestamp()
        valid_reminders = []
        for rem in self.reminders:
            target_time = rem.get("timestamp", 0)
            if rem.get("is_alarm"):
                # Always keep alarm in valid reminders and re-adjust timestamp if it has passed today
                if target_time <= now:
                    dt = datetime.fromtimestamp(target_time)
                    next_dt = dt + timedelta(days=1)
                    rem["timestamp"] = next_dt.timestamp()
                valid_reminders.append(rem)
                continue
                
            if target_time > now:
                valid_reminders.append(rem)
                delay = target_time - now
                threading.Timer(delay, self.trigger_reminder, args=[rem]).start()
            elif rem.get("daily", False):
                dt = datetime.fromtimestamp(target_time)
                next_dt = dt.replace(year=datetime.now().year, month=datetime.now().month, day=datetime.now().day)
                if next_dt.timestamp() <= now:
                    next_dt += timedelta(days=1)
                rem["timestamp"] = next_dt.timestamp()
                valid_reminders.append(rem)
                delay = next_dt.timestamp() - now
                threading.Timer(delay, self.trigger_reminder, args=[rem]).start()
        self.reminders = valid_reminders
        self.reminders.sort(key=lambda x: x["timestamp"])
        self.save_reminders()

    def add_reminder(self, text, delay_seconds, daily=False):
        now = datetime.now()
        target_timestamp = now.timestamp() + delay_seconds
        
        for rem in self.reminders:
            if rem["text"].lower() == text.lower() and rem.get("daily") == daily:
                return

        reminder = {
            "text": text,
            "timestamp": target_timestamp,
            "daily": daily
        }
        self.reminders.append(reminder)
        self.reminders.sort(key=lambda x: x["timestamp"])
        self.save_reminders()
        
        if self.display_mode == "info" and ("напоминаний" in self.date_label.cget("text").lower() or "список" in self.date_label.cget("text").lower()):
            self.refresh_reminder_info_screen()
        
        def schedule_timer():
            try:
                threading.Timer(delay_seconds, self.trigger_reminder, args=[reminder]).start()
            except Exception as e:
                print(f"DEBUG ОШИБКА в schedule_timer: {e}")

        self.root.after(0, schedule_timer)

    def trigger_reminder(self, reminder):
        if reminder not in self.reminders:
            return
            
        self.speak_russian(f"Напоминание: {reminder['text']}")
        
        if reminder.get("daily", False):
            now = datetime.now()
            dt = datetime.fromtimestamp(reminder["timestamp"])
            next_dt = dt + timedelta(days=1)
            reminder["timestamp"] = next_dt.timestamp()
            self.reminders.sort(key=lambda x: x["timestamp"])
            self.save_reminders()
            
            delay = next_dt.timestamp() - now.timestamp()
            threading.Timer(delay, self.trigger_reminder, args=[reminder]).start()
        else:
            self.reminders = [r for r in self.reminders if r != reminder]
            self.save_reminders()

    def remove_reminder_by_index(self, index_1_based):
        try:
            display_reminders = sorted([r for r in self.reminders if not r.get("is_alarm")], key=lambda x: x["timestamp"])
            idx = index_1_based - 1
            
            if 0 <= idx < len(display_reminders):
                target_rem = display_reminders[idx]
                self.reminders.remove(target_rem)
                self.save_reminders()
                
                if self.display_mode == "info" and ("напоминаний" in self.date_label.cget("text").lower() or "список" in self.date_label.cget("text").lower()):
                    self.refresh_reminder_info_screen()
                    
                self.speak_russian(f"Напоминание {index_1_based} удалено.")
            else:
                self.speak_russian("Такого номера напоминания нет.")
        except Exception as e:
            print(f"Ошибка удаления напоминания: {e}")
            self.speak_russian("Не удалось удалить напоминание.")

    def refresh_reminder_info_screen(self):
        texts, _ = self.get_formatted_reminders_display()
        if [r for r in self.reminders if not r.get("is_alarm")]:
            self.weather_desc_label.config(text=texts, font=('Helvetica', 20, 'bold'), fg='lightseagreen')
            self.weather_temp_label.config(text="")
        else:
            self.weather_desc_label.config(text="Активных напоминаний нет.", font=('Helvetica', 20, 'bold'), fg='lightseagreen')
            self.weather_temp_label.config(text="")

    def send_telegram_alert(self, message):
        if not self.telegram_bot_token or self.telegram_bot_token == "YOUR_TELEGRAM_BOT_TOKEN":
            print("Telegram token not configured.")
            return
        try:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            payload = {
                "chat_id": self.telegram_chat_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            print(f"Failed to send Telegram alert: {e}")

    def send_telegram_message_to_chat(self, chat_id, message):
        if not self.telegram_bot_token:
            print("Telegram token not configured.")
            return False
        try:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            response = requests.post(url, json=payload, timeout=5)
            return response.status_code == 200
        except Exception as e:
            print(f"Failed to send targeted Telegram message: {e}")
            return False

    def trigger_emergency_mode(self):
        self.speak_russian("Внимание! Включаю режим тревоги и отправляю экстренное сообщение.")
        
        threading.Thread(
            target=self.send_telegram_alert,
            args=("⚠️ **ТРЕВОГА НА УМНЫХ ЧАСАХ АЛИСЫ!**\n\nПользователь запросил помощь (голосовая команда тревоги). Требуется немедленное внимание!",),
            daemon=True
        ).start()
        
        emergency_text = (
            "⚠️ ВНИМАНИЕ: ТРЕВОГА! ⚠️\n\n"
            "Экстренное уведомление отправлено близким.\n\n"
            "Экстренная служба: 911\n\n"
            "Для отмены скажите 'Алиса, отмена тревоги'"
        )
        
        # Switch to emergency screen
        self.display_mode = "emergency"
        if self.weather_timer and self.weather_timer.is_alive():
            self.weather_timer.cancel()

        self.frame.place(x=40, y=40, relwidth=0.9, relheight=0.9, anchor='nw')
        
        self.date_label.config(text="Режим тревоги", fg="#ff69b4", font=('Helvetica', 45, 'bold'))
        self.time_label.config(text="", font=('Helvetica', 10))
        self.alarm_display_label.config(text="")
        
        self.weather_desc_label.config(text=emergency_text, font=('Helvetica', 32, 'bold'), fg="#ff69b4")
        self.weather_temp_label.config(text="")
        
        self.trigger_emergency_chirp(first_run=True)

    def trigger_emergency_chirp(self, first_run=False):
        if self.display_mode != "emergency":
            return
            
        try:
            subprocess.Popen(
                ["play", "-q", "-n", "synth", "0.08", "sine", "2500", "vol", "0.1"], 
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL
            )
        except Exception:
            pass
            
        self.emergency_chirp_timer = threading.Timer(60.0, self.trigger_emergency_chirp)
        self.emergency_chirp_timer.start()

    def get_city_by_ip(self):
        try:
            response = requests.get("https://ipinfo.io/json", timeout=5)
            if response.status_code == 200:
                city = response.json().get("city")
                if city:
                    return city
        except Exception as e:
            print(f"Не удалось определить город по IP: {e}")
        return "Boston"

    def setup_ui(self):
        self.frame = tk.Frame(self.root, bg='black')
        self.frame.place(relx=0.05, rely=0.05, relwidth=0.9, relheight=0.9)

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
        
        self.weather_desc_label.pack_forget()
        self.weather_temp_label.pack_forget()
        self.date_label.pack_forget()
        self.time_label.pack_forget()
        self.alarm_display_label.pack_forget()
        
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
        
        self.weather_desc_label.pack(anchor='w', pady=(0, 2))
        self.weather_temp_label.pack(anchor='w', pady=(0, 10))
        self.date_label.pack(anchor='w', pady=(0, 15))
        self.time_label.pack(anchor='w', pady=(0, 0))
        self.alarm_display_label.pack(anchor='w', pady=(10, 0))
        
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

    def update_countdown_timer(self):
        if self.display_mode == "clock":
            return
            
        if self.countdown_seconds > 0:
            mins = self.countdown_seconds // 60
            secs = self.countdown_seconds % 60
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
        if self.alarm_time:
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
                try:
                    self.active_audio_process.kill()
                except Exception:
                    pass
            self.active_audio_process = None

    def speak_russian(self, text, lang='ru'):
        if self.active_audio_process:
            try:
                self.active_audio_process.terminate()
                self.active_audio_process.kill()
                self.active_audio_process.wait(timeout=0.2)
            except Exception:
                pass
            self.active_audio_process = None

        os.system("pkill -9 mpg123 2>/dev/null")
        os.system("pkill -9 play 2>/dev/null")
        os.system("pkill -9 aplay 2>/dev/null")
        os.system("pkill -9 paplay 2>/dev/null")

        def speech_worker():
            try:
                from gtts import gTTS
                audio_file = os.path.join(SCRIPT_DIR, "response.mp3")
                
                tts = gTTS(text=text, lang=lang, slow=False)
                tts.save(audio_file)
                
                self.active_audio_process = subprocess.Popen(
                    ["mpg123", "-q", audio_file],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                self.active_audio_process.wait()
                
                if os.path.exists(audio_file):
                    os.remove(audio_file)
                self.active_audio_process = None
            except Exception as e:
                print(f"Ошибка речи: {e}")

        time.sleep(0.1)
        threading.Thread(target=speech_worker, daemon=True).start()

    def update_display(self):
        if self.display_mode == "clock":
            days_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
            months_ru = ["", "Января", "Февраля", "Марта", "Апреля", "Мая", "Июня", "Июля", "Августа", "Сентября", "Октября", "Ноября", "Декабря"]
            
            t = time.localtime()
            date_str = f"{days_ru[t.tm_wday]}, {t.tm_mday} {months_ru[t.tm_mon]}"
            current_hm = time.strftime('%H:%M')
            
            self.date_label.config(text=date_str)
            self.time_label.config(text=time.strftime('%H:%M:%S'), font=('Helvetica', 110, 'bold'))
            
            parts = self.current_weather_text.split(", ")
            city_prefix = f"Погода в {self.city}:"
            if len(parts) >= 2:
                weather_full_text = f"{city_prefix} {parts[0]}, {parts[1]}"
            else:
                weather_full_text = f"{city_prefix} {self.current_weather_text}"

            self.weather_desc_label.config(text=weather_full_text, font=('Helvetica', 22, 'bold'), fg='lightseagreen', wraplength=1200)
            self.weather_temp_label.config(text="", font=('Helvetica', 22, 'bold'))
            
            if self.alarm_time and current_hm == self.alarm_time and not self.alarm_triggered_today:
                self.alarm_triggered_today = True
                threading.Thread(target=self.trigger_alarm, daemon=True).start()
            elif current_hm != self.alarm_time:
                self.alarm_triggered_today = False
                
        self.root.after(1000, self.update_display)

    def shift_position(self):
        if self.display_mode in ["clock", "emergency"]:
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            
            max_x = max(20, screen_width - 850)
            max_y = max(20, screen_height - 500)
            
            self.frame.place(x=random.randint(20, max_x),
                             y=random.randint(20, max_y),
                             relwidth=0.9, relheight=0.9, anchor='nw')
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
        
        # Advance alarm to next day if daily, or clear it
        for rem in self.reminders:
            if rem.get("is_alarm"):
                dt = datetime.fromtimestamp(rem["timestamp"])
                next_dt = dt + timedelta(days=1)
                rem["timestamp"] = next_dt.timestamp()
                self.save_reminders()
                break

    def fetch_weather(self):
        if self.weather_api_key != "YOUR_WEATHER_API_KEY":
            try:
                url_current = f"https://api.openweathermap.org/data/2.5/weather?q={self.city}&appid={self.weather_api_key}&units=metric&lang=ru"
                resp_curr = requests.get(url_current, timeout=10)
                if resp_curr.status_code == 200:
                    data = resp_curr.json()
                    self.current_weather_text = f"{data['weather'][0]['description'].capitalize()}, {round(data['main']['temp'])}°C"

                url_forecast = f"https://api.openweathermap.org/data/2.5/forecast?q={self.city}&appid={self.weather_api_key}&units=metric&lang=ru"
                resp_fore = requests.get(url_forecast, timeout=10)
                if resp_fore.status_code == 200:
                    fdata = resp_fore.json()
                    today_date = datetime.now().strftime('%Y-%m-%d')
                    tomorrow_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
                    
                    today_lines, tomorrow_lines, weekly_lines, seen_dates = [], [], [], set()
                    days_full_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
                    
                    for item in fdata.get("list", []):
                        item_date, item_time = item["dt_txt"].split()
                        line = f"{item_time[:5]}: {item['weather'][0]['description']}, {round(item['main']['temp'])}°C"
                        if item_date == today_date:
                            today_lines.append(line)
                        elif item_date == tomorrow_date:
                            tomorrow_lines.append(line)
                        if item_date not in seen_dates and "12:00" in item_time:
                            seen_dates.add(item_date)
                            dt_obj = datetime.strptime(item_date, '%Y-%m-%d')
                            weekly_lines.append(f"{days_full_ru[dt_obj.weekday()]} : {item['weather'][0]['description']}, {round(item['main']['temp'])}°C")
                    
                    self.today_hourly_forecast = "\n".join(today_lines) if today_lines else "Недоступно."
                    self.tomorrow_hourly_forecast = "\n".join(tomorrow_lines) if tomorrow_lines else "Недоступно."
                    self.weekly_forecast_text = "\n".join(weekly_lines) if weekly_lines else "Недоступно."
            except Exception as e:
                print(f"Weather fetch error: {e}")
        else:
            self.current_weather_text = "API ключ погоды не настроен"
        
        self.root.after(600000, self.fetch_weather)

    def ask_ai(self, prompt):
        if self.groq_api_key == "gsk_YOUR_GROQ_API_KEY_HERE":
            return "Пожалуйста, настройте API ключ Groq в коде."
        try:
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.groq_api_key}", "Content-Type": "application/json"},
                json={
                    "model": "qwen/qwen3.8-27b",
                    "messages": [
                        {"role": "system", "content": "Ты голосовой ассистент на умных часах. Отвечай кратко, по делу, исключительно на русском языке, максимум в 1-2 предложениях."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 150
                },
                timeout=10
            )
            if response.status_code == 200:
                answer = response.json()["choices"][0]["message"]["content"]
                if answer: return answer.strip()
        except Exception as e:
            print(f"Groq connection error: {e}")
        return "Не удалось связаться с искусственным интеллектом."

    def check_for_updates(self, manual=False):
        def update_worker():
            if manual:
                self.speak_russian("Проверяю обновления.")
                time.sleep(3.5)
            try:
                response = requests.get(self.github_raw_url, timeout=10)
                if response.status_code == 200:
                    remote_code = response.text
                    script_path = os.path.abspath(__file__)
                    with open(script_path, "r", encoding="utf-8") as f:
                        local_code = f.read()
                    if remote_code.strip() != local_code.strip():
                        with open(script_path, "w", encoding="utf-8") as f:
                            f.write(remote_code)
                        self.speak_russian("Обновление загружено. Перезапуск.")
                        time.sleep(3.0)
                        os.execv(sys.executable, ['python'] + sys.argv)
                    elif manual:
                        self.speak_russian("Обновлений не найдено.")
                elif manual:
                    self.speak_russian("Не удалось получить ответ от сервера.")
            except Exception:
                if manual:
                    self.speak_russian("Ошибка соединения с сетью.")
        threading.Thread(target=update_worker, daemon=True).start()

    def voice_listener_loop(self):
        time.sleep(5)
        import pyaudio
        from vosk import Model, KaldiRecognizer
        import audioop

        if not os.path.exists(self.model_path):
            print(f"Модель Vosk не найдена по пути: {self.model_path}")
            return

        try:
            vosk_model = Model(self.model_path)
        except Exception as e:
            print(f"Ошибка загрузки модели Vosk: {e}")
            return

        RATE, CHUNK, CHANNELS = 16000, 4000, 1
        p = pyaudio.PyAudio()
        r = sr.Recognizer()

        print("Локальный сторож Vosk запущен...")

        while self.is_running:
            stream = None
            try:
                rec = KaldiRecognizer(vosk_model, RATE)
                rec.SetWords(False)
                stream = p.open(format=pyaudio.paInt16, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)

                while self.is_running:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    if data and rec.AcceptWaveform(data):
                        recognized_text = json.loads(rec.Result()).get("text", "").lower()
                        # --- CHECK EMERGENCY WAKE PHRASES FIRST ---
                        if any(phrase in recognized_text for phrase in ["алиса тревога", "алиса мне плохо"]):
                            self.trigger_emergency_mode()
                            break
                        # ------------------------------------------
                        
                        if "алиса" in recognized_text or "alisa" in recognized_text:
                            self.speak_russian("Слушаю.")
                            break

                if not self.is_running:
                    break

                frames, silent_chunks = [], 0
                silence_threshold_chunks = int((RATE / CHUNK) * 3)

                while self.is_running:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    if not data: break
                    frames.append(data)
                    if audioop.rms(data, 2) < 300:
                        silent_chunks += 1
                    else:
                        silent_chunks = 0
                    if silent_chunks > silence_threshold_chunks:
                        break

                if stream:
                    stream.stop_stream()
                    stream.close()
                    stream = None

                if frames and self.is_running:
                    try:
                        query = r.recognize_google(sr.AudioData(b''.join(frames), RATE, 2), language="ru-RU").lower()
                    except Exception as e:
                        print(f"Ошибка распознавания речи: {e}")
                        continue
                        
                    print(f"Распознано: {query}")
                    
                    # --- EMERGENCY CANCELLATION CHECKS (RUNS FIRST) ---
                    if any(phrase in query for phrase in ["отмена тревог", "отмени тревогу", "все хорошо", "всё в порядке"]):
                        self.speak_russian("Режим тревоги отменен.")
                        threading.Thread(
                            target=self.send_telegram_alert,
                            args=("✅ **ТРЕВОГА ОТМЕНЕНА НА УМНЫХ ЧАСАХ АЛИСЫ!**\n\nПользователь отменил тревогу. Ситуация в норме.",),
                            daemon=True
                        ).start()
                        self.restore_clock_mode()
                        continue
                    # ---------------------------------------------------

                    if "будильник" in query and any(w in query for w in ["выключи", "отмени", "удали", "сотри"]):
                        self.alarm_time = None
                        self.alarm_display_label.config(text="")
                        self.reminders = [r for r in self.reminders if not r.get("is_alarm")]
                        self.save_reminders()
                        self.speak_russian("Будильник выключен.")
                    elif "будильник" in query:
                        match_alarm = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(?:час(?:а|ов)?)?\s*(вечера|дня|утра)?", query)
                        if match_alarm:
                            hour = int(match_alarm.group(1))
                            minute = int(match_alarm.group(2)) if match_alarm.group(2) else 0
                            modifier = match_alarm.group(3)
                            
                            if modifier in ["вечера", "дня"] and hour < 12:
                                hour += 12
                            elif modifier == "утра" and hour == 12:
                                hour = 0
                                
                            self.alarm_time = f"{hour:02d}:{minute:02d}"
                            self.alarm_triggered_today = False
                            self.alarm_display_label.config(text=f"⏰ Будильник: {self.alarm_time}")
                            
                            now_dt = datetime.now()
                            target_dt = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
                            if target_dt.timestamp() <= now_dt.timestamp():
                                target_dt += timedelta(days=1)
                                
                            self.reminders = [r for r in self.reminders if not r.get("is_alarm")]
                            alarm_reminder = {
                                "text": f"Будильник на {self.alarm_time}",
                                "timestamp": target_dt.timestamp(),
                                "daily": True,
                                "is_alarm": True,
                                "alarm_time_str": self.alarm_time
                            }
                            self.reminders.append(alarm_reminder)
                            self.save_reminders()
                            
                            self.speak_russian(f"Будильник установлен на {hour:02d}:{minute:02d}.")
                        else:
                            self.speak_russian("Не поняла время будильника.")
                    elif "напомни" in query:
                        match_delay = re.search(r"напомни\s+(.*?)\s+через\s+(\d+)\s*(минут|минуту|минуты|час|часа|часов)", query)
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
                    elif any(w in query for w in ["пошли сообщение", "отправь сообщение", "пошли смс", "напиши"]):
                        remainder = re.sub(r"^(?:пошли сообщение|отправь сообщение|пошли смс|напиши)\s+", "", query, flags=re.IGNORECASE).strip()
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
                    elif any(w in query for w in ["включи часы", "переключись на часы", "вернись к часам", "покажи часы"]):
                        self.speak_russian("Включаю часы.")
                        self.restore_clock_mode()
                    elif any(w in query for w in ["включи свет", "свет включи", "белый экран"]):
                        self.speak_russian("Включаю свет.")
                        self.turn_light_on()
                    elif any(w in query for w in ["выключи свет", "свет выключи"]):
                        self.speak_russian("Выключаю свет.")
                        self.turn_light_off()
                    elif any(w in query for w in ["список напоминаний", "покажи напоминания", "покажи список напоминаний"]):
                        texts, announcement = self.get_formatted_reminders_display()
                        self.show_info_screen("Список напоминаний", texts, announcement)
                    elif any(w in query for w in ["удали напоминание", "сотри напоминание", "убери напоминание"]):
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
                        match_num = re.search(r"(?:удали|удалить|сотри|убери)\s+(?:напоминание\s+)?(?:номер\s+)?(\d+)", query)
                        if match_num:
                            rem_num = int(match_num.group(1))
                        else:
                            for word, val in number_words.items():
                                if word in query:
                                    rem_num = val
                                    break
                                    
                        if rem_num:
                            self.remove_reminder_by_index(rem_num)
                        else:
                            self.speak_russian("Укажите номер напоминания для удаления.")
                    elif any(w in query for w in ["список команд", "команды", "справка"]):
                        self.show_info_screen("Справочник команд", COMMAND_LIST_TEXT, "Показываю список доступных команд.")
                    elif "погода" in query or "сегодня" in query:
                        self.show_info_screen(f"Погода ({self.city})", self.current_weather_text, f"Погода в городе {self.city}: {self.current_weather_text}")
                    elif "завтра" in query:
                        self.show_info_screen("Прогноз на завтра", self.tomorrow_hourly_forecast, "Показываю прогноз на завтра.")
                    elif "неделя" in query or "неделю" in query:
                        self.show_info_screen("Прогноз на неделю", self.weekly_forecast_text, "Прогноз на неделю.")
                    elif "который час" in query:
                        current_time_str = time.strftime('%H:%M')
                        self.speak_russian(f"Сейчас {current_time_str}")
                    elif "выключи экран" in query:
                        self.speak_russian("Экран выключен.")
                    elif "обновись" in query:
                        self.check_for_updates(manual=True)
                    else:
                        ai_answer = self.ask_ai(query)
                        self.speak_russian(ai_answer)
            except Exception as e:
                print(f"Ошибка аудиопотока Vosk: {e}")
                if stream:
                    try: stream.stop_stream(); stream.close()
                    except: pass
                time.sleep(1)

        try:
            p.terminate()
        except Exception:
            pass

    def on_closing(self):
        self.is_running = False
        self.stop_all_audio()
        if self.weather_timer and self.weather_timer.is_alive():
            self.weather_timer.cancel()
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)

if __name__ == "__main__":
    root = tk.Tk()
    app = AlisaApp(root)
    root.mainloop()
