# chunker.py
from __future__ import annotations


def chunk_markdown(text: str, max_length: int = 1900) -> list[str]:
    if not text:
        return [""]
    if len(text) <= max_length:
        return [text]

    lines = text.splitlines(keepends=True)
    chunks: list[str] = []
    current_lines: list[str] = []
    current_len = 0

    in_code_block = False
    active_fence_header = "```"

    def flush_current(close_code_fence: bool) -> None:
        nonlocal current_lines, current_len
        if not current_lines:
            return
        body = "".join(current_lines)
        if close_code_fence:
            if not body.endswith("\n"):
                body += "\n"
            body += "```"
        chunks.append(body.strip("\n"))
        current_lines = []
        current_len = 0

    for line in lines:
        stripped = line.lstrip()
        is_fence_line = stripped.startswith("```")

        while len(line) > max_length - 20:
            if current_lines:
                flush_current(close_code_fence=in_code_block)
                if in_code_block:
                    header_line = f"{active_fence_header}\n"
                    current_lines.append(header_line)
                    current_len += len(header_line)

            slice_budget = max_length - current_len - (10 if in_code_block else 0)
            segment = line[:slice_budget]
            line = line[slice_budget:]
            current_lines.append(segment)
            current_len += len(segment)
            flush_current(close_code_fence=in_code_block)
            if in_code_block:
                header_line = f"{active_fence_header}\n"
                current_lines.append(header_line)
                current_len += len(header_line)

        reserve_for_closing = 6 if in_code_block else 0
        if current_len + len(line) + reserve_for_closing > max_length:
            flush_current(close_code_fence=in_code_block)
            if in_code_block:
                header_line = f"{active_fence_header}\n"
                current_lines.append(header_line)
                current_len += len(header_line)

        current_lines.append(line)
        current_len += len(line)

        if is_fence_line:
            if not in_code_block:
                in_code_block = True
                active_fence_header = stripped.rstrip("\r\n")
            else:
                in_code_block = False
                active_fence_header = "```"

    if current_lines:
        flush_current(close_code_fence=in_code_block)

    return chunks or [text[:max_length]]