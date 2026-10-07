#!/usr/bin/env bash
# Publish the Community Edition: the current commit minus everything in .publicignore, as ONE
# new commit on the public repository (its history is separate; internal commits stay private).
#
#   tools/release/publish-public.sh "Release 0.2.0: <summary>"      # build, verify, push
#   tools/release/publish-public.sh --dry-run                       # build and verify only
#
# Verification before pushing: no paid code or references remain, the Python workspace
# resolves without the Enterprise package, lint + types + unit tests pass, and the web UI
# typechecks, tests and builds without src/ee.
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT=$PWD
PUBLIC=${TORQRUN_PUBLIC_REPO:-https://github.com/anshulsharma7/torqrun.git}
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
msg=${1:?usage: publish-public.sh "<commit message>" | --dry-run}
[ -z "$(git status --porcelain)" ] || { echo "commit your changes first" >&2; exit 1; }

work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
tree="$work/tree"; mkdir "$tree"
git archive HEAD | tar -x -C "$tree"
while read -r path; do
  case "$path" in ''|'#'*) continue ;; esac
  rm -rf "${tree:?}/$path"
done < .publicignore

# Workspace without the Enterprise package.
sed -i '/torqrun-ee/d' "$tree/pyproject.toml"
(cd "$tree" && uv lock --quiet)

echo "== verifying the Community tree"
if grep -rIl --exclude-dir=.git "torqrun-ee\|torqrun_ee" "$tree" | grep -v -e 'apps/api/src/torqrun_api/edition.py' -e 'apps/scheduler/src/torqrun_scheduler/main.py' -e 'deploy/docker/python.Dockerfile' -e 'tools/release/publish-public.sh'; then
  echo "unexpected Enterprise references above" >&2; exit 1
fi
for p in packages/torqrun-ee tests/ee apps/web/src/ee apps/web/e2e/ee; do
  [ ! -e "$tree/$p" ] || { echo "$p must not be published" >&2; exit 1; }
done
(cd "$tree" && uv sync --quiet --frozen && uv run ruff check . -q && uv run ruff format --check . -q \
  && uv run mypy | tail -1 && uv run pytest -q -m "not integration" -p no:cacheprovider | tail -1)
# The web check runs on a scratch copy (the container writes as root); the tree stays clean.
cp -a "$tree/apps/web" "$work/webcheck"
docker run --rm -e CI=true -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 -v "$work/webcheck:/app" -w /app node:22-alpine \
  sh -c "corepack enable >/dev/null 2>&1 && pnpm install --frozen-lockfile --silent && pnpm typecheck && pnpm test --silent && pnpm build >/dev/null && echo 'web: ok'; status=\$?; rm -rf /app/* /app/.[!.]*; exit \$status"
rm -rf "$tree/.venv"
find "$tree" -name __pycache__ -type d -prune -exec rm -rf {} +
rm -rf "$tree/.mypy_cache" "$tree/.ruff_cache" "$tree/.pytest_cache" "$tree/apps/web/tsconfig.tsbuildinfo"

if [ "$DRY" = 1 ]; then
  echo "dry run OK: $(find "$tree" -type f | wc -l) files would be published"; exit 0
fi
git clone --quiet "$PUBLIC" "$work/public"
find "$work/public" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a "$tree/." "$work/public/"
cd "$work/public"
git config user.name "$(git -C "$ROOT" config user.name)"
git config user.email "$(git -C "$ROOT" config user.email)"
git add -A
if git diff --cached --quiet; then echo "public repo is already up to date"; exit 0; fi
git commit --quiet -F - <<MSG
$msg

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UkNHBU3uxHXUq2CsR1TTQx
MSG
git push --quiet origin HEAD:main
echo "published: $(git log --oneline -1)"
