"""Run in a tmux pane: how far tmux moves the cursor for each text.

``python -I -S width_probe.py <texts.json> <result.json>``. Each text is
drawn at the start of the line and followed by a cursor position request,
which tmux itself answers; requests go out in batches.
"""

import json
import os
import re
import sys
import tty

_BATCH = 512
_REPLY = re.compile(rb"\x1b\[\d+;(\d+)R")


def main(texts_path: str, result_path: str) -> None:
    with open(texts_path) as source:
        texts = json.load(source)
    tty.setraw(0)
    widths: list[int] = []
    pending = b""
    for start in range(0, len(texts), _BATCH):
        batch = texts[start : start + _BATCH]
        os.write(1, b"".join(b"\r" + text.encode() + b"\x1b[6n" for text in batch))
        replies: list[re.Match[bytes]] = []
        while len(replies) < len(batch):
            pending += os.read(0, 65536)
            replies = list(_REPLY.finditer(pending))
        widths.extend(int(match.group(1)) - 1 for match in replies[: len(batch)])
        pending = pending[replies[len(batch) - 1].end() :]
    with open(result_path + ".tmp", "w") as result:
        json.dump(widths, result)
    os.replace(result_path + ".tmp", result_path)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
