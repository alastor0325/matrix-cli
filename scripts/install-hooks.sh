#!/bin/sh
cp "$(dirname "$0")/pre-commit" "$(git rev-parse --git-dir)/hooks/pre-commit"
chmod +x "$(git rev-parse --git-dir)/hooks/pre-commit"
echo "Hooks installed."
