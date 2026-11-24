"""
Adds `address` and `event_link` to the events code in your main.py.
Version 3: skips changes you already made by hand, finishes the rest.

USAGE:
    python apply_events_patch_v3.py app/routes/main.py

- Makes a backup first: main.py.bak
- If a spot cannot be found, your file is NOT changed and it tells you what it saw
- Safe to run more than once
"""
import re
import sys

if len(sys.argv) != 2:
    sys.exit("Usage: python apply_events_patch_v3.py path/to/main.py")

path = sys.argv[1]
raw = open(path, "r", encoding="utf-8", newline="").read()
crlf = "\r\n" in raw
src = raw.replace("\r\n", "\n")


def func_span(s, name):
    m = re.search(rf"^def {name}\(", s, re.M)
    if not m:
        return None
    nxt = re.search(r"^(@bp\.route|def )", s[m.end():], re.M)
    end = m.end() + (nxt.start() if nxt else len(s) - m.end())
    return m.start(), end


def patch_select(s, fname, need):
    """Add missing columns to the SELECT ... FROM tourism_events inside one function."""
    sp = func_span(s, fname)
    if not sp:
        return s, f"function {fname}() not found"
    a, b = sp
    seg = s[a:b]
    m = re.search(r"(SELECT\s+)(.*?)(\s+FROM\s+tourism_events)", seg, re.S | re.I)
    if not m:
        return s, f"SELECT ... FROM tourism_events not found inside {fname}()"
    cols = m.group(2)
    missing = [c for c in need if not re.search(rf"\b{c}\b", cols)]
    if not missing:
        return s, "already done"
    new = m.group(1) + cols.rstrip() + ", " + ", ".join(missing) + m.group(3)
    seg = seg[:m.start()] + new + seg[m.end():]
    return s[:a] + seg + s[b:], "changed"


def patch_regex(s, label, pattern, repl, done_marker):
    if done_marker in s:
        return s, "already done"
    if len(re.findall(pattern, s)) != 1:
        return s, "spot not found"
    return re.sub(pattern, repl, s), "changed"


def read_block(m):
    ind = m.group(2)
    return (
        f'\n{ind}address = (data.get("address") or "").strip()[:300] or None'
        f'\n{ind}event_link = (data.get("event_link") or "").strip()[:500] or None'
        f'\n{ind}if event_link and not re.match(r"^https?://", event_link, re.I):'
        f'\n{ind}    return jsonify({{"success": False, "error": "Invalid event link"}}), 400'
        f'\n\n{ind}{m.group(3)}'
    )


def params_block(m):
    ind = m.group(2)
    return (f'\n{ind}"address": address,\n{ind}"event_link": event_link,'
            f'\n{ind}"description": (data.get("description") or "").strip() or None,')


steps = []  # (label, status)


def run(label, result):
    global src
    src, status = result
    steps.append((label, status))


run("calendar list (month) SELECT", patch_select(src, "list_events", ["organizer_contact", "address", "event_link"]))
run("upcoming list SELECT", patch_select(src, "upcoming_events", ["organizer_contact", "address", "event_link"]))
run("admin list SELECT", patch_select(src, "admin_list_events", ["address", "event_link"]))

run("contribute: read address + link", patch_regex(
    src, "read",
    r'(\n)([ \t]*)(result\s*=\s*db\.session\.execute\(\s*text\(\s*"""\s*INSERT\s+INTO\s+tourism_events)',
    read_block, 'data.get("event_link")'))
run("contribute: INSERT columns", patch_regex(
    src, "cols",
    r"(organizer_contact,\s*organizer_email,)(\s*)(description,\s*photo_url\))",
    lambda m: f"{m.group(1)} address, event_link,{m.group(2)}{m.group(3)}",
    "organizer_email, address, event_link"))
run("contribute: INSERT values", patch_regex(
    src, "vals",
    r"(:organizer_contact,\s*:organizer_email,)(\s*)(:description,\s*:photo_url\))",
    lambda m: f"{m.group(1)} :address, :event_link,{m.group(2)}{m.group(3)}",
    ":organizer_email, :address, :event_link"))
run("contribute: parameters", patch_regex(
    src, "params",
    r'(\n([ \t]*))"description":\s*\(data\.get\("description"\)\s*or\s*""\)\.strip\(\)\s*or\s*None,',
    params_block, '"address": address,'))

bad = [(l, s) for l, s in steps if s not in ("changed", "already done")]
print("Result:")
for label, status in steps:
    print(f"  {'OK ' if (label, status) not in bad else 'FAIL'}  {label}: {status}")

if bad:
    print("\nNothing was changed in your file.")
    print("\nLines in your file that mention tourism_events:")
    n = 0
    for i, line in enumerate(raw.replace("\r\n", "\n").split("\n"), 1):
        if "tourism_events" in line:
            print(f"  line {i}: {line.strip()[:110]}")
            n += 1
            if n >= 25:
                break
    if n == 0:
        print("  (none!) Wrong main.py? Check the path.")
    print("\nPaste this whole output here and I will fix the script.")
    sys.exit(1)

if all(s == "already done" for _, s in steps):
    sys.exit("\nEverything was already patched. Nothing to do.")

with open(path + ".bak", "w", encoding="utf-8", newline="") as f:
    f.write(raw)
with open(path, "w", encoding="utf-8", newline="") as f:
    f.write(src.replace("\n", "\r\n") if crlf else src)

print(f"\nDone. Backup saved as {path}.bak")
print("Next: run the two ALTER TABLE lines from events_migration.sql, then restart the backend.")