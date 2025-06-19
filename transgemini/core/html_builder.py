import base64
import html
import os
import re
from pathlib import Path

from transgemini.core.utils import find_image_placeholders


def _convert_placeholders_to_html_img(text_with_placeholders, item_image_map_for_this_html,
                                      epub_new_image_objects,
                                      canonical_title,
                                      current_html_file_path_relative_to_opf=None,
                                      opf_dir_path=None):
    if not text_with_placeholders: return ""
    if item_image_map_for_this_html is None: item_image_map_for_this_html = {}
    if epub_new_image_objects is None: epub_new_image_objects = {}

    def apply_inline_markdown_carefully(text_segment):
        known_tags_map = {}
        temp_id_counter = 0

        def tag_replacer(match):
            nonlocal temp_id_counter
            tag = match.group(0)
            placeholder = f"__HTML_TAG_PLACEHOLDER_{temp_id_counter}__"
            known_tags_map[placeholder] = tag
            temp_id_counter += 1
            return placeholder

        text_with_placeholders_for_tags = re.sub(r'(<br\s*/?>|<img\s+[^>]*?/>)', tag_replacer, text_segment,
                                                 flags=re.IGNORECASE | re.DOTALL)

        def markdown_replacer(match_md):
            marker = match_md.group(1)
            content_to_wrap = html.escape(match_md.group(2))
            if marker == '**': return f'<strong>{content_to_wrap}</strong>'
            if marker == '*':  return f'<em>{content_to_wrap}</em>'
            if marker == '`':  return f'<code>{content_to_wrap}</code>'
            return match_md.group(0)

        processed_text_with_md = re.sub(r'(\*\*|\*|`)(.+?)\1', markdown_replacer, text_with_placeholders_for_tags,
                                        flags=re.DOTALL)
        final_text = processed_text_with_md
        for placeholder, original_tag in known_tags_map.items():
            final_text = final_text.replace(placeholder, original_tag)
        return final_text

    processed_parts_for_img_restore = []
    last_idx_img_restore = 0

    for placeholder_tag, img_uuid in find_image_placeholders(text_with_placeholders):
        match_start = text_with_placeholders.find(placeholder_tag, last_idx_img_restore)
        if match_start == -1: continue
        processed_parts_for_img_restore.append(text_with_placeholders[last_idx_img_restore:match_start])
        img_info = item_image_map_for_this_html.get(img_uuid)
        img_tag_html = f"<!-- Placeholder Error: UUID {img_uuid} not fully processed -->"
        if img_info:
            original_src_from_html_map = img_info.get('original_src')
            final_img_src_attr_value_for_tag = None
            final_attributes_for_tag = dict(img_info.get('attributes', {}))
            if original_src_from_html_map is not None:
                final_img_src_attr_value_for_tag = original_src_from_html_map
                final_attributes_for_tag.pop('{http://www.w3.org/1999/xlink}href', None)
                final_attributes_for_tag.pop('xlink:href', None)
            elif img_uuid in epub_new_image_objects:
                epub_img_object_for_new = epub_new_image_objects.get(img_uuid)
                if epub_img_object_for_new:
                    image_path_rel_to_opf_for_new = epub_img_object_for_new.file_name.replace('\\', '/')
                    if current_html_file_path_relative_to_opf is not None:
                        html_dir_rel_to_opf_for_new = os.path.dirname(current_html_file_path_relative_to_opf).replace(
                            '\\', '/')
                        if html_dir_rel_to_opf_for_new == '.': html_dir_rel_to_opf_for_new = ""
                        try:
                            final_img_src_attr_value_for_tag = os.path.relpath(image_path_rel_to_opf_for_new,
                                                                               start=html_dir_rel_to_opf_for_new).replace(
                                '\\', '/')
                        except ValueError:
                            final_img_src_attr_value_for_tag = image_path_rel_to_opf_for_new
                    else:
                        final_img_src_attr_value_for_tag = image_path_rel_to_opf_for_new
            if final_img_src_attr_value_for_tag is not None:
                alt_text_raw = final_attributes_for_tag.get('alt',
                                                            img_info.get('original_filename', f'Image {img_uuid[:7]}'))
                alt_text_escaped = html.escape(str(alt_text_raw), quote=True)
                attr_strings_list = [f'src="{html.escape(final_img_src_attr_value_for_tag, quote=True)}"',
                                     f'alt="{alt_text_escaped}"']
                for key, value in final_attributes_for_tag.items():
                    key_lower = str(key).lower()
                    if key_lower not in ['src', 'alt', 'xlink:href', '{http://www.w3.org/1999/xlink}href']:
                        attr_strings_list.append(f'{html.escape(str(key))}="{html.escape(str(value))}"')
                width_attr = final_attributes_for_tag.get('width');
                height_attr = final_attributes_for_tag.get('height')
                styles_to_add = []
                if not width_attr or (isinstance(width_attr, str) and '%' in width_attr): styles_to_add.append(
                    "max-width: 100%;")
                if not height_attr or (isinstance(height_attr, str) and '%' in height_attr):
                    if "max-width: 100%;" in styles_to_add and not height_attr: styles_to_add.append("height: auto;")
                if styles_to_add: attr_strings_list.append(f'style="{html.escape(" ".join(styles_to_add))}"')
                img_tag_html = f"<img {' '.join(attr_strings_list)} />"
        processed_parts_for_img_restore.append(img_tag_html)
        last_idx_img_restore = match_start + len(placeholder_tag)
    processed_parts_for_img_restore.append(text_with_placeholders[last_idx_img_restore:])
    text_after_img_restore = "".join(processed_parts_for_img_restore)

    text_normalized_newlines = re.sub(r'<br\s*/?>', '\n', text_after_img_restore, flags=re.IGNORECASE)

    text_normalized_newlines = re.sub(r'\n{3,}', '\n\n', text_normalized_newlines)

    lines = text_normalized_newlines.splitlines()  # Делим по \n.

    html_body_segments = []
    paragraph_part_buffer = []
    current_list_tag_md = None
    in_code_block_md = False
    code_block_buffer_md = []

    heading_re_md = re.compile(r'^\s*(#{1,6})\s+(.*)')
    hr_re_md = re.compile(r'^\s*---\s*$')
    ul_item_re_md = re.compile(r'^\s*[\*\-]\s+(.*)')
    ol_item_re_md = re.compile(r'^\s*\d+\.\s+(.*)')
    code_fence_re_md = re.compile(r'^\s*```(.*)')

    def finalize_paragraph_md():
        nonlocal paragraph_part_buffer, html_body_segments
        if paragraph_part_buffer:
            para_content_raw = "<br />".join(paragraph_part_buffer)  # Восстанавливаем <br />
            processed_content = apply_inline_markdown_carefully(para_content_raw)
            html_body_segments.append(f"<p>{processed_content}</p>")
            paragraph_part_buffer = []

    def finalize_list_md():
        nonlocal current_list_tag_md, html_body_segments
        if current_list_tag_md:
            html_body_segments.append(f"</{current_list_tag_md}>")
            current_list_tag_md = None

    def finalize_code_block_md():
        nonlocal in_code_block_md, code_block_buffer_md, html_body_segments
        if code_block_buffer_md:
            escaped_code = html.escape("\n".join(code_block_buffer_md))
            html_body_segments.append(escaped_code)
        if in_code_block_md:
            html_body_segments.append("</code></pre>")
            in_code_block_md = False
        code_block_buffer_md = []

    for i, line_text in enumerate(lines):  # line_text это строка без \n на конце
        stripped_line = line_text.strip()

        is_standalone_image = False
        if stripped_line.startswith("<img") and stripped_line.endswith("/>"):
            if re.fullmatch(r'\s*<img\s+[^>]*?/>\s*', line_text, re.IGNORECASE):
                is_standalone_image = True

        if is_standalone_image:
            finalize_paragraph_md()
            finalize_list_md()
            finalize_code_block_md()
            html_body_segments.append(line_text)
            continue

        code_fence_match = code_fence_re_md.match(stripped_line)
        if code_fence_match:
            finalize_paragraph_md()
            finalize_list_md()
            if not in_code_block_md:
                in_code_block_md = True
                code_block_buffer_md = []
                lang = html.escape(code_fence_match.group(1).strip())
                html_body_segments.append(f'<pre><code class="language-{lang}">' if lang else "<pre><code>")
            else:
                finalize_code_block_md()
            continue

        if in_code_block_md:
            code_block_buffer_md.append(line_text)
            continue

        if not stripped_line:  # Если строка пуста ПОСЛЕ strip
            finalize_paragraph_md()
            finalize_list_md()

            continue  # Переходим к следующей строке

        heading_match = heading_re_md.match(line_text)
        hr_match = hr_re_md.match(stripped_line)  # hr всегда на всю строку
        ul_item_match = ul_item_re_md.match(line_text)
        ol_item_match = ol_item_re_md.match(line_text)

        is_block_markdown = bool(heading_match or hr_match or ul_item_match or ol_item_match)

        if is_block_markdown:
            finalize_paragraph_md()

        if heading_match:
            finalize_list_md()
            level = len(heading_match.group(1))
            heading_text_raw = heading_match.group(2).strip()  # strip() здесь, т.к. это содержимое тега
            processed_heading_text = apply_inline_markdown_carefully(heading_text_raw)
            html_body_segments.append(f"<h{level}>{processed_heading_text}</h{level}>")
        elif hr_match:
            finalize_list_md()
            html_body_segments.append("<hr />")
        elif ul_item_match:
            if current_list_tag_md != 'ul':
                finalize_list_md()
                html_body_segments.append("<ul>")
                current_list_tag_md = 'ul'
            list_item_raw = ul_item_match.group(1).strip()  # strip() здесь
            processed_list_item = apply_inline_markdown_carefully(list_item_raw)
            html_body_segments.append(f"<li>{processed_list_item}</li>")
        elif ol_item_match:
            if current_list_tag_md != 'ol':
                finalize_list_md()
                html_body_segments.append("<ol>")
                current_list_tag_md = 'ol'
            list_item_raw = ol_item_match.group(1).strip()  # strip() здесь
            processed_list_item = apply_inline_markdown_carefully(list_item_raw)
            html_body_segments.append(f"<li>{processed_list_item}</li>")
        else:  # Если это не MD-блок и не пустая строка (уже проверили stripped_line)
            finalize_list_md()  # Закрыть список, если эта строка не является его продолжением

            paragraph_part_buffer.append(line_text)

    finalize_paragraph_md()
    finalize_list_md()
    finalize_code_block_md()

    body_content_final = "\n".join(html_body_segments)

    final_title_text_for_html_tag = html.escape(
        str(canonical_title or Path(current_html_file_path_relative_to_opf or "document").stem).strip())
    if not final_title_text_for_html_tag: final_title_text_for_html_tag = "Untitled Document"
    stylesheet_path_final = "../Styles/stylesheet.css"
    if current_html_file_path_relative_to_opf is not None:
        html_abs_dir_in_epub = ""
        if opf_dir_path:
            abs_html_path_in_epub = os.path.join(opf_dir_path, current_html_file_path_relative_to_opf)
            html_abs_dir_in_epub = os.path.dirname(abs_html_path_in_epub)
        else:
            html_abs_dir_in_epub = os.path.dirname(current_html_file_path_relative_to_opf)
        if html_abs_dir_in_epub == '.': html_abs_dir_in_epub = ""
        css_dir_from_root = os.path.join(opf_dir_path or "", "Styles")
        abs_stylesheet_path_in_epub = os.path.join(css_dir_from_root, "stylesheet.css")
        abs_stylesheet_path_in_epub = os.path.normpath(abs_stylesheet_path_in_epub).replace('\\', '/')
        html_abs_dir_in_epub = os.path.normpath(html_abs_dir_in_epub).replace('\\', '/')
        try:
            stylesheet_path_final = os.path.relpath(abs_stylesheet_path_in_epub, start=html_abs_dir_in_epub).replace(
                '\\', '/')
        except ValueError:
            if not html_abs_dir_in_epub:
                stylesheet_path_final = abs_stylesheet_path_in_epub.lstrip('/')
            else:
                stylesheet_path_final = abs_stylesheet_path_in_epub
    stylesheet_link_tag = f'<link rel="stylesheet" type="text/css" href="{html.escape(stylesheet_path_final, quote=True)}"/>'
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="ru" xml:lang="ru">
<head>
<meta charset="utf-8" />
<title>{final_title_text_for_html_tag}</title>
{stylesheet_link_tag}
</head>
<body>
{body_content_final}
</body>
</html>"""

def write_to_html(out_path, translated_content_with_placeholders, image_map, title):
    """Creates HTML file with embedded Base64 images."""
    if image_map is None: image_map = {}
    print(f"[INFO] HTML: Creating HTML file with embedded images: {out_path}")
    html_body_content = ""

    lines = translated_content_with_placeholders.splitlines()  # Разделяем по \n, если они там есть (обычно нет, если <br />)
    paragraph_buffer = []

    def process_text_block_for_html(text_block):

        processed_parts = []
        last_index = 0

        text_block_escaped_amp = text_block.replace('&', '&')

        text_block_br_protected = re.sub(r'<br\s*/?>', '__TEMP_BR_TAG__', text_block_escaped_amp, flags=re.IGNORECASE)

        text_block_lt_gt_escaped = text_block_br_protected.replace('<', '<').replace('>', '>')

        temp_md_text = text_block_lt_gt_escaped
        temp_md_text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', temp_md_text, flags=re.DOTALL)
        temp_md_text = re.sub(r'(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)', r'<em>\1</em>', temp_md_text, flags=re.DOTALL)
        temp_md_text = re.sub(r'`(.*?)`', r'<code>\1</code>', temp_md_text, flags=re.DOTALL)

        final_md_text = temp_md_text.replace('<strong>', '<strong>').replace('</strong>', '</strong>')
        final_md_text = final_md_text.replace('<em>', '<em>').replace('</em>', '</em>')
        final_md_text = final_md_text.replace('<code>', '<code>').replace('</code>', '</code>')

        text_with_md_and_br = final_md_text.replace('__TEMP_BR_TAG__', '<br />')

        placeholders = find_image_placeholders(text_with_md_and_br)  # Ищем плейсхолдеры в тексте с Markdown и <br />

        for placeholder_tag, img_uuid in placeholders:
            match_start = text_with_md_and_br.find(placeholder_tag, last_index)
            if match_start == -1: continue

            text_before = text_with_md_and_br[last_index:match_start]
            processed_parts.append(text_before)  # Добавляем текст "как есть", он уже обработан

            if img_uuid in image_map:
                img_info = image_map[img_uuid];
                img_path = img_info['saved_path']
                if os.path.exists(img_path):
                    try:
                        with open(img_path, 'rb') as f_img:
                            img_data = f_img.read()
                        b64_data = base64.b64encode(img_data).decode('ascii')
                        content_type = img_info.get('content_type', 'image/jpeg');
                        data_uri = f"data:{content_type};base64,{b64_data}"
                        alt_text_raw = img_info.get('original_filename', f'Image {img_uuid[:8]}');

                        alt_text = html.escape(alt_text_raw, quote=True)
                        img_tag = f'<img src="{html.escape(data_uri, quote=True)}" alt="{alt_text}" style="max-width: 100%; height: auto;" />'
                        processed_parts.append(img_tag)
                    except Exception as img_err:
                        print(
                            f"[ERROR] HTML Write: Failed to read/encode image {img_path}: {img_err}"); processed_parts.append(
                            f"[Err embed img: {img_uuid[:8]}]")
                else:
                    print(f"[ERROR] HTML Write: Image path not found: {img_path}"); processed_parts.append(
                        f"[Img path miss: {img_uuid[:8]}]")
            else:
                print(f"[WARN] HTML Write: Placeholder UUID '{img_uuid}' not found."); processed_parts.append(
                    f"[Unk Img: {img_uuid[:8]}]")
            last_index = match_start + len(placeholder_tag)

        text_after = text_with_md_and_br[last_index:]
        processed_parts.append(text_after)  # Добавляем остаток текста "как есть"
        return "".join(processed_parts)

    current_list_type = None
    in_code_block = False
    code_block_lines = []

    for line in lines:  # line может содержать <br />
        stripped_line = line.strip()
        is_code_fence = stripped_line == '```'

        if is_code_fence:
            if not in_code_block:
                if paragraph_buffer: html_body_content += f"<p>{process_text_block_for_html('<br/>'.join(paragraph_buffer))}</p>\n"; paragraph_buffer = []
                if current_list_type: html_body_content += f"</{current_list_type}>\n"; current_list_type = None
                in_code_block = True;
                code_block_lines = []
            else:
                in_code_block = False
                escaped_code = html.escape("\n".join(code_block_lines))  # Экранируем все содержимое блока кода
                html_body_content += f"<pre><code>{escaped_code}</code></pre>\n"
            continue

        if in_code_block:
            code_block_lines.append(line);
            continue

        heading_match = re.match(r'^(#{1,6})\s+(.*)', stripped_line)
        hr_match = stripped_line == '---'
        ul_match = re.match(r'^[\*\-]\s+(.*)', stripped_line)
        ol_match = re.match(r'^\d+\.\s+(.*)', stripped_line)

        if current_list_type and not (
                (current_list_type == 'ul' and ul_match) or (current_list_type == 'ol' and ol_match)):
            html_body_content += f"</{current_list_type}>\n";
            current_list_type = None
        if paragraph_buffer and (heading_match or hr_match or ul_match or ol_match):
            para_content = process_text_block_for_html("<br/>".join(paragraph_buffer));
            html_body_content += f"<p>{para_content}</p>\n" if para_content.strip() else "";
            paragraph_buffer = []

        if heading_match:
            level = len(heading_match.group(1));
            heading_text = process_text_block_for_html(heading_match.group(2).strip())
            if heading_text: html_body_content += f"<h{level}>{heading_text}</h{level}>\n"
        elif hr_match:
            html_body_content += "<hr/>\n"
        elif ul_match:
            if current_list_type != 'ul': html_body_content += "<ul>\n"; current_list_type = 'ul'
            list_text = process_text_block_for_html(ul_match.group(1).strip());
            html_body_content += f"<li>{list_text}</li>\n"
        elif ol_match:
            if current_list_type != 'ol': html_body_content += "<ol>\n"; current_list_type = 'ol'
            list_text = process_text_block_for_html(ol_match.group(1).strip());
            html_body_content += f"<li>{list_text}</li>\n"
        elif line or find_image_placeholders(line):
            paragraph_buffer.append(line)  # line уже содержит <br /> если они были
        elif not stripped_line and paragraph_buffer:

            para_content = process_text_block_for_html(
                "".join(paragraph_buffer));  # Не соединяем через <br/>, т.к. они уже есть
            html_body_content += f"<p>{para_content}</p>\n" if para_content.strip() else "";
            paragraph_buffer = []

    if current_list_type: html_body_content += f"</{current_list_type}>\n"
    if paragraph_buffer:
        para_content = process_text_block_for_html("".join(paragraph_buffer));  # Не соединяем через <br/>
        html_body_content += f"<p>{para_content}</p>\n" if para_content.strip() else ""
    if in_code_block:
        escaped_code = html.escape("\n".join(code_block_lines))
        html_body_content += f"<pre><code>{escaped_code}</code></pre>\n"

    safe_title = html.escape(title or "Переведенный документ")
    html_template = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{safe_title}</title>
<style>
body {{ font-family: sans-serif; line-height: 1.6; margin: 2em auto; max-width: 800px; padding: 0 1em; color: #333; background-color: #fdfdfd; }}
p {{ margin-top: 0; margin-bottom: 1em; text-align: justify; }}
h1, h2, h3, h4, h5, h6 {{ margin-top: 1.8em; margin-bottom: 0.6em; line-height: 1.3; font-weight: normal; color: #111; border-bottom: 1px solid #eee; padding-bottom: 0.2em;}}
h1 {{ font-size: 2em; }} h2 {{ font-size: 1.7em; }} h3 {{ font-size: 1.4em; }}
img {{ max-width: 100%; height: auto; display: block; margin: 1.5em auto; border: 1px solid #ddd; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
hr {{ border: none; border-top: 1px solid #ccc; margin: 2.5em 0; }}
ul, ol {{ margin-left: 1.5em; margin-bottom: 1em; padding-left: 1.5em; }}
li {{ margin-bottom: 0.4em; }}
strong {{ font-weight: bold; }}
em {{ font-style: italic; }}
a {{ color: #007bff; text-decoration: none; }} a:hover {{ text-decoration: underline; }}
code {{ background-color: #f0f0f0; padding: 0.1em 0.3em; border-radius: 3px; font-family: Consolas, monospace; font-size: 0.9em; }}
pre {{ background-color: #f5f5f5; border: 1px solid #ddd; border-radius: 4px; padding: 1em; overflow-x: auto; white-space: pre; }}
pre code {{ background-color: transparent; padding: 0; border-radius: 0; font-size: 0.9em; }}
</style>
</head>
<body>
{html_body_content.strip()}
</body>
</html>"""
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_template)
        print(f"[SUCCESS] HTML file saved: {out_path}")
    except Exception as write_err:
        print(f"[ERROR] Failed to write HTML file {out_path}: {write_err}"); raise