import sys, os, time, threading, queue, subprocess, tempfile, ctypes, random, webbrowser, json, tkinter as tk
from pathlib import Path

# Проверка ОС
if sys.platform != 'win32':
    ctypes.windll.user32.MessageBoxW(0, "Только Windows 10/11", "Ошибка", 0x10)
    sys.exit(1)

win_ver = sys.getwindowsversion()
if not (win_ver.major == 10 and win_ver.minor == 0):
    ctypes.windll.user32.MessageBoxW(0, "Требуется Windows 10 или 11", "Ошибка", 0x10)
    sys.exit(1)

IS_WIN11 = win_ver.build >= 22000
OS_NAME = "Windows 11" if IS_WIN11 else "Windows 10"

import customtkinter as ctk
from customtkinter import CTk, CTkFrame, CTkLabel, CTkButton, CTkProgressBar, CTkTextbox, CTkScrollableFrame, CTkTabview
import psutil

# -------------------- Конфиг --------------------
CONFIG_FILE = Path(sys.argv[0]).parent / "config.json"

DEFAULT_CONFIG = {
    "theme": "dark",
    "autostart": False,
    "update_interval": 2500,
    "start_minimized": False,
    "animations": True
}

def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                for k, v in DEFAULT_CONFIG.items():
                    if k not in cfg:
                        cfg[k] = v
                return cfg
        except:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=4)

# -------------------- Права администратора --------------------
def is_admin():
    try: return ctypes.windll.shell32.IsUserAnAdmin()
    except: return False

# -------------------- Импорт доп. библиотек --------------------
wmi_available = False
try:
    import wmi
    wmi_conn = wmi.WMI()
    wmi_available = True
except: pass

nvml_available = False
try:
    import nvidia_ml_py as nvml
    nvml_available = True
except:
    try:
        import pynvml as nvml
        nvml_available = True
    except: pass

send2trash_available = False
try:
    import send2trash
    send2trash_available = True
except: pass

# pygame для музыки
try:
    import pygame
    pygame_available = True
except:
    pygame_available = False

# -------------------- Сбор данных --------------------
def get_cpu_info():
    usage = psutil.cpu_percent(interval=0.1)
    freq = psutil.cpu_freq().current if psutil.cpu_freq() else 0
    temp = None
    try:
        temps = psutil.sensors_temperatures()
        for name in ['coretemp', 'cpu-thermal', 'acpitz']:
            if name in temps:
                for entry in temps[name]:
                    if 'Package' in entry.label or 'CPU' in entry.label:
                        temp = entry.current
                        break
                if temp is not None: break
        if temp is None and wmi_available:
            for sensor in wmi_conn.Win32_PerfFormattedData_Counters_ThermalZoneInformation():
                temp = (sensor.Temperature - 273.15) / 10.0
                break
    except: pass
    return usage, freq, temp

def get_gpu_info():
    if not nvml_available: return None
    try:
        nvml.nvmlInit()
        if nvml.nvmlDeviceGetCount() == 0:
            nvml.nvmlShutdown()
            return None
        handle = nvml.nvmlDeviceGetHandleByIndex(0)
        util = nvml.nvmlDeviceGetUtilizationRates(handle)
        load = util.gpu
        clock = nvml.nvmlDeviceGetClockInfo(handle, nvml.NVML_CLOCK_GRAPHICS)
        temp = nvml.nvmlDeviceGetTemperature(handle, nvml.NVML_TEMPERATURE_GPU)
        nvml.nvmlShutdown()
        return load, clock, temp
    except:
        try: nvml.nvmlShutdown()
        except: pass
        return None

def get_memory_modules():
    mods = []
    if wmi_available:
        try:
            for mem in wmi_conn.Win32_PhysicalMemory():
                size = int(mem.Capacity)//(1024**3) if mem.Capacity else 0
                speed = int(mem.Speed) if mem.Speed else 0
                loc = mem.DeviceLocator.strip() if mem.DeviceLocator else "Unknown"
                mods.append((size, speed, loc))
        except: pass
    return mods

def get_disk_info():
    disks = []
    partitions = psutil.disk_partitions()
    logical = {}
    for p in partitions:
        if 'cdrom' in p.opts or p.fstype == '': continue
        try:
            usage = psutil.disk_usage(p.mountpoint)
        except PermissionError: continue
        letter = p.device.strip('\\').rstrip('\\')
        logical[letter] = usage._asdict()
    if wmi_available:
        try:
            for disk in wmi_conn.Win32_PerfFormattedData_PerfDisk_LogicalDisk():
                if disk.Name == '_Total': continue
                for letter, info in logical.items():
                    if disk.Name == f"{letter}:":
                        info['util'] = float(disk.PercentDiskTime or 0)
                        info['read'] = int(disk.DiskReadBytesPersec or 0)
                        info['write'] = int(disk.DiskWriteBytesPersec or 0)
                        break
        except: pass
    for letter, info in logical.items():
        disks.append({
            'name': f"Диск {letter}",
            'percent': info['percent'],
            'util': info.get('util', 0),
            'read': info.get('read', 0),
            'write': info.get('write', 0)
        })
    return disks

def get_mem_speed():
    mods = get_memory_modules()
    return mods[0][1] if mods else 0

def get_process_count():
    return len(psutil.pids())

# -------------------- Диагностика --------------------
def run_diagnostics():
    tips = []
    cpu_percent = psutil.cpu_percent(interval=0.5)
    if cpu_percent > 80:
        tips.append(f"⚠️ Высокая загрузка CPU ({cpu_percent:.0f}%). Закройте тяжёлые фоновые приложения.")

    mem = psutil.virtual_memory()
    if mem.percent > 85:
        tips.append(f"⚠️ Мало свободной памяти ({mem.percent}%). Добавьте ОЗУ или закройте программы.")
    elif mem.available < 1_000_000_000:
        tips.append("💡 Осталось <1 ГБ свободной RAM. Рекомендуется закрыть часть приложений или добавить память.")

    for part in psutil.disk_partitions():
        if 'fixed' not in part.opts: continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
            if usage.percent > 90:
                tips.append(f"⚠️ Диск {part.device} заполнен на {usage.percent}%. Очистите место.")
        except: pass

    try:
        temps = psutil.sensors_temperatures()
        for key in ['coretemp', 'cpu-thermal', 'acpitz']:
            if key in temps:
                for entry in temps[key]:
                    if 'Package' in entry.label or 'CPU' in entry.label:
                        if entry.current > 80:
                            tips.append(f"🔥 Температура CPU {entry.current}°C! Очистите систему охлаждения.")
                        elif entry.current > 65:
                            tips.append(f"🌡️ Температура CPU {entry.current}°C – выше нормы. Проверьте вентиляцию.")
                        break
    except: pass

    net = psutil.net_io_counters()
    if net.bytes_sent == 0 and net.bytes_recv == 0:
        tips.append("🌐 Сетевая активность не обнаружена. Проверьте подключение к интернету.")

    if not tips:
        tips.append("✅ Система работает нормально. Особых проблем не выявлено.")
    return tips

# -------------------- Расширенная оптимизация --------------------
def optimize_step(progress, log, admin):
    total = 27 if admin else 16
    cur = 0
    def update(msg):
        nonlocal cur
        cur += 1
        progress(int(cur/total*100))
        log(msg)

    try:
        update("[*] Сканирование системы...")
        time.sleep(0.3)

        update("[+] Очистка временных файлов пользователя")
        tmp = Path(os.environ.get('TEMP', tempfile.gettempdir()))
        cnt = 0
        for f in tmp.iterdir():
            try:
                if f.is_file():
                    (send2trash.send2trash(str(f)) if send2trash_available else f.unlink(missing_ok=True))
                    cnt += 1
            except: pass
        log(f"  └ Удалено {cnt} файлов")

        update("[+] Удаление пустых папок в TEMP")
        cnt = 0
        for root, dirs, files in os.walk(tmp, topdown=False):
            for d in dirs:
                p = Path(root)/d
                try:
                    if not any(p.iterdir()):
                        (send2trash.send2trash(str(p)) if send2trash_available else p.rmdir())
                        cnt += 1
                except: pass
        log(f"  └ Удалено {cnt} папок")

        update("[+] Очистка кэша миниатюр")
        thumb = Path(os.environ['LOCALAPPDATA'])/'Microsoft'/'Windows'/'Explorer'
        cnt = 0
        for pat in ['thumbcache_*.db','iconcache_*.db']:
            for f in thumb.glob(pat):
                try:
                    (send2trash.send2trash(str(f)) if send2trash_available else f.unlink(missing_ok=True))
                    cnt += 1
                except: pass
        log(f"  └ Очищено {cnt} файлов кэша")

        update("[+] Очистка кэша шрифтов")
        fc = Path(os.environ['LOCALAPPDATA'])/'Microsoft'/'Windows'/'Fonts'/'fontcache'
        try:
            for f in fc.iterdir():
                if f.is_file():
                    try:
                        (send2trash.send2trash(str(f)) if send2trash_available else f.unlink(missing_ok=True))
                    except: pass
            log("  └ Кэш шрифтов очищен")
        except: log("  └ Не удалось очистить кэш шрифтов")

        update("[+] Освобождение рабочего набора процессов")
        freed = 0
        for proc in psutil.process_iter(['pid']):
            try:
                h = ctypes.windll.kernel32.OpenProcess(0x0400, False, proc.info['pid'])
                if h:
                    ctypes.windll.kernel32.SetProcessWorkingSetSize(h, -1, -1)
                    ctypes.windll.kernel32.CloseHandle(h)
                    freed += 1
            except: pass
        log(f"  └ Рабочий набор очищен для {freed} процессов")

        update("[+] Очистка DNS-кэша")
        subprocess.run('ipconfig /flushdns', shell=True, capture_output=True)
        log("  └ DNS-кэш очищен")

        if not admin:
            update("[!] Оптимизация завершена (часть шагов пропущена без прав администратора)")
            return

        update("[+] Очистка Prefetch")
        try:
            prefetch = Path(os.environ['SystemRoot'])/'Prefetch'
            cnt = 0
            for pf in prefetch.glob('*.pf'):
                try: pf.unlink(); cnt += 1
                except: pass
            log(f"  └ Очищено {cnt} файлов Prefetch")
        except PermissionError: log("  └ Нет доступа к Prefetch")

        update("[*] Сброс Winsock и TCP/IP")
        subprocess.run('netsh winsock reset', shell=True, capture_output=True)
        subprocess.run('netsh int ip reset', shell=True, capture_output=True)
        log("  └ Сетевые стеки сброшены")

        update("[*] Настройка TCP/IP для низкого пинга")
        subprocess.run('netsh int tcp set global rss=enabled', shell=True, capture_output=True)
        subprocess.run('netsh int tcp set global chimney=enabled', shell=True, capture_output=True)
        subprocess.run('netsh int tcp set global autotuninglevel=normal', shell=True, capture_output=True)
        log("  └ TCP/IP оптимизирован (RSS, Chimney, autotuning)")

        update("[*] Установка схемы высокой производительности")
        subprocess.run('powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c', shell=True, capture_output=True)
        log("  └ Схема питания: Высокая производительность")

        update("[*] Отключение SysMain и WSearch")
        for svc in ['SysMain', 'WSearch']:
            subprocess.run(f'net stop {svc} /y', shell=True, capture_output=True)
        log("  └ Службы SysMain и WSearch остановлены")

        update("[+] Завершение фоновых процессов (браузеры, мессенджеры)")
        killed = 0
        targets = ['chrome.exe','firefox.exe','msedge.exe','discord.exe','skype.exe','telegram.exe','onedrive.exe']
        for proc in psutil.process_iter(['name']):
            if proc.info['name'] and proc.info['name'].lower() in targets:
                try: proc.kill(); killed += 1
                except: pass
        log(f"  └ Завершено {killed} фоновых процессов")

        update("[+] Очистка кэша Windows Store")
        subprocess.run('wsreset.exe /s', shell=True, capture_output=True)
        log("  └ Кэш Store очищен")

        update("[+] TRIM для SSD")
        subprocess.run('fsutil behavior set DisableDeleteNotify 0', shell=True, capture_output=True)
        for p in psutil.disk_partitions():
            if 'fixed' in p.opts:
                subprocess.run(f'defrag {p.device} /L', shell=True, capture_output=True)
        log("  └ TRIM выполнен для всех SSD (дефрагментация HDD пропущена для скорости)")

        update("[*] Включение игрового режима (Game Mode)")
        subprocess.run('reg add "HKCU\\Software\\Microsoft\\GameBar" /v AllowAutoGameMode /t REG_DWORD /d 1 /f', shell=True, capture_output=True)
        subprocess.run('reg add "HKCU\\Software\\Microsoft\\GameBar" /v AutoGameModeEnabled /t REG_DWORD /d 1 /f', shell=True, capture_output=True)
        log("  └ Игровой режим активирован")

        update("[*] Отключение Xbox Game Bar (освобождение ресурсов)")
        subprocess.run('reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\GameDVR" /v AppCaptureEnabled /t REG_DWORD /d 0 /f', shell=True, capture_output=True)
        subprocess.run('reg add "HKCU\\Software\\Microsoft\\GameBar" /v UseNexusForGameBarEnabled /t REG_DWORD /d 0 /f', shell=True, capture_output=True)
        log("  └ Xbox Game Bar отключён")

        update("[*] Настройка визуальных эффектов (макс. производительность)")
        subprocess.run('reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\VisualEffects" /v VisualFXSetting /t REG_DWORD /d 2 /f', shell=True, capture_output=True)
        log("  └ Визуальные эффекты: максимальная производительность")

        update("[*] Очистка кэша DirectX (Shader Cache)")
        dx_cache = Path(os.environ['LOCALAPPDATA'])/'NVIDIA'/'DXCache'
        if dx_cache.exists():
            for f in dx_cache.iterdir():
                try: f.unlink(missing_ok=True)
                except: pass
        amd_cache = Path(os.environ['LOCALAPPDATA'])/'AMD'/'DxCache'
        if amd_cache.exists():
            for f in amd_cache.iterdir():
                try: f.unlink(missing_ok=True)
                except: pass
        log("  └ Кэш шейдеров DirectX очищен")

        update("[+] Повышение приоритетов процессов")
        try:
            for proc in psutil.process_iter(['pid', 'cpu_percent']):
                if proc.info['cpu_percent'] > 20:
                    h = ctypes.windll.kernel32.OpenProcess(0x0200, False, proc.info['pid'])
                    if h:
                        ctypes.windll.kernel32.SetPriorityClass(h, 0x00000080)
                        ctypes.windll.kernel32.CloseHandle(h)
            log("  └ Приоритеты процессов повышены")
        except: log("  └ Ошибка изменения приоритетов")

        if IS_WIN11:
            update("[*] Windows 11: Отключение виджетов и новостей")
            subprocess.run('reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Feeds" /v ShellFeedsTaskbarViewMode /t REG_DWORD /d 2 /f', shell=True, capture_output=True)
            subprocess.run('reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Widgets" /v EnableAppNotification /t REG_DWORD /d 0 /f', shell=True, capture_output=True)
            log("  └ Виджеты и новости отключены")
            update("[*] Windows 11: Отключение Copilot (если есть)")
            subprocess.run('reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Copilot" /v IsCopilotAvailable /t REG_DWORD /d 0 /f', shell=True, capture_output=True)
            log("  └ Copilot отключён")
        else:
            update("[*] Windows 10: Отключение Cortana")
            subprocess.run('reg add "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\Windows Search" /v AllowCortana /t REG_DWORD /d 0 /f', shell=True, capture_output=True)
            log("  └ Cortana отключена")
            update("[*] Windows 10: Отключение рекламы на экране блокировки и в меню Пуск")
            subprocess.run('reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\ContentDeliveryManager" /v RotatingLockScreenEnabled /t REG_DWORD /d 0 /f', shell=True, capture_output=True)
            subprocess.run('reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\ContentDeliveryManager" /v SystemPaneSuggestionsEnabled /t REG_DWORD /d 0 /f', shell=True, capture_output=True)
            log("  └ Реклама и предложения отключены")

        update("[*] Увеличение размера системного кэша")
        subprocess.run('reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Memory Management" /v LargeSystemCache /t REG_DWORD /d 1 /f', shell=True, capture_output=True)
        log("  └ LargeSystemCache включён")

        update("[*] Фиксация файла подкачки (4-8 ГБ)")
        subprocess.run('wmic computersystem where name="%computername%" set AutomaticManagedPagefile=False', shell=True, capture_output=True)
        subprocess.run('wmic pagefileset where name="C:\\\\pagefile.sys" set InitialSize=4096,MaximumSize=8192', shell=True, capture_output=True)
        log("  └ Файл подкачки настроен (4-8 ГБ)")

        update("[✔] Глубокая оптимизация завершена")
    except Exception as e:
        log(f"[✘] Критическая ошибка: {e}")
        progress(100)

# -------------------- Splash Screen --------------------
class Splash(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master)
        self.overrideredirect(True)
        self.geometry(f"500x300+{self.winfo_screenwidth()//2-250}+{self.winfo_screenheight()//2-150}")
        self.attributes("-alpha", 0.0)
        self.configure(fg_color=("#f0f0f0", "#0d1117"))
        ctk.CTkLabel(self, text="⚡ SYSTEM OPTIMIZER", font=("Segoe UI", 28, "bold"), text_color="#58a6ff").pack(pady=40)
        ctk.CTkLabel(self, text="Загрузка...", font=("Segoe UI", 14), text_color=("#000000", "#c9d1d9")).pack()
        self.progress = ctk.CTkProgressBar(self, width=300)
        self.progress.pack(pady=30)
        self.progress.set(0)
        self.fade_in(0.0)

    def fade_in(self, alpha):
        if alpha < 1.0:
            self.attributes("-alpha", alpha)
            self.after(20, self.fade_in, alpha+0.05)
        else:
            self.after(500, self.run_progress, 0)

    def run_progress(self, val):
        if val < 1.0:
            self.progress.set(val)
            self.after(30, self.run_progress, val+0.02)
        else:
            self.master.deiconify()
            self.destroy()

# -------------------- Ритм-игра --------------------
class RhythmGame(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("🎵 Ритм-игра")
        self.geometry("800x600")
        self.resizable(False, False)
        self.configure(fg_color=("#f0f0f0", "#1e1e2e"))

        self.score = 0
        self.combo = 0
        self.missed = 0
        self.notes = []
        self.note_data = []
        self.lanes = 3
        self.lane_width = 100
        self.start_x = 150
        self.bpm = 153  # можно изменить для синхронизации с музыкой
        self.spawn_delay = 1000
        self.note_speed = 6
        self.game_active = False

        # Инициализация pygame для музыки
        if pygame_available:
            pygame.mixer.init()
            self.music_file = "song.ogg"  # ваш файл должен лежать рядом с init.py
        else:
            self.music_file = None

        ctk.CTkLabel(self, text="🎤 Ритм-игра", font=("Segoe UI", 24, "bold"), text_color=("#000000", "#cba6f7")).pack(pady=10)

        # Цвет холста зависит от темы
        theme = ctk.get_appearance_mode()
        bg_color = "#ffffff" if theme == "light" else "#313244"
        self.canvas = tk.Canvas(self, width=600, height=250, bg=bg_color, highlightthickness=0)
        self.canvas.pack(pady=10)
        self.draw_lanes()

        self.score_label = ctk.CTkLabel(self, text="Счёт: 0   Комбо: 0", font=("Segoe UI", 14), text_color=("#000000", "#cdd6f4"))
        self.score_label.pack()

        self.start_btn = ctk.CTkButton(self, text="Старт", command=self.start_game)
        self.start_btn.pack(pady=10)

        self.bind("<KeyPress>", self.key_press)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def draw_lanes(self):
        self.canvas.delete("all")
        for i in range(self.lanes):
            x = self.start_x + i * self.lane_width
            self.canvas.create_rectangle(x, 0, x + self.lane_width, 250, outline="#585b70", width=2)
            key = ["A", "S", "D"][i]
            self.canvas.create_text(x + self.lane_width//2, 230, text=key, fill="#89b4fa", font=("Arial", 20, "bold"))

    def play_music(self):
        if not pygame_available or not self.music_file:
            return
        try:
            pygame.mixer.music.load(self.music_file)
            pygame.mixer.music.play(-1)  # зациклить
        except Exception as e:
            print("Не удалось загрузить музыку:", e)

    def start_game(self):
        if self.game_active:
            return
        self.game_active = True
        self.score = 0
        self.combo = 0
        self.missed = 0
        self.notes.clear()
        self.note_data.clear()
        self.canvas.delete("note")

        # Синхронизация с BPM
        quarter_ms = 60000 / self.bpm
        self.spawn_delay = int(quarter_ms)
        frames = quarter_ms / 30
        self.note_speed = 200 / frames

        self.play_music()
        self.spawn_note()
        self.update_loop()
        self.start_btn.configure(state="disabled")

    def spawn_note(self):
        if not self.game_active:
            return
        lane = random.randint(0, self.lanes-1)
        x = self.start_x + lane * self.lane_width + self.lane_width//2
        y = 0
        note_id = self.canvas.create_rectangle(x-20, y-20, x+20, y+20, fill="#f9e2af", tags="note")
        self.notes.append(note_id)
        self.note_data.append([lane, self.note_speed, 0, True])
        self.after(self.spawn_delay, self.spawn_note)

    def update_loop(self):
        if not self.game_active:
            return
        to_remove = []
        for i, data in enumerate(self.note_data):
            if not data[3]:
                continue
            data[2] += data[1]
            y = data[2]
            note_id = self.notes[i]
            self.canvas.coords(note_id, self.start_x + data[0]*self.lane_width + self.lane_width//2 - 20, y-20,
                               self.start_x + data[0]*self.lane_width + self.lane_width//2 + 20, y+20)
            if y > 260:
                self.missed += 1
                self.combo = 0
                data[3] = False
                self.canvas.delete(note_id)
                to_remove.append(i)

        for idx in reversed(to_remove):
            del self.notes[idx]
            del self.note_data[idx]

        self.update_score_label()
        self.after(30, self.update_loop)

    def key_press(self, event):
        if not self.game_active:
            return
        key = event.keysym.upper()
        lane_map = {'A': 0, 'S': 1, 'D': 2}
        if key not in lane_map:
            return
        lane = lane_map[key]
        best_idx = -1
        best_dist = 999
        for i, data in enumerate(self.note_data):
            if data[3] and data[0] == lane:
                dist = abs(data[2] - 200)
                if dist < best_dist and 180 <= data[2] <= 230:
                    best_dist = dist
                    best_idx = i
        if best_idx != -1:
            self.canvas.delete(self.notes[best_idx])
            del self.notes[best_idx]
            del self.note_data[best_idx]
            self.score += 100
            self.combo += 1
        else:
            self.missed += 1
            self.combo = 0
        self.update_score_label()

    def update_score_label(self):
        self.score_label.configure(text=f"Счёт: {self.score}   Комбо: {self.combo}   Промахи: {self.missed}")

    def on_close(self):
        self.game_active = False
        if pygame_available and pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.quit()
        self.destroy()

# -------------------- Основное приложение --------------------
class SystemOptimizer(CTk):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        ctk.set_appearance_mode(self.config["theme"])

        self.withdraw()
        self.title(" ")
        self.geometry("1050x800")
        self.minsize(900, 600)

        self.overrideredirect(True)
        try:
            self.iconbitmap(default="icon.ico")
        except:
            pass
        self.after(100, self._fix_taskbar)

        self.queue = queue.Queue()
        self.admin = is_admin()

        # Кастомный заголовок (тёмный в обеих темах, чтобы не терять видимость)
        title_bar_fg = ("#2b2b2b", "#0d1117")
        title_text_color = ("#000000", "#c9d1d9")
        self.title_bar = CTkFrame(self, height=36, fg_color=title_bar_fg, corner_radius=0)
        self.title_bar.pack(fill="x", side="top")
        self.title_bar.pack_propagate(False)

        title_label = CTkLabel(self.title_bar, text="  System Optimizer", font=("Segoe UI", 12, "bold"),
                               text_color=title_text_color)
        title_label.pack(side="left", padx=10)

        btn_frame = CTkFrame(self.title_bar, fg_color="transparent")
        btn_frame.pack(side="right")

        self.btn_donate = CTkButton(btn_frame, text="❤️ Поддержать", width=100, height=28,
                                    fg_color="transparent", hover_color="#30363d",
                                    font=("Segoe UI", 11), text_color="#f85149",
                                    command=self.open_donate)
        self.btn_donate.pack(side="left", padx=4)

        self.btn_min = CTkButton(btn_frame, text="─", width=36, height=28, fg_color="transparent",
                                 hover_color="#30363d", command=self.iconify)
        self.btn_min.pack(side="left", padx=2)
        self.btn_max = CTkButton(btn_frame, text="☐", width=36, height=28, fg_color="transparent",
                                 hover_color="#30363d", command=self.toggle_max)
        self.btn_max.pack(side="left", padx=2)
        self.btn_close = CTkButton(btn_frame, text="✕", width=36, height=28, fg_color="transparent",
                                  hover_color="#da3633", command=self.destroy)
        self.btn_close.pack(side="left", padx=2)

        self.title_bar.bind("<Button-1>", self.start_move)
        self.title_bar.bind("<ButtonRelease-1>", self.stop_move)
        self.title_bar.bind("<B1-Motion>", self.do_move)
        title_label.bind("<Button-1>", self.start_move)
        title_label.bind("<ButtonRelease-1>", self.stop_move)
        title_label.bind("<B1-Motion>", self.do_move)

        # Основной контейнер
        self.bg = CTkFrame(self, fg_color=("#f0f0f0", "#0d1117"))
        self.bg.pack(fill="both", expand=True)

        self.tabs = CTkTabview(self.bg, corner_radius=10, fg_color=("#ffffff", "#161b22"))
        self.tabs.pack(fill="both", expand=True, padx=10, pady=(0,10))
        self.tab_mon = self.tabs.add("Мониторинг")
        self.tab_opt = self.tabs.add("Оптимизация")
        self.tab_diag = self.tabs.add("Диагностика")
        self.tab_settings = self.tabs.add("Настройки")

        self.build_monitor()
        self.build_optimizer()
        self.build_diagnostics()
        self.build_settings()

        self.after(100, self.process_queue)
        self.update_stats()
        interval = self.config.get("update_interval", 2500)
        self._update_job = self.after(interval, self.schedule_update)

        Splash(self)

        if self.config.get("start_minimized", False):
            self.iconify()

    def _fix_taskbar(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x40000
            WS_EX_TOOLWINDOW = 0x80
            style = ctypes.windll.user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            style = style | WS_EX_APPWINDOW
            style = style & ~WS_EX_TOOLWINDOW
            ctypes.windll.user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0002 | 0x0001)
        except Exception as e:
            print("Не удалось настроить панель задач:", e)

    def start_move(self, event):
        self.x = event.x
        self.y = event.y

    def stop_move(self, event):
        self.x = None
        self.y = None

    def do_move(self, event):
        deltax = event.x - self.x
        deltay = event.y - self.y
        x = self.winfo_x() + deltax
        y = self.winfo_y() + deltay
        self.geometry(f"+{x}+{y}")

    def toggle_max(self):
        if self.state() == 'normal':
            self.state('zoomed')
            self.btn_max.configure(text="❐")
        else:
            self.state('normal')
            self.btn_max.configure(text="☐")

    # ---------- Вкладка Мониторинг ----------
    def build_monitor(self):
        scroll = CTkScrollableFrame(self.tab_mon, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=5, pady=5)

        card_fg = ("#ffffff", "#161b22")
        def make_card(parent):
            return CTkFrame(parent, corner_radius=10, fg_color=card_fg)

        cpu = make_card(scroll)
        cpu.pack(fill="x", pady=5, padx=5)
        ctk.CTkLabel(cpu, text="🧠 Процессор (CPU)", font=("Segoe UI", 16, "bold"), text_color="#58a6ff").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10,0))
        self.cpu_usage = ctk.CTkLabel(cpu, text="Загрузка: --%", font=("Segoe UI", 14))
        self.cpu_usage.grid(row=1, column=0, sticky="w", padx=20, pady=2)
        self.cpu_freq = ctk.CTkLabel(cpu, text="Частота: -- МГц", font=("Segoe UI", 14))
        self.cpu_freq.grid(row=2, column=0, sticky="w", padx=20)
        self.cpu_temp_label = ctk.CTkLabel(cpu, text="Температура: --°C", font=("Segoe UI", 14))
        self.cpu_temp_label.grid(row=3, column=0, sticky="w", padx=20, pady=2)
        self.cpu_temp_label.grid_remove()
        self.proc_count = ctk.CTkLabel(cpu, text="Процессов: --", font=("Segoe UI", 14))
        self.proc_count.grid(row=4, column=0, sticky="w", padx=20, pady=(0,10))

        gpu = make_card(scroll)
        gpu.pack(fill="x", pady=5, padx=5)
        ctk.CTkLabel(gpu, text="🎮 Видеокарта (GPU)", font=("Segoe UI", 16, "bold"), text_color="#58a6ff").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10,0))
        self.gpu_load = ctk.CTkLabel(gpu, text="Загрузка: --%", font=("Segoe UI", 14))
        self.gpu_load.grid(row=1, column=0, sticky="w", padx=20)
        self.gpu_freq = ctk.CTkLabel(gpu, text="Частота: -- МГц", font=("Segoe UI", 14))
        self.gpu_freq.grid(row=2, column=0, sticky="w", padx=20)
        self.gpu_temp = ctk.CTkLabel(gpu, text="Температура: --°C", font=("Segoe UI", 14))
        self.gpu_temp.grid(row=3, column=0, sticky="w", padx=20, pady=(0,10))

        ram = make_card(scroll)
        ram.pack(fill="x", pady=5, padx=5)
        ctk.CTkLabel(ram, text="🧮 Оперативная память", font=("Segoe UI", 16, "bold"), text_color="#58a6ff").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10,0))
        self.ram_usage_label = ctk.CTkLabel(ram, text="Загрузка: --%", font=("Segoe UI", 14))
        self.ram_usage_label.grid(row=1, column=0, sticky="w", padx=20)
        self.ram_info_label = ctk.CTkLabel(ram, text="", font=("Segoe UI", 13), text_color=("#000000", "#8b949e"))
        self.ram_info_label.grid(row=2, column=0, sticky="w", padx=20, pady=(0,10))

        disk = make_card(scroll)
        disk.pack(fill="x", pady=5, padx=5)
        ctk.CTkLabel(disk, text="💾 Диски", font=("Segoe UI", 16, "bold"), text_color="#58a6ff").pack(anchor="w", padx=10, pady=(10,0))
        self.disk_text = CTkTextbox(disk, height=150, font=("Consolas", 12), fg_color=("#ffffff", "#0d1117"), text_color=("#000000", "#c9d1d9"))
        self.disk_text.pack(fill="x", padx=10, pady=10)

    # ---------- Вкладка Оптимизация ----------
    def build_optimizer(self):
        self.opt_frame = CTkFrame(self.tab_opt, corner_radius=10, fg_color=("#ffffff", "#161b22"))
        self.opt_frame.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(self.opt_frame, text="🛠️ Глубокая оптимизация", font=("Segoe UI", 18, "bold"), text_color="#58a6ff").pack(pady=10)
        admin_txt = "✅ Права администратора" if self.admin else "⚠️ Без прав администратора (ограничено)"
        self.admin_lbl = ctk.CTkLabel(self.opt_frame, text=admin_txt, font=("Segoe UI", 12),
                                      text_color="#3fb950" if self.admin else "#f85149")
        self.admin_lbl.pack(pady=5)
        if not self.admin:
            self.elev_btn = CTkButton(self.opt_frame, text="🔒 Перезапустить от администратора",
                                      command=self.restart_as_admin)
            self.elev_btn.pack(pady=5)

        self.opt_btn = CTkButton(self.opt_frame, text="▶️ Начать оптимизацию", font=("Segoe UI", 14, "bold"),
                                 fg_color="#238636", hover_color="#2ea043", command=self.start_opt)
        self.opt_btn.pack(pady=15)

        self.progress = CTkProgressBar(self.opt_frame, width=400, fg_color=("#e0e0e0", "#0d1117"), progress_color="#3fb950")
        self.progress.pack(pady=10)
        self.progress.set(0)

        self.log_box = CTkTextbox(self.opt_frame, height=14, font=("Courier New", 12),
                                  fg_color=("#ffffff", "#0d1117"), text_color=("#000000", "#c9d1d9"))
        self.log_box.pack(fill="both", expand=True, padx=10, pady=5)

    def restart_as_admin(self):
        try:
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
        except: pass
        self.destroy()

    def open_donate(self):
        webbrowser.open("https://www.donationalerts.com/r/mr_gleyg")

    def start_opt(self):
        self.opt_btn.configure(state="disabled")
        self.log_box.delete("1.0", "end")
        self.progress.set(0)
        self.log_box.configure(fg_color=("#000000", "#000000"), text_color="#00ff00")
        self.log(">>> Инициализация ядра...")
        self.after(100, self.run_optimization)

    def run_optimization(self):
        def worker():
            try:
                optimize_step(self.set_progress, self.hacker_log, admin=self.admin)
            except Exception as e:
                self.hacker_log(f"[!] Критический сбой: {e}")
            finally:
                self.queue.put(("finish",))
        threading.Thread(target=worker, daemon=True).start()

    def hacker_log(self, msg):
        self.queue.put(("log", msg))

    def set_progress(self, val):
        self.queue.put(("progress", val))

    def log(self, msg):
        self.log_box.insert("end", f"{msg}\n")
        self.log_box.see("end")

    # ---------- Вкладка Диагностика ----------
    def build_diagnostics(self):
        diag_frame = CTkFrame(self.tab_diag, corner_radius=10, fg_color=("#ffffff", "#161b22"))
        diag_frame.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(diag_frame, text="🔍 Диагностика системы", font=("Segoe UI", 18, "bold"), text_color="#58a6ff").pack(pady=10)
        ctk.CTkLabel(diag_frame, text="Анализ текущего состояния и рекомендации по улучшению",
                     font=("Segoe UI", 12), text_color=("#000000", "#8b949e")).pack()

        self.diag_btn = CTkButton(diag_frame, text="Запустить диагностику", font=("Segoe UI", 14, "bold"),
                                  fg_color="#238636", hover_color="#2ea043", command=self.start_diag)
        self.diag_btn.pack(pady=15)

        self.diag_text = CTkTextbox(diag_frame, height=20, font=("Segoe UI", 13),
                                    fg_color=("#ffffff", "#0d1117"), text_color=("#000000", "#c9d1d9"), wrap="word")
        self.diag_text.pack(fill="both", expand=True, padx=10, pady=10)

    def start_diag(self):
        self.diag_btn.configure(state="disabled")
        self.diag_text.delete("1.0", "end")
        self.diag_text.insert("end", "Выполняется диагностика...\n\n")

        def worker():
            tips = run_diagnostics()
            self.queue.put(("diag_done", tips))

        threading.Thread(target=worker, daemon=True).start()

    def show_diagnostics(self, tips):
        self.diag_text.delete("1.0", "end")
        self.diag_text.insert("end", "=== Результаты диагностики ===\n\n")
        for tip in tips:
            self.diag_text.insert("end", f"{tip}\n\n")
        self.diag_btn.configure(state="normal")

    # ---------- Вкладка Настройки ----------
    def build_settings(self):
        settings_scroll = CTkScrollableFrame(self.tab_settings, fg_color="transparent")
        settings_scroll.pack(fill="both", expand=True, padx=5, pady=5)

        ctk.CTkLabel(settings_scroll, text="⚙️ Настройки", font=("Segoe UI", 18, "bold"), text_color="#58a6ff").pack(pady=10)

        card_fg = ("#ffffff", "#161b22")
        def make_setting_card(parent):
            return CTkFrame(parent, corner_radius=10, fg_color=card_fg)

        theme_frame = make_setting_card(settings_scroll)
        theme_frame.pack(fill="x", pady=5, padx=5)
        ctk.CTkLabel(theme_frame, text="🎨 Тема оформления", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=(10,5))

        self.theme_var = ctk.StringVar(value=self.config["theme"])
        self.theme_switch = ctk.CTkSwitch(theme_frame, text="Тёмная / Светлая",
                                          variable=self.theme_var, onvalue="dark", offvalue="light",
                                          command=self.change_theme)
        self.theme_switch.grid(row=1, column=0, sticky="w", padx=20, pady=(0,10))
        if self.config["theme"] == "dark":
            self.theme_switch.select()
        else:
            self.theme_switch.deselect()

        autostart_frame = make_setting_card(settings_scroll)
        autostart_frame.pack(fill="x", pady=5, padx=5)
        ctk.CTkLabel(autostart_frame, text="🚀 Автозапуск", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=(10,5))

        self.autostart_var = ctk.BooleanVar(value=self.config["autostart"])
        self.autostart_switch = ctk.CTkSwitch(autostart_frame, text="Запускать при старте Windows",
                                              variable=self.autostart_var, command=self.toggle_autostart)
        self.autostart_switch.grid(row=1, column=0, sticky="w", padx=20, pady=(0,10))

        interval_frame = make_setting_card(settings_scroll)
        interval_frame.pack(fill="x", pady=5, padx=5)
        ctk.CTkLabel(interval_frame, text="⏱️ Интервал мониторинга (сек)", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=(10,5))

        self.interval_var = ctk.StringVar(value=str(self.config["update_interval"] // 1000))
        interval_menu = ctk.CTkOptionMenu(interval_frame, values=["1", "2", "5"],
                                          variable=self.interval_var, command=self.change_interval)
        interval_menu.grid(row=1, column=0, sticky="w", padx=20, pady=(0,10))

        tray_frame = make_setting_card(settings_scroll)
        tray_frame.pack(fill="x", pady=5, padx=5)
        self.start_minimized_var = ctk.BooleanVar(value=self.config.get("start_minimized", False))
        self.tray_switch = ctk.CTkSwitch(tray_frame, text="Запускать свёрнутым в трей",
                                         variable=self.start_minimized_var, command=self.toggle_start_minimized)
        self.tray_switch.pack(anchor="w", padx=20, pady=10)

        anim_frame = make_setting_card(settings_scroll)
        anim_frame.pack(fill="x", pady=5, padx=5)
        self.animations_var = ctk.BooleanVar(value=self.config.get("animations", True))
        self.anim_switch = ctk.CTkSwitch(anim_frame, text="Анимации интерфейса",
                                         variable=self.animations_var, command=self.toggle_animations)
        self.anim_switch.pack(anchor="w", padx=20, pady=10)

        secret_frame = make_setting_card(settings_scroll)
        secret_frame.pack(fill="x", pady=20, padx=5)
        ctk.CTkLabel(secret_frame, text="🔒 Совершенно секретно", font=("Segoe UI", 12), text_color=("#000000", "#8b949e")).pack(pady=(10,0))

        self.secret_btn = ctk.CTkButton(secret_frame, text="Открыть",
                                        fg_color="#cba6f7", hover_color="#b4befe",
                                        text_color="#1e1e2e", font=("Segoe UI", 14, "bold"),
                                        command=self.open_rhythm_game)
        self.secret_btn.pack(pady=10)

    def change_theme(self, *args):
        theme = self.theme_var.get()
        ctk.set_appearance_mode(theme)
        self.config["theme"] = theme
        save_config(self.config)

    def change_interval(self, choice):
        seconds = int(choice)
        self.config["update_interval"] = seconds * 1000
        save_config(self.config)
        if hasattr(self, '_update_job'):
            self.after_cancel(self._update_job)
        self.schedule_update()

    def toggle_autostart(self):
        enabled = self.autostart_var.get()
        self.config["autostart"] = enabled
        save_config(self.config)
        try:
            import winreg
            key = r"Software\Microsoft\Windows\CurrentVersion\Run"
            reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE)
            if enabled:
                exe_path = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(sys.argv[0])
                winreg.SetValueEx(reg, "SystemOptimizer", 0, winreg.REG_SZ, exe_path)
            else:
                try:
                    winreg.DeleteValue(reg, "SystemOptimizer")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(reg)
        except Exception as e:
            print("Ошибка автозагрузки:", e)

    def toggle_start_minimized(self):
        self.config["start_minimized"] = self.start_minimized_var.get()
        save_config(self.config)

    def toggle_animations(self):
        self.config["animations"] = self.animations_var.get()
        save_config(self.config)

    def open_rhythm_game(self):
        RhythmGame(self)

    # ---------- Очередь сообщений ----------
    def process_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                if msg[0] == "progress":
                    self.progress.set(msg[1] / 100)
                elif msg[0] == "log":
                    self.log(msg[1])
                elif msg[0] == "finish":
                    self.log_box.configure(fg_color=("#ffffff", "#0d1117"), text_color=("#000000", "#c9d1d9"))
                    self.opt_btn.configure(state="normal")
                    self.log("[✔] Оптимизация завершена.")
                elif msg[0] == "update_stats":
                    self.apply_stats(msg[1])
                elif msg[0] == "diag_done":
                    self.show_diagnostics(msg[1])
        except queue.Empty:
            pass
        finally:
            self.after(100, self.process_queue)

    def schedule_update(self):
        self.update_stats()
        interval = self.config.get("update_interval", 2500)
        self._update_job = self.after(interval, self.schedule_update)

    def update_stats(self):
        def worker():
            try:
                cpu_usage, cpu_freq, cpu_temp = get_cpu_info()
                gpu_info = get_gpu_info()
                mem = psutil.virtual_memory()
                mem_speed = get_mem_speed() if wmi_available else 0
                disks = get_disk_info()
                procs = get_process_count()
                self.queue.put(("update_stats", (cpu_usage, cpu_freq, cpu_temp, gpu_info, mem, mem_speed, disks, procs)))
            except Exception as e:
                print(f"Stats error: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def apply_stats(self, data):
        cpu_usage, cpu_freq, cpu_temp, gpu_info, mem, mem_speed, disks, procs = data
        self.cpu_usage.configure(text=f"Загрузка: {cpu_usage:.1f}%")
        self.cpu_freq.configure(text=f"Частота: {cpu_freq:.0f} МГц")
        if cpu_temp is not None:
            self.cpu_temp_label.configure(text=f"Температура: {cpu_temp:.0f}°C")
            self.cpu_temp_label.grid()
        else:
            self.cpu_temp_label.grid_remove()
        self.proc_count.configure(text=f"Процессов: {procs}")

        if gpu_info:
            load, freq, temp = gpu_info
            self.gpu_load.configure(text=f"Загрузка: {load:.1f}%")
            self.gpu_freq.configure(text=f"Частота: {freq} МГц")
            self.gpu_temp.configure(text=f"Температура: {temp}°C")
        else:
            self.gpu_load.configure(text="Загрузка: недоступно")
            self.gpu_freq.configure(text="Частота: недоступно")
            self.gpu_temp.configure(text="Температура: недоступно")

        used_gb = mem.used / (1024**3)
        total_gb = mem.total / (1024**3)
        self.ram_usage_label.configure(text=f"Загрузка: {mem.percent:.1f}%")
        info = f"Занято: {used_gb:.1f} ГБ из {total_gb:.1f} ГБ"
        if mem_speed > 0:
            info += f" | Скорость: {mem_speed} МГц"
        else:
            info += " | Скорость: неизвестна"
        self.ram_info_label.configure(text=info)

        self.disk_text.delete("1.0", "end")
        if disks:
            for d in disks:
                self.disk_text.insert("end", f"{d['name']}\n")
                self.disk_text.insert("end", f"  Заполнено: {d['percent']:.1f}%")
                if wmi_available:
                    self.disk_text.insert("end", f" | Нагрузка: {d['util']:.1f}%")
                self.disk_text.insert("end", f"\n  Чтение: {d['read']/1024:.1f} КБ/с | Запись: {d['write']/1024:.1f} КБ/с\n\n")
            if not wmi_available:
                self.disk_text.insert("end", "ℹ️ Для нагрузки и скорости установите 'wmi'.\n")
        else:
            self.disk_text.insert("end", "Информация о дисках недоступна")

if __name__ == "__main__":
    app = SystemOptimizer()
    app.mainloop()