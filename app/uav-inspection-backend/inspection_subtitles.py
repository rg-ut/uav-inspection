"""Read a DJI flight subtitle without leaking temporary files."""
import re
from pathlib import Path

from fastapi import HTTPException

MAX_BYTES = 64 * 1024 * 1024
TIMING = re.compile(r'^\d{2,}:\d{2}:\d{2}[,.]\d{3}\s+-->\s+\d{2,}:\d{2}:\d{2}[,.]\d{3}', re.MULTILINE)


def srt_bytes(upload):
    if Path(upload.filename or '').suffix.lower() != '.srt':
        raise HTTPException(422, '定位字幕请使用 SRT 文件。')
    data = upload.file.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(422, 'SRT 字幕为空或超过 64 MB。')
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise HTTPException(422, '无法读取 SRT 字幕，请转换为 UTF-8。')
    if not TIMING.search(text):
        raise HTTPException(422, 'SRT 字幕没有有效的时间轴。')
    return text.encode('utf-8')
