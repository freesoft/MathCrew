import sqlite3
import os
from datetime import datetime
from math import sqrt
import bcrypt

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "math_tutor.db")

LEVEL_TITLES = {
    1: "Beginner", 2: "Learner", 3: "Explorer", 4: "Thinker",
    5: "Star", 6: "Problem Pro", 7: "Math Hero", 8: "Number Ninja",
    9: "Super Brain", 10: "Genius", 11: "Mastermind", 12: "Legend",
}

ACHIEVEMENTS = {
    "first_correct":     {"name": "First Star",       "icon": "⭐",  "desc": "Get your first answer right"},
    "ten_correct":       {"name": "Perfect Ten",      "icon": "🎯",  "desc": "Get 10 answers correct"},
    "fifty_correct":     {"name": "Half Century",     "icon": "🏆",  "desc": "Get 50 answers correct"},
    "hundred_correct":   {"name": "Century Club",     "icon": "👑",  "desc": "Get 100 answers correct"},
    "streak_3":          {"name": "Hat Trick",        "icon": "🔥",  "desc": "3 correct in a row"},
    "streak_5":          {"name": "High Five",        "icon": "✋",  "desc": "5 correct in a row"},
    "streak_10":         {"name": "On Fire",          "icon": "💥",  "desc": "10 correct in a row"},
    "try_3_topics":      {"name": "Explorer",         "icon": "🌍",  "desc": "Try 3 different topics"},
    "try_5_topics":      {"name": "Adventurer",       "icon": "🧭",  "desc": "Try 5 different topics"},
    "ten_problems":      {"name": "Getting Started",  "icon": "🚀",  "desc": "Attempt 10 problems"},
    "fifty_problems":    {"name": "Determined",       "icon": "💪",  "desc": "Attempt 50 problems"},
    "scaffold_win":      {"name": "Comeback Kid",     "icon": "💫",  "desc": "Get a practice problem right"},
    "scaffold_master":   {"name": "Never Give Up",    "icon": "🧗",  "desc": "Complete 5 practice rounds"},
    "perfect_session_5": {"name": "Flawless",         "icon": "💎",  "desc": "Get 5 in a row without any wrong"},
    "level_5":           {"name": "Rising Star",      "icon": "⭐",  "desc": "Reach Level 5"},
}

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS student (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            pin TEXT NOT NULL DEFAULT '0000',
            grade INTEGER NOT NULL DEFAULT 4,
            curriculum_style TEXT NOT NULL DEFAULT 'common_core',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            correct_answer TEXT,
            student_answer TEXT,
            is_correct INTEGER NOT NULL DEFAULT 0,
            topic TEXT,
            requested_topic TEXT,
            feedback TEXT,
            weakness TEXT,
            misconception_type TEXT,
            misconception_detail TEXT,
            scaffold_level INTEGER NOT NULL DEFAULT 0,
            scaffold_parent_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (student_id) REFERENCES student(id)
        );
        CREATE TABLE IF NOT EXISTS achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            achievement_key TEXT NOT NULL,
            unlocked_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (student_id) REFERENCES student(id),
            UNIQUE(student_id, achievement_key)
        );
        CREATE TABLE IF NOT EXISTS problem_bank (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grade INTEGER NOT NULL,
            curriculum_style TEXT NOT NULL DEFAULT 'common_core',
            topic TEXT NOT NULL,
            question TEXT NOT NULL,
            correct_answer TEXT NOT NULL,
            hint TEXT NOT NULL,
            is_scaffold INTEGER NOT NULL DEFAULT 0,
            scaffold_misconception_type TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            times_served INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_bank_lookup
            ON problem_bank (grade, curriculum_style, topic, is_scaffold);
    """)
    # Migration: add columns for existing DBs
    for col, coldef in [
        ("misconception_type", "TEXT"),
        ("misconception_detail", "TEXT"),
        ("scaffold_level", "INTEGER NOT NULL DEFAULT 0"),
        ("scaffold_parent_id", "INTEGER"),
    ]:
        try:
            conn.execute(f"ALTER TABLE history ADD COLUMN {col} {coldef}")
        except sqlite3.OperationalError:
            pass  # column already exists
    # Migration: add curriculum_style to student table
    try:
        conn.execute("ALTER TABLE student ADD COLUMN curriculum_style TEXT NOT NULL DEFAULT 'common_core'")
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.commit()
    conn.close()

# --- Student Management ---

def get_all_students():
    conn = get_conn()
    rows = conn.execute("SELECT id, name, grade, curriculum_style, created_at FROM student ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_student(student_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM student WHERE id = ?", (student_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def _hash_pin(pin):
    return bcrypt.hashpw(pin.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def _verify_pin_hash(pin, hashed):
    return bcrypt.checkpw(pin.encode("utf-8"), hashed.encode("utf-8"))

def verify_pin(student_id, pin):
    conn = get_conn()
    row = conn.execute("SELECT pin FROM student WHERE id = ?", (student_id,)).fetchone()
    conn.close()
    if not row:
        return False
    stored = row["pin"]
    # Support both bcrypt hashes and legacy plaintext PINs
    if stored.startswith("$2"):
        return _verify_pin_hash(pin, stored)
    # Legacy plaintext: verify and upgrade to bcrypt
    if stored == pin:
        _migrate_pin(student_id, pin)
        return True
    return False

def _migrate_pin(student_id, pin):
    """Upgrade a legacy plaintext PIN to bcrypt hash."""
    conn = get_conn()
    conn.execute("UPDATE student SET pin = ?, updated_at = datetime('now') WHERE id = ?",
                 (_hash_pin(pin), student_id))
    conn.commit()
    conn.close()

def create_student(name, pin, grade, curriculum_style="common_core"):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO student (name, pin, grade, curriculum_style) VALUES (?, ?, ?, ?)",
        (name, _hash_pin(pin), grade, curriculum_style)
    )
    student_id = cur.lastrowid
    conn.commit()
    conn.close()
    return student_id

def update_student(student_id, name=None, grade=None, pin=None, curriculum_style=None):
    conn = get_conn()
    fields = []
    values = []
    if name is not None:
        fields.append("name = ?")
        values.append(name)
    if grade is not None:
        fields.append("grade = ?")
        values.append(grade)
    if pin is not None:
        fields.append("pin = ?")
        values.append(_hash_pin(pin))
    if curriculum_style is not None:
        fields.append("curriculum_style = ?")
        values.append(curriculum_style)
    if fields:
        fields.append("updated_at = datetime('now')")
        values.append(student_id)
        conn.execute(f"UPDATE student SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
    conn.close()

# --- History ---

def save_result(student_id, question, correct_answer, student_answer, is_correct,
                topic=None, requested_topic=None, feedback=None, weakness=None,
                misconception_type=None, misconception_detail=None,
                scaffold_level=0, scaffold_parent_id=None):
    conn = get_conn()
    conn.execute(
        """INSERT INTO history
           (student_id, question, correct_answer, student_answer, is_correct,
            topic, requested_topic, feedback, weakness,
            misconception_type, misconception_detail, scaffold_level, scaffold_parent_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (student_id, question, str(correct_answer), str(student_answer),
         1 if is_correct else 0, topic, requested_topic, feedback, weakness,
         misconception_type, misconception_detail, scaffold_level, scaffold_parent_id)
    )
    conn.commit()
    conn.close()

def update_feedback(history_id, feedback):
    conn = get_conn()
    conn.execute("UPDATE history SET feedback = ? WHERE id = ?", (feedback, history_id))
    conn.commit()
    conn.close()

def get_last_history_id(student_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM history WHERE student_id = ? ORDER BY id DESC LIMIT 1",
        (student_id,)
    ).fetchone()
    conn.close()
    return row["id"] if row else None

def get_stats(student_id):
    conn = get_conn()
    total = conn.execute(
        "SELECT COUNT(*) as c FROM history WHERE student_id = ?", (student_id,)
    ).fetchone()["c"]
    correct = conn.execute(
        "SELECT COUNT(*) as c FROM history WHERE student_id = ? AND is_correct = 1", (student_id,)
    ).fetchone()["c"]

    # Topic breakdown
    topic_rows = conn.execute(
        """SELECT topic,
                  COUNT(*) as total,
                  SUM(is_correct) as correct
           FROM history
           WHERE student_id = ? AND topic IS NOT NULL
           GROUP BY topic""",
        (student_id,)
    ).fetchall()

    topics = {}
    for r in topic_rows:
        topics[r["topic"]] = {
            "total": r["total"],
            "correct": r["correct"],
            "pct": int(r["correct"] / r["total"] * 100) if r["total"] > 0 else 0
        }

    conn.close()
    return {
        "total": total,
        "correct": correct,
        "pct": int(correct / total * 100) if total > 0 else 0,
        "topics": topics
    }

def get_score_over_time(student_id, limit=20):
    conn = get_conn()
    rows = conn.execute(
        """SELECT id, is_correct, created_at FROM history
           WHERE student_id = ?
           ORDER BY id DESC LIMIT ?""",
        (student_id, limit)
    ).fetchall()
    conn.close()
    rows = list(reversed(rows))

    # Running accuracy
    data = []
    running_correct = 0
    for i, r in enumerate(rows):
        running_correct += r["is_correct"]
        data.append({
            "problem_num": i + 1,
            "is_correct": r["is_correct"],
            "running_pct": int(running_correct / (i + 1) * 100),
            "date": r["created_at"]
        })
    return data

def get_history(student_id, limit=50):
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM history
           WHERE student_id = ?
           ORDER BY id DESC LIMIT ?""",
        (student_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def update_misconception(history_id, misconception_type, misconception_detail):
    """Update misconception analysis for a history entry."""
    conn = get_conn()
    conn.execute(
        "UPDATE history SET misconception_type = ?, misconception_detail = ? WHERE id = ?",
        (misconception_type, misconception_detail, history_id)
    )
    conn.commit()
    conn.close()

def get_misconception_stats(student_id):
    """Get misconception type breakdown for dashboard."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT misconception_type, COUNT(*) as cnt
           FROM history
           WHERE student_id = ? AND is_correct = 0 AND misconception_type IS NOT NULL
           GROUP BY misconception_type
           ORDER BY cnt DESC""",
        (student_id,)
    ).fetchall()
    conn.close()
    return {r["misconception_type"]: r["cnt"] for r in rows}

def history_summary_text(student_id, limit=10):
    """Same format as CLI history_summary() so agent prompts work unchanged."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT question, student_answer, correct_answer, is_correct, weakness,
                  misconception_type, misconception_detail, scaffold_level
           FROM history WHERE student_id = ?
           ORDER BY id DESC LIMIT ?""",
        (student_id, limit)
    ).fetchall()
    conn.close()

    if not rows:
        return "No history yet. This is the first problem."

    rows = list(reversed(rows))
    total = len(rows)
    correct = sum(1 for r in rows if r["is_correct"])
    lines = [f"Total: {correct}/{total} correct ({int(correct/total*100)}%)"]
    for r in rows:
        mark = "correct" if r["is_correct"] else "wrong"
        scaffold_tag = f" [scaffold Lv{r['scaffold_level']}]" if r["scaffold_level"] else ""
        lines.append(
            f"  {mark} Q: {r['question']} | Student answered: {r['student_answer']} | Correct: {r['correct_answer']}{scaffold_tag}"
        )
        if not r["is_correct"] and r["misconception_type"]:
            lines.append(f"     Misconception: {r['misconception_type']} - {r['misconception_detail']}")
        elif not r["is_correct"] and r["weakness"]:
            lines.append(f"     Weakness: {r['weakness']}")
    return "\n".join(lines)

# --- Problem Bank ---

def save_to_problem_bank(grade, curriculum_style, topic, question, correct_answer, hint,
                         is_scaffold=False, scaffold_misconception_type=None):
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO problem_bank
           (grade, curriculum_style, topic, question, correct_answer, hint,
            is_scaffold, scaffold_misconception_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (grade, curriculum_style, topic, question, str(correct_answer), hint,
         1 if is_scaffold else 0, scaffold_misconception_type)
    )
    bank_id = cur.lastrowid
    conn.commit()
    conn.close()
    return bank_id

def find_reusable_problem(grade, curriculum_style, topic, student_id,
                          is_scaffold=False, scaffold_misconception_type=None,
                          exclude_recent=20):
    conn = get_conn()
    # Get recent questions this student has seen
    seen_rows = conn.execute(
        "SELECT question FROM history WHERE student_id = ? ORDER BY id DESC LIMIT ?",
        (student_id, exclude_recent)
    ).fetchall()
    seen_questions = {r["question"] for r in seen_rows}

    # Build query
    if is_scaffold and scaffold_misconception_type:
        rows = conn.execute(
            """SELECT * FROM problem_bank
               WHERE grade = ? AND curriculum_style = ? AND topic = ?
                     AND is_scaffold = 1 AND scaffold_misconception_type = ?
               ORDER BY times_served ASC, RANDOM()""",
            (grade, curriculum_style, topic, scaffold_misconception_type)
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM problem_bank
               WHERE grade = ? AND curriculum_style = ? AND topic = ?
                     AND is_scaffold = ?
               ORDER BY times_served ASC, RANDOM()""",
            (grade, curriculum_style, topic, 1 if is_scaffold else 0)
        ).fetchall()

    conn.close()
    for r in rows:
        if r["question"] not in seen_questions:
            return dict(r)
    return None

def increment_times_served(bank_id):
    conn = get_conn()
    conn.execute("UPDATE problem_bank SET times_served = times_served + 1 WHERE id = ?", (bank_id,))
    conn.commit()
    conn.close()

# --- Gamification ---

def _level_from_xp(xp):
    return min(int(0.4 * sqrt(xp)) + 1, 50)

def _level_title(level):
    return LEVEL_TITLES.get(level, "Math Wizard")

def get_gamification_stats(student_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT is_correct, scaffold_level FROM history WHERE student_id = ? ORDER BY id",
        (student_id,)
    ).fetchall()
    conn.close()

    xp = 0
    streak = 0
    best_streak = 0
    for r in rows:
        if r["is_correct"]:
            if r["scaffold_level"] and r["scaffold_level"] > 0:
                xp += 8
            else:
                xp += 10
            streak += 1
            if streak > best_streak:
                best_streak = streak
            # Streak bonuses
            if streak == 3:
                xp += 3
            elif streak == 5:
                xp += 5
            elif streak == 10:
                xp += 10
            elif streak == 20:
                xp += 20
        else:
            xp += 2
            streak = 0

    level = _level_from_xp(xp)
    next_level = level + 1
    # XP thresholds: level = min(int(0.4*sqrt(xp))+1, 50)
    # Solving for xp: xp_for_level = ((level - 1) / 0.4)^2
    xp_for_current = ((level - 1) / 0.4) ** 2 if level > 1 else 0
    xp_for_next = ((next_level - 1) / 0.4) ** 2
    xp_in_level = xp - xp_for_current
    xp_needed = xp_for_next - xp_for_current
    pct = int(xp_in_level / xp_needed * 100) if xp_needed > 0 else 0

    return {
        "xp": xp,
        "level": level,
        "level_title": _level_title(level),
        "xp_for_next": int(xp_for_next),
        "xp_progress_pct": max(0, min(100, pct)),
        "streak": streak,
        "best_streak": best_streak,
    }

def get_unlocked_achievements(student_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT achievement_key, unlocked_at FROM achievements WHERE student_id = ? ORDER BY unlocked_at",
        (student_id,)
    ).fetchall()
    conn.close()
    return [{"key": r["achievement_key"], "unlocked_at": r["unlocked_at"]} for r in rows]

def unlock_achievement(student_id, key):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO achievements (student_id, achievement_key) VALUES (?, ?)",
            (student_id, key)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.close()
        return False

def check_achievements(student_id):
    stats = get_gamification_stats(student_id)
    conn = get_conn()
    correct = conn.execute(
        "SELECT COUNT(*) as c FROM history WHERE student_id = ? AND is_correct = 1",
        (student_id,)
    ).fetchone()["c"]
    total = conn.execute(
        "SELECT COUNT(*) as c FROM history WHERE student_id = ?",
        (student_id,)
    ).fetchone()["c"]
    topics = conn.execute(
        "SELECT COUNT(DISTINCT topic) as c FROM history WHERE student_id = ? AND topic IS NOT NULL",
        (student_id,)
    ).fetchone()["c"]
    scaffold_wins = conn.execute(
        "SELECT COUNT(*) as c FROM history WHERE student_id = ? AND is_correct = 1 AND scaffold_level > 0",
        (student_id,)
    ).fetchone()["c"]
    conn.close()

    newly_unlocked = []
    checks = {
        "first_correct": correct >= 1,
        "ten_correct": correct >= 10,
        "fifty_correct": correct >= 50,
        "hundred_correct": correct >= 100,
        "streak_3": stats["best_streak"] >= 3,
        "streak_5": stats["best_streak"] >= 5,
        "streak_10": stats["best_streak"] >= 10,
        "try_3_topics": topics >= 3,
        "try_5_topics": topics >= 5,
        "ten_problems": total >= 10,
        "fifty_problems": total >= 50,
        "scaffold_win": scaffold_wins >= 1,
        "scaffold_master": scaffold_wins >= 5,
        "perfect_session_5": stats["best_streak"] >= 5,
        "level_5": stats["level"] >= 5,
    }
    for key, condition in checks.items():
        if condition and key in ACHIEVEMENTS:
            if unlock_achievement(student_id, key):
                newly_unlocked.append(key)
    return newly_unlocked

# Initialize on import
init_db()
