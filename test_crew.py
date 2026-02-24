from crewai import Agent, Task, Crew, LLM

# Local Ollama gemma3:4b model
llm = LLM(
    model="ollama/gemma3:4b",
    base_url="http://localhost:11434",
)

# === Student learning history (simulation data) ===
student_history = """
[Student Info]
- Name: Hyunji
- Age: 10 years old (4th grade)

[Recent History - Last 10 Problems]
1. Addition (2-digit+2-digit): 45+37=82 ✅ Correct
2. Addition (3-digit+3-digit): 234+567=801 ✅ Correct
3. Subtraction (with borrowing): 52-28=24 ✅ Correct
4. Multiplication (1-digit×1-digit): 7×8=54 ❌ Wrong (answer: 56)
5. Multiplication (1-digit×1-digit): 6×9=54 ✅ Correct
6. Multiplication (2-digit×1-digit): 23×4=82 ❌ Wrong (answer: 92)
7. Multiplication (2-digit×1-digit): 15×3=45 ✅ Correct
8. Division (basic): 20÷4=5 ✅ Correct
9. Division (basic): 35÷7=6 ❌ Wrong (answer: 5)
10. Mixed (addition+multiplication): 3×5+10=20 ❌ Wrong (answer: 25)

[Accuracy by Area]
- Addition: 100% (2/2)
- Subtraction: 100% (1/1)
- Multiplication: 50% (2/4)
- Division: 50% (1/2)
- Mixed operations: 0% (0/1)

[Error Pattern Notes]
- Mistakes in 7× and 8× multiplication tables
- Errors in carrying over with 2-digit × 1-digit
- Confusion with reverse multiplication in division
- Does not know order of operations (multiply before add)
"""

# === Agent Definitions ===

# 1. Learning Manager: Analyzes history and directs next steps
learning_manager = Agent(
    role="Learning Manager",
    goal="Analyze the student's correct/wrong answer history, identify weak areas, "
         "and give specific instructions on what topic, difficulty, and type of problem to create next",
    backstory="You are an elementary math education specialist with 15 years of experience. "
              "You diagnose exactly where a child is struggling based on their learning data, "
              "and design the appropriate next step - not too hard, not too easy.",
    llm=llm,
    verbose=True,
)

# 2. Problem Creator: Creates problems based on manager's instructions
problem_creator = Agent(
    role="Problem Creator",
    goal="Create age-appropriate math problems for a 10-year-old based on the Learning Manager's instructions",
    backstory="You are an expert at creating elementary math problems. "
              "You make story-based problems using real-life situations that kids find fun and relatable, "
              "matching exactly the difficulty level you are instructed to use.",
    llm=llm,
    verbose=True,
)

# 3. Solution Helper: Explains step-by-step
solution_helper = Agent(
    role="Solution Helper",
    goal="Explain the solution to each problem step-by-step at a 10-year-old's level",
    backstory="You are a master at making kids say 'Aha! I get it!' "
              "You use simple words, analogies, and visuals to explain concepts. "
              "You pay special attention to the areas where the student often makes mistakes.",
    llm=llm,
    verbose=True,
)

# 4. Cheerleader: Motivation and encouragement
cheerleader = Agent(
    role="Cheerleader",
    goal="Encourage the student to feel confident about math and give fun real-life examples",
    backstory="You are an expert at boosting kids' learning motivation. "
              "You praise specific achievements, reassure them that mistakes are okay, "
              "and show how math is useful in everyday life in a fun way.",
    llm=llm,
    verbose=True,
)

# === Task Definitions (sequential) ===

# Task 1: Analyze learning history and give instructions
analyze_task = Task(
    description=f"Here is student Hyunji's learning history:\n\n{student_history}\n\n"
                "Analyze this history and:\n"
                "1. Identify Hyunji's weakest areas\n"
                "2. Give specific instructions for the next problems: topic, difficulty, and type\n"
                "3. Explain why these problems are appropriate for her right now",
    expected_output="Weakness analysis + specific problem creation instructions (topic, difficulty, type)",
    agent=learning_manager,
)

# Task 2: Create problems
create_task = Task(
    description="Based on the Learning Manager's analysis and instructions, "
                "create 2 math problems suitable for Hyunji (10 years old).\n"
                "- Use story-based problems with real-life situations\n"
                "- Use kid-friendly themes (snacks, toys, animals, etc.)\n"
                "- Include the correct answer for each problem",
    expected_output="2 story-based math problems with correct answers",
    agent=problem_creator,
)

# Task 3: Step-by-step solution
solve_task = Task(
    description="For each of the 2 problems created, write a step-by-step solution.\n"
                "- Pay special attention to Hyunji's common mistakes (carrying, 7×/8× tables, order of operations)\n"
                "- Use simple language a 10-year-old can understand\n"
                "- Explain WHY each step is done",
    expected_output="Step-by-step solutions for each problem (simple explanation + watch-out tips)",
    agent=solution_helper,
)

# Task 4: Encouragement message
cheer_task = Task(
    description="Based on Hyunji's learning history and today's problems/solutions:\n"
                "1. Praise specific things Hyunji is doing well\n"
                "2. Encourage her that she'll get better at the weak areas soon\n"
                "3. Give a fun real-life example of how today's math is used\n"
                "- Write in a bright, friendly tone",
    expected_output="Praise + encouragement + real-life example message",
    agent=cheerleader,
)

# === Crew Setup and Execution ===
crew = Crew(
    agents=[learning_manager, problem_creator, solution_helper, cheerleader],
    tasks=[analyze_task, create_task, solve_task, cheer_task],
    verbose=True,
)

print("=" * 50)
print("  Hyunji's Math Helper - CrewAI Test")
print("=" * 50)
print()
result = crew.kickoff()
print()
print("=" * 50)
print("  FINAL RESULT")
print("=" * 50)
print(result)
