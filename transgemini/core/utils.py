import math
import re
from pathlib import Path
from PIL import Image
from io import BytesIO
import imghdr

from transgemini.config import IMAGE_PLACEHOLDER_PREFIX, TRANSLATED_SUFFIX, PILLOW_AVAILABLE


def create_image_placeholder(img_uuid):
    return f"<||{IMAGE_PLACEHOLDER_PREFIX}{img_uuid}||>"

def find_image_placeholders(text):
    pattern = re.compile(r"<\|\|(" + IMAGE_PLACEHOLDER_PREFIX + r"([a-f0-9]{32}))\|\|>")
    return [(match.group(0), match.group(2)) for match in pattern.finditer(text)]


def add_translated_suffix(filename):
    """Adds _translated before the file extension, handling multiple suffixes."""
    if not filename: return filename
    path = Path(filename)

    suffixes = "".join(path.suffixes)
    if not suffixes:  # Handle case with no extension (e.g., just "myfile")
        stem = path.name

        return str(path.parent / f"{stem}{TRANSLATED_SUFFIX}")
    else:

        stem = path.name.replace(suffixes, "")

        return str(path.parent / f"{stem}{TRANSLATED_SUFFIX}{suffixes}")


def format_size(size_bytes):
    """Converts bytes to a human-readable format (KB, MB, GB)."""
    if size_bytes == 0: return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024))) if size_bytes > 0 else 0
    i = min(i, len(size_name) - 1)
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

def split_text_into_chunks(text, limit_chars, search_window, min_chunk_size):
    """Splits text into chunks, respecting paragraphs and sentences where possible."""
    chunks = []
    start_index = 0
    text_len = len(text)
    target_size = max(min_chunk_size, limit_chars - search_window // 2)

    while start_index < text_len:
        if text_len - start_index <= limit_chars:
            chunks.append(text[start_index:])
            break

        ideal_end_index = min(start_index + target_size, text_len)
        search_start = max(start_index + min_chunk_size, ideal_end_index - search_window)
        search_end = min(ideal_end_index + search_window, text_len)
        split_index = -1

        potential_splits = []

        search_slice = text[search_start:search_end]
        if search_slice:
            for match in re.finditer(r'\n\n', search_slice):
                potential_splits.append(
                    (abs((search_start + match.end()) - ideal_end_index), search_start + match.end(), 1))
            for match in re.finditer(r"[.!?]\s+", search_slice):
                potential_splits.append(
                    (abs((search_start + match.end()) - ideal_end_index), search_start + match.end(), 2))
            for match in re.finditer(r'\n', search_slice):
                potential_splits.append(
                    (abs((search_start + match.end()) - ideal_end_index), search_start + match.end(), 3))

            for match in re.finditer(r' ', search_slice):
                current_split_pos = search_start + match.end()
                preceding_text = text[max(0, current_split_pos - 50):current_split_pos]
                following_text = text[current_split_pos:min(text_len, current_split_pos + 5)]
                if f"<||{IMAGE_PLACEHOLDER_PREFIX}" in preceding_text and "||>" not in following_text:
                    continue  # Likely inside a placeholder, don't split here
                potential_splits.append((abs(current_split_pos - ideal_end_index), current_split_pos, 4))

        potential_splits.sort()

        if potential_splits:
            split_index = potential_splits[0][1]

            if split_index <= start_index + min_chunk_size:
                split_index = -1  # Ignore this split point

        if split_index == -1:
            if ideal_end_index > start_index + min_chunk_size:
                split_index = ideal_end_index
            else:  # Force split at limit or end of text
                split_index = min(start_index + limit_chars, text_len)

        split_index = min(split_index, text_len)
        if split_index <= start_index:

            split_index = min(start_index + limit_chars, text_len)
            if split_index <= start_index:  # Final fallback if limit is tiny or zero
                split_index = text_len

        chunks.append(text[start_index:split_index])
        start_index = split_index

    return [chunk for chunk in chunks if chunk.strip()]

def get_image_extension_from_data(image_data, fallback_ext="jpeg"):
    """Determines image extension from binary data."""
    if not image_data: return fallback_ext
    ext = imghdr.what(None, image_data)
    if ext == 'jpeg': return 'jpg'
    if ext is None and PILLOW_AVAILABLE:
        try:
            with Image.open(BytesIO(image_data)) as img:
                img_format = img.format
                if img_format:
                    fmt_lower = img_format.lower()
                    if fmt_lower == 'jpeg': return 'jpg'
                    if fmt_lower in ['png', 'gif', 'bmp', 'tiff', 'webp']: return fmt_lower
        except Exception:
            pass  # Ignore Pillow errors if imghdr failed
    return ext if ext else fallback_ext

def convert_emf_to_png(emf_data):
    """Converts EMF image data to PNG using Pillow."""
    if not PILLOW_AVAILABLE:
        print("[WARN] Pillow library not found, cannot convert EMF image. Skipping.")
        return None
    try:

        with Image.open(BytesIO(emf_data)) as img:

            if img.mode == 'CMYK':
                img = img.convert('RGB')
            elif img.mode == 'P':
                img = img.convert('RGBA')  # Convert palette to RGBA for transparency
            elif img.mode == '1':
                img = img.convert('L')  # Convert bilevel to grayscale

            png_bytes_io = BytesIO()
            img.save(png_bytes_io, format='PNG')
            return png_bytes_io.getvalue()
    except ImportError:  # Might happen if EMF plugin for Pillow is missing
        print("[ERROR] Failed to convert EMF: Pillow EMF support might be missing or incomplete on this system.")
        return None
    except Exception as e:
        print(f"[ERROR] Failed to convert EMF to PNG: {e}")
        return None




