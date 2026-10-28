#!/usr/bin/env bash
# Make a LuxTunes release (for the maintainer):
#
#   bash packaging/release.sh 0.8.5
#
# Does every step in the order the release build needs:
#   1. runs the tests (stops if any fail; nothing is changed yet);
#   2. sets __version__ in src/luxtunes/__init__.py and commits everything;
#   3. shows the e-mail on each commit about to be published, and stops if any
#      looks like a personal address (only GitHub noreply addresses or the
#      placeholder go out);
#   4. asks, then pushes the commits, and only then tags v<version> and pushes
#      the tag, which starts the build on GitHub.
set -euo pipefail

version="${1:-}"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Usage: bash packaging/release.sh 0.8.5"
    exit 1
fi
tag="v$version"
cd "$(dirname "$0")/.."

stop() { echo; echo "STOPPED: $*"; exit 1; }

[ "$(git branch --show-current)" = "main" ] || stop "switch to the main branch first (git switch main)."
if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
    stop "$tag already exists on this computer. If it's a mistake: git tag -d $tag"
fi
if git ls-remote --exit-code --tags origin "refs/tags/$tag" >/dev/null 2>&1; then
    stop "$tag already exists on GitHub. If it's a mistake: git push origin --delete $tag"
fi

echo "== 1. Tests"
python=python3
[ -x venv/bin/python ] && python=venv/bin/python
"$python" -m pytest -q || stop "the tests didn't pass, so nothing was changed."

echo "== 2. Version $version, committed"
sed -i -E "s/^__version__ = \"[^\"]*\"/__version__ = \"$version\"/" src/luxtunes/__init__.py
grep -q "^__version__ = \"$version\"" src/luxtunes/__init__.py || stop "couldn't set the version."
git add -A src tests docs packaging README.md
if git diff --cached --quiet; then
    echo "   (already committed)"
else
    git commit -q -m "LuxTunes $version"
fi
git log -1 --format='   %h %s'

echo "== 3. E-mail on the commits about to be published"
emails=$(git log origin/main..HEAD --format='%ae%n%ce' | sort -u)
echo "$emails" | sed 's/^/   /'
if echo "$emails" | grep '@' | grep -qv 'users\.noreply\.github\.com$'; then
    stop "a personal e-mail address would be published. Nothing was pushed.
Fix the commits' e-mail first (see 'Making a release.md'), then run this again."
fi

echo "== 4. Publish"
read -r -p "Push LuxTunes $version to GitHub and start the release build? [y/N] " answer
[[ "$answer" =~ ^[Yy] ]] || stop "you said no. The commit is made but not pushed; run 'git push' and tag later, or run this again."
git push origin main
git tag "$tag"
git push origin "$tag"
echo
echo "Done. The build is running: see the Actions tab on GitHub. When it finishes,"
echo "LuxTunes $version is on the Releases page. Then run: bash packaging/install.sh"
