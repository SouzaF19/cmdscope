from __future__ import annotations

import socket
import time
from pathlib import Path

root = Path.cwd()
listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.bind(("127.0.0.1", 0))
listener.listen(1)
(root / "port.txt").write_text(str(listener.getsockname()[1]), encoding="utf-8")
time.sleep(3)
listener.close()
