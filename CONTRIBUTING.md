# Contributing

Thanks for taking the time. This repo is small on purpose — two skills, one demo,
a pile of hard-won lessons in `references/pitfalls.md`.

## What would help most

**Adapters for other apps.** If you wired this pipeline up to your own app, the
adapter is the most valuable thing you can contribute back. It lives in
`skills/tutorial-video-recorder/scripts/adapters/<your_app>/` and needs two methods:

```python
class Adapter:
    def route(self, url):
        """Return (status, body) to replace this response; None = pass through."""

    def fix_summary(self, url, payload, scfg):
        """Recompute header totals from the same data the table uses."""
```

The contract is in
[`skills/tutorial-video-recorder/references/adapter-guide.md`](skills/tutorial-video-recorder/references/adapter-guide.md).

## Before you open a PR

```bash
# 1. syntax
python -m compileall -q skills demo

# 2. pipeline self-check (13 assertions)
python skills/tutorial-video-recorder/scripts/selfcheck.py

# 3. leak scan — exit code 0 required
python skills/tutorial-video-post/scripts/scan_leak.py skills README.md README.zh-CN.md docs demo

# 4. frontmatter — name must match the directory name
python - <<'PY'
import pathlib, re, sys
for p in pathlib.Path("skills").glob("*/SKILL.md"):
    fm = p.read_text(encoding="utf-8").split("---", 2)[1]
    name = re.search(r"^name:\s*(.+)$", fm, re.M).group(1).strip()
    assert name == p.parent.name, f"{p}: {name} != {p.parent.name}"
print("frontmatter OK")
PY
```

CI runs all four on every push. It must be green before merge.

## House rules

These are not style preferences — they exist because breaking them produces
**silent** failures:

1. **Never hardcode app-specific paths, cache keys, or field names in the generic
   layer.** They belong in `project.json` or `assets/domains/*.json`. A hardcoded
   path silently stops intercepting and real data lands on screen.
2. **Never hardcode anyone's private identity into a script.** Private patterns go
   in `assets/leak_patterns.json`, which is gitignored when customized.
3. **Summary fields must be recomputed from the same source the table renders.**
   Computing the total with a second, independent price source is how you get
   "header total ≠ sum of line items" — which looks like plausible data and fools
   everyone, including you.
4. **New pitfalls get an entry in `references/pitfalls.md`,** indexed by symptom.
   That file is the actual value of this repo; the code is just the implementation.

## Commit messages

Short and specific — what broke and why the fix works:

```
fix(adapter): recompute header total from order unit_price

The summary field was computed from quote_map prices while the table used
each order's own unit_price. The two differ, so the header total drifted
from the sum of line items by ~20x.
```

## License

By contributing you agree that your contribution is licensed under the MIT License.
