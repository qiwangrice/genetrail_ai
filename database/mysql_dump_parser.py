from __future__ import annotations

from typing import Iterator, List, Optional


def _unescape_mysql_string(value: str) -> str:
    return (
        value.replace("\\'", "'")
        .replace('\\"', '"')
        .replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
        .replace("\\\\", "\\")
    )


def parse_mysql_value(raw: str) -> Optional[str | int | float]:
    raw = raw.strip()
    if raw == "NULL":
        return None
    if raw.startswith("'") and raw.endswith("'"):
        return _unescape_mysql_string(raw[1:-1].replace("''", "'"))
    if raw.startswith('"') and raw.endswith('"'):
        return _unescape_mysql_string(raw[1:-1])
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def iter_insert_rows(line: str, table: str) -> Iterator[List]:
    prefix = f"INSERT INTO `{table}` VALUES "
    if not line.startswith(prefix):
        return
    data = line[len(prefix) :].rstrip().rstrip(";")

    idx = 0
    length = len(data)
    while idx < length:
        while idx < length and data[idx] != "(":
            idx += 1
        if idx >= length:
            break
        idx += 1

        fields: List[str] = []
        current: List[str] = []
        in_string = False
        quote = ""

        while idx < length:
            char = data[idx]
            if in_string:
                if char == "\\" and idx + 1 < length:
                    current.append(char)
                    idx += 1
                    current.append(data[idx])
                elif char == quote:
                    next_char = data[idx + 1] if idx + 1 < length else ""
                    if next_char == quote:
                        current.append(char)
                        current.append(next_char)
                        idx += 2
                        continue
                    in_string = False
                else:
                    current.append(char)
                idx += 1
                continue

            if char in ("'", '"'):
                in_string = True
                quote = char
                idx += 1
                continue
            if char == ",":
                fields.append("".join(current))
                current = []
                idx += 1
                continue
            if char == ")":
                fields.append("".join(current))
                idx += 1
                break
            current.append(char)
            idx += 1

        yield [parse_mysql_value(field) for field in fields]

        while idx < length and data[idx] in ", \n\r\t":
            idx += 1
