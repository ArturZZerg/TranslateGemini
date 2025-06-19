import importlib
import subprocess
import sys

MODELS = {

    "Gemini 2.5 Flash Preview 05-20": {  # From user list / original code
        "id": "models/gemini-2.5-flash-preview-05-20",
        "rpm": 10,  # Moderate RPM
        "needs_chunking": True,  # Assume requires chunking
        "post_request_delay": 60  # Delay for Flash models
    },

    "Gemini 2.5 Flash-Lite Preview": {  # From user list / original code
        "id": "models/gemini-2.5-flash-lite-preview-06-17",
        "rpm": 15,  # Moderate RPM
        "needs_chunking": True,  # Assume requires chunking
        "post_request_delay": 60  # Delay for Flash models
    },

    "Gemini 2.5 Pro Experimental 03-25": {  # From user list / original code
        "id": "models/gemini-2.5-pro-preview-03-25",
        "rpm": 10,  # Moderate RPM
        "needs_chunking": True,  # Assume requires chunking
        "post_request_delay": 60  # Delay for Flash models
    },

    "Gemini 2.0 Flash": {  # From user list / original code
        "id": "models/gemini-2.0-flash",
        "rpm": 15,  # Higher RPM for Flash
        "needs_chunking": True,  # Requires chunking for large inputs
        "post_request_delay": 60  # Delay for Flash models
    },
    "Gemini 2.0 Flash Experimental": {  # From user list / original code
        "id": "models/gemini-2.0-flash-exp",
        "rpm": 10,  # Higher RPM for Flash
        "needs_chunking": True,  # Requires chunking for large inputs
        "post_request_delay": 60  # Delay for Flash models
    },
    "Gemini 2.0 Flash-Lite": {  # From user list
        "id": "models/gemini-2.0-flash-lite",
        "rpm": 20,  # Guess: Higher than standard Flash
        "needs_chunking": True,  # Assume needs chunking like other Flash
        "post_request_delay": 60  # Assume needs delay like other Flash
    },
    "Gemini 2.0 Flash Live": {  # From user list
        "id": "models/gemini-2.0-flash-live-001",
        "rpm": 15,  # Guess: Similar to standard Flash
        "needs_chunking": True,  # Assume needs chunking
        "post_request_delay": 60  # Assume needs delay
    },

    "Gemini 1.5 Flash": {  # From user list (using recommended 'latest' tag)
        "id": "models/gemini-1.5-flash-latest",
        "rpm": 20,  # Guess: Higher RPM for Flash models
        "needs_chunking": True,  # Assume needs chunking
        "post_request_delay": 60  # Assume needs delay
    },

}

DEFAULT_MODEL_NAME = "Gemini 2.5 Flash Preview" if "Gemini 2.0 Flash" in MODELS else list(MODELS.keys())[0]

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 25
API_TIMEOUT_SECONDS = 600  # 10 минут

DEFAULT_CHARACTER_LIMIT_FOR_CHUNK = 900_000  # Default limit (can be adjusted in GUI)
DEFAULT_CHUNK_SEARCH_WINDOW = 500  # Default window (can be adjusted in GUI)
MIN_CHUNK_SIZE = 500  # Minimum size to avoid tiny chunks
CHUNK_HTML_SOURCE = True  # Keep False: HTML chunking with embedded images is complex and disabled by default

SETTINGS_FILE = 'translator_settings.ini'

OUTPUT_FORMATS = {
    "Текстовый файл (.txt)": "txt",
    "Документ Word (.docx)": "docx",
    "Markdown (.md)": "md",
    "EPUB (.epub)": "epub",  # Triggers EPUB rebuild logic if input is also EPUB
    "FictionBook2 (.fb2)": "fb2",
    "HTML (.html)": "html",
}
DEFAULT_OUTPUT_FORMAT_DISPLAY = "Текстовый файл (.txt)"  # Default display name for format dropdown

IMAGE_PLACEHOLDER_PREFIX = "img_placeholder_"

TRANSLATED_SUFFIX = "_translated"

def ensure_package(package_name, import_name=None, extras=None):
    """Проверяет наличие пакета и устанавливает его при необходимости."""
    import_name = import_name or package_name
    if importlib.util.find_spec(import_name) is None:
        print(f"Пакет '{package_name}' не найден. Устанавливаю...")
        try:
            install_target = package_name + extras if extras else package_name
            subprocess.check_call([sys.executable, "-m", "pip", "install", install_target])
        except Exception as e:
            print(f"Не удалось установить пакет '{package_name}': {e}")
            return False
    return True

ensure_package("bs4", "bs4")
ensure_package("PySocks", "socks")  # For SOCKS proxy support
ensure_package("PyQt6", "PyQt6")
ensure_package("google-generativeai", "google")
ensure_package("python-docx", "docx")
ensure_package("lxml", "lxml")
ensure_package("ebooklib", "ebooklib")
ensure_package("Pillow", "PIL")

DOCX_AVAILABLE = importlib.util.find_spec("docx") is not None
LXML_AVAILABLE = importlib.util.find_spec("lxml") is not None
EBOOKLIB_AVAILABLE = importlib.util.find_spec("ebooklib") is not None
PILLOW_AVAILABLE = importlib.util.find_spec("PIL") is not None
BS4_AVAILABLE = importlib.util.find_spec("bs4") is not None