import json
import os
import re
import sys
import time
import logging
import warnings
import threading

# Suppress litellm noisy error logs
logging.getLogger("LiteLLM").setLevel(logging.CRITICAL)
logging.getLogger("litellm").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")

from dotenv import load_dotenv
load_dotenv()

from crewai import Agent, Task, Crew, LLM

def _parse_json_lenient(raw):
    """Parse JSON that may contain invalid escapes like LaTeX \\frac{}{} or \\(."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    sanitized = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw)
    return json.loads(sanitized)

def _clean_latex(text):
    """Strip LaTeX notation from text, converting to plain readable math."""
    if not text or not isinstance(text, str):
        return text
    text = re.sub(r'\\frac\{([^}]*)\}\{([^}]*)\}', r'\1/\2', text)
    text = re.sub(r'\frac\{([^}]*)\}\{([^}]*)\}', r'\1/\2', text)
    text = re.sub(r'\\[(\[]', '', text)
    text = re.sub(r'\\[)\]]', '', text)
    text = text.replace('\\times', 'x').replace('\\div', '/').replace('\\cdot', '*')
    text = re.sub(r'\\text\{([^}]*)\}', r'\1', text)
    text = text.replace('\f', '')
    return text.strip()

# === Hybrid LLM Setup ===
# Gemini API for problem generation (accuracy matters)
gemini_llm = LLM(
    model="gemini/gemini-2.5-flash",
    api_key=os.getenv("GEMINI_API_KEY"),
)

# Local Ollama for feedback (speed, no API cost)
local_llm = LLM(
    model="ollama/gemma3:4b",
    base_url="http://localhost:11434",
)

# === Visual Pipeline Display ===
AGENTS = {
    "manager":  {"icon": "🧠", "name": "Learning Manager ", "desc": "Analyzing student history", "llm_tag": "Gemini"},
    "creator":  {"icon": "📐", "name": "Problem Creator  ", "desc": "Creating a fun problem",   "llm_tag": "Gemini"},
    "helper":   {"icon": "💡", "name": "Solution Helper  ", "desc": "Explaining step-by-step",  "llm_tag": "Local"},
}

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
RED = "\033[91m"
MAGENTA = "\033[95m"
BLUE = "\033[94m"

class PipelineDisplay:
    def __init__(self, agents_to_show):
        self.agents = agents_to_show
        self.status = {a: "waiting" for a in agents_to_show}
        self.results = {a: "" for a in agents_to_show}
        self.spinner_idx = 0
        self.spinners = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._stop = False
        self._thread = None
        self._drawn_lines = 0

    def _clear_display(self):
        if self._drawn_lines > 0:
            sys.stdout.write(f"\033[{self._drawn_lines}A")
            for _ in range(self._drawn_lines):
                sys.stdout.write("\033[2K\n")
            sys.stdout.write(f"\033[{self._drawn_lines}A")
            sys.stdout.flush()

    def _draw(self):
        self._clear_display()
        lines = []
        lines.append(f"  {CYAN}┌{'─' * 56}┐{RESET}")
        lines.append(f"  {CYAN}│{RESET} {BOLD}Agent Pipeline{RESET}    {DIM}(Gemini=cloud  Local=ollama){RESET}    {CYAN}│{RESET}")
        lines.append(f"  {CYAN}├{'─' * 56}┤{RESET}")

        for key in self.agents:
            agent = AGENTS[key]
            st = self.status[key]
            llm_tag = agent["llm_tag"]
            tag_color = MAGENTA if llm_tag == "Gemini" else BLUE
            tag = f"{tag_color}[{llm_tag}]{RESET}"

            if st == "working":
                spinner = self.spinners[self.spinner_idx % len(self.spinners)]
                status_text = f"{YELLOW}{spinner} {agent['desc']}...{RESET}"
            elif st == "done":
                preview = self.results.get(key, "")
                if preview:
                    preview = preview[:30].replace('\n', ' ').strip()
                    status_text = f"{GREEN}✅ Done{RESET} {DIM}→ {preview}...{RESET}"
                else:
                    status_text = f"{GREEN}✅ Done{RESET}"
            else:
                status_text = f"{DIM}⏳ Waiting{RESET}"

            lines.append(f"  {CYAN}│{RESET}  {agent['icon']} {BOLD}{agent['name']}{RESET} {tag} {status_text}")

        lines.append(f"  {CYAN}└{'─' * 56}┘{RESET}")

        output = "\n".join(lines) + "\n"
        sys.stdout.write(output)
        sys.stdout.flush()
        self._drawn_lines = len(lines)

    def _animate(self):
        while not self._stop:
            self._draw()
            self.spinner_idx += 1
            time.sleep(0.15)

    def start(self):
        self._stop = False
        self._drawn_lines = 0
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def set_status(self, agent_key, status, result=""):
        self.status[agent_key] = status
        if result:
            self.results[agent_key] = result

    def stop(self):
        self._stop = True
        if self._thread:
            self._thread.join(timeout=1)
        self._draw()


# === Student progress tracking ===
history = []

def history_summary():
    if not history:
        return "No history yet. This is the first problem."
    lines = []
    correct = sum(1 for h in history if h["correct"])
    total = len(history)
    lines.append(f"Total: {correct}/{total} correct ({int(correct/total*100)}%)")
    for h in history:
        mark = "✅" if h["correct"] else "❌"
        lines.append(f"  {mark} Q: {h['question']} | Student answered: {h['student_answer']} | Correct: {h['correct_answer']}")
        if not h["correct"] and h.get("weakness"):
            lines.append(f"     Weakness: {h['weakness']}")
    return "\n".join(lines)

# === Agents (Hybrid: Gemini for accuracy, Local for speed) ===
manager = Agent(
    role="Learning Manager",
    goal="Analyze the student's history and decide what kind of problem to give next. "
         "Give SPECIFIC instructions: topic, difficulty, and what to focus on.",
    backstory="You are an expert elementary math tutor. You look at what the student "
              "got right and wrong, and pick the perfect next challenge - not too easy, not too hard.",
    llm=gemini_llm,  # Gemini: needs accuracy for analysis
    verbose=False,
)

creator = Agent(
    role="Problem Creator",
    goal="Create exactly ONE math word problem for a 10-year-old based on the manager's instructions. "
         "You MUST output valid JSON with keys: question, correct_answer (number only), hint. "
         "DOUBLE CHECK that correct_answer is mathematically correct!",
    backstory="You create fun, story-based math problems using everyday situations kids love "
              "(snacks, pets, toys, sports). You always match the requested difficulty exactly. "
              "You always verify your math is correct before giving the answer.",
    llm=gemini_llm,  # Gemini: needs accuracy for correct answers
    verbose=False,
)

helper = Agent(
    role="Solution Helper",
    goal="Explain the solution step-by-step in a way a 10-year-old can understand. "
         "Be warm and encouraging. If the student got it wrong, gently show where the mistake was.",
    backstory="You're amazing at making kids understand math. You use simple words, "
              "fun comparisons, and always make the student feel good about trying.",
    llm=local_llm,  # Local: fast feedback, personality matters more than precision
    verbose=False,
)

def run_agent_task(agent, description, expected_output):
    task = Task(description=description, expected_output=expected_output, agent=agent)
    crew = Crew(agents=[agent], tasks=[task], verbose=False)
    return str(crew.kickoff())

def get_problem(round_num):
    h = history_summary()
    display = PipelineDisplay(["manager", "creator"])
    display.start()

    # Step 1: Learning Manager analyzes (Gemini)
    display.set_status("manager", "working")
    analysis = run_agent_task(
        manager,
        f"Round {round_num}. Student is Hyunji, 10 years old, 4th grade.\n\n"
        f"Learning history:\n{h}\n\n"
        "Analyze and give specific instructions for the next problem. "
        "State the topic, difficulty level, and what skill to target.",
        "Specific instructions for next problem (topic, difficulty, focus area)"
    )
    display.set_status("manager", "done", analysis)

    # Step 2: Problem Creator makes a problem (Gemini)
    display.set_status("creator", "working")
    result = run_agent_task(
        creator,
        f"The Learning Manager says:\n{analysis}\n\n"
        "Based on these instructions, create exactly ONE math word problem.\n\n"
        "IMPORTANT: Your final answer must be ONLY valid JSON in this exact format:\n"
        '{"question": "the word problem text", "correct_answer": 42, "hint": "a helpful hint"}\n\n'
        "Rules:\n"
        "- correct_answer must be a single number (integer)\n"
        "- VERIFY your math is correct!\n"
        "- question should be a fun story problem for a 10-year-old\n"
        "- hint should help without giving away the answer\n"
        "- Output ONLY the JSON, nothing else",
        '{"question": "...", "correct_answer": number, "hint": "..."}'
    )
    display.set_status("creator", "done", result)
    display.stop()
    print()

    # Parse JSON
    problem = None
    try:
        start = result.find('{')
        end = result.rfind('}')
        if start != -1 and end > start:
            problem = _parse_json_lenient(result[start:end+1])
    except (json.JSONDecodeError, ValueError):
        pass

    if not problem:
        problem = {"question": result.strip(), "correct_answer": None, "hint": "Try your best!"}

    problem["question"] = _clean_latex(problem.get("question", ""))
    problem["hint"] = _clean_latex(problem.get("hint", ""))
    return problem, analysis

def get_feedback(question, correct_answer, student_answer, is_correct):
    status = "CORRECT! ✅" if is_correct else f"WRONG ❌ (correct answer was {correct_answer})"

    display = PipelineDisplay(["helper"])
    display.start()
    display.set_status("helper", "working")

    feedback = run_agent_task(
        helper,
        f"The student (Hyunji, 10 years old) just answered a math problem.\n\n"
        f"Problem: {question}\n"
        f"Correct answer: {correct_answer}\n"
        f"Student's answer: {student_answer}\n"
        f"Result: {status}\n\n"
        f"Give a SHORT response (3-5 sentences):\n"
        f"- If correct: praise her and explain briefly why the answer is right\n"
        f"- If wrong: be gentle, show the correct steps simply, encourage her to try again\n"
        f"Keep it warm, fun, and at a 10-year-old's level.",
        "Short, encouraging feedback (3-5 sentences)"
    )

    display.set_status("helper", "done", feedback)
    display.stop()
    print()
    return feedback


# === Score Display ===
def show_score():
    if not history:
        print(f"  {DIM}No problems answered yet.{RESET}")
        return
    correct = sum(1 for h in history if h["correct"])
    total = len(history)
    pct = int(correct / total * 100)
    bar_len = 20
    filled = int(bar_len * correct / total)
    bar = "█" * filled + "░" * (bar_len - filled)
    color = GREEN if pct >= 70 else YELLOW if pct >= 40 else RED
    print(f"  {BOLD}📊 Score: {color}{correct}/{total}{RESET} ({color}{pct}%{RESET})")
    print(f"  {BOLD}   [{color}{bar}{RESET}{BOLD}]{RESET}")
    print()


# === Main Interactive Loop ===
def main():
    print()
    print(f"  {CYAN}╔══════════════════════════════════════════════════╗{RESET}")
    print(f"  {CYAN}║{RESET}    {BOLD}🧮  Hyunji's Math Adventure!  🧮{RESET}             {CYAN}║{RESET}")
    print(f"  {CYAN}║{RESET}                                                  {CYAN}║{RESET}")
    print(f"  {CYAN}║{RESET}  {DIM}Powered by: Gemini (brain) + Ollama (heart){RESET}   {CYAN}║{RESET}")
    print(f"  {CYAN}║{RESET}                                                  {CYAN}║{RESET}")
    print(f"  {CYAN}║{RESET}  I'll give you fun math problems!                {CYAN}║{RESET}")
    print(f"  {CYAN}║{RESET}  Type your answer, or:                           {CYAN}║{RESET}")
    print(f"  {CYAN}║{RESET}    {YELLOW}'hint'{RESET}  = get a hint                          {CYAN}║{RESET}")
    print(f"  {CYAN}║{RESET}    {YELLOW}'skip'{RESET}  = skip this problem                   {CYAN}║{RESET}")
    print(f"  {CYAN}║{RESET}    {YELLOW}'score'{RESET} = see your score                      {CYAN}║{RESET}")
    print(f"  {CYAN}║{RESET}    {YELLOW}'quit'{RESET}  = finish                              {CYAN}║{RESET}")
    print(f"  {CYAN}╚══════════════════════════════════════════════════╝{RESET}")
    print()

    round_num = 0

    while True:
        round_num += 1
        print(f"  {BOLD}━━━ Round {round_num} ━━━{RESET}")
        print()

        problem, analysis = get_problem(round_num)
        question = problem.get("question", "")
        correct_answer = problem.get("correct_answer")
        hint = problem.get("hint", "Think step by step!")

        print(f"  {BOLD}📝 Problem:{RESET}")
        print(f"  {question}")
        print()

        # Answer loop
        while True:
            answer = input(f"  {BOLD}👉 Your answer:{RESET} ").strip().lower()

            if answer == "quit":
                print()
                show_score()
                print(f"  🌟 Great job today! See you next time! 🎉")
                print()
                return

            if answer == "hint":
                print(f"  💡 {YELLOW}Hint: {hint}{RESET}")
                print()
                continue

            if answer == "score":
                show_score()
                continue

            if answer == "skip":
                print(f"  ⏭️  The answer was: {BOLD}{correct_answer}{RESET}")
                history.append({
                    "question": question,
                    "student_answer": "skipped",
                    "correct_answer": correct_answer,
                    "correct": False,
                    "weakness": "skipped"
                })
                print()
                break

            try:
                student_num = int(answer)
            except ValueError:
                try:
                    student_num = float(answer)
                except ValueError:
                    print(f"  🔢 Please type a number! (or 'hint', 'skip', 'quit')")
                    print()
                    continue

            if correct_answer is not None:
                is_correct = (student_num == correct_answer)
            else:
                is_correct = False

            history.append({
                "question": question,
                "student_answer": student_num,
                "correct_answer": correct_answer,
                "correct": is_correct,
                "weakness": None if is_correct else "needs review"
            })

            # Get AI feedback (Local model - fast)
            print()
            feedback = get_feedback(question, correct_answer, student_num, is_correct)

            if is_correct:
                print(f"  {GREEN}✅ Correct! The answer is {correct_answer}!{RESET}")
            else:
                print(f"  {RED}❌ Not quite... The answer was {correct_answer}{RESET}")
            print()
            print(f"  {feedback}")
            print()
            break

        # Periodic score check
        if round_num % 3 == 0:
            show_score()
            cont = input(f"  {BOLD}🎮 Want to keep going? (yes/no):{RESET} ").strip().lower()
            if cont in ("no", "n", "quit", "q"):
                print()
                show_score()
                print(f"  🌟 Amazing effort! See you next time! 🎉")
                print()
                return
            print()

if __name__ == "__main__":
    main()
