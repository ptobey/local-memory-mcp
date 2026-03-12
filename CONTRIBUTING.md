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

## Good First Contributions
- Documentation clarity improvements (setup, integrations, troubleshooting).
- Reproducible bug fixes with minimal behavioral surface area.
- Better error messages and config validation.
- Small developer-experience improvements that keep local-first defaults.

## Out Of Scope (For Now)
- Large architecture redesigns.
- New cloud/SaaS-style dependencies or hosted-control-plane assumptions.
- Broad UI/product overhauls not tied to current v1 goals.
- Major behavioral changes without prior issue discussion.

## Maintainer Bandwidth
- This is a small, early-stage project.
- Review/response may take up to 7 days depending on workload.
- PRs may be closed if they do not align with project scope or local-first/privacy constraints.

## Reporting Issues
Please include:

- environment details (OS, Python version),
- exact steps to reproduce,
- expected behavior vs actual behavior,
- relevant logs or tracebacks.

## Security
Do not open public issues for exposed secrets or sensitive data.  
Report privately using the process in [`SECURITY.md`](SECURITY.md).
