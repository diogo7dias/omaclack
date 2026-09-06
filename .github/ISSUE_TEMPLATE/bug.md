---
name: Bug
about: Something does not sound, open or toggle
title: ""
labels: bug
---

**What happens**

**Expected**

**Environment**
- Omarchy version (`omarchy version`):
- Output of `printf '{"cmd":"status"}\n' | socat - UNIX-CONNECT:$XDG_RUNTIME_DIR/omaclack/ctl.sock`:
- Shell log: `journalctl --user _COMM=quickshell --since "10 min ago" | grep -i omaclack`
