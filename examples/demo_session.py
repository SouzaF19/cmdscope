"""Small end-to-end demo used for manual validation."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

root = Path.cwd()
(root / "created.txt").write_text("created\n", encoding="utf-8")
(root / "modified.txt").write_text("before\n", encoding="utf-8")
time.sleep(0.1)
with (root / "modified.txt").open("a", encoding="utf-8") as handle:
    handle.write("after\n")
(root / "deleted.txt").unlink(missing_ok=True)
(root / "new-dir").mkdir(exist_ok=True)
(root / "new-dir" / "nested.txt").write_text("nested\n", encoding="utf-8")

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.6)"])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    time.sleep(0.7)
child.wait(timeout=5)
print("demo complete")
