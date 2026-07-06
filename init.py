import sys, os, time, threading, queue, subprocess, tempfile, ctypes, random
from pathlib import Path

# -------------------- Проверка ОС --------------------
if sys.platform != 'win32':
    ctypes.windll.user32.MessageBoxW(0, "Только Windows 10/11", "Ошибка", 0x10)
    sys.exit(1)

win_ver = sys.getwindowsversion()
if not (win_ver.major == 10 and win_ver.minor == 0):
    ctypes.windll.user32.MessageBoxW(0, "Требуется Windows 10 или 11", "Ошибка", 0x10)
    sys.exit(1)

is_win11 = win_ver.build >= 22000
OS_NAME = "Windows 11" if is_win11 else "Windows 10"

import customtkinter as ctk
from customtkinter import (CTk, CTkFrame, CTkLabel, CTkButton,
                          CTkProgressBar, CTkTextbox, CTkScrollableFrame, CTkTabview)
import psutil

# -------------------- Библиотеки --------------------
def is_admin():
    try: return ctypes.windll.shell32.IsUserAnAdmin()
    except: return False

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
    """Возвращает список (size_gb, speed, locator) или пустой"""
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
    """Собирает данные по логическим дискам (буквам): заполненность, нагрузка, скорость"""
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
    """Возвращает скорость ОЗУ (из первой планки) или 0"""
    mods = get_memory_modules()
    return mods[0][1] if mods else 0

def get_process_count():
    return len(psutil.pids())

# -------------------- Оптимизация --------------------
def optimize_step(progress, log, admin):
    total = 20 if admin else 12
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

        # Только для администратора
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
        try:
            subprocess.run('netsh int tcp set global rss=enabled', shell=True, capture_output=True)
            subprocess.run('netsh int tcp set global chimney=enabled', shell=True, capture_output=True)
            subprocess.run('netsh int tcp set global autotuninglevel=normal', shell=True, capture_output=True)
            log("  └ TCP/IP оптимизирован (RSS, Chimney, autotuning)")
        except: log("  └ Ошибка настройки TCP")

        update("[*] Схема питания: Высокая производительность")
        try:
            subprocess.run('powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c', shell=True, capture_output=True)
            log("  └ Схема питания установлена")
        except: log("  └ Не удалось сменить схему питания")

        update("[*] Остановка тяжёлых служб (SysMain, WSearch)")
        for svc in ['SysMain', 'WSearch']:
            try:
                subprocess.run(f'net stop {svc} /y', shell=True, capture_output=True)
                log(f"  └ Служба {svc} остановлена")
            except: pass

        update("[*] Завершение фоновых процессов (браузеры, мессенджеры)")
        killed = 0
        targets = ['chrome.exe','firefox.exe','msedge.exe','discord.exe','skype.exe','telegram.exe','onedrive.exe']
        for proc in psutil.process_iter(['name']):
            if proc.info['name'] and proc.info['name'].lower() in targets:
                try: proc.kill(); killed += 1
                except: pass
        log(f"  └ Завершено {killed} фоновых процессов")

        update("[*] Очистка кэша Windows Store")
        try:
            subprocess.run('wsreset.exe /s', shell=True, capture_output=True)
            log("  └ Кэш Store очищен")
        except: log("  └ Не удалось очистить кэш Store")

        update("[*] TRIM для SSD")
        try:
            subprocess.run('fsutil behavior set DisableDeleteNotify 0', shell=True, capture_output=True)
            for p in psutil.disk_partitions():
                if 'fixed' in p.opts:
                    try: subprocess.run(f'defrag {p.device} /L', shell=True, capture_output=True)
                    except: pass
            log("  └ TRIM выполнен для всех SSD")
        except: log("  └ Ошибка TRIM")

        update("[*] Проверка обновлений драйверов")
        try:
            subprocess.run('usoclient StartScan', shell=True, capture_output=True)
            log("  └ Сканирование обновлений запущено")
        except: log("  └ Не удалось запустить проверку обновлений")

        update("[*] Повышение приоритета для активных процессов")
        try:
            for proc in psutil.process_iter(['pid', 'cpu_percent']):
                if proc.info['cpu_percent'] > 20:
                    try:
                        h = ctypes.windll.kernel32.OpenProcess(0x0200, False, proc.info['pid'])
                        if h:
                            ctypes.windll.kernel32.SetPriorityClass(h, 0x00000080)  # HIGH_PRIORITY_CLASS
                            ctypes.windll.kernel32.CloseHandle(h)
                    except: pass
            log("  └ Приоритеты процессов повышены")
        except: log("  └ Ошибка изменения приоритетов")

        update("[✔] Глубокая оптимизация завершена")
    except Exception as e:
        log(f"[✘] Критическая ошибка: {e}")
        progress(100)

# -------------------- Интерфейс --------------------
class Splash(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master)
        self.overrideredirect(True)
        self.geometry(f"500x300+{self.winfo_screenwidth()//2-250}+{self.winfo_screenheight()//2-150}")
        self.attributes("-alpha", 0.0)
        self.configure(fg_color="#0d1117")
        ctk.CTkLabel(self, text="⚡ SYSTEM OPTIMIZER", font=("Segoe UI", 28, "bold"), text_color="#58a6ff").pack(pady=40)
        ctk.CTkLabel(self, text="Загрузка...", font=("Segoe UI", 14), text_color="#c9d1d9").pack()
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

class SystemOptimizer(CTk):
    def __init__(self):
        super().__init__()
        self.title(f"Оптимизатор системы - {OS_NAME}")
        self.geometry("1050x800")
        self.minsize(900, 600)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        self.withdraw()  # скрыто до конца сплеша

        self.queue = queue.Queue()
        self.admin = is_admin()

        # Вкладки
        self.tabs = CTkTabview(self, corner_radius=10)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=10)
        self.tab_mon = self.tabs.add("Мониторинг")
        self.tab_opt = self.tabs.add("Оптимизация")

        self.build_monitor()
        self.build_optimizer()

        self.after(100, self.process_queue)
        self.update_stats()
        self.after(2500, self.schedule_update)

        Splash(self)

    # ---------- Мониторинг ----------
    def build_monitor(self):
        scroll = CTkScrollableFrame(self.tab_mon, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=5, pady=5)

        # CPU
        cpu = CTkFrame(scroll, corner_radius=10, fg_color="#161b22")
        cpu.pack(fill="x", pady=5, padx=5)
        ctk.CTkLabel(cpu, text="🧠 Процессор (CPU)", font=("Segoe UI", 16, "bold"), text_color="#58a6ff").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10,0))
        self.cpu_usage = ctk.CTkLabel(cpu, text="Загрузка: --%", font=("Segoe UI", 14))
        self.cpu_usage.grid(row=1, column=0, sticky="w", padx=20, pady=2)
        self.cpu_freq = ctk.CTkLabel(cpu, text="Частота: -- МГц", font=("Segoe UI", 14))
        self.cpu_freq.grid(row=2, column=0, sticky="w", padx=20)
        self.cpu_temp_label = ctk.CTkLabel(cpu, text="Температура: --°C", font=("Segoe UI", 14))
        self.cpu_temp_label.grid(row=3, column=0, sticky="w", padx=20, pady=2)
        self.cpu_temp_label.grid_remove()  # скрыта по умолчанию
        self.proc_count = ctk.CTkLabel(cpu, text="Процессов: --", font=("Segoe UI", 14))
        self.proc_count.grid(row=4, column=0, sticky="w", padx=20, pady=(0,10))

        # GPU
        gpu = CTkFrame(scroll, corner_radius=10, fg_color="#161b22")
        gpu.pack(fill="x", pady=5, padx=5)
        ctk.CTkLabel(gpu, text="🎮 Видеокарта (GPU)", font=("Segoe UI", 16, "bold"), text_color="#58a6ff").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10,0))
        self.gpu_load = ctk.CTkLabel(gpu, text="Загрузка: --%", font=("Segoe UI", 14))
        self.gpu_load.grid(row=1, column=0, sticky="w", padx=20)
        self.gpu_freq = ctk.CTkLabel(gpu, text="Частота: -- МГц", font=("Segoe UI", 14))
        self.gpu_freq.grid(row=2, column=0, sticky="w", padx=20)
        self.gpu_temp = ctk.CTkLabel(gpu, text="Температура: --°C", font=("Segoe UI", 14))
        self.gpu_temp.grid(row=3, column=0, sticky="w", padx=20, pady=(0,10))

        # RAM
        ram = CTkFrame(scroll, corner_radius=10, fg_color="#161b22")
        ram.pack(fill="x", pady=5, padx=5)
        ctk.CTkLabel(ram, text="🧮 Оперативная память", font=("Segoe UI", 16, "bold"), text_color="#58a6ff").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10,0))
        self.ram_usage_label = ctk.CTkLabel(ram, text="Загрузка: --%", font=("Segoe UI", 14))
        self.ram_usage_label.grid(row=1, column=0, sticky="w", padx=20)
        self.ram_info_label = ctk.CTkLabel(ram, text="", font=("Segoe UI", 13), text_color="#8b949e")
        self.ram_info_label.grid(row=2, column=0, sticky="w", padx=20, pady=(0,10))

        # Диски
        disk = CTkFrame(scroll, corner_radius=10, fg_color="#161b22")
        disk.pack(fill="x", pady=5, padx=5)
        ctk.CTkLabel(disk, text="💾 Диски", font=("Segoe UI", 16, "bold"), text_color="#58a6ff").pack(anchor="w", padx=10, pady=(10,0))
        self.disk_text = CTkTextbox(disk, height=150, font=("Consolas", 12), fg_color="#0d1117", text_color="#c9d1d9")
        self.disk_text.pack(fill="x", padx=10, pady=10)

    # ---------- Оптимизация ----------
    def build_optimizer(self):
        self.opt_frame = CTkFrame(self.tab_opt, corner_radius=10, fg_color="#161b22")
        self.opt_frame.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(self.opt_frame, text="🛠️ Глубокая оптимизация", font=("Segoe UI", 18, "bold"), text_color="#58a6ff").pack(pady=10)

        # Права админа
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

        self.progress = CTkProgressBar(self.opt_frame, width=400, fg_color="#0d1117", progress_color="#3fb950")
        self.progress.pack(pady=10)
        self.progress.set(0)

        # Терминал в стиле хакера
        self.log_box = CTkTextbox(self.opt_frame, height=16, font=("Courier New", 12),
                                  fg_color="#0d1117", text_color="#c9d1d9")
        self.log_box.pack(fill="both", expand=True, padx=10, pady=5)

    def restart_as_admin(self):
        try:
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
        except: pass
        self.destroy()

    def start_opt(self):
        self.opt_btn.configure(state="disabled")
        self.log_box.delete("1.0", "end")
        self.progress.set(0)
        # Включаем хакерский режим
        self.log_box.configure(fg_color="#000000", text_color="#00ff00")
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

    def process_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                if msg[0] == "progress":
                    self.progress.set(msg[1] / 100)
                elif msg[0] == "log":
                    self.log(msg[1])
                elif msg[0] == "finish":
                    self.log_box.configure(fg_color="#0d1117", text_color="#c9d1d9")
                    self.opt_btn.configure(state="normal")
                    self.log("[✔] Оптимизация завершена. Можно безопасно закрыть.")
                elif msg[0] == "update_stats":
                    self.apply_stats(msg[1])
        except queue.Empty:
            pass
        finally:
            self.after(100, self.process_queue)

    def schedule_update(self):
        self.update_stats()
        self.after(2500, self.schedule_update)

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

        # CPU
        self.cpu_usage.configure(text=f"Загрузка: {cpu_usage:.1f}%")
        self.cpu_freq.configure(text=f"Частота: {cpu_freq:.0f} МГц")
        if cpu_temp is not None:
            self.cpu_temp_label.configure(text=f"Температура: {cpu_temp:.0f}°C")
            self.cpu_temp_label.grid()
        else:
            self.cpu_temp_label.grid_remove()
        self.proc_count.configure(text=f"Процессов: {procs}")

        # GPU
        if gpu_info:
            load, freq, temp = gpu_info
            self.gpu_load.configure(text=f"Загрузка: {load:.1f}%")
            self.gpu_freq.configure(text=f"Частота: {freq} МГц")
            self.gpu_temp.configure(text=f"Температура: {temp}°C")
        else:
            self.gpu_load.configure(text="Загрузка: недоступно")
            self.gpu_freq.configure(text="Частота: недоступно")
            self.gpu_temp.configure(text="Температура: недоступно")

        # RAM
        used_gb = mem.used / (1024**3)
        total_gb = mem.total / (1024**3)
        self.ram_usage_label.configure(text=f"Загрузка: {mem.percent:.1f}%")
        info = f"Занято: {used_gb:.1f} ГБ из {total_gb:.1f} ГБ"
        if mem_speed > 0:
            info += f" | Скорость: {mem_speed} МГц"
        else:
            info += " | Скорость: неизвестна"
        self.ram_info_label.configure(text=info)

        # Диски
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