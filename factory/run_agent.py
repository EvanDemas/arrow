import json, os, pathlib, subprocess, sys, time

spec = pathlib.Path(sys.argv[1]).read_text()
out = pathlib.Path(os.environ["OUT_DIR"]); out.mkdir(parents=True, exist_ok=True)
max_turns = os.environ.get("MAX_TURNS", "30")
max_usd = os.environ.get("MAX_USD", "2.00")
timeout_s = int(os.environ.get("TIMEOUT_S", "1200"))

prompt = f"""You are implementing a task in this repository (the Python library "arrow").
Rules:
- Change only what the spec requires. Respect its "Out of scope" section.
- Never modify anything under .github/ or factory/.
- Add or update tests for your change.
- Run `python -m pytest -q` and fix failures you caused.
- Do not commit. Leave changes in the working tree.
- Finish with a 3-5 sentence summary of what you did.

SPEC:
{spec}
"""

cmd = ["claude", "-p", prompt, "--output-format", "json",
       "--max-turns", max_turns, "--max-budget-usd", max_usd,
       "--allowedTools",
       "Read,Edit,Write,Glob,Grep,Bash(python -m pytest:*),Bash(git status:*),Bash(git diff:*)"]

start = time.time()
try:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    stdout, rc = p.stdout, p.returncode
    (out / "agent_stderr.txt").write_text(p.stderr)
except subprocess.TimeoutExpired:
    stdout, rc = "", -1
elapsed = round(time.time() - start)
(out / "agent_result.json").write_text(stdout)

try:
    r = json.loads(stdout)
except Exception:
    r = {}
summary = (
    f"**Agent run**\n\n- exit code: {rc}{' (timeout)' if rc == -1 else ''}\n"
    f"- cost: ${r.get('total_cost_usd', 'unknown')}\n"
    f"- turns: {r.get('num_turns', 'unknown')}\n"
    f"- wall-clock: {elapsed}s\n\n"
    f"**Agent's summary**\n\n{r.get('result', '(none)')}\n"
)
(out / "summary.md").write_text(summary)
print(summary)