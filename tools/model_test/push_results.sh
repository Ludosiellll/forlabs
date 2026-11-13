#!/bin/bash
# Зашифрованные результаты (ключом провайдера) — в ветку model-results; в открытом виде только status.txt.
set -e
tag="$1"
tar czf - -C "$GITHUB_WORKSPACE" --exclude='bin' --exclude='obj' --exclude='node_modules' out \
  | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:NG_KEY -out "$RUNNER_TEMP/results.enc"
d="$RUNNER_TEMP/res_repo"; rm -rf "$d"; mkdir -p "$d"; cd "$d"
git init -q; git checkout -q -b model-results
cp "$RUNNER_TEMP/results.enc" .
progress=$(cat "$GITHUB_WORKSPACE/out/labs/progress.txt" 2>/dev/null || echo "-")
printf "run %s attempt %s\nstate %s\nprogress %s\ntime %s\n" "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT" "$tag" \
  "$progress" "$(date -u +%FT%TZ)" > status.txt
git add .
git -c user.name=forlabs-ci -c user.email=ci@forlabs.invalid commit -qm "model test: $tag ($GITHUB_RUN_ID)"
git push -q -f "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" HEAD:model-results
