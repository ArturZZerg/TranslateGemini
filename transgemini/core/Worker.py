
import html
import os
import re
import tempfile
import time
import traceback
import uuid
import zipfile
from pathlib import Path

from PyQt6 import  QtCore

from google.api_core import exceptions as google_exceptions
from google import generativeai as genai
from lxml import etree

from transgemini.config import *

from transgemini.core.OperationCancelledError import OperationCancelledError

from transgemini.core.epub_builder import write_to_epub
from transgemini.core.fb2_builder import write_to_fb2
from transgemini.core.html_builder import write_to_html
from transgemini.core.parser import process_html_images, read_docx_with_images, write_markdown_to_docx
from transgemini.core.utils import create_image_placeholder, find_image_placeholders, format_size, \
    split_text_into_chunks, add_translated_suffix

from concurrent.futures import ThreadPoolExecutor, as_completed, CancelledError


class Worker(QtCore.QObject):
    file_progress = QtCore.pyqtSignal(int)
    chunk_progress = QtCore.pyqtSignal(str, int, int)
    current_file_status = QtCore.pyqtSignal(str)
    log_message = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(int, int, list)
    total_tasks_calculated = QtCore.pyqtSignal(int)

    def __init__(self, api_key, out_folder, prompt_template, files_to_process_data,
                 model_config, max_concurrent_requests, output_format,
                 chunking_enabled_gui, chunk_limit, chunk_window,
                 temperature, chunk_delay_seconds, proxy_string=None):  # <-- Добавлен proxy_string
        super().__init__()
        self.api_key = api_key
        self.out_folder = out_folder
        self.prompt_template = prompt_template
        self.files_to_process_data = files_to_process_data
        self.model_config = model_config
        self.max_concurrent_requests = max_concurrent_requests
        self.output_format = output_format
        self.chunking_enabled_gui = chunking_enabled_gui
        self.chunk_limit = chunk_limit
        self.chunk_window = chunk_window
        self.temperature = temperature  # <-- Сохраняем температуру
        self.chunk_delay_seconds = chunk_delay_seconds  # <-- Сохраняем новую настройку
        self.proxy_string = proxy_string  # <-- Сохраняем строку прокси

        self.is_cancelled = False
        self.is_finishing = False  # <--- НОВЫЙ ФЛАГ
        self._critical_error_occurred = False
        self.model = None
        self.executor = None
        self.epub_build_states = {}
        self.total_tasks = 0
        self.processed_task_count = 0
        self.success_count = 0
        self.error_count = 0
        self.errors_list = []

    def finish_processing(self):  # <--- ВОТ ЭТОТ МЕТОД
        if not self.is_finishing and not self.is_cancelled:  # Не устанавливать, если уже отменяется
            self.log_message.emit("[SIGNAL] Получен сигнал ЗАВЕРШЕНИЯ (Worker.finish_processing)...")
            self.is_finishing = True

    def setup_client(self):
        """Initializes the Gemini API client and configures proxy."""
        try:
            if not self.api_key: raise ValueError("API ключ не предоставлен.")

            # --- НАЧАЛО ИЗМЕНЕНИЙ ДЛЯ ПРОКСИ ---
            # Сначала очистим переменные окружения, чтобы избежать конфликтов
            # с предыдущими запусками или системными настройками, если мы собираемся
            # использовать PySocks.
            if 'HTTP_PROXY' in os.environ: os.environ.pop('HTTP_PROXY')
            if 'HTTPS_PROXY' in os.environ: os.environ.pop('HTTPS_PROXY')
            applied_proxy_method = "None"
            proxy_url_for_env = None  # URL, который будет установлен в env, если это HTTP/S

            if self.proxy_string and self.proxy_string.strip():
                proxy_url_config = self.proxy_string.strip()

                # Автокоррекция socks5(h) -> socks5h
                if proxy_url_config.lower().startswith("socks5(h)://"):
                    corrected_url = "socks5h://" + proxy_url_config[len("socks5(h)://"):]
                    self.log_message.emit(
                        f"[INFO] Proxy scheme '{proxy_url_config}' auto-corrected to '{corrected_url}'.")
                    proxy_url_config = corrected_url

                parsed_url = None
                try:
                    from urllib.parse import urlparse
                    parsed_url = urlparse(proxy_url_config)
                except Exception as e_parse_url:
                    self.log_message.emit(f"[ERROR] Could not parse proxy URL '{proxy_url_config}': {e_parse_url}")
                    # Ошибка парсинга URL, прокси не будет применен.
                    # genai.configure может упасть позже, если сеть недоступна.

                if parsed_url and parsed_url.scheme.lower() in ["socks5", "socks5h"]:
                    try:
                        import socks  # PySocks
                        import socket

                        host = parsed_url.hostname
                        port = parsed_url.port
                        username = parsed_url.username
                        password = parsed_url.password

                        if not host or not port:
                            raise ValueError("SOCKS5/SOCKS5h URL is missing host or port.")

                        is_rdns = parsed_url.scheme.lower() == "socks5h"

                        # Сохраняем оригинальный socket.socket, если он еще не сохранен PySocks
                        if not hasattr(socks, '_original_socket_module_attrs'):
                            socks._original_socket_module_attrs = {'socket': socket.socket}  # Простой способ сохранить
                        elif 'socket' not in socks._original_socket_module_attrs:
                            socks._original_socket_module_attrs['socket'] = socket.socket

                        socks.set_default_proxy(
                            socks.SOCKS5,
                            host,
                            port,
                            rdns=is_rdns,
                            username=username if username else None,
                            password=password if password else None
                        )
                        socket.socket = socks.socksocket  # Monkeypatch

                        applied_proxy_method = f"{parsed_url.scheme.upper()} via PySocks: {host}:{port} (RDNS={is_rdns})"
                        self.log_message.emit(f"[INFO] {applied_proxy_method}")
                        # Важно: Не устанавливаем HTTP_PROXY/HTTPS_PROXY для SOCKS, когда используется PySocks.
                    except ImportError:
                        self.log_message.emit(
                            "[ERROR] PySocks library not found, but SOCKS proxy specified. Please install 'PySocks'.")
                        self.log_message.emit(f"       Прокси '{proxy_url_config}' НЕ будет применен.")
                        applied_proxy_method = "SOCKS Error (PySocks missing)"
                    except ValueError as ve:
                        self.log_message.emit(f"[ERROR] Invalid SOCKS5/SOCKS5h URL '{proxy_url_config}': {ve}")
                        applied_proxy_method = "SOCKS Error (URL invalid)"
                    except Exception as e_socks:
                        self.log_message.emit(
                            f"[ERROR] Failed to set SOCKS proxy '{proxy_url_config}' using PySocks: {e_socks}\n{traceback.format_exc()}")
                        applied_proxy_method = f"SOCKS Error ({type(e_socks).__name__})"

                elif parsed_url and parsed_url.scheme.lower() in ["http", "https"]:
                    proxy_url_for_env = proxy_url_config  # Этот URL пойдет в переменные окружения
                    applied_proxy_method = f"{parsed_url.scheme.upper()} via ENV: {proxy_url_for_env}"
                    self.log_message.emit(f"[INFO] {applied_proxy_method}")
                elif self.proxy_string and self.proxy_string.strip():  # Если URL был, но схема неизвестна или не парсится
                    self.log_message.emit(
                        f"[WARN] Unknown or unparseable proxy URL: '{self.proxy_string.strip()}'. Proxy settings via environment variables will be attempted.")
                    proxy_url_for_env = self.proxy_string.strip()  # Попытка установить "как есть"
                    applied_proxy_method = "Unknown scheme (attempting ENV)"

            # Устанавливаем переменные окружения только если это HTTP/HTTPS прокси
            # или если это была неизвестная схема, где мы пытаемся установить "как есть".
            if proxy_url_for_env:
                os.environ['HTTP_PROXY'] = proxy_url_for_env
                os.environ['HTTPS_PROXY'] = proxy_url_for_env
                if applied_proxy_method == "Unknown scheme (attempting ENV)":
                    self.log_message.emit(f"        (Set HTTP_PROXY/HTTPS_PROXY to '{proxy_url_for_env}')")
            elif not (applied_proxy_method.startswith("SOCKS5 via PySocks") or applied_proxy_method.startswith(
                    "SOCKS5H via PySocks")):
                if self.proxy_string and self.proxy_string.strip() and applied_proxy_method == "None":  # Если был proxy_string, но не распознался
                    self.log_message.emit(
                        f"[WARN] Proxy '{self.proxy_string.strip()}' was provided but not applied due to parsing/scheme issues. No proxy configured by application.")
                elif not (self.proxy_string and self.proxy_string.strip()):
                    self.log_message.emit("[INFO] No proxy string provided. Proxy not configured by the application.")

            # --- КОНЕЦ ИЗМЕНЕНИЙ ДЛЯ ПРОКСИ ---

            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(
                self.model_config['id']
            )

            self.log_message.emit(f"Используется модель: {self.model_config['id']}")
            self.log_message.emit(f"Температура: {self.temperature:.1f}")

            self.log_message.emit(f"Параллельные запросы (макс): {self.max_concurrent_requests}")
            self.log_message.emit(f"Формат вывода: .{self.output_format}")
            self.log_message.emit(f"Таймаут API: {API_TIMEOUT_SECONDS} сек.")
            self.log_message.emit(f"Макс. ретраев при 429/503/500/504: {MAX_RETRIES}")
            if self.model_config.get('post_request_delay', 0) > 0:
                self.log_message.emit(f"Доп. задержка после запроса: {self.model_config['post_request_delay']} сек.")

            model_needs_chunking = self.model_config.get('needs_chunking', False)
            actual_chunking_behavior = "ВКЛЮЧЕН (GUI)" if self.chunking_enabled_gui else "ОТКЛЮЧЕН (GUI)"
            reason = ""
            if self.chunking_enabled_gui:
                chunk_info = f"(Лимит: {self.chunk_limit:,} симв., Окно: {self.chunk_window:,} симв.)"
                if self.chunk_delay_seconds > 0:
                    chunk_info += f", Задержка: {self.chunk_delay_seconds:.1f} сек.)"
                else:
                    chunk_info += ")"
                if model_needs_chunking:
                    reason = f"{chunk_info} - Модель его требует."
                else:
                    reason = f"{chunk_info} - Применяется если файл > лимита."
                if not CHUNK_HTML_SOURCE: reason += " [Чанкинг HTML отключен]"
            else:
                reason = "(ВНИМАНИЕ: модель может требовать чанкинг!)" if model_needs_chunking else "(модель не требует)"
            self.log_message.emit(f"Чанкинг: {actual_chunking_behavior} {reason}")
            self.log_message.emit(f"Формат плейсхолдера изображения: {create_image_placeholder('uuid_example')}")
            self.log_message.emit("Клиент Gemini API успешно настроен.")
            return True
        except Exception as e:
            self.log_message.emit(f"[ERROR] Ошибка настройки клиента Gemini API: {e}\n{traceback.format_exc()}")
            # Попытка отменить monkeypatch, если он был применен
            if 'applied_proxy_method' in locals() and applied_proxy_method.endswith("via PySocks"):
                try:
                    import socket
                    import socks
                    # Простой способ сбросить настройки прокси в PySocks
                    socks.set_default_proxy()
                    # Восстановить оригинальный socket.socket
                    if hasattr(socks,
                               '_original_socket_module_attrs') and 'socket' in socks._original_socket_module_attrs:
                        socket.socket = socks._original_socket_module_attrs['socket']
                        self.log_message.emit("[INFO] Attempted to revert PySocks monkeypatch on error.")
                    else:
                        self.log_message.emit(
                            "[WARN] Could not reliably revert PySocks monkeypatch on error (original socket not found).")
                except Exception as e_revert:
                    self.log_message.emit(f"[WARN] Error trying to revert PySocks monkeypatch: {e_revert}")
            # Очищаем переменные окружения на всякий случай, если они были установлены ошибочно
            if 'HTTP_PROXY' in os.environ: os.environ.pop('HTTP_PROXY')
            if 'HTTPS_PROXY' in os.environ: os.environ.pop('HTTPS_PROXY')
            return False

    def _generate_content_with_retry(self, prompt_for_api, context_log_prefix="API Call"):
        """
        Makes the API call with retry logic for specific errors and applies temperature.
        Checks for cancellation and handles various API errors robustly.
        Simplified version focusing on correct content extraction and error reporting.
        """
        retries = 0
        last_error = None

        safety_settings = [
            {"category": c, "threshold": "BLOCK_NONE"} for c in [
                "HARM_CATEGORY_HARASSMENT",
                "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "HARM_CATEGORY_DANGEROUS_CONTENT",
            ]
        ]

        generation_config_dict = {"temperature": self.temperature}
        if hasattr(genai, 'GenerationConfig'):
            generation_config_obj = genai.GenerationConfig(temperature=self.temperature)
        else:
            generation_config_obj = generation_config_dict

        while retries <= MAX_RETRIES:  # Основной цикл для сетевых ошибок
            if self.is_cancelled:
                raise OperationCancelledError(f"Отменено ({context_log_prefix})")

            response_obj = None
            try:
                response_obj = self.model.generate_content(
                    contents=prompt_for_api,
                    safety_settings=safety_settings,
                    generation_config=generation_config_obj
                )

                translated_text = None
                problem_details = ""

                # 1. Проверяем prompt_feedback (если есть) на явный блок
                if hasattr(response_obj, 'prompt_feedback') and response_obj.prompt_feedback:
                    if hasattr(response_obj.prompt_feedback,
                               'block_reason') and response_obj.prompt_feedback.block_reason:
                        block_reason_name = str(response_obj.prompt_feedback.block_reason)
                        if block_reason_name not in ["BLOCK_REASON_UNSPECIFIED", "0"]:
                            problem_details = f"Запрос заблокирован API (Prompt Feedback): {block_reason_name}. Full Feedback: {str(response_obj.prompt_feedback)}"
                            self.log_message.emit(f"[API BLOCK] {context_log_prefix}: {problem_details}")
                            raise RuntimeError(problem_details)  # Это фатальная ошибка для данного запроса

                # 2. Проверяем кандидатов
                if hasattr(response_obj, 'candidates') and response_obj.candidates:
                    candidate = response_obj.candidates[0]
                    candidate_finish_reason = getattr(candidate, 'finish_reason', None)

                    # Проверяем на проблемные finish_reason
                    # Важно: FinishReason может быть объектом enum или числом.
                    # В вашем логе 'FinishReason.PROHIBITED_CONTENT' - это объект.
                    # finish_reason == 8 также был 'OTHER' или проблемой.

                    finish_reason_name = ""
                    if candidate_finish_reason is not None:
                        try:  # Пытаемся получить имя из enum, если это объект enum
                            finish_reason_name = candidate_finish_reason.name
                        except AttributeError:  # Если это число или строка
                            finish_reason_name = str(candidate_finish_reason)

                    # Список "плохих" причин завершения (можно расширить)
                    # PROHIBITED_CONTENT было в вашем логе, SAFETY - стандартная, OTHER(8) - тоже проблема
                    bad_finish_reasons_names = ["SAFETY", "PROHIBITED_CONTENT", "RECITATION", "OTHER"]
                    # Числовые эквиваленты (если SDK возвращает числа)
                    bad_finish_reasons_numbers_str = ["2", "3", "4", "8"]  # "2" для SAFETY, "8" для OTHER и т.д.

                    if finish_reason_name.upper() in bad_finish_reasons_names or \
                            finish_reason_name in bad_finish_reasons_numbers_str:
                        problem_details = (f"Проблема с генерацией контента у кандидата. "
                                           f"Finish Reason: {finish_reason_name}. "
                                           f"Safety Ratings: {getattr(candidate, 'safety_ratings', 'N/A')}")
                        self.log_message.emit(f"[API CONTENT ISSUE] {context_log_prefix}: {problem_details}")
                        raise RuntimeError(problem_details)  # Фатально для этого запроса

                    # Если finish_reason хороший, пытаемся извлечь текст
                    if hasattr(candidate, 'content') and hasattr(candidate.content,
                                                                 'parts') and candidate.content.parts:
                        text_parts = [part.text for part in candidate.content.parts if hasattr(part, 'text')]
                        if text_parts:
                            translated_text = "".join(text_parts)

                # 3. Если текст не извлечен из кандидата, пробуем response.text (с осторожностью)
                if translated_text is None:
                    if hasattr(response_obj, 'text'):
                        try:
                            # Эта строка вызывала ValueError в вашем случае
                            # Если finish_reason был плохим, мы уже должны были выйти с RuntimeError
                            # Значит, если мы здесь, finish_reason должен быть нормальным (STOP, UNSPECIFIED, MAX_TOKENS)
                            # но .text все равно может вызвать ошибку, если Parts пустые по другой причине.
                            current_text = response_obj.text  # Попытка доступа
                            if current_text is not None:  # Убедимся, что это не None
                                translated_text = current_text
                            else:  # .text вернул None, но явной ошибки не было
                                problem_details = ("response.text вернул None, но finish_reason был нормальным. "
                                                   f"Кандидаты: {getattr(response_obj, 'candidates', 'N/A')}")
                                self.log_message.emit(f"[API CONTENT WARNING] {context_log_prefix}: {problem_details}")
                                raise RuntimeError(problem_details)
                        except ValueError as ve:  # Перехватываем ValueError от response.text
                            problem_details = (f"Ошибка ValueError при доступе к response.text: {ve}. "
                                               f"FinishReason (из лога): {finish_reason_name if 'finish_reason_name' in locals() else 'неизвестно'}. "
                                               f"Кандидаты: {getattr(response_obj, 'candidates', 'N/A')}")
                            self.log_message.emit(f"[API CONTENT ERROR] {context_log_prefix}: {problem_details}")
                            raise RuntimeError(problem_details) from ve  # Перевыбрасываем с деталями

                if translated_text is None:  # Если текст так и не получен
                    problem_details = (f"Не удалось извлечь текст из ответа API. "
                                       f"FinishReason (из лога): {finish_reason_name if 'finish_reason_name' in locals() else 'неизвестно'}. "
                                       f"Кандидаты: {getattr(response_obj, 'candidates', 'N/A')}")
                    self.log_message.emit(f"[API CONTENT FAIL] {context_log_prefix}: {problem_details}")
                    raise RuntimeError(problem_details)

                # Если все хорошо, и текст получен:
                delay_needed = self.model_config.get('post_request_delay', 0)
                if delay_needed > 0:
                    self.log_message.emit(f"[INFO] {context_log_prefix}: Применяем задержку {delay_needed} сек...")
                    slept_time = 0
                    while slept_time < delay_needed:
                        if self.is_cancelled: raise OperationCancelledError("Отменено во время пост-задержки")
                        time.sleep(1);
                        slept_time += 1
                return translated_text

            except (google_exceptions.ResourceExhausted,
                    google_exceptions.DeadlineExceeded,
                    google_exceptions.ServiceUnavailable,
                    google_exceptions.InternalServerError,
                    google_exceptions.RetryError
                    ) as retryable_error:
                error_code_map = {
                    google_exceptions.ResourceExhausted: "429 Limit",
                    google_exceptions.ServiceUnavailable: "503 Unavailable",
                    google_exceptions.InternalServerError: "500 Internal",
                    google_exceptions.DeadlineExceeded: "504 Timeout",
                    google_exceptions.RetryError: "Retry Failed"
                }
                error_code = error_code_map.get(type(retryable_error), "API Transient")
                if isinstance(retryable_error, google_exceptions.RetryError) and retryable_error.__cause__:
                    nested_code = error_code_map.get(type(retryable_error.__cause__), "Unknown")
                    error_code = f"Retry Failed ({nested_code})"

                last_error = retryable_error
                retries += 1
                error_details_log = f"  Полная ошибка: {str(last_error)}\n  Args: {getattr(last_error, 'args', 'N/A')}"
                if hasattr(last_error, 'debug_error_string') and callable(
                        getattr(last_error, 'debug_error_string', None)):
                    error_details_log += f"\n  Debug String: {last_error.debug_error_string()}"

                if retries > MAX_RETRIES:
                    self.log_message.emit(
                        f"[FAIL] {context_log_prefix}: Ошибка {error_code}, исчерпаны попытки ({MAX_RETRIES}).\n{error_details_log}")
                    raise last_error
                else:
                    delay = RETRY_DELAY_SECONDS * (2 ** (retries - 1))
                    self.log_message.emit(
                        f"[WARN] {context_log_prefix}: Ошибка {error_code}. Попытка {retries}/{MAX_RETRIES} через {delay} сек...\n{error_details_log}")
                    slept_time = 0
                    while slept_time < delay:
                        if self.is_cancelled:
                            raise OperationCancelledError(f"Отменено во время ожидания retry ({error_code})")
                        time.sleep(1);
                        slept_time += 1
                    continue

            except (google_exceptions.InvalidArgument,
                    google_exceptions.PermissionDenied,
                    google_exceptions.Unauthenticated,
                    google_exceptions.NotFound
                    ) as non_retryable_error:
                error_type_name = type(non_retryable_error).__name__
                self.log_message.emit(
                    f"[API FAIL] {context_log_prefix}: Неисправимая ошибка API ({error_type_name}): {non_retryable_error}\n"
                    f"  Args: {getattr(non_retryable_error, 'args', 'N/A')}"
                )
                raise non_retryable_error

            except RuntimeError as rte:  # Перехватываем наши собственные RuntimeError (проблемы с контентом)
                # Эти ошибки уже залогированы там, где они возникли
                # Если это была первая сетевая попытка (retries == 0) и мы хотим дать шанс основному циклу ретраев,
                # то нужно увеличить retries и continue, если retries < MAX_RETRIES.
                # Но для PROHIBITED_CONTENT и SAFETY это не поможет.
                # Для OTHER - может быть.
                # Пока что, если RuntimeError из-за контента, считаем это фатальной ошибкой для этой сетевой попытки.
                # Основной цикл ретраев сработает, если это была не последняя сетевая попытка.
                if "Запрос заблокирован API" in str(rte) or "Критическая причина завершения" in str(
                        rte) or "Проблема с генерацией контента у кандидата" in str(rte):
                    # Для этих случаев ретрай бессмысленен
                    raise rte  # Перевыбрасываем

                # Для других RuntimeError (например, "Не удалось извлечь текст...") можно попробовать сетевой ретрай, если он есть
                if retries < MAX_RETRIES:
                    self.log_message.emit(
                        f"[WARN] {context_log_prefix}: Ошибка контента ({rte}). Попытка сетевого ретрая {retries + 1}/{MAX_RETRIES}...")
                    last_error = rte  # Сохраняем ошибку
                    retries += 1
                    # Задержка перед следующим сетевым ретраем
                    delay = RETRY_DELAY_SECONDS * (2 ** (retries - 1))  # Используем уже инкрементированный retries
                    self.log_message.emit(f"       Ожидание {delay} сек перед сетевым ретраем...")
                    slept_time_rte = 0
                    while slept_time_rte < delay:
                        if self.is_cancelled: raise OperationCancelledError("Отменено во время ожидания RTE-ретрая")
                        time.sleep(1);
                        slept_time_rte += 1
                    continue
                else:  # Если сетевые ретраи исчерпаны
                    raise rte  # Перевыбрасываем исходную ошибку контента


            except Exception as e:  # Общий обработчик
                error_type_name = type(e).__name__
                tb_str = traceback.format_exc()
                response_details_log = ""
                # ... (блок извлечения деталей из response_obj, как был раньше)
                if 'response_obj' in locals() and response_obj is not None:
                    fr_name = "N/A";
                    pf_log = "N/A";
                    cand_log = "N/A"
                    try:
                        if hasattr(response_obj, 'prompt_feedback'): pf_log = str(response_obj.prompt_feedback)
                        if hasattr(response_obj, 'candidates') and response_obj.candidates:
                            cand_log = str(response_obj.candidates)
                            if hasattr(response_obj.candidates[0], 'finish_reason'):
                                # Попробуем получить .name, если это enum, иначе строку
                                raw_fr = response_obj.candidates[0].finish_reason
                                fr_name = getattr(raw_fr, 'name', str(raw_fr))
                        response_details_log = (f"\n  Детали ответа (если доступны):\n"
                                                f"    FinishReason: {fr_name}\n"
                                                f"    Prompt Feedback: {pf_log}\n"
                                                f"    Candidates: {cand_log}")
                    except Exception:
                        response_details_log = "\n  (Не удалось получить доп. детали ответа при ошибке)"
                self.log_message.emit(
                    f"[CALL ERROR] {context_log_prefix}: Неожиданная ошибка ({error_type_name}): {e}\n"
                    f"  Args: {getattr(e, 'args', 'N/A')}"
                    f"{response_details_log}\n{tb_str}"
                )
                raise e

        # Если вышли из цикла без return (т.е. все MAX_RETRIES исчерпаны)
        final_error = last_error if last_error else RuntimeError(
            f"Неизвестная ошибка API после {MAX_RETRIES} ретраев ({context_log_prefix}).")
        self.log_message.emit(f"[FAIL] {context_log_prefix}: Исчерпаны все попытки. Последняя ошибка: {final_error}")
        raise final_error

    def process_single_chunk(self, chunk_text, base_filename_for_log, chunk_index, total_chunks):
        """Processes a single chunk of text by calling the API."""
        if self.is_cancelled:
            raise OperationCancelledError(f"Отменено перед чанком {chunk_index + 1}/{total_chunks}")
        chunk_log_prefix = f"{base_filename_for_log} [Chunk {chunk_index + 1}/{total_chunks}]"
        prompt_for_chunk = self.prompt_template.replace("{text}", chunk_text)
        try:

            placeholders_before = find_image_placeholders(chunk_text)
            placeholders_before_uuids = {p[1] for p in placeholders_before}

            if placeholders_before:
                self.log_message.emit(
                    f"[INFO] {chunk_log_prefix}: Отправка чанка с {len(placeholders_before)} плейсхолдерами (UUIDs: {sorted(list(placeholders_before_uuids))}).")

            translated_chunk = self._generate_content_with_retry(prompt_for_chunk, chunk_log_prefix)

            translated_chunk = html.unescape(translated_chunk)

            placeholders_after_translation_raw = find_image_placeholders(translated_chunk)

            newly_appeared_placeholders_tags_to_remove = []
            if placeholders_after_translation_raw:  # Только если в переведенном тексте вообще есть плейсхолдеры
                for p_tag, p_uuid in placeholders_after_translation_raw:
                    if p_uuid not in placeholders_before_uuids:
                        newly_appeared_placeholders_tags_to_remove.append(p_tag)  # Собираем именно теги для удаления

            if newly_appeared_placeholders_tags_to_remove:
                self.log_message.emit(
                    f"[WARN] {chunk_log_prefix}: Обнаружены новые плейсхолдеры ({len(newly_appeared_placeholders_tags_to_remove)} шт.) после перевода, которых не было в оригинале. Они будут удалены.")
                for p_tag_to_remove in newly_appeared_placeholders_tags_to_remove:
                    match_uuid_in_tag = re.search(r"<\|\|" + IMAGE_PLACEHOLDER_PREFIX + r"([a-f0-9]{32})\|\|>",
                                                  p_tag_to_remove)
                    uuid_for_log = match_uuid_in_tag.group(1) if match_uuid_in_tag else "неизвестный UUID"
                    self.log_message.emit(f"  - Удаляется новый плейсхолдер: {p_tag_to_remove} (UUID: {uuid_for_log})")
                    translated_chunk = translated_chunk.replace(p_tag_to_remove, "")

            placeholders_after_cleaning = find_image_placeholders(translated_chunk)
            placeholders_after_cleaning_uuids = {p[1] for p in placeholders_after_cleaning}

            if len(placeholders_before) != len(placeholders_after_cleaning):
                self.log_message.emit(
                    f"[WARN] {chunk_log_prefix}: Количество плейсхолдеров ИЗМЕНИЛОСЬ! (Оригинал: {len(placeholders_before)}, После перевода и очистки: {len(placeholders_after_cleaning)})")
                self.log_message.emit(f"  Оригинальные UUIDs: {sorted(list(placeholders_before_uuids))}")
                self.log_message.emit(f"  Итоговые UUIDs: {sorted(list(placeholders_after_cleaning_uuids))}")

            elif placeholders_before:  # Если были плейсхолдеры и количество совпало

                if placeholders_before_uuids != placeholders_after_cleaning_uuids:
                    self.log_message.emit(
                        f"[WARN] {chunk_log_prefix}: Набор UUID плейсхолдеров ИЗМЕНИЛСЯ (даже после очистки)! (Оригинал: {sorted(list(placeholders_before_uuids))}, Итог: {sorted(list(placeholders_after_cleaning_uuids))})")

                if not all(p[0].startswith("<||") and p[0].endswith("||>") and len(p[1]) == 32 for p in
                           placeholders_after_cleaning):
                    self.log_message.emit(
                        f"[WARN] {chunk_log_prefix}: Плейсхолдеры в итоговом тексте выглядят поврежденными.")

            self.log_message.emit(f"[INFO] {chunk_log_prefix}: Чанк успешно переведен и обработан.")
            return chunk_index, translated_chunk
        except OperationCancelledError as oce:
            self.log_message.emit(f"[CANCELLED] {chunk_log_prefix}: Обработка чанка отменена.");
            raise oce

        except Exception as e:
            self.log_message.emit(f"[FAIL] {chunk_log_prefix}: Ошибка API вызова/обработки чанка: {e}");
            raise e  # Re-raise

    def process_single_epub_html(self, original_epub_path, html_path_in_epub):
        """
        Processes a single HTML file from an EPUB for EPUB->EPUB mode.
        Returns data for building the EPUB, including original content if translation fails or finishing.
        """
        log_prefix = f"{os.path.basename(original_epub_path)} -> {html_path_in_epub}"

        if self.is_cancelled:
            # Возвращаем False, чтобы эта задача не считалась успешной для сборки EPUB
            return False, html_path_in_epub, None, None, False, f"Отменено перед началом: {log_prefix}"

        # Если "Завершить" вызвано до начала обработки этого HTML, используем оригинал
        if self.is_finishing:
            self.log_message.emit(
                f"[FINISHING] {log_prefix}: HTML часть пропущена (режим завершения). Попытка использовать оригинал.")
            self.chunk_progress.emit(log_prefix, 0, 0)
            # Пытаемся прочитать оригинал, чтобы сборка EPUB могла его использовать
            try:
                with zipfile.ZipFile(original_epub_path, 'r') as epub_zip_orig:
                    original_html_bytes_for_finish = epub_zip_orig.read(html_path_in_epub)
                # Возвращаем True, чтобы эта оригинальная часть была включена в сборку
                return True, html_path_in_epub, original_html_bytes_for_finish, {}, True, "Пропущено (режим завершения)"
            except Exception as e_read_orig:
                self.log_message.emit(
                    f"[FINISHING-ERROR] {log_prefix}: Не удалось прочитать оригинал при завершении: {e_read_orig}")
                # Возвращаем False, так как даже оригинал не удалось получить
                return False, html_path_in_epub, None, None, False, f"Пропущено (режим завершения, оригинал недоступен: {e_read_orig})"

        with tempfile.TemporaryDirectory(prefix=f"translator_epub_{uuid.uuid4().hex[:8]}_") as temp_dir:
            image_map = {}
            content_with_placeholders = ""
            original_html_bytes = None

            try:
                self.log_message.emit(f"Обработка EPUB HTML: {log_prefix}")

                with zipfile.ZipFile(original_epub_path, 'r') as epub_zip:
                    try:
                        original_html_bytes = epub_zip.read(html_path_in_epub)
                        file_size_bytes = len(original_html_bytes)
                        original_html_str = ""
                        try:
                            original_html_str = original_html_bytes.decode('utf-8')
                        except UnicodeDecodeError:
                            try:
                                original_html_str = original_html_bytes.decode('cp1251'); self.log_message.emit(
                                    f"[WARN] {log_prefix}: Использовано cp1251.")
                            except UnicodeDecodeError:
                                original_html_str = original_html_bytes.decode('latin-1',
                                                                               errors='ignore'); self.log_message.emit(
                                    f"[WARN] {log_prefix}: Использовано latin-1 (с потерями).")

                        if not original_html_str and original_html_bytes:
                            self.log_message.emit(
                                f"[ERROR] {log_prefix}: Не удалось декодировать HTML. Используется оригинал.")
                            return True, html_path_in_epub, original_html_bytes, {}, True, "Ошибка декодирования HTML"

                        processing_context = (epub_zip, html_path_in_epub)
                        content_with_placeholders = process_html_images(original_html_str, processing_context, temp_dir,
                                                                        image_map)
                        original_content_len_text = len(content_with_placeholders)
                        self.log_message.emit(
                            f"[INFO] {log_prefix}: HTML прочитан/обработан (Размер: {format_size(file_size_bytes)}, {original_content_len_text:,} симв. текста, {len(image_map)} изобр.).")

                    except KeyError:
                        return False, html_path_in_epub, None, None, False, f"Ошибка: HTML '{html_path_in_epub}' не найден в EPUB."
                    except Exception as html_proc_err:
                        self.log_message.emit(
                            f"[ERROR] {log_prefix}: Ошибка подготовки HTML для перевода: {html_proc_err}. Используется оригинал (если доступен).")
                        if original_html_bytes:
                            return True, html_path_in_epub, original_html_bytes, image_map or {}, True, f"Ошибка обработки HTML: {html_proc_err}"
                        else:
                            return False, html_path_in_epub, None, None, False, f"Критическая ошибка обработки HTML '{html_path_in_epub}': {html_proc_err}"

                if not content_with_placeholders.strip():
                    self.log_message.emit(f"[INFO] {log_prefix}: Пропущен (пустой контент после извлечения текста).")
                    return True, html_path_in_epub, original_html_bytes if original_html_bytes is not None else b"", image_map or {}, True, "Пустой контент после обработки"

                chunks = []
                can_chunk_html = CHUNK_HTML_SOURCE
                potential_chunking = self.chunking_enabled_gui and original_content_len_text > self.chunk_limit

                if potential_chunking and not can_chunk_html:
                    chunks.append(content_with_placeholders)
                    self.log_message.emit(
                        f"[INFO] {log_prefix}: Чанкинг HTML отключен, отправляется целиком ({original_content_len_text:,} симв.).")
                elif potential_chunking and can_chunk_html:
                    self.log_message.emit(
                        f"[INFO] {log_prefix}: Контент ({original_content_len_text:,} симв.) > лимита ({self.chunk_limit:,}). Разделяем...")
                    chunks = split_text_into_chunks(content_with_placeholders, self.chunk_limit, self.chunk_window,
                                                    MIN_CHUNK_SIZE)
                    self.log_message.emit(f"[INFO] {log_prefix}: Разделено на {len(chunks)} чанков.")
                    if not chunks:
                        self.log_message.emit(
                            f"[WARN] {log_prefix}: Ошибка разделения на чанки (пустой результат). Используется оригинал.")
                        return True, html_path_in_epub, original_html_bytes, image_map or {}, True, "Ошибка разделения на чанки"
                else:
                    chunks.append(content_with_placeholders)
                    self.log_message.emit(
                        f"[INFO] {log_prefix}: Контент ({original_content_len_text:,} симв.) отправляется целиком (чанкинг выкл/не нужен/HTML выкл).")

                if not chunks:
                    self.log_message.emit(f"[ERROR] {log_prefix}: Нет чанков для обработки. Используется оригинал.")
                    return True, html_path_in_epub, original_html_bytes, image_map or {}, True, "Ошибка подготовки чанков"

                translated_chunks_map = {}
                total_chunks = len(chunks)
                self.chunk_progress.emit(log_prefix, 0, total_chunks)

                translation_failed_for_any_chunk = False
                first_chunk_error_msg = None
                processed_current_chunk_in_finishing_mode_epub = False

                for i, chunk_text in enumerate(chunks):
                    if self.is_cancelled:
                        raise OperationCancelledError(f"Отменено перед чанком {i + 1} для {log_prefix}")

                    if self.is_finishing and processed_current_chunk_in_finishing_mode_epub:
                        self.log_message.emit(
                            f"[FINISHING] {log_prefix}: Пропуск оставшихся чанков HTML ({i + 1} из {total_chunks}).")
                        break
                    try:
                        _, translated_text_chunk = self.process_single_chunk(chunk_text, log_prefix, i, total_chunks)
                        translated_chunks_map[i] = translated_text_chunk
                        self.chunk_progress.emit(log_prefix, i + 1, total_chunks)

                        if self.chunk_delay_seconds > 0 and (i < total_chunks - 1):
                            delay_val = self.chunk_delay_seconds
                            self.log_message.emit(
                                f"[INFO] {log_prefix}: Задержка {delay_val:.1f} сек. перед следующим чанком HTML...")
                            start_sleep = time.monotonic()
                            while time.monotonic() - start_sleep < delay_val:
                                if self.is_cancelled: raise OperationCancelledError(
                                    "Отменено во время задержки между чанками HTML")
                                time.sleep(min(0.1, delay_val - (time.monotonic() - start_sleep)))

                        if self.is_finishing:  # Если флаг установился во время или после этого чанка
                            self.log_message.emit(
                                f"[FINISHING] {log_prefix}: Чанк HTML {i + 1}/{total_chunks} обработан. Завершение обработки этой HTML части...")
                            processed_current_chunk_in_finishing_mode_epub = True
                            if i < total_chunks - 1:  # Если это не последний чанк, то следующий точно пропускаем
                                pass
                            else:  # Это был последний чанк
                                break

                    except OperationCancelledError as oce_chunk:
                        raise oce_chunk
                    except Exception as e_chunk:
                        translation_failed_for_any_chunk = True
                        first_chunk_error_msg = f"Ошибка перевода чанка HTML {i + 1}: {e_chunk}"
                        self.log_message.emit(f"[FAIL] {log_prefix}: {first_chunk_error_msg}")
                        if self.is_finishing:
                            self.log_message.emit(
                                f"[FINISHING-ERROR] {log_prefix}: Ошибка на чанке HTML {i + 1} во время завершения. Попытка использовать предыдущие или оригинал.")
                            processed_current_chunk_in_finishing_mode_epub = True
                        break

                if self.is_cancelled:  # Если отмена произошла во время цикла чанков
                    raise OperationCancelledError(f"Отменено во время или после обработки чанков для {log_prefix}")

                if translation_failed_for_any_chunk and not translated_chunks_map:  # Ошибка на первом же чанке или ничего не собрано
                    self.log_message.emit(
                        f"[WARN] {log_prefix}: Не удалось перевести HTML. Используется оригинал. Причина: {first_chunk_error_msg or 'Неизвестная ошибка чанка HTML'}")
                    self.chunk_progress.emit(log_prefix, 0, 0)
                    return True, html_path_in_epub, original_html_bytes, image_map or {}, True, first_chunk_error_msg

                if not translated_chunks_map:  # Если карта пуста (может быть, если is_finishing и первый чанк не успел)
                    if self.is_finishing:
                        self.log_message.emit(
                            f"[FINISHING] {log_prefix}: Нет переведенных чанков для HTML. Используется оригинал.")
                        self.chunk_progress.emit(log_prefix, 0, 0)
                        return True, html_path_in_epub, original_html_bytes, image_map or {}, True, "Пропущено (режим завершения, нет данных для HTML)"
                    # Если не is_finishing и translated_chunks_map пуст, это должно было быть обработано выше
                    # как ошибка чанкинга или пустой контент. Но на всякий случай:
                    self.log_message.emit(
                        f"[ERROR] {log_prefix}: Нет переведенных чанков для HTML по неизвестной причине. Используется оригинал.")
                    return True, html_path_in_epub, original_html_bytes, image_map or {}, True, "Нет переведенных чанков HTML"

                # Если есть какие-то чанки в translated_chunks_map
                final_translated_content_str = "\n".join(
                    translated_chunks_map[i] for i in sorted(translated_chunks_map.keys())).strip()

                warning_msg_for_return = None
                if self.is_finishing and len(translated_chunks_map) < total_chunks:
                    self.log_message.emit(
                        f"[FINISHING] {log_prefix}: HTML часть переведена частично ({len(translated_chunks_map)}/{total_chunks} чанков).")
                    warning_msg_for_return = "Частично переведено (завершение)"
                elif translation_failed_for_any_chunk and translated_chunks_map:  # Была ошибка, но есть что сохранить
                    self.log_message.emit(
                        f"[WARN] {log_prefix}: HTML часть переведена частично из-за ошибки ({len(translated_chunks_map)}/{total_chunks} чанков). Причина первой ошибки: {first_chunk_error_msg}")
                    warning_msg_for_return = f"Частично из-за ошибки: {first_chunk_error_msg or 'N/A'}"

                self.log_message.emit(
                    f"[SUCCESS/PARTIAL] {log_prefix}: HTML часть (возможно, частично) подготовлена для сборки EPUB.")
                self.chunk_progress.emit(log_prefix, len(translated_chunks_map), total_chunks)
                return True, html_path_in_epub, final_translated_content_str, image_map or {}, False, warning_msg_for_return

            except OperationCancelledError as oce:
                self.log_message.emit(f"[CANCELLED] {log_prefix}: Обработка HTML части прервана ({oce})")
                self.chunk_progress.emit(log_prefix, 0, 0)
                return False, html_path_in_epub, None, None, False, str(oce)

            except Exception as e_outer:
                safe_log_prefix_on_error = f"{os.path.basename(original_epub_path)} -> {html_path_in_epub}"
                detailed_error_msg = f"[CRITICAL] {safe_log_prefix_on_error}: Неожиданная ошибка при обработке HTML файла: {type(e_outer).__name__}: {e_outer}"
                tb_str = traceback.format_exc()
                self.log_message.emit(detailed_error_msg + "\n" + tb_str)
                self.chunk_progress.emit(safe_log_prefix_on_error, 0, 0)
                final_error_msg_return = f"Неожиданная ошибка HTML ({safe_log_prefix_on_error}): {type(e_outer).__name__}"
                if original_html_bytes is not None:
                    self.log_message.emit(
                        f"[WARN] {log_prefix}: Использование оригинала из-за неожиданной ошибки: {final_error_msg_return}")
                    return True, html_path_in_epub, original_html_bytes, image_map or {}, True, final_error_msg_return
                else:
                    return False, html_path_in_epub, None, None, False, f"Критическая ошибка И оригинал не доступен: {final_error_msg_return}"

    def process_single_file(self, file_info_tuple):
        input_type, filepath, epub_html_path_or_none = file_info_tuple
        base_name = os.path.basename(filepath)
        log_prefix = f"{base_name}" + (f" -> {epub_html_path_or_none}" if epub_html_path_or_none else "")
        self.current_file_status.emit(f"Обработка: {log_prefix}")
        self.log_message.emit(f"Начало обработки: {log_prefix}")

        effective_path_obj_for_stem = None
        if input_type == 'epub' and epub_html_path_or_none:
            # Если обрабатывается HTML-часть из EPUB для вывода не в EPUB,
            # имя выходного файла должно базироваться на имени HTML-части.
            effective_path_obj_for_stem = Path(epub_html_path_or_none)
        else:
            # Для других типов ввода (txt, docx) или если это EPUB, но epub_html_path_or_none не указан (маловероятно здесь),
            # базируемся на имени входного файла.
            effective_path_obj_for_stem = Path(filepath)

        # Получаем "чистое" имя файла без всех расширений
        true_stem = effective_path_obj_for_stem.name
        all_suffixes = "".join(effective_path_obj_for_stem.suffixes)
        if all_suffixes:
            true_stem = true_stem.replace(all_suffixes, "")

        if not true_stem:  # Обработка случаев типа ".bashrc" или если имя было пустым
            temp_name = effective_path_obj_for_stem.name
            true_stem = os.path.splitext(temp_name[1:] if temp_name.startswith('.') else temp_name)[0]
            if not true_stem: true_stem = "file"  # Крайний случай

        final_out_filename = f"{true_stem}{TRANSLATED_SUFFIX}.{self.output_format}"
        out_path = os.path.join(self.out_folder, final_out_filename)

        image_map = {};
        temp_dir_obj = None;
        book_title_guess = Path(filepath).stem.replace('_translated', '')

        try:
            with tempfile.TemporaryDirectory(prefix=f"translator_{uuid.uuid4().hex[:8]}_") as temp_dir_path:
                temp_dir_obj = temp_dir_path  # For cleanup check in finally

                original_content = ""

                if input_type == 'txt':
                    with open(filepath, 'r', encoding='utf-8') as f:
                        original_content = f.read()
                elif input_type == 'docx':
                    if not DOCX_AVAILABLE: raise ImportError("python-docx не установлен")
                    original_content = read_docx_with_images(filepath, temp_dir_path, image_map)
                elif input_type == 'epub':  # Это для EPUB -> TXT/DOCX/MD/HTML (не EPUB->EPUB)
                    if not epub_html_path_or_none: raise ValueError("Путь к HTML в EPUB не указан.")
                    if not BS4_AVAILABLE: raise ImportError("beautifulsoup4 не установлен")
                    with zipfile.ZipFile(filepath, 'r') as epub_zip:
                        html_bytes = epub_zip.read(epub_html_path_or_none)

                        html_str = ""
                        try:
                            html_str = html_bytes.decode('utf-8')
                        except UnicodeDecodeError:
                            try:
                                html_str = html_bytes.decode('cp1251', errors='ignore'); self.log_message.emit(
                                    f"[WARN] {log_prefix}: cp1251 для HTML.")
                            except UnicodeDecodeError:
                                html_str = html_bytes.decode('latin-1', errors='ignore'); self.log_message.emit(
                                    f"[WARN] {log_prefix}: latin-1 для HTML.")

                        epub_zip_dir = os.path.dirname(epub_html_path_or_none)
                        processing_context = (epub_zip, epub_html_path_or_none)
                        original_content = process_html_images(html_str, processing_context, temp_dir_path, image_map)
                        book_title_guess = Path(epub_html_path_or_none).stem  # Используем имя HTML файла для заголовка
                else:
                    raise ValueError(f"Неподдерживаемый тип ввода: {input_type}")

                if self.is_cancelled: raise OperationCancelledError("Отменено после чтения файла")
                if self.is_finishing and not (
                        input_type == 'epub' and epub_html_path_or_none):  # Если "Завершить" и это не обработка HTML для EPUB-сборки (там своя логика)
                    self.log_message.emit(
                        f"[FINISHING] {log_prefix}: Файл пропущен из-за режима завершения (активирован до начала обработки этого файла).")
                    return file_info_tuple, False, "Пропущено (режим завершения)"
                if not original_content.strip() and not image_map:
                    self.log_message.emit(f"[INFO] {log_prefix}: Пропущен (пустой контент).");
                    return file_info_tuple, True, "Пустой контент"  # Считаем успехом, если пустой

                original_content_len = len(original_content)
                self.log_message.emit(
                    f"[INFO] {log_prefix}: Прочитано ({format_size(original_content_len)} симв., {len(image_map)} изобр.).")

                chunks = []

                can_chunk_this_input = not (input_type == 'epub' and not CHUNK_HTML_SOURCE)

                if self.chunking_enabled_gui and original_content_len > self.chunk_limit and can_chunk_this_input:
                    self.log_message.emit(
                        f"[INFO] {log_prefix}: Контент ({original_content_len:,} симв.) > лимита ({self.chunk_limit:,}). Разделяем...");
                    chunks = split_text_into_chunks(original_content, self.chunk_limit, self.chunk_window,
                                                    MIN_CHUNK_SIZE)
                    self.log_message.emit(f"[INFO] {log_prefix}: Разделено на {len(chunks)} чанков.")
                else:
                    chunks.append(original_content)
                    reason_no_chunk = ""
                    if not self.chunking_enabled_gui:
                        reason_no_chunk = "(чанкинг выключен)"
                    elif original_content_len <= self.chunk_limit:
                        reason_no_chunk = "(размер < лимита)"
                    elif not can_chunk_this_input:
                        reason_no_chunk = "(чанкинг HTML/EPUB отключен)"
                    self.log_message.emit(
                        f"[INFO] {log_prefix}: Контент ({original_content_len:,} симв.) отправляется целиком {reason_no_chunk}.")

                if not chunks:  # Если split_text_into_chunks вернул пустой список
                    self.log_message.emit(
                        f"[WARN] {log_prefix}: Не удалось разделить на чанки (пустой результат). Пропускаем.");
                    return file_info_tuple, False, "Ошибка разделения на чанки"

                translated_chunks_map = {}
                total_chunks = len(chunks)
                self.chunk_progress.emit(log_prefix, 0, total_chunks)
                processed_current_chunk_in_finishing_mode = False

                for i, chunk_text in enumerate(chunks):
                    if self.is_cancelled: raise OperationCancelledError(f"Отменено перед чанком {i + 1}")

                    # Если режим завершения уже активен и мы не обрабатываем самый первый чанк этого файла,
                    # или если это не первый чанк и режим завершения только что активировался.
                    if self.is_finishing and processed_current_chunk_in_finishing_mode:
                        self.log_message.emit(
                            f"[FINISHING] {log_prefix}: Пропуск оставшихся чанков ({i + 1} из {total_chunks}).")
                        break

                    try:
                        _, translated_text = self.process_single_chunk(chunk_text, log_prefix, i, total_chunks)
                        translated_chunks_map[i] = translated_text
                        self.chunk_progress.emit(log_prefix, i + 1, total_chunks)

                        if self.is_finishing:  # Если флаг установился во время или после этого чанка
                            self.log_message.emit(
                                f"[FINISHING] {log_prefix}: Чанк {i + 1}/{total_chunks} обработан. Завершение обработки файла...")
                            processed_current_chunk_in_finishing_mode = True  # Помечаем, что текущий чанк обработан в режиме завершения
                            # Не выходим из цикла сразу, если это был первый чанк, дадим сохраниться.
                            # Если это не первый чанк, то следующий if self.is_finishing and processed_current_chunk_in_finishing_mode сработает.
                            # Или, если это последний чанк, цикл закончится естественно.
                            if i < total_chunks - 1:  # Если это не последний чанк, и мы в режиме завершения, то следующий точно пропускаем
                                pass  # break будет на следующей итерации
                            else:  # Это был последний чанк, и мы в режиме завершения
                                break


                    except OperationCancelledError as oce:
                        raise oce
                    except Exception as e:
                        if self.is_finishing:  # Если ошибка во время завершения, пытаемся сохранить то, что есть
                            self.log_message.emit(
                                f"[FINISHING-ERROR] {log_prefix}: Ошибка на чанке {i + 1} во время завершения: {e}. Попытка сохранить предыдущие.")
                            processed_current_chunk_in_finishing_mode = True  # Чтобы не продолжать
                            break  # Выходим из цикла чанков, чтобы сохранить то, что есть
                        return file_info_tuple, False, f"Ошибка обработки чанка {i + 1}: {e}"

                # После цикла обработки чанков
                if self.is_cancelled and not translated_chunks_map:
                    raise OperationCancelledError(
                        f"Отменено во время обработки чанков для {log_prefix}, нет данных для сохранения")

                if not translated_chunks_map:
                    if self.is_finishing:  # Если завершаем и для этого файла ничего не успело перевестись
                        self.log_message.emit(
                            f"[FINISHING] {log_prefix}: Нет переведенных чанков для сохранения (режим завершения).")
                        return file_info_tuple, False, "Пропущено (режим завершения, нет данных)"
                    elif original_content.strip() or image_map:  # Если был контент, но не перевелся (и не режим завершения)
                        self.log_message.emit(f"[FAIL] {log_prefix}: Не удалось перевести ни одного чанка.")
                        return file_info_tuple, False, "Ошибка: Не удалось перевести ни одного чанка."
                    else:  # Пустой файл изначально
                        self.log_message.emit(f"[INFO] {log_prefix}: Пропущен (пустой контент).")
                        return file_info_tuple, True, "Пустой контент"

                # Если есть что сохранять (translated_chunks_map не пуст)
                if self.is_finishing and len(translated_chunks_map) < total_chunks:
                    self.log_message.emit(
                        f"[FINISHING] {log_prefix}: Сохранение частично переведенного файла ({len(translated_chunks_map)}/{total_chunks} чанков).")
                elif not self.is_finishing and len(
                        translated_chunks_map) != total_chunks:  # Обычный режим, но не все чанки (ошибка где-то выше не отловлена)
                    return file_info_tuple, False, f"Ошибка: Не все чанки ({len(translated_chunks_map)}/{total_chunks}) были успешно обработаны."

                join_char = "\n\n" if self.output_format in ['txt', 'md'] and len(translated_chunks_map) > 1 else "\n";
                final_translated_content = join_char.join(
                    translated_chunks_map[i] for i in sorted(translated_chunks_map.keys())).strip()

                self.log_message.emit(f"[INFO] {log_prefix}: Запись результата ({self.output_format}) в: {out_path}");
                write_success_log = ""

                content_to_write = final_translated_content
                if self.output_format in ['txt', 'md', 'docx', 'fb2']:
                    content_to_write = re.sub(r'<br\s*/?>', '\n', final_translated_content, flags=re.IGNORECASE)

                try:
                    if self.output_format == 'fb2':
                        if not LXML_AVAILABLE: raise RuntimeError("LXML недоступна для записи FB2.")
                        write_to_fb2(out_path, content_to_write, image_map, book_title_guess);
                        write_success_log = "Файл FB2 сохранен."
                    elif self.output_format == 'docx':
                        if not DOCX_AVAILABLE: raise RuntimeError("python-docx недоступна для записи DOCX.")
                        write_markdown_to_docx(out_path, content_to_write, image_map);
                        write_success_log = "Файл DOCX сохранен."
                    elif self.output_format == 'html':  # Это для write_to_html, не для EPUB

                        write_to_html(out_path, final_translated_content, image_map, book_title_guess);
                        write_success_log = "Файл HTML сохранен."
                    elif self.output_format in ['txt', 'md']:

                        final_text_no_placeholders = content_to_write;
                        markers = find_image_placeholders(final_text_no_placeholders)
                        if markers: self.log_message.emit(
                            f"[INFO] {log_prefix}: Замена {len(markers)} плейсхолдеров для {self.output_format.upper()}...");
                        for tag, uuid_val in markers: replacement = f"[Image: {image_map.get(uuid_val, {}).get('original_filename', uuid_val)}]"; final_text_no_placeholders = final_text_no_placeholders.replace(
                            tag, replacement)
                        with open(out_path, 'w', encoding='utf-8') as f:
                            f.write(
                                final_text_no_placeholders); write_success_log = f"Файл {self.output_format.upper()} сохранен."
                    else:
                        raise RuntimeError(f"Неподдерживаемый формат вывода '{self.output_format}' для записи.")

                    self.log_message.emit(f"[SUCCESS] {log_prefix}: {write_success_log}");
                    self.chunk_progress.emit(log_prefix, total_chunks, total_chunks);
                    return file_info_tuple, True, None
                except Exception as write_err:
                    self.log_message.emit(
                        f"[FAIL] {log_prefix}: Ошибка записи файла {out_path}: {write_err}\n{traceback.format_exc()}"); self.chunk_progress.emit(
                        log_prefix, 0,
                        0); return file_info_tuple, False, f"Ошибка записи {self.output_format.upper()}: {write_err}"

        except FileNotFoundError as fnf_err:  # <--- УБЕДИТЕСЬ, ЧТО ЭТА СТРОКА ИМЕЕТ ТОТ ЖЕ ОТСТУП, ЧТО И ВНЕШНИЙ "try:"
            self.log_message.emit(f"[FAIL] {log_prefix}: Файл не найден: {fnf_err}")
            return file_info_tuple, False, f"Файл не найден: {fnf_err}"
        except IOError as e:  # <--- И ЭТА СТРОКА
            self.log_message.emit(f"[FAIL] {log_prefix}: Ошибка чтения/записи файла: {e}")
            return file_info_tuple, False, f"Ошибка I/O: {e}"
        except OperationCancelledError as oce:  # <--- И ЭТА СТРОКА
            self.log_message.emit(f"[CANCELLED] {log_prefix}: Обработка файла прервана ({oce})")
            self.chunk_progress.emit(log_prefix, 0, 0)
            return file_info_tuple, False, str(oce)
        except Exception as e:  # <--- И ЭТА СТРОКА (общий обработчик для внешнего try)
            self.log_message.emit(
                f"[CRITICAL] {log_prefix}: Неожиданная ошибка обработки файла: {e}\n{traceback.format_exc()}")
            self.chunk_progress.emit(log_prefix, 0, 0)
            return file_info_tuple, False, f"Критическая ошибка файла: {e}"
        finally:  # <--- И БЛОК FINALLY ДЛЯ ВНЕШНЕГО TRY

            if temp_dir_obj and os.path.exists(temp_dir_obj):  # temp_dir_obj был инициализирован ранее
                try:

                    pass  # tempfile.TemporaryDirectory() сам очистит при выходе из 'with'
                except Exception as e_clean:
                    self.log_message.emit(f"[WARN] Не удалось удалить временную папку {temp_dir_obj}: {e_clean}")

    def build_translated_epub(self, original_epub_path, translated_items_list, build_metadata):

        base_name = Path(original_epub_path).name;
        log_prefix = f"EPUB Rebuild: {base_name}"
        self.log_message.emit(f"[INFO] {log_prefix}: Запуск финальной сборки EPUB...")
        self.current_file_status.emit(f"Сборка EPUB: {base_name}...")
        output_filename = add_translated_suffix(base_name);
        output_epub_path = os.path.join(self.out_folder, output_filename)
        book_title_guess = Path(original_epub_path).stem
        if self.is_cancelled: return original_epub_path, False, f"Отменено перед сборкой EPUB: {log_prefix}"
        try:

            success, error = write_to_epub(
                out_path=output_epub_path,
                processed_epub_parts=translated_items_list,
                # <--- ИЗМЕНЕНО 'translated_items' на 'processed_epub_parts'
                original_epub_path=original_epub_path,
                build_metadata=build_metadata,
                book_title_override=book_title_guess
            )

            if success:
                self.log_message.emit(
                    f"[SUCCESS] {log_prefix}: Финальный EPUB успешно сохранен: {output_epub_path}"); self.current_file_status.emit(
                    f"EPUB собран: {base_name}"); return original_epub_path, True, None
            else:
                self.log_message.emit(
                    f"[FAIL] {log_prefix}: Ошибка сборки EPUB: {error}"); self.current_file_status.emit(
                    f"Ошибка сборки EPUB: {base_name}"); return original_epub_path, False, f"Ошибка сборки EPUB: {error}"
        except OperationCancelledError as oce:
            self.log_message.emit(
                f"[CANCELLED] {log_prefix}: Сборка EPUB прервана."); return original_epub_path, False, f"Сборка EPUB отменена: {oce}"
        except Exception as e:
            self.log_message.emit(
                f"[CRITICAL] {log_prefix}: Неожиданная ошибка при сборке EPUB: {e}\n{traceback.format_exc()}"); self.current_file_status.emit(
                f"Критическая ошибка сборки: {base_name}"); return original_epub_path, False, f"Критическая ошибка сборки EPUB: {e}"

    @QtCore.pyqtSlot()
    def run(self):
        if not self.setup_client():
            self.finished.emit(0, 1, ["Критическая ошибка: Не удалось инициализировать Gemini API клиент."])
            return

        is_epub_to_epub_mode = isinstance(self.files_to_process_data, dict)
        self.total_tasks = 0
        self.epub_build_states = {}

        if not is_epub_to_epub_mode:
            self.total_tasks = len(self.files_to_process_data)
        else:
            actual_html_tasks_count = 0
            build_tasks_count = 0
            for epub_path, epub_data in self.files_to_process_data.items():
                html_paths_to_process = epub_data.get('html_paths', [])
                self.epub_build_states[epub_path] = {
                    'pending': set(html_paths_to_process),
                    'results': [],
                    'combined_image_map': {},
                    'future': None,
                    'build_metadata': epub_data['build_metadata'],
                    'failed': False,  # Флаг, если сам EPUB (сборка или критическая ошибка HTML) не удался
                    'processed_build_result': False,
                    'html_errors_count': 0  # Счетчик ошибок именно для HTML-частей этого EPUB
                }
                actual_html_tasks_count += len(html_paths_to_process)
                build_tasks_count += 1
            self.total_tasks = actual_html_tasks_count + build_tasks_count
            if actual_html_tasks_count == 0 and build_tasks_count > 0:
                self.log_message.emit("[INFO] EPUB->EPUB режим: Нет HTML для перевода, только сборка.")

        self.total_tasks_calculated.emit(self.total_tasks)
        if self.total_tasks == 0:
            self.log_message.emit("[WARN] Нет задач для выполнения.")
            self.finished.emit(0, 0, [])
            return

        self.processed_task_count = 0
        self.success_count = 0
        self.error_count = 0
        self.errors_list = []
        self._critical_error_occurred = False
        executor_exception = None

        self.log_message.emit(f"Запуск ThreadPoolExecutor с max_workers={self.max_concurrent_requests}")
        try:
            with ThreadPoolExecutor(max_workers=self.max_concurrent_requests,
                                    thread_name_prefix='TranslateWorker') as self.executor:
                futures = {}

                # 1. Submit initial file/HTML processing tasks
                if not is_epub_to_epub_mode:
                    self.log_message.emit(f"Отправка {self.total_tasks} задач (Стандартный режим)...")
                    for file_info_tuple in self.files_to_process_data:
                        if self.is_cancelled: break  # Прекращаем добавление, если уже отмена
                        # Для 'single_file' режим is_finishing проверяется внутри process_single_file
                        future = self.executor.submit(self.process_single_file, file_info_tuple)
                        futures[future] = {'type': 'single_file', 'info': file_info_tuple}
                else:  # EPUB->EPUB mode
                    self.log_message.emit(f"Отправка задач на обработку HTML для {len(self.epub_build_states)} EPUB...")
                    for epub_path, build_state in self.epub_build_states.items():
                        if self.is_cancelled: break  # Прекращаем, если отмена
                        # Если is_finishing, мы НЕ добавляем новые HTML-задачи в executor,
                        # но существующие (если они были добавлены до is_finishing) должны обработаться.
                        # process_single_epub_html сам вернет оригинал, если is_finishing был установлен до его начала.
                        html_to_submit = list(build_state['pending'])
                        if not html_to_submit:
                            self.log_message.emit(
                                f"[INFO] EPUB {Path(epub_path).name}: Нет HTML для перевода. Сборка будет запущена позже, если потребуется.")
                        else:
                            for html_path in html_to_submit:
                                if self.is_cancelled: break
                                # Здесь не проверяем is_finishing при добавлении, так как
                                # process_single_epub_html обработает это.
                                future = self.executor.submit(self.process_single_epub_html, epub_path, html_path)
                                futures[future] = {'type': 'epub_html', 'epub_path': epub_path, 'html_path': html_path}
                        if self.is_cancelled: break

                initial_futures_list = list(futures.keys())  # Копируем ключи, так как будем изменять futures
                self.log_message.emit(f"Ожидание завершения {len(initial_futures_list)} начальных задач...")

                # 2. Process results of initial tasks (HTML или одиночные файлы)
                for future in as_completed(initial_futures_list):
                    if self._critical_error_occurred:  # Если критическая ошибка, прекращаем всё
                        if future.done() and not future.cancelled():
                            try:
                                future.result()
                            except Exception:
                                pass
                        continue

                    # Если жесткая отмена, не обрабатываем результат, ждем finally
                    if self.is_cancelled:
                        if future.done() and not future.cancelled():
                            try:
                                future.result()
                            except Exception:
                                pass
                        continue

                    task_info = futures.pop(future, None)  # Удаляем из словаря
                    if not task_info: continue

                    task_type = task_info['type']
                    status_msg_prefix = "Завершение: "
                    if task_type == 'single_file':
                        status_msg_prefix += Path(task_info['info'][1]).name
                    elif task_type == 'epub_html':
                        status_msg_prefix += f"{Path(task_info['epub_path']).name} -> {task_info['html_path']}"
                    self.current_file_status.emit(status_msg_prefix + "...")

                    try:
                        result = future.result()  # Получаем результат или исключение

                        if task_type == 'single_file':
                            file_info_tuple, success, error_message = result
                            self.processed_task_count += 1
                            if success:
                                self.success_count += 1
                            else:
                                self.error_count += 1
                                err_detail = f"{Path(file_info_tuple[1]).name}: {error_message or 'Неизвестная ошибка'}"
                                self.errors_list.append(err_detail);
                                self.log_message.emit(f"[FAIL] {err_detail}")
                            self.file_progress.emit(self.processed_task_count)

                        elif task_type == 'epub_html':
                            epub_path = task_info['epub_path']
                            html_path = task_info['html_path']
                            build_state = self.epub_build_states.get(epub_path)
                            if not build_state or build_state.get(
                                'failed'): continue  # Если сам EPUB уже помечен как failed

                            prep_success, _, content_data, img_map_data, is_orig, err_warn = result
                            self.processed_task_count += 1

                            if prep_success:
                                build_state['results'].append({
                                    'original_filename': html_path, 'content_to_write': content_data,
                                    'image_map': img_map_data or {}, 'is_original_content': is_orig,
                                    'translation_warning': err_warn if is_orig and err_warn else None
                                })
                                if img_map_data:
                                    for uuid_k, img_info_d in img_map_data.items():
                                        if 'saved_path' in img_info_d and img_info_d['saved_path']:
                                            build_state['combined_image_map'][uuid_k] = img_info_d
                                if is_orig and err_warn:
                                    self.log_message.emit(
                                        f"[WARN] {Path(epub_path).name} -> {html_path}: Использован оригинал. Причина: {err_warn}")
                                    # Не считаем это глобальной ошибкой, если файл включен в сборку
                                    build_state['html_errors_count'] += 1
                                    self.errors_list.append(f"{Path(epub_path).name} -> {html_path}: {err_warn}")
                                # Если is_orig=False, это успешный перевод чанка(ов)
                            else:  # prep_success is False - HTML-часть не удалось подготовить, даже оригинал
                                self.error_count += 1  # Учитываем как глобальную ошибку
                                build_state['failed'] = True  # Весь EPUB считается неуспешным
                                build_state['html_errors_count'] += 1
                                err_detail = f"{Path(epub_path).name} -> {html_path}: {err_warn or 'Критическая ошибка подготовки HTML'}"
                                self.errors_list.append(err_detail);
                                self.log_message.emit(f"[FAIL] {err_detail}")
                                if build_state.get('future') and not build_state['future'].done():
                                    try:
                                        build_state['future'].cancel()  # Отменяем сборку, если она уже была запущена
                                    except Exception:
                                        pass

                            try:
                                if html_path in build_state['pending']: build_state['pending'].remove(html_path)
                            except KeyError:
                                pass

                            # Запуск сборки, если все HTML для этого EPUB обработаны (или их не было)
                            # И сборка еще не была запущена, И сам EPUB не помечен как failed
                            if not build_state['pending'] and not build_state.get('future') and not build_state.get(
                                    'failed'):
                                self.log_message.emit(
                                    f"[INFO] Все HTML части для {Path(epub_path).name} обработаны. Запуск задачи сборки...")
                                build_state['build_metadata']['combined_image_map'] = build_state.get(
                                    'combined_image_map', {})
                                build_future_submit = self.executor.submit(self.build_translated_epub, epub_path,
                                                                           build_state['results'],
                                                                           build_state['build_metadata'])
                                build_state['future'] = build_future_submit
                                futures[build_future_submit] = {'type': 'epub_build',
                                                                'epub_path': epub_path}  # Добавляем в общий пул

                            self.file_progress.emit(self.processed_task_count)

                    except (OperationCancelledError, CancelledError) as cancel_err:
                        self.processed_task_count += 1;
                        self.error_count += 1
                        err_origin_str = "N/A";
                        epub_path_local_cancel = None
                        if task_type == 'single_file':
                            err_origin_str = Path(task_info['info'][1]).name
                        elif task_type == 'epub_html':
                            err_origin_str = f"{Path(task_info['epub_path']).name} -> {task_info['html_path']}"; epub_path_local_cancel = \
                            task_info['epub_path']

                        err_detail_cancel = f"{err_origin_str}: Отменено ({type(cancel_err).__name__})"
                        self.errors_list.append(err_detail_cancel);
                        self.log_message.emit(f"[CANCELLED] Задача отменена: {err_origin_str}")

                        if epub_path_local_cancel and epub_path_local_cancel in self.epub_build_states:
                            self.epub_build_states[epub_path_local_cancel]['failed'] = True
                            self.epub_build_states[epub_path_local_cancel]['html_errors_count'] += 1
                            if task_info['html_path'] in self.epub_build_states[epub_path_local_cancel].get('pending',
                                                                                                            set()):
                                try:
                                    self.epub_build_states[epub_path_local_cancel]['pending'].remove(
                                        task_info['html_path'])
                                except KeyError:
                                    pass
                            if self.epub_build_states[epub_path_local_cancel].get('future') and not \
                            self.epub_build_states[epub_path_local_cancel]['future'].done():
                                try:
                                    self.epub_build_states[epub_path_local_cancel]['future'].cancel()
                                except Exception:
                                    pass
                        self.file_progress.emit(self.processed_task_count)

                    except (google_exceptions.ServiceUnavailable, google_exceptions.RetryError,
                            google_exceptions.ResourceExhausted) as critical_api_error:
                        self.processed_task_count += 1;
                        self.error_count += 1
                        err_origin_api = "N/A";
                        epub_path_local_api = None
                        if task_type == 'single_file':
                            err_origin_api = Path(task_info['info'][1]).name
                        elif task_type == 'epub_html':
                            err_origin_api = f"{Path(task_info['epub_path']).name} -> {task_info['html_path']}"; epub_path_local_api = \
                            task_info['epub_path']

                        error_type_name_api = type(critical_api_error).__name__
                        err_detail_api = f"{err_origin_api}: Критическая ошибка API ({error_type_name_api}), остановка: {critical_api_error}"
                        self.errors_list.append(err_detail_api);
                        self.log_message.emit(f"[CRITICAL] {err_detail_api}")
                        self.log_message.emit(
                            "[STOPPING] Обнаружена критическая ошибка API. Попытка сохранить прогресс и остановить...")

                        if epub_path_local_api and epub_path_local_api in self.epub_build_states:
                            self.epub_build_states[epub_path_local_api]['failed'] = True
                            self.epub_build_states[epub_path_local_api]['html_errors_count'] += 1

                        self.is_cancelled = True;
                        self._critical_error_occurred = True  # Устанавливаем флаги
                        self.file_progress.emit(self.processed_task_count)
                        break  # Выход из цикла as_completed

                    except Exception as e:
                        self.processed_task_count += 1;
                        self.error_count += 1
                        err_origin_exc = "N/A";
                        epub_path_local_exc = None
                        if task_type == 'single_file':
                            err_origin_exc = Path(task_info['info'][1]).name
                        elif task_type == 'epub_html':
                            err_origin_exc = f"{Path(task_info['epub_path']).name} -> {task_info['html_path']}"; epub_path_local_exc = \
                            task_info['epub_path']

                        err_msg_exc = f"Критическая ошибка обработки результата для {err_origin_exc}: {e}"
                        self.errors_list.append(err_msg_exc);
                        self.log_message.emit(f"[CRITICAL] {err_msg_exc}\n{traceback.format_exc()}")

                        if epub_path_local_exc and epub_path_local_exc in self.epub_build_states:
                            self.epub_build_states[epub_path_local_exc]['failed'] = True
                            self.epub_build_states[epub_path_local_exc]['html_errors_count'] += 1
                            build_future_to_cancel_exc = self.epub_build_states[epub_path_local_exc].get('future')
                            if build_future_to_cancel_exc and not build_future_to_cancel_exc.done():
                                try:
                                    build_future_to_cancel_exc.cancel()
                                except Exception:
                                    pass
                        self.file_progress.emit(self.processed_task_count)
                    finally:
                        self.current_file_status.emit("")
                        self.chunk_progress.emit("", 0, 0)

                    # Если is_finishing был установлен, и мы вышли из цикла as_completed для initial_futures_list
                    # то новые HTML задачи уже не добавляются. Теперь нужно дождаться запущенных задач сборки EPUB.
                    if self.is_finishing and not self.is_cancelled and not self._critical_error_occurred:
                        self.log_message.emit(
                            "[FINISHING] Обработка начальных задач завершена. Ожидание задач сборки EPUB...")
                        # Не выходим из цикла as_completed полностью, так как могут быть задачи сборки EPUB
                        # которые были добавлены в futures.
                        # Просто не добавляем новые HTML-задачи, если бы они были.

                self.log_message.emit(
                    "Обработка первоначальных задач (файлы/HTML) завершена или прервана (is_finishing/is_cancelled/_critical).")

                # 3. Process EPUB build tasks
                # Этот блок выполняется, чтобы собрать EPUB из уже обработанных HTML-частей.
                # Он должен выполниться даже если is_finishing=True.
                # Если is_cancelled или _critical_error_occurred, большинство задач сборки, вероятно, не запустятся
                # или будут отменены в finally, но если какие-то уже в futures, попытаемся их обработать.
                if is_epub_to_epub_mode:  # and not self.is_cancelled and not self._critical_error_occurred:
                    # Убрали проверку на is_cancelled/is_critical, чтобы попытаться обработать то, что есть,
                    # и чтобы finally мог корректно отменить build_futures.
                    # Запускаем задачи сборки для тех EPUB, где все HTML обработаны (или их не было)
                    # и сборка еще не была запущена/провалена, ИЛИ если is_finishing и мы хотим собрать то, что есть.
                    for epub_path, state in self.epub_build_states.items():
                        if not state.get('pending') and not state.get('future') and not state.get('failed'):
                            log_prefix_build_final = "[INFO]"
                            if self.is_finishing:
                                log_prefix_build_final = "[FINISHING INFO]"
                            elif self.is_cancelled:
                                log_prefix_build_final = "[CANCELLED INFO]"  # Если отмена, но все же пытаемся
                            self.log_message.emit(
                                f"{log_prefix_build_final} Запуск (или проверка) задачи сборки для {Path(epub_path).name}...")
                            state['build_metadata']['combined_image_map'] = state.get('combined_image_map', {})
                            build_future_submit = self.executor.submit(self.build_translated_epub, epub_path,
                                                                       state['results'], state['build_metadata'])
                            state['future'] = build_future_submit
                            futures[build_future_submit] = {'type': 'epub_build', 'epub_path': epub_path}

                    build_futures_to_wait = [
                        state['future'] for state in self.epub_build_states.values()
                        if state.get('future') and not state.get('processed_build_result')
                    ]

                    if build_futures_to_wait:
                        log_prefix_wait_final = "[INFO]"
                        if self.is_finishing:
                            log_prefix_wait_final = "[FINISHING INFO]"
                        elif self.is_cancelled:
                            log_prefix_wait_final = "[CANCELLED INFO]"
                        self.log_message.emit(
                            f"{log_prefix_wait_final} Ожидание завершения {len(build_futures_to_wait)} задач сборки EPUB...")
                        for build_future in as_completed(build_futures_to_wait):
                            if self.is_cancelled and not self.is_finishing:  # Если жесткая отмена, не ждем сборки
                                if build_future.done() and not build_future.cancelled():
                                    try:
                                        build_future.result()
                                    except Exception:
                                        pass
                                continue

                            task_info_build = futures.pop(build_future, None)  # Удаляем из общего пула
                            if not task_info_build or task_info_build['type'] != 'epub_build': continue

                            epub_path_build = task_info_build['epub_path']
                            build_state_build = self.epub_build_states.get(epub_path_build)
                            if not build_state_build or build_state_build.get('processed_build_result'): continue

                            self.current_file_status.emit(f"Завершение сборки EPUB: {Path(epub_path_build).name}...")
                            try:
                                _, success_build, error_message_build = build_future.result()
                                self.processed_task_count += 1  # Задача сборки - это тоже задача
                                build_state_build['processed_build_result'] = True
                                if success_build:
                                    self.success_count += 1
                                    # Если были ошибки в HTML частях этого EPUB, то сборка не считается полностью успешной
                                    # и self.success_count не должен был увеличиваться для этой задачи сборки,
                                    # или должен быть уменьшен, если html_errors_count > 0.
                                    # Но сам EPUB файл может быть собран.
                                    # Пока оставим так: success_count инкрементируется, если сборка физически произошла.
                                    # Проблема с "0 ошибок" в итоге, если html_errors_count > 0, должна быть решена выше.
                                    log_msg_build = f"[OK] Сборка EPUB {Path(epub_path_build).name} завершена."
                                    if build_state_build['html_errors_count'] > 0:
                                        log_msg_build += f" (ВНИМАНИЕ: {build_state_build['html_errors_count']} HTML-частей использовал(и) оригинал или не были обработаны)."
                                    self.log_message.emit(log_msg_build)
                                else:
                                    self.error_count += 1;
                                    build_state_build['failed'] = True
                                    err_detail_build = f"Ошибка сборки EPUB {Path(epub_path_build).name}: {error_message_build or 'N/A'}"
                                    self.errors_list.append(err_detail_build);
                                    self.log_message.emit(f"[FAIL] {err_detail_build}")
                                self.file_progress.emit(self.processed_task_count)
                            except (OperationCancelledError, CancelledError) as cancel_err_build:
                                if not build_state_build.get(
                                    'processed_build_result'): self.processed_task_count += 1; self.error_count += 1
                                build_state_build['processed_build_result'] = True;
                                build_state_build['failed'] = True
                                err_detail_cancel_build = f"Сборка EPUB: {Path(epub_path_build).name}: Отменено ({type(cancel_err_build).__name__})"
                                self.errors_list.append(err_detail_cancel_build);
                                self.log_message.emit(f"[CANCELLED] {err_detail_cancel_build}")
                                self.file_progress.emit(self.processed_task_count)
                            except Exception as build_exc:
                                if not build_state_build.get(
                                    'processed_build_result'): self.processed_task_count += 1; self.error_count += 1
                                build_state_build['processed_build_result'] = True;
                                build_state_build['failed'] = True
                                err_msg_build_exc = f"Критическая ошибка future для сборки EPUB {Path(epub_path_build).name}: {build_exc}"
                                self.errors_list.append(err_msg_build_exc);
                                self.log_message.emit(f"[CRITICAL] {err_msg_build_exc}\n{traceback.format_exc()}")
                                self.file_progress.emit(self.processed_task_count)
                            finally:
                                self.current_file_status.emit("")
                                self.chunk_progress.emit("", 0, 0)
                        self.log_message.emit("[INFO] Завершено ожидание задач сборки EPUB (если были).")

        except KeyboardInterrupt:
            self.log_message.emit("[SIGNAL] Получен KeyboardInterrupt, отмена...")
            self.is_cancelled = True
            executor_exception = KeyboardInterrupt("Отменено пользователем")
        except Exception as exec_err:
            self.log_message.emit(f"[CRITICAL] Ошибка в ThreadPoolExecutor: {exec_err}\n{traceback.format_exc()}")
            executor_exception = exec_err
            self.is_cancelled = True
        finally:
            # 4. Shutdown executor and finalize
            if self.executor:
                wait_for_active = True  # Всегда ждем активные
                cancel_queued = False

                if self.is_cancelled or self._critical_error_occurred:
                    self.log_message.emit(
                        "[INFO] Отмена/Ошибка: Принудительное завершение Executor, отмена ожидающих задач...")
                    cancel_queued = True
                elif self.is_finishing:
                    self.log_message.emit(
                        "[INFO] Завершение: Ожидание завершения активных задач Executor, отмена остальных в очереди...")
                    cancel_queued = True  # Отменяем то, что не успело начаться
                else:  # Нормальное завершение
                    self.log_message.emit("[INFO] Нормальное завершение: Ожидание Executor...")

                if sys.version_info >= (3, 9):
                    self.executor.shutdown(wait=wait_for_active, cancel_futures=cancel_queued)
                else:  # Python < 3.9
                    if cancel_queued:
                        self.log_message.emit("[INFO] Python < 3.9: Ручная отмена оставшихся задач в очереди...")
                        active_futures_to_cancel_final = []
                        # Собираем все оставшиеся futures из словаря 'futures' и из 'build_state'
                        if 'futures' in locals() and isinstance(futures, dict):
                            active_futures_to_cancel_final.extend([f for f in futures.keys() if not f.done()])
                        if is_epub_to_epub_mode:
                            for state_val in self.epub_build_states.values():
                                build_fut_val = state_val.get('future')
                                if build_fut_val and not build_fut_val.done() and build_fut_val not in active_futures_to_cancel_final:
                                    active_futures_to_cancel_final.append(build_fut_val)
                        for fut_to_cancel in active_futures_to_cancel_final:
                            try:
                                fut_to_cancel.cancel()
                            except Exception:
                                pass
                    self.executor.shutdown(wait=wait_for_active)

            self.executor = None
            self.log_message.emit("ThreadPoolExecutor завершен.")

            # Финальный подсчет ошибок/успехов для EPUB
            if is_epub_to_epub_mode:
                for epub_path, state in self.epub_build_states.items():
                    # Если сборка не была обработана (т.е. processed_build_result=False)
                    # и EPUB не был помечен как 'failed' из-за ошибки HTML,
                    # но при этом был is_finishing или is_cancelled, считаем это пропуском/ошибкой сборки.
                    if not state.get('processed_build_result'):
                        if not state.get('failed'):  # Если не было ошибки до этого
                            self.error_count += 1  # Считаем незавершенную/незапущенную сборку как ошибку
                            reason = "не завершена (отмена)" if self.is_cancelled else \
                                "не завершена (завершение)" if self.is_finishing else \
                                    "не обработана (ошибка)"
                            self.errors_list.append(f"Сборка EPUB: {Path(epub_path).name}: {reason}")
                        state['failed'] = True  # Помечаем, что EPUB не был успешно собран
                        state['processed_build_result'] = True  # Помечаем, что результат учтен
                        self.log_message.emit(
                            f"[WARN] Задача сборки {Path(epub_path).name} учтена как неуспешная ({reason}).")
                # Пересчитываем общий progress_bar.maximum, если total_tasks был 0
                if self.total_tasks == 0 and self.processed_task_count > 0:
                    self.progress_bar.setRange(0, self.processed_task_count)
                self.file_progress.emit(self.processed_task_count)

            final_status_msg = "Завершено"
            log_separator = "\n" + "=" * 40 + "\n"
            if self._critical_error_occurred:
                final_status_msg = "Остановлено (ошибка API)"
                self.log_message.emit(f"{log_separator}--- ПРОЦЕСС ОСТАНОВЛЕН (КРИТ. ОШИБКА API) ---")
            elif self.is_cancelled:
                final_status_msg = "Отменено"
                self.log_message.emit(f"{log_separator}--- ПРОЦЕСС ОТМЕНЕН ПОЛЬЗОВАТЕЛЕМ ---")
            elif self.is_finishing:
                final_status_msg = "Завершено (частично)"
                self.log_message.emit(f"{log_separator}--- ПРОЦЕСС ЗАВЕРШЕН ПО КОМАНДЕ (частично) ---")
            elif executor_exception:
                final_status_msg = "Ошибка Executor"
                self.log_message.emit(f"{log_separator}--- ПРОЦЕСС ЗАВЕРШЕН С ОШИБКОЙ EXECUTOR ---")
            else:
                self.log_message.emit(f"{log_separator}--- ОБРАБОТКА ЗАВЕРШЕНА ---")

            self.current_file_status.emit(final_status_msg)
            self.chunk_progress.emit("", 0, 0)

            if executor_exception: self.errors_list.insert(0, f"Критическая ошибка Executor: {executor_exception}")

            # Коррекция счетчиков для более точного отображения
            # processed_task_count должен быть равен total_tasks в идеале, но может быть меньше при отмене/ошибке
            # error_count = (общее количество задач, которые должны были быть выполнены) - (успешно выполненные)
            # Если total_tasks = 0, то error_count должен быть 0, если нет executor_exception.
            if self.total_tasks > 0:
                # error_count не должен превышать количество задач, которые не были успешными
                max_possible_errors = self.total_tasks - self.success_count
                if self.error_count > max_possible_errors: self.error_count = max_possible_errors
                if self.error_count < 0: self.error_count = 0
            elif not executor_exception:  # total_tasks == 0 и нет других ошибок
                self.error_count = 0

            self.log_message.emit(
                f"ИТОГ: Успешно: {self.success_count}, Ошибок/Отменено/Пропущено: {self.error_count} из {self.total_tasks} задач.")
            self.finished.emit(self.success_count, self.error_count, self.errors_list)

    def cancel(self):
        if not self.is_cancelled:
            self.log_message.emit("[SIGNAL] Получен сигнал отмены (Worker.cancel)...")
            self.is_cancelled = True