# Global AGENTS.md

## Language and communication

- Respond in Japanese unless the task, repository, or user explicitly requires English.
- Be concise, but do not omit important technical reasoning.
- For implementation tasks, explain the intended change before editing.
- For non-trivial tasks, inspect the relevant files before proposing a solution.
- If assumptions are necessary, state them clearly.

## Work style

- Prefer small, reviewable changes.
- Do not rewrite large areas unless the user explicitly asks.
- Keep changes focused on the requested task.
- Do not introduce new production dependencies without explaining why.
- Do not change formatting across unrelated files.
- Do not hide failing tests or warnings.

## Safety rules

- Never expose or commit secrets, API keys, tokens, passwords, cookies, private keys, or `.env` values.
- Do not run destructive commands without explicit confirmation.
- Destructive commands include:
  - deleting many files
  - database reset/drop/truncate
  - force push
  - changing global Git config
  - modifying system directories
- Do not send code, files, or private repository content to external services unless explicitly requested.

## Implementation rules

- Read existing conventions before adding new patterns.
- Prefer existing libraries, utilities, and architecture.
- Keep business logic out of thin route/controller layers when the project already has services.
- Add or update tests when behavior changes.
- Prefer clear names over clever abstractions.
- Avoid premature generalization.

## Verification

After code changes, run the smallest relevant checks first.

For Python projects, try:

```bash
pytest
ruff check .
mypy .
```