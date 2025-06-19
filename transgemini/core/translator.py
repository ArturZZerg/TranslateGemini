import configparser
import os
import time
import traceback
import zipfile
from pathlib import Path

from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QListWidget, QPushButton,
    QDialogButtonBox, QLabel, QWidget, QLineEdit, QComboBox, QSpinBox,
    QCheckBox, QPlainTextEdit, QDoubleSpinBox, QProgressBar, QTextEdit,
    QGridLayout, QGroupBox, QHBoxLayout, QMessageBox, QFileDialog, QScrollArea
)
from PyQt6.QtCore import QStandardPaths, Qt
from google.api_core import exceptions as google_exceptions
from google import generativeai as genai
from lxml import etree

from transgemini.config import *
from transgemini.core.EpubHtmlSelectorDialog import EpubHtmlSelectorDialog
from transgemini.core.Worker import Worker


class TranslatorApp(QWidget):

    def finish_translation_gently(self):
        if self.worker_ref and self.thread_ref and self.thread_ref.isRunning():
            self.append_log("Отправка сигнала ЗАВЕРШЕНИЯ (сохранить текущее)...")
            self.status_label.setText("Завершение...")
            if hasattr(self.worker_ref, 'finish_processing'):  # Проверка на случай, если ссылка устарела
                self.worker_ref.finish_processing()
            self.finish_btn.setEnabled(False)  # Отключить кнопку "Завершить"
            # Кнопка "Отмена" остается активной для возможности жесткой остановки
            self.append_log("Ожидание завершения текущих задач и сохранения...")
        else:
            self.append_log("[WARN] Нет активного процесса для завершения.")

    def __init__(self, api_key):
        super().__init__()
        self.api_key = api_key
        self.out_folder = ""
        self.selected_files_data_tuples = []
        self.worker = None;
        self.thread = None;
        self.worker_ref = None;
        self.thread_ref = None
        self.config = configparser.ConfigParser()

        self.file_selection_group_box = None  # Инициализируем здесь, чтобы PyCharm не ругался
        self.init_ui()
        self.load_settings()

    def update_file_count_display(self):
        """Обновляет заголовок группы выбора файлов, показывая количество выбранных файлов."""
        count = len(self.selected_files_data_tuples)
        self.file_selection_group_box.setTitle(f"1. Исходные файлы (Выбрано: {count})")

    def init_ui(self):

        pillow_status = "Pillow OK" if PILLOW_AVAILABLE else "Pillow Missing!"
        lxml_status = "lxml OK" if LXML_AVAILABLE else "lxml Missing!"
        bs4_status = "BS4 OK" if BS4_AVAILABLE else "BS4 Missing!"
        ebooklib_status = "EbookLib OK" if EBOOKLIB_AVAILABLE else "EbookLib Missing!"
        docx_status = "Docx OK" if DOCX_AVAILABLE else "Docx Missing!"
        self.setWindowTitle(
            f"Batch File Translator v2.16 ({pillow_status}, {lxml_status}, {bs4_status}, {ebooklib_status}, {docx_status})")

        self.setGeometry(100, 100, 950, 950)  # Уменьшил высоту по умолчанию, т.к. будет скролл

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)  # Убираем лишние отступы основного layout

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)  # !!! ВАЖНО: Позволяет содержимому растягиваться по ширине
        scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)  # Показывать верт. скроллбар по необходимости
        scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # Гориз. скроллбар обычно не нужен

        container_widget = QWidget()
        container_layout = QVBoxLayout(container_widget)

        self.file_selection_group_box = QGroupBox("1. Исходные файлы (Выбрано: 0)")  # <<< ЭТУ ДОБАВЬ (ты уже сделал)
        file_box = self.file_selection_group_box  # <<< И ЭТУ ДОБАВЬ (ты уже сделал)
        file_layout = QVBoxLayout(file_box)  # <<< Вот здесь file_box должен быть self.file_selection_group_box
        file_btn_layout = QHBoxLayout()
        self.file_select_btn = QPushButton("Выбрать файлы (TXT, DOCX, EPUB)")
        self.file_select_btn.setToolTip(
            "Выберите файлы TXT, DOCX или EPUB.\nПри выборе EPUB -> EPUB будет предпринята попытка пересборки книги\nс ИЗМЕНЕНИЕМ существующего файла оглавления (NAV/NCX) и переименованием файлов (_translated).")
        self.file_select_btn.clicked.connect(self.select_files)
        self.clear_list_btn = QPushButton("Очистить список");
        self.clear_list_btn.clicked.connect(self.clear_file_list)
        file_btn_layout.addWidget(self.file_select_btn);
        file_btn_layout.addWidget(self.clear_list_btn)
        self.file_list_widget = QListWidget();
        self.file_list_widget.setToolTip("Список файлов/частей для перевода.");
        self.file_list_widget.setFixedHeight(150)  # Можно убрать FixedHeight, если хотите, чтобы он растягивался
        file_layout.addLayout(file_btn_layout);
        file_layout.addWidget(self.file_list_widget)

        container_layout.addWidget(file_box)

        out_box = QGroupBox("2. Папка для перевода");
        out_layout = QHBoxLayout(out_box)
        self.out_btn = QPushButton("Выбрать папку");
        self.out_lbl = QLineEdit("<не выбрано>");
        self.out_lbl.setReadOnly(True);
        self.out_lbl.setCursorPosition(0)
        self.out_btn.clicked.connect(self.select_output_folder)
        out_layout.addWidget(self.out_btn);
        out_layout.addWidget(self.out_lbl, 1);

        container_layout.addWidget(out_box)

        format_box = QGroupBox("3. Формат сохранения")
        format_layout = QHBoxLayout(format_box)
        format_layout.addWidget(QLabel("Формат:"))
        self.format_combo = QComboBox();
        self.format_combo.setToolTip("Выберите формат для сохранения.\n(EPUB/FB2/DOCX требуют доп. библиотек)")
        self.format_indices = {}
        for i, (display_text, format_code) in enumerate(OUTPUT_FORMATS.items()):
            self.format_combo.addItem(display_text);
            self.format_indices[format_code] = i
            is_enabled = True;
            tooltip = f"Сохранить как .{format_code}"
            if format_code == 'docx' and not DOCX_AVAILABLE:
                is_enabled = False; tooltip = "Требуется: python-docx"
            elif format_code == 'epub' and (not EBOOKLIB_AVAILABLE or not LXML_AVAILABLE or not BS4_AVAILABLE):
                is_enabled = False; tooltip = "Требуется: ebooklib, lxml, beautifulsoup4"
            elif format_code == 'fb2' and not LXML_AVAILABLE:
                is_enabled = False; tooltip = "Требуется: lxml"

            if format_code in ['docx', 'epub', 'fb2', 'html'] and not PILLOW_AVAILABLE:
                if is_enabled:
                    tooltip += "\n(Реком.: Pillow для изобр.)"
                else:
                    tooltip += "; Pillow (реком.)"

            item = self.format_combo.model().item(i)
            if item: item.setEnabled(is_enabled); self.format_combo.setItemData(i, tooltip, Qt.ItemDataRole.ToolTipRole)
        format_layout.addWidget(self.format_combo, 1);

        container_layout.addWidget(format_box)
        self.format_combo.currentIndexChanged.connect(self.on_output_format_changed)  # Keep connection

        # --- НАЧАЛО БЛОКА ПРОКСИ ---
        proxy_box = QGroupBox("4. Настройки Прокси")  # Обновляем нумерацию до 4
        proxy_layout = QHBoxLayout(proxy_box)
        proxy_layout.addWidget(
            QLabel("URL Прокси (например, http(s)://user:pass@host:port или socks5(h)://host:port):"))
        self.proxy_url_edit = QLineEdit()
        self.proxy_url_edit.setPlaceholderText("Оставьте пустым, если прокси не нужен")
        self.proxy_url_edit.setToolTip(
            "Введите полный URL вашего прокси-сервера.\n"
            "Поддерживаются HTTP, HTTPS, SOCKS4(a), SOCKS5(h).\n"
            "Примеры:\n"
            "  HTTP: http://127.0.0.1:8080\n"
            "  HTTPS с авторизацией: https://user:password@proxy.example.com:443\n"
            "  SOCKS5: socks5://127.0.0.1:1080 (требует PySocks и requests>=2.10)\n"
            "  SOCKS5 с DNS через прокси: socks5h://127.0.0.1:1080"
        )
        proxy_layout.addWidget(self.proxy_url_edit, 1)
        container_layout.addWidget(proxy_box)
        # --- КОНЕЦ БЛОКА ПРОКСИ ---

        settings_prompt_box = QGroupBox("5. Настройки API, Чанкинга и Промпт");
        settings_prompt_layout = QVBoxLayout(settings_prompt_box)
        # Обновляем нумерацию последующих групп
        api_settings_layout = QGridLayout();
        self.model_combo = QComboBox();
        self.model_combo.addItems(MODELS.keys())
        try:
            self.model_combo.setCurrentText(DEFAULT_MODEL_NAME)
        except Exception:
            self.model_combo.setCurrentIndex(0)  # Fallback if default isn't present
        self.model_combo.setToolTip("Выберите модель Gemini.");
        self.concurrency_spin = QSpinBox();
        self.concurrency_spin.setRange(1, 60);
        self.concurrency_spin.setToolTip("Макс. одновременных запросов к API.")
        self.model_combo.currentTextChanged.connect(self.update_concurrency_suggestion);
        self.check_api_key_btn = QPushButton("Проверить API ключ");
        self.check_api_key_btn.setToolTip("Выполнить тестовый запрос к API.");
        self.check_api_key_btn.clicked.connect(self.check_api_key)
        api_settings_layout.addWidget(QLabel("Модель API:"), 0, 0);
        api_settings_layout.addWidget(self.model_combo, 0, 1);
        api_settings_layout.addWidget(QLabel("Паралл. запросы:"), 1, 0);
        api_settings_layout.addWidget(self.concurrency_spin, 1, 1);
        api_settings_layout.addWidget(self.check_api_key_btn, 0, 2, 2, 1, alignment=Qt.AlignmentFlag.AlignCenter);
        api_settings_layout.setColumnStretch(1, 1);
        settings_prompt_layout.addLayout(api_settings_layout)
        api_settings_layout.addWidget(QLabel("Температура:"), 2, 0)
        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0.0, 2.0)  # Диапазон 0.0 - 2.0
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setValue(1.0)  # <--- Устанавливаем значение по умолчанию 1.0
        self.temperature_spin.setDecimals(1)
        self.temperature_spin.setToolTip(
            "Контроль креативности модели.\n0.0 = максимально детерминировано,\n1.0 = стандартно,\n>1.0 = более случайно/креативно.")
        api_settings_layout.addWidget(self.temperature_spin, 2, 1)
        api_settings_layout.addWidget(self.check_api_key_btn, 0, 2, 3, 1,
                                      alignment=Qt.AlignmentFlag.AlignCenter)  # Span 3 rows now

        chunking_group = QGroupBox("Настройки Чанкинга");
        chunking_layout = QGridLayout(chunking_group);
        self.chunking_checkbox = QCheckBox("Включить Чанкинг")
        chunking_tooltip = f"Разделять файлы > лимита символов.\n(ВНИМАНИЕ: Чанкинг HTML/EPUB отключен из-за сложности обработки изображений и структуры).";
        self.chunking_checkbox.setToolTip(chunking_tooltip)  # Updated tooltip
        self.chunk_limit_spin = QSpinBox();
        self.chunk_limit_spin.setRange(5000, 5000000);
        self.chunk_limit_spin.setSingleStep(10000);
        self.chunk_limit_spin.setValue(DEFAULT_CHARACTER_LIMIT_FOR_CHUNK);
        self.chunk_limit_spin.setToolTip("Макс. размер чанка в символах.")
        self.chunk_window_spin = QSpinBox();
        self.chunk_window_spin.setRange(100, 20000);
        self.chunk_window_spin.setSingleStep(100);
        self.chunk_window_spin.setValue(DEFAULT_CHUNK_SEARCH_WINDOW);
        self.chunk_window_spin.setToolTip("Окно поиска разделителя.")
        self.chunk_delay_spin = QDoubleSpinBox()
        self.chunk_delay_spin.setRange(0.0, 300.0)  # От 0 до 5 минут
        self.chunk_delay_spin.setSingleStep(0.1)
        self.chunk_delay_spin.setValue(0.0)  # По умолчанию без задержки
        self.chunk_delay_spin.setDecimals(1)
        self.chunk_delay_spin.setToolTip("Задержка в секундах между отправкой чанков.\n0.0 = без задержки.")
        self.chunking_checkbox.stateChanged.connect(self.toggle_chunking_details);
        chunking_layout.addWidget(self.chunking_checkbox, 0, 0, 1, 4);
        chunking_layout.addWidget(QLabel("Лимит символов:"), 1, 0);
        chunking_layout.addWidget(self.chunk_limit_spin, 1, 1);
        chunking_layout.addWidget(QLabel("Окно поиска:"), 1, 2);
        chunking_layout.addWidget(self.chunk_window_spin, 1, 3);
        chunking_layout.addWidget(QLabel("Задержка (сек):"), 2, 0);
        chunking_layout.addWidget(self.chunk_delay_spin, 2, 1)
        self.chunk_limit_spin.setEnabled(self.chunking_checkbox.isChecked());
        self.chunk_window_spin.setEnabled(self.chunking_checkbox.isChecked());
        settings_prompt_layout.addWidget(chunking_group);
        self.chunk_delay_spin.setEnabled(self.chunking_checkbox.isChecked())
        self.model_combo.currentTextChanged.connect(self.update_chunking_checkbox_suggestion)

        self.prompt_lbl = QLabel("Промпт (инструкция для API, `{text}` будет заменен):");
        self.prompt_edit = QPlainTextEdit();
        self.prompt_edit.setPlaceholderText("Загрузка промпта...")
        self.prompt_edit.setMinimumHeight(100)

        self.prompt_edit.setPlainText("""--- PROMPT START ---

**Твоя Роль:** Переводчик и редактор, адаптирующий тексты (литература, статьи, DOCX, HTML) с разных языков на русский. Учитывай культурные особенности (Япония, Китай, Корея, США), речевые обороты, форматирование текста и HTML.

**Твоя Задача:** Адаптируй текст `{text}` на русский, сохраняя смысл, стиль, исходное форматирование и плейсхолдеры изображений `<||img_placeholder_...||>`.

**II. ПРИНЦИПЫ АДАПТАЦИИ**

1.  **Естественный русский:** Избегай буквальности, ищи русские эквиваленты.
2.  **Смысл и тон:** Точно передавай смысл, атмосферу, авторский стиль.
3.  **Культурная адаптация:**
    *   **Хонорифики (-сан, -кун):** Опускай или заменяй естественными обращениями (по имени, господин/госпожа). Транслитерация – крайне редко.
    *   **Реалии:** Адаптируй (русский эквивалент, краткое пояснение в тексте). Без сносок.
    *   **Ономатопея:** Заменяй русскими звукоподражаниями или описаниями.

**III. ФОРМАТИРОВАНИЕ И СПЕЦТЕГИ**

1.  **Простой Текст / Markdown:**
    *   **Абзацы:** Сохраняй; если нет – расставляй по правилам русского языка.
    *   **Заголовки (Markdown `#`, `##`):** Сохраняй разметку.
    *   **Списки (`*`, `-`, `1.`):** Переводи текст элемента, сохраняй маркеры.
    *   **Оглавления:** Формат: **Глава X: Название главы** ... текст ... (Конец главы).

2.  **HTML Контент:**
    *   **ВАЖНО: СОХРАНЯЙ ВСЕ HTML-ТЕГИ!** Переводи **ТОЛЬКО видимый текст** (внутри `<p>`, `<h1>`, `<li>`, `<td>`, `<span>`, `<a>`, значения атрибутов `title`, `alt` и т.д.).
    *   **НЕ МЕНЯЙ, НЕ УДАЛЯЙ, НЕ ДОБАВЛЯЙ** HTML-теги, атрибуты или структуру (исключение: плейсхолдеры изображений).
    *   HTML-комментарии (`<!-- ... -->`), `<script>`, `<style>` – **БЕЗ ИЗМЕНЕНИЙ.**

3.  **<|| ПЛЕЙСХОЛДЕРЫ ИЗОБРАЖЕНИЙ ||>**
    *   Теги вида `<||img_placeholder_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx||>` (где `x` - 32 шестнадцатеричных символа).
    *   **КРИТИЧЕСКИ ВАЖНО: КОПИРУЙ ЭТИ ТЕГИ АБСОЛЮТНО ТОЧНО, СИМВОЛ В СИМВОЛ. НЕ МЕНЯЙ, НЕ УДАЛЯЙ, НЕ ДОБАВЛЯЙ ПРОБЕЛОВ ВНУТРИ, НЕ ПЕРЕВОДИ. ОНИ ДОЛЖНЫ ОСТАТЬСЯ НА СВОИХ МЕСТАХ.**

4.  **Стилизация и Пунктуация (для ВСЕХ типов контента):**
    *   Реплики `[]` -> `— Реплика`.
    *   Японские кавычки `『』` -> русские «елочки» (`«Цитата»`).
    *   Мысли персонажей в скобках -> `«Мысль...»` (без тире перед кавычками).
    *   Названия навыков, предметов, квестов -> `[Название]`.
    *   Длинные повторы символов -> 4-5 (напр., `А-а-а-а...`). Используй дефис: `П-привет`, `А-а-ах!`.
    *   Фразы с `...!` или `...?` -> знак препинания *перед* многоточием (`Текст!..`, `Текст?..`).
    *   Избегай множественных знаков препинания в конце фраз -> `А?`, `А!`, `А?!`.

**V. ГЛОССАРИЙ**

*   Если предоставлен – **строго придерживайся**.

**VI. ИТОГОВЫЙ РЕЗУЛЬТАТ**

*   **ТОЛЬКО** переведенный и адаптированный текст/HTML, **СОХРАНЯЯ ПЛЕЙСХОЛДЕРЫ `<||img_placeholder_...||>` БЕЗ ИЗМЕНЕНИЙ.**
*   **БЕЗ** вводных фраз («Вот перевод:»).
*   **БЕЗ** оригинального текста.
*   **БЕЗ** твоих комментариев (кроме неизмененных HTML-комментариев).

--- PROMPT END ---
    """)
        settings_prompt_layout.addWidget(self.prompt_lbl);
        settings_prompt_layout.addWidget(self.prompt_edit, 1);

        container_layout.addWidget(settings_prompt_box, 1)  # Увеличиваем растяжение для промпта

        controls_box = QGroupBox("6. Управление и Прогресс");
        controls_main_layout = QVBoxLayout(controls_box);
        hbox_controls = QHBoxLayout()
        self.start_btn = QPushButton("🚀 Начать перевод");
        self.start_btn.setStyleSheet("background-color: #ccffcc; font-weight: bold;");
        self.start_btn.clicked.connect(self.start_translation)
        self.finish_btn = QPushButton("🏁 Завершить")  # <--- НОВАЯ КНОПКА
        self.finish_btn.setToolTip(
            "Завершить текущий файл (сохранить переведенные чанки) и остановить остальные задачи.")
        self.finish_btn.setEnabled(False)
        self.finish_btn.setStyleSheet("background-color: #e6ffe6;")  # Светло-зеленый
        self.finish_btn.clicked.connect(self.finish_translation_gently)  # <--- НОВЫЙ ОБРАБОТЧИК
        self.cancel_btn = QPushButton("❌ Отмена");
        self.cancel_btn.setEnabled(False);
        self.cancel_btn.setStyleSheet("background-color: #ffcccc;");
        self.cancel_btn.clicked.connect(self.cancel_translation)
        hbox_controls.addWidget(self.start_btn, 1);
        hbox_controls.addWidget(self.finish_btn)
        hbox_controls.addWidget(self.cancel_btn);
        controls_main_layout.addLayout(hbox_controls)
        self.progress_bar = QProgressBar();
        self.progress_bar.setRange(0, 100);
        self.progress_bar.setValue(0);
        self.progress_bar.setTextVisible(True);
        self.progress_bar.setFormat("%v / %m задач (%p%)")
        controls_main_layout.addWidget(self.progress_bar);
        self.status_label = QLabel("Готов");
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        controls_main_layout.addWidget(self.status_label);

        container_layout.addWidget(controls_box)

        self.log_lbl = QLabel("Лог выполнения:");
        self.log_output = QTextEdit();
        self.log_output.setReadOnly(True);
        self.log_output.setFont(QtGui.QFont("Consolas", 9));
        self.log_output.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.log_output.setMinimumHeight(150)  # Зададим минимальную высоту логу

        container_layout.addWidget(self.log_lbl);
        container_layout.addWidget(self.log_output, 2)  # Увеличиваем растяжение для лога

        scroll_area.setWidget(container_widget)

        main_layout.addWidget(scroll_area)

        self.update_concurrency_suggestion(self.model_combo.currentText())
        self.update_chunking_checkbox_suggestion(self.model_combo.currentText())
        self.toggle_chunking_details(self.chunking_checkbox.checkState().value)

    @QtCore.pyqtSlot(int)
    def toggle_chunking_details(self, state):
        enabled = (state == Qt.CheckState.Checked.value)
        self.chunk_limit_spin.setEnabled(enabled)
        self.chunk_window_spin.setEnabled(enabled)

        self.chunk_delay_spin.setEnabled(enabled)

    @QtCore.pyqtSlot(str)
    def update_concurrency_suggestion(self, model_display_name):

        if model_display_name in MODELS:
            model_rpm = MODELS[model_display_name].get('rpm', 1)

            practical_limit = max(1, min(model_rpm, 15))  # Capped suggestion at 15

            self.concurrency_spin.setValue(min(practical_limit,
                                               self.concurrency_spin.maximum()))
            self.concurrency_spin.setToolTip(
                f"Макс. запросов.\nМодель: {model_display_name}\nЗаявлено RPM: {model_rpm}\nРеком.: ~{practical_limit}")
        else:
            self.concurrency_spin.setValue(1)  # Fallback for unknown models
            self.concurrency_spin.setToolTip("Макс. запросов.")

    @QtCore.pyqtSlot(str)
    def update_chunking_checkbox_suggestion(self, model_display_name):

        needs_chunking = False
        tooltip_text = f"Разделять файлы > лимита."
        if model_display_name in MODELS:
            needs_chunking = MODELS[model_display_name].get('needs_chunking', False)
            tooltip_text += "\nРЕКОМЕНДУЕТСЯ ВКЛ." if needs_chunking else "\nМОЖНО ВЫКЛ."
        else:  # Assume unknown models might need it
            needs_chunking = True
            tooltip_text += "\nНеизвестная модель, реком. ВКЛ."

        if not CHUNK_HTML_SOURCE:
            tooltip_text += "\n(ВНИМАНИЕ: Чанкинг HTML/EPUB отключен)."

        self.chunking_checkbox.setChecked(needs_chunking)
        self.chunking_checkbox.setToolTip(tooltip_text)

        self.toggle_chunking_details(self.chunking_checkbox.checkState().value)

    @QtCore.pyqtSlot(int)
    def on_output_format_changed(self, index):
        """ Warns user if EPUB output is selected with non-EPUB inputs """

        selected_format_display = self.format_combo.itemText(index)
        current_output_format = OUTPUT_FORMATS.get(selected_format_display, 'txt')

        if not self.selected_files_data_tuples: return  # No files selected yet

        if current_output_format == 'epub':

            if any(ft != 'epub' for ft, _, _ in self.selected_files_data_tuples):
                QMessageBox.warning(self, "Несовместимые файлы",
                                    "Для вывода в формат EPUB выбраны не только EPUB файлы.\n"
                                    "Этот режим (EPUB->EPUB) требует ТОЛЬКО EPUB файлов в списке.\n\n"
                                    "Пожалуйста, очистите список и выберите только EPUB файлы, "
                                    "либо выберите другой формат вывода.")

                first_enabled_idx = 0
                for i in range(self.format_combo.count()):
                    if self.format_combo.model().item(i).isEnabled():
                        first_enabled_idx = i;
                        break
                self.format_combo.setCurrentIndex(first_enabled_idx)

    def select_files(self):
        """Selects source files, handles EPUB HTML selection and TOC identification."""

        last_dir = self.out_folder or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        files, _ = QFileDialog.getOpenFileNames(self, "Выберите файлы TXT, DOCX или EPUB", last_dir,
                                                "Поддерживаемые файлы (*.txt *.docx *.epub);;All files (*)")
        if not files: return

        selected_format_display = self.format_combo.currentText()
        current_output_format = OUTPUT_FORMATS.get(selected_format_display, 'txt')
        is_potential_epub_rebuild_mode = (current_output_format == 'epub')

        new_files_data_tuples = [];
        added_count = 0;
        skipped_count = 0

        current_files_set = {(p1, p2) for _, p1, p2 in self.selected_files_data_tuples}

        for file_path in files:
            file_ext = os.path.splitext(file_path)[1].lower()
            base_name = os.path.basename(file_path)

            if is_potential_epub_rebuild_mode:
                if file_ext != '.epub':
                    self.append_log(
                        f"[WARN] Пропуск {file_ext.upper()}: {base_name} (нельзя смешивать с EPUB при выводе в EPUB)")
                    skipped_count += 1
                    continue

                elif any(ft != 'epub' for ft, _, _ in self.selected_files_data_tuples):
                    self.append_log(
                        f"[WARN] Пропуск EPUB: {base_name} (список уже содержит не-EPUB файлы, нельзя выбрать EPUB формат)")
                    skipped_count += 1
                    continue

            else:  # Not EPUB output mode
                if file_ext == '.epub' and any(ft != 'epub' for ft, _, _ in self.selected_files_data_tuples):
                    self.append_log(
                        f"[WARN] Пропуск EPUB: {base_name} (нельзя смешивать EPUB с TXT/DOCX для этого формата вывода)")
                    skipped_count += 1
                    continue
                if file_ext != '.epub' and any(ft == 'epub' for ft, _, _ in self.selected_files_data_tuples):
                    self.append_log(
                        f"[WARN] Пропуск {file_ext.upper()}: {base_name} (список уже содержит EPUB, нельзя выбрать не-EPUB формат для них)")
                    skipped_count += 1
                    continue

            if file_ext == '.txt':
                file_tuple_key = (file_path, None)
                if file_tuple_key not in current_files_set:
                    new_files_data_tuples.append(('txt', file_path, None))
                    current_files_set.add(file_tuple_key);
                    added_count += 1
                else:
                    skipped_count += 1  # Already in list
            elif file_ext == '.docx':
                if not DOCX_AVAILABLE:
                    self.append_log(f"[WARN] Пропуск DOCX: {base_name} (библиотека 'python-docx' не найдена)");
                    skipped_count += 1;
                    continue
                file_tuple_key = (file_path, None)
                if file_tuple_key not in current_files_set:
                    new_files_data_tuples.append(('docx', file_path, None))
                    current_files_set.add(file_tuple_key);
                    added_count += 1
                else:
                    skipped_count += 1
            elif file_ext == '.epub':

                if not BS4_AVAILABLE or not LXML_AVAILABLE:  # Ebooklib checked based on output format later
                    self.append_log(
                        f"[WARN] Пропуск EPUB: {base_name} (требуется 'beautifulsoup4' и 'lxml' для обработки EPUB)");
                    skipped_count += 1;
                    continue

                try:
                    self.append_log(f"Анализ EPUB: {base_name}...")

                    nav_path, ncx_path, opf_dir_found, nav_id, ncx_id = self._find_epub_toc_paths(file_path)

                    if opf_dir_found is None:
                        self.append_log(f"[ERROR] Не удалось определить структуру OPF в {base_name}. Пропуск файла.")
                        skipped_count += 1;
                        continue

                    can_process_epub = True
                    missing_lib_reason = ""
                    if current_output_format == 'epub' and (not EBOOKLIB_AVAILABLE):
                        can_process_epub = False;
                        missing_lib_reason = "EbookLib (для записи EPUB)"
                    elif current_output_format == 'fb2' and not LXML_AVAILABLE:  # LXML already checked above
                        pass  # Should be fine if LXML check passed
                    elif current_output_format == 'docx' and not DOCX_AVAILABLE:
                        can_process_epub = False;
                        missing_lib_reason = "python-docx (для записи DOCX)"

                    if not can_process_epub:
                        self.append_log(
                            f"[WARN] Пропуск EPUB->{current_output_format.upper()}: {base_name} (отсутствует '{missing_lib_reason}')")
                        skipped_count += 1;
                        continue

                    with zipfile.ZipFile(file_path, 'r') as epub_zip:

                        html_files_in_epub = sorted([
                            name for name in epub_zip.namelist()
                            if name.lower().endswith(('.html', '.xhtml', '.htm'))
                               and not name.startswith(('__MACOSX', 'META-INF/'))  # Exclude common non-content paths
                        ])
                        if not html_files_in_epub:
                            self.append_log(f"[WARN] В EPUB '{base_name}' не найдено HTML/XHTML файлов.");
                            skipped_count += 1;
                            continue

                        dialog = EpubHtmlSelectorDialog(file_path, html_files_in_epub, nav_path, ncx_path, self)
                        if dialog.exec():
                            selected_html = dialog.get_selected_files()
                            if selected_html:
                                self.append_log(f"Выбрано {len(selected_html)} HTML из {base_name}:")
                                for html_path in selected_html:  # html_path is relative to zip root
                                    epub_tuple_key = (file_path, html_path)
                                    if epub_tuple_key not in current_files_set:

                                        new_files_data_tuples.append(('epub', file_path, html_path))
                                        current_files_set.add(epub_tuple_key)

                                        is_nav_file = (html_path == nav_path)
                                        log_suffix = ""
                                        if is_nav_file and is_potential_epub_rebuild_mode:
                                            log_suffix = " (NAV - БУДЕТ ИЗМЕНЕН, НЕ ПЕРЕВЕДЕН)"
                                        elif is_nav_file:
                                            log_suffix = " (NAV)"  # For non-EPUB output
                                        self.append_log(f"  + {html_path}{log_suffix}")
                                        added_count += 1
                                    else:
                                        self.append_log(f"  - {html_path} (дубликат)");
                                        skipped_count += 1
                            else:  # No HTML files selected in dialog
                                self.append_log(f"HTML не выбраны из {base_name}.");
                                skipped_count += 1
                        else:  # Dialog cancelled
                            self.append_log(f"Выбор HTML из {base_name} отменен.");
                            skipped_count += 1
                except zipfile.BadZipFile:
                    self.append_log(f"[ERROR] Не удалось открыть EPUB: {base_name}. Возможно, поврежден.");
                    skipped_count += 1
                except Exception as e:
                    self.append_log(f"[ERROR] Ошибка обработки EPUB {base_name}: {e}\n{traceback.format_exc()}");
                    skipped_count += 1
            else:  # Unsupported file extension
                self.append_log(f"[WARN] Пропуск неподдерживаемого файла: {base_name}");
                skipped_count += 1

        if new_files_data_tuples:
            self.selected_files_data_tuples.extend(new_files_data_tuples)
            self.update_file_list_widget()  # Sorts and updates display
            log_msg = f"Добавлено {added_count} файлов/частей."
            if skipped_count > 0: log_msg += f" Пропущено {skipped_count}."
            self.append_log(log_msg)
        elif skipped_count > 0:
            self.append_log(f"Новые файлы не добавлены. Пропущено {skipped_count}.")
        else:  # No files selected or all skipped/duplicates
            if files:  # If files were initially selected but none added/skipped
                self.append_log("Выбранные файлы уже в списке или не поддерживаются.")

    def _find_epub_toc_paths(self, epub_path):
        """Finds NAV, NCX paths, OPF directory, and NAV/NCX item IDs within an EPUB."""

        nav_path_in_zip = None;
        ncx_path_in_zip = None
        opf_dir_in_zip = None;
        opf_path_in_zip = None
        nav_item_id = None;
        ncx_item_id = None
        try:
            with zipfile.ZipFile(epub_path, 'r') as zipf:

                try:
                    container_data = zipf.read('META-INF/container.xml')

                    container_root = etree.fromstring(container_data)

                    cnt_ns = {'oebps': 'urn:oasis:names:tc:opendocument:xmlns:container'}
                    opf_path_rel = container_root.xpath('//oebps:rootfile/@full-path', namespaces=cnt_ns)[0]
                    opf_path_in_zip = opf_path_rel.replace('\\', '/')  # Normalize path separator
                    opf_dir_in_zip = os.path.dirname(opf_path_in_zip)
                    if opf_dir_in_zip == '.': opf_dir_in_zip = ""  # Use empty string for root
                except (KeyError, IndexError, etree.XMLSyntaxError) as container_err:

                    print(
                        f"[WARN] EPUB {Path(epub_path).name}: container.xml не найден/некорректен ({container_err}). Поиск OPF...")
                    found_opf = False
                    for name in zipf.namelist():

                        if name.lower().endswith('.opf') and not name.lower().startswith(
                                'meta-inf/') and name.lower() != 'mimetype':
                            opf_path_in_zip = name.replace('\\', '/')

                            opf_dir_in_zip = os.path.dirname(opf_path_in_zip)

                            if opf_dir_in_zip == '.': opf_dir_in_zip = ""

                            print(
                                f"[INFO] EPUB {Path(epub_path).name}: Найден OPF: {opf_path_in_zip} (в директории: '{opf_dir_in_zip or '<root>'}')")
                            found_opf = True;
                            break  # Take the first one found
                    if not found_opf:
                        self.append_log(
                            f"[ERROR] EPUB {Path(epub_path).name}: Не удалось найти OPF файл (ни через container.xml, ни поиском).")

                        return None, None, None, None, None  # Critical failure

                if opf_path_in_zip is None or opf_dir_in_zip is None:
                    self.append_log(f"[ERROR] EPUB {Path(epub_path).name}: OPF путь или директория не определены.")
                    return None, None, None, None, None

                opf_data = zipf.read(opf_path_in_zip)
                opf_root = etree.fromstring(opf_data)  # Use lxml for parsing OPF
                ns = {'opf': 'http://www.idpf.org/2007/opf'}  # OPF namespace

                ncx_id_from_spine = None
                spine_node = opf_root.find('opf:spine', ns)
                if spine_node is not None:
                    ncx_id_from_spine = spine_node.get('toc')  # 'toc' attribute points to NCX ID

                manifest_node = opf_root.find('opf:manifest', ns)
                if manifest_node is not None:
                    for item in manifest_node.findall('opf:item', ns):
                        item_id = item.get('id');
                        item_href = item.get('href');
                        item_media_type = item.get('media-type');
                        item_properties = item.get('properties')

                        if item_href:  # Ensure href exists

                            item_path_abs = os.path.normpath(os.path.join(opf_dir_in_zip, item_href)).replace('\\', '/')

                            if item_properties and 'nav' in item_properties.split():
                                if nav_path_in_zip is None:  # Take the first one found
                                    nav_path_in_zip = item_path_abs
                                    nav_item_id = item_id
                                else:
                                    print(
                                        f"[WARN] EPUB {Path(epub_path).name}: Найдено несколько элементов с 'properties=nav'. Используется первый: {nav_path_in_zip}")

                            if item_media_type == 'application/x-dtbncx+xml' or (
                                    ncx_id_from_spine and item_id == ncx_id_from_spine):
                                if ncx_path_in_zip is None:  # Take the first one found
                                    ncx_path_in_zip = item_path_abs
                                    ncx_item_id = item_id
                                else:
                                    print(
                                        f"[WARN] EPUB {Path(epub_path).name}: Найдено несколько NCX файлов. Используется первый: {ncx_path_in_zip}")

            log_parts = [f"OPF_Dir='{opf_dir_in_zip or '<root>'}'"]
            if nav_path_in_zip: log_parts.append(f"NAV='{nav_path_in_zip}'(ID={nav_item_id})")
            if ncx_path_in_zip: log_parts.append(f"NCX='{ncx_path_in_zip}'(ID={ncx_item_id})")
            self.append_log(f"Структура {Path(epub_path).name}: {', '.join(log_parts)}")

            return nav_path_in_zip, ncx_path_in_zip, opf_dir_in_zip, nav_item_id, ncx_item_id

        except (KeyError, IndexError, etree.XMLSyntaxError, zipfile.BadZipFile) as e:
            self.append_log(
                f"[ERROR] Не удалось найти/прочитать структуру OPF/TOC в {os.path.basename(epub_path)}: {e}")
            return None, None, None, None, None  # Return None for all on error

    def update_file_list_widget(self):
        """ Updates the list widget display, sorting items. """

        self.file_list_widget.clear()
        display_items = []

        sorted_data = sorted(self.selected_files_data_tuples, key=lambda x: (x[1], x[2] if x[2] else ""))
        self.selected_files_data_tuples = sorted_data  # Update internal list with sorted version

        for file_type, path1, path2 in self.selected_files_data_tuples:
            if file_type == 'epub':

                display_items.append(f"{os.path.basename(path1)}  ->  {path2}")
            else:

                display_items.append(os.path.basename(path1))

        self.file_list_widget.addItems(display_items)
        self.file_list_widget.scrollToBottom()  # Scroll to show newly added items
        self.update_file_count_display()  # <<< ВОТ ЭТУ СТРОЧКУ ДОБАВИЛИ

    def clear_file_list(self):

        self.selected_files_data_tuples = []  # Clear internal data
        self.file_list_widget.clear()  # Clear display
        self.append_log("Список файлов очищен.")
        self.update_file_count_display()  # <<< И СЮДА ТОЖЕ ДОБАВИЛИ

    def select_output_folder(self):

        current_path = self.out_lbl.text()
        start_dir = current_path if os.path.isdir(current_path) else QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation)
        path = QFileDialog.getExistingDirectory(self, "Выберите папку для сохранения переводов", start_dir)
        if path:
            self.out_folder = path
            self.out_lbl.setText(path)
            self.out_lbl.setCursorPosition(0)  # Show start of path
            self.append_log(f"Папка вывода: {path}")

    def load_settings(self):

        default_prompt = self.prompt_edit.toPlainText()
        default_out_folder = ""
        default_format_display_name = DEFAULT_OUTPUT_FORMAT_DISPLAY
        default_model_name = DEFAULT_MODEL_NAME
        default_concurrency = self.concurrency_spin.value()
        default_chunking_enabled = self.chunking_checkbox.isChecked()
        default_chunk_limit = self.chunk_limit_spin.value()
        default_chunk_window = self.chunk_window_spin.value()
        default_temperature = 1.0
        default_chunk_delay = 0.0  # <-- Новое значение по умолчанию
        default_proxy_url = ""  # <-- Новое значение по умолчанию для прокси

        settings_loaded_successfully = False
        settings_source_message = f"Файл '{SETTINGS_FILE}' не найден или пуст. Используются умолчания."

        try:
            if os.path.exists(SETTINGS_FILE):
                self.config.clear()
                read_ok = self.config.read(SETTINGS_FILE, encoding='utf-8')
                if read_ok and 'Settings' in self.config:
                    settings = self.config['Settings']

                    self.prompt_edit.setPlainText(settings.get('Prompt', default_prompt))
                    loaded_out_folder = settings.get('OutputFolder', default_out_folder)
                    self.out_folder = loaded_out_folder if os.path.isdir(loaded_out_folder) else default_out_folder
                    self.out_lbl.setText(self.out_folder if self.out_folder else "<не выбрано>")
                    self.out_lbl.setCursorPosition(0)
                    saved_format_display = settings.get('OutputFormat', default_format_display_name)
                    format_index = self.format_combo.findText(saved_format_display, Qt.MatchFlag.MatchFixedString)
                    first_enabled_idx = 0
                    for i_fmt in range(self.format_combo.count()):
                        if self.format_combo.model().item(i_fmt).isEnabled():
                            first_enabled_idx = i_fmt;
                            break
                    if format_index != -1 and self.format_combo.model().item(format_index).isEnabled():
                        self.format_combo.setCurrentIndex(format_index)
                    else:
                        self.format_combo.setCurrentIndex(first_enabled_idx)
                        if format_index != -1:
                            settings_source_message = f"[WARN] Сохраненный формат '{saved_format_display}' недоступен. Используется '{self.format_combo.itemText(first_enabled_idx)}'."
                    model_name = settings.get('Model', default_model_name)
                    self.model_combo.setCurrentText(model_name if model_name in MODELS else default_model_name)
                    self.concurrency_spin.setValue(settings.getint('Concurrency', default_concurrency))
                    self.chunking_checkbox.setChecked(settings.getboolean('ChunkingEnabled', default_chunking_enabled))
                    self.chunk_limit_spin.setValue(settings.getint('ChunkLimit', default_chunk_limit))
                    self.chunk_window_spin.setValue(settings.getint('ChunkWindow', default_chunk_window))
                    self.temperature_spin.setValue(settings.getfloat('Temperature', default_temperature))

                    self.chunk_delay_spin.setValue(settings.getfloat('ChunkDelay', default_chunk_delay))

                    # --- ЗАГРУЗКА ПРОКСИ ---
                    self.proxy_url_edit.setText(settings.get('ProxyURL', default_proxy_url))
                    # --- КОНЕЦ ЗАГРУЗКИ ПРОКСИ ---

                    settings_loaded_successfully = True
                    settings_source_message = f"Настройки загружены из '{SETTINGS_FILE}'."
        except (configparser.Error, ValueError, KeyError) as e:
            settings_source_message = f"[ERROR] Ошибка загрузки настроек ({e}). Используются умолчания."
            settings_loaded_successfully = False

        self.append_log(settings_source_message)

        if not settings_loaded_successfully:
            self.prompt_edit.setPlainText(default_prompt)
            self.out_folder = default_out_folder
            self.out_lbl.setText(self.out_folder if self.out_folder else "<не выбрано>")
            self.out_lbl.setCursorPosition(0)
            first_enabled_idx_def = 0
            for i_fmt_def in range(self.format_combo.count()):
                if self.format_combo.model().item(i_fmt_def).isEnabled():
                    first_enabled_idx_def = i_fmt_def;
                    break
            self.format_combo.setCurrentIndex(first_enabled_idx_def)
            self.model_combo.setCurrentText(default_model_name)
            self.concurrency_spin.setValue(default_concurrency)
            self.chunking_checkbox.setChecked(default_chunking_enabled)
            self.chunk_limit_spin.setValue(default_chunk_limit)
            self.chunk_window_spin.setValue(default_chunk_window)
            self.temperature_spin.setValue(default_temperature)

            self.chunk_delay_spin.setValue(default_chunk_delay)
            # --- УСТАНОВКА ПРОКСИ ПО УМОЛЧАНИЮ ---
            self.proxy_url_edit.setText(default_proxy_url)
            # --- КОНЕЦ УСТАНОВКИ ПРОКСИ ---

        self.toggle_chunking_details(self.chunking_checkbox.checkState().value)
        self.update_concurrency_suggestion(self.model_combo.currentText())
        self.update_chunking_checkbox_suggestion(self.model_combo.currentText())

    def save_settings(self):
        try:
            if 'Settings' not in self.config: self.config['Settings'] = {}
            settings = self.config['Settings']
            settings['Prompt'] = self.prompt_edit.toPlainText()
            settings['OutputFolder'] = self.out_folder or ""
            settings['OutputFormat'] = self.format_combo.currentText()
            settings['Model'] = self.model_combo.currentText()
            settings['Concurrency'] = str(self.concurrency_spin.value())
            settings['ChunkingEnabled'] = str(self.chunking_checkbox.isChecked())
            settings['ChunkLimit'] = str(self.chunk_limit_spin.value())
            settings['ChunkWindow'] = str(self.chunk_window_spin.value())
            settings['Temperature'] = str(self.temperature_spin.value())

            settings['ChunkDelay'] = str(self.chunk_delay_spin.value())

            # --- СОХРАНЕНИЕ ПРОКСИ ---
            settings['ProxyURL'] = self.proxy_url_edit.text().strip()
            # --- КОНЕЦ СОХРАНЕНИЯ ПРОКСИ ---

            with open(SETTINGS_FILE, 'w', encoding='utf-8') as configfile:
                self.config.write(configfile)
        except Exception as e:
            self.append_log(f"[ERROR] Не удалось сохранить настройки: {e}")

    def check_api_key(self):
        """Checks if the API key is valid by listing models."""

        current_api_key_to_check = self.api_key
        prompt_for_new_key = not current_api_key_to_check

        if prompt_for_new_key:
            key, ok = QtWidgets.QInputDialog.getText(self, "Требуется API ключ", "Введите ваш Google API Key:",
                                                     QLineEdit.EchoMode.Password)
            current_api_key_to_check = key.strip() if ok and key.strip() else None

        if not current_api_key_to_check:
            QMessageBox.warning(self, "Проверка ключа", "API ключ не введен.")
            return

        self.append_log(f"Проверка API ключа...")
        self.check_api_key_btn.setEnabled(False)
        self.setCursor(Qt.CursorShape.WaitCursor)

        key_valid = False
        original_key = self.api_key  # Store original key in case check fails

        try:

            genai.configure(api_key=current_api_key_to_check)

            models = genai.list_models()

            key_valid = any(m.name.startswith("models/") for m in models)

            if key_valid:

                if current_api_key_to_check != self.api_key:
                    self.api_key = current_api_key_to_check
                    self.append_log("[INFO] Новый API ключ принят и сохранен.")
                QMessageBox.information(self, "Проверка ключа", "API ключ действителен.")
                self.append_log("[SUCCESS] API ключ действителен.")
            else:

                QMessageBox.warning(self, "Проверка ключа", "Ключ принят API, но не найдено доступных моделей Gemini.")
                self.append_log("[WARN] Проверка ключа: Нет доступных моделей Gemini.")

        except google_exceptions.Unauthenticated as e:
            QMessageBox.critical(self, "Проверка ключа", f"Ошибка аутентификации (неверный ключ?):\n{e}")
            self.append_log(f"[ERROR] Проверка ключа: Неверный ({e})")

            if current_api_key_to_check == self.api_key: self.api_key = None
            key_valid = False
        except google_exceptions.PermissionDenied as e:
            QMessageBox.critical(self, "Проверка ключа", f"Ошибка разрешений (ключ не активирован для API?):\n{e}")
            self.append_log(f"[ERROR] Проверка ключа: Ошибка разрешений ({e})")
            key_valid = False  # Key is likely valid but lacks permissions
        except google_exceptions.GoogleAPICallError as e:  # Network errors etc.
            QMessageBox.critical(self, "Проверка ключа", f"Ошибка вызова API (сеть?):\n{e}")
            self.append_log(f"[ERROR] Проверка ключа: Ошибка вызова API ({e})")
            key_valid = False
        except Exception as e:  # Catch-all
            QMessageBox.critical(self, "Проверка ключа", f"Неожиданная ошибка:\n{e}")
            self.append_log(f"[ERROR] Проверка ключа: ({e})\n{traceback.format_exc()}")
            key_valid = False
        finally:

            self.check_api_key_btn.setEnabled(True)
            self.unsetCursor()

            final_key_to_configure = self.api_key  # self.api_key was updated only if key_valid and different
            try:
                if final_key_to_configure:
                    genai.configure(api_key=final_key_to_configure)
                else:

                    self.append_log("[WARN] Действующий API ключ неизвестен. API может не работать.")

            except Exception as configure_err:

                self.append_log(f"[ERROR] Ошибка восстановления конфигурации API: {configure_err}")

    @QtCore.pyqtSlot(str)
    def handle_log_message(self, message):

        self.append_log(message)

    def append_log(self, message):
        """Appends a timestamped message to the log widget."""

        current_time = time.strftime("%H:%M:%S")

        message_str = str(message).strip()

        for line in message_str.splitlines():
            self.log_output.append(f"[{current_time}] {line}")

        scrollbar = self.log_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    @QtCore.pyqtSlot(int)
    def update_file_progress(self, processed_count):

        self.progress_bar.setValue(processed_count)

    @QtCore.pyqtSlot(int)
    def update_progress_bar_range(self, total_tasks):
        """Sets the maximum value of the progress bar."""

        self.progress_bar.setRange(0, max(1, total_tasks))
        self.progress_bar.setValue(0)  # Reset progress value
        self.progress_bar.setFormat(f"%v / {total_tasks} задач (%p%)")  # Update text format
        self.append_log(f"Общее количество задач для выполнения: {total_tasks}")

    @QtCore.pyqtSlot(str)
    def handle_current_file_status(self, message):

        self.status_label.setText(message)

    @QtCore.pyqtSlot(str, int, int)
    def handle_chunk_progress(self, filename, current_chunk, total_chunks):
        """Updates the status label with chunk processing progress."""

        if total_chunks > 1 and current_chunk >= 0:
            max_len = 60  # Max length for filename display

            display_name = filename if len(filename) <= max_len else f"...{filename[-(max_len - 3):]}"
            self.status_label.setText(f"Файл: {display_name} [Чанк: {current_chunk}/{total_chunks}]")
        elif total_chunks == 1 and current_chunk > 0:  # Single chunk file completed
            max_len = 60
            display_name = filename if len(filename) <= max_len else f"...{filename[-(max_len - 3):]}"
            self.status_label.setText(f"Файл: {display_name} [1/1 Завершено]")

    def start_translation(self):
        """Validates inputs and starts the background worker thread."""
        prompt_template = self.prompt_edit.toPlainText().strip()
        selected_model_name = self.model_combo.currentText()
        max_concurrency = self.concurrency_spin.value()
        selected_files_tuples = list(self.selected_files_data_tuples)
        selected_format_display = self.format_combo.currentText()
        output_format = OUTPUT_FORMATS.get(selected_format_display, 'txt')
        chunking_enabled_gui = self.chunking_checkbox.isChecked()
        chunk_limit = self.chunk_limit_spin.value();
        chunk_window = self.chunk_window_spin.value()
        temperature = self.temperature_spin.value()

        chunk_delay = self.chunk_delay_spin.value()

        # --- ПОЛУЧЕНИЕ ПРОКСИ ИЗ GUI ---
        proxy_string = self.proxy_url_edit.text().strip()
        # --- КОНЕЦ ПОЛУЧЕНИЯ ПРОКСИ ---

        if not selected_files_tuples:
            QMessageBox.warning(self, "Ошибка", "Не выбраны файлы для перевода.");
            return
        if not self.out_folder:
            QMessageBox.warning(self, "Ошибка", "Не выбрана папка вывода.");
            return
        if not os.path.isdir(self.out_folder):
            reply = QMessageBox.question(self, "Папка не существует",
                                         f"Папка '{self.out_folder}' не найдена.\nСоздать?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.Yes)
            if reply == QMessageBox.StandardButton.Yes:
                try:
                    os.makedirs(self.out_folder, exist_ok=True); self.append_log(f"Папка '{self.out_folder}' создана.")
                except OSError as e:
                    QMessageBox.critical(self, "Ошибка", f"Не удалось создать папку: {e}"); return
            else:
                return

        if output_format == 'docx' and not DOCX_AVAILABLE:
            QMessageBox.critical(self, "Ошибка",
                                 "Выбран формат вывода DOCX, но библиотека 'python-docx' не установлена.");
            return
        if output_format == 'epub' and (not EBOOKLIB_AVAILABLE or not LXML_AVAILABLE or not BS4_AVAILABLE):
            QMessageBox.critical(self, "Ошибка",
                                 "Выбран формат вывода EPUB, но не установлены: 'ebooklib', 'lxml' и 'beautifulsoup4'.");
            return
        if output_format == 'fb2' and not LXML_AVAILABLE:
            QMessageBox.critical(self, "Ошибка", "Выбран формат вывода FB2, но библиотека 'lxml' не установлена.");
            return
        if output_format in ['docx', 'epub', 'fb2', 'html'] and not PILLOW_AVAILABLE:
            QMessageBox.warning(self, "Предупреждение",
                                f"Выбран формат вывода {output_format.upper()} с поддержкой изображений, но библиотека 'Pillow' не найдена.\nОбработка некоторых форматов изображений (напр., EMF) может быть невозможна.")
        needs_docx_input = any(ft == 'docx' for ft, _, _ in selected_files_tuples)
        needs_epub_input = any(ft == 'epub' for ft, _, _ in selected_files_tuples)
        if needs_docx_input and not DOCX_AVAILABLE:
            QMessageBox.critical(self, "Ошибка",
                                 "Выбраны DOCX файлы для ввода, но библиотека 'python-docx' не установлена.");
            return
        if needs_epub_input and (not BS4_AVAILABLE or not LXML_AVAILABLE):
            QMessageBox.critical(self, "Ошибка",
                                 "Выбраны EPUB файлы для ввода, но не установлены 'beautifulsoup4' и/или 'lxml'.");
            return
        if selected_model_name not in MODELS:
            QMessageBox.critical(self, "Ошибка", f"Некорректная модель API: {selected_model_name}");
            return
        if "{text}" not in prompt_template:
            QMessageBox.warning(self, "Ошибка Промпта",
                                "Промпт ДОЛЖЕН содержать плейсхолдер `{text}` для вставки текста.");
            return
        if "<||" not in prompt_template or "img_placeholder" not in prompt_template:
            QMessageBox.warning(self, "Предупреждение Промпта",
                                "Промпт не содержит явных инструкций для обработки плейсхолдеров изображений (`<||img_placeholder_...||>`).\nAPI может их случайно изменить или удалить.")
        if not self.api_key:
            key, ok = QtWidgets.QInputDialog.getText(self, "Требуется API ключ", "Введите ваш Google API Key:",
                                                     QLineEdit.EchoMode.Password)
            if ok and key.strip():
                self.api_key = key.strip(); self.append_log("[INFO] API ключ принят.")
            else:
                QMessageBox.critical(self, "Ошибка", "API ключ не предоставлен."); return
        if self.thread_ref and self.thread_ref.isRunning():
            QMessageBox.warning(self, "Внимание", "Процесс перевода уже запущен.");
            return

        is_epub_to_epub_mode = False
        worker_data = None
        if output_format == 'epub':
            if not selected_files_tuples or not all(ft == 'epub' for ft, _, _ in selected_files_tuples):
                QMessageBox.critical(self, "Ошибка Конфигурации",
                                     "Обнаружена несовместимость: выбран вывод EPUB, но список содержит не-EPUB файлы. Очистите список и попробуйте снова.")
                return
            is_epub_to_epub_mode = True
            epub_groups_for_worker = {}
            epub_paths_in_list = sorted(list(set(p1 for ft, p1, _ in selected_files_tuples if ft == 'epub')))
            valid_epubs_found = False
            failed_epub_structures = []
            for epub_path in epub_paths_in_list:
                nav_path, ncx_path, opf_dir, nav_id, ncx_id = self._find_epub_toc_paths(epub_path)
                if opf_dir is None:
                    QMessageBox.warning(self, "Ошибка EPUB",
                                        f"Не удалось обработать структуру EPUB:\n{Path(epub_path).name}\n\nПропуск этого файла.")
                    failed_epub_structures.append(epub_path)
                    continue
                html_paths_for_this_epub = [p2 for ft, p1, p2 in selected_files_tuples if
                                            ft == 'epub' and p1 == epub_path and p2]
                html_to_translate_for_worker = [p for p in html_paths_for_this_epub if p != nav_path]
                epub_groups_for_worker[epub_path] = {
                    'html_paths': html_to_translate_for_worker,
                    'build_metadata': {
                        'nav_path_in_zip': nav_path, 'ncx_path_in_zip': ncx_path,
                        'opf_dir': opf_dir, 'nav_item_id': nav_id, 'ncx_item_id': ncx_id
                    }
                }
                valid_epubs_found = True
            if failed_epub_structures:
                self.selected_files_data_tuples = [t for t in self.selected_files_data_tuples if
                                                   t[1] not in failed_epub_structures]
                self.update_file_list_widget()
            if not valid_epubs_found:
                QMessageBox.warning(self, "Нет файлов",
                                    "Не найдено допустимых EPUB файлов для обработки в режиме EPUB->EPUB (возможно, ошибки структуры).")
                self.clear_file_list();
                return
            worker_data = epub_groups_for_worker
            QMessageBox.information(self, "Режим EPUB->EPUB",
                                    "Запуск в режиме EPUB -> EPUB.\nБудет выполнено:\n"
                                    "- Перевод выбранных HTML (кроме файла NAV).\n"
                                    "- Переименование переведенных файлов (*_translated.html/xhtml).\n"
                                    "- Поиск и ИЗМЕНЕНИЕ существующего файла оглавления (NAV/NCX) для обновления ссылок.")
        else:
            worker_data = selected_files_tuples

        self.log_output.clear();
        self.progress_bar.setRange(0, 100);
        self.progress_bar.setValue(0);
        self.progress_bar.setFormat("Подготовка...")
        self.status_label.setText("Подготовка...");
        self.append_log("=" * 40 + f"\nНАЧАЛО ПЕРЕВОДА")
        self.append_log(f"Режим: {'EPUB->EPUB Rebuild' if is_epub_to_epub_mode else 'Стандартный'}")
        self.append_log(f"Модель: {selected_model_name}");
        self.append_log(f"Паралл. запросы: {max_concurrency}");
        self.append_log(f"Формат вывода: .{output_format}")

        chunking_log_msg = f"Чанкинг GUI: {'Да' if chunking_enabled_gui else 'Нет'} (Лимит: {chunk_limit:,}, Окно: {chunk_window:,}"
        if chunking_enabled_gui and chunk_delay > 0:
            chunking_log_msg += f", Задержка: {chunk_delay:.1f} сек.)"
        else:
            chunking_log_msg += ")"
        self.append_log(chunking_log_msg)

        if not CHUNK_HTML_SOURCE and chunking_enabled_gui: self.append_log("[INFO] Чанкинг HTML/EPUB отключен.")
        self.append_log(f"Папка вывода: {self.out_folder}")
        self.append_log(
            f"Поддержка: DOCX={'ДА' if DOCX_AVAILABLE else 'НЕТ'}, BS4={'ДА' if BS4_AVAILABLE else 'НЕТ'}, LXML={'ДА' if LXML_AVAILABLE else 'НЕТ'}, EbookLib={'ДА' if EBOOKLIB_AVAILABLE else 'НЕТ'}, Pillow={'ДА' if PILLOW_AVAILABLE else 'НЕТ'}")
        self.append_log("=" * 40);
        self.set_controls_enabled(False)
        self.thread = QtCore.QThread()

        self.worker = Worker(
            self.api_key, self.out_folder, prompt_template, worker_data,
            MODELS[selected_model_name], max_concurrency, output_format,
            chunking_enabled_gui, chunk_limit, chunk_window,
            temperature,
            chunk_delay,  # <-- Вот этот аргумент был пропущен
            proxy_string=proxy_string  # <--- Передаем строку прокси в Worker

        )
        self.worker.moveToThread(self.thread)
        self.worker_ref = self.worker
        self.thread_ref = self.thread
        self.worker.file_progress.connect(self.update_file_progress)
        self.worker.current_file_status.connect(self.handle_current_file_status)
        self.worker.chunk_progress.connect(self.handle_chunk_progress)
        self.worker.log_message.connect(self.handle_log_message)
        self.worker.finished.connect(self.on_translation_finished)
        self.worker.total_tasks_calculated.connect(self.update_progress_bar_range)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self.clear_worker_refs)
        # --- ЛОГИРОВАНИЕ ПРОКСИ (после инициализации Worker, чтобы он уже имел self.proxy_string) ---
        if self.worker.proxy_string:  # Проверяем, что worker.proxy_string установлен
            self.append_log(f"Прокси для Worker настроен на: {self.worker.proxy_string}")
        else:
            self.append_log("Прокси для Worker: Не используется")
        # --- КОНЕЦ ЛОГИРОВАНИЯ ПРОКСИ ---
        self.thread.start()
        self.append_log("Рабочий поток запущен...")
        self.status_label.setText("Запуск...")

    def cancel_translation(self):
        if self.worker_ref and self.thread_ref and self.thread_ref.isRunning():
            self.append_log("Отправка сигнала ОТМЕНЫ...")
            self.status_label.setText("Отмена...")
            self.worker_ref.cancel()
            self.cancel_btn.setEnabled(False)
            self.finish_btn.setEnabled(False)  # <--- ДОБАВИТЬ
            self.append_log("Ожидание завершения потока...")
        else:
            self.append_log("[WARN] Нет активного процесса для отмены.")

    @QtCore.pyqtSlot(int, int, list)
    def on_translation_finished(self, success_count, error_count, errors):
        worker_ref_exists = self.worker_ref is not None
        was_cancelled = worker_ref_exists and self.worker_ref.is_cancelled
        was_finishing = worker_ref_exists and hasattr(self.worker_ref, 'is_finishing') and self.worker_ref.is_finishing

        # Логируем финальные итоги перед показом QMessageBox
        log_end_separator = "=" * 40
        self.append_log(f"\n{log_end_separator}")
        if was_cancelled:
            self.append_log("--- ПРОЦЕСС БЫЛ ОТМЕНЕН ПОЛЬЗОВАТЕЛЕМ ---")
        elif was_finishing:
            self.append_log("--- ПРОЦЕСС БЫЛ ЗАВЕРШЕН ПО КОМАНДЕ 'ЗАВЕРШИТЬ' (частично) ---")
        # Дополнительные логи об ошибках Executor или API уже должны быть в Worker.run

        self.append_log(f"ИТОГ: Успешно: {success_count}, Ошибок/Отменено/Пропущено: {error_count}")
        if errors:
            self.append_log("Детали ошибок/отмен/пропусков:")
            max_errors_to_show = 30
            for i, e in enumerate(errors[:max_errors_to_show]):
                error_str = str(e)
                max_len = 350
                display_error = error_str[:max_len] + ('...' if len(error_str) > max_len else '')
                self.append_log(f"- {display_error}")
            if len(errors) > max_errors_to_show:
                self.append_log(f"- ... ({len(errors) - max_errors_to_show} еще)")
        self.append_log(log_end_separator)

        final_message = ""
        msg_type = QMessageBox.Icon.Information
        title = "Завершено"
        total_tasks = self.progress_bar.maximum()  # Получаем общее количество задач из прогресс-бара

        if was_cancelled:
            title = "Отменено"
            msg_type = QMessageBox.Icon.Warning
            final_message = f"Процесс отменен.\n\nУспешно до отмены: {success_count}\nОшибок/Пропущено: {error_count}"
            self.status_label.setText("Отменено")
        elif was_finishing:
            title = "Завершено (частично)"
            msg_type = QMessageBox.Icon.Information
            final_message = f"Процесс завершен по команде 'Завершить'.\n\nОбработано (полностью или частично): {success_count}\nОшибок/Пропущено по другим причинам: {error_count}"
            self.status_label.setText("Завершено (частично)")
        elif error_count == 0 and success_count > 0:
            title = "Готово!"
            msg_type = QMessageBox.Icon.Information
            final_message = f"Перевод {success_count} заданий успешно завершен!"
            self.status_label.setText("Готово!")
        elif success_count > 0 and error_count > 0:
            title = "Завершено с ошибками"
            msg_type = QMessageBox.Icon.Warning
            final_message = f"Перевод завершен.\n\nУспешно: {success_count}\nОшибок/Пропущено: {error_count}\n\nСм. лог."
            self.status_label.setText("Завершено с ошибками")
        elif success_count == 0 and error_count > 0:
            title = "Завершено с ошибками"
            msg_type = QMessageBox.Icon.Critical
            final_message = f"Не удалось успешно перевести ни одного задания.\nОшибок/Пропущено: {error_count}\n\nСм. лог."
            self.status_label.setText("Завершено с ошибками")
        elif success_count == 0 and error_count == 0 and total_tasks > 0:
            title = "Внимание"
            msg_type = QMessageBox.Icon.Warning
            final_message = f"Обработка завершена, но нет успешных заданий или ошибок (возможно, все пропущено или отменено до начала?).\nПроверьте лог."
            self.status_label.setText("Завершено (проверьте лог)")
        elif total_tasks == 0:  # Если изначально не было задач
            title = "Нет задач"
            msg_type = QMessageBox.Icon.Information
            final_message = "Нет файлов или задач для обработки."
            self.status_label.setText("Нет задач")
        else:  # Общий случай, если ни одно из условий выше не сработало
            final_message = "Обработка завершена."
            self.status_label.setText("Завершено")

        if self.isVisible():  # Показываем QMessageBox только если окно видимо
            QMessageBox(msg_type, title, final_message, QMessageBox.StandardButton.Ok, self).exec()
        else:  # Если окно не видимо (например, закрыто во время выполнения), просто логируем
            self.append_log(f"Диалог завершения: {title} - {final_message}")

    @QtCore.pyqtSlot()
    def clear_worker_refs(self):

        self.append_log("Фоновый поток завершен. Очистка ссылок...");
        self.worker = None
        self.thread = None
        self.worker_ref = None
        self.thread_ref = None
        self.set_controls_enabled(True)
        self.append_log("Интерфейс разблокирован.")

    def set_controls_enabled(self, enabled):
        widgets_to_toggle = [
            self.file_select_btn, self.clear_list_btn, self.out_btn, self.format_combo,
            self.model_combo, self.concurrency_spin, self.temperature_spin,
            self.chunking_checkbox, self.proxy_url_edit,  # <-- Добавлено поле прокси

            self.chunk_delay_spin,  # <-- Добавлено

            self.prompt_edit,
            self.start_btn, self.check_api_key_btn
        ]
        for widget in widgets_to_toggle: widget.setEnabled(enabled)
        if enabled:
            self.toggle_chunking_details(
                self.chunking_checkbox.checkState().value)  # This will also handle chunk_delay_spin
            for code, index in self.format_indices.items():
                item = self.format_combo.model().item(index)
                if item:
                    is_available = True;
                    tooltip = f"Сохранить как .{code}"
                    if code == 'docx' and not DOCX_AVAILABLE:
                        is_available = False; tooltip = "Требуется: python-docx"
                    elif code == 'epub' and (not EBOOKLIB_AVAILABLE or not LXML_AVAILABLE or not BS4_AVAILABLE):
                        is_available = False; tooltip = "Требуется: ebooklib, lxml, beautifulsoup4"
                    elif code == 'fb2' and not LXML_AVAILABLE:
                        is_available = False; tooltip = "Требуется: lxml"
                    if code in ['docx', 'epub', 'fb2', 'html'] and not PILLOW_AVAILABLE:
                        if is_available:
                            tooltip += "\n(Реком.: Pillow для изобр.)"
                        else:
                            tooltip += "; Pillow (реком.)"
                    item.setEnabled(is_available);
                    self.format_combo.setItemData(index, tooltip, Qt.ItemDataRole.ToolTipRole)
                    self.cancel_btn.setEnabled(False)  # Убедиться, что кнопки управления процессом выключены
                    self.finish_btn.setEnabled(False)
        else:
            self.chunk_limit_spin.setEnabled(False)
            self.chunk_window_spin.setEnabled(False)
            self.chunk_delay_spin.setEnabled(False)
            self.cancel_btn.setEnabled(True)  # Включить кнопки управления процессом
            self.finish_btn.setEnabled(True)

    def closeEvent(self, event: QtGui.QCloseEvent):

        self.save_settings()
        if self.thread_ref and self.thread_ref.isRunning():
            reply = QMessageBox.question(self, "Процесс выполняется", "Перевод все еще выполняется.\nПрервать и выйти?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self.append_log("Выход во время выполнения, отмена..."); self.cancel_translation(); event.accept()
            else:
                event.ignore()
        else:
            event.accept()