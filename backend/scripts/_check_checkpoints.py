import sqlite3, json, sys
sys.path.insert(0, '.')

conn = sqlite3.connect('.runtime/checkpoints.db')

# List tables
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tables:", [t[0] for t in tables])

# Sample checkpoint data - look for compress/update_memory nodes
rows = conn.execute("SELECT thread_id, type, channel FROM writes LIMIT 5").fetchall()
print("\nSample writes cols:", rows[:2] if rows else "empty")

# Check if compress events appear anywhere
try:
    rows = conn.execute(
        "SELECT thread_id, channel, value FROM writes WHERE value LIKE '%compress%' LIMIT 5"
    ).fetchall()
    print(f"\nCompress events in writes: {len(rows)}")
    for r in rows[:2]:
        print(" ", r[0][:8], r[1], str(r[2])[:100])
except Exception as e:
    print("writes query error:", e)

try:
    rows = conn.execute(
        "SELECT thread_id, channel, blob FROM checkpoints WHERE channel LIKE '%compress%' LIMIT 5"
    ).fetchall()
    print(f"\nCompress in checkpoints: {len(rows)}")
except Exception as e:
    print("checkpoints query error:", e)

# Check actual schema
for t in [r[0] for r in tables]:
    cols = conn.execute(f"PRAGMA table_info({t})").fetchall()
    print(f"\n{t} columns:", [c[1] for c in cols])

conn.close()
