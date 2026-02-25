"""Unit tests for db.py"""
import sqlite3
import os
import pytest
import bcrypt
import db


@pytest.fixture(autouse=True)
def use_temp_db(tmp_path, monkeypatch):
    """Use a temporary database for each test."""
    test_db = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init_db()
    yield test_db


# --- PIN hashing ---

class TestPinHashing:
    def test_create_student_hashes_pin(self):
        sid = db.create_student("Alice", "1234", 4)
        student = db.get_student(sid)
        assert student["pin"].startswith("$2"), "PIN should be bcrypt hashed"
        assert student["pin"] != "1234"

    def test_verify_correct_pin(self):
        sid = db.create_student("Bob", "5678", 3)
        assert db.verify_pin(sid, "5678")

    def test_verify_wrong_pin(self):
        sid = db.create_student("Carol", "1111", 2)
        assert not db.verify_pin(sid, "9999")

    def test_verify_nonexistent_student(self):
        assert not db.verify_pin(99999, "1234")

    def test_update_student_hashes_pin(self):
        sid = db.create_student("Dave", "0000", 5)
        db.update_student(sid, pin="4321")
        student = db.get_student(sid)
        assert student["pin"].startswith("$2")
        assert db.verify_pin(sid, "4321")
        assert not db.verify_pin(sid, "0000")

    def test_legacy_plaintext_pin_verifies_and_migrates(self):
        # Insert plaintext PIN directly, bypassing create_student
        conn = db.get_conn()
        conn.execute(
            "INSERT INTO student (name, pin, grade) VALUES (?, ?, ?)",
            ("Legacy", "7777", 3)
        )
        conn.commit()
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()

        # Should verify successfully
        assert db.verify_pin(sid, "7777")

        # Should have been migrated to bcrypt
        student = db.get_student(sid)
        assert student["pin"].startswith("$2")

        # Should still verify after migration
        assert db.verify_pin(sid, "7777")

    def test_legacy_plaintext_wrong_pin_no_migration(self):
        conn = db.get_conn()
        conn.execute(
            "INSERT INTO student (name, pin, grade) VALUES (?, ?, ?)",
            ("Legacy2", "3333", 2)
        )
        conn.commit()
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()

        assert not db.verify_pin(sid, "0000")

        # PIN should remain plaintext (no migration on failed attempt)
        student = db.get_student(sid)
        assert student["pin"] == "3333"


# --- Student CRUD ---

class TestStudentCRUD:
    def test_create_and_get(self):
        sid = db.create_student("Test", "1234", 4, "common_core")
        student = db.get_student(sid)
        assert student["name"] == "Test"
        assert student["grade"] == 4
        assert student["curriculum_style"] == "common_core"

    def test_get_nonexistent(self):
        assert db.get_student(99999) is None

    def test_get_all_students(self):
        db.create_student("A", "1111", 1)
        db.create_student("B", "2222", 2)
        students = db.get_all_students()
        names = [s["name"] for s in students]
        assert "A" in names
        assert "B" in names

    def test_update_student_name_and_grade(self):
        sid = db.create_student("Old", "1234", 3)
        db.update_student(sid, name="New", grade=5)
        student = db.get_student(sid)
        assert student["name"] == "New"
        assert student["grade"] == 5


# --- History ---

class TestHistory:
    def test_save_and_get_history(self):
        sid = db.create_student("H", "1234", 4)
        db.save_result(sid, "2+2", "4", "4", True, topic="addition")
        db.save_result(sid, "3+5", "8", "7", False, topic="addition")
        history = db.get_history(sid)
        assert len(history) == 2
        assert history[0]["is_correct"] == 0  # most recent first (wrong)
        assert history[1]["is_correct"] == 1

    def test_get_stats(self):
        sid = db.create_student("S", "1234", 4)
        db.save_result(sid, "1+1", "2", "2", True, topic="addition")
        db.save_result(sid, "2+3", "5", "4", False, topic="addition")
        db.save_result(sid, "3*2", "6", "6", True, topic="multiplication")
        stats = db.get_stats(sid)
        assert stats["total"] == 3
        assert stats["correct"] == 2
        assert "addition" in stats["topics"]
        assert "multiplication" in stats["topics"]

    def test_get_last_history_id(self):
        sid = db.create_student("L", "1234", 4)
        assert db.get_last_history_id(sid) is None
        db.save_result(sid, "1+1", "2", "2", True)
        hid = db.get_last_history_id(sid)
        assert hid is not None

    def test_update_feedback(self):
        sid = db.create_student("F", "1234", 4)
        db.save_result(sid, "1+1", "2", "2", True)
        hid = db.get_last_history_id(sid)
        db.update_feedback(hid, "Great job!")
        history = db.get_history(sid)
        assert history[0]["feedback"] == "Great job!"

    def test_update_misconception(self):
        sid = db.create_student("M", "1234", 4)
        db.save_result(sid, "5-3", "2", "8", False)
        hid = db.get_last_history_id(sid)
        db.update_misconception(hid, "operation_swap", "Added instead of subtracted")
        history = db.get_history(sid)
        assert history[0]["misconception_type"] == "operation_swap"

    def test_history_summary_text_empty(self):
        sid = db.create_student("E", "1234", 4)
        summary = db.history_summary_text(sid)
        assert "No history" in summary

    def test_history_summary_text_with_data(self):
        sid = db.create_student("T", "1234", 4)
        db.save_result(sid, "1+1", "2", "2", True, topic="addition")
        summary = db.history_summary_text(sid)
        assert "1/1 correct" in summary

    def test_score_over_time(self):
        sid = db.create_student("OT", "1234", 4)
        db.save_result(sid, "1+1", "2", "2", True)
        db.save_result(sid, "2+2", "4", "3", False)
        data = db.get_score_over_time(sid)
        assert len(data) == 2
        assert data[0]["is_correct"] == 1
        assert data[1]["is_correct"] == 0


# --- Problem Bank ---

class TestProblemBank:
    def test_save_and_find(self):
        sid = db.create_student("PB", "1234", 4)
        db.save_to_problem_bank(4, "common_core", "addition", "2+3", "5", "Count on")
        found = db.find_reusable_problem(4, "common_core", "addition", sid)
        assert found is not None
        assert found["question"] == "2+3"

    def test_find_excludes_recent(self):
        sid = db.create_student("PB2", "1234", 4)
        db.save_to_problem_bank(4, "common_core", "addition", "2+3", "5", "Count on")
        # Student has seen this question
        db.save_result(sid, "2+3", "5", "5", True, topic="addition")
        found = db.find_reusable_problem(4, "common_core", "addition", sid, exclude_recent=20)
        assert found is None

    def test_increment_times_served(self):
        bank_id = db.save_to_problem_bank(4, "common_core", "addition", "9+1", "10", "hint")
        db.increment_times_served(bank_id)
        conn = db.get_conn()
        row = conn.execute("SELECT times_served FROM problem_bank WHERE id = ?", (bank_id,)).fetchone()
        conn.close()
        assert row["times_served"] == 1


# --- Gamification ---

class TestGamification:
    def test_initial_stats(self):
        sid = db.create_student("G", "1234", 4)
        stats = db.get_gamification_stats(sid)
        assert stats["xp"] == 0
        assert stats["level"] == 1
        assert stats["streak"] == 0

    def test_xp_gain(self):
        sid = db.create_student("G2", "1234", 4)
        db.save_result(sid, "1+1", "2", "2", True)
        stats = db.get_gamification_stats(sid)
        assert stats["xp"] == 10

    def test_streak_tracking(self):
        sid = db.create_student("G3", "1234", 4)
        for i in range(3):
            db.save_result(sid, f"{i}+1", str(i+1), str(i+1), True)
        stats = db.get_gamification_stats(sid)
        assert stats["streak"] == 3
        assert stats["best_streak"] == 3

    def test_streak_breaks_on_wrong(self):
        sid = db.create_student("G4", "1234", 4)
        db.save_result(sid, "1+1", "2", "2", True)
        db.save_result(sid, "2+2", "4", "3", False)
        stats = db.get_gamification_stats(sid)
        assert stats["streak"] == 0
        assert stats["best_streak"] == 1

    def test_achievements_unlock(self):
        sid = db.create_student("A1", "1234", 4)
        db.save_result(sid, "1+1", "2", "2", True)
        newly = db.check_achievements(sid)
        assert "first_correct" in newly

    def test_achievements_no_duplicate(self):
        sid = db.create_student("A2", "1234", 4)
        db.save_result(sid, "1+1", "2", "2", True)
        db.check_achievements(sid)
        newly = db.check_achievements(sid)
        assert "first_correct" not in newly

    def test_get_unlocked_achievements(self):
        sid = db.create_student("A3", "1234", 4)
        db.save_result(sid, "1+1", "2", "2", True)
        db.check_achievements(sid)
        unlocked = db.get_unlocked_achievements(sid)
        keys = [a["key"] for a in unlocked]
        assert "first_correct" in keys
