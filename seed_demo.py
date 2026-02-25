"""Seed demo data for Alex (student_id=4) so screenshots look interesting."""
import sys, os, random
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db

STUDENT_ID = 4  # Alex

# Clear existing history & achievements for Alex
conn = db.get_conn()
conn.execute("DELETE FROM history WHERE student_id = ?", (STUDENT_ID,))
conn.execute("DELETE FROM achievements WHERE student_id = ?", (STUDENT_ID,))
conn.commit()
conn.close()

# Realistic Grade 4 Singapore Math problems
PROBLEMS = [
    # Addition
    ("Addition", "Sarah had 2,456 stamps. She received 1,378 more. How many stamps does she have now?", "3834", True),
    ("Addition", "A shop sold 3,245 apples on Monday and 2,876 on Tuesday. How many apples were sold in total?", "6121", True),
    ("Addition", "Find the sum of 4,567 and 3,298.", "7865", True),
    ("Addition", "A library has 5,432 books. 2,789 new books arrive. How many books are there now?", "8221", True),
    # Subtraction
    ("Subtraction", "A farmer had 8,000 eggs. He sold 3,456 eggs. How many are left?", "4544", True),
    ("Subtraction", "What is 7,205 minus 4,638?", "2567", True),
    ("Subtraction", "A train had 5,200 passengers. 2,875 got off. How many are still on the train?", "2325", False),  # wrong
    # Multiplication
    ("Multiplication", "A box contains 24 oranges. How many oranges are in 15 boxes?", "360", True),
    ("Multiplication", "Find the product of 36 and 27.", "972", True),
    ("Multiplication", "Each shelf holds 48 books. There are 12 shelves. How many books in total?", "576", True),
    ("Multiplication", "A pack has 35 stickers. How many stickers in 18 packs?", "630", True),
    ("Multiplication", "What is 56 x 23?", "1288", False),  # wrong
    # Division
    ("Division", "864 marbles are shared equally among 6 children. How many does each get?", "144", True),
    ("Division", "A baker made 756 cookies and packed them into boxes of 12. How many boxes?", "63", True),
    ("Division", "What is 945 divided by 9?", "105", True),
    # Fractions
    ("Fractions", "What is 3/4 + 1/8?", "7/8", True),
    ("Fractions", "Simplify: 12/16", "3/4", True),
    ("Fractions", "Tom ate 2/5 of a pizza. Jerry ate 1/5. How much was eaten?", "3/5", True),
    ("Fractions", "What is 5/6 - 1/3?", "1/2", False),  # wrong
    # Word Problems
    ("Word Problems", "A rectangle is 24 cm long and 15 cm wide. What is its perimeter?", "78", True),
    ("Word Problems", "Ben saved $12.50 each week for 8 weeks. How much did he save?", "100", True),
    ("Word Problems", "A rope is 4.5 m long. 3 equal pieces are cut. How long is each piece?", "1.5", True),
    # More correct answers to build streak
    ("Multiplication", "What is 45 x 12?", "540", True),
    ("Addition", "What is 6,789 + 3,456?", "10245", True),
    ("Division", "What is 1,200 divided by 8?", "150", True),
]

base_time = datetime.now() - timedelta(days=5)

for i, (topic, question, answer, is_correct) in enumerate(PROBLEMS):
    # Space out problems over the past 5 days
    created_at = base_time + timedelta(hours=i * 4, minutes=random.randint(0, 30))

    student_answer = answer if is_correct else str(int(answer) + random.choice([-3, -2, 2, 3])) if answer.isdigit() else "wrong"

    conn = db.get_conn()
    conn.execute(
        """INSERT INTO history
           (student_id, question, correct_answer, student_answer, is_correct,
            topic, requested_topic, feedback, weakness,
            misconception_type, misconception_detail, scaffold_level, scaffold_parent_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (STUDENT_ID, question, answer, student_answer,
         1 if is_correct else 0, topic, "Auto",
         "Great job!" if is_correct else "Let's review this.",
         None if is_correct else "needs practice",
         None if is_correct else "computational",
         None if is_correct else "Arithmetic error in calculation",
         0, None,
         created_at.strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()

# Add a scaffold problem (practice after wrong answer)
conn = db.get_conn()
wrong_id = conn.execute(
    "SELECT id FROM history WHERE student_id = ? AND is_correct = 0 LIMIT 1",
    (STUDENT_ID,)
).fetchone()["id"]
conn.execute(
    """INSERT INTO history
       (student_id, question, correct_answer, student_answer, is_correct,
        topic, requested_topic, feedback, scaffold_level, scaffold_parent_id, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    (STUDENT_ID, "What is 5,200 - 2,875?", "2325", "2325", 1,
     "Subtraction", "Auto", "You got it this time!", 1, wrong_id,
     (base_time + timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S"))
)
conn.commit()
conn.close()

# Unlock achievements
achievements_to_unlock = [
    "first_correct", "ten_correct", "streak_3", "streak_5",
    "try_3_topics", "try_5_topics", "ten_problems",
    "scaffold_win", "perfect_session_5"
]

conn = db.get_conn()
for key in achievements_to_unlock:
    try:
        conn.execute(
            "INSERT INTO achievements (student_id, achievement_key) VALUES (?, ?)",
            (STUDENT_ID, key)
        )
    except Exception:
        pass
conn.commit()
conn.close()

# Verify
stats = db.get_stats(STUDENT_ID)
gam = db.get_gamification_stats(STUDENT_ID)
achs = db.get_unlocked_achievements(STUDENT_ID)

print(f"Seeded {stats['total']} problems: {stats['correct']} correct ({stats['pct']}%)")
print(f"XP: {gam['xp']}, Level: {gam['level']} ({gam['level_title']})")
print(f"Streak: {gam['streak']}, Best: {gam['best_streak']}")
print(f"Achievements: {len(achs)} unlocked")
print(f"Topics: {list(stats['topics'].keys())}")
