---

name: safe-implementation

description: Use for implementation tasks that modify code. Inspect first, plan, make small edits, run relevant checks, review the diff, and summarize risks.

---



# Safe Implementation Skill



Use this skill when modifying code.



## Goal



Make focused, safe, reviewable code changes without unnecessary rewrites.



## Workflow



1. Inspect the repository structure and relevant files.

2. Identify existing conventions before editing.

3. Briefly explain the intended change.

4. Make the smallest coherent change.

5. Run the most relevant checks.

6. Review the diff.

7. Summarize changed files, verification, and remaining risks.



## Rules



- Do not introduce new production dependencies unless necessary.

- Do not rewrite unrelated code.

- Do not change public behavior accidentally.

- Do not remove tests unless explicitly requested.

- Prefer existing architecture, naming, and formatting.

- If tests fail, report the failure honestly and identify likely causes.

- If a command is unavailable, say so clearly and suggest the command the user should run.



## Verification guidance



For Python projects, look for:



- `pyproject.toml`

- `requirements.txt`

- `pytest.ini`

- `tox.ini`



Then prefer:



```bash

pytest

ruff check .

mypy .

