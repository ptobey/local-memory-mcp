# Contributing

Thanks for helping improve this project.

## Ground Rules
- Keep changes local-first and privacy-conscious.
- Prefer practical improvements over large redesigns.
- Do not add hidden network dependencies.
- Keep docs honest about current behavior and limitations.

## Dev Setup
```bash
python -m venv .venv
```

Windows:
```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS/Linux:
```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## Pull Requests
- Keep PRs focused and small enough to review.
- Include a short problem statement and why your approach is correct.
- Update docs/examples when behavior changes.
- If behavior is intentionally rough, document the limitation explicitly.

## Reporting Issues
Please include:

- environment details (OS, Python version),
- exact steps to reproduce,
- expected behavior vs actual behavior,
- relevant logs or tracebacks.

## Security
Do not open public issues for exposed secrets or sensitive data.  
Report privately to the maintainer.
