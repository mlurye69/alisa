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
    "• Алиса, список команд\n"
    "• Алиса, который час\n"
    "• Алиса, погода / сегодня / завтра / неделя\n"
    "• Алиса, будильник на [время]\n"
    "• Алиса, выключи будильник\n"
    "• Алиса, напомни [текст] через [X] минут/часов\n"
    "• Алиса, напомни [текст] [дата] в [время]\n"
    "• Алиса, добавь ежедневное напоминание [текст] в [час] часов\n"
    "• Алиса, список напоминаний\n"
    "• Алиса, удали напоминание [номер]\n"
    "• Алиса, переведи [фраза] / как сказать [фраза]\n"
    "• Алиса, прочти\n"
    "• Алиса, [ваш вопрос для ИИ]\n"
    "• Алиса, включи свет / выключи свет\n"
    "• Алиса, выключи экран / включи экран\n"
    "• Алиса, выключить компьютер\n"
    "• Алиса, обновись"
)


class AlisaApp:
    def __init__(self, root):
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
        self.load_reminders()

        self.setup_ui()
        
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
            "CITY": "Boston"
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
        
        self.model_path = default_config.get("VOSK_MODEL_PATH", os.path.join(SCRIPT_DIR, "vosk-model-small-ru-0.22"))
        self.github_raw_url = default_config.get("GITHUB_RAW_URL", "https://raw.githubusercontent.com/mlurye69/alisa/main/alisa.py")
        
        raw_chime = default_config.get("CHIME_SOUND_PATH", "Sounds/alarm-clock-1.mp3")
        self.chime_sound_path = raw_chime if os.path.isabs(raw_chime) else os.path.join(SCRIPT_DIR, raw_chime)

    def load_reminders(self):
        if os.path.exists(REMINDERS_FILE):
            try:
                with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
                    self.reminders = json.load(f)
            except Exception as e:
                print(f"Ошибка загрузки reminders.json: {e}")
                self.reminders = []

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
            if target_time > now:
                valid_reminders.append(rem)
                delay = target_time - now
                threading.Timer(delay, self.trigger_reminder, args=[rem]).start()
            elif rem.get("daily", False):
                # If a daily reminder's time already passed today, schedule it for tomorrow at the same time
                dt = datetime.fromtimestamp(target_time)
                next_dt = dt.replace(year=datetime.now().year, month=datetime.now().month, day=datetime.now().day)
                if next_dt.timestamp() <= now:
                    next_dt += timedelta(days=1)
                rem["timestamp"] = next_dt.timestamp()
                valid_reminders.append(rem)
                delay = next_dt.timestamp() - now
                threading.Timer(delay, self.trigger_reminder, args=[rem]).start()
        self.reminders = valid_reminders
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
        self.save_reminders()
        
        # If reminder list screen is currently open, refresh it live
        if self.display_mode == "info" and "напоминаний" in self.date_label.cget("text").lower():
            self.refresh_reminder_info_screen()
        
        threading.Timer(delay_seconds, self.trigger_reminder, args=[reminder]).start()
        if daily:
            self.speak_russian("Ежедневное напоминание установлено.")
        else:
            self.speak_russian("Напоминание установлено.")

    def add_daily_reminder_at_time(self, text, hour, minute=0):
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target.timestamp() <= now.timestamp():
            target += timedelta(days=1)
        
        delay_seconds = target.timestamp() - now.timestamp()
        self.add_reminder(text, delay_seconds, daily=True)

    def trigger_reminder(self, reminder):
        if reminder not in self.reminders:
            return
            
        self.speak_russian(f"Напоминание: {reminder['text']}")
        
        if reminder.get("daily", False):
            # Reschedule for the next day
            now = datetime.now()
            dt = datetime.fromtimestamp(reminder["timestamp"])
            next_dt = dt + timedelta(days=1)
            reminder["timestamp"] = next_dt.timestamp()
            self.save_reminders()
            
            delay = next_dt.timestamp() - now.timestamp()
            threading.Timer(delay, self.trigger_reminder, args=[reminder]).start()
        else:
            self.reminders = [r for r in self.reminders if r != reminder]
            self.save_reminders()

    def remove_reminder_by_index(self, index_1_based):
        try:
            idx = index_1_based - 1
            if 0 <= idx < len(self.reminders):
                removed = self.reminders.pop(idx)
                self.save_reminders()
                
                # If reminder list screen is currently open, refresh it live
                if self.display_mode == "info" and "напоминаний" in self.date_label.cget("text").lower():
                    self.refresh_reminder_info_screen()
                    
                self.speak_russian(f"Напоминание {index_1_based} удалено.")
            else:
                self.speak_russian("Такого номера напоминания нет.")
        except Exception as e:
            print(f"Ошибка удаления напоминания: {e}")
            self.speak_russian("Не удалось удалить напоминание.")

    def refresh_reminder_info_screen(self):
        if self.reminders:
            lines = []
            for i, rem in enumerate(self.reminders, 1):
                dt_str = datetime.fromtimestamp(rem["timestamp"]).strftime('%d.%m %H:%M')
                daily_mark = " (ежедневно)" if rem.get("daily") else ""
                lines.append(f"{i}. {rem['text']} ({dt_str}){daily_mark}")
            texts = "\n".join(lines)
            self.weather_label.config(text=texts)
        else:
            self.weather_label.config(text="Активных напоминаний нет.")

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

        # Split weather into two labels
        self.weather_desc_label = tk.Label(self.frame, font=('Helvetica', 22, 'bold'), fg='lightseagreen', bg='black', justify='left', anchor='w')
        self.weather_desc_label.pack(anchor='w', pady=(0, 2))

        self.weather_temp_label = tk.Label(self.frame, font=('Helvetica', 28, 'bold'), fg='lightseagreen', bg='black', justify='left', anchor='w')
        self.weather_temp_label.pack(anchor='w', pady=(0, 10))

        self.date_label = tk.Label(self.frame, font=('Helvetica', 32, 'bold'), fg='mediumseagreen', bg='black', anchor='w')
        self.date_label.pack(anchor='w', pady=(0, 15))

        self.time_label = tk.Label(self.frame, font=('Helvetica', 110, 'bold'), fg='mediumseagreen', bg='black', anchor='w')
        self.time_label.pack(anchor='w', pady=(0, 0))

    def turn_light_on(self):
        self.display_mode = "light"
        if self.light_timer and self.light_timer.is_alive():
            self.light_timer.cancel()
        
        # Hide all weather and clock elements
        self.weather_desc_label.pack_forget()
        self.weather_temp_label.pack_forget()
        self.date_label.pack_forget()
        self.time_label.pack_forget()
        
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
        
        # Restore weather and clock labels
        self.weather_desc_label.pack(anchor='w', pady=(0, 2))
        self.weather_temp_label.pack(anchor='w', pady=(0, 10))
        self.date_label.pack(anchor='w', pady=(0, 15))
        self.time_label.pack(anchor='w', pady=(0, 0))
        
        self.frame.place(x=40, y=40, relwidth=0.9, relheight=0.9, anchor='nw')

    def show_info_screen(self, title_text, body_text, announcement):
        self.display_mode = "info"
        if self.weather_timer and self.weather_timer.is_alive():
            self.weather_timer.cancel()

        self.frame.place(x=40, y=40, relwidth=0.9, relheight=0.9, anchor='nw')
        self.date_label.config(text=title_text)
        self.time_label.config(text="", font=('Helvetica', 10))
        self.weather_label.config(text=body_text, font=('Helvetica', 20, 'bold'), fg='white')
        
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
        self.weather_label.config(font=('Helvetica', 28, 'bold'), fg='lightseagreen')
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
            
            # Split weather string into condition and temperature
            parts = self.current_weather_text.split(", ")
            city_prefix = f"Погода в {self.city}:"
            if len(parts) >= 2:
                desc_text = f"{city_prefix} {parts[0]}"
                temp_text = parts[1]
            else:
                desc_text = f"Погода в {self.city}:"
                temp_text = self.current_weather_text

            self.weather_desc_label.config(text=desc_text, font=('Helvetica', 22, 'bold'), fg='lightseagreen')
            self.weather_temp_label.config(text=temp_text, font=('Helvetica', 26, 'bold'), fg='lightseagreen')
            
            if self.alarm_time and current_hm == self.alarm_time and not self.alarm_triggered_today:
                self.alarm_triggered_today = True
                threading.Thread(target=self.trigger_alarm, daemon=True).start()
            elif current_hm != self.alarm_time:
                self.alarm_triggered_today = False
                
        self.root.after(1000, self.update_display)

    def shift_position(self):
        if self.display_mode == "clock":
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            self.frame.place(x=random.randint(40, max(50, screen_width - 650)),
                             y=random.randint(40, max(50, screen_height - 400)),
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
        self.alarm_time = None

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

        while True:
            stream = None
            try:
                rec = KaldiRecognizer(vosk_model, RATE)
                rec.SetWords(False)
                stream = p.open(format=pyaudio.paInt16, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)

                while True:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    if data and rec.AcceptWaveform(data):
                        recognized_text = json.loads(rec.Result()).get("text", "").lower()
                        if "алиса" in recognized_text or "alisa" in recognized_text:
                            self.speak_russian("Слушаю.")
                            break

                frames, silent_chunks = [], 0
                silence_threshold_chunks = int((RATE / CHUNK) * 3)

                while True:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    if not data: break
                    frames.append(data)
                    if audioop.rms(data, 2) < 300:
                        silent_chunks += 1
                    else:
                        silent_chunks = 0
                    if silent_chunks > silence_threshold_chunks:
                        break

                stream.stop_stream()
                stream.close()
                stream = None

                if frames:
                    query = r.recognize_google(sr.AudioData(b''.join(frames), RATE, 2), language="ru-RU").lower()
                    print(f"Распознано: {query}")
                    
                    if "напомни" in query:
                        match_delay = re.search(r"напомни\s+(.*?)\s+через\s+(\d+)\s*(минут|минуту|минуты|час|часа|часов)", query)
                        if match_delay:
                            text_reminder = match_delay.group(1).strip()
                            amount = int(match_delay.group(2))
                            unit = match_delay.group(3)
                            delay_seconds = amount * 60 if "мин" in unit else amount * 3600
                            self.add_reminder(text_reminder, delay_seconds, daily=False)
                        else:
                            # 2. Check for absolute time: "напомни [текст] в [час] часов [вечера/дня/утра]"
                            match_time = re.search(r"напомни\s+(.*?)\s+в\s+(\d+)(?:\s+час(?:а|ов)?)?\s*(вечера|дня|утра)?", query)
                            if match_time:
                                text_reminder = match_time.group(1).strip()
                                hour = int(match_time.group(2))
                                modifier = match_time.group(3)
                                
                                if modifier in ["вечера", "дня"] and hour < 12:
                                    hour += 12
                                    
                                now = datetime.now()
                                target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
                                if target.timestamp() <= now.timestamp():
                                    target += timedelta(days=1)
                                    
                                delay_seconds = target.timestamp() - now.timestamp()
                                self.add_reminder(text_reminder, delay_seconds, daily=False)
                            else:
                                self.speak_russian("Не поняла время напоминания.")
                    elif "ежедневное напоминание" in query or "ежедневное" in query or "каждый день" in query:
                        match_daily = re.search(r"(?:ежедневное\s+напоминание|напоминание|ежедневное)\s+(.+?)\s+(?:в|на)\s+(\d+)(?:\s+час(?:а|ов)?)?\s*(вечера|дня|утра)?", query)
                        if match_daily:
                            text_reminder = match_daily.group(1).strip()
                            text_reminder = re.sub(r"\s+(?:в|каждый\s+день)$", "", text_reminder)
                            hour = int(match_daily.group(2))
                            modifier = match_daily.group(3)
                            
                            if modifier in ["вечера", "дня"] and hour < 12:
                                hour += 12
                                
                            self.add_daily_reminder_at_time(text_reminder, hour)
                        else:
                            self.speak_russian("Не поняла текст или время ежедневного напоминания.")
                    elif any(w in query for w in ["включи свет", "свет включи", "белый экран"]):
                        self.speak_russian("Включаю свет.")
                        self.turn_light_on()
                    elif any(w in query for w in ["выключи свет", "свет выключи"]):
                        self.speak_russian("Выключаю свет.")
                        self.turn_light_off()
                    elif "список напоминаний" in query:
                        if self.reminders:
                            lines = []
                            for i, rem in enumerate(self.reminders, 1):
                                dt_str = datetime.fromtimestamp(rem["timestamp"]).strftime('%d.%m %H:%M')
                                daily_mark = " (ежедневно)" if rem.get("daily") else ""
                                lines.append(f"{i}. {rem['text']} ({dt_str}){daily_mark}")
                            texts = "\n".join(lines)
                            self.show_info_screen("Список напоминаний", texts, "Показываю активные напоминания.")
                        else:
                            self.show_info_screen("Список напоминаний", "Активных напоминаний нет.", "У вас нет активных напоминаний.")
                    elif any(w in query for w in ["удали напоминание", "сотри напоминание", "убери напоминание"]):
                        match_num = re.search(r"(?:удали|удалить)\s+напоминание\s+(\d+)", query)
                        if match_num:
                            rem_num = int(match_num.group(1))
                            self.remove_reminder_by_index(rem_num)
                        else:
                            self.speak_russian("Укажите номер напоминания для удаления.")
                    elif any(w in query for w in ["включи свет", "свет включи", "белый экран"]):
                        self.speak_russian("Включаю свет.")
                        self.turn_light_on()
                    elif any(w in query for w in ["выключи свет", "свет выключи"]):
                        self.speak_russian("Выключаю свет.")
                        self.turn_light_off()
                    elif "список напоминаний" in query:
                        if self.reminders:
                            texts = ", ".join([rem["text"] for rem in self.reminders])
                            self.show_info_screen("Список напоминаний", texts, "Показываю активные напоминания.")
                        else:
                            self.show_info_screen("Список напоминаний", "Активных напоминаний нет.", "У вас нет активных напоминаний.")
                    elif any(w in query for w in ["список команд", "команды", "справка"]):
                        self.show_info_screen("Справочник команд", COMMAND_LIST_TEXT, "Показываю список доступных команд.")
                    elif "погода" in query or "сегодня" in query:
                        self.show_info_screen(f"Погода ({self.city})", self.current_weather_text, f"Погода в городе {self.city}: {self.current_weather_text}")
                    elif "завтра" in query:
                        self.show_info_screen("Прогноз на завтра", self.tomorrow_hourly_forecast, "Показываю прогноз на завтра.")
                    elif "неделя" in query or "неделю" in query:
                        self.show_info_screen("Прогноз на неделю", self.weekly_forecast_text, "Показываю прогноз на неделю.")
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

    def on_closing(self):
        self.stop_all_audio()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = AlisaApp(root)
    root.mainloop()
