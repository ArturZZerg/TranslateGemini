import html
import os
import re
import time
import traceback
import uuid
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup
from ebooklib import epub
from lxml import etree

from urllib.parse import urlparse, urljoin, unquote

from transgemini.config import *
from transgemini.core.html_builder import _convert_placeholders_to_html_img
from transgemini.core.utils import add_translated_suffix


def generate_nav_html(nav_data_list, nav_file_path_in_zip, book_title, book_lang="ru"):
    """
    Generates XHTML content for nav.xhtml based on spine data.
    Simplified version focusing on the list structure.
    """
    if not nav_data_list:
        print("[WARN] NAV Gen: Input data list is empty. NAV not generated.")
        return None

    if not LXML_AVAILABLE:
        print("[ERROR] NAV Gen: LXML library is required for reliable NAV generation.")

        print("[WARN] NAV Gen: LXML not found, attempting basic string generation (less reliable).")
        nav_lines = []
        nav_lines.append("<?xml version='1.0' encoding='utf-8'?>")
        nav_lines.append("<!DOCTYPE html>")
        nav_lines.append(
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{book_lang}" xml:lang="{book_lang}">')
        nav_lines.append("<head>")
        nav_lines.append("  <meta charset=\"utf-8\"/>")

        nav_lines.append("</head>")
        nav_lines.append("<body>")
        nav_lines.append('  <nav epub:type="toc" id="toc">')  # Используем id="toc"

        nav_lines.append("    <ol>")
        nav_dir = os.path.dirname(nav_file_path_in_zip).replace('\\', '/')
        if nav_dir == '.': nav_dir = ""
        link_count_str = 0
        for item_path, item_title in nav_data_list:
            safe_item_title = html.escape(str(item_title).strip())
            if not safe_item_title: safe_item_title = "Untitled Entry"  # Заглушка
            try:
                item_path_norm = item_path.replace('\\', '/').lstrip('/')
                nav_parent_dir_norm = os.path.dirname(nav_file_path_in_zip.replace('\\', '/').lstrip('/')).replace('\\',
                                                                                                                   '/')
                relative_href = os.path.relpath(item_path_norm,
                                                start=nav_parent_dir_norm if nav_parent_dir_norm else '.').replace('\\',
                                                                                                                   '/')
                safe_href = html.escape(relative_href, quote=True)
                nav_lines.append(f'      <li><a href="{safe_href}">{safe_item_title}</a></li>')
                link_count_str += 1
            except ValueError as e:
                print(
                    f"[WARN] NAV Gen (String): Failed to calculate relative path for '{item_path}' from '{nav_parent_dir_norm or '<root>'}': {e}. Skipping link.")
            except Exception as e_loop:
                print(f"[ERROR] NAV Gen (String): Error processing item ('{item_path}', '{item_title}'): {e_loop}")
        nav_lines.append("    </ol>")
        nav_lines.append("  </nav>")
        nav_lines.append("</body>")
        nav_lines.append("</html>")
        print(f"[INFO] NAV Gen (String): Finished generation. Added {link_count_str} links.")
        return "\n".join(nav_lines).encode('utf-8')

    print(
        f"[INFO] NAV Gen (lxml): Starting NAV generation for '{nav_file_path_in_zip}' with {len(nav_data_list)} entries...")
    xhtml_ns = "http://www.w3.org/1999/xhtml"
    epub_ns = "http://www.idpf.org/2007/ops"
    NSMAP = {None: xhtml_ns, "epub": epub_ns}

    html_tag = etree.Element(f"{{{xhtml_ns}}}html", nsmap=NSMAP)
    xml_lang_attr_name = "{http://www.w3.org/XML/1998/namespace}lang"
    html_tag.set(xml_lang_attr_name, book_lang)
    html_tag.set("lang", book_lang)

    head = etree.SubElement(html_tag, f"{{{xhtml_ns}}}head")

    etree.SubElement(head, f"{{{xhtml_ns}}}meta", charset="utf-8")  # Добавляем meta charset

    body = etree.SubElement(html_tag, f"{{{xhtml_ns}}}body")
    nav = etree.SubElement(body, f"{{{xhtml_ns}}}nav", id="toc")  # Используем id="toc"
    nav.set(f"{{{epub_ns}}}type", "toc")

    ol = etree.SubElement(nav, f"{{{xhtml_ns}}}ol")

    nav_dir = os.path.dirname(nav_file_path_in_zip).replace('\\', '/')
    if nav_dir == '.': nav_dir = ""  # Корень

    link_count = 0
    for item_path, item_title in nav_data_list:
        safe_item_title = html.escape(str(item_title).strip())
        if not safe_item_title: safe_item_title = "Untitled Entry"  # Заглушка для пустых заголовков

        try:
            item_path_norm = item_path.replace('\\', '/').lstrip('/')
            nav_parent_dir_norm = os.path.dirname(nav_file_path_in_zip.replace('\\', '/').lstrip('/')).replace('\\',
                                                                                                               '/')

            relative_href = os.path.relpath(item_path_norm,
                                            start=nav_parent_dir_norm if nav_parent_dir_norm else '.').replace('\\',
                                                                                                               '/')
            safe_href = html.escape(relative_href, quote=True)

            li = etree.SubElement(ol, f"{{{xhtml_ns}}}li")
            a = etree.SubElement(li, f"{{{xhtml_ns}}}a", href=safe_href)
            a.text = safe_item_title
            link_count += 1


        except ValueError as e:
            print(
                f"[WARN] NAV Gen (lxml): Failed to calculate relative path for '{item_path}' from '{nav_parent_dir_norm or '<root>'}': {e}. Skipping link.")
        except Exception as e_loop:
            print(f"[ERROR] NAV Gen (lxml): Error processing item ('{item_path}', '{item_title}'): {e_loop}")

    if link_count == 0 and len(nav_data_list) > 0:
        print("[WARN] NAV Gen (lxml): No list items were added to NAV despite input data.")
        ol.append(etree.Comment(" Error: No valid links generated "))
    elif link_count != len(nav_data_list):
        print(f"[WARN] NAV Gen (lxml): Added {link_count} links, but received {len(nav_data_list)} data items.")

    nav_output_string = etree.tostring(html_tag, encoding='unicode', method='html', xml_declaration=False,
                                       pretty_print=True)

    doctype = '<!DOCTYPE html>'
    xml_declaration = "<?xml version='1.0' encoding='utf-8'?>"
    final_output = f"{xml_declaration}\n{doctype}\n{nav_output_string}"

    print(f"[INFO] NAV Gen (lxml): Finished generation for '{nav_file_path_in_zip}'. Added {link_count} links.")
    return final_output.encode('utf-8')  # Возвращаем байты UTF-8

def generate_ncx_manual(book_id, book_title, ncx_data_list):
    """
    Generates the content of an NCX file manually from a prepared list of data
    derived from nav.xhtml.

    Args:
        book_id (str): The unique identifier for the book (for dtb:uid).
        book_title (str): The title of the book (for docTitle).
        ncx_data_list (list): A list of tuples extracted from nav.xhtml:
                              [(nav_point_id, content_src, link_text), ...].
                              - nav_point_id: Pre-generated ID for the navPoint.
                              - content_src: Pre-calculated relative path for content src.
                              - link_text: Text label for the navPoint.

    Returns:
        bytes: The generated NCX content as bytes (UTF-8 encoded XML), or None if error.
    """
    if not ncx_data_list:
        print("[WARN] NCX Manual Gen: Input data list is empty. NCX not generated.")
        return None

    print(f"[INFO] NCX Manual Gen: Starting NCX generation from {len(ncx_data_list)} NAV entries...")

    ncx_lines = []
    ncx_lines.append("<?xml version='1.0' encoding='utf-8'?>")
    ncx_lines.append('<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">')
    ncx_lines.append('  <head>')

    safe_book_id = html.escape(book_id or f"urn:uuid:{uuid.uuid4()}", quote=True)
    ncx_lines.append(f'    <meta content="{safe_book_id}" name="dtb:uid"/>')

    ncx_lines.append('    <meta content="1" name="dtb:depth"/>')  # Ставим 1, если есть navPoints
    ncx_lines.append('    <meta content="0" name="dtb:totalPageCount"/>')
    ncx_lines.append('    <meta content="0" name="dtb:maxPageNumber"/>')
    ncx_lines.append('  </head>')

    safe_book_title = html.escape(book_title or "Untitled")
    ncx_lines.append('  <docTitle>')
    ncx_lines.append(f'    <text>{safe_book_title}</text>')
    ncx_lines.append('  </docTitle>')

    ncx_lines.append('  <docAuthor>')
    ncx_lines.append(f'    <text>Translator</text>')  # Можно заменить на что-то другое
    ncx_lines.append('  </docAuthor>')

    ncx_lines.append('  <navMap>')

    play_order_counter = 1
    for nav_point_id, content_src, link_text in ncx_data_list:
        safe_id = html.escape(nav_point_id, quote=True)
        safe_label = html.escape(link_text)
        safe_src = html.escape(content_src, quote=True)

        ncx_lines.append(f'    <navPoint id="{safe_id}" playOrder="{play_order_counter}">')
        ncx_lines.append('      <navLabel>')
        ncx_lines.append(f'        <text>{safe_label}</text>')
        ncx_lines.append('      </navLabel>')
        ncx_lines.append(f'      <content src="{safe_src}"/>')
        ncx_lines.append('    </navPoint>')
        play_order_counter += 1

    ncx_lines.append('  </navMap>')
    ncx_lines.append('</ncx>')

    ncx_output_string = "\n".join(ncx_lines)
    print(f"[INFO] NCX Manual Gen: Generated {play_order_counter - 1} navPoints from NAV data.")

    return ncx_output_string.encode('utf-8')

def parse_nav_for_ncx_data(nav_content_bytes, nav_base_path_in_zip):
    """Извлекает данные из NAV XHTML для генерации NCX."""
    if not nav_content_bytes or not BS4_AVAILABLE: return []
    ncx_data = []
    play_order = 1
    try:
        soup = BeautifulSoup(nav_content_bytes, 'lxml-xml')  # Используем XML парсер
        nav_list = soup.find('nav', attrs={'epub:type': 'toc'})
        if not nav_list: nav_list = soup  # Fallback, если нет <nav>
        list_tag = nav_list.find(['ol', 'ul'])
        if not list_tag: return []

        nav_dir = os.path.dirname(nav_base_path_in_zip).replace('\\', '/')
        if nav_dir == '.': nav_dir = ""  # Корень

        for link in list_tag.find_all('a', href=True):
            href = link.get('href')
            text = link.get_text(strip=True)
            if not href or not text or href.startswith('#') or href.startswith(('http:', 'https:', 'mailto:')):
                continue

            try:

                abs_path_in_zip = os.path.normpath(os.path.join(nav_dir, unquote(href))).replace('\\', '/')
                content_src = abs_path_in_zip.lstrip('/')  # NCX src обычно от корня

                content_src_base = urlparse(content_src).path

                safe_base_name = re.sub(r'[^\w\-]+', '_', Path(content_src_base).stem)
                nav_point_id = f"navpoint_{safe_base_name}_{play_order}"

                ncx_data.append((nav_point_id, content_src, text))  # Сохраняем путь с фрагментом, если был
                play_order += 1
            except Exception as e:
                print(f"[WARN NavParseForNCX] Error processing NAV link '{href}': {e}")
        return ncx_data
    except Exception as e:
        print(f"[ERROR NavParseForNCX] Failed to parse NAV content: {e}")
        return []

def parse_ncx_for_nav_data(ncx_content_bytes, opf_dir):
    """Извлекает данные из NCX для генерации NAV HTML."""
    if not ncx_content_bytes or not LXML_AVAILABLE: return []
    nav_data = []  # Будет содержать кортежи: (путь_от_корня_zip, заголовок)
    try:
        root = etree.fromstring(ncx_content_bytes)
        ns = {'ncx': 'http://www.daisy.org/z3986/2005/ncx/'}
        for nav_point in root.xpath('//ncx:navMap/ncx:navPoint', namespaces=ns):
            content_tag = nav_point.find('ncx:content', ns)
            label_tag = nav_point.find('.//ncx:text', ns)

            if content_tag is not None and label_tag is not None:
                src = content_tag.get('src')
                text = label_tag.text.strip() if label_tag.text else "Untitled"
                if not src: continue

                try:

                    unquoted_src = unquote(urlparse(src).path)  # Убираем URL-кодирование и фрагменты

                    if opf_dir:

                        abs_path_in_zip = os.path.normpath(os.path.join(opf_dir, unquoted_src)).replace('\\', '/')
                    else:

                        abs_path_in_zip = os.path.normpath(unquoted_src).replace('\\', '/')

                    abs_path_in_zip = '/'.join(part for part in abs_path_in_zip.split('/') if part != '..')
                    abs_path_in_zip = abs_path_in_zip.lstrip('/')

                    nav_data.append((abs_path_in_zip, text))

                except Exception as e:
                    print(f"[WARN NcxParseForNav] Error processing NCX src '{src}': {e}")
        return nav_data
    except Exception as e:
        print(f"[ERROR NcxParseForNav] Failed to parse NCX content: {e}")
        return []

def update_nav_content(nav_content_bytes, nav_base_path_in_zip, filename_map, canonical_titles):
    """Обновляет href и текст ссылок в существующем NAV контенте."""
    if not nav_content_bytes or not BS4_AVAILABLE: return None
    try:
        soup = BeautifulSoup(nav_content_bytes, 'lxml-xml')
        nav_list = soup.find('nav', attrs={'epub:type': 'toc'})
        if not nav_list: nav_list = soup
        list_tag = nav_list.find(['ol', 'ul'])
        if not list_tag: return nav_content_bytes  # Не нашли список, возвращаем как есть

        nav_dir = os.path.dirname(nav_base_path_in_zip).replace('\\', '/')
        if nav_dir == '.': nav_dir = ""  # Корень

        updated_count = 0
        for link in list_tag.find_all('a', href=True):
            href = link.get('href')
            if not href or href.startswith('#') or href.startswith(('http:', 'https:', 'mailto:')):
                continue

            original_target_full_path = None
            frag = None
            try:

                original_target_full_path = os.path.normpath(
                    os.path.join(nav_dir, unquote(urlparse(href).path))).replace('\\', '/').lstrip('/')
                frag = urlparse(href).fragment

            except Exception as e:
                print(f"[WARN NAV Update] Error resolving original path for href '{href}': {e}")
                continue

            new_target_relative_path = filename_map.get(original_target_full_path)

            if new_target_relative_path:
                try:

                    nav_parent_dir = os.path.dirname(nav_base_path_in_zip).replace('\\',
                                                                                   '/')  # Директория, где лежит NAV
                    new_rel_href = os.path.relpath(new_target_relative_path, start=nav_parent_dir).replace('\\', '/')

                    new_href_val = new_rel_href + (f"#{frag}" if frag else "")
                    link['href'] = new_href_val  # Обновляем href
                    updated_count += 1
                except ValueError as e:
                    print(
                        f"[WARN NAV Update] Error calculating relative href for '{new_target_relative_path}' from '{nav_parent_dir}': {e}")

            target_canonical_title = canonical_titles.get(original_target_full_path)
            if target_canonical_title:
                link.string = html.escape(str(target_canonical_title).strip())  # Устанавливаем новый текст

        print(f"[INFO] NAV Update: Updated attributes for {updated_count} links.")

        return str(soup).encode('utf-8')

    except Exception as e:
        print(f"[ERROR NAV Update] Failed to update NAV content: {e}\n{traceback.format_exc()}")
        return None  # Возвращаем None в случае ошибки

def update_ncx_content(ncx_content_bytes, opf_dir, filename_map, canonical_titles):
    """Обновляет src и text в существующем NCX контенте."""
    if not ncx_content_bytes or not LXML_AVAILABLE: return None
    try:

        ncx_ns_uri = 'http://www.daisy.org/z3986/2005/ncx/'
        ns = {'ncx': ncx_ns_uri}

        root = etree.fromstring(ncx_content_bytes)
        updated_count = 0

        for nav_point in root.xpath('//ncx:navPoint', namespaces=ns):
            content_tag = nav_point.find('ncx:content', ns)
            label_tag = nav_point.find('.//ncx:text', ns)  # Ищем text внутри navLabel

            if content_tag is None or label_tag is None: continue

            src = content_tag.get('src')
            if not src: continue

            original_target_full_path = None
            frag = None
            try:

                original_target_full_path = os.path.normpath(
                    os.path.join(opf_dir, unquote(urlparse(src).path))).replace('\\', '/').lstrip('/')
                frag = urlparse(src).fragment

            except Exception as e:
                print(f"[WARN NCX Update] Error resolving original path for src '{src}': {e}")
                continue

            new_target_relative_path = filename_map.get(original_target_full_path)

            if new_target_relative_path:
                try:

                    if opf_dir:  # Если OPF не в корне
                        new_src = os.path.relpath(new_target_relative_path, start=opf_dir).replace('\\', '/')
                    else:  # OPF в корне, новый путь уже относителен корню
                        new_src = new_target_relative_path

                    new_src_val = new_src + (f"#{frag}" if frag else "")
                    content_tag.set('src', new_src_val)  # Обновляем src
                    updated_count += 1
                except ValueError as e:
                    print(
                        f"[WARN NCX Update] Error calculating relative src for '{new_target_relative_path}' from '{opf_dir or '<root>'}': {e}")

            target_canonical_title = canonical_titles.get(original_target_full_path)
            if target_canonical_title:
                label_tag.text = str(target_canonical_title).strip()  # Устанавливаем новый текст

        print(f"[INFO] NCX Update: Updated attributes for {updated_count} navPoints.")

        return etree.tostring(root, encoding='utf-8', xml_declaration=True, pretty_print=True)

    except Exception as e:
        print(f"[ERROR NCX Update] Failed to update NCX content: {e}\n{traceback.format_exc()}")
        return None  # Возвращаем None в случае ошибки

def write_to_epub(out_path, processed_epub_parts, original_epub_path, build_metadata, book_title_override=None):
    start_time = time.time()
    if not EBOOKLIB_AVAILABLE: return False, "EbookLib library is required"
    if not LXML_AVAILABLE: return False, "lxml library is required"
    if not BS4_AVAILABLE: return False, "BeautifulSoup4 required"
    if not os.path.exists(original_epub_path): return False, f"Original EPUB not found: {original_epub_path}"

    print(f"[INFO] EPUB Rebuild: Starting rebuild for '{os.path.basename(original_epub_path)}' -> '{out_path}'")
    book = epub.EpubBook()

    nav_path_orig_from_meta = build_metadata.get('nav_path_in_zip')
    ncx_path_orig_from_meta = build_metadata.get('ncx_path_in_zip')
    opf_dir_from_meta = build_metadata.get('opf_dir', '')  # Это директория OPF в оригинальном EPUB
    nav_id_orig_from_meta = build_metadata.get('nav_item_id')
    ncx_id_orig_from_meta = build_metadata.get('ncx_item_id')

    final_book_title = book_title_override or Path(original_epub_path).stem
    final_author = "Translator";
    final_identifier = f"urn:uuid:{uuid.uuid4()}";
    final_language = "ru"

    original_manifest_items_from_zip = {}  # {path_in_zip: {id, media_type, properties, original_href}}
    original_spine_idrefs_from_zip = []

    combined_new_image_map_from_worker = build_metadata.get('combined_image_map', {})

    filename_map = {}  # original_full_path_in_zip -> new_full_path_in_zip (для обновления NAV/NCX)
    final_book_item_ids = set()  # Для отслеживания уникальности ID
    book_items_to_add_to_epub_obj = []  # Список объектов EpubItem, EpubHtml, EpubImage для добавления в book

    new_book_items_structure_map = {}
    id_to_new_item_map = {}  # Для быстрого доступа по ID в spine

    processed_original_paths_from_zip = set()  # Отслеживать, какие файлы из ZIP уже обработаны
    canonical_titles_map = {}  # original_full_path_in_zip -> canonical_title

    opf_dir_for_new_epub = opf_dir_from_meta  # Директория OPF в НОВОМ EPUB (обычно та же)

    try:
        with zipfile.ZipFile(original_epub_path, 'r') as original_zip:
            zip_contents_normalized = {name.replace('\\', '/'): name for name in original_zip.namelist()}
            opf_path_in_zip_abs = None

            try:
                container_data = original_zip.read('META-INF/container.xml')
                container_root = etree.fromstring(container_data);
                cnt_ns = {'c': 'urn:oasis:names:tc:opendocument:xmlns:container'}
                opf_path_rel_to_container = container_root.xpath('//c:rootfile/@full-path', namespaces=cnt_ns)[0]
                opf_path_in_zip_abs = opf_path_rel_to_container.replace('\\', '/')

                temp_opf_dir_check = os.path.dirname(opf_path_in_zip_abs).replace('\\', '/')
                temp_opf_dir_check = "" if temp_opf_dir_check == '.' else temp_opf_dir_check.lstrip('/')
                if opf_dir_for_new_epub != temp_opf_dir_check:
                    print(
                        f"[WARN] OPF directory mismatch: Meta='{opf_dir_for_new_epub}', Re-check='{temp_opf_dir_check}'. Using meta: '{opf_dir_for_new_epub}'.")
            except Exception:  # Fallback
                pot_opf = [p for p in zip_contents_normalized if
                           p.lower().endswith('.opf') and not p.lower().startswith(
                               'meta-inf/') and p.lower() != 'mimetype']
                if not pot_opf: pot_opf = [p for p in zip_contents_normalized if
                                           p.lower().endswith('.opf') and p.lower() != 'mimetype']
                if not pot_opf: raise FileNotFoundError("Cannot find OPF in original EPUB.")
                opf_path_in_zip_abs = pot_opf[0]

            if not opf_path_in_zip_abs: raise FileNotFoundError("OPF path could not be determined.")

            opf_data_bytes = original_zip.read(zip_contents_normalized[opf_path_in_zip_abs])
            opf_root = etree.fromstring(opf_data_bytes)
            ns_opf_parse = {'opf': 'http://www.idpf.org/2007/opf', 'dc': 'http://purl.org/dc/elements/1.1/'}

            meta_node = opf_root.find('.//opf:metadata', ns_opf_parse) or opf_root.find('.//metadata')
            if meta_node is not None:
                def get_text_meta(
                        element): return element.text.strip() if element is not None and element.text else None

                lang_node = meta_node.find('.//dc:language', ns_opf_parse) or meta_node.find('.//language')
                title_node = meta_node.find('.//dc:title', ns_opf_parse) or meta_node.find('.//title')
                creator_node = meta_node.find('.//dc:creator', ns_opf_parse) or meta_node.find('.//creator')
                id_element = meta_node.find('.//dc:identifier[@id]', ns_opf_parse) or \
                             meta_node.find('.//identifier[@id]', ns_opf_parse) or \
                             meta_node.find('.//dc:identifier', ns_opf_parse) or \
                             meta_node.find('.//identifier')
                final_language = get_text_meta(lang_node) or final_language
                final_book_title = book_title_override or get_text_meta(title_node) or final_book_title
                final_author = get_text_meta(creator_node) or final_author
                final_identifier = get_text_meta(id_element) or final_identifier or f"urn:uuid:{uuid.uuid4()}"
            book.set_title(final_book_title);
            book.add_author(final_author);
            book.set_identifier(final_identifier);
            book.set_language(final_language)

            manifest_node = opf_root.find('.//opf:manifest', ns_opf_parse) or opf_root.find('.//manifest')
            if manifest_node is not None:
                for item_mf_loop in (
                        manifest_node.findall('.//opf:item', ns_opf_parse) or manifest_node.findall('.//item')):
                    item_id = item_mf_loop.get('id');
                    href = item_mf_loop.get('href');
                    media_type = item_mf_loop.get('media-type');
                    props = item_mf_loop.get('properties')
                    if not item_id or not href or not media_type: continue

                    full_path_in_zip = os.path.normpath(os.path.join(opf_dir_from_meta, unquote(href))).replace('\\',
                                                                                                                '/').lstrip(
                        '/')
                    original_manifest_items_from_zip[full_path_in_zip] = {'id': item_id, 'media_type': media_type,
                                                                          'properties': props, 'original_href': href}

            spine_node = opf_root.find('.//opf:spine', ns_opf_parse) or opf_root.find('.//spine')
            ncx_id_from_spine_attr = None
            if spine_node is not None:
                ncx_id_from_spine_attr = spine_node.get('toc')  # Это ID NCX файла из манифеста
                original_spine_idrefs_from_zip = [i_ref.get('idref') for i_ref in (
                            spine_node.findall('.//opf:itemref', ns_opf_parse) or spine_node.findall('.//itemref')) if
                                                  i_ref.get('idref')]

            if nav_path_orig_from_meta and nav_path_orig_from_meta in zip_contents_normalized:
                try:
                    nav_data_bytes = original_zip.read(zip_contents_normalized[nav_path_orig_from_meta])
                    nav_soup = BeautifulSoup(nav_data_bytes, 'lxml-xml')
                    nav_list_el = nav_soup.find('nav', attrs={'epub:type': 'toc'}) or nav_soup
                    list_tag_nav = nav_list_el.find(['ol', 'ul'])
                    if list_tag_nav:
                        nav_dir_current = os.path.dirname(nav_path_orig_from_meta).replace('\\', '/')
                        if nav_dir_current == '.': nav_dir_current = ""
                        for link in list_tag_nav.find_all('a', href=True):
                            href = link.get('href');
                            title_text = link.get_text(strip=True)
                            if not href or not title_text or href.startswith(('#', 'http:', 'mailto:')): continue
                            try:
                                target_full_path = os.path.normpath(
                                    os.path.join(nav_dir_current, unquote(urlparse(href).path))).replace('\\',
                                                                                                         '/').lstrip(
                                    '/')
                                if target_full_path not in canonical_titles_map: canonical_titles_map[
                                    target_full_path] = title_text
                            except Exception:
                                pass
                except Exception as nav_err_read:
                    print(f"[WARN write_epub] Error reading original NAV for titles: {nav_err_read}")
            elif ncx_path_orig_from_meta and ncx_path_orig_from_meta in zip_contents_normalized:
                try:
                    ncx_data_bytes = original_zip.read(zip_contents_normalized[ncx_path_orig_from_meta])
                    ncx_root_titles = etree.fromstring(ncx_data_bytes);
                    ncx_ns_titles = {'ncx': 'http://www.daisy.org/z3986/2005/ncx/'}
                    for nav_point in ncx_root_titles.xpath('//ncx:navMap/ncx:navPoint', namespaces=ncx_ns_titles):
                        content_tag = nav_point.find('ncx:content', ncx_ns_titles);
                        label_tag = nav_point.find('.//ncx:text', ncx_ns_titles)
                        if content_tag is not None and label_tag is not None and content_tag.get('src'):
                            src_attr = content_tag.get('src');
                            title_text = label_tag.text.strip() if label_tag.text else None
                            if not src_attr or not title_text: continue
                            try:
                                target_full_path = os.path.normpath(
                                    os.path.join(opf_dir_from_meta, unquote(urlparse(src_attr).path))).replace('\\',
                                                                                                               '/').lstrip(
                                    '/')
                                if target_full_path not in canonical_titles_map: canonical_titles_map[
                                    target_full_path] = title_text
                            except Exception:
                                pass
                except Exception as ncx_err_read:
                    print(f"[WARN write_epub] Error reading original NCX for titles: {ncx_err_read}")

            new_image_objects_for_manifest = {}  # uuid -> EpubImage object
            img_counter = 1
            for img_uuid, new_img_info in combined_new_image_map_from_worker.items():
                temp_img_path = new_img_info.get('saved_path')
                if not temp_img_path or not os.path.exists(temp_img_path):
                    print(
                        f"[WARN write_epub] New image for UUID {img_uuid} has invalid temp path: '{temp_img_path}'. Skipping.")
                    continue
                try:
                    with open(temp_img_path, 'rb') as f_new_img:
                        img_data_bytes = f_new_img.read()
                    content_type = new_img_info.get('content_type', 'image/jpeg')
                    ext_new_img = content_type.split('/')[-1];
                    ext_new_img = 'jpg' if ext_new_img == 'jpeg' else ext_new_img
                    orig_fname_for_new = new_img_info.get('original_filename',
                                                          f'new_image_{img_uuid[:6]}.{ext_new_img}')

                    img_folder_in_epub = "Images"  # Можно сделать настраиваемым
                    new_img_rel_path_in_epub = os.path.join(img_folder_in_epub,
                                                            re.sub(r'[^\w\.\-]', '_', orig_fname_for_new)).replace('\\',
                                                                                                                   '/')

                    new_img_id = f"new_img_{img_uuid[:6]}_{img_counter}"
                    if new_img_id in final_book_item_ids: new_img_id = f"{new_img_id}_{uuid.uuid4().hex[:3]}"

                    epub_img_obj_new = epub.EpubImage(uid=new_img_id, file_name=new_img_rel_path_in_epub,
                                                      media_type=content_type, content=img_data_bytes)
                    book_items_to_add_to_epub_obj.append(epub_img_obj_new)
                    new_image_objects_for_manifest[
                        img_uuid] = epub_img_obj_new  # Для использования в _convert_placeholders
                    final_book_item_ids.add(new_img_id)

                    new_img_abs_path_in_epub = os.path.normpath(
                        os.path.join(opf_dir_for_new_epub, new_img_rel_path_in_epub)).replace('\\', '/').lstrip('/')
                    new_book_items_structure_map[new_img_abs_path_in_epub] = {'item': epub_img_obj_new,
                                                                              'content_bytes': None,
                                                                              'canonical_title': None}
                    id_to_new_item_map[new_img_id] = new_book_items_structure_map[new_img_abs_path_in_epub]
                    processed_original_paths_from_zip.add(
                        new_img_abs_path_in_epub)  # Помечаем, что этот путь уже занят новым изображением
                    img_counter += 1
                except Exception as e_new_img:
                    print(f"[ERROR write_epub] Failed to add new image (UUID {img_uuid}): {e_new_img}")

            print(f"[INFO write_epub] Начало обработки {len(processed_epub_parts)} HTML-частей для сборки...")

            for part_data in processed_epub_parts:

                if 'content_to_write' not in part_data or part_data['content_to_write'] is None:
                    original_fn_for_skip = part_data.get('original_filename', 'Неизвестный HTML')
                    warning_msg_for_skip = part_data.get('translation_warning',
                                                         'Данные контента отсутствуют или повреждены')
                    print(
                        f"[WARN write_epub] Пропуск HTML-части '{original_fn_for_skip}', так как 'content_to_write' отсутствует или None. Причина: {warning_msg_for_skip}")
                    if original_fn_for_skip:
                        processed_original_paths_from_zip.add(original_fn_for_skip)
                    continue

                original_html_path_in_zip = part_data['original_filename']
                content_to_use = part_data['content_to_write']
                image_map_for_this_part = part_data.get('image_map', {})
                is_original = part_data.get('is_original_content', False)

                original_item_info = original_manifest_items_from_zip.get(original_html_path_in_zip)
                if not original_item_info:
                    print(
                        f"[WARN write_epub] Нет записи в манифесте для оригинального HTML: {original_html_path_in_zip}. Пропуск этой части.")
                    processed_original_paths_from_zip.add(original_html_path_in_zip)
                    continue

                original_item_id = original_item_info['id']
                original_href_from_manifest = original_item_info['original_href']  # Путь относительно OPF

                new_html_rel_path_in_epub = ""  # Путь нового файла относительно OPF
                final_html_content_bytes = None

                current_part_canonical_title = canonical_titles_map.get(original_html_path_in_zip)

                if is_original:
                    new_html_rel_path_in_epub = original_href_from_manifest.replace('\\', '/')
                    final_html_content_bytes = content_to_use  # Это уже bytes

                    abs_path_for_map = os.path.normpath(
                        os.path.join(opf_dir_for_new_epub, new_html_rel_path_in_epub)).replace('\\', '/').lstrip('/')
                    filename_map[original_html_path_in_zip] = abs_path_for_map

                    if not current_part_canonical_title and final_html_content_bytes:
                        try:
                            temp_html_str_orig = final_html_content_bytes.decode('utf-8', errors='replace')
                            temp_soup_orig = BeautifulSoup(temp_html_str_orig, 'lxml')
                            extracted_title = None
                            h_tag = temp_soup_orig.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
                            title_tag = temp_soup_orig.head.title if temp_soup_orig.head else None
                            if h_tag and h_tag.get_text(strip=True):
                                extracted_title = h_tag.get_text(strip=True)
                            elif title_tag and title_tag.string:
                                stripped_title = title_tag.string.strip()
                                generic_titles = ['untitled', 'unknown', 'navigation', 'toc', 'table of contents',
                                                  'index', 'contents', 'оглавление', 'содержание', 'индекс', 'cover',
                                                  'title page', 'copyright', 'chapter']
                                if stripped_title and stripped_title.lower() not in generic_titles and len(
                                        stripped_title) > 1:
                                    extracted_title = stripped_title
                            if extracted_title: current_part_canonical_title = extracted_title
                        except Exception as e_title_orig_extract:
                            print(
                                f"[DEBUG write_epub] Ошибка извлечения заголовка из оригинального HTML {original_html_path_in_zip}: {e_title_orig_extract}")

                else:  # Переведенный контент (content_to_use это строка с Markdown-like разметкой и плейсхолдерами)
                    new_html_rel_path_in_epub = add_translated_suffix(original_href_from_manifest).replace('\\', '/')

                    temp_title_for_conversion = current_part_canonical_title
                    if not temp_title_for_conversion and isinstance(content_to_use, str):
                        first_line_md = content_to_use.split('\n', 1)[0].strip()
                        md_h_match = re.match(r'^(#{1,6})\s+(.*)', first_line_md)
                        if md_h_match: temp_title_for_conversion = md_h_match.group(2).strip()
                    if not temp_title_for_conversion:  # Если все еще нет, используем имя файла
                        temp_title_for_conversion = Path(new_html_rel_path_in_epub).stem.replace('_translated',
                                                                                                 '').replace('_',
                                                                                                             ' ').capitalize()

                    final_html_str_rendered = _convert_placeholders_to_html_img(
                        text_with_placeholders=content_to_use,
                        item_image_map_for_this_html=image_map_for_this_part,
                        epub_new_image_objects=new_image_objects_for_manifest,
                        canonical_title=temp_title_for_conversion,  # Используем временный/предполагаемый заголовок
                        current_html_file_path_relative_to_opf=new_html_rel_path_in_epub,
                        opf_dir_path=opf_dir_for_new_epub
                    )

                    actual_translated_title_from_html = None
                    try:
                        soup_final_html = BeautifulSoup(final_html_str_rendered, 'lxml')
                        h1_tag = soup_final_html.body.find('h1') if soup_final_html.body else None
                        if h1_tag and h1_tag.get_text(strip=True):
                            actual_translated_title_from_html = h1_tag.get_text(strip=True)
                        else:
                            title_tag_final = soup_final_html.head.title if soup_final_html.head else None
                            if title_tag_final and title_tag_final.string:
                                stripped_final_title = title_tag_final.string.strip()
                                generic_titles_check = ['untitled', 'unknown', 'navigation', 'toc', 'table of contents',
                                                        'index', 'contents', 'оглавление', 'содержание', 'индекс',
                                                        'cover', 'title page', 'copyright', 'chapter']
                                if stripped_final_title and stripped_final_title.lower() not in generic_titles_check and len(
                                        stripped_final_title) > 1:
                                    actual_translated_title_from_html = stripped_final_title

                        if actual_translated_title_from_html:
                            current_part_canonical_title = actual_translated_title_from_html

                    except Exception as e_title_extract_final:
                        print(
                            f"[WARN write_epub] Не удалось извлечь заголовок из финального HTML для {new_html_rel_path_in_epub}: {e_title_extract_final}")

                    if actual_translated_title_from_html:
                        try:

                            soup_to_update_title = BeautifulSoup(final_html_str_rendered, 'lxml')
                            if soup_to_update_title.head:
                                if soup_to_update_title.head.title:
                                    soup_to_update_title.head.title.string = html.escape(
                                        actual_translated_title_from_html)
                                else:  # Если тега <title> нет, но есть <head>
                                    new_title_tag_in_head = soup_to_update_title.new_tag("title")
                                    new_title_tag_in_head.string = html.escape(actual_translated_title_from_html)
                                    soup_to_update_title.head.insert(0, new_title_tag_in_head)
                                final_html_str_rendered = str(soup_to_update_title)  # Обновляем строку

                        except Exception as e_title_force_update:
                            print(
                                f"[WARN write_epub] Не удалось принудительно обновить тег <title> в {new_html_rel_path_in_epub}: {e_title_force_update}")

                    final_html_content_bytes = final_html_str_rendered.encode('utf-8')
                    abs_path_for_map_translated = os.path.normpath(
                        os.path.join(opf_dir_for_new_epub, new_html_rel_path_in_epub)).replace('\\', '/').lstrip('/')
                    filename_map[original_html_path_in_zip] = abs_path_for_map_translated

                if not current_part_canonical_title:
                    cleaned_stem = Path(new_html_rel_path_in_epub).stem.replace('_translated', '')
                    cleaned_stem = re.sub(r'^[\d_-]+', '', cleaned_stem)  # Удаляем префиксы типа "01_", "001-"
                    cleaned_stem = cleaned_stem.replace('_', ' ').replace('-', ' ').strip()
                    current_part_canonical_title = cleaned_stem.capitalize() if cleaned_stem else f"Документ {original_item_id}"

                canonical_titles_map[
                    original_html_path_in_zip] = current_part_canonical_title  # Обновляем глобальную карту заголовков

                final_html_item_id = original_item_id
                if final_html_item_id in final_book_item_ids:  # Обеспечиваем уникальность ID
                    final_html_item_id = f"html_{Path(new_html_rel_path_in_epub).stem}_{uuid.uuid4().hex[:4]}"

                epub_html_obj = epub.EpubHtml(
                    uid=final_html_item_id,
                    file_name=new_html_rel_path_in_epub,  # Путь относительно OPF
                    title=html.escape(current_part_canonical_title),  # Используем финальный канонический заголовок
                    lang=final_language,
                    content=final_html_content_bytes  # Это всегда bytes
                )
                epub_html_obj.media_type = 'application/xhtml+xml'

                book_items_to_add_to_epub_obj.append(epub_html_obj)
                final_book_item_ids.add(final_html_item_id)

                new_html_abs_path_in_epub_map_key = os.path.normpath(
                    os.path.join(opf_dir_for_new_epub, new_html_rel_path_in_epub)).replace('\\', '/').lstrip('/')
                new_book_items_structure_map[new_html_abs_path_in_epub_map_key] = {
                    'item': epub_html_obj,
                    'content_bytes': final_html_content_bytes,
                    # Сохраняем байты для возможного повторного использования
                    'canonical_title': current_part_canonical_title
                }
                id_to_new_item_map[final_html_item_id] = new_book_items_structure_map[new_html_abs_path_in_epub_map_key]
                processed_original_paths_from_zip.add(
                    original_html_path_in_zip)  # Помечаем оригинальный путь как обработанный

            items_to_skip_copying = set()  # NAV, NCX из build_metadata
            if nav_path_orig_from_meta: items_to_skip_copying.add(nav_path_orig_from_meta)
            if ncx_path_orig_from_meta: items_to_skip_copying.add(ncx_path_orig_from_meta)

            for orig_full_path, orig_item_info in original_manifest_items_from_zip.items():
                if orig_full_path in processed_original_paths_from_zip:  # Уже обработан (HTML или замененное изображение)
                    continue
                if orig_full_path in items_to_skip_copying:  # Явно пропускаемые (старые NAV/NCX)
                    continue
                if orig_item_info.get('properties') and 'nav' in orig_item_info[
                    'properties'].split():  # Пропуск старого NAV по свойству
                    continue

                actual_zip_entry_name = zip_contents_normalized.get(orig_full_path)
                if not actual_zip_entry_name:  # Fallback if case mismatch or slight path variation
                    actual_zip_entry_name = next((o_name for norm_name, o_name in zip_contents_normalized.items() if
                                                  norm_name.lower() == orig_full_path.lower()), None)
                if not actual_zip_entry_name:
                    print(
                        f"[WARN write_epub] Original manifest item '{orig_full_path}' not found in ZIP. Skipping copy.")
                    continue

                try:
                    item_content_bytes = original_zip.read(actual_zip_entry_name)
                    item_id_copy = orig_item_info['id']
                    item_href_copy = orig_item_info['original_href']  # Это путь относительно OPF
                    item_media_type_copy = orig_item_info['media_type']

                    if item_id_copy in final_book_item_ids: item_id_copy = f"item_copy_{Path(item_href_copy).stem}_{uuid.uuid4().hex[:3]}"

                    new_item_obj_copy = None
                    if item_media_type_copy.startswith('image/'):
                        new_item_obj_copy = epub.EpubImage(uid=item_id_copy, file_name=item_href_copy,
                                                           media_type=item_media_type_copy, content=item_content_bytes)
                    elif item_media_type_copy == 'text/css':
                        new_item_obj_copy = epub.EpubItem(uid=item_id_copy, file_name=item_href_copy,
                                                          media_type=item_media_type_copy, content=item_content_bytes)
                    elif item_media_type_copy.startswith('font/') or item_media_type_copy in ['application/font-woff',
                                                                                              'application/vnd.ms-opentype',
                                                                                              'application/octet-stream',
                                                                                              'application/x-font-ttf']:
                        new_item_obj_copy = epub.EpubItem(uid=item_id_copy, file_name=item_href_copy,
                                                          media_type=item_media_type_copy, content=item_content_bytes)
                    else:  # Другие типы файлов
                        new_item_obj_copy = epub.EpubItem(uid=item_id_copy, file_name=item_href_copy,
                                                          media_type=item_media_type_copy, content=item_content_bytes)

                    if new_item_obj_copy:
                        book_items_to_add_to_epub_obj.append(new_item_obj_copy)
                        final_book_item_ids.add(item_id_copy)

                        filename_map[orig_full_path] = os.path.normpath(
                            os.path.join(opf_dir_for_new_epub, item_href_copy)).replace('\\', '/').lstrip('/')

                        new_abs_path_copy = filename_map[orig_full_path]
                        new_book_items_structure_map[new_abs_path_copy] = {'item': new_item_obj_copy,
                                                                           'content_bytes': item_content_bytes,
                                                                           'canonical_title': None}
                        id_to_new_item_map[item_id_copy] = new_book_items_structure_map[new_abs_path_copy]
                        processed_original_paths_from_zip.add(orig_full_path)
                except KeyError:
                    print(
                        f"[WARN write_epub] Original manifest item '{orig_full_path}' (href: {orig_item_info.get('original_href')}) could not be read from ZIP. Skipping.")
                except Exception as e_copy:
                    print(f"[ERROR write_epub] Failed to copy original manifest item '{orig_full_path}': {e_copy}")

            for item_obj in book_items_to_add_to_epub_obj:
                try:
                    book.add_item(item_obj)
                except Exception as add_final_err:
                    print(
                        f"[ERROR write_epub] Failed to add item ID='{getattr(item_obj, 'id', 'N/A')}' to book: {add_final_err}")

            final_nav_item_obj = None;
            final_ncx_item_obj = None
            new_nav_content_bytes = None;
            new_ncx_content_bytes = None

            final_nav_rel_path_in_epub = "nav.xhtml"  # Стандартное имя
            final_ncx_rel_path_in_epub = "toc.ncx"  # Стандартное имя

            spine_item_objects_for_toc_gen = []
            for orig_idref in original_spine_idrefs_from_zip:

                original_item_path_for_idref = next(
                    (p for p, i_info in original_manifest_items_from_zip.items() if i_info['id'] == orig_idref), None)
                if not original_item_path_for_idref: continue

                new_item_abs_path = filename_map.get(original_item_path_for_idref)
                if not new_item_abs_path: continue

                new_item_entry = new_book_items_structure_map.get(new_item_abs_path)
                if not new_item_entry or not new_item_entry.get('item'): continue

                new_epub_item_obj = new_item_entry['item']
                if isinstance(new_epub_item_obj, epub.EpubHtml) and new_epub_item_obj.file_name.replace('\\',
                                                                                                        '/') != final_nav_rel_path_in_epub:
                    item_title_for_toc = canonical_titles_map.get(original_item_path_for_idref,
                                                                  Path(new_epub_item_obj.file_name).stem)
                    spine_item_objects_for_toc_gen.append((new_epub_item_obj, item_title_for_toc))

            nav_item_id_to_use = nav_id_orig_from_meta or "nav"
            ncx_item_id_to_use = ncx_id_orig_from_meta or ncx_id_from_spine_attr or "ncx"

            if nav_path_orig_from_meta and nav_path_orig_from_meta in zip_contents_normalized:  # Был NAV
                print(f"[INFO write_epub] Обновление существующего NAV: {nav_path_orig_from_meta}")
                orig_nav_bytes = original_zip.read(zip_contents_normalized[nav_path_orig_from_meta])
                new_nav_content_bytes = update_nav_content(orig_nav_bytes, nav_path_orig_from_meta, filename_map,
                                                           canonical_titles_map)
                if new_nav_content_bytes: final_nav_rel_path_in_epub = Path(
                    nav_path_orig_from_meta).name  # Сохраняем оригинальное имя файла NAV
            elif spine_item_objects_for_toc_gen:  # Не было NAV, но есть что добавить в spine
                print("[INFO write_epub] Генерация нового NAV из элементов spine...")
                nav_data_for_gen_html = []
                for item_obj_nav, title_nav in spine_item_objects_for_toc_gen:
                    abs_path_for_nav_href = os.path.normpath(
                        os.path.join(opf_dir_for_new_epub, item_obj_nav.file_name)).replace('\\', '/').lstrip('/')
                    nav_data_for_gen_html.append((abs_path_for_nav_href, title_nav))
                new_nav_content_bytes = generate_nav_html(nav_data_for_gen_html,
                                                          os.path.join(opf_dir_for_new_epub,
                                                                       final_nav_rel_path_in_epub).replace('\\',
                                                                                                           '/').lstrip(
                                                              '/'),
                                                          final_book_title, final_language)

            if ncx_path_orig_from_meta and ncx_path_orig_from_meta in zip_contents_normalized:  # Был NCX
                print(f"[INFO write_epub] Обновление существующего NCX: {ncx_path_orig_from_meta}")
                orig_ncx_bytes = original_zip.read(zip_contents_normalized[ncx_path_orig_from_meta])
                new_ncx_content_bytes = update_ncx_content(orig_ncx_bytes, opf_dir_from_meta, filename_map,
                                                           canonical_titles_map)
                if new_ncx_content_bytes: final_ncx_rel_path_in_epub = Path(
                    ncx_path_orig_from_meta).name  # Сохраняем оригинальное имя файла NCX
            elif new_nav_content_bytes:  # Не было NCX, но сгенерировали NAV, из него генерируем NCX
                print("[INFO write_epub] Генерация нового NCX из данных нового NAV...")

                nav_path_for_ncx_parse_abs = os.path.normpath(
                    os.path.join(opf_dir_for_new_epub, final_nav_rel_path_in_epub)).replace('\\', '/').lstrip('/')
                ncx_data_from_new_nav = parse_nav_for_ncx_data(new_nav_content_bytes, nav_path_for_ncx_parse_abs)
                if ncx_data_from_new_nav:
                    new_ncx_content_bytes = generate_ncx_manual(final_identifier, final_book_title,
                                                                ncx_data_from_new_nav)
            elif spine_item_objects_for_toc_gen:  # Не было ни NAV, ни NCX, генерируем NCX из spine
                print("[INFO write_epub] Генерация нового NCX из элементов spine (NAV не был сгенерирован)...")
                ncx_data_from_spine_gen = []
                for i_ncx, (item_obj_ncx, title_ncx) in enumerate(spine_item_objects_for_toc_gen):
                    ncx_src_for_gen = item_obj_ncx.file_name.replace('\\', '/')  # Относительно OPF
                    safe_base_ncx = re.sub(r'[^\w\-]+', '_', Path(ncx_src_for_gen).stem);
                    nav_point_id_ncx = f"navpoint_{safe_base_ncx}_{i_ncx + 1}"
                    ncx_data_from_spine_gen.append((nav_point_id_ncx, ncx_src_for_gen, title_ncx))
                if ncx_data_from_spine_gen:
                    new_ncx_content_bytes = generate_ncx_manual(final_identifier, final_book_title,
                                                                ncx_data_from_spine_gen)

            if new_nav_content_bytes:
                if nav_item_id_to_use in final_book_item_ids: nav_item_id_to_use = f"{nav_item_id_to_use}_{uuid.uuid4().hex[:4]}"
                final_nav_item_obj = epub.EpubHtml(uid=nav_item_id_to_use, file_name=final_nav_rel_path_in_epub,
                                                   title=final_book_title, lang=final_language,
                                                   content=new_nav_content_bytes)
                final_nav_item_obj.media_type = 'application/xhtml+xml'

                if 'nav' not in final_nav_item_obj.properties:  # Проверяем, нет ли уже такого свойства
                    final_nav_item_obj.properties.append('nav')

                book.add_item(final_nav_item_obj);
                final_book_item_ids.add(nav_item_id_to_use)
                book.toc = (final_nav_item_obj,)  # Устанавливаем NAV как TOC
                print(
                    f"[INFO write_epub] NAV добавлен/обновлен. ID: {nav_item_id_to_use}, Path: {final_nav_rel_path_in_epub}")
            else:
                book.toc = ()
                print(f"[INFO write_epub] NAV контент не был сгенерирован/обновлен. book.toc будет пуст.")

            if new_ncx_content_bytes:
                if ncx_item_id_to_use in final_book_item_ids: ncx_item_id_to_use = f"{ncx_item_id_to_use}_{uuid.uuid4().hex[:4]}"
                final_ncx_item_obj = epub.EpubItem(uid=ncx_item_id_to_use, file_name=final_ncx_rel_path_in_epub,
                                                   media_type='application/x-dtbncx+xml', content=new_ncx_content_bytes)
                book.add_item(final_ncx_item_obj);
                final_book_item_ids.add(ncx_item_id_to_use)

                book.spine_toc = final_ncx_item_obj.id

                print(
                    f"[INFO write_epub] NCX добавлен/обновлен. ID: {ncx_item_id_to_use}, Path: {final_ncx_rel_path_in_epub}")
            elif ncx_id_from_spine_attr:
                existing_ncx_item = book.get_item_with_id(ncx_id_from_spine_attr)
                if existing_ncx_item and existing_ncx_item.media_type == 'application/x-dtbncx+xml':
                    book.spine_toc = ncx_id_from_spine_attr
                    print(f"[INFO write_epub] Использован существующий NCX из spine: ID={ncx_id_from_spine_attr}")

            final_spine_idrefs_for_book = []
            for orig_idref_spine in original_spine_idrefs_from_zip:
                original_path_for_idref_spine = next(
                    (p for p, item_info_spine in original_manifest_items_from_zip.items() if
                     item_info_spine['id'] == orig_idref_spine), None)
                if not original_path_for_idref_spine: continue
                new_abs_path_for_idref_spine = filename_map.get(original_path_for_idref_spine)
                if not new_abs_path_for_idref_spine: continue
                new_item_entry_for_idref_spine = new_book_items_structure_map.get(new_abs_path_for_idref_spine)
                if new_item_entry_for_idref_spine and new_item_entry_for_idref_spine.get('item'):
                    final_spine_idrefs_for_book.append(new_item_entry_for_idref_spine['item'].id)

            if not final_spine_idrefs_for_book and spine_item_objects_for_toc_gen:  # Fallback, если original_spine_idrefs_from_zip пуст
                final_spine_idrefs_for_book = [item_obj_s.id for item_obj_s, _ in spine_item_objects_for_toc_gen]

            book.spine = final_spine_idrefs_for_book
            if not book.spine:  # Крайний случай: добавляем первый HTML, если spine пуст
                first_html_item = next(
                    (item for item in book.items if isinstance(item, epub.EpubHtml) and item != final_nav_item_obj),
                    None)
                if first_html_item:
                    book.spine = [first_html_item.id]
                else:
                    print("[WARN write_epub] Не удалось сформировать spine, нет подходящих HTML элементов.")

            print(f"[INFO write_epub] Запись финального EPUB файла в: {out_path}...")
            epub.write_epub(out_path, book, {})  # Опции по умолчанию
            end_time = time.time()
            print(f"[SUCCESS] EPUB Rebuild: Файл сохранен: {out_path} (Заняло {end_time - start_time:.2f} сек)")
            return True, None

    except FileNotFoundError as e_fnf:
        err_msg = f"EPUB Rebuild Error: Файл не найден - {e_fnf}";
        print(f"[ERROR] {err_msg}");
        return False, err_msg
    except (zipfile.BadZipFile, etree.XMLSyntaxError) as e_xml_zip:
        err_msg = f"EPUB Rebuild Error: Не удалось разобрать структуру EPUB - {e_xml_zip}";
        print(f"[ERROR] {err_msg}");
        return False, err_msg
    except ImportError as e_imp:
        err_msg = f"EPUB Rebuild Error: Отсутствует библиотека - {e_imp}";
        print(f"[ERROR] {err_msg}");
        return False, err_msg
    except ValueError as e_val:
        err_msg = f"EPUB Rebuild Error: {e_val}";
        print(f"[ERROR] {err_msg}");
        return False, err_msg
    except Exception as e_generic:
        tb_str = traceback.format_exc()
        err_msg = f"EPUB Rebuild Error: Неожиданная ошибка - {type(e_generic).__name__}: {e_generic}"
        print(f"[ERROR] {err_msg}\n{tb_str}");
        return False, err_msg

