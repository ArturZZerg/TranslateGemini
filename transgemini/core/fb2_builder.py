import base64
import os
import re
import time

from lxml import etree

from transgemini.config import LXML_AVAILABLE
from transgemini.core.utils import find_image_placeholders


def write_to_fb2(out_path, translated_content_with_placeholders, image_map, title):
    if not LXML_AVAILABLE: raise ImportError("lxml library is required to write FB2 files.")
    if image_map is None: image_map = {}
    print(f"[INFO] FB2: Creating FB2 file with image support: {out_path}")

    print(f"DEBUG write_to_fb2: image_map received with {len(image_map)} entries.")
    if image_map:
        print(f"  UUIDs in received image_map: {list(image_map.keys())}")

    placeholders_in_text = find_image_placeholders(translated_content_with_placeholders)
    print(f"DEBUG write_to_fb2: Found {len(placeholders_in_text)} placeholders in translated_content.")
    if placeholders_in_text:
        print(f"  UUIDs from placeholders in text: {[p[1] for p in placeholders_in_text]}")

    FB2_NS = "http://www.gribuser.ru/xml/fictionbook/2.0";
    XLINK_NS = "http://www.w3.org/1999/xlink";
    nsmap = {None: FB2_NS, "l": XLINK_NS}
    l_href_attr = f"{{{XLINK_NS}}}href"
    fb2_root = etree.Element("FictionBook", nsmap=nsmap)

    description = etree.SubElement(fb2_root, "description")
    title_info = etree.SubElement(description, "title-info")
    document_info = etree.SubElement(description, "document-info")
    book_title_text = title or "Переведенный Документ"  # Renamed variable
    etree.SubElement(title_info, "book-title").text = book_title_text
    author_elem = etree.SubElement(title_info, "author");
    etree.SubElement(author_elem, "first-name").text = "Translator"
    etree.SubElement(title_info, "genre").text = "unspecified";
    etree.SubElement(title_info, "lang").text = "ru"
    doc_author = etree.SubElement(document_info, "author");
    etree.SubElement(doc_author, "nickname").text = "TranslatorApp"
    etree.SubElement(document_info, "program-used").text = "TranslatorApp using Gemini";
    etree.SubElement(document_info, "date", attrib={"value": time.strftime("%Y-%m-%d")}).text = time.strftime(
        "%d %B %Y", time.localtime());
    etree.SubElement(document_info, "version").text = "1.0"

    binary_sections = [];
    placeholder_to_binary_id = {};
    binary_id_counter = 1
    processed_uuids_for_binary = set()
    images_added_to_binary_count = 0  # New counter

    for placeholder_tag, img_uuid_from_text in placeholders_in_text:  # Renamed img_uuid
        print(f"DEBUG write_to_fb2: Processing placeholder for UUID from text: {img_uuid_from_text}")
        if img_uuid_from_text in image_map and img_uuid_from_text not in processed_uuids_for_binary:
            img_info = image_map[img_uuid_from_text]
            img_path = img_info.get('saved_path')
            print(f"  UUID {img_uuid_from_text} found in image_map. Path: {img_path}")

            if img_path and os.path.exists(img_path):
                try:
                    with open(img_path, 'rb') as f_img:
                        img_data = f_img.read()
                    base_id = f"img_{img_uuid_from_text[:8]}_{binary_id_counter}";
                    binary_id = re.sub(r'[^\w.-]', '_', base_id)
                    content_type = img_info.get('content_type', 'image/jpeg')
                    base64_encoded_data = base64.b64encode(img_data).decode('ascii')

                    binary_sections.append((binary_id, content_type, base64_encoded_data))
                    placeholder_to_binary_id[img_uuid_from_text] = binary_id
                    processed_uuids_for_binary.add(img_uuid_from_text)
                    binary_id_counter += 1
                    images_added_to_binary_count += 1  # Increment counter
                    print(
                        f"    Successfully prepared binary data for UUID {img_uuid_from_text}. Binary ID: {binary_id}")
                except Exception as e:
                    print(f"[ERROR] FB2: Failed to read/encode image {img_path} for UUID {img_uuid_from_text}: {e}")
            elif not img_path:
                print(f"[ERROR] FB2: No 'saved_path' found in image_map for UUID {img_uuid_from_text}.")
            else:  # img_path exists in map, but file os.path.exists(img_path) is false
                print(
                    f"[ERROR] FB2: Image path from image_map does not exist on disk: {img_path} (for UUID {img_uuid_from_text})")
        elif img_uuid_from_text not in image_map:
            print(f"  UUID {img_uuid_from_text} from placeholder NOT FOUND in image_map.")
        elif img_uuid_from_text in processed_uuids_for_binary:
            print(f"  UUID {img_uuid_from_text} already processed for binary section.")

    print(f"DEBUG write_to_fb2: Total images prepared for binary section: {images_added_to_binary_count}")

    body = etree.SubElement(fb2_root, "body")
    lines = translated_content_with_placeholders.splitlines()
    para_buffer = [];
    current_section = None;
    is_first_section = True

    def add_paragraph_to_fb2(target_element, para_lines):
        nonlocal is_first_section  # Allow modification of outer scope variable
        if not para_lines: return
        full_para_text = "\n".join(para_lines).strip()
        if not full_para_text: return

        parent_section = target_element
        if parent_section is None or parent_section.tag != 'section':
            last_section = body.xpath('section[last()]')
            parent_section = last_section[0] if last_section else None
            if parent_section is None:
                parent_section = etree.SubElement(body, "section")
                is_first_section = False

        p = etree.SubElement(parent_section, "p")
        last_index = 0;
        current_tail_element = None

        placeholders_in_para = find_image_placeholders(full_para_text)
        for placeholder_tag_para, img_uuid_para in placeholders_in_para:  # Renamed variables
            match_start = full_para_text.find(placeholder_tag_para, last_index)
            if match_start == -1: continue

            text_before = full_para_text[last_index:match_start]
            if text_before:
                if current_tail_element is not None:
                    current_tail_element.tail = (current_tail_element.tail or "") + text_before
                else:
                    p.text = (p.text or "") + text_before

            if img_uuid_para in placeholder_to_binary_id:
                binary_id_para = placeholder_to_binary_id[img_uuid_para]  # Renamed
                try:
                    img_elem = etree.SubElement(p, "image")
                    img_elem.set(l_href_attr, f"#{binary_id_para}")
                    current_tail_element = img_elem
                except ValueError as ve:
                    print(f"[ERROR] FB2: Failed to create image element for binary ID '{binary_id_para}': {ve}")
                    error_text_ve = f" [FB2 Img Err: {img_uuid_para[:8]}] "  # Renamed
                    if current_tail_element is not None:
                        current_tail_element.tail = (current_tail_element.tail or "") + error_text_ve
                    else:
                        p.text = (p.text or "") + error_text_ve
                    current_tail_element = None
            else:
                original_filename_fb2 = image_map.get(img_uuid_para, {}).get('original_filename',
                                                                             img_uuid_para)  # Renamed
                error_text_nf = f" [Img Placeholder {img_uuid_para[:8]} found in text, but no binary data prepared (orig: {original_filename_fb2})] "  # Renamed
                print(
                    f"DEBUG write_to_fb2 (add_paragraph): Placeholder {img_uuid_para} found in paragraph, but not in placeholder_to_binary_id map.")
                if current_tail_element is not None:
                    current_tail_element.tail = (current_tail_element.tail or "") + error_text_nf
                else:
                    p.text = (p.text or "") + error_text_nf
                current_tail_element = None
            last_index = match_start + len(placeholder_tag_para)

        text_after = full_para_text[last_index:]
        if text_after:
            if current_tail_element is not None:
                current_tail_element.tail = (current_tail_element.tail or "") + text_after
            else:
                p.text = (p.text or "") + text_after
        if len(p) == 0 and not (p.text or "").strip() and p.getparent() is not None:
            p.getparent().remove(p)

    for line in lines:
        stripped_line = line.strip()
        chapter_match = re.match(r'^(#{1,3})\s+(.*)', stripped_line)
        if chapter_match:
            if para_buffer and current_section is not None:
                add_paragraph_to_fb2(current_section, para_buffer)
            para_buffer = []
            current_section = etree.SubElement(body, "section")
            title_elem = etree.SubElement(current_section, "title")
            add_paragraph_to_fb2(title_elem, [chapter_match.group(2).strip()])
            if not title_elem.xpath('.//text() | .//image'):
                current_section.remove(title_elem)
            is_first_section = False
        else:
            if is_first_section and not current_section and stripped_line:
                current_section = etree.SubElement(body, "section")
                is_first_section = False
            if stripped_line or find_image_placeholders(line):  # Check raw line for placeholders
                para_buffer.append(line)
            elif not stripped_line and para_buffer:
                if current_section is None:
                    current_section = etree.SubElement(body, "section");
                    is_first_section = False
                add_paragraph_to_fb2(current_section, para_buffer);
                para_buffer = []
    if para_buffer:
        if current_section is None: current_section = etree.SubElement(body, "section")
        add_paragraph_to_fb2(current_section, para_buffer)
    if not body.xpath('section'):
        print("[WARN] FB2: No sections created. Adding empty fallback section.")
        etree.SubElement(body, "section")

    if binary_sections:  # This list is populated based on successful processing
        print(
            f"[INFO] FB2: Adding {len(binary_sections)} binary image sections.")  # This should match images_added_to_binary_count
        for binary_id_add, content_type_add, base64_data_add in binary_sections:  # Renamed variables
            try:
                etree.SubElement(fb2_root, "binary", id=binary_id_add,
                                 attrib={"content-type": content_type_add}).text = base64_data_add
            except ValueError as ve_add:
                print(f"[ERROR] FB2: Invalid binary ID '{binary_id_add}' during write: {ve_add}")
    else:
        print("[INFO] FB2: No binary image data to add.")

    try:
        tree = etree.ElementTree(fb2_root)
        tree.write(out_path, pretty_print=True, xml_declaration=True, encoding="utf-8")
        print(f"[SUCCESS] FB2 file saved: {out_path}")
    except Exception as write_err:
        print(f"[ERROR] Failed to write FB2 file {out_path}: {write_err}");
        raise write_err