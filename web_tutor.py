import json
import os
import re
import logging
import warnings
import threading
import asyncio
from queue import Queue, Empty

# Suppress noisy logs
logging.getLogger("LiteLLM").setLevel(logging.CRITICAL)
logging.getLogger("litellm").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")

from dotenv import load_dotenv
load_dotenv()

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import HTMLResponse, JSONResponse
from starlette.requests import Request
from sse_starlette.sse import EventSourceResponse

from crewai import Agent, Task, Crew, LLM
import db

def _parse_json_lenient(raw):
    """Parse JSON that may contain invalid escapes like LaTeX \\frac{}{} or \\(."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Fix invalid backslash escapes: replace \X where X is not a valid JSON escape
    # Valid JSON escapes: \", \\, \/, \b, \f, \n, \r, \t, \uXXXX
    sanitized = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw)
    return json.loads(sanitized)

def _clean_latex(text):
    """Strip LaTeX notation from text, converting to plain readable math."""
    if not text or not isinstance(text, str):
        return text
    # \frac{a}{b} -> a/b (normal backslash version)
    text = re.sub(r'\\frac\{([^}]*)\}\{([^}]*)\}', r'\1/\2', text)
    # \frac after JSON parsing: \f becomes form-feed char, so "frac" -> <FF>rac
    text = re.sub(r'\frac\{([^}]*)\}\{([^}]*)\}', r'\1/\2', text)
    # Remove \( \) and \[ \] delimiters
    text = re.sub(r'\\[(\[]', '', text)
    text = re.sub(r'\\[)\]]', '', text)
    # \times -> x, \div -> /, \cdot -> *
    text = text.replace('\\times', 'x').replace('\\div', '/').replace('\\cdot', '*')
    # Remove remaining backslash commands like \text{...} -> ...
    text = re.sub(r'\\text\{([^}]*)\}', r'\1', text)
    # Clean up stray form-feed chars from \f being parsed as JSON escape
    text = text.replace('\f', '')
    return text.strip()

# === LLM Setup ===
gemini_llm = LLM(
    model="gemini/gemini-2.5-flash",
    api_key=os.getenv("GEMINI_API_KEY"),
)

USE_LOCAL = os.getenv("USE_LOCAL_LLM", "true").lower() == "true"
try:
    if USE_LOCAL:
        local_llm = LLM(model="ollama/gemma3:4b", base_url="http://localhost:11434")
    else:
        local_llm = gemini_llm
except Exception:
    local_llm = gemini_llm

# === Curriculum Styles ===
CURRICULUM_STYLES = {
    "common_core": {
        "display_name": "Common Core",
        "pedagogy": (
            "Focus on conceptual understanding and real-world problem solving. "
            "Use visual models (number lines, area models, tape diagrams). "
            "Emphasize explaining reasoning and multiple solution strategies. "
            "Align with California Common Core State Standards."
        ),
        "grades": {
            1: "Addition and subtraction within 20, place value to 120, measuring lengths, basic shapes",
            2: "Addition and subtraction within 100, intro to place value (hundreds), measuring/estimating lengths, basic arrays for multiplication",
            3: "Multiplication and division within 100, fractions on number lines, area and perimeter, rounding to nearest 10/100",
            4: "Multi-digit arithmetic, fraction equivalence and ordering, decimal notation (tenths/hundredths), angles and lines, multi-step word problems",
            5: "Fraction operations (add/subtract/multiply), decimal operations, volume, coordinate plane, order of operations",
            6: "Ratios and proportional relationships, dividing fractions, integers and rational numbers, expressions and equations, statistical thinking",
        },
    },
    "rsm": {
        "display_name": "RSM",
        "pedagogy": (
            "Emphasize logical reasoning and algebraic thinking from early grades. "
            "Use challenging multi-step problems that build abstract thinking. "
            "Introduce concepts 1-2 years ahead of standard curriculum. "
            "Focus on problem-solving strategies, pattern recognition, and mathematical proof. "
            "Russian School of Mathematics approach."
        ),
        "grades": {
            1: "Addition/subtraction within 100, intro to multiplication as groups, simple logic puzzles, number patterns, basic algebraic thinking (find the missing number)",
            2: "Multiplication/division facts, multi-step addition/subtraction, intro to fractions as parts, number patterns and sequences, simple equations with unknowns",
            3: "Multi-digit multiplication, long division, fraction operations, intro to negative numbers, algebraic expressions, logic and combinatorics problems",
            4: "Advanced fraction/decimal operations, intro to ratios, order of operations with parentheses, coordinate graphing, multi-step challenge word problems, basic number theory (factors/multiples/primes)",
            5: "Ratio and proportion, percent applications, integer arithmetic, algebraic equations (one variable), geometry proofs (angles, triangles), combinatorics and probability intro",
            6: "Linear equations and inequalities, advanced ratios/proportions/percents, geometry (circles, Pythagorean theorem intro), statistics, exponents, intro to functions",
        },
    },
    "singapore": {
        "display_name": "Singapore Math",
        "pedagogy": (
            "CPA (Concrete-Pictorial-Abstract) approach. "
            "Use bar models for word problems and part-whole/comparison models. "
            "Emphasize number bonds, mental math strategies, and place value mastery. "
            "Build deep number sense before moving to algorithms. "
            "Singapore Math / Math in Focus methodology."
        ),
        "grades": {
            1: "Number bonds within 20, addition/subtraction strategies (making 10), place value to 100, mental math, bar models for simple word problems, basic shapes and patterns",
            2: "Addition/subtraction within 1000, multiplication tables (2,3,4,5,10), bar models for two-step problems, mental math strategies, measurement (length, mass, volume), money",
            3: "All multiplication/division facts, bar models for multi-step problems, fraction concepts (naming, comparing, equivalent), mental math (compensation, rounding), area and perimeter, time and measurement",
            4: "Multi-digit multiplication/division, fraction operations (like denominators), decimal concepts and operations, bar models for fraction/ratio word problems, angles and geometric figures, data analysis",
            5: "Fraction operations (unlike denominators, multiply/divide), decimal operations, ratio and proportion, percent, volume of solids, coordinate geometry, algebraic expressions",
            6: "Advanced ratio/proportion/percent, algebraic expressions and equations, geometry (area of triangles/circles, nets, surface area), data analysis and probability, negative numbers, rate and speed problems",
        },
    },
}
VALID_STYLES = list(CURRICULUM_STYLES.keys())

KNOWN_TOPICS = [
    "Addition", "Subtraction", "Multiplication", "Division",
    "Mixed Operations", "Fractions", "Decimals", "Geometry", "Word Problems"
]

def _extract_topic_from_analysis(analysis_text):
    """Extract canonical topic from Manager's freeform analysis. Returns None on failure."""
    analysis_lower = analysis_text.lower()
    for topic in KNOWN_TOPICS:
        if topic.lower() in analysis_lower:
            return topic
    return None

def get_curriculum_info(grade, style="common_core"):
    """Return (grade_scope, pedagogy) for a given grade and curriculum style."""
    s = CURRICULUM_STYLES.get(style, CURRICULUM_STYLES["common_core"])
    grade_scope = s["grades"].get(grade, s["grades"].get(4, ""))
    return grade_scope, s["pedagogy"]

# === Per-session state ===
# Maps session_id -> Queue for SSE events
sse_queues: dict[str, Queue] = {}
# Maps session_id -> current problem data
current_problems: dict[str, dict] = {}
# Maps session_id -> scaffold context (misconception info, level, parent_history_id)
scaffold_states: dict[str, dict] = {}

def get_session_id(request: Request) -> str | None:
    return request.cookies.get("session_id")

def get_student_id(request: Request) -> int | None:
    sid = request.cookies.get("student_id")
    return int(sid) if sid else None

# === Agent helpers ===
def run_agent_task(agent, description, expected_output):
    task = Task(description=description, expected_output=expected_output, agent=agent)
    crew = Crew(agents=[agent], tasks=[task], verbose=False)
    return str(crew.kickoff())

def send_event(session_id, event_type, data):
    q = sse_queues.get(session_id)
    if q:
        q.put({"event": event_type, "data": json.dumps(data)})

def make_agents(student):
    """Create agents with student-specific prompts."""
    name = student["name"]
    grade = student["grade"]
    style = student.get("curriculum_style", "common_core")
    curriculum, pedagogy = get_curriculum_info(grade, style)

    manager = Agent(
        role="Learning Manager",
        goal=f"Analyze {name}'s history and decide what kind of problem to give next. "
             "Give SPECIFIC instructions: topic, difficulty, and what to focus on.",
        backstory=f"You are an expert elementary math tutor for {name} (Grade {grade}). "
                  f"Their curriculum covers: {curriculum}. "
                  f"Teaching approach: {pedagogy} "
                  "You look at what the student got right and wrong, and pick the perfect next challenge.",
        llm=gemini_llm,
        verbose=False,
    )
    creator = Agent(
        role="Problem Creator",
        goal=f"Create exactly ONE math word problem for {name} (Grade {grade}) based on the manager's instructions. "
             "You MUST output valid JSON with keys: question, correct_answer (number only), hint, topic. "
             "DOUBLE CHECK that correct_answer is mathematically correct!",
        backstory=f"You create fun, story-based math problems for a Grade {grade} student using everyday "
                  "situations kids love (snacks, pets, toys, sports). You always match the requested difficulty. "
                  f"Teaching approach: {pedagogy} "
                  "You always verify your math is correct before giving the answer.",
        llm=gemini_llm,
        verbose=False,
    )
    helper = Agent(
        role="Solution Helper",
        goal=f"Explain the solution step-by-step in a way {name} (Grade {grade}) can understand. "
             "Be warm and encouraging. If the student got it wrong, gently show where the mistake was.",
        backstory="You're amazing at making kids understand math. You use simple words, "
                  "fun comparisons, and always make the student feel good about trying. "
                  f"Teaching approach: {pedagogy}",
        llm=local_llm,
        verbose=False,
    )
    analyst = Agent(
        role="Misconception Analyst",
        goal="Analyze the student's wrong answer to identify the specific misconception or error pattern. "
             "Classify the error and suggest what concept needs targeted practice.",
        backstory="You are an expert in math education diagnostics. You look at a student's wrong answer "
                  "and figure out exactly WHY they got it wrong — was it a calculation slip, a conceptual "
                  "misunderstanding, a procedural error, or carelessness? You then recommend targeted practice.",
        llm=gemini_llm,
        verbose=False,
    )
    return manager, creator, helper, analyst

# === Background work: generate problem ===
def generate_problem_bg(session_id, student, requested_topic):
    try:
        manager, creator, helper, analyst = make_agents(student)
        student_id = student["id"]
        name = student["name"]
        grade = student["grade"]
        style = student.get("curriculum_style", "common_core")
        curriculum, pedagogy = get_curriculum_info(grade, style)
        h = db.history_summary_text(student_id)

        topic_instruction = ""
        if requested_topic:
            topic_instruction = f"\n\nIMPORTANT: The student specifically requested a problem about: {requested_topic}. Focus on this topic, but keep the difficulty appropriate for Grade {grade} (Curriculum: {curriculum})."

        # Step 1: Manager
        send_event(session_id, "pipeline", {"agents": [
            {"key": "manager", "status": "working"},
            {"key": "creator", "status": "waiting"},
        ]})

        analysis = run_agent_task(
            manager,
            f"Student: {name}, Grade {grade}.\n"
            f"Curriculum: {curriculum}\n"
            f"Teaching approach: {pedagogy}\n\n"
            f"Learning history (recent):\n{h}\n"
            f"{topic_instruction}\n\n"
            "Analyze and give specific instructions for the next problem. "
            "State the topic, difficulty level, and what skill to target.",
            "Specific instructions for next problem (topic, difficulty, focus area)"
        )

        # Step 2: Try problem bank before calling Creator
        extracted_topic = _extract_topic_from_analysis(analysis)
        resolved_topic = requested_topic or extracted_topic

        cached = None
        if resolved_topic:
            cached = db.find_reusable_problem(grade, style, resolved_topic, student_id)

        if cached:
            # Bank hit — skip Creator LLM call
            db.increment_times_served(cached["id"])
            problem = {
                "question": cached["question"],
                "correct_answer": cached["correct_answer"],
                "hint": cached["hint"],
                "topic": cached["topic"],
            }
            send_event(session_id, "pipeline", {"agents": [
                {"key": "manager", "status": "done"},
                {"key": "creator", "status": "done"},
            ]})
        else:
            # Bank miss — run Creator as usual
            send_event(session_id, "pipeline", {"agents": [
                {"key": "manager", "status": "done"},
                {"key": "creator", "status": "working"},
            ]})

            result = run_agent_task(
                creator,
                f"The Learning Manager says:\n{analysis}\n\n"
                f"CRITICAL CONSTRAINT: This student is in Grade {grade}. "
                f"Their curriculum covers ONLY: {curriculum}. "
                f"Teaching approach: {pedagogy}\n"
                f"The problem difficulty MUST match Grade {grade} level — do NOT create problems beyond this scope.\n\n"
                "Based on these instructions, create exactly ONE math word problem.\n\n"
                "IMPORTANT: Your final answer must be ONLY valid JSON in this exact format:\n"
                '{"question": "the word problem text", "correct_answer": 42, "hint": "a helpful hint", "topic": "Addition"}\n\n'
                "Rules:\n"
                "- correct_answer must be a single number (integer or decimal)\n"
                "- VERIFY your math is correct!\n"
                f"- question should be a fun story problem for a Grade {grade} student\n"
                "- hint should help without giving away the answer\n"
                "- topic should be one of: Addition, Subtraction, Multiplication, Division, Mixed Operations, Fractions, Decimals, Geometry, Word Problems\n"
                "- Do NOT use LaTeX or math notation like \\frac{}{} or \\( \\). Write fractions as plain text like '1/3' or 'one third'\n"
                "- Output ONLY the JSON, nothing else",
                '{"question": "...", "correct_answer": number, "hint": "...", "topic": "..."}'
            )

            send_event(session_id, "pipeline", {"agents": [
                {"key": "manager", "status": "done"},
                {"key": "creator", "status": "done"},
            ]})

            # Parse JSON - find outermost { ... } and sanitize LaTeX escapes
            problem = None
            try:
                start = result.find('{')
                end = result.rfind('}')
                if start != -1 and end > start:
                    raw = result[start:end+1]
                    problem = _parse_json_lenient(raw)
            except Exception:
                pass

            if not problem:
                problem = {
                    "question": result.strip(),
                    "correct_answer": None,
                    "hint": "Try your best!",
                    "topic": requested_topic or "Unknown"
                }

            # Clean any LaTeX remnants from display text
            problem["question"] = _clean_latex(problem.get("question", ""))
            problem["hint"] = _clean_latex(problem.get("hint", ""))

            # Save to problem bank on successful parse
            save_topic = problem.get("topic") or resolved_topic or "Unknown"
            if problem.get("correct_answer") is not None:
                db.save_to_problem_bank(
                    grade, style, save_topic,
                    problem["question"], problem["correct_answer"], problem.get("hint", "")
                )

        problem["requested_topic"] = requested_topic
        current_problems[session_id] = problem
        send_event(session_id, "problem", problem)

    except Exception as e:
        send_event(session_id, "error_msg", {"message": str(e)})

# === Background work: check answer + feedback ===
def check_answer_bg(session_id, student, answer_str):
    try:
        problem = current_problems.get(session_id)
        if not problem:
            send_event(session_id, "error_msg", {"message": "No active problem"})
            return

        question = problem.get("question", "")
        correct_answer = problem.get("correct_answer")
        topic = problem.get("topic")
        requested_topic = problem.get("requested_topic")
        is_scaffold = problem.get("is_scaffold", False)
        scaffold_level = problem.get("scaffold_level", 0)

        # Parse answer
        try:
            student_num = int(answer_str)
        except ValueError:
            try:
                student_num = float(answer_str)
            except ValueError:
                send_event(session_id, "error_msg", {"message": "Please enter a number"})
                return

        # Check correctness
        if correct_answer is not None:
            try:
                is_correct = (float(student_num) == float(correct_answer))
            except (ValueError, TypeError):
                is_correct = False
        else:
            is_correct = False

        # Determine scaffold parent
        scaffold_ctx = scaffold_states.get(session_id)
        parent_history_id = scaffold_ctx.get("parent_history_id") if scaffold_ctx else None

        # Gamification: capture state before save
        gam_before = db.get_gamification_stats(student["id"])

        # Save to DB
        db.save_result(
            student_id=student["id"],
            question=question,
            correct_answer=correct_answer,
            student_answer=student_num,
            is_correct=is_correct,
            topic=topic,
            requested_topic=requested_topic,
            weakness=None if is_correct else "needs review",
            scaffold_level=scaffold_level,
            scaffold_parent_id=parent_history_id,
        )
        history_id = db.get_last_history_id(student["id"])

        # --- CORRECT ANSWER ---
        if is_correct:
            send_event(session_id, "pipeline", {"agents": [
                {"key": "helper", "status": "working"},
            ]})
            manager, creator, helper, analyst = make_agents(student)
            name = student["name"]
            grade = student["grade"]
            style = student.get("curriculum_style", "common_core")
            _, pedagogy = get_curriculum_info(grade, style)

            feedback = run_agent_task(
                helper,
                f"The student ({name}, Grade {grade}) just answered a math problem.\n\n"
                f"Problem: {question}\n"
                f"Correct answer: {correct_answer}\n"
                f"Student's answer: {student_num}\n"
                f"Result: CORRECT!\n"
                f"Teaching approach: {pedagogy}\n\n"
                "Give a SHORT response (3-5 sentences):\n"
                "- Praise them and explain briefly why the answer is right\n"
                f"Keep it warm, fun, and at a Grade {grade} level.",
                "Short, encouraging feedback (3-5 sentences)"
            )
            if history_id:
                db.update_feedback(history_id, feedback)

            send_event(session_id, "pipeline", {"agents": [
                {"key": "helper", "status": "done"},
            ]})

            # If this was a scaffold problem, signal completion
            if is_scaffold:
                scaffold_states.pop(session_id, None)
                gam_after = db.get_gamification_stats(student["id"])
                new_achievements = db.check_achievements(student["id"])
                xp_earned = gam_after["xp"] - gam_before["xp"]
                leveled_up = gam_after["level"] > gam_before["level"]
                send_event(session_id, "feedback", {
                    "is_correct": True,
                    "correct_answer": correct_answer,
                    "feedback": feedback,
                    "skipped": False,
                    "scaffold_complete": True,
                    "xp_earned": xp_earned,
                    "xp_total": gam_after["xp"],
                    "level": gam_after["level"],
                    "level_title": gam_after["level_title"],
                    "leveled_up": leveled_up,
                    "streak": gam_after["streak"],
                    "xp_for_next": gam_after["xp_for_next"],
                    "xp_progress_pct": gam_after["xp_progress_pct"],
                    "new_achievements": [{"key": k, **db.ACHIEVEMENTS.get(k, {})} for k in new_achievements],
                })
            else:
                gam_after = db.get_gamification_stats(student["id"])
                new_achievements = db.check_achievements(student["id"])
                xp_earned = gam_after["xp"] - gam_before["xp"]
                leveled_up = gam_after["level"] > gam_before["level"]
                send_event(session_id, "feedback", {
                    "is_correct": True,
                    "correct_answer": correct_answer,
                    "feedback": feedback,
                    "skipped": False,
                    "xp_earned": xp_earned,
                    "xp_total": gam_after["xp"],
                    "level": gam_after["level"],
                    "level_title": gam_after["level_title"],
                    "leveled_up": leveled_up,
                    "streak": gam_after["streak"],
                    "xp_for_next": gam_after["xp_for_next"],
                    "xp_progress_pct": gam_after["xp_progress_pct"],
                    "new_achievements": [{"key": k, **db.ACHIEVEMENTS.get(k, {})} for k in new_achievements],
                })
            return

        # --- WRONG ANSWER ---
        # Step 1: Helper feedback (immediate)
        send_event(session_id, "pipeline", {"agents": [
            {"key": "helper", "status": "working"},
        ]})

        manager, creator, helper, analyst = make_agents(student)
        name = student["name"]
        grade = student["grade"]
        style = student.get("curriculum_style", "common_core")
        _, pedagogy = get_curriculum_info(grade, style)

        feedback = run_agent_task(
            helper,
            f"The student ({name}, Grade {grade}) just answered a math problem.\n\n"
            f"Problem: {question}\n"
            f"Correct answer: {correct_answer}\n"
            f"Student's answer: {student_num}\n"
            f"Result: WRONG (correct answer was {correct_answer})\n"
            f"Teaching approach: {pedagogy}\n\n"
            "Give a SHORT response (3-5 sentences):\n"
            "- Be gentle, show the correct steps simply, encourage them to try again\n"
            f"Keep it warm, fun, and at a Grade {grade} level.",
            "Short, encouraging feedback (3-5 sentences)"
        )
        if history_id:
            db.update_feedback(history_id, feedback)

        send_event(session_id, "pipeline", {"agents": [
            {"key": "helper", "status": "done"},
        ]})

        # Step 2: Check scaffold limit
        current_scaffold_level = scaffold_level  # level of the problem just answered
        if current_scaffold_level >= 2:
            # Max scaffold depth reached — no more practice rounds
            scaffold_states.pop(session_id, None)
            gam_after = db.get_gamification_stats(student["id"])
            new_achievements = db.check_achievements(student["id"])
            xp_earned = gam_after["xp"] - gam_before["xp"]
            leveled_up = gam_after["level"] > gam_before["level"]
            send_event(session_id, "feedback", {
                "is_correct": False,
                "correct_answer": correct_answer,
                "feedback": feedback,
                "skipped": False,
                "analyzing": False,
                "scaffold_maxed": True,
                "xp_earned": xp_earned,
                "xp_total": gam_after["xp"],
                "level": gam_after["level"],
                "level_title": gam_after["level_title"],
                "leveled_up": leveled_up,
                "streak": gam_after["streak"],
                "xp_for_next": gam_after["xp_for_next"],
                "xp_progress_pct": gam_after["xp_progress_pct"],
                "new_achievements": [{"key": k, **db.ACHIEVEMENTS.get(k, {})} for k in new_achievements],
            })
            return

        # Step 3: Send feedback with analyzing flag, then run Analyst
        gam_after = db.get_gamification_stats(student["id"])
        new_achievements = db.check_achievements(student["id"])
        xp_earned = gam_after["xp"] - gam_before["xp"]
        leveled_up = gam_after["level"] > gam_before["level"]
        send_event(session_id, "feedback", {
            "is_correct": False,
            "correct_answer": correct_answer,
            "feedback": feedback,
            "skipped": False,
            "analyzing": True,
            "xp_earned": xp_earned,
            "xp_total": gam_after["xp"],
            "level": gam_after["level"],
            "level_title": gam_after["level_title"],
            "leveled_up": leveled_up,
            "streak": gam_after["streak"],
            "xp_for_next": gam_after["xp_for_next"],
            "xp_progress_pct": gam_after["xp_progress_pct"],
            "new_achievements": [{"key": k, **db.ACHIEVEMENTS.get(k, {})} for k in new_achievements],
        })

        send_event(session_id, "pipeline", {"agents": [
            {"key": "analyst", "status": "working"},
        ]})

        analyst_result = run_agent_task(
            analyst,
            f"Student: {name}, Grade {grade}\n"
            f"Problem: {question}\n"
            f"Correct answer: {correct_answer}\n"
            f"Student's answer: {student_num}\n\n"
            "Analyze why the student got this wrong. Output ONLY valid JSON:\n"
            '{"misconception_type": "computational|conceptual|procedural|careless", '
            '"misconception_detail": "brief explanation of the specific error", '
            '"scaffold_topic": "what concept to practice", '
            '"scaffold_hint": "a tip for the practice problem"}\n\n'
            "Rules:\n"
            "- misconception_type must be one of: computational, conceptual, procedural, careless\n"
            "- misconception_detail: 1 sentence explaining what went wrong\n"
            "- scaffold_topic: the specific skill to reinforce\n"
            "- scaffold_hint: a gentle hint for the upcoming practice problem\n"
            "- Output ONLY the JSON, nothing else",
            '{"misconception_type": "...", "misconception_detail": "...", "scaffold_topic": "...", "scaffold_hint": "..."}'
        )

        send_event(session_id, "pipeline", {"agents": [
            {"key": "analyst", "status": "done"},
        ]})

        # Parse analyst JSON
        misconception = None
        try:
            start = analyst_result.find('{')
            end = analyst_result.rfind('}')
            if start != -1 and end > start:
                misconception = _parse_json_lenient(analyst_result[start:end+1])
        except Exception:
            pass

        if not misconception:
            misconception = {
                "misconception_type": "unknown",
                "misconception_detail": "Could not determine the specific error",
                "scaffold_topic": topic or "General math",
                "scaffold_hint": "Try thinking step by step!",
            }

        # Save misconception to DB
        if history_id:
            db.update_misconception(
                history_id,
                misconception.get("misconception_type", "unknown"),
                misconception.get("misconception_detail", ""),
            )

        # Determine next scaffold level
        next_level = current_scaffold_level + 1

        # Store scaffold context
        scaffold_states[session_id] = {
            "misconception": misconception,
            "scaffold_level": next_level,
            "parent_history_id": parent_history_id or history_id,
            "topic": topic,
            "original_question": question,
        }

        # Send scaffold_ready event
        send_event(session_id, "scaffold_ready", {
            "misconception_type": misconception.get("misconception_type"),
            "misconception_detail": misconception.get("misconception_detail"),
            "scaffold_topic": misconception.get("scaffold_topic"),
            "scaffold_hint": misconception.get("scaffold_hint"),
            "scaffold_level": next_level,
            "available": True,
        })

    except Exception as e:
        send_event(session_id, "error_msg", {"message": str(e)})

# === Background work: generate scaffold problem ===
def generate_scaffold_problem_bg(session_id, student):
    try:
        scaffold_ctx = scaffold_states.get(session_id)
        if not scaffold_ctx:
            send_event(session_id, "error_msg", {"message": "No scaffold context"})
            return

        misconception = scaffold_ctx["misconception"]
        scaffold_level = scaffold_ctx["scaffold_level"]
        topic = scaffold_ctx.get("topic", "Math")
        misconception_type = misconception.get("misconception_type")
        name = student["name"]
        grade = student["grade"]
        style = student.get("curriculum_style", "common_core")
        curriculum, pedagogy = get_curriculum_info(grade, style)

        # Try problem bank first
        cached = db.find_reusable_problem(
            grade, style, topic, student["id"],
            is_scaffold=True, scaffold_misconception_type=misconception_type
        )

        if cached:
            # Bank hit — skip Creator LLM call entirely (no Agent init needed)
            db.increment_times_served(cached["id"])
            problem = {
                "question": cached["question"],
                "correct_answer": cached["correct_answer"],
                "hint": cached["hint"],
                "topic": cached["topic"],
            }
            send_event(session_id, "pipeline", {"agents": [
                {"key": "creator", "status": "done"},
            ]})
        else:
            # Bank miss — initialize agents and run Creator
            manager, creator, helper, analyst = make_agents(student)

            send_event(session_id, "pipeline", {"agents": [
                {"key": "creator", "status": "working"},
            ]})

            result = run_agent_task(
                creator,
                f"This student ({name}, Grade {grade}) just got a problem wrong.\n\n"
                f"CRITICAL CONSTRAINT: This student is in Grade {grade}. "
                f"Their curriculum covers ONLY: {curriculum}. "
                f"Teaching approach: {pedagogy}\n"
                f"The problem difficulty MUST match Grade {grade} level.\n\n"
                f"Misconception type: {misconception_type}\n"
                f"What went wrong: {misconception.get('misconception_detail')}\n"
                f"Topic to practice: {misconception.get('scaffold_topic')}\n"
                f"Original problem topic: {topic}\n\n"
                "Create a SIMPLER practice problem that targets this specific weakness.\n"
                "The problem should be EASIER than the original so the student can build confidence.\n\n"
                "IMPORTANT: Your final answer must be ONLY valid JSON in this exact format:\n"
                '{"question": "the word problem text", "correct_answer": 42, "hint": "a helpful hint", "topic": "' + (topic or "Math") + '"}\n\n'
                "Rules:\n"
                "- Make it simpler/easier than a typical grade-level problem\n"
                "- Focus specifically on the misconception area\n"
                "- correct_answer must be a single number (integer or decimal)\n"
                "- VERIFY your math is correct!\n"
                f"- question should be a fun story problem for a Grade {grade} student\n"
                f"- hint: {misconception.get('scaffold_hint', 'Think step by step!')}\n"
                "- Do NOT use LaTeX or math notation\n"
                "- Output ONLY the JSON, nothing else",
                '{"question": "...", "correct_answer": number, "hint": "...", "topic": "..."}'
            )

            send_event(session_id, "pipeline", {"agents": [
                {"key": "creator", "status": "done"},
            ]})

            # Parse JSON
            problem = None
            try:
                start = result.find('{')
                end = result.rfind('}')
                if start != -1 and end > start:
                    problem = _parse_json_lenient(result[start:end+1])
            except Exception:
                pass

            if not problem:
                problem = {
                    "question": result.strip(),
                    "correct_answer": None,
                    "hint": misconception.get("scaffold_hint", "Try your best!"),
                    "topic": topic or "Unknown",
                }

            problem["question"] = _clean_latex(problem.get("question", ""))
            problem["hint"] = _clean_latex(problem.get("hint", ""))

            # Save to problem bank on successful parse
            if problem.get("correct_answer") is not None:
                db.save_to_problem_bank(
                    grade, style, problem.get("topic", topic),
                    problem["question"], problem["correct_answer"], problem.get("hint", ""),
                    is_scaffold=True, scaffold_misconception_type=misconception_type
                )

        problem["is_scaffold"] = True
        problem["scaffold_level"] = scaffold_level
        problem["requested_topic"] = None

        current_problems[session_id] = problem
        send_event(session_id, "problem", problem)

    except Exception as e:
        send_event(session_id, "error_msg", {"message": str(e)})

# === Routes ===

async def homepage(request: Request):
    with open(os.path.join(os.path.dirname(__file__), "templates", "index.html")) as f:
        html = f.read()
    return HTMLResponse(html)

async def api_students(request: Request):
    return JSONResponse(db.get_all_students())

async def api_student(request: Request):
    student_id = get_student_id(request)
    if student_id:
        student = db.get_student(student_id)
        if student:
            return JSONResponse({"id": student["id"], "name": student["name"], "grade": student["grade"], "curriculum_style": student.get("curriculum_style", "common_core")})
    return JSONResponse({})

async def api_login(request: Request):
    data = await request.json()
    student_id = data.get("student_id")
    pin = data.get("pin", "")
    if not student_id:
        return JSONResponse({"error": "Missing student_id"}, status_code=400)
    if not db.verify_pin(student_id, pin):
        return JSONResponse({"error": "Wrong PIN"}, status_code=401)
    student = db.get_student(student_id)
    resp = JSONResponse({"id": student["id"], "name": student["name"], "grade": student["grade"], "curriculum_style": student.get("curriculum_style", "common_core")})
    session_id = f"s{student_id}_{os.urandom(4).hex()}"
    resp.set_cookie("student_id", str(student_id), max_age=86400*30)
    resp.set_cookie("session_id", session_id, max_age=86400*30)
    return resp

async def api_setup(request: Request):
    data = await request.json()
    name = data.get("name", "").strip()
    grade = data.get("grade")
    pin = data.get("pin")
    curriculum_style = data.get("curriculum_style", "common_core")

    if not name:
        return JSONResponse({"error": "Name required"}, status_code=400)
    if not grade or grade not in range(1, 7):
        return JSONResponse({"error": "Grade must be 1-6"}, status_code=400)
    if curriculum_style not in VALID_STYLES:
        curriculum_style = "common_core"

    student_id = get_student_id(request)
    if student_id:
        # Update existing
        update_kwargs = {"name": name, "grade": grade, "curriculum_style": curriculum_style}
        if pin:
            update_kwargs["pin"] = pin
        db.update_student(student_id, **update_kwargs)
        student = db.get_student(student_id)
    else:
        # Create new
        if not pin or len(pin) != 4:
            return JSONResponse({"error": "4-digit PIN required"}, status_code=400)
        student_id = db.create_student(name, pin, grade, curriculum_style)
        student = db.get_student(student_id)

    resp = JSONResponse({"id": student["id"], "name": student["name"], "grade": student["grade"], "curriculum_style": student.get("curriculum_style", "common_core")})
    existing_session = get_session_id(request)
    if existing_session and student_id == get_student_id(request):
        # Keep existing session for updates (don't break SSE connection)
        pass
    else:
        session_id = f"s{student_id}_{os.urandom(4).hex()}"
        resp.set_cookie("student_id", str(student_id), max_age=86400*30)
        resp.set_cookie("session_id", session_id, max_age=86400*30)
    return resp

async def api_logout(request: Request):
    session_id = get_session_id(request)
    if session_id and session_id in sse_queues:
        del sse_queues[session_id]
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("student_id")
    resp.delete_cookie("session_id")
    return resp

async def api_new_problem(request: Request):
    student_id = get_student_id(request)
    session_id = get_session_id(request)
    if not student_id or not session_id:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    student = db.get_student(student_id)
    if not student:
        return JSONResponse({"error": "Student not found"}, status_code=404)

    # Clear scaffold state when requesting a normal new problem
    scaffold_states.pop(session_id, None)

    data = await request.json()
    topic = data.get("topic")

    threading.Thread(
        target=generate_problem_bg,
        args=(session_id, student, topic),
        daemon=True
    ).start()
    return JSONResponse({"ok": True})

async def api_submit_answer(request: Request):
    student_id = get_student_id(request)
    session_id = get_session_id(request)
    if not student_id or not session_id:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    student = db.get_student(student_id)
    if not student:
        return JSONResponse({"error": "Student not found"}, status_code=404)

    data = await request.json()
    answer = data.get("answer", "").strip()
    if not answer:
        return JSONResponse({"error": "No answer"}, status_code=400)

    threading.Thread(
        target=check_answer_bg,
        args=(session_id, student, answer),
        daemon=True
    ).start()
    return JSONResponse({"ok": True})

async def api_scaffold_problem(request: Request):
    student_id = get_student_id(request)
    session_id = get_session_id(request)
    if not student_id or not session_id:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    student = db.get_student(student_id)
    if not student:
        return JSONResponse({"error": "Student not found"}, status_code=404)
    if session_id not in scaffold_states:
        return JSONResponse({"error": "No scaffold context"}, status_code=400)

    threading.Thread(
        target=generate_scaffold_problem_bg,
        args=(session_id, student),
        daemon=True
    ).start()
    return JSONResponse({"ok": True})

async def api_skip(request: Request):
    student_id = get_student_id(request)
    session_id = get_session_id(request)
    if not student_id or not session_id:
        return JSONResponse({"error": "Not logged in"}, status_code=401)

    problem = current_problems.get(session_id)
    if problem:
        db.save_result(
            student_id=student_id,
            question=problem.get("question", ""),
            correct_answer=problem.get("correct_answer"),
            student_answer="skipped",
            is_correct=False,
            topic=problem.get("topic"),
            requested_topic=problem.get("requested_topic"),
            weakness="skipped"
        )
    gam = db.get_gamification_stats(student_id)
    return JSONResponse({
        "ok": True,
        "xp_total": gam["xp"],
        "level": gam["level"],
        "level_title": gam["level_title"],
        "streak": gam["streak"],
        "xp_for_next": gam["xp_for_next"],
        "xp_progress_pct": gam["xp_progress_pct"],
    })

async def api_gamification(request: Request):
    student_id = get_student_id(request)
    if not student_id:
        return JSONResponse({"xp": 0, "level": 1, "level_title": "Beginner", "streak": 0, "best_streak": 0, "xp_for_next": 6, "xp_progress_pct": 0})
    return JSONResponse(db.get_gamification_stats(student_id))

async def api_achievements(request: Request):
    student_id = get_student_id(request)
    if not student_id:
        return JSONResponse({"unlocked": [], "all": db.ACHIEVEMENTS})
    return JSONResponse({"unlocked": db.get_unlocked_achievements(student_id), "all": db.ACHIEVEMENTS})

async def api_stats(request: Request):
    student_id = get_student_id(request)
    if not student_id:
        return JSONResponse({"total": 0, "correct": 0, "pct": 0, "topics": {}})
    return JSONResponse(db.get_stats(student_id))

async def api_score_over_time(request: Request):
    student_id = get_student_id(request)
    if not student_id:
        return JSONResponse([])
    return JSONResponse(db.get_score_over_time(student_id))

async def api_history(request: Request):
    student_id = get_student_id(request)
    if not student_id:
        return JSONResponse([])
    return JSONResponse(db.get_history(student_id))

async def api_events(request: Request):
    session_id = get_session_id(request)
    if not session_id:
        return JSONResponse({"error": "Not logged in"}, status_code=401)

    q = Queue()
    sse_queues[session_id] = q

    async def event_generator():
        try:
            while True:
                try:
                    msg = q.get(block=False)
                    yield msg
                except Empty:
                    await asyncio.sleep(0.2)
                    yield {"event": "ping", "data": "{}"}
        except asyncio.CancelledError:
            sse_queues.pop(session_id, None)
            raise

    return EventSourceResponse(event_generator())

# === App ===
routes = [
    Route("/", homepage),
    Route("/api/students", api_students),
    Route("/api/student", api_student),
    Route("/api/login", api_login, methods=["POST"]),
    Route("/api/setup", api_setup, methods=["POST"]),
    Route("/api/logout", api_logout, methods=["POST"]),
    Route("/api/new-problem", api_new_problem, methods=["POST"]),
    Route("/api/submit-answer", api_submit_answer, methods=["POST"]),
    Route("/api/skip", api_skip, methods=["POST"]),
    Route("/api/scaffold-problem", api_scaffold_problem, methods=["POST"]),
    Route("/api/gamification", api_gamification),
    Route("/api/achievements", api_achievements),
    Route("/api/stats", api_stats),
    Route("/api/score-over-time", api_score_over_time),
    Route("/api/history", api_history),
    Route("/api/events", api_events),
]

app = Starlette(routes=routes)

if __name__ == "__main__":
    import uvicorn
    print("\n  Math Adventure Web App")
    print("  Open: http://localhost:8000\n")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
