#!/usr/bin/env bash
# Build the manuscript and the supplementary material with tectonic, outside the repository.
#
#   paper/build.sh                                  draft without results
#   paper/build.sh --results bench/results          draft with the results of the bench analysis
#   paper/build.sh --submission --results bench/results
#
# --results DIR      the result folder of `scriptkin bench analyse`. The results script turns it into
#                    tables, figures, and numbers. The consequence folders are read from inside it.
# --consequence DIR  the measured consequence folder, when it is not DIR/consequence.
# --grid DIR         the grid consequence folder, when it is not DIR/consequence_grid.
# --submission       stop at an unfinished passage, a missing result, a dry run, or an identifying word.
# --out DIR          the build folder. Default: the build folder of ./check, subfolder paper.
#
# The build stops at an undefined reference, an undefined citation, or a label that is defined twice.
set -euo pipefail

PAPER=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$PAPER/.." && pwd)
BUILD=${SCRIPT_BUILD:-"$HOME/Library/Caches/SCRIPT/$(basename "$ROOT")-$(printf %s "$ROOT" | shasum | cut -c1-8)"}/paper
MODE=draft
RESULTS=""
CONSEQUENCE=""
GRID=""
while [ $# -gt 0 ]; do
    case "$1" in
        --submission) MODE=submission ;;
        --results) RESULTS=$2; shift ;;
        --consequence) CONSEQUENCE=$2; shift ;;
        --grid) GRID=$2; shift ;;
        --out) BUILD=$2; shift ;;
        *) echo "build.sh: unknown argument $1" >&2; exit 2 ;;
    esac
    shift
done

fail() { echo "build.sh: $1" >&2; exit 1; }

SRC="$BUILD/src"
OUT="$BUILD/out"
rm -rf "$SRC" "$OUT"
mkdir -p "$SRC" "$OUT"
# Copy the sources without the metadata files of an exFAT drive.
(cd "$PAPER" && find . -type f ! -name '._*' ! -name '.DS_Store' ! -path './tools/*' -print0 | cpio -0 -pdm --quiet "$SRC")

if [ -n "$RESULTS" ]; then
    ARGS=(--results "$RESULTS" --out "$SRC/results")
    [ -n "$CONSEQUENCE" ] && ARGS+=(--consequence "$CONSEQUENCE")
    [ -n "$GRID" ] && ARGS+=(--grid "$GRID")
    python3 "$PAPER/tools/make_results.py" "${ARGS[@]}"
fi

if [ "$MODE" = submission ]; then
    printf '%s\n' '\submissiontrue' > "$SRC/buildconfig.tex"
else
    printf '%s\n' '\submissionfalse' > "$SRC/buildconfig.tex"
fi

STATUS=0
for DOC in main supplement; do
    echo "== $DOC ($MODE)"
    if ! (cd "$SRC" && tectonic -X compile "$DOC.tex" --keep-logs --keep-intermediates --outdir "$OUT" \
            >"$OUT/$DOC.build.txt" 2>&1); then
        grep -E '^(error|!)|Submission build' "$OUT/$DOC.build.txt" | head -20 >&2 || true
        fail "$DOC.tex does not compile. See $OUT/$DOC.build.txt"
    fi
    LOG="$OUT/$DOC.log"
    if grep -E "Reference .* undefined|Citation .* undefined|There were undefined references|multiply defined|Label .* multiply" "$LOG" | sort -u | head -20 | grep .; then
        echo "build.sh: $DOC has an undefined or repeated reference." >&2
        STATUS=1
    fi
    if [ -f "$OUT/$DOC.blg" ] && grep -E "I didn't find a database entry|Warning--" "$OUT/$DOC.blg" | head -20 | grep .; then
        echo "build.sh: $DOC has a bibliography warning." >&2
        STATUS=1
    fi
    OVERFULL=$(grep -c "Overfull \\\\hbox" "$LOG" || true)
    PAGES=$(pdfinfo "$OUT/$DOC.pdf" | awk '/^Pages/{print $2}')
    pdftotext -layout "$OUT/$DOC.pdf" "$OUT/$DOC.txt"
    PENDING=$(grep -c "PENDING:" "$OUT/$DOC.txt" || true)
    DRY=$(grep -c "DRY RUN" "$OUT/$DOC.txt" || true)
    echo "$DOC.pdf: $PAGES pages, $PENDING unfinished passages, $DRY dry-run marks, $OVERFULL overfull boxes"
    if grep -n "??" "$OUT/$DOC.txt" | head -5 | grep .; then
        echo "build.sh: $DOC shows an unresolved reference." >&2
        STATUS=1
    fi
    if [ "$MODE" = submission ]; then
        [ "$PENDING" -eq 0 ] || { echo "build.sh: $DOC has unfinished passages." >&2; STATUS=1; }
        [ "$DRY" -eq 0 ] || { echo "build.sh: $DOC holds dry-run content." >&2; STATUS=1; }
        # Words that identify the authors or the project. Extend the list in anonymity_words.txt.
        if grep -n -w -F -f "$PAPER/anonymity_words.txt" "$OUT/$DOC.txt" | head -10 | grep .; then
            echo "build.sh: $DOC holds an identifying word." >&2
            STATUS=1
        fi
        if pdfinfo "$OUT/$DOC.pdf" | grep -E "^Author:[[:space:]]*[^[:space:]]"; then
            echo "build.sh: $DOC has an author in its document properties." >&2
            STATUS=1
        fi
    fi
done
echo "main.pdf: about $(wc -w < "$OUT/main.txt" | tr -d ' ') words in all, with captions, tables, and references"
echo "output: $OUT"
exit $STATUS
