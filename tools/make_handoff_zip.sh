#!/usr/bin/env bash
# Builds a compact, self-contained handoff ZIP so another Claude Code account can resume.
# Contents: full git bundle (all history), working-tree snapshot (no deps/builds),
# chat transcript(s) of this project, and a START-HERE prompt.
# Usage: tools/make_handoff_zip.sh [output_dir]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/handoff-out}"
STAMP="$(date -u +%Y%m%dT%H%MZ)"
WORK="$(mktemp -d)"; PKG="$WORK/TFSC-Unified-Handoff-$STAMP"; mkdir -p "$PKG/transcripts" "$OUT"
cd "$ROOT"
git bundle create "$PKG/repo.bundle" --all >/dev/null 2>&1
git ls-files -z --cached --others --exclude-standard | tar --null -T - -czf "$PKG/worktree-snapshot.tar.gz"
git log --oneline -n 200 > "$PKG/GIT-LOG.txt"
git status --short > "$PKG/UNCOMMITTED.txt" || true
cp docs/handoff/RESUME.md docs/handoff/SESSION-LOG.md "$PKG/" 2>/dev/null || true
# Chat history (Claude Code transcripts for this project directory)
for d in "$HOME/.claude/projects/"*tfsc-unified-platform*; do
  [ -d "$d" ] && find "$d" -maxdepth 1 -name '*.jsonl' -exec cp {} "$PKG/transcripts/" \;
done
cat > "$PKG/START-HERE.md" <<'MD'
# Continue the TFSC unified-platform work in a new Claude Code account

1. Restore the repo (either):
   - `git clone repo.bundle tfsc-unified-platform && cd tfsc-unified-platform && git checkout claude/vigilant-babbage-cg07la`
   - or push it to your own GitHub: `git remote add origin <url> && git push -u origin --all`
   - (`worktree-snapshot.tar.gz` contains uncommitted files too, if any — see UNCOMMITTED.txt)
2. Optional chat history: `transcripts/*.jsonl` are the raw Claude Code transcripts. To resume the
   very same conversation locally, copy a transcript into
   `~/.claude/projects/<your-project-dir-with-dashes>/` and run `claude --resume`.
   Otherwise `SESSION-LOG.md` is the human-readable summary.
3. Re-attach the private inputs that are deliberately not in git (see RESUME.md "Raw inputs").
4. Paste this prompt into Claude Code in the repo root:

   > Read docs/handoff/RESUME.md and docs/handoff/MASTER-COMMAND.md. Continue the work from the
   > first unchecked "Next actions" item, phase by phase, committing and pushing after each step,
   > and keep RESUME.md and SESSION-LOG.md updated.
MD
(cd "$WORK" && zip -qr9 "$OUT/TFSC-Unified-Handoff-$STAMP.zip" "$(basename "$PKG")")
rm -rf "$WORK"
echo "$OUT/TFSC-Unified-Handoff-$STAMP.zip"
