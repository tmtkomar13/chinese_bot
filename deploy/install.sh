#!/bin/bash
# Run from the extracted project as root on Debian.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
app=/opt/chinese-trainer
state=/var/lib/chinese-trainer
config=/etc/chinese-trainer.env
if [[ $EUID != 0 ]]; then
    echo "Запустите установку через sudo." >&2
    exit 1
fi
if [[ -e $app || -e $state/trainer.sqlite3 || -e $config ]]; then
    echo "Обнаружена предыдущая установка. Порядок обновления приведён в README.md." >&2
    exit 1
fi
apt-get update
apt-get install -y chromium
if ! dpkg-query -W -f='${Status}' python3-venv 2>/dev/null | grep -q 'install ok installed'; then
    apt-get update
    apt-get install -y python3 python3-venv
fi
python3 -c 'import sys; assert sys.version_info >= (3, 9), "Требуется Python 3.9+"'
# Stage and validate everything before installing files or enabling services.
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
python3 -m venv "$stage/venv"
"$stage/venv/bin/python" -m pip wheel -q --wheel-dir "$stage/wheels" -r requirements.txt
"$stage/venv/bin/python" -m pip install --no-index --find-links "$stage/wheels" -r requirements.txt
"$stage/venv/bin/python" -m trainer --db data/trainer.sqlite3 check
read -rp "ID сообщества [238004375]: " group
read -rp "ID или короткое имя владельца [tmtkomar]: " owner
read -rsp "Ключ сообщества ВК: " token
printf '\n'
[[ ${group:=238004375} =~ ^[1-9][0-9]*$ && $token =~ ^[a-zA-Z0-9._-]+$ ]]
export VK_GROUP_ID="$group" VK_OWNER_NAME="${owner:-tmtkomar}" VK_TOKEN="$token"
number=$("$stage/venv/bin/python" - <<'PY'
import os, sys
import vk_api
api = vk_api.VkApi(token=os.environ["VK_TOKEN"]).get_api()
try:
    api.groups.getLongPollServer(group_id=int(os.environ["VK_GROUP_ID"]))
    print(int(api.users.get(user_ids=os.environ["VK_OWNER_NAME"])[0]["id"]))
except Exception as error:
    sys.exit(f"Проверьте настройки ВК, ключ и имя владельца ({type(error).__name__}).")
PY
)
printf 'VK_GROUP_ID=%s\nVK_OWNER_ID=%s\nVK_TOKEN=%s\n' "$group" "$number" "$token" > "$stage/config"
read -rp "Каталог Yandex AI Studio: " folder
read -rsp "API-ключ Yandex AI Studio: " ai_key
printf '\n'
[[ $folder =~ ^[a-zA-Z0-9_-]+$ && $ai_key =~ ^[a-zA-Z0-9._-]+$ ]]
printf 'YANDEX_FOLDER_ID=%s\nYANDEX_API_KEY=%s\nCHROMIUM_PATH=/usr/bin/chromium\n' "$folder" "$ai_key" >> "$stage/config"
unset token VK_TOKEN ai_key
printf 'Вход разрешён владельцу с числовым ID %s.\n' "$number"
getent passwd chinese-trainer >/dev/null || useradd --system --user-group --home-dir "$state" --shell /usr/sbin/nologin chinese-trainer
install -d -m 755 "$app"
# venv launchers contain absolute paths. Create the final venv in place.
python3 -m venv "$app/.venv"
"$app/.venv/bin/python" -m pip install --no-index --find-links "$stage/wheels" -r requirements.txt
install -d -m 755 "$app/trainer" "$app/trainer/migrations" "$app/data"
install -m 644 trainer/*.py "$app/trainer/"
install -m 644 trainer/migrations/*.sql "$app/trainer/migrations/"
install -m 644 data/vocabulary.tsv "$app/data/"
cp -R assets "$app/"
chmod -R a+rX "$app/assets" "$app/.venv"
install -d -o chinese-trainer -g chinese-trainer -m 700 "$state"
install -o chinese-trainer -g chinese-trainer -m 600 data/trainer.sqlite3 "$state/trainer.sqlite3"
install -m 600 "$stage/config" "$config"
install -m 644 deploy/*.service deploy/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now chinese-trainer.service chinese-trainer-backup.timer
systemctl start chinese-trainer-backup.service
systemctl is-active chinese-trainer.service chinese-trainer-backup.timer
