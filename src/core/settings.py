"""Settings persistence (settings.ini).

Stored at: %LOCALAPPDATA%/Digital Book Reader/settings.ini
"""
from configparser import ConfigParser
from pathlib import Path

from .app_paths import data_dir

BASE_DIR = data_dir()
SETTINGS_FILE = BASE_DIR / "settings.ini"


class Settings:
    """Key-value settings with typed defaults."""

    _defaults = {
        # Library
        "library_path": str(Path.home() / "Documents" / "Ebooks"),
        "library_view_mode": "1",          # 0=card, 1=table
        "last_style_id": "NightStyle",     # DayStyle | NightStyle | SepiaStyle
        # System
        "language": "en",                  # en | es (affects TTS voice auto-pick)
        "start_fullscreen": "false",
        "check_updates": "true",
        "register_default_app": "true",
        # Reading / viewer.text
        "font_family": "Segoe UI",
        "font_size": "16",
        "text_align": "left",
        "line_height": "1.5",
        "column_count": "2",
        "zoom": "100",
        "page_padding": "32",
        # TTS
        "tts_volume": "1",
        "tts_rate": "1",
        "tts_pitch": "1",
        "tts_voice": "",                   # empty = auto-pick by language
        "tts_highlight": "true",           # highlight spoken word (.speechActive)
        # Hotkeys (Qt key sequence strings; "None" disables)
        "hotkey_fullscreen": "F11",
        "hotkey_theme": "Alt+T",
        "hotkey_bookmark": "Ctrl+D",
        "hotkey_sidebar": "Ctrl+B",
    }

    def __init__(self, path: Path = SETTINGS_FILE) -> None:
        self.path = path
        self._cfg = ConfigParser()
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as fh:
                self._cfg.read_file(fh)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            self._cfg.write(fh)

    # -- accessors ----------------------------------------------------------
    def get(self, key: str, default: str | None = None) -> str:
        if key in self._defaults:
            return self._cfg.get("app", key, fallback=self._defaults[key])
        return self._cfg.get("app", key, fallback=default or "")

    def set(self, key: str, value: str) -> None:
        if "app" not in self._cfg.sections():
            self._cfg.add_section("app")
        self._cfg["app"][key] = str(value)

    def get_bool(self, key: str) -> bool:
        return self.get(key).strip().lower() in ("1", "true", "yes", "on")

    @property
    def library_path(self) -> Path:
        return Path(self.get("library_path"))
