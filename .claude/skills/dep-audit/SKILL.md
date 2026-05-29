---
name: dep-audit
description: Audit project dependencies for known CVEs and supply-chain red flags. Runs pip-audit on the Python deps and npm audit on the frontend, and when reviewing a dependency bump, diffs the lockfile for newly added packages and new install/postinstall lifecycle scripts (the Shai-Hulud / chalk-debug attack vector). Use before merging a dependency bump or on demand to check the current tree.
disable-model-invocation: true
---

# dep-audit

Fills the gap left by CI: Trivy scans the built *image* and dependency-review gates *PRs*, but
nothing audits the resolved dependency tree directly, and nothing inspects lifecycle scripts — the
exact vector behind the recent npm supply-chain attacks (chalk/debug Sept 2025, the Shai-Hulud
worm). This skill is local, read-only, and adds no registry dependency to run.

## What to do

### 1. Known-CVE scan

```bash
# Python — pip-audit reads the active environment / requirements
.venv/bin/pip-audit -r requirements.txt -r requirements-dev.txt 2>/dev/null \
  || .venv/bin/python -m pip install --quiet pip-audit && .venv/bin/pip-audit -r requirements.txt
# Frontend — uses the committed lockfile, no install needed
npm audit --prefix frontend --omit=dev
```
Report each advisory: package, installed version, severity, fixed-in version. Recommend the bump.

### 2. Supply-chain red flags (the part CI doesn't do)

When the diff touches `frontend/package-lock.json`, `frontend/package.json`, `requirements.txt`, or
`requirements-dev.txt`:

- **New packages**: `git diff main...HEAD -- frontend/package-lock.json` — list every newly added
  package name + version. For each, sanity-check: is it a typosquat of a popular package? Brand-new
  with near-zero downloads? Maintainer recently changed?
- **New lifecycle scripts**: grep the lockfile diff (and, for direct deps, the package's own
  manifest) for newly introduced `"postinstall"`, `"preinstall"`, `"install"` scripts. These run
  arbitrary code at install time and are the primary worm vector. Flag every one and require a human
  to eyeball what it runs.
- **Unpinned / floating ranges**: flag direct deps specified as `^`/`~`/`latest`/`*` in
  `package.json` where an exact pin would be safer for a security-sensitive dep.
- **Integrity**: confirm `package-lock.json` entries carry `integrity` hashes (npm verifies these on
  install — their absence is a red flag).

### 3. Output

```
## dep-audit

### CVEs
  [HIGH] <pkg> 1.2.3 → fixed in 1.2.4  (npm/PyPI advisory id)
  (none) ✅

### Supply-chain review (this diff)
  new packages: <pkg>@x.y.z (downloads? maintainer?) …
  ⚠️ new postinstall script in <pkg> — runs: <command>  ← human must vet
  ⚠️ floating range: "<pkg>": "^x" in package.json — consider exact pin
verdict: <safe / vet-before-merge / has-fixable-CVEs>
```

Read-only — report and recommend; do not edit lockfiles or run `npm install`/`pip install` of new
packages yourself (a human pins deliberately, per the project's supply-chain stance).
