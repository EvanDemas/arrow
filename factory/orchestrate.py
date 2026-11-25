import json, os, pathlib, subprocess, sys, time

SPEC = pathlib.Path(sys.argv[1]).read_text()
OUT = pathlib.Path(os.environ["OUT_DIR"]); OUT.mkdir(parents=True, exist_ok=True)
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "3"))
MAX_TURNS = os.environ.get("MAX_TURNS", "30")
TASK_BUDGET = float(os.environ.get("TASK_BUDGET_USD", "1.50"))
ATTEMPT_TIMEOUT = int(os.environ.get("TIMEOUT_S", "900"))
TOOLS = "Read,Edit,Write,Glob,Grep,Bash(python -m pytest:*),Bash(git status:*),Bash(git diff:*)"

RULES = """You are implementing a task in this repository (the Python library "arrow").
Rules:
- Change only what the spec requires. Respect its "Out of scope" section.
- Never modify anything under .github/ or factory/.
- Add or update tests for your change.
- Run `python -m pytest -q` and fix failures you caused.
- Do not commit. Leave changes in the working tree.
- Finish with a 3-5 sentence summary of what you did.
"""

def build_prompt(feedback):
    p = f"{RULES}\nSPEC:\n{SPEC}\n"
    if feedback:
        p += ("\nA previous attempt already modified the working tree, but verification failed. "
              "Fix the problem. Do not start over unless necessary.\n\n"
              "VERIFICATION OUTPUT (tail):\n" + feedback)
    return p

def run_agent(prompt, budget_left):
    cmd = ["claude", "-p", prompt, "--output-format", "json",
           "--max-turns", MAX_TURNS, "--max-budget-usd", f"{budget_left:.2f}",
           "--allowedTools", TOOLS]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=ATTEMPT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"timeout": True}
    try:
        r = json.loads(p.stdout)
    except Exception:
        r = {}
    r["_rc"] = p.returncode
    return r

def run_tests():
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                           capture_output=True, text=True, timeout=600, env=env)
        tail = "\n".join((p.stdout + p.stderr).strip().splitlines()[-60:])
        return p.returncode == 0, tail
    except subprocess.TimeoutExpired:
        return False, "pytest timed out after 600s"

def diff_stats():
    subprocess.run(["git", "add", "-N", "."], capture_output=True)
    out = subprocess.run(["git", "diff", "--numstat"], capture_output=True, text=True).stdout
    files = lines = 0
    for row in out.splitlines():
        a, d, _ = row.split("\t", 2)
        files += 1
        lines += (int(a) if a.isdigit() else 0) + (int(d) if d.isdigit() else 0)
    return files, lines

attempts, spent, feedback, status = [], 0.0, None, "failed"
t_start = time.time()
for n in range(1, MAX_ATTEMPTS + 1):
    left = TASK_BUDGET - spent
    if left < 0.02:
        status = "budget_exhausted"
        break
    t0 = time.time()
    r = run_agent(build_prompt(feedback), left)
    cost = float(r.get("total_cost_usd") or 0)
    spent += cost
    (OUT / f"attempt{n}_result.json").write_text(json.dumps(r, indent=2))
    ok, tail = run_tests()
    files, lines = diff_stats()
    if ok and files == 0:
        ok, tail = False, "No files were changed. Implement the spec."
    (OUT / f"attempt{n}_tests.txt").write_text(tail)
    attempts.append({"attempt": n, "cost_usd": round(cost, 4), "turns": r.get("num_turns"),
                     "seconds": round(time.time() - t0), "tests_passed": ok,
                     "files_changed": files, "lines_changed": lines})
    if ok:
        status = "passed"
        break
    feedback = tail
    if spent >= TASK_BUDGET:
        status = "budget_exhausted"
        break

metrics = {"issue": os.environ.get("ISSUE_NUM"), "status": status,
           "total_cost_usd": round(spent, 4), "seconds": round(time.time() - t_start),
           "attempts": attempts}
(OUT / "metrics.json").write_text(json.dumps(metrics, indent=2))

rows = "\n".join(f"| {a['attempt']} | ${a['cost_usd']} | {a['turns']} | {a['seconds']}s | "
                 f"{'pass' if a['tests_passed'] else 'fail'} | {a['files_changed']} files / {a['lines_changed']} lines |"
                 for a in attempts)
summary = (f"**Factory run: {status}**\n\nTotal cost: ${metrics['total_cost_usd']} "
           f"(task budget ${TASK_BUDGET:.2f}), {metrics['seconds']}s\n\n"
           f"| Attempt | Cost | Turns | Time | Tests | Diff |\n|---|---|---|---|---|---|\n{rows}\n")
(OUT / "summary.md").write_text(summary)
print(summary)

with open(os.environ["GITHUB_OUTPUT"], "a") as f:
    f.write(f"status={status}\n")