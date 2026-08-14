#!/usr/bin/env bash
# Сборка wlrestart без Gradle: NeoForge 1.21.1 в проде использует читаемые
# Mojang-имена, поэтому достаточно javac по jar-ам установленного сервера.
#
#   ./build.sh [путь-к-серверу] [путь-к-jdk21]
#
# По умолчанию берёт сервер из C:\Users\rusla\Desktop\сервак.
# Запускать из Git Bash: пути для javac конвертируются через cygpath.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SERVER="${1:-/c/Users/rusla/Desktop/сервак}"
JDK="${2:-/c/Program Files/Java/jdk-21.0.10}"

JAVAC="$JDK/bin/javac.exe"
JAR="$JDK/bin/jar.exe"
[ -x "$JAVAC" ] || { echo "нет javac: $JAVAC"; exit 1; }

LIBS="$SERVER/libraries"
[ -d "$LIBS" ] || { echo "нет папки libraries: $LIBS"; exit 1; }

win() { cygpath -w "$1"; }

# Класспас — jar-ы сервера в windows-виде через ';'.
# Рядом с server-...-srg.jar (читаемые Mojang-имена) лежат slim/unpacked/extra —
# в них имена обфусцированы, и если javac возьмёт их первыми, весь API
# «пропадёт». Поэтому их отбрасываем.
CP=""
while IFS= read -r jar; do
    case "$jar" in
        *-slim.jar|*-unpacked.jar|*-extra.jar) continue ;;
    esac
    CP="$CP$(win "$jar");"
done < <(find "$LIBS" -name '*.jar')

OUT="$HERE/build"
rm -rf "$OUT"
mkdir -p "$OUT/classes"

echo "==> компилирую"
: > "$OUT/sources.txt"
while IFS= read -r f; do
    win "$f" >> "$OUT/sources.txt"
done < <(find "$HERE/src" -name '*.java')

"$JAVAC" -encoding UTF-8 --release 21 -nowarn \
    -cp "$CP" -d "$(win "$OUT/classes")" "@$(win "$OUT/sources.txt")"

echo "==> собираю jar"
cp -r "$HERE/resources/." "$OUT/classes/"
"$JAR" --create --file "$(win "$OUT/wlrestart-1.0.0.jar")" -C "$(win "$OUT/classes")" .

echo "готово: $OUT/wlrestart-1.0.0.jar"
