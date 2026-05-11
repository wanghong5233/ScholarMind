Review the current git diff (staged + unstaged) for this project.

Check for these issues in order of severity:

## Critical (must fix)
- Hardcoded secrets, API keys, or credentials
- Bare `except` / `except Exception` that swallow errors silently
- SQL injection (string concatenation in queries)
- Backend/frontend contract drift between Pydantic schemas and TypeScript types
- Ungrounded RAG/DeepResearch claims without citations, retrieved chunks, web evidence, or tool traces
- Silent fallbacks that hide ingestion, indexing, provider auth, LLM, parsing, or streaming failures
- Accidental production dependency on `DeepResearch_ReferenceProjects/`

## Important
- Missing type hints on function signatures
- Functions exceeding 40 lines
- Comments that just restate the code
- Ghost Layers (wrapper classes that only delegate)
- New dependencies not in `pyproject.toml` / `requirements*.txt`
- If a new dependency was added, verify it is a real PyPI package and not a hallucinated name
- Prompts that ask for free-form prose when the caller expects structured JSON
- DeepResearch report fields that do not map to evidence blocks, citations, or quality gates
- Doc Studio edits that bypass workspace safety checks or user confirmation for risky changes
- Public/demo deployment changes that expose admin, debug, internal, or secret-bearing endpoints

## Style
- Inconsistent naming (should be snake_case for functions, PascalCase for classes)
- Missing docstrings on public functions
- Print statements instead of logging
- Chinese user-facing copy mixed into code identifiers

For each issue found, report: severity, file:line, what's wrong, and a concrete fix.
