"""Live acceptance probe. Run on the server with the service environment."""
import json
import os
import secrets
import tempfile
import argparse
from pathlib import Path

import vk_api

from . import ai, media


def probe_vk():
    owner = int(os.environ["VK_OWNER_ID"])
    client = vk_api.VkApi(token=os.environ["VK_TOKEN"])
    with tempfile.TemporaryDirectory() as temp:
        path = media.render("人人", Path(temp))
        try:
            attachment = media.upload(client, owner, path)
        except vk_api.exceptions.ApiError as error:
            if error.code != 15:
                raise
            raise SystemExit("ВК отклонил загрузку GIF (код 15). Создайте ключ сообщества "
                             "с правами на сообщения и файлы (документы) и введите его "
                             "при повторном запуске обновления.") from None
        client.get_api().messages.send(peer_id=owner, random_id=secrets.randbelow(2**31-1)+1,
                                       message="Проверка GIF. Откройте «人人» на телефоне и компьютере.",
                                       attachment=attachment)
    print("ВК принял GIF.")


def probe_yandex():
    task = {"hanzi":"人", "translation":"человек", "allowed":"我是中国人他她你哪国吗的"}
    question = ai.generate(task)
    result = ai.check({"question":json.dumps(question), "answer":question["zh"]})
    if not result["correct"]:
        raise RuntimeError("Модель отклонила собственный эталон")
    print("Генерация и проверка прошли.", question["ru"])


def main(vk_only=False):
    failures = []
    checks = [("ВК", probe_vk)]
    if not vk_only:
        checks.append(("Yandex", probe_yandex))
    for name, probe in checks:
        try:
            probe()
        except (Exception, SystemExit) as error:
            failures.append(name)
            if isinstance(error, SystemExit) or (name == "ВК" and str(error).startswith(("Загрузка GIF:", "Сбой загрузки GIF в ВК ("))):
                detail = str(error)
            else:
                status = getattr(getattr(error, "response", None), "status_code", None)
                detail = f"{type(error).__name__}, код {status or getattr(error, 'code', '—')}"
            print(f"{name} — {detail}", flush=True)
    if failures:
        raise SystemExit(2 if failures == ["ВК"] else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--vk-only", action="store_true")
    main(parser.parse_args().vk_only)

