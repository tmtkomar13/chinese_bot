# Тренажёр китайского языка в ВК

Личный бот для занятий по первой ступени Boya Chinese. Откройте личные сообщения сообщества и напишите «Меню». Нажмите «Начать тренировку», прочитайте перевод и напишите слово на бумаге. По кнопке «Показать ответ» появятся иероглифы и пиньинь. Оцените ответ одной из трёх кнопок. Чтобы продолжить прерванную тренировку, снова нажмите «Начать тренировку».

В базе 705 карточек из 30 уроков. Изначально доступны уроки 1–13. В «Открытых уроках» можно выбрать последний урок кнопками или сообщением, например `урок 14`. Выбор применяется к следующей подборке. Через «Точно знаю» в главном меню можно вернуть отдельное слово. В статистике попыткой считается оценка «Вспомнил» или «Не вспомнил».

## Словарь на Windows

Готовая база первого запуска находится в `data/trainer.sqlite3`. В `data/vocabulary.tsv` сохранены все 684 позиции поурочных списков и 22 позиции из разделов имён собственных, включая вложенные строки в таблицах. Объём совпадает с двумя указателями на печатной с. 209. В уроках 9 и 23 запись 看 со значением «смотреть» имеет один ключ. В уроке 20 у 看 другое значение и отдельный ключ. Пустое поле части речи соответствует пустой ячейке в учебнике. Пиньинь для 联欢会, пропущенный в таблице урока 30, взят из словарного указателя на печатной с. 203.

Для воспроизведения базы нужен Python 3.9 или новее. В PowerShell из корня проекта выполните:

```powershell
python -X utf8 -m trainer import data/vocabulary.tsv
python -X utf8 -m trainer check
```

Чтобы собрать независимую копию, укажите путь перед командой, например `python -m trainer --db data/second.sqlite3 import data/vocabulary.tsv`. При исправлении карточки сохраните её `key`. Повтор одного значения в другом уроке получает тот же ключ, новое значение — новый. Импорт нормализует Unicode, объединяет повторения, обновляет текст и сохраняет самый ранний урок. В таблице сохраняется порядок исходных строк. Номера в ключах — постоянные идентификаторы, их не нужно пересчитывать при новой вставке.

Для проверки бота на Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m unittest discover -s tests -v
```

## Сообщество ВК

1. В управлении сообществом включите сообщения и возможности ботов, чтобы в диалоге работала клавиатура.
2. В разделе работы с API создайте ключ доступа сообщества с разрешением на сообщения. Сохраните его для ввода в терминале нетбука.
3. Включите Long Poll API. В настройках событий включите «Входящее сообщение» (`message_new`). Выберите актуальную версию с сообщением в `object.message`.
4. Для этой установки задано сообщество `238004375`. Скрипт принимает числовой ID владельца или его короткое имя, по умолчанию `tmtkomar`. При установке он запрашивает числовой ID у ВК. В рабочем режиме бот принимает только личные сообщения от этого ID. [Документация библиотеки Bots Long Poll](https://vk-api.readthedocs.io/en/latest/bot_longpoll.html)

## Первый перенос и запуск на Debian Minimal

Нужны доступ по SSH, права sudo, systemd и интернет на нетбуке. Из папки проекта запустите в интерактивном PowerShell:

```powershell
.\deploy\from-windows.ps1 -Destination tmtkomar@HomeServer
```

Скрипт упакует готовую SQLite, исходный TSV, программу и файлы установки, скопирует архив через SCP и откроет SSH-терминал. Введите запрошенные пароли в этом окне. На Debian установщик при необходимости установит `python3-venv`, проверит базу, попросит ключ ВК со скрытым вводом и проверит доступ к Long Poll. Затем создаст системного пользователя, отдельное окружение Python и две службы. Последние строки `active` относятся к боту и таймеру. Входящий порт для ВК открывать не требуется.

Для переноса через флешку скопируйте `trainer/`, `deploy/`, `data/` и `requirements.txt` с сохранением структуры. Войдите на нетбуке в скопированный каталог и выполните `sudo bash deploy/install.sh`. Первичная установка останавливается при обнаружении существующей установки. Действующая база находится в `/var/lib/chinese-trainer/trainer.sqlite3`, токен и два ID — в `/etc/chinese-trainer.env` с правами 0600. После смены токена отредактируйте файл через `sudoedit /etc/chinese-trainer.env` и перезапустите службу.

```sh
systemctl status chinese-trainer --no-pager
sudo journalctl -u chinese-trainer -b -n 50 --no-pager
sudo journalctl -u chinese-trainer -f
systemctl list-timers chinese-trainer-backup.timer
```

## Новая редакция словаря

На Windows исправьте TSV и проверьте импорт в отдельную копию базы. Перенесите только проверенный TSV:

```powershell
scp data/vocabulary.tsv tmtkomar@HomeServer:vocabulary-update.tsv
```

На Debian запустите резервирование, обновите рабочую SQLite транзакцией и сохраните копию исходника. Бот может работать во время импорта.

```sh
sudo systemctl start chinese-trainer-backup.service
sudo install -m 644 ~/vocabulary-update.tsv /opt/chinese-trainer/data/vocabulary-update.tsv
cd /opt/chinese-trainer
sudo -u chinese-trainer .venv/bin/python -m trainer --db /var/lib/chinese-trainer/trainer.sqlite3 import data/vocabulary-update.tsv
sudo mv data/vocabulary-update.tsv data/vocabulary.tsv
```

## Копии и восстановление

Таймер запускается в 03:15 по времени нетбука, пропущенный запуск выполняется после включения. Копия создаётся через SQLite Backup API, проходит `integrity_check` и публикуется атомарно. За одну дату остаётся один снимок, всего до семи. Чтобы увидеть результат:

```sh
sudo ls -lh /var/lib/chinese-trainer/backups
sudo journalctl -u chinese-trainer-backup.service -n 20
```

Для восстановления выберите нужную дату из списка. Выполняйте в оболочке root (`sudo -i`). Сначала остановите запись и сохраните текущую базу под отдельным именем. Команда `check` проверяет выбранную копию до замены.

```sh
set -e
state=/var/lib/chinese-trainer
read -rp 'Дата выбранной копии, ГГГГ-ММ-ДД: ' day
cd /opt/chinese-trainer
systemctl stop chinese-trainer-backup.timer chinese-trainer-backup.service chinese-trainer.service
sudo -u chinese-trainer .venv/bin/python -m trainer --db "$state/backups/trainer-$day.sqlite3" check
cp -p "$state/trainer.sqlite3" "$state/before-restore-$(date +%s).sqlite3"
install -o chinese-trainer -g chinese-trainer -m 600 "$state/backups/trainer-$day.sqlite3" "$state/restore.sqlite3"
mv "$state/restore.sqlite3" "$state/trainer.sqlite3"
systemctl start chinese-trainer.service chinese-trainer-backup.timer
exit
```

Если проверка прервалась до замены, запустите остановленные службы той же командой `systemctl start`. После восстановления откройте статистику и незавершённую тренировку.

## Работа с закрытой крышкой и проверка на самом нетбуке

Для проверки через живой диалог запустите в PowerShell `.\deploy\from-windows.ps1 -CheckTraining`. Оставьте окно открытым, пройдите первую карточку в ВК и дождитесь сообщения в терминале о перезапуске службы. Нажмите «Начать тренировку», завершите остальные задания. Скрипт проверит сохранённые оценки и резервную копию. Для ручной проверки используйте шаги ниже.

В Debian Minimal создайте каталог `sudo mkdir -p /etc/systemd/logind.conf.d`. Откройте `sudoedit /etc/systemd/logind.conf.d/80-trainer-lid.conf` и сохраните:

```ini
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
```

Параметры действуют после перезагрузки. Назначайте перезагрузку, когда можно прервать другие задачи нетбука. [Параметры крышки в systemd-logind](https://www.freedesktop.org/software/systemd/man/latest/logind.conf.html)

1. Запишите текущую статистику, начните тренировку в личном диалоге сообщества. Покажите ответ, нажмите «Не вспомнил». После открытия следующего ответа запомните слово и номер задания.
2. Выполните `sudo systemctl restart chinese-trainer`. Нажмите «Начать тренировку». Должны вернуться тот же номер и уже открытый ответ. Проверьте, что попытка и ошибка учтены один раз.
3. Закончите остальные задания, включая «Вспомнил» и «Точно знаю». Сравните итоги с изменением общей статистики. Верните одно исключённое слово через меню.
4. Когда нетбук можно перезагрузить, выполните `sudo systemctl reboot`. После загрузки проверьте обе записи через `systemctl is-active chinese-trainer chinese-trainer-backup.timer`. Закройте крышку, откройте статистику в ВК и сравните с результатом перед перезагрузкой.
5. При кратком перерыве связи бот повторяет подключение с задержкой от 1 до 120 секунд. Проверьте восстановление диалога по журналу и сохранённой карточке.

Подробности устройства и порядок расширения программы — в [путеводителе по проекту](PROJECT_STRUCTURE.md).
