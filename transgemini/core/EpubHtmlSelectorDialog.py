import os
import re
from pathlib import Path

from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QListWidget, QPushButton,
    QDialogButtonBox, QLabel, QWidget, QLineEdit, QComboBox, QSpinBox,
    QCheckBox, QPlainTextEdit, QDoubleSpinBox, QProgressBar, QTextEdit,
    QGridLayout, QGroupBox, QHBoxLayout, QMessageBox, QFileDialog, QScrollArea
)
from PyQt6.QtCore import QStandardPaths, Qt

from transgemini.config import TRANSLATED_SUFFIX


class EpubHtmlSelectorDialog(QDialog):

    def __init__(self, epub_filename, html_files, nav_path, ncx_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Выберите HTML/XHTML файлы из '{os.path.basename(epub_filename)}'")
        self.setMinimumWidth(500);
        self.setMinimumHeight(400)  # Можно даже чуть больше высоту, например 450
        layout = QVBoxLayout(self)
        info_text = f"Найденные HTML/XHTML файлы в:\n{epub_filename}\n\n"
        info_text += f"Авто-определен NAV (Оглавление EPUB3): {nav_path or 'Нет'}\n"
        info_text += "\nВыберите файлы для перевода.\n(NAV файл РЕКОМЕНДУЕТСЯ ИСКЛЮЧИТЬ, т.к. ссылки обновятся автоматически):"

        self.info_label = QLabel(info_text)
        layout.addWidget(self.info_label)

        self.hide_translated_checkbox = QCheckBox("Скрыть файлы _translated")
        self.hide_translated_checkbox.setToolTip(
            "Если отмечено, файлы с суффиксом _translated (например, chapter1_translated.html) будут скрыты из списка."
        )
        self.hide_translated_checkbox.setChecked(False)
        self.hide_translated_checkbox.stateChanged.connect(self.update_file_visibility)  # Эта строка остается
        layout.addWidget(self.hide_translated_checkbox)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)

        self.list_widget.itemSelectionChanged.connect(self.update_selection_count_label)

        self.all_html_files_with_data = []  # Эта часть остается как была
        for file_path in html_files:
            item = QtWidgets.QListWidgetItem(file_path)
            is_nav = (nav_path and file_path == nav_path)
            is_translated = Path(file_path).stem.endswith(TRANSLATED_SUFFIX)  # Проверяем суффикс

            self.all_html_files_with_data.append({
                'text': file_path,
                'is_nav': is_nav,
                'is_translated': is_translated  # Сохраняем, является ли файл переведенным
            })

            if is_nav:
                item.setBackground(QtGui.QColor("#fff0f0"))  # Light red background for NAV
                item.setToolTip(
                    f"{file_path}\n(Это файл ОГЛАВЛЕНИЯ EPUB3 (NAV).\nНЕ РЕКОМЕНДУЕТСЯ переводить - ссылки обновятся автоматически.)")
                item.setSelected(False)  # Deselect NAV by default
            else:
                item_text_lower = item.text().lower()
                path = Path(item_text_lower)
                filename_lower = path.name
                filename_base = path.stem.split('.')[0]  # Get stem before first dot

                skip_indicators = ['toc', 'nav', 'ncx', 'cover', 'title', 'index', 'copyright', 'about', 'meta', 'opf',
                                   'masthead', 'colophon', 'imprint', 'acknowledgments', 'dedication',
                                   'glossary', 'bibliography', 'notes', 'annotations', 'epigraph', 'halftitle',
                                   'frontmatter', 'backmatter', 'preface', 'introduction', 'appendix', 'biography',
                                   'isbn', 'legal', 'notice', 'otherbooks', 'prelims', 'team', 'promo', 'bonus']
                content_indicators = ['chapter', 'part', 'section', 'content', 'text', 'page', 'body', 'main',
                                      'article',
                                      'chp', 'chap', 'prt', 'sec', 'glava', 'prologue', 'epilogue']

                is_likely_skip = any(skip in filename_base for skip in skip_indicators)
                parent_dir_lower = str(path.parent).lower()
                is_likely_skip = is_likely_skip or any(skip in parent_dir_lower for skip in
                                                       ['toc', 'nav', 'meta', 'frontmatter', 'backmatter', 'index',
                                                        'notes'])
                is_likely_content = any(indicator in filename_base for indicator in content_indicators)
                is_chapter_like = re.fullmatch(r'(ch|gl|chap|chapter|part|section|sec|glava)[\d_-]+.*',
                                               filename_base) or \
                                  re.fullmatch(r'[\d]+', filename_base) or \
                                  re.match(r'^[ivxlcdm]+$', filename_base)

                if not is_likely_skip and (is_likely_content or is_chapter_like):
                    item.setSelected(True)
                else:
                    if not is_likely_skip and 'text' in filename_base:
                        item.setSelected(True)
                    else:
                        item.setSelected(False)
                item.setToolTip(file_path)

        layout.addWidget(self.list_widget)  # Добавляем список

        self.selection_count_label = QLabel("Выбрано: 0 из 0")
        layout.addWidget(self.selection_count_label)

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        self.update_file_visibility()

    def update_selection_count_label(self):
        """Обновляет метку, показывающую количество выбранных и общее количество видимых файлов."""
        selected_items_count = len(self.list_widget.selectedItems())
        total_visible_items_count = self.list_widget.count()  # count() дает количество элементов в виджете
        self.selection_count_label.setText(f"Выбрано: {selected_items_count} из {total_visible_items_count} (видимых)")

    def update_file_visibility(self):
        hide_translated = self.hide_translated_checkbox.isChecked()

        current_selected_text = None
        selected_items_list = self.list_widget.selectedItems()  # QListWidget.selectedItems() returns a list
        if selected_items_list:  # Check if the list is not empty
            current_selected_text = selected_items_list[0].text()

        self.list_widget.clear()

        for file_data in self.all_html_files_with_data:
            if hide_translated and file_data['is_translated']:
                continue

            item = QtWidgets.QListWidgetItem(file_data['text'])

            if file_data['is_nav']:
                item.setBackground(QtGui.QColor("#fff0f0"))
                item.setToolTip(
                    f"{file_data['text']}\n(Это файл ОГЛАВЛЕНИЯ EPUB3 (NAV).\nНЕ РЕКОМЕНДУЕТСЯ переводить - ссылки обновятся автоматически.)")
                item.setSelected(False)
            else:
                item_text_lower = item.text().lower()
                path = Path(item_text_lower)
                filename_base = path.stem.split('.')[0]

                skip_indicators = ['toc', 'nav', 'ncx', 'cover', 'title', 'index', 'copyright', 'about', 'meta', 'opf',
                                   'masthead', 'colophon', 'imprint', 'acknowledgments', 'dedication',
                                   'glossary', 'bibliography', 'notes', 'annotations', 'epigraph', 'halftitle',
                                   'frontmatter', 'backmatter', 'preface', 'introduction', 'appendix', 'biography',
                                   'isbn', 'legal', 'notice', 'otherbooks', 'prelims', 'team', 'promo', 'bonus']
                content_indicators = ['chapter', 'part', 'section', 'content', 'text', 'page', 'body', 'main',
                                      'article',
                                      'chp', 'chap', 'prt', 'sec', 'glava', 'prologue', 'epilogue']

                is_likely_skip = any(skip in filename_base for skip in skip_indicators)
                parent_dir_lower = str(path.parent).lower()
                is_likely_skip = is_likely_skip or any(skip in parent_dir_lower for skip in
                                                       ['toc', 'nav', 'meta', 'frontmatter', 'backmatter', 'index',
                                                        'notes'])

                is_likely_content = any(indicator in filename_base for indicator in content_indicators)

                is_chapter_like_match = re.fullmatch(r'(ch|gl|chap|chapter|part|section|sec|glava)[\d_-]+.*',
                                                     filename_base) or \
                                        re.fullmatch(r'[\d]+', filename_base) or \
                                        re.match(r'^[ivxlcdm]+$', filename_base)

                content_topic_criteria = bool(is_likely_content or is_chapter_like_match)

                should_be_selected = (not file_data['is_translated'] and
                                      not is_likely_skip and
                                      content_topic_criteria)

                if (not should_be_selected and
                        not file_data['is_translated'] and
                        not is_likely_skip and
                        'text' in filename_base):
                    should_be_selected = True

                item.setSelected(should_be_selected)  # should_be_selected теперь всегда будет True или False
                item.setToolTip(file_data['text'])

            self.list_widget.addItem(item)

            if current_selected_text and item.text() == current_selected_text:
                item.setSelected(True)

        self.update_selection_count_label()  # <<< ВОТ ЭТУ СТРОЧКУ ДОБАВИЛИ В КОНЕЦ

    def get_selected_files(self):
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count()) if
                self.list_widget.item(i).isSelected()]