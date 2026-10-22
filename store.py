"""Local SQLite storage. Source documents and occurrence mappings are immutable."""
import hashlib
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from parser import parse_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
 deleted_at TEXT NULL, revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sources (
 id INTEGER PRIMARY KEY, contact_id INTEGER NOT NULL REFERENCES contacts(id),
 name TEXT NOT NULL, sha256 TEXT NOT NULL, raw BLOB NOT NULL, content TEXT NOT NULL,
 created_at TEXT NOT NULL, unparsed_messages INTEGER NOT NULL DEFAULT 0,
 UNIQUE(contact_id, sha256)
);
CREATE TABLE IF NOT EXISTS messages (
 id INTEGER PRIMARY KEY, contact_id INTEGER NOT NULL REFERENCES contacts(id),
 timestamp TEXT NOT NULL, speaker TEXT NOT NULL, text TEXT NOT NULL, kind TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_contact_time ON messages(contact_id,timestamp,id);
CREATE TABLE IF NOT EXISTS occurrences (
 source_id INTEGER NOT NULL REFERENCES sources(id),
 sequence INTEGER NOT NULL, message_id INTEGER NOT NULL REFERENCES messages(id),
 line_start INTEGER NOT NULL, line_end INTEGER NOT NULL,
 PRIMARY KEY(source_id,sequence)
);
CREATE INDEX IF NOT EXISTS idx_occurrences_message ON occurrences(message_id);
CREATE TABLE IF NOT EXISTS id_sequences (
 entity TEXT PRIMARY KEY, next_id INTEGER NOT NULL
);
"""

SCHEMA_VERSION = 3
ID_TABLES = {"contacts": "contacts", "sources": "sources", "messages": "messages"}


def signature(message):
    return (message["timestamp"], message["speaker"], message["text"], message["kind"])


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as con:
            con.executescript(SCHEMA)
            self._migrate(con)

    def _migrate(self, con):
        columns = {row["name"] for row in con.execute("PRAGMA table_info(contacts)")}
        if "deleted_at" not in columns:
            con.execute("ALTER TABLE contacts ADD COLUMN deleted_at TEXT NULL")
        if "revision" not in columns:
            con.execute("ALTER TABLE contacts ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
        source_columns = {row["name"] for row in con.execute("PRAGMA table_info(sources)")}
        if "unparsed_messages" not in source_columns:
            con.execute("ALTER TABLE sources ADD COLUMN unparsed_messages INTEGER NOT NULL DEFAULT 0")
        for entity, table in ID_TABLES.items():
            next_id = con.execute(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {table}").fetchone()[0]
            con.execute(
                "INSERT OR IGNORE INTO id_sequences(entity,next_id) VALUES(?,?)",
                (entity, next_id),
            )
        con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _allocate_id(self, con, entity):
        if entity not in ID_TABLES:
            raise ValueError("未知 ID 序列。")
        row = con.execute(
            "UPDATE id_sequences SET next_id=next_id+1 WHERE entity=? RETURNING next_id-1",
            (entity,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"缺少 ID 序列：{entity}")
        return row[0]

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            with con:
                yield con
        finally:
            con.close()

    def create_contact(self, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ValueError("联系人名称应为 1–100 个字符。")
        with self.connection() as con:
            contact_id = self._allocate_id(con, "contacts")
            con.execute(
                "INSERT INTO contacts(id,name,created_at) VALUES(?,?,?)",
                (contact_id, name.strip(), datetime.now().isoformat(timespec="seconds")),
            )
        return {"contact_id": contact_id}

    def contact_revision(self, contact_id):
        return int(self.contact(contact_id, include_deleted=True)["revision"])

    def bump_revision(self, con, contact_id):
        row = con.execute(
            "UPDATE contacts SET revision=revision+1 WHERE id=? RETURNING revision",
            (contact_id,),
        ).fetchone()
        if row is None:
            raise ValueError("联系人不存在。")
        return int(row[0])

    def contacts(self, include_deleted=False):
        where = "" if include_deleted else "WHERE c.deleted_at IS NULL"
        with self.connection() as con:
            return [dict(row) for row in con.execute(f"""
                SELECT c.*, COUNT(m.id) AS message_count, MIN(m.timestamp) AS first_at,
                MAX(m.timestamp) AS last_at,
                (SELECT COUNT(*) FROM sources s WHERE s.contact_id=c.id) AS file_count
                FROM contacts c LEFT JOIN messages m ON m.contact_id=c.id
                {where}
                GROUP BY c.id ORDER BY c.created_at DESC,c.id DESC
            """)]

    def contact(self, contact_id, include_deleted=False):
        where = "c.id=?" + ("" if include_deleted else " AND c.deleted_at IS NULL")
        with self.connection() as con:
            row = con.execute(f"""
                SELECT c.*, COUNT(m.id) AS message_count, MIN(m.timestamp) AS first_at,
                MAX(m.timestamp) AS last_at,
                (SELECT COUNT(*) FROM sources s WHERE s.contact_id=c.id) AS file_count
                FROM contacts c LEFT JOIN messages m ON m.contact_id=c.id
                WHERE {where} GROUP BY c.id
            """, (contact_id,)).fetchone()
        if row is None:
            raise ValueError("联系人不存在。")
        return dict(row)

    def trash(self):
        return [contact for contact in self.contacts(include_deleted=True)
                if contact["deleted_at"] is not None]

    def trash_contact(self, contact_id):
        contact = self.contact(contact_id)
        deleted_at = datetime.now().isoformat(timespec="seconds")
        with self.connection() as con:
            con.execute("UPDATE contacts SET deleted_at=? WHERE id=? AND deleted_at IS NULL",
                        (deleted_at, contact_id))
            revision = self.bump_revision(con, contact_id)
        return {"contact_id": contact_id, "name": contact["name"],
                "message_count": contact["message_count"], "file_count": contact["file_count"],
                "deleted_at": deleted_at, "revision": revision}

    def restore_contact(self, contact_id):
        contact = self.contact(contact_id, include_deleted=True)
        if contact["deleted_at"] is None:
            raise ValueError("联系人不在回收站中。")
        with self.connection() as con:
            con.execute("UPDATE contacts SET deleted_at=NULL WHERE id=? AND deleted_at IS NOT NULL",
                        (contact_id,))
            revision = self.bump_revision(con, contact_id)
        return {"contact_id": contact_id, "name": contact["name"],
                "message_count": contact["message_count"], "file_count": contact["file_count"],
                "revision": revision}

    def purge_contact(self, contact_id, exact_name):
        contact = self.contact(contact_id, include_deleted=True)
        if contact["deleted_at"] is None:
            raise ValueError("只能永久删除回收站中的联系人。")
        if not isinstance(exact_name, str) or exact_name != contact["name"]:
            raise ValueError("联系人名称不匹配，未执行永久删除。")
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("DELETE FROM occurrences WHERE source_id IN (SELECT id FROM sources WHERE contact_id=?)",
                        (contact_id,))
            con.execute("DELETE FROM messages WHERE contact_id=?", (contact_id,))
            con.execute("DELETE FROM sources WHERE contact_id=?", (contact_id,))
            con.execute("DELETE FROM contacts WHERE id=? AND deleted_at IS NOT NULL", (contact_id,))
        return {"contact_id": contact_id, "name": contact["name"],
                "message_count": contact["message_count"], "file_count": contact["file_count"]}

    def trash_matches(self, name="", digest=""):
        name = name.strip() if isinstance(name, str) else ""
        digest = digest.strip() if isinstance(digest, str) else ""
        if not name and not digest:
            return []
        matches = []
        with self.connection() as con:
            rows = con.execute("""
                SELECT c.*, COUNT(DISTINCT m.id) AS message_count,
                    COUNT(DISTINCT s.id) AS file_count,
                    MAX(CASE WHEN s.sha256=? THEN 1 ELSE 0 END) AS file_match
                FROM contacts c
                LEFT JOIN messages m ON m.contact_id=c.id
                LEFT JOIN sources s ON s.contact_id=c.id
                WHERE c.deleted_at IS NOT NULL
                GROUP BY c.id
                HAVING c.name=? OR file_match=1
                ORDER BY c.deleted_at DESC,c.id DESC
            """, (digest, name)).fetchall()
        for row in rows:
            reasons = []
            if name and row["name"] == name:
                reasons.append("name")
            if digest and row["file_match"]:
                reasons.append("file")
            matches.append({"id": row["id"], "name": row["name"], "deleted_at": row["deleted_at"],
                            "message_count": row["message_count"], "file_count": row["file_count"],
                            "reasons": reasons})
        return matches

    def import_text(self, filename, text, raw, name="", contact_id=None):
        parsed = parse_text(text)
        incoming = parsed["messages"]
        if not incoming:
            raise ValueError("没有可导入的消息，请检查格式。")
        digest = hashlib.sha256(raw).hexdigest()
        now = datetime.now().isoformat(timespec="seconds")
        name = (name or parsed["contact_name"] or "未命名联系人").strip()
        if not name or len(name) > 100:
            raise ValueError("联系人名称应为 1–100 个字符。")
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            created_contact = contact_id is None
            if contact_id is None:
                contact_id = self._allocate_id(con, "contacts")
                con.execute(
                    "INSERT INTO contacts(id,name,created_at) VALUES(?,?,?)",
                    (contact_id, name, now),
                )
            elif not con.execute("SELECT id FROM contacts WHERE id=? AND deleted_at IS NULL", (contact_id,)).fetchone():
                raise ValueError("联系人不存在。")
            same = con.execute("SELECT id FROM sources WHERE contact_id=? AND sha256=?", (contact_id, digest)).fetchone()
            if same:
                # A user-initiated reimport is a lifecycle boundary even when the
                # stored rows are unchanged. Invalidate any prior QA context while
                # preserving the stable source and message identities.
                self.bump_revision(con, contact_id)
                return {"contact_id": contact_id, "added": 0, "existing": len(incoming), "uncertain": 0,
                        "source_id": same["id"], "warnings": parsed["warnings"], "duplicate_file": True}
            source_ids = [r[0] for r in con.execute("SELECT id FROM sources WHERE contact_id=? ORDER BY id DESC", (contact_id,))]
            fingerprints = [signature(m) for m in incoming]
            counts = Counter(fingerprints)
            mapping, used_ids, prior_signatures = {}, set(), set()
            for source_id in source_ids:
                previous = [dict(r) for r in con.execute("""
                    SELECT m.* FROM occurrences o JOIN messages m ON m.id=o.message_id
                    WHERE o.source_id=? ORDER BY o.sequence
                """, (source_id,))]
                previous_fp = [signature(m) for m in previous]
                prior_signatures.update(previous_fp)
                previous_counts = Counter(previous_fp)
                # Only align continuous sequences with a unique anchor in both files.
                # Popular short messages cannot alone establish identity.
                matcher = SequenceMatcher(None, previous_fp, fingerprints, autojunk=True)
                for block in matcher.get_matching_blocks():
                    if block.size < 3:
                        continue
                    has_anchor = any(counts[fingerprints[j]] == 1 and previous_counts[fingerprints[j]] == 1
                                     for j in range(block.b, block.b + block.size))
                    if not has_anchor:
                        continue
                    for offset in range(block.size):
                        new_index, old_id = block.b + offset, previous[block.a + offset]["id"]
                        if new_index not in mapping and old_id not in used_ids:
                            mapping[new_index] = old_id
                            used_ids.add(old_id)
            source_id = self._allocate_id(con, "sources")
            unparsed_messages = sum(
                1
                for item in parsed["unparsed"]
                if item["text"].lstrip().startswith(("[19", "[20", "19", "20"))
            )
            con.execute(
                """INSERT INTO sources(
                    id,contact_id,name,sha256,raw,content,created_at,unparsed_messages
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    source_id, contact_id, Path(filename).name[:200], digest, raw,
                    text, now, unparsed_messages,
                ),
            )
            added = uncertain = 0
            for index, message in enumerate(incoming):
                message_id = mapping.get(index)
                if message_id is None:
                    message_id = self._allocate_id(con, "messages")
                    con.execute(
                        "INSERT INTO messages(id,contact_id,timestamp,speaker,text,kind) VALUES(?,?,?,?,?,?)",
                        (message_id, contact_id, *signature(message)),
                    )
                    added += 1
                    uncertain += int(fingerprints[index] in prior_signatures)
                con.execute("INSERT INTO occurrences VALUES(?,?,?,?,?)",
                            (source_id, index, message_id, message["line_start"], message["line_end"]))
            if not created_contact:
                self.bump_revision(con, contact_id)
            return {"contact_id": contact_id, "source_id": source_id, "added": added,
                    "existing": len(mapping), "uncertain": uncertain, "warnings": parsed["warnings"],
                    "duplicate_file": False}

    def messages(self, contact_id, date_from="", date_to="", speaker=""):
        self.contact(contact_id)
        params = [contact_id]
        where = "m.contact_id=?"
        if date_from:
            where += " AND m.timestamp>=?"
            params.append(date_from + " 00:00:00")
        if date_to:
            where += " AND m.timestamp<=?"
            params.append(date_to + " 23:59:59")
        if speaker:
            where += " AND m.speaker=?"
            params.append(speaker)
        with self.connection() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT m.* FROM messages m WHERE " + where + " ORDER BY m.timestamp,m.id", params)]
            origins = {r["message_id"]: dict(r) for r in con.execute("""
                SELECT o.*,s.name AS source_name FROM occurrences o JOIN sources s ON s.id=o.source_id
                WHERE s.contact_id=? ORDER BY o.source_id DESC
            """, (contact_id,))}
            for row in rows:
                origin = origins[row["id"]]
                row.update({k: origin[k] for k in ("source_id", "source_name", "sequence", "line_start", "line_end")})
            return rows

    def message_scope(self, contact_id, filters):
        filters = filters or {}
        if not isinstance(filters, dict):
            raise ValueError("筛选条件格式无效。")
        date_from = filters.get("date_from", "")
        date_to = filters.get("date_to", "")
        speaker = filters.get("speaker", "")
        if any(not isinstance(value, str) for value in (date_from, date_to, speaker)):
            raise ValueError("筛选条件格式无效。")
        all_messages = self.messages(contact_id)
        filtered = self.messages(
            contact_id,
            date_from=date_from,
            date_to=date_to,
            speaker=speaker,
        )
        with self.connection() as con:
            untimestamped = con.execute(
                "SELECT COALESCE(SUM(unparsed_messages),0) FROM sources WHERE contact_id=?",
                (contact_id,),
            ).fetchone()[0]
        return {
            "contact_id": contact_id,
            "date_from": date_from,
            "date_to": date_to,
            "speaker": speaker,
            "total_effective_messages": len(all_messages),
            "filtered_messages": len(filtered),
            "timestamped_messages": len(all_messages),
            "untimestamped_messages": int(untimestamped),
            "messages": filtered,
        }

    def context(self, message_id):
        with self.connection() as con:
            focus = con.execute("""SELECT m.* FROM messages m
                JOIN contacts c ON c.id=m.contact_id
                WHERE m.id=? AND c.deleted_at IS NULL""", (message_id,)).fetchone()
            if focus is None:
                raise ValueError("消息不存在。")
            occurrence = con.execute(
                "SELECT * FROM occurrences WHERE message_id=? ORDER BY source_id LIMIT 1", (message_id,)).fetchone()
            nearby = [dict(r) for r in con.execute("""
                SELECT m.*,o.line_start,o.line_end,o.sequence FROM occurrences o
                JOIN messages m ON m.id=o.message_id WHERE o.source_id=? AND o.sequence BETWEEN ? AND ?
                ORDER BY o.sequence
            """, (occurrence["source_id"], max(0, occurrence["sequence"] - 3), occurrence["sequence"] + 3))]
            source = con.execute("SELECT id,name,content FROM sources WHERE id=?", (occurrence["source_id"],)).fetchone()
            lines = source["content"].splitlines()
            start = max(0, occurrence["line_start"] - 4)
            end = min(len(lines), occurrence["line_end"] + 3)
            return {"focus": dict(focus), "messages": nearby, "source_id": source["id"],
                    "source_name": source["name"], "line_start": occurrence["line_start"],
                    "line_end": occurrence["line_end"],
                    "raw_lines": [{"number": i + 1, "text": lines[i]} for i in range(start, end)]}

    def source(self, source_id):
        with self.connection() as con:
            row = con.execute("""SELECT s.* FROM sources s
                JOIN contacts c ON c.id=s.contact_id
                WHERE s.id=? AND c.deleted_at IS NULL""", (source_id,)).fetchone()
            if not row:
                raise ValueError("源文件不存在。")
            return dict(row)
