import sys
import ctypes
import os
import re
import time
import json
import queue
import threading
import subprocess
import stat
import traceback
import tempfile
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from ctypes import wintypes, Structure, c_int, c_uint, c_void_p, c_wchar_p, byref, windll, create_unicode_buffer, cast, POINTER
from PyQt6.QtCore import Qt, QEvent, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QAction
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QCheckBox,
    QComboBox,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QFileDialog,
    QMessageBox,
    QProgressDialog,
    QDialog,
    QMenu)
APP_VERSION = "2.0.0"
# Name of the source script. Used to recognize widget shortcuts that a dev
# (source-code) run left bound to python, so the packaged app can reclaim
# them and the widget launches the installed program instead of the source.
APP_SCRIPT_NAME = "desktop_organizer.py"
# GitHub repo that hosts the release downloads for update checks.
UPDATE_CHECK_URL = "https://api.github.com/repos/zlatik007/desktop-organizer/releases/latest"
RELEASES_PAGE_URL = "https://github.com/zlatik007/desktop-organizer/releases/latest"
def _version_tuple(version: str) -> tuple:
    """Parse a version string into a comparable tuple of integers."""
    parts = tuple(int(p) for p in re.findall(r"\d+", version or ""))
    if len(parts) < 3:
        parts = parts + (0,) * (3 - len(parts))
    return parts
def _normalize_version(tag: str) -> str:
    """Turn a release tag like 'v1.2.0' into '1.2.0'."""
    return (tag or "").strip().lstrip("vV").strip()
def is_newer_version(latest: str) -> bool:
    """True when the given version string is newer than the running app."""
    return _version_tuple(latest) > _version_tuple(APP_VERSION)
def _pick_config_dir() -> Path:
    """Store settings next to the program (portable). Falls back to the
    per-user folder only if the program folder is not writable."""
    candidate = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return candidate
    except Exception:
        return Path.home() / ".desktop_organizer"
CONFIG_DIR = _pick_config_dir()
LANG_DIR = CONFIG_DIR / "languages"
# All user data (settings, widget lists, renamed items) lives in its own
# subfolder so the program folder stays clean.
DATA_DIR = CONFIG_DIR / "data"
JSON_STORAGE_FILE = DATA_DIR / "widgets.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
# Custom display names for items inside widgets: {widget_name: {path: name}}.
# Kept in a separate file so widgets.json stays backward compatible.
NAMES_STORAGE_FILE = DATA_DIR / "names.json"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
LANG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
def _migrate_legacy_data_files():
    """Move settings/widgets/names created before the "data" folder
    existed into it, so no user data is lost on the first run after the
    update."""
    for name in ("settings.json", "widgets.json", "names.json"):
        old = CONFIG_DIR / name
        new = DATA_DIR / name
        if old.exists() and not new.exists():
            try:
                old.replace(new)
            except Exception:
                pass
_migrate_legacy_data_files()
def load_settings() -> dict:
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"language": "en", "confirm_exit": True}
def save_settings(settings: dict):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=4)
    except Exception:
        pass
def load_widgets_data() -> dict:
    """Read widgets storage; return {} on any problem."""
    try:
        if JSON_STORAGE_FILE.exists():
            with open(JSON_STORAGE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}
def save_widgets_data(data: dict):
    with open(JSON_STORAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
def load_item_names() -> dict:
    """Read per-widget display-name overrides; return {} on any problem."""
    try:
        if NAMES_STORAGE_FILE.exists():
            with open(NAMES_STORAGE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}
def save_item_names(data: dict):
    try:
        with open(NAMES_STORAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception:
        pass
def remove_item_paths_from_widget(widget_name: str, paths: set):
    """Remove the given normalized paths from a widget's storage
    (widgets.json) and from the custom-names file (names.json). Returns the
    number of entries actually removed from storage."""
    data = load_widgets_data()
    stored = data.get(widget_name, [])
    kept = [p for p in stored if not (isinstance(p, str) and str(Path(p)) in paths)]
    removed = len(stored) - len(kept)
    data[widget_name] = kept
    save_widgets_data(data)
    names_data = load_item_names()
    overrides = names_data.get(widget_name)
    if isinstance(overrides, dict):
        changed = False
        for p in paths:
            if p in overrides:
                del overrides[p]
                changed = True
        if changed:
            save_item_names(names_data)
    return removed
_ORIGINAL_EXCEPTHOOK = sys.excepthook
def _log_unhandled_exception(exc_type, exc_value, exc_tb):
    """Log unhandled exceptions to crash.log next to the program."""
    try:
        with open(CONFIG_DIR / "crash.log", "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            f.write("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    except Exception:
        pass
    try:
        _ORIGINAL_EXCEPTHOOK(exc_type, exc_value, exc_tb)
    except Exception:
        pass
sys.excepthook = _log_unhandled_exception
CURRENT_LANG = "en"
TRANSLATIONS = {}
def load_translations(lang: str):
    global CURRENT_LANG, TRANSLATIONS
    CURRENT_LANG = lang
    filename = "init.json" if lang == "en" else "russian.lng" if lang == "ru" else f"{lang}.lng"
    file_path = LANG_DIR / filename
    bundled_path = Path(sys.argv[0]).resolve().parent / "languages" / filename
    # PyInstaller 6 puts bundled data into _internal; fall back to it so the
    # portable exe works even when languages are not copied next to the exe.
    if not bundled_path.exists() and getattr(sys, "frozen", False):
        internal_path = Path(sys.argv[0]).resolve().parent / "_internal" / "languages" / filename
        if internal_path.exists():
            bundled_path = internal_path
    TRANSLATIONS = {}
    try:
        if bundled_path.exists():
            with open(bundled_path, "r", encoding="utf-8") as f:
                TRANSLATIONS = json.load(f)
            if not file_path.exists() or bundled_path.stat().st_mtime > file_path.stat().st_mtime:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(TRANSLATIONS, f, ensure_ascii=False, indent=4)
            return
    except Exception:
        pass
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                TRANSLATIONS = json.load(f)
        except Exception:
            pass
def tr(key: str, **kwargs) -> str:
    text = TRANSLATIONS.get(key, key)
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except Exception:
        return text
load_translations(load_settings().get("language", "en"))
_speech_queue = queue.Queue(maxsize=8)
_speech_worker_started = False
_speech_lock = threading.Lock()
def _speech_worker_loop():
    try:
        import pyttsx3
    except Exception:
        return
    engine = None
    engine_broken = False
    while True:
        try:
            text = _speech_queue.get()
        except Exception:
            return
        if engine_broken:
            continue
        try:
            if engine is None:
                engine = pyttsx3.init()
            engine.say(text)
            engine.runAndWait()
        except Exception:
            engine_broken = True
            try:
                if engine is not None:
                    engine.stop()
            except Exception:
                pass
_nvda_dll = None
_nvda_speak_func = None
_nvda_cancel_func = None
_nvda_checked = False


def _app_base_dir() -> Path:
    """Directory of the running app: the exe for frozen builds, the script
    folder when running from source."""
    return Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent


def _nvda_controller_client_candidates() -> list:
    """Possible locations of nvdaControllerClient.dll.

    Modern NVDA no longer installs this DLL; NV Access ships it separately
    (controllerClient.zip) for applications to bundle with themselves. The
    copies bundled next to this app (nvdaControllerClient_x64.dll and
    _x86.dll in the project root, named by architecture) are tried first,
    then a copy with the original name next to the app, and finally NVDA's
    own install folder for older NVDA versions that still shipped the DLL
    there.
    """
    base = _app_base_dir()
    is_64bit = ctypes.sizeof(c_void_p) * 8 == 64
    if is_64bit:
        candidates = [base / "nvdaControllerClient_x64.dll", base / "nvdaControllerClient_x86.dll"]
    else:
        candidates = [base / "nvdaControllerClient_x86.dll", base / "nvdaControllerClient_x64.dll"]
    # A manually placed copy with the original name also works.
    candidates.append(base / "nvdaControllerClient.dll")
    for env_var in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        pf = os.environ.get(env_var)
        if pf:
            candidates.append(Path(pf) / "NVDA" / "nvdaControllerClient.dll")
    # Portable installs commonly live under AppData\Local\Programs.
    candidates.append(Path.home() / "AppData" / "Local" / "Programs" / "NVDA" / "nvdaControllerClient.dll")
    try:
        import winreg
    except Exception:
        winreg = None
    if winreg is not None:
        for hive, key in (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\NVDA"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\NVDA"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\NVDA"),
        ):
            try:
                with winreg.OpenKey(hive, key) as k:
                    loc, _ = winreg.QueryValueEx(k, "InstallLocation")
                if loc:
                    candidates.append(Path(loc) / "nvdaControllerClient.dll")
            except Exception:
                continue
    seen = set()
    unique = []
    for c in candidates:
        try:
            s = str(c.resolve())
        except Exception:
            continue
        if s in seen:
            continue
        seen.add(s)
        unique.append(c)
    return unique


def _load_nvda_client():
    """Load NVDA's controller client DLL (once) so announcements are spoken
    by NVDA itself with the user's usual voice."""
    global _nvda_dll, _nvda_speak_func, _nvda_cancel_func, _nvda_checked
    if _nvda_checked:
        return _nvda_dll
    _nvda_checked = True
    if sys.platform != "win32":
        return None
    for dll_path in _nvda_controller_client_candidates():
        try:
            dll = ctypes.WinDLL(str(dll_path))
        except Exception:
            continue  # wrong architecture or missing dependencies
        try:
            speak_func = getattr(dll, "nvdaController_speakText", None)
            if speak_func is None:
                speak_func = getattr(dll, "nvdaControllerClient_speakText", None)
            test_func = getattr(dll, "nvdaController_testIfRunning", None)
            if speak_func is None or test_func is None:
                continue
            speak_func.argtypes = [c_wchar_p]
            speak_func.restype = c_int
            test_func.argtypes = []
            test_func.restype = c_int
            cancel_func = getattr(dll, "nvdaController_cancelSpeech", None)
            if cancel_func is not None:
                cancel_func.argtypes = []
                cancel_func.restype = c_int
            _nvda_dll = dll
            _nvda_speak_func = speak_func
            _nvda_cancel_func = cancel_func
            return dll
        except Exception:
            continue
    return None


def nvda_speak(text: str, cancel: bool = False) -> bool:
    """Ask a running NVDA to speak text with its own synthesizer. With
    cancel, pending NVDA speech is interrupted first so the new message is
    heard immediately. Returns False when NVDA is not available or running."""
    if _load_nvda_client() is None:
        return False
    try:
        if _nvda_dll.nvdaController_testIfRunning() != 0:
            return False
        if cancel and _nvda_cancel_func is not None:
            _nvda_cancel_func()
        _nvda_speak_func(text)
        return True
    except Exception:
        return False


def sapi_speak(text: str) -> bool:
    """Speak text through the Windows SAPI voice via PowerShell.

    Used only as a fallback when NVDA is not available or not running; the
    app prefers speaking through NVDA's own synthesizer via the bundled
    nvdaControllerClient.dll."""
    if sys.platform != "win32":
        return False
    try:
        ps_cmd = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Speak('{text.replace(chr(39), chr(39) * 2)}')"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            creationflags=CREATE_NO_WINDOW,
        )
        return True
    except Exception:
        return False


def announce_speech(text: str, interrupt: bool = False):
    # Order: NVDA itself (via the bundled controller client DLL), then the
    # built-in Windows system voice (SAPI), then pyttsx3.
    if nvda_speak(text, cancel=interrupt):
        return
    if sapi_speak(text):
        return
    try:
        global _speech_worker_started
        with _speech_lock:
            if interrupt:
                # Drop queued announcements so a rapid series (e.g. drag
                # previews) does not build up a speech backlog.
                try:
                    while True:
                        _speech_queue.get_nowait()
                except queue.Empty:
                    pass
            if not _speech_worker_started:
                _speech_worker_started = True
                threading.Thread(target=_speech_worker_loop, name="tts-speech", daemon=True).start()
        _speech_queue.put_nowait(text)
    except Exception:
        pass
def _message_box(parent, title: str, text: str, icon=QMessageBox.Icon.NoIcon) -> QMessageBox:
    """Build a QMessageBox whose text is announced by screen readers on open.

    NVDA and other screen readers only announce the focused default button of
    a QMessageBox and stay silent about the static text label. Setting the
    box's accessible name to the message text makes the reader speak it as
    soon as the dialog opens, instead of requiring the user to read the
    whole window manually (Insert+B).
    """
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(icon)
    box.setAccessibleName(text)
    return box
def report_error(parent, message_key: str, error: Exception, title_key: str = "error_title"):
    """Speak and show a critical error dialog with the same translated text."""
    err_str = str(error)
    text = tr(message_key, error=err_str)
    announce_speech(text)
    box = _message_box(parent, tr(title_key), text, QMessageBox.Icon.Critical)
    box.addButton(QMessageBox.StandardButton.Ok)
    box.exec()
def report_warning(parent, message_key: str, title_key: str = "attention_title", **kwargs):
    """Speak and show a warning dialog with the same translated text."""
    text = tr(message_key, **kwargs)
    announce_speech(text)
    box = _message_box(parent, tr(title_key), text, QMessageBox.Icon.Warning)
    box.addButton(QMessageBox.StandardButton.Ok)
    box.exec()
def prompt_update_dialog(parent, latest_version: str, asset_url, respect_skip: bool = True):
    """Show the "new version available" dialog and offer to download the update.

    With respect_skip, a version the user already dismissed is not offered again
    on later starts; the manual check in Settings always shows its result.
    """
    if respect_skip and load_settings().get("skipped_update_version") == latest_version:
        return
    text = tr("update_available_msg", version=latest_version, current=APP_VERSION)
    announce_speech(text)
    box = _message_box(parent, tr("update_available_title"), text, QMessageBox.Icon.Information)
    download_btn = box.addButton(tr("update_download_btn"), QMessageBox.ButtonRole.AcceptRole)
    box.addButton(tr("update_later_btn"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(download_btn)
    box.exec()
    if box.clickedButton() == download_btn:
        if asset_url:
            download_update_and_install(parent, latest_version, asset_url)
        else:
            # No portable zip attached to the release: fall back to the page.
            announce_speech(tr("update_no_asset_msg"))
            try:
                webbrowser.open(RELEASES_PAGE_URL)
            except Exception:
                pass
    else:
        # Remember the dismissed version so the auto check stops nagging.
        settings = load_settings()
        settings["skipped_update_version"] = latest_version
        save_settings(settings)
def download_update_and_install(parent, version: str, asset_url: str):
    """Download the release zip with a progress dialog, then offer to install."""
    dest = Path(tempfile.gettempdir()) / "DesktopOrganizer_update.zip"
    dialog = QProgressDialog(tr("update_downloading_msg", version=version), tr("cancel"), 0, 0, parent)
    dialog.setWindowTitle(tr("update_downloading_title"))
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.setValue(0)
    announce_speech(tr("update_downloading_msg", version=version))
    worker = UpdateDownloadWorker(asset_url, dest, parent)
    def on_progress(done, total):
        try:
            if total > 0:
                dialog.setRange(0, total)
                dialog.setValue(done)
        except RuntimeError:
            pass
    def on_done(zip_path, error):
        try:
            dialog.close()
            if error == "cancelled":
                announce_speech(tr("update_download_cancelled"))
                return
            if error:
                report_error(parent, "update_download_failed", Exception(error), title_key="error_title")
                return
            if zip_path:
                _confirm_install_update(parent, zip_path)
        except RuntimeError:
            pass  # the parent window was closed while downloading
    dialog.canceled.connect(worker.requestInterruption)
    worker.progress.connect(on_progress)
    worker.completed.connect(on_done)
    _running_download_workers.add(worker)
    worker.completed.connect(lambda w=worker: _forget_download_worker(w))
    worker.start()
def _confirm_install_update(parent, zip_path: Path):
    """Ask for confirmation before closing the app and replacing its files."""
    text = tr("update_download_done_msg")
    announce_speech(text)
    box = _message_box(parent, tr("update_download_done_title"), text, QMessageBox.Icon.Information)
    install_btn = box.addButton(tr("update_install_btn"), QMessageBox.ButtonRole.AcceptRole)
    box.addButton(tr("update_later_btn"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(install_btn)
    box.exec()
    if box.clickedButton() == install_btn:
        _start_update_install(parent, zip_path)
def _start_update_install(parent, zip_path: Path):
    """Apply the downloaded update: launch a hidden PowerShell script that
    replaces the program files and restarts the app."""
    try:
        script = _write_updater_script(zip_path)
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-File", str(script)],
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as e:
        try:
            report_error(parent, "update_install_failed", e, title_key="error_title")
        except RuntimeError:
            pass
        return
    announce_speech(tr("update_installing_announce"))
    app = QApplication.instance()
    if app is not None:
        app.quit()
def _write_updater_script(zip_path: Path) -> Path:
    """Create a hidden PowerShell updater that waits for this app to exit,
    replaces the program files from the downloaded zip and restarts the app.

    The archive may contain a single top-level folder (e.g. a zip of the
    "dist/DesktopOrganizer" folder); its contents are copied into the app
    folder. The zip and the updater script are removed afterwards.
    """
    app_dir = str(Path(sys.executable).resolve().parent)
    app_dir_ps = _ps_escape(app_dir)
    zip_ps = _ps_escape(str(zip_path))
    exe = str(Path(sys.executable).resolve())
    exe_ps = _ps_escape(exe)
    pid = os.getpid()
    if getattr(sys, "frozen", False):
        restart = f"Start-Process -FilePath '{exe_ps}' -WorkingDirectory '{app_dir_ps}'"
    else:
        script_arg = _ps_escape(str(Path(sys.argv[0]).resolve()))
        restart = f"Start-Process -FilePath '{exe_ps}' -ArgumentList '{script_arg}' -WorkingDirectory '{app_dir_ps}'"
    content = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$appPid = {pid}\n"
        f"$zip = '{zip_ps}'\n"
        f"$appDir = '{app_dir_ps}'\n"
        "$waited = 0\n"
        "while ((Get-Process -Id $appPid -ErrorAction SilentlyContinue) -and $waited -lt 120) {\n"
        "    Start-Sleep -Milliseconds 500\n"
        "    $waited++\n"
        "}\n"
        "if (Get-Process -Id $appPid -ErrorAction SilentlyContinue) {\n"
        "    # The app did not exit in time; abort without touching anything.\n"
        "    Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue\n"
        "    exit 1\n"
        "}\n"
        "Start-Sleep -Milliseconds 1000\n"
        "$stage = Join-Path $env:TEMP ('do_update_' + [guid]::NewGuid().ToString('N'))\n"
        "New-Item -ItemType Directory -Path $stage | Out-Null\n"
        "try {\n"
        "    Expand-Archive -LiteralPath $zip -DestinationPath $stage -Force\n"
        "    $entries = @(Get-ChildItem -LiteralPath $stage -Force)\n"
        "    if ($entries.Count -eq 1 -and $entries[0].PSIsContainer) { $src = $entries[0].FullName }\n"
        "    else { $src = $stage }\n"
        "    $attempts = 0\n"
        "    $ok = $false\n"
        "    while (-not $ok -and $attempts -lt 20) {\n"
        "        try {\n"
        "            Copy-Item -Path (Join-Path $src '*') -Destination $appDir -Recurse -Force -ErrorAction Stop\n"
        "            $ok = $true\n"
        "        } catch {\n"
        "            Start-Sleep -Milliseconds 500\n"
        "            $attempts++\n"
        "        }\n"
        "    }\n"
        "    if (-not $ok) { throw 'Could not replace the program files' }\n"
        "    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue\n"
        "} finally {\n"
        "    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue\n"
        "}\n"
        f"{restart}\n"
        "Start-Sleep -Milliseconds 500\n"
        "Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue\n"
    )
    script_path = Path(sys.executable).resolve().parent / "update_install.ps1"
    try:
        script_path.write_text(content, encoding="utf-8-sig")
    except Exception:
        # Fall back to the temp folder so the update can still run.
        script_path = Path(tempfile.gettempdir()) / "update_install.ps1"
        script_path.write_text(content, encoding="utf-8-sig")
    return script_path
class SHFILEOPSTRUCTW(Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", c_uint),
        ("pFrom", c_wchar_p),
        ("pTo", c_wchar_p),
        ("fFlags", wintypes.WORD),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", c_void_p),
        ("lpszProgressTitle", c_wchar_p),
    ]
FO_DELETE = 3
FOF_ALLOWUNDO = 0x0040
FOF_NOCONFIRMATION = 0x0010
FOF_SILENT = 0x0004
# Hide the console window for PowerShell subprocess calls.
CREATE_NO_WINDOW = 0x08000000
def _ps_escape(s: str) -> str:
    return s.replace("'", "''")
def _shell_delete(path: Path, to_recycle_bin: bool) -> bool:
    """Delete a file via the Windows shell so Explorer refreshes desktop
    icons. With to_recycle_bin, moves to the Recycle Bin instead. Returns
    True if the file is gone."""
    path_p = Path(path).resolve()
    if not path_p.exists():
        return False
    # Clear the read-only attribute or the delete silently fails.
    try:
        os.chmod(path_p, stat.S_IWRITE)
    except Exception:
        pass
    path_str = str(path_p) + '\0'
    buffer = create_unicode_buffer(path_str)
    fileop = SHFILEOPSTRUCTW()
    fileop.hwnd = None
    fileop.wFunc = FO_DELETE
    fileop.pFrom = cast(buffer, c_wchar_p)
    fileop.pTo = None
    fileop.fFlags = (FOF_ALLOWUNDO if to_recycle_bin else 0) | FOF_NOCONFIRMATION | FOF_SILENT
    sh_op = windll.shell32.SHFileOperationW
    sh_op.argtypes = [POINTER(SHFILEOPSTRUCTW)]
    sh_op.restype = c_int
    res = sh_op(byref(fileop))
    return res == 0 and not path_p.exists()
def move_to_recycle_bin(path: Path) -> bool:
    try:
        if _shell_delete(path, to_recycle_bin=True):
            return True
        path_p = Path(path).resolve()
        if not path_p.exists():
            return True
        ps_cmd = (
            f"Add-Type -AssemblyName Microsoft.VisualBasic; "
            f"[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('{_ps_escape(str(path_p))}', "
            f"'OnlyErrorDialogs', 'SendToRecycleBin')"
        )
        res_ps = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], creationflags=CREATE_NO_WINDOW)
        ok = res_ps.returncode == 0
        if ok:
            _notify_shell_file_deleted(path_p)
        return ok
    except Exception:
        return False
def _notify_shell_file_deleted(path: Path):
    """Notify Explorer that a file was deleted so desktop icons refresh."""
    try:
        SHCNE_DELETE = 0x00000004
        SHCNE_UPDATEDIR = 0x00001000
        SHCNF_PATHW = 0x0005
        SHCNF_FLUSH = 0x1000
        # Export is "SHChangeNotify" (no A/W variant; args are LPCVOID).
        sh_change = windll.shell32.SHChangeNotify
        sh_change.argtypes = [wintypes.LONG, wintypes.UINT, c_wchar_p, c_wchar_p]
        sh_change.restype = None
        path_str = str(path)
        sh_change(SHCNE_DELETE, SHCNF_PATHW | SHCNF_FLUSH, path_str, None)
        sh_change(SHCNE_UPDATEDIR, SHCNF_PATHW | SHCNF_FLUSH, str(path.parent), None)
    except Exception:
        pass
def delete_permanently(path: Path) -> bool:
    try:
        if _shell_delete(path, to_recycle_bin=False):
            return True
        path_p = Path(path).resolve()
        if not path_p.exists():
            return True
        path_p.unlink()
        _notify_shell_file_deleted(path_p)
        return not path_p.exists()
    except Exception:
        return False
def get_desktop_path() -> Path:
    return Path(os.path.expanduser("~/Desktop"))
def get_desktop_shortcut_path(widget_name: str) -> Path:
    return get_desktop_path() / f"{widget_name}.lnk"
def get_public_desktop_path() -> Path:
    return Path(os.environ.get("PUBLIC", "C:\\Users\\Public")) / "Desktop"
def is_on_desktop(path: Path) -> bool:
    try:
        desktop = get_desktop_path().resolve()
        public_desktop = get_public_desktop_path().resolve()
        target = Path(path).resolve()
        return (desktop in target.parents or target.parent == desktop) or \
               (public_desktop in target.parents or target.parent == public_desktop)
    except Exception:
        return False
def resolve_shortcut_targets_batch(shortcut_paths):
    """Resolve the targets of many .lnk files with a single PowerShell call."""
    result = {}
    if not shortcut_paths:
        return result
    resolved_map = {}
    for p in shortcut_paths:
        try:
            resolved_map[str(Path(p).resolve())] = Path(p)
        except Exception:
            result[str(p)] = p
    if not resolved_map:
        return result
    chunk_size = 40
    keys = list(resolved_map)
    for start in range(0, len(keys), chunk_size):
        chunk = keys[start:start + chunk_size]
        path_args = ", ".join("'" + _ps_escape(k) + "'" for k in chunk)
        ps_cmd = (
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "$WshShell = New-Object -ComObject WScript.Shell; "
            "$paths = @(" + path_args + "); "
            "foreach ($p in $paths) { "
            "$sh = $WshShell.CreateShortcut($p); "
            "Write-Output ($p + \"`t\" + $sh.TargetPath) }"
        )
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW, timeout=30,
            )
            for line in res.stdout.lstrip("\ufeff").splitlines():
                p_str, sep, target = line.partition("\t")
                p_str = p_str.strip()
                target = target.strip()
                if sep and p_str in resolved_map:
                    result[p_str] = Path(target) if target else resolved_map[p_str]
        except Exception:
            pass
    for k, p in resolved_map.items():
        result.setdefault(k, p)
    return result
def _write_desktop_shortcut(shortcut_path: Path, widget_name: str):
    """Create (or overwrite) a .lnk at shortcut_path that opens the given
    widget with the installed (packaged) copy of the program.

    Only the packaged copy creates or rewrites widget shortcuts. A copy
    launched from source code must not bind a desktop shortcut to the
    source folder: that would make the widget keep launching the dev copy
    (and reading the dev folder's data) instead of the installed program.
    """
    if not getattr(sys, "frozen", False):
        return
    target_path = Path(sys.executable).resolve()
    args_str = f'"{widget_name}"'
    work_dir = Path(sys.argv[0]).resolve().parent
    ps_cmd = (
        f"$WshShell = New-Object -ComObject WScript.Shell; "
        f"$Shortcut =$WshShell.CreateShortcut('{_ps_escape(str(shortcut_path))}'); "
        f"$Shortcut.TargetPath = '{_ps_escape(str(target_path))}'; "
        f"$Shortcut.Arguments = '{_ps_escape(args_str)}'; "
        f"$Shortcut.WorkingDirectory = '{_ps_escape(str(work_dir))}'; "
        f"$Shortcut.Save()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], creationflags=CREATE_NO_WINDOW)
def create_desktop_shortcut(widget_name: str) -> bool:
    """Create (or refresh) the desktop shortcut for a widget. Returns True
    when the shortcut is in place; False when the running copy cannot
    create shortcuts (a dev copy started from source code) or on failure."""
    if not getattr(sys, "frozen", False):
        return False
    try:
        _write_desktop_shortcut(get_desktop_shortcut_path(widget_name), widget_name)
        return True
    except Exception:
        return False
def _parse_quoted_args(arguments: str) -> list:
    """Extract the double-quoted tokens from a shortcut's argument string.
    Widget names may contain spaces, so a naive split would break them."""
    tokens = []
    i = 0
    n = len(arguments or "")
    while i < n:
        if arguments[i] == '"':
            j = arguments.find('"', i + 1)
            if j == -1:
                tokens.append(arguments[i + 1:])
                break
            tokens.append(arguments[i + 1:j])
            i = j + 1
        else:
            i += 1
    return tokens
def _parse_widget_name_from_args(arguments: str):
    """Recover the widget name a shortcut was created for. Frozen shortcuts
    pass just the widget name; source shortcuts pass script + widget name."""
    tokens = _parse_quoted_args(arguments or "")
    if not tokens:
        return None
    if getattr(sys, 'frozen', False):
        if len(tokens) == 1:
            return tokens[0]
        # A shortcut left over from a dev (source-code) run: script + widget.
        if len(tokens) >= 2 and os.path.basename(tokens[0]).lower() == APP_SCRIPT_NAME:
            return tokens[1]
        return None
    return tokens[1] if len(tokens) >= 2 else None
def _shortcut_is_ours(target, arguments: str) -> bool:
    """True when a desktop shortcut was created by (a previous copy of) this
    program: the target is our executable and the arguments carry a widget
    name, or it runs our script from a python interpreter.

    In a packaged build, shortcuts that a dev (source-code) run left bound
    to python + our script count as ours too, so repair reclaims them and
    the widget launches the installed program instead of the source code.
    """
    if not target or not arguments:
        return False
    try:
        target_name = Path(target).name.lower()
    except Exception:
        return False
    tokens = _parse_quoted_args(arguments)
    if getattr(sys, 'frozen', False):
        if target_name == Path(sys.executable).name.lower():
            return len(tokens) == 1
        if target_name.startswith("python"):
            return len(tokens) >= 2 and os.path.basename(tokens[0]).lower() == APP_SCRIPT_NAME
        return False
    if not target_name.startswith("python"):
        return False
    return len(tokens) >= 2 and os.path.basename(tokens[0]).lower() == os.path.basename(Path(sys.argv[0])).lower()
def resolve_shortcut_details_batch(shortcut_paths):
    """Resolve the target path and argument string of many .lnk files with a
    single PowerShell call. Returns {resolved_lnk_path: (target, arguments)}."""
    result = {}
    if not shortcut_paths:
        return result
    resolved_map = {}
    for p in shortcut_paths:
        try:
            resolved_map[str(Path(p).resolve())] = Path(p)
        except Exception:
            result[str(p)] = (p, "")
    if not resolved_map:
        return result
    chunk_size = 40
    keys = list(resolved_map)
    for start in range(0, len(keys), chunk_size):
        chunk = keys[start:start + chunk_size]
        path_args = ", ".join("'" + _ps_escape(k) + "'" for k in chunk)
        ps_cmd = (
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "$WshShell = New-Object -ComObject WScript.Shell; "
            "$paths = @(" + path_args + "); "
            "foreach ($p in $paths) { "
            "$sh = $WshShell.CreateShortcut($p); "
            "Write-Output ($p + \"`t\" + $sh.TargetPath + \"`t\" + $sh.Arguments) }"
        )
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW, timeout=30,
            )
            for line in res.stdout.lstrip("\ufeff").splitlines():
                p_str, sep, rest = line.partition("\t")
                p_str = p_str.strip()
                if not sep or p_str not in resolved_map:
                    continue
                target_str, sep2, args_str = rest.partition("\t")
                target_str = target_str.strip()
                args_str = args_str.strip()
                target = Path(target_str) if target_str else resolved_map[p_str]
                result[p_str] = (target, args_str)
        except Exception:
            pass
    for k, p in resolved_map.items():
        result.setdefault(k, (p, ""))
    return result
def repair_orphaned_desktop_shortcuts():
    """Repoint desktop widget shortcuts to the currently running copy of the
    program.

    Widget shortcuts hard-code the path of the executable that created them.
    After an update to a new version (e.g. a portable build unpacked into a
    different folder) that path may point at a copy that no longer exists,
    leaving the desktop shortcut broken. Shortcuts that belong to this
    program and target a missing or different executable are recreated in
    place so they keep working and open the widget with the new version.
    """
    try:
        current_exe = str(Path(sys.executable).resolve()).lower()
    except Exception:
        return
    search_dirs = [get_desktop_path()]
    public_desktop = get_public_desktop_path()
    if public_desktop.exists():
        search_dirs.append(public_desktop)
    lnk_paths = []
    for d in search_dirs:
        try:
            lnk_paths.extend(p for p in d.iterdir() if p.suffix.lower() == ".lnk")
        except Exception:
            continue
    if not lnk_paths:
        return
    details = resolve_shortcut_details_batch(lnk_paths)
    for p in lnk_paths:
        try:
            key = str(Path(p).resolve())
        except Exception:
            continue
        entry = details.get(key)
        if not entry:
            continue
        target, arguments = entry
        if not target or not arguments or not _shortcut_is_ours(target, arguments):
            continue
        try:
            target_exists = Path(target).exists()
        except Exception:
            target_exists = False
        if target_exists:
            try:
                if str(Path(target).resolve()).lower() == current_exe:
                    continue  # already points at the running copy
            except Exception:
                continue
        widget_name = _parse_widget_name_from_args(arguments)
        if widget_name:
            try:
                _write_desktop_shortcut(p, widget_name)
            except Exception:
                pass
def delete_desktop_shortcut(widget_name: str) -> bool:
    """Remove the desktop shortcut(s) for a widget. Returns True when the
    removal was attempted; False when the running copy may not manage
    shortcuts (a dev copy started from source code)."""
    if not getattr(sys, "frozen", False):
        # Dev copies must not remove the installed app's desktop shortcuts;
        # the source run works only with its own folder's data.
        return False
    try:
        shortcut_path = get_desktop_shortcut_path(widget_name)
        public_shortcut = get_public_desktop_path() / f"{widget_name}.lnk"
        paths = (shortcut_path, public_shortcut)
        for sp in paths:
            if sp.exists() and not move_to_recycle_bin(sp):
                delete_permanently(sp)
        return True
    except Exception:
        return False
class AccessibleSmartButton(QPushButton):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._is_available = True
        self._base_text = text
    def set_available(self, available: bool):
        self._is_available = available
        self.setEnabled(available)
        self.setToolTip("" if available else "Action unavailable, select items")
class DesktopScanWorker(QThread):
    finished = pyqtSignal(list)
    def run(self):
        items = []
        try:
            search_dirs = [get_desktop_path()]
            public_desktop = get_public_desktop_path()
            if public_desktop.exists():
                search_dirs.append(public_desktop)
            for d in search_dirs:
                if self.isInterruptionRequested():
                    break
                try:
                    dir_entries = list(d.iterdir())
                except Exception:
                    continue
                lnk_paths = [f for f in dir_entries if f.suffix.lower() == ".lnk"]
                resolved = resolve_shortcut_targets_batch(lnk_paths)
                for file_path in dir_entries:
                    if self.isInterruptionRequested():
                        break
                    if file_path.suffix.lower() == ".lnk":
                        try:
                            target = resolved.get(str(Path(file_path).resolve()), file_path)
                        except Exception:
                            target = file_path
                    elif file_path.is_file() and file_path.name.lower() != "desktop.ini":
                        target = file_path
                    else:
                        continue
                    items.append((file_path.stem, target, file_path))
        except Exception:
            pass
        self.finished.emit(items)
class UpdateCheckWorker(QThread):
    """Fetch the latest release version and its portable zip from GitHub."""
    completed = pyqtSignal(object, object, object)  # (version or None, asset_url or None, error or None)
    def run(self):
        try:
            req = urllib.request.Request(
                UPDATE_CHECK_URL,
                headers={
                    "User-Agent": f"DesktopOrganizer/{APP_VERSION}",
                    "Accept": "application/vnd.github+json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    # No releases published yet: nothing newer than what we run.
                    self.completed.emit(None, None, None)
                    return
                raise
            if not isinstance(data, dict):
                self.completed.emit(None, None, None)
                return
            tag = data.get("tag_name")
            # The portable build is attached as a zip named like
            # "desktop_organizer_1.0.0.zip"; fall back to the first zip asset.
            asset_url = None
            for asset in (data.get("assets") or []):
                if not isinstance(asset, dict):
                    continue
                name = (asset.get("name") or "").lower()
                url = asset.get("browser_download_url")
                if not (name.endswith(".zip") and url):
                    continue
                if asset_url is None:
                    asset_url = url
                if name.startswith("desktop_organizer"):
                    asset_url = url
                    break
            self.completed.emit(_normalize_version(tag) if tag else None, asset_url, None)
        except Exception as e:
            self.completed.emit(None, None, str(e))
class UpdateDownloadWorker(QThread):
    """Download the release zip asset with progress reporting."""
    progress = pyqtSignal(int, int)  # (done_bytes, total_bytes; 0 total = unknown)
    completed = pyqtSignal(object, object)  # (zip_path or None, error or None)
    def __init__(self, url: str, dest: Path, parent=None):
        super().__init__(parent)
        self._url = url
        self._dest = dest
    def run(self):
        tmp = self._dest.with_name(self._dest.name + ".part")
        try:
            req = urllib.request.Request(
                self._url,
                headers={"User-Agent": f"DesktopOrganizer/{APP_VERSION}"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = 0
                try:
                    total = int(resp.headers.get("Content-Length") or 0)
                except Exception:
                    total = 0
                with open(tmp, "wb") as f:
                    done = 0
                    while True:
                        if self.isInterruptionRequested():
                            raise InterruptedError("cancelled")
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        self.progress.emit(done, total)
            tmp.replace(self._dest)
            self.completed.emit(self._dest, None)
        except InterruptedError:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            self.completed.emit(None, "cancelled")
        except Exception as e:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            self.completed.emit(None, str(e))
_running_download_workers = set()
def _forget_download_worker(worker):
    _running_download_workers.discard(worker)
def safe_resolve_str(path: Path) -> str:
    try:
        return str(Path(path).resolve()).lower()
    except Exception:
        return str(path).lower()
def item_display_name(path: Path, hide_extension: bool = False) -> str:
    name = path.name if path.name else str(path)
    if hide_extension and path.is_file():
        return path.stem
    return name
def item_display_name_for(widget_name: str, path: Path, names_data=None) -> str:
    """Display name of an item inside a widget: the custom override if set,
    otherwise the item's real name. The file on disk is never touched."""
    if names_data is None:
        names_data = load_item_names()
    overrides = names_data.get(widget_name)
    if isinstance(overrides, dict):
        override = overrides.get(str(path))
        if override:
            return override
    return item_display_name(path, hide_extension=True)
class SettingsDialog(QDialog):
    """Language/settings dialog. With language_only=True it becomes the
    compact language picker shown on the very first launch."""
    def __init__(self, parent=None, language_only: bool = False):
        super().__init__(parent)
        self.language_only = language_only
        self._update_worker = None
        self._original_lang = CURRENT_LANG
        self.selected_language = CURRENT_LANG
        self.setWindowTitle(tr("select_lang_title" if language_only else "settings_title"))
        self.resize(380, 160 if language_only else 320)
        layout = QVBoxLayout(self)
        if language_only:
            self.prompt_label = QLabel(tr("select_lang_prompt"))
            self.prompt_label.setWordWrap(True)
            layout.addWidget(self.prompt_label)
        else:
            self.lang_label = QLabel(tr("language_label"))
            layout.addWidget(self.lang_label)
        # Combo item labels stay in their own language to avoid double NVDA speech.
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("English", "en")
        self.lang_combo.addItem("Русский", "ru")
        self.lang_combo.setAccessibleName(tr("select_lang_prompt" if language_only else "language_label"))
        self.lang_combo.currentIndexChanged.connect(self.on_language_changed)
        layout.addWidget(self.lang_combo)
        if not language_only:
            self.lang_hint = QLabel(tr("language_hint"))
            self.lang_hint.setWordWrap(True)
            layout.addWidget(self.lang_hint)
            self.confirm_exit_cb = QCheckBox(tr("ask_on_exit"))
            self.confirm_exit_cb.setAccessibleName(tr("ask_on_exit"))
            self.confirm_exit_cb.setChecked(load_settings().get("confirm_exit", True))
            layout.addWidget(self.confirm_exit_cb)
            self.auto_update_cb = QCheckBox(tr("auto_check_updates"))
            self.auto_update_cb.setAccessibleName(tr("auto_check_updates"))
            self.auto_update_cb.setChecked(load_settings().get("check_updates", True))
            layout.addWidget(self.auto_update_cb)
            self.close_on_launch_cb = QCheckBox(tr("close_widget_on_launch_label"))
            self.close_on_launch_cb.setAccessibleName(tr("close_widget_on_launch_label"))
            self.close_on_launch_cb.setChecked(load_settings().get("close_widget_on_launch", True))
            layout.addWidget(self.close_on_launch_cb)
            self.check_now_btn = QPushButton(tr("update_check_now_btn"))
            self.check_now_btn.setAccessibleName(tr("update_check_now_btn"))
            self.check_now_btn.clicked.connect(self.check_updates_now)
            layout.addWidget(self.check_now_btn)
        buttons_layout = QHBoxLayout()
        self.confirm_btn = QPushButton(tr("confirm"))
        self.confirm_btn.setDefault(True)
        self.confirm_btn.clicked.connect(self.accept)
        self.cancel_btn = QPushButton(tr("cancel"))
        self.cancel_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(self.confirm_btn)
        buttons_layout.addWidget(self.cancel_btn)
        layout.addLayout(buttons_layout)
        # Set the language last so retranslate_dialog sees every widget created.
        index = self.lang_combo.findData(CURRENT_LANG)
        self.lang_combo.setCurrentIndex(index if index >= 0 else 0)
        announce_speech(tr("select_lang_prompt" if language_only else "language_label"))
    def on_language_changed(self, index):
        """Switch the dialog language so its controls follow the choice."""
        load_translations(self.lang_combo.itemData(index) or "en")
        self.retranslate_dialog()
    def reject(self):
        """Restore the previous language when the dialog is cancelled."""
        if CURRENT_LANG != self._original_lang:
            load_translations(self._original_lang)
        super().reject()
    def retranslate_dialog(self):
        self.setWindowTitle(tr("select_lang_title" if self.language_only else "settings_title"))
        if self.language_only:
            self.prompt_label.setText(tr("select_lang_prompt"))
            self.lang_combo.setAccessibleName(tr("select_lang_prompt"))
        else:
            self.lang_label.setText(tr("language_label"))
            self.lang_combo.setAccessibleName(tr("language_label"))
            self.lang_hint.setText(tr("language_hint"))
            self.confirm_exit_cb.setText(tr("ask_on_exit"))
            self.confirm_exit_cb.setAccessibleName(tr("ask_on_exit"))
            self.auto_update_cb.setText(tr("auto_check_updates"))
            self.auto_update_cb.setAccessibleName(tr("auto_check_updates"))
            self.close_on_launch_cb.setText(tr("close_widget_on_launch_label"))
            self.close_on_launch_cb.setAccessibleName(tr("close_widget_on_launch_label"))
            self.check_now_btn.setText(tr("update_check_now_btn"))
            self.check_now_btn.setAccessibleName(tr("update_check_now_btn"))
        self.confirm_btn.setText(tr("confirm"))
        self.cancel_btn.setText(tr("cancel"))
    def accept(self):
        self.selected_language = self.lang_combo.currentData() or "en"
        if not self.language_only:
            self.confirm_exit = self.confirm_exit_cb.isChecked()
            self.check_updates = self.auto_update_cb.isChecked()
            self.close_widget_on_launch = self.close_on_launch_cb.isChecked()
        load_translations(self.selected_language)
        super().accept()
    def check_updates_now(self):
        """Run an immediate update check from the settings dialog."""
        if getattr(self, "_update_worker", None) is not None and self._update_worker.isRunning():
            return
        self.check_now_btn.setEnabled(False)
        self.check_now_btn.setText(tr("update_checking"))
        worker = UpdateCheckWorker()
        worker.completed.connect(self.on_manual_check_done)
        self._update_worker = worker
        worker.start()
    def on_manual_check_done(self, latest, asset_url, error):
        try:
            self.check_now_btn.setEnabled(True)
            self.check_now_btn.setText(tr("update_check_now_btn"))
            self._update_worker = None
        except RuntimeError:
            return  # the dialog was closed while the check was running
        if error:
            report_warning(self, "update_check_failed", title_key="attention_title", error=error)
            return
        if latest and is_newer_version(latest):
            prompt_update_dialog(self, latest, asset_url, respect_skip=False)
        else:
            text = tr("update_up_to_date", version=APP_VERSION)
            announce_speech(text)
            box = _message_box(self, tr("settings_title"), text, QMessageBox.Icon.Information)
            box.addButton(QMessageBox.StandardButton.Ok)
            box.exec()
class ExitConfirmDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("confirm_exit_title"))
        self.resize(380, 190)
        layout = QVBoxLayout(self)
        label = QLabel(tr("confirm_exit_msg"))
        label.setWordWrap(True)
        layout.addWidget(label)
        hint_label = QLabel(tr("ask_on_exit_hint"))
        hint_label.setWordWrap(True)
        layout.addWidget(hint_label)
        self.ask_on_exit_cb = QCheckBox(tr("ask_on_exit"))
        self.ask_on_exit_cb.setAccessibleName(tr("ask_on_exit"))
        self.ask_on_exit_cb.setChecked(load_settings().get("confirm_exit", True))
        layout.addWidget(self.ask_on_exit_cb)
        buttons_layout = QHBoxLayout()
        yes_btn = QPushButton(tr("yes"))
        yes_btn.setAutoDefault(False)
        yes_btn.setDefault(True)
        yes_btn.setAccessibleName(tr("yes"))
        yes_btn.clicked.connect(self.accept)
        no_btn = QPushButton(tr("no"))
        no_btn.setAutoDefault(False)
        no_btn.setAccessibleName(tr("no"))
        no_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(yes_btn)
        buttons_layout.addWidget(no_btn)
        layout.addLayout(buttons_layout)
        announce_speech(tr("confirm_exit_msg"))
class RenameItemDialog(QDialog):
    """Ask for a new display name for an item inside a widget."""
    def __init__(self, current_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("rename_item_title"))
        self.resize(380, 140)
        layout = QVBoxLayout(self)
        prompt_label = QLabel(tr("rename_item_prompt"))
        layout.addWidget(prompt_label)
        self.name_input = QLineEdit(current_name)
        self.name_input.selectAll()
        # Accessible name announces the field's purpose once, on focus.
        self.name_input.setAccessibleName(tr("rename_item_prompt"))
        prompt_label.setBuddy(self.name_input)
        layout.addWidget(self.name_input)
        buttons_layout = QHBoxLayout()
        ok_btn = QPushButton(tr("rename"))
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton(tr("cancel"))
        cancel_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(ok_btn)
        buttons_layout.addWidget(cancel_btn)
        layout.addLayout(buttons_layout)
    def new_name(self):
        return self.name_input.text().strip()
class DeleteItemsDialog(QDialog):
    """Mark widget items with checkboxes and remove the marked ones after
    confirmation. Files and folders on disk are never touched."""

    def __init__(self, widget_name, parent=None):
        super().__init__(parent)
        self.widget_name = widget_name or ""
        self.removed_count = 0
        self.removed_name = ""
        self.setWindowTitle(tr("delete_items_title"))
        self.resize(460, 420)
        layout = QVBoxLayout(self)
        prompt = QLabel(tr("delete_items_prompt"))
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        self.items_list = QListWidget()
        data = load_widgets_data()
        names_data = load_item_names()
        for p_str in data.get(self.widget_name, []):
            if not isinstance(p_str, str):
                continue
            try:
                p = Path(p_str)
            except Exception:
                continue
            item = QListWidgetItem(item_display_name_for(self.widget_name, p, names_data))
            item.setData(Qt.ItemDataRole.UserRole, str(p))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.items_list.addItem(item)
        layout.addWidget(self.items_list)
        buttons_layout = QHBoxLayout()
        delete_btn = QPushButton(tr("delete_item_btn"))
        delete_btn.setAutoDefault(False)
        delete_btn.setDefault(False)
        delete_btn.clicked.connect(self._confirm_delete)
        cancel_btn = QPushButton(tr("cancel"))
        cancel_btn.setAutoDefault(False)
        cancel_btn.setDefault(False)
        cancel_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(delete_btn)
        buttons_layout.addWidget(cancel_btn)
        layout.addLayout(buttons_layout)

    def showEvent(self, event):
        """Focus the checklist on open so the user can mark items right away
        (arrow keys to move, Space to check/uncheck)."""
        super().showEvent(event)
        if self.items_list.count() > 0:
            self.items_list.setCurrentRow(0)
            self.items_list.setFocus()

    def _checked_items(self):
        checked = []
        for i in range(self.items_list.count()):
            item = self.items_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) and item.checkState() == Qt.CheckState.Checked:
                checked.append(item)
        return checked

    def _confirm_delete(self):
        checked = self._checked_items()
        if not checked:
            announce_speech(tr("check_items_first"))
            return
        if len(checked) == 1:
            confirm_text = tr("delete_item_confirm", name=checked[0].text())
        else:
            confirm_text = tr("delete_items_confirm", count=len(checked))
        msg_box = _message_box(self, tr("delete_confirm_title"), confirm_text)
        yes_btn = msg_box.addButton(tr("yes"), QMessageBox.ButtonRole.YesRole)
        no_btn = msg_box.addButton(tr("no"), QMessageBox.ButtonRole.NoRole)
        msg_box.setDefaultButton(no_btn)
        # The message box's accessible name makes NVDA read the question.
        msg_box.exec()
        if msg_box.clickedButton() != yes_btn:
            return
        seen = set()
        names = []
        for item in checked:
            path_str = item.data(Qt.ItemDataRole.UserRole)
            if not path_str:
                continue
            target_path = str(Path(path_str))
            if target_path in seen:
                continue
            seen.add(target_path)
            names.append(item.text())
        if not seen:
            return
        remove_item_paths_from_widget(self.widget_name, seen)
        self.removed_count = len(names)
        self.removed_name = names[0] if len(names) == 1 else ""
        self.accept()
class ItemOrderListWidget(QListWidget):
    """QListWidget that supports drag & drop reordering. Emits signals so
    the widget can save the new order and speak the item's live position
    while the user drags it."""
    order_changed = pyqtSignal(object, int)  # (moved item, new row)
    drag_position = pyqtSignal(object, int)  # (dragged item, preview row)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_item = None
        self._last_drag_row = None

    def dragEnterEvent(self, event):
        super().dragEnterEvent(event)
        if event.isAccepted():
            self._drag_item = self.currentItem()
            self._last_drag_row = None

    def dragMoveEvent(self, event):
        super().dragMoveEvent(event)
        if not event.isAccepted():
            return
        dragged = self._drag_item
        if dragged is None:
            dragged = self.currentItem()
        if dragged is None:
            return
        row = self._target_row_for(event)
        if row != self._last_drag_row:
            self._last_drag_row = row
            self.drag_position.emit(dragged, row)

    def dropEvent(self, event):
        super().dropEvent(event)
        if event.isAccepted():
            dragged = self._drag_item
            if dragged is None:
                dragged = self.currentItem()
            if dragged is not None:
                self.order_changed.emit(dragged, self.row(dragged))
            self._last_drag_row = None
            self._drag_item = None

    def _target_row_for(self, event) -> int:
        """Row the dragged item would occupy if dropped at the cursor."""
        count = self.count()
        idx = self.indexAt(event.position().toPoint())
        if not idx.isValid():
            return count
        row = idx.row()
        if self.dropIndicatorPosition() == QAbstractItemView.DropIndicatorPosition.BelowItem:
            row += 1
        return max(0, min(row, count))


class WidgetViewDialog(QDialog):
    def __init__(self, widget_name, parent=None):
        super().__init__(parent)
        self.widget_name = widget_name or ""
        self._last_launch_time = 0
        self._last_drag_announce = 0
        self.setWindowTitle(self.widget_name)
        self.resize(550, 450)
        layout = QVBoxLayout(self)
        self.path_label = QLabel(self.widget_name)
        layout.addWidget(self.path_label)
        self.items_list = ItemOrderListWidget()
        self.items_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.items_list.customContextMenuRequested.connect(self.show_item_context_menu)
        # Items can be reordered by dragging or with the move buttons.
        self.items_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.items_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        # Deleting is done with item checkboxes (mark items, then Delete).
        self.items_list.order_changed.connect(self.on_items_reordered)
        self.items_list.drag_position.connect(self._announce_drag_position)
        # Rename is available from the context menu and via the F2 hotkey.
        self.rename_action = QAction(tr("rename_item_btn"), self)
        self.rename_action.setShortcut(QKeySequence(Qt.Key.Key_F2))
        self.rename_action.triggered.connect(self.rename_selected_item)
        self.addAction(self.rename_action)
        self.move_up_action = QAction(tr("move_up_btn"), self)
        self.move_up_action.setShortcut(QKeySequence("Alt+Up"))
        self.move_up_action.triggered.connect(lambda: self.move_selected_item(-1))
        self.addAction(self.move_up_action)
        self.move_down_action = QAction(tr("move_down_btn"), self)
        self.move_down_action.setShortcut(QKeySequence("Alt+Down"))
        self.move_down_action.triggered.connect(lambda: self.move_selected_item(1))
        self.addAction(self.move_down_action)
        self.delete_action = QAction(tr("delete_item_btn"), self)
        self.delete_action.setShortcut(QKeySequence(Qt.Key.Key_Delete))
        self.delete_action.triggered.connect(self.delete_selected_item)
        self.addAction(self.delete_action)
        self.items_menu = QMenu(self)
        self.items_menu.addAction(self.rename_action)
        self.items_menu.addSeparator()
        self.items_menu.addAction(self.delete_action)
        layout.addWidget(self.items_list)
        buttons_layout = QHBoxLayout()
        launch_btn = QPushButton(tr("open_launch"))
        launch_btn.setAccessibleName(tr("open_launch_acc"))
        launch_btn.setAutoDefault(False)
        launch_btn.setDefault(False)
        launch_btn.clicked.connect(self.launch_or_open_selected_item)
        buttons_layout.addWidget(launch_btn)
        delete_btn = QPushButton(tr("delete_item_btn"))
        delete_btn.setAutoDefault(False)
        delete_btn.setDefault(False)
        delete_btn.clicked.connect(self.delete_selected_item)
        buttons_layout.addWidget(delete_btn)
        shortcut_btn = QPushButton(tr("create_shortcut_btn"))
        shortcut_btn.setAccessibleName(tr("create_shortcut_acc"))
        shortcut_btn.setAutoDefault(False)
        shortcut_btn.setDefault(False)
        shortcut_btn.clicked.connect(self.create_shortcut_for_this_widget)
        buttons_layout.addWidget(shortcut_btn)
        close_btn = QPushButton(tr("close"))
        close_btn.setAutoDefault(False)
        close_btn.setDefault(False)
        close_btn.clicked.connect(self.accept)
        buttons_layout.addWidget(close_btn)
        layout.addLayout(buttons_layout)
        self.load_widget_items()
        self.items_list.itemActivated.connect(self.launch_or_open_selected_item)

    def showEvent(self, event):
        """Put focus on the first list item as soon as the widget opens, so
        screen reader users immediately hear the first element."""
        super().showEvent(event)
        if self.items_list.count() > 0:
            self.items_list.setFocus()

    def load_widget_items(self):
        self.items_list.clear()
        data = load_widgets_data()
        paths = data.get(self.widget_name, [])
        if not isinstance(paths, list):
            paths = []
        self.path_label.setText(self.widget_name)
        if not paths:
            placeholder = QListWidgetItem(tr("empty_widget"))
            placeholder.setFlags(placeholder.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.items_list.addItem(placeholder)
            return
        names_data = load_item_names()
        for p_str in paths:
            try:
                p = Path(p_str)
                item = QListWidgetItem(item_display_name_for(self.widget_name, p, names_data))
                item.setData(Qt.ItemDataRole.UserRole, str(p))
                self.items_list.addItem(item)
            except Exception:
                continue
        if self.items_list.count() > 0:
            self.items_list.setCurrentRow(0)

    def move_selected_item(self, delta: int):
        """Move the selected item one step up (-1) or down (+1) and save the
        new order so it stays the same on the next open."""
        current_row = self.items_list.currentRow()
        target_row = current_row + delta
        if current_row < 0 or target_row < 0 or target_row >= self.items_list.count():
            return
        current_item = self.items_list.item(current_row)
        if current_item is None or not current_item.data(Qt.ItemDataRole.UserRole):
            return
        moved_item = self.items_list.takeItem(current_row)
        self.items_list.insertItem(target_row, moved_item)
        self.items_list.setCurrentItem(moved_item)
        self.save_item_order()
        self._schedule_position_announce(moved_item, target_row)
    def on_items_reordered(self, item, row: int):
        """Save the new order after a drag & drop and speak the result."""
        self.save_item_order()
        self._schedule_position_announce(item, row)
    def _schedule_position_announce(self, item, row: int):
        """Speak the new position shortly after the move: NVDA first
        announces the element itself on the focus change, so the delayed
        message is the last thing spoken and is not cut off."""
        def _delayed():
            try:
                self._announce_item_position(item, row)
            except RuntimeError:
                pass  # the dialog was closed before the timer fired
        QTimer.singleShot(350, _delayed)
    def _neighbors_of(self, row: int):
        prev_item = self.items_list.item(row - 1) if row > 0 else None
        next_item = self.items_list.item(row + 1) if row < self.items_list.count() - 1 else None
        prev_name = prev_item.text() if prev_item is not None else ""
        next_name = next_item.text() if next_item is not None else ""
        return prev_name, next_name
    def _announce_item_position(self, item, row: int):
        """Speak where the moved item now sits relative to its neighbours, so
        screen reader users can keep track while reordering."""
        prev_name, next_name = self._neighbors_of(row)
        if not (prev_name or next_name):
            return
        if prev_name and next_name:
            text = tr("item_moved_between", name=item.text(), prev=prev_name, next=next_name)
        elif next_name:
            text = tr("item_moved_first", name=item.text(), next=next_name)
        else:
            text = tr("item_moved_last", name=item.text(), prev=prev_name)
        announce_speech(text, interrupt=True)
    def _announce_drag_position(self, item, row: int):
        """Live preview while dragging: speak where the item would land and
        remind that releasing the mouse button applies the move. Throttled so
        rapid drags do not pile up speech processes."""
        now = time.time()
        if now - self._last_drag_announce < 0.7:
            return
        self._last_drag_announce = now
        prev_name, next_name = self._neighbors_of(row)
        if not (prev_name or next_name):
            return
        if prev_name and next_name:
            text = tr("item_drag_between", name=item.text(), prev=prev_name, next=next_name)
        elif next_name:
            text = tr("item_drag_first", name=item.text(), next=next_name)
        else:
            text = tr("item_drag_last", name=item.text(), prev=prev_name)
        announce_speech(text, interrupt=True)
    def save_item_order(self):
        """Write the current list order of this widget's items to storage."""
        paths = []
        for i in range(self.items_list.count()):
            item = self.items_list.item(i)
            path_str = item.data(Qt.ItemDataRole.UserRole)
            if path_str:
                paths.append(path_str)
        data = load_widgets_data()
        data[self.widget_name] = paths
        save_widgets_data(data)
    def show_item_context_menu(self, pos):
        item = self.items_list.itemAt(pos)
        if item is None or not item.data(Qt.ItemDataRole.UserRole):
            return
        # Keep an existing multi-selection when the click lands on one of its
        # items, so "Delete" can remove several items at once. Otherwise
        # select only the clicked item (setCurrentItem clears the old one).
        if item not in self.items_list.selectedItems():
            self.items_list.setCurrentItem(item)
        self.items_menu.exec(self.items_list.viewport().mapToGlobal(pos))
    def create_shortcut_for_this_widget(self):
        """Create (or refresh) the desktop shortcut that opens this widget."""
        if create_desktop_shortcut(self.widget_name):
            announce_speech(tr("shortcut_created_announce", name=self.widget_name))
        else:
            announce_speech(tr("shortcut_unavailable_announce"))
    def launch_or_open_selected_item(self):
        current_time = time.time()
        if current_time - self._last_launch_time < 0.6:
            return
        self._last_launch_time = current_time
        current_item = self.items_list.currentItem()
        if not current_item:
            return
        file_path_str = current_item.data(Qt.ItemDataRole.UserRole)
        if not file_path_str:
            return
        target_path = Path(file_path_str)
        if not target_path.exists():
            report_warning(self, "element_not_found_msg", title_key="element_not_found_title")
            return
        try:
            announce_speech(tr("launching", name=item_display_name_for(self.widget_name, target_path)))
            os.startfile(str(target_path))
            settings = load_settings()
            if settings.get("close_widget_on_launch", True):
                self.accept()
        except Exception as e:
            report_error(self, "launch_error_msg", e, title_key="launch_error_title")
    def rename_selected_item(self):
        """Set a custom display name for the selected item. The file on disk
        keeps its real name; only the name shown in the widget changes."""
        current_item = self.items_list.currentItem()
        if not current_item:
            announce_speech(tr("select_item_first"))
            return
        path_str = current_item.data(Qt.ItemDataRole.UserRole)
        if not path_str:
            return
        names_data = load_item_names()
        overrides = names_data.get(self.widget_name)
        if not isinstance(overrides, dict):
            overrides = {}
            names_data[self.widget_name] = overrides
        current_name = overrides.get(path_str) or item_display_name(Path(path_str), hide_extension=True)
        dialog = RenameItemDialog(current_name, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_name = dialog.new_name()
        if new_name == current_name:
            return
        if new_name:
            overrides[path_str] = new_name
        else:
            # Empty input removes the custom name; the real name is shown.
            overrides.pop(path_str, None)
        save_item_names(names_data)
        self.load_widget_items()
        for i in range(self.items_list.count()):
            item = self.items_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == path_str:
                self.items_list.setCurrentItem(item)
                break
        final_name = new_name or item_display_name(Path(path_str), hide_extension=True)
        announce_speech(tr("item_renamed", name=final_name))
    def delete_selected_item(self):
        """Open the deletion dialog: mark the items to remove with checkboxes,
        confirm, and delete them from this widget. Only the widget's list is
        changed; files and folders on disk are not touched."""
        has_items = any(
            self.items_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.items_list.count())
        )
        if not has_items:
            announce_speech(tr("empty_widget"))
            return
        dialog = DeleteItemsDialog(self.widget_name, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.load_widget_items()
        self.items_list.setFocus()
        if dialog.removed_count == 1:
            text = tr("item_removed", name=dialog.removed_name)
        else:
            text = tr("items_removed_count", count=dialog.removed_count)
        # Delay so NVDA finishes announcing the newly focused item first.
        QTimer.singleShot(350, lambda: announce_speech(text))
_running_scan_workers = set()
def _forget_scan_worker(worker):
    _running_scan_workers.discard(worker)
class WidgetCreationWizardDialog(QDialog):
    def __init__(self, edit_widget_name=None, parent=None):
        super().__init__(parent)
        self.edit_widget_name = edit_widget_name
        self.is_editing = edit_widget_name is not None
        self.setWindowTitle(tr("edit_widget_title", name=edit_widget_name) if self.is_editing else tr("create_widget_title"))
        self.resize(600, 520)
        layout = QVBoxLayout(self)
        input_label = QLabel(tr("widget_name_label"))
        layout.addWidget(input_label)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText(tr("widget_name_placeholder"))
        # Accessible name makes screen readers announce the field's purpose
        # instead of just its role ("editor"). The label is the field's buddy,
        # so the phrase is spoken only once, when the field gets focus.
        self.name_input.setAccessibleName(tr("widget_name_accessible"))
        input_label.setBuddy(self.name_input)
        self.name_input.textChanged.connect(self.update_create_button_state)
        if self.is_editing:
            self.name_input.setText(edit_widget_name)
        layout.addWidget(self.name_input)
        list_label = QLabel(tr("desktop_shortcuts_label"))
        layout.addWidget(list_label)
        self.shortcuts_list = QListWidget()
        list_label.setBuddy(self.shortcuts_list)
        self.shortcuts_list.itemChanged.connect(self.update_create_button_state)
        loading_item = QListWidgetItem(tr("scanning_shortcuts"))
        loading_item.setFlags(loading_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.shortcuts_list.addItem(loading_item)
        layout.addWidget(self.shortcuts_list)
        add_items_layout = QHBoxLayout()
        add_file_btn = QPushButton(tr("add_file"))
        add_file_btn.setAccessibleName(tr("add_file_acc"))
        add_file_btn.clicked.connect(self.add_manual_file)
        add_folder_btn = QPushButton(tr("add_folder"))
        add_folder_btn.setAccessibleName(tr("add_folder_acc"))
        add_folder_btn.clicked.connect(self.add_manual_folder)
        add_items_layout.addWidget(add_file_btn)
        add_items_layout.addWidget(add_folder_btn)
        layout.addLayout(add_items_layout)
        buttons_layout = QHBoxLayout()
        save_btn_text = tr("save_changes") if self.is_editing else tr("create")
        self.create_btn = AccessibleSmartButton(save_btn_text)
        self.create_btn.setAutoDefault(False)
        self.create_btn.setDefault(False)
        self.create_btn.clicked.connect(self.on_save_clicked)
        buttons_layout.addWidget(self.create_btn)
        layout.addLayout(buttons_layout)
        self.update_create_button_state()
        # The field's purpose is announced via its accessible name, so no
        # separate speech announcement here (would duplicate the phrase).
        self.scan_worker = DesktopScanWorker()
        self.scan_worker.finished.connect(self.on_desktop_scanned)
        self.scan_worker.start()
    def done(self, result):
        self._stop_scan_worker()
        super().done(result)
    def _stop_scan_worker(self):
        worker = getattr(self, "scan_worker", None)
        if worker is None:
            return
        try:
            if worker.isRunning():
                worker.requestInterruption()
                worker.wait(2000)
        except Exception:
            pass
        if worker.isRunning():
            # Keep the thread alive until it exits (PyQt6 aborts otherwise).
            _running_scan_workers.add(worker)
            worker.finished.connect(lambda w=worker: _forget_scan_worker(w))
    def update_create_button_state(self):
        # getattr guard: textChanged fires before the other widgets exist.
        has_text = bool(self.name_input.text().strip())
        has_checked = False
        shortcuts_list = getattr(self, "shortcuts_list", None)
        if shortcuts_list is not None:
            for i in range(shortcuts_list.count()):
                item = shortcuts_list.item(i)
                if item.checkState() == Qt.CheckState.Checked:
                    has_checked = True
                    break
        create_btn = getattr(self, "create_btn", None)
        if create_btn is not None:
            create_btn.set_available(has_text and has_checked)
    def _add_checkable_item(self, name, target_path, lnk_path=None, checked=False):
        item = QListWidgetItem(name)
        item.setData(Qt.ItemDataRole.UserRole, str(target_path))
        if lnk_path is not None:
            item.setData(Qt.ItemDataRole.UserRole + 2, str(lnk_path))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.shortcuts_list.addItem(item)
        return item
    def on_desktop_scanned(self, desktop_items):
        self.shortcuts_list.blockSignals(True)
        try:
            self.shortcuts_list.clear()
            existing_paths = set()
            saved_paths = []
            names_overrides = {}
            if self.is_editing:
                data = load_widgets_data()
                saved_paths = data.get(self.edit_widget_name, [])
                if not isinstance(saved_paths, list):
                    saved_paths = []
                existing_paths = {safe_resolve_str(Path(p)) for p in saved_paths if isinstance(p, str)}
                names_data = load_item_names()
                overrides = names_data.get(self.edit_widget_name)
                if isinstance(overrides, dict):
                    names_overrides = overrides
            if not desktop_items and not existing_paths:
                item = QListWidgetItem(tr("no_shortcuts_found"))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self.shortcuts_list.addItem(item)
                self.update_create_button_state()
                announce_speech(tr("scan_completed_none"))
                return
            if self.is_editing:
                # Existing widget items keep their saved order; desktop items
                # that are not part of the widget yet are appended after.
                scanned_by_path = {}
                for name, target_path, lnk_path in desktop_items:
                    scanned_by_path.setdefault(safe_resolve_str(target_path), (name, target_path, lnk_path))
                added_paths = set()
                for p in saved_paths:
                    if not isinstance(p, str):
                        continue
                    path = Path(p)
                    entry = scanned_by_path.get(safe_resolve_str(path))
                    if entry is not None:
                        name, target_path, lnk_path = entry
                        display_name = names_overrides.get(str(target_path)) or name
                        self._add_checkable_item(display_name, target_path, lnk_path, checked=True)
                        added_paths.add(safe_resolve_str(target_path))
                    elif path.exists():
                        display_name = names_overrides.get(p) or item_display_name(path)
                        self._add_checkable_item(display_name, path, checked=True)
                        added_paths.add(safe_resolve_str(path))
                for name, target_path, lnk_path in desktop_items:
                    if safe_resolve_str(target_path) not in added_paths:
                        self._add_checkable_item(name, target_path, lnk_path, checked=False)
            else:
                for name, target_path, lnk_path in desktop_items:
                    self._add_checkable_item(name, target_path, lnk_path, checked=False)
            self.update_create_button_state()
            announce_speech(tr("scan_completed_count", count=self.shortcuts_list.count()))
        except Exception:
            pass
        finally:
            self.shortcuts_list.blockSignals(False)
    def add_manual_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, tr("select_file_title"))
        if file_path:
            self.add_custom_item_to_list(Path(file_path))
    def add_manual_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self, tr("select_folder_title"))
        if folder_path:
            self.add_custom_item_to_list(Path(folder_path))
    def add_custom_item_to_list(self, path: Path):
        path_str = str(path)
        for i in range(self.shortcuts_list.count()):
            item = self.shortcuts_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == path_str:
                item.setCheckState(Qt.CheckState.Checked)
                self.update_create_button_state()
                announce_speech(tr("item_selected", name=path.name))
                return
        if self.shortcuts_list.count() == 1 and not self.shortcuts_list.item(0).data(Qt.ItemDataRole.UserRole):
            self.shortcuts_list.clear()
        self._add_checkable_item(item_display_name(path), path, checked=True)
        self.update_create_button_state()
        announce_speech(tr("item_added", name=path.name))
    def _confirm_clean_desktop_shortcuts(self, shortcuts):
        shortcut_count = len(shortcuts)
        msg_box = _message_box(self, tr("delete_shortcuts_title"), tr("delete_shortcuts_msg", count=shortcut_count))
        bin_btn = msg_box.addButton(tr("delete_to_bin"), QMessageBox.ButtonRole.YesRole)
        perm_btn = msg_box.addButton(tr("delete_permanently"), QMessageBox.ButtonRole.DestructiveRole)
        keep_btn = msg_box.addButton(tr("keep_shortcuts"), QMessageBox.ButtonRole.NoRole)
        cancel_btn = msg_box.addButton(tr("cancel"), QMessageBox.ButtonRole.RejectRole)
        msg_box.setDefaultButton(bin_btn)
        announce_speech(tr("delete_shortcuts_announce", count=shortcut_count))
        msg_box.exec()
        clicked = msg_box.clickedButton()
        if clicked == cancel_btn:
            announce_speech(tr("creation_cancelled"))
            return False
        if clicked == bin_btn:
            deleted = sum(1 for lnk in shortcuts if move_to_recycle_bin(lnk))
            announce_speech(tr("moved_to_recycle_bin", count=deleted) if deleted else tr("delete_failed"))
        elif clicked == perm_btn:
            deleted = sum(1 for lnk in shortcuts if delete_permanently(lnk))
            announce_speech(tr("deleted_permanently", count=deleted) if deleted else tr("delete_failed"))
        elif clicked == keep_btn:
            announce_speech(tr("shortcuts_kept"))
        return True
    def on_save_clicked(self):
        if not self.create_btn._is_available:
            announce_speech(f"{self.create_btn._base_text}, {tr('unavailable')}")
            return
        new_widget_name = self.name_input.text().strip()
        if not new_widget_name:
            report_warning(self, "enter_widget_name_err", title_key="error_title")
            return
        selected_items = []
        desktop_shortcuts_to_clean = []
        for i in range(self.shortcuts_list.count()):
            item = self.shortcuts_list.item(i)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            target_path_str = item.data(Qt.ItemDataRole.UserRole)
            if not target_path_str:
                continue
            target_p = Path(target_path_str)
            selected_items.append(target_p)
            lnk_path_str = item.data(Qt.ItemDataRole.UserRole + 2)
            if not lnk_path_str:
                continue
            try:
                lnk_p = Path(lnk_path_str)
                if (lnk_p.exists() and lnk_p.suffix.lower() == ".lnk" and not lnk_p.is_dir()
                        and is_on_desktop(lnk_p) and safe_resolve_str(lnk_p) != safe_resolve_str(target_p)
                        and lnk_p not in desktop_shortcuts_to_clean):
                    desktop_shortcuts_to_clean.append(lnk_p)
            except Exception:
                pass
        if not selected_items:
            announce_speech(tr("no_items_selected"))
            return
        if desktop_shortcuts_to_clean:
            if not self._confirm_clean_desktop_shortcuts(desktop_shortcuts_to_clean):
                return
        try:
            data = load_widgets_data()
            if new_widget_name in data and (not self.is_editing or self.edit_widget_name != new_widget_name):
                report_warning(self, "widget_exists_err", title_key="error_title", name=new_widget_name)
                return
            names_data = load_item_names()
            if self.is_editing and self.edit_widget_name != new_widget_name:
                if self.edit_widget_name in data:
                    data[new_widget_name] = data.pop(self.edit_widget_name)
                delete_desktop_shortcut(self.edit_widget_name)
                if self.edit_widget_name in names_data:
                    names_data[new_widget_name] = names_data.pop(self.edit_widget_name)
            data[new_widget_name] = [str(p) for p in selected_items]
            save_widgets_data(data)
            # Drop display-name overrides for items no longer in the widget.
            kept_paths = {str(p) for p in selected_items}
            overrides = names_data.get(new_widget_name)
            if isinstance(overrides, dict):
                for p in list(overrides):
                    if p not in kept_paths:
                        del overrides[p]
                save_item_names(names_data)
            create_desktop_shortcut(new_widget_name)
            self.saved_widget_name = new_widget_name
            self.accept()
        except Exception as e:
            report_error(self, "save_error_msg", e, title_key="save_error_title")
class DesktopOrganizerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{tr('app_title')} {APP_VERSION}")
        self.resize(780, 500)
        self.init_ui()
        self.load_saved_widgets()
        self._update_worker = None
        self._exit_confirmed = False
        self._exit_confirm_open = False
        self.start_auto_update_check()
    def init_ui(self):
        central_widget = QWidget()
        main_layout = QVBoxLayout(central_widget)
        # Button shortcuts use '&' mnemonics so screen readers announce them.
        buttons_top_layout = QHBoxLayout()
        self.add_widget_btn = self._add_button(buttons_top_layout, "create_widget_btn", "create_widget_acc", QKeySequence("Alt+C"), self.open_creation_wizard)
        self.edit_widget_btn = self._add_button(buttons_top_layout, "edit_widget_btn", "edit_widget_acc", QKeySequence("Alt+E"), self.open_edit_wizard)
        self.delete_widget_btn = self._add_button(buttons_top_layout, "delete_widget_btn", "delete_widget_acc", QKeySequence(Qt.Key.Key_Delete), self.delete_selected_widget)
        self.shortcut_btn = self._add_button(buttons_top_layout, "create_shortcut_btn", "create_shortcut_acc", QKeySequence("Alt+K"), self.create_shortcut_for_selected_widget)
        self.settings_btn = self._add_button(buttons_top_layout, "settings_btn", "settings_acc", QKeySequence("Alt+S"), self.open_settings)
        self.exit_btn = self._add_button(buttons_top_layout, "exit_btn", "exit_acc", QKeySequence("Alt+Q"), self.exit_application)
        main_layout.addLayout(buttons_top_layout)
        self.widgets_list = QListWidget()
        self.widgets_list.itemActivated.connect(self.open_widget_view)
        self.widgets_list.itemChanged.connect(self.update_delete_button_label)
        self.widgets_list.currentRowChanged.connect(self.update_delete_button_label)
        # Context menu (right-click) on a widget: create its desktop shortcut.
        self.widgets_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.widgets_list.customContextMenuRequested.connect(self.show_widget_context_menu)
        self.widget_shortcut_action = QAction(tr("create_shortcut_btn"), self)
        self.widget_shortcut_action.triggered.connect(self.create_shortcut_for_selected_widget)
        self.widgets_menu = QMenu(self)
        self.widgets_menu.addAction(self.widget_shortcut_action)
        main_layout.addWidget(self.widgets_list)
        self.setCentralWidget(central_widget)
        self.statusBar().showMessage(tr("ready_status"))
        self.version_label = QLabel(f"v{APP_VERSION}")
        self.version_label.setObjectName("versionLabel")
        self.version_label.setAccessibleName(f"{tr('app_title')} {APP_VERSION}")
        self.statusBar().addPermanentWidget(self.version_label)
        announce_speech(tr("app_started_announce"))
    def _add_button(self, layout, text_key: str, acc_key: str, shortcut, slot) -> QPushButton:
        """Create a translated button with an accessible shortcut."""
        btn = QPushButton(tr(text_key))
        # autoDefault lets keyboard/screen reader users activate the button
        # with Enter while it has focus; with autoDefault disabled Enter is
        # ignored and nothing happens.
        btn.setAutoDefault(True)
        btn.setShortcut(shortcut)
        btn.setAccessibleName(tr(acc_key))
        btn.clicked.connect(slot)
        layout.addWidget(btn)
        return btn
    def start_auto_update_check(self):
        """Check for a newer release on startup when enabled in settings."""
        if not load_settings().get("check_updates", True):
            return
        worker = UpdateCheckWorker()
        worker.completed.connect(self.on_auto_update_check_done)
        self._update_worker = worker
        worker.start()
    def on_auto_update_check_done(self, latest, asset_url, error):
        self._update_worker = None
        try:
            if latest and is_newer_version(latest):
                prompt_update_dialog(self, latest, asset_url, respect_skip=True)
        except RuntimeError:
            pass  # the window was closed while the check was running
    def open_settings(self):
        dialog = SettingsDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            settings = load_settings()
            settings["language"] = dialog.selected_language
            settings["confirm_exit"] = dialog.confirm_exit
            settings["check_updates"] = dialog.check_updates
            settings["close_widget_on_launch"] = dialog.close_widget_on_launch
            save_settings(settings)
            self.retranslate_ui()
            announce_speech(tr("lang_changed"))
    def retranslate_ui(self):
        self.setWindowTitle(f"{tr('app_title')} {APP_VERSION}")
        for btn, text_key, acc_key in (
            (self.add_widget_btn, "create_widget_btn", "create_widget_acc"),
            (self.edit_widget_btn, "edit_widget_btn", "edit_widget_acc"),
            (self.shortcut_btn, "create_shortcut_btn", "create_shortcut_acc"),
            (self.settings_btn, "settings_btn", "settings_acc"),
            (self.exit_btn, "exit_btn", "exit_acc"),
        ):
            btn.setText(tr(text_key))
            btn.setAccessibleName(tr(acc_key))
        self.widget_shortcut_action.setText(tr("create_shortcut_btn"))
        self.update_delete_button_label()
        self.version_label.setAccessibleName(f"{tr('app_title')} {APP_VERSION}")
        self.statusBar().showMessage(tr("ready_status"))
    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.load_saved_widgets()
        super().changeEvent(event)
    def closeEvent(self, event):
        if self._confirm_exit_if_needed():
            event.accept()
        else:
            event.ignore()
    def _confirm_exit_if_needed(self):
        """Return True when the app may close. The confirmation dialog is
        shown at most once per run: a repeated close/exit request (for
        example a second click on the window's close button) must not open a
        second dialog, and once the user has confirmed, exit proceeds
        without asking again."""
        if self._exit_confirmed:
            return True
        if self._exit_confirm_open:
            return False  # a confirmation is already on screen; ignore repeats
        settings = load_settings()
        if not settings.get("confirm_exit", True):
            return True
        self._exit_confirm_open = True
        dialog = None
        accepted = False
        try:
            dialog = ExitConfirmDialog(parent=self)
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            self._exit_confirm_open = False
        if accepted and dialog is not None:
            self._exit_confirmed = True
            settings["confirm_exit"] = dialog.ask_on_exit_cb.isChecked()
            save_settings(settings)
        return accepted
    def exit_application(self):
        """Really quit the program."""
        if self._confirm_exit_if_needed():
            app = QApplication.instance()
            if app is not None:
                app.quit()
    def load_saved_widgets(self):
        current_selection = self.widgets_list.currentItem()
        selected_name = current_selection.data(Qt.ItemDataRole.UserRole) if current_selection else None
        self.widgets_list.clear()
        self.update_delete_button_label()
        data = load_widgets_data()
        if not data:
            return
        try:
            # Widget data is only removed by explicit user action
            # (delete_selected_widget). A missing desktop shortcut no longer
            # destroys the widget: the shortcut may be absent for unrelated
            # reasons (portable drive not attached, antivirus, manual move),
            # and silently wiping data is not recoverable.
            for widget_name in sorted(data, key=str.lower):
                self.add_widget_item_to_list(widget_name)
            if selected_name:
                for i in range(self.widgets_list.count()):
                    item = self.widgets_list.item(i)
                    if item.data(Qt.ItemDataRole.UserRole) == selected_name:
                        self.widgets_list.setCurrentItem(item)
                        break
        except Exception:
            pass
    def open_creation_wizard(self):
        try:
            wizard = WidgetCreationWizardDialog(parent=self)
            if wizard.exec() == QDialog.DialogCode.Accepted and hasattr(wizard, 'saved_widget_name'):
                name = wizard.saved_widget_name
                self.load_saved_widgets()
                if name:
                    announce_speech(tr("widget_created_announce", name=name))
        except Exception as e:
            report_error(self, "widget_edit_error", e)
    def open_edit_wizard(self):
        current_item = self.widgets_list.currentItem()
        if not current_item:
            announce_speech(tr("select_widget_first"))
            return
        widget_name = current_item.data(Qt.ItemDataRole.UserRole)
        if not widget_name:
            return
        try:
            wizard = WidgetCreationWizardDialog(edit_widget_name=widget_name, parent=self)
            if wizard.exec() == QDialog.DialogCode.Accepted and hasattr(wizard, 'saved_widget_name'):
                self.load_saved_widgets()
                announce_speech(tr("widget_updated_announce", name=wizard.saved_widget_name))
        except Exception as e:
            report_error(self, "widget_edit_error", e)
    def add_widget_item_to_list(self, widget_name):
        item = QListWidgetItem(widget_name)
        item.setData(Qt.ItemDataRole.UserRole, widget_name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Unchecked)
        self.widgets_list.addItem(item)
    def show_widget_context_menu(self, pos):
        """Right-click menu on a widget: create/refresh its desktop shortcut."""
        item = self.widgets_list.itemAt(pos)
        if item is None:
            return
        if item not in self.widgets_list.selectedItems():
            self.widgets_list.setCurrentItem(item)
        self.widgets_menu.exec(self.widgets_list.viewport().mapToGlobal(pos))
    def create_shortcut_for_selected_widget(self):
        """Create (or refresh) the desktop shortcut for the selected widget."""
        current_item = self.widgets_list.currentItem()
        if current_item is None:
            announce_speech(tr("select_widget_first"))
            return
        widget_name = current_item.data(Qt.ItemDataRole.UserRole)
        if not widget_name:
            announce_speech(tr("select_widget_first"))
            return
        if create_desktop_shortcut(widget_name):
            announce_speech(tr("shortcut_created_announce", name=widget_name))
        else:
            announce_speech(tr("shortcut_unavailable_announce"))
    def open_widget_view(self, item):
        widget_name = item.data(Qt.ItemDataRole.UserRole)
        if widget_name:
            try:
                view_dialog = WidgetViewDialog(widget_name, self)
                view_dialog.exec()
            except Exception as e:
                report_error(self, "widget_open_error", e)
    def update_delete_button_label(self):
        checked_count = 0
        for i in range(self.widgets_list.count()):
            item = self.widgets_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                checked_count += 1
        if checked_count == 0 and self.widgets_list.currentItem() is not None:
            checked_count = 1
        if checked_count > 1:
            self.delete_widget_btn.setText(tr("delete_widget_btn_multi"))
            self.delete_widget_btn.setAccessibleName(tr("delete_widget_btn_multi"))
        elif checked_count == 1:
            self.delete_widget_btn.setText(tr("delete_widget_btn_single"))
            self.delete_widget_btn.setAccessibleName(tr("delete_widget_btn_single"))
        else:
            self.delete_widget_btn.setText(tr("delete_widget_btn"))
            self.delete_widget_btn.setAccessibleName(tr("delete_widget_acc"))
        # setText() resets the shortcut; re-assert it.
        self.delete_widget_btn.setShortcut(QKeySequence(Qt.Key.Key_Delete))
    def _checked_widget_names(self):
        names = []
        for i in range(self.widgets_list.count()):
            item = self.widgets_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                name = item.data(Qt.ItemDataRole.UserRole)
                if name:
                    names.append(name)
        return names
    def delete_selected_widget(self):
        checked_widgets = self._checked_widget_names()
        if not checked_widgets:
            current_item = self.widgets_list.currentItem()
            name = current_item.data(Qt.ItemDataRole.UserRole) if current_item else None
            if name:
                checked_widgets = [name]
        if not checked_widgets:
            report_warning(self, "no_widgets_selected")
            return
        names_str = ", ".join(f"'{w}'" for w in checked_widgets)
        msg_text = (
            tr("delete_choice_multi", names=names_str)
            if len(checked_widgets) > 1
            else tr("delete_choice_single", name=checked_widgets[0])
        )
        msg_box = _message_box(self, tr("delete_confirm_title"), msg_text)
        full_btn = msg_box.addButton(tr("delete_widget_full_btn"), QMessageBox.ButtonRole.YesRole)
        shortcut_btn = msg_box.addButton(tr("delete_shortcut_only_btn"), QMessageBox.ButtonRole.NoRole)
        cancel_btn = msg_box.addButton(tr("cancel"), QMessageBox.ButtonRole.RejectRole)
        # The safer action (keep the widget, remove only the shortcut) is the
        # default, so Enter never destroys a widget by accident.
        msg_box.setDefaultButton(shortcut_btn)
        announce_speech(msg_text)
        msg_box.exec()
        clicked = msg_box.clickedButton()
        if clicked is None or clicked == cancel_btn:
            return
        if clicked == shortcut_btn:
            # Remove only the desktop shortcut(s); the widgets stay in the app.
            results = [delete_desktop_shortcut(w) for w in checked_widgets]
            if not any(results):
                announce_speech(tr("shortcut_unavailable_announce"))
                return
            announce_speech(tr("shortcut_deleted_announce", name=names_str))
            return
        # clicked == full_btn: remove the widgets completely (data + shortcut).
        try:
            data = load_widgets_data()
            for widget_name in checked_widgets:
                data.pop(widget_name, None)
            save_widgets_data(data)
        except Exception:
            pass
        try:
            names_data = load_item_names()
            changed = False
            for widget_name in checked_widgets:
                if widget_name in names_data:
                    del names_data[widget_name]
                    changed = True
            if changed:
                save_item_names(names_data)
        except Exception:
            pass
        for widget_name in checked_widgets:
            delete_desktop_shortcut(widget_name)
        self.load_saved_widgets()
        self.widgets_list.setFocus()
        announce_speech(tr("deleted_count_announce", count=len(checked_widgets)))
def main():
    app = QApplication(sys.argv)
    # Keep desktop widget shortcuts working across version updates: repoint
    # any shortcuts that still target an old, missing copy of the program.
    repair_orphaned_desktop_shortcuts()
    if len(sys.argv) > 1:
        widget_name = sys.argv[1]
        try:
            dialog = WidgetViewDialog(widget_name)
            dialog.exec()
        except Exception as e:
            report_error(None, "widget_open_error", e)
        return
    if not SETTINGS_FILE.exists():
        language_dialog = SettingsDialog(language_only=True)
        if language_dialog.exec() == QDialog.DialogCode.Accepted:
            settings = load_settings()
            settings["language"] = language_dialog.selected_language
            save_settings(settings)
        else:
            # The user cancelled the first-run language choice: close the
            # app instead of silently continuing with the default language.
            return
    window = DesktopOrganizerWindow()
    window.show()
    sys.exit(app.exec())
if __name__ == "__main__":
    main()
