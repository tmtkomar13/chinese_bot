#!/bin/bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
app=/opt/chinese-trainer
state=/var/lib/chinese-trainer
config=/etc/chinese-trainer.env
[[ $EUID == 0 && -d $app && -f $state/trainer.sqlite3 && -f $config ]]
if ! command -v chromium >/dev/null || ! dpkg-query -W -f='${Status}' python3-venv 2>/dev/null | grep -qx 'install ok installed'; then
    apt-get update
    apt-get install -y chromium python3-venv
fi
release=$(mktemp -d /opt/chinese-trainer-release.XXXXXXXX)
chmod 755 "$release"
cp -R trainer assets "$release/"
mkdir -p "$release/data"
cp data/vocabulary.tsv "$release/data/"
python3 -m venv "$release/.venv"
python="$release/.venv/bin/python"
packages=$("$python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
relative=${packages#"$release/.venv/"}
reused=false
for previous in /opt/chinese-trainer-release.????????; do
    [[ $previous != "$release" && -d "$previous/.venv/$relative" ]] || continue
    old_python="$previous/.venv/bin/python"
    [[ -x $old_python ]] || continue
    if "$old_python" -m pip --disable-pip-version-check install --dry-run --no-index -r requirements.txt >/dev/null 2>&1 &&
       "$old_python" -m pip check >/dev/null 2>&1 &&
       "$old_python" -c 'import vk_api, requests, tzdata; from PIL import Image; from playwright.sync_api import sync_playwright' 2>/dev/null; then
        cp -a "$previous/.venv/$relative/." "$packages/"
        reused=true
        break
    fi
done
if [[ $reused == true ]]; then
    echo "Использую зависимости из предыдущей установки на сервере."
    "$python" -m pip --disable-pip-version-check install --no-index -r requirements.txt
    "$python" -m pip check
else
    "$python" -m pip install -r requirements.txt
fi
chmod -R a+rX "$release"
snapshot=$(mktemp -d /var/backups/chinese-trainer.XXXXXXXX)
cp -p "$config" "$snapshot/environment"
cp -p "$config" "$snapshot/new-environment"
echo "Пара для отката: $snapshot" >&2
read -rsp "Новый ключ ВК (сообщения и документы; Enter — оставить текущий): " vk_key
printf '\n'
if [[ -n $vk_key ]]; then
    [[ $vk_key =~ ^[a-zA-Z0-9._-]+$ ]]
    sed -i '/^VK_TOKEN=/d' "$snapshot/new-environment"
    printf '\nVK_TOKEN=%s\n' "$vk_key" >> "$snapshot/new-environment"
    unset vk_key
fi
if ! grep -q '^YANDEX_FOLDER_ID=.' "$snapshot/new-environment"; then
    read -rp "Каталог Yandex AI Studio: " folder
    [[ $folder =~ ^[a-zA-Z0-9_-]+$ ]]
    printf '\nYANDEX_FOLDER_ID=%s\n' "$folder" >> "$snapshot/new-environment"
fi
if ! grep -q '^YANDEX_API_KEY=.' "$snapshot/new-environment"; then
    read -rsp "Ключ Yandex AI Studio: " api_key
    printf '\n'
    [[ $api_key =~ ^[a-zA-Z0-9._-]+$ ]]
    printf 'YANDEX_API_KEY=%s\n' "$api_key" >> "$snapshot/new-environment"
fi
grep -q '^CHROMIUM_PATH=' "$snapshot/new-environment" || echo CHROMIUM_PATH=/usr/bin/chromium >> "$snapshot/new-environment"
probe_options=()
while true; do
    if systemd-run --wait --pipe --collect -p User=chinese-trainer -p WorkingDirectory="$release" \
        -p EnvironmentFile="$snapshot/new-environment" "$release/.venv/bin/python" -u -m trainer.probe "${probe_options[@]}"; then
        break
    else
        result=$?
    fi
    [[ $result == 2 ]] || exit "$result"
    read -rp "Проверка ВК не прошла. Enter — повторить только ВК, q — выйти: " decision
    [[ -z $decision ]] || exit "$result"
    probe_options=(--vk-only)
done
read -rp "Если GIF работает на телефоне и компьютере, введите UPDATE для обновления: " decision
[[ $decision == UPDATE ]] || exit 0
systemctl stop chinese-trainer-backup.timer chinese-trainer-backup.service chinese-trainer.service
trap 'echo "Обновление прервано. Проверьте состояние служб и снимок $snapshot." >&2' ERR
cp -a "$app/." "$snapshot/application"
python3 - "$state/trainer.sqlite3" "$snapshot/trainer.sqlite3" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as src, sqlite3.connect(sys.argv[2]) as dst:
    src.backup(dst)
PY
cp "$snapshot/trainer.sqlite3" "$snapshot/test.sqlite3"
(cd "$release" && .venv/bin/python -m trainer --db "$snapshot/test.sqlite3" check)
install -m 600 "$snapshot/new-environment" "$config"
mv -T "$app" "$release.previous"
ln -s "$release" "$app"
(cd "$app" && sudo -u chinese-trainer .venv/bin/python -m trainer --db "$state/trainer.sqlite3" check)
systemctl start chinese-trainer.service chinese-trainer-backup.timer
systemctl is-active chinese-trainer.service chinese-trainer-backup.timer

