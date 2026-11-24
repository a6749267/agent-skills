## Summary

<!-- What does this PR change, and why? -->

## Type

- [ ] Bug fix
- [ ] New adapter
- [ ] New capability
- [ ] Docs / README
- [ ] Refactor

## Checklist

- [ ] `python -m compileall -q skills demo` — clean
- [ ] `python skills/tutorial-video-recorder/scripts/selfcheck.py` — all assertions pass
- [ ] `python skills/tutorial-video-post/scripts/scan_leak.py skills README.md README.zh-CN.md docs demo` — exit code 0
- [ ] `SKILL.md` frontmatter `name` matches the directory name
- [ ] No app-specific paths, cache keys, or field names hardcoded in the generic layer
- [ ] No real data, real domain vocabulary, or private identifiers in the diff
- [ ] New pitfall documented in `references/pitfalls.md` (if this fixes a failure mode)

## What I verified

<!-- How did you confirm it works? Paste the relevant output. -->
