"""Seed conversations straight into the backend's SQLite file (FTS triggers index the messages).
usage: seed_chats.py <data_dir> <n_chats> <msgs_per_chat> [needle_index] [needle_word]"""
import json, sqlite3, sys, time, uuid, glob, os

d, n, per = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
needle_i = int(sys.argv[4]) if len(sys.argv) > 4 else -1
needle = sys.argv[5] if len(sys.argv) > 5 else ""
path = next(p for p in glob.glob(os.path.join(d, "*.db")) + glob.glob(os.path.join(d, "*.sqlite*")) if not p.endswith(("-wal", "-shm")))
db = sqlite3.connect(path, timeout=30)
db.execute("PRAGMA busy_timeout=30000")
now = time.time()
ids = []
for i in range(n):
    cid = uuid.uuid4().hex[:16]
    ids.append(cid)
    t = now - (n - i) * 60
    db.execute("INSERT INTO conversations(id, project_id, title, model, settings, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
               (cid, None, f"Seed chat {i:03d}", "mock-chat", "{}", t, t))
    for j in range(per):
        role = "user" if j % 2 == 0 else "assistant"
        body = f"{'question' if role == 'user' else 'answer'} {j} of chat {i}"
        if i == needle_i and j == 0:
            body += f" {needle}"
        db.execute("INSERT INTO messages(id, conversation_id, role, content, model, created_at) VALUES (?,?,?,?,?,?)",
                   (uuid.uuid4().hex[:16], cid, role, body, "mock-chat" if role == "assistant" else None, t + j))
db.commit()
print(json.dumps(ids))
