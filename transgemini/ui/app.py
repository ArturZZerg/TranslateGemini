import argparse
import os
import sys
import traceback

from PyQt6.QtWidgets import QApplication, QMessageBox

from transgemini.config import DOCX_AVAILABLE, BS4_AVAILABLE, LXML_AVAILABLE, EBOOKLIB_AVAILABLE, PILLOW_AVAILABLE
from transgemini.core.translator import TranslatorApp


def run_app():
    def excepthook(exc_type, exc_value, exc_tb):
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        error_message = f"Неперехваченная ошибка:\n\n{exc_type.__name__}: {exc_value}\n\n{tb_str}"
        print(f"КРИТИЧЕСКАЯ ОШИБКА:\n{error_message}", file=sys.stderr)
        try:
            app_instance = QApplication.instance() or QApplication(sys.argv); QMessageBox.critical(None,
                                                                                                   "Критическая Ошибка",
                                                                                                   error_message)
        except Exception as mb_error:
            print(f"Не удалось показать MessageBox: {mb_error}", file=sys.stderr)
        sys.exit(1)


    sys.excepthook = excepthook
    try:
        main()
    except SystemExit:
        pass
    except Exception as e:
        error_message = f"Критическая ошибка запуска:\n{type(e).__name__}: {e}\n\n{traceback.format_exc()}"
        print(f"КРИТИЧЕСКАЯ ОШИБКА ЗАПУСКА:\n{error_message}", file=sys.stderr)
        try:
            app_instance = QApplication.instance()
            if not app_instance: app_instance = QApplication(sys.argv)
            QMessageBox.critical(None, "Ошибка Запуска", error_message)
        except Exception as mb_error:
            print(f"Не удалось показать MessageBox: {mb_error}", file=sys.stderr)
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Batch File Translator v2.12 (EPUB TOC Fixes)")
    parser.add_argument("--api_key", help="Google API Key (или GOOGLE_API_KEY env var).")
    args = parser.parse_args();
    api_key = args.api_key or os.environ.get("GOOGLE_API_KEY")
    app = QApplication.instance() or QApplication(sys.argv)
    missing_libs_msg = [];
    install_pkgs = []
    if not DOCX_AVAILABLE: missing_libs_msg.append("'python-docx' (для DOCX)"); install_pkgs.append("python-docx")
    if not BS4_AVAILABLE: missing_libs_msg.append("'beautifulsoup4' (для EPUB/HTML входа/выхода)"); install_pkgs.append(
        "beautifulsoup4")
    if not LXML_AVAILABLE: missing_libs_msg.append("'lxml' (для FB2/EPUB выхода/анализа)"); install_pkgs.append("lxml")
    if not EBOOKLIB_AVAILABLE: missing_libs_msg.append("'ebooklib' (для EPUB выхода)"); install_pkgs.append("ebooklib")
    if not PILLOW_AVAILABLE: missing_libs_msg.append("'Pillow' (для изобр.)"); install_pkgs.append("Pillow")
    if missing_libs_msg: lib_list = "\n - ".join(
        missing_libs_msg); install_cmd = f"pip install {' '.join(install_pkgs)}"; QMessageBox(QMessageBox.Icon.Warning,
                                                                                              "Отсутствуют библиотеки",
                                                                                              f"Не найдены библиотеки:\n\n - {lib_list}\n\nФункциональность ограничена.\n\nУстановить:\n{install_cmd}",
                                                                                              QMessageBox.StandardButton.Ok).exec()
    try:
        win = TranslatorApp(api_key=api_key);
        win.show()
        if not api_key: win.append_log("[WARN] API ключ не предоставлен.")
    except Exception as e:
        error_message = f"Критическая ошибка GUI:\n{type(e).__name__}: {e}\n\n{traceback.format_exc()}"; print(
            error_message, file=sys.stderr); QMessageBox.critical(None, "Ошибка Запуска GUI", error_message); sys.exit(
            1)
    sys.exit(app.exec())