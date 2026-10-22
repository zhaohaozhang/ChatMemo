"""Parse exported chat text without executing or interpreting message contents."""
import re
from datetime import datetime

MAX_BYTES = 5 * 1024 * 1024
MAX_MESSAGES = 30000
HEADER = re.compile(
    r"^\[?(?P<time>\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?)\]?"
    r"\s+(?:\[(?P<kind>[^\]]{1,20})\]\s+)?(?P<speaker>[^:\n：]{1,100})[:：]\s?(?P<text>.*)$"
)


def decode_text(data):
    if len(data) > MAX_BYTES:
        raise ValueError("单个文件最多 5 MB，请分段导出后分别导入。")
    if not data or b"\x00" in data[:1000] and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ValueError("请选择非空的 TXT 文本文件。")
    encodings = ["utf-16"] if data.startswith((b"\xff\xfe", b"\xfe\xff")) else ["utf-8-sig", "gb18030"]
    for encoding in encodings:
        try:
            text = data.decode(encoding)
            if "\x00" in text:
                raise ValueError("文件包含二进制内容，请导出为 TXT。")
            return text, encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别文本编码，请另存为 UTF-8 TXT 后重试。")


def parse_text(text):
    messages, warnings, unparsed = [], [], []
    contact = ""
    current = None
    for line_number, line in enumerate(text.splitlines(), 1):
        if not messages and line.startswith(("联系人:", "联系人：")):
            contact = re.split("[:：]", line, maxsplit=1)[1].strip()
        match = HEADER.match(line)
        if match:
            value = match.groupdict()
            try:
                timestamp = datetime.fromisoformat(value["time"].replace("/", "-").replace("T", " "))
            except ValueError:
                warnings.append(f"第 {line_number} 行时间无效，保留在原文件中，未作为独立消息导入。")
                unparsed.append({"line": line_number, "text": line})
                current = None
                continue
            current = {
                "timestamp": timestamp.isoformat(sep=" ", timespec="seconds"),
                "speaker": value["speaker"].strip(), "text": value["text"],
                "kind": value["kind"] or "文本",
                "line_start": line_number, "line_end": line_number,
            }
            messages.append(current)
            if len(messages) > MAX_MESSAGES:
                raise ValueError("单个文件最多 30,000 条消息，请分段导入。")
        elif current is not None:
            current["text"] += "\n" + line
            current["line_end"] = line_number
        elif line.strip():
            unparsed.append({"line": line_number, "text": line})
    if not messages:
        warnings.append("尚未识别到消息。支持：[2026-09-01 18:00:00] 小林: 正文。请检查预览或调整导出格式。")
    return {
        "messages": messages, "warnings": warnings, "unparsed": unparsed,
        "contact_name": contact, "line_count": len(text.splitlines()),
        "speakers": sorted({m["speaker"] for m in messages}),
    }
