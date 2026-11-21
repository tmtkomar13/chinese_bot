import json
import logging
import os
import time
import threading

from .worker import enqueue, remind, work

from .training import moscow_now
from .dialog import respond
from .media import style_for
from .search_dialog import search_is_open
from .storage import transaction


def accept(db, owner, message):
    if message.get("from_id") != owner or message.get("peer_id") != owner or message.get("out"):
        return
    incoming = message.get("conversation_message_id")
    if not isinstance(incoming, int) or incoming <= 0:
        return
    inserted = db.execute("INSERT OR IGNORE INTO vk_messages(peer,incoming) VALUES (?,?)",
                          (owner, incoming))
    if not inserted.rowcount:
        return
    try:
        payload = json.loads(message.get("payload") or "{}")
    except (ValueError, TypeError):
        payload = {}
    command = payload.get("action") if isinstance(payload, dict) else None
    # A new screen supersedes the pending attachment. Showing the answer
    # again registers a fresh request if that version is still rendering.
    db.execute("""UPDATE tasks SET gif_request=NULL WHERE gif_request IS NOT NULL
                  AND session IN (SELECT id FROM sessions WHERE learner=? AND finished=0)""", (owner,))
    reply = respond(db, owner, message.get("text", ""), command if isinstance(command, str) else None)
    if reply.get("attachment") and "gif_revision" in reply:
        enqueue(db, owner, {"text": reply["text"], "buttons": reply["buttons"]})
        reply["text"] = "Порядок черт"
        reply["gif_only"] = True
    enqueue(db, owner, reply)


def keyboard(buttons):
    rows = []
    for row in buttons:
        rows.append([{"action": {"type": "open_link", "label": label, "link": command}}
                     if command.startswith(("https://", "http://")) else
                     {"action": {"type": "text", "label": label,
                                 "payload": json.dumps({"action": command})},
                      "color": "secondary"} for label, command in row])
    return json.dumps({"one_time": False, "buttons": rows}, ensure_ascii=False)


def is_current_gif(db, owner, reply):
    matches = db.execute("""SELECT l.speed,m.style FROM learners l JOIN sessions s ON s.learner=l.id
            JOIN tasks t ON t.session=s.id JOIN cards c ON c.id=t.card
            JOIN media m ON m.word=c.hanzi AND m.attachment=?
            WHERE l.id=? AND l.animation='gif' AND l.animation_revision=?
              AND t.gif_request=l.animation_revision AND s.finished=0 AND s.id=? AND t.number=?""",
                         (reply["attachment"], owner, reply["gif_revision"], *reply["task_state"][:2]))
    return any(row["style"] == style_for(row["speed"]) for row in matches)


def _deliver_reply(db, api, msg, reply):
    while True:
        attachment = reply.get("attachment")
        extra = {"attachment": attachment} if attachment else {}
        if "buttons" in reply and not (reply.get("gif_only") or reply.get("omit_keyboard")):
            extra["keyboard"] = keyboard(reply["buttons"])
        try:
            api.messages.send(peer_id=msg["peer"], random_id=msg["id"], message=reply["text"],
                              **extra)
            if attachment:
                logging.info("ВК принял GIF для карточки %s", reply.get("task_state", [])[:2])
            break
        except Exception as error:
            code = getattr(error, "code", None)
            if attachment and code in (100, 121, 129, 150):
                logging.warning("ВК отклонил вложение карточки: код %s", code)
                with transaction(db):
                    db.execute("DELETE FROM media WHERE attachment=?", (attachment,))
                    if reply.get("gif_only") and not reply.get("fresh_upload"):
                        # The open card still requests this version. Let the worker upload it again.
                        db.execute("DELETE FROM outbox WHERE id=?", (msg["id"],))
                        return
                    if reply.get("fresh_upload"):
                        db.execute("UPDATE tasks SET gif_request=NULL WHERE session=? AND number=?",
                                   reply["task_state"][:2])
                    reply.pop("attachment")
                    reply.pop("gif_revision", None)
                    reply.pop("gif_only", None)
                    reply["text"] += "\nGIF недоступен."
                    db.execute("UPDATE outbox SET reply=? WHERE id=?", (json.dumps(reply), msg["id"]))
            elif not attachment and code == 100:
                if "keyboard" not in extra:
                    logging.warning("ВК отклонил текст сообщения outbox %s: код %s", msg["id"], code)
                    break
                reply["omit_keyboard"] = True
                db.execute("UPDATE outbox SET reply=? WHERE id=?", (json.dumps(reply), msg["id"]))
            else:
                raise
    db.execute("DELETE FROM outbox WHERE id=?", (msg["id"],))


def deliver(db, api, owner):
    for msg in db.execute("SELECT id, peer, reply FROM outbox WHERE peer=? ORDER BY id",
                          (owner,)).fetchall():
        reply = json.loads(msg["reply"])
        if "task_state" in reply and search_is_open(db, owner):
            db.execute("DELETE FROM outbox WHERE id=?", (msg["id"],))
            continue
        if "reminder_day" in reply and search_is_open(db, owner):
            continue
        if "gif_revision" in reply and not is_current_gif(db, owner, reply):
            db.execute("DELETE FROM outbox WHERE id=?", (msg["id"],))
            continue
        if reply.get("attachment") and "gif_revision" not in reply and db.execute(
                "SELECT 1 FROM media WHERE attachment=? LIMIT 1", (reply["attachment"],)).fetchone():
            # V1 replies stored the GIF and the card keyboard together.
            reply.pop("attachment")
        if "reminder_day" in reply:
            day = reply["reminder_day"]
            due = db.execute("""SELECT reminders FROM learners WHERE id=? AND NOT EXISTS(
                        SELECT 1 FROM sessions WHERE learner=? AND kind='daily' AND day=? AND finished=1)""",
                             (owner, owner, day)).fetchone()
            if day != moscow_now().date().isoformat() or not due or not due[0]:
                db.execute("DELETE FROM outbox WHERE id=?", (msg["id"],))
                continue
        if "task_state" in reply and not db.execute(
                "SELECT 1 FROM tasks WHERE session=? AND number=? AND phase=? AND tries=? AND (?=0 OR grade IS NULL)",
                (*reply["task_state"], int(reply.get("requires_ungraded", False)))).fetchone():
            db.execute("DELETE FROM outbox WHERE id=?", (msg["id"],))
            continue
        _deliver_reply(db, api, msg, reply)


def poll_once(db, poll, owner, group):
    events = poll.check()
    with transaction(db):
        for event in events:
            if event.group_id == group and event.raw["type"] == "message_new" and event.message:
                accept(db, owner, event.message)
        db.execute("INSERT OR REPLACE INTO vk_cursor VALUES (?,?)", (group, str(poll.ts)))


def run(db):
    import vk_api

    token = os.environ["VK_TOKEN"]
    group, owner = int(os.environ["VK_GROUP_ID"]), int(os.environ["VK_OWNER_ID"])
    if min(group, owner) <= 0 or not token.strip():
        raise ValueError("Проверьте VK_GROUP_ID, VK_OWNER_ID и VK_TOKEN")
    client = vk_api.VkApi(token=token)
    api = client.get_api()
    stop = threading.Event()
    path = db.execute("PRAGMA database_list").fetchone()[2]
    thread = threading.Thread(target=work, args=(path, owner, stop), daemon=True)
    thread.start()
    try:
        listen(db, api, client, owner, group)
    finally:
        stop.set()
        thread.join(timeout=2)


def listen(db, api, client, owner, group):
    import requests
    import vk_api
    from vk_api.bot_longpoll import VkBotLongPoll
    delay = 1
    while True:
        poll = None
        try:
            remind(db, owner)
            deliver(db, api, owner)
            poll = VkBotLongPoll(client, group, wait=3)
            saved = db.execute("SELECT ts FROM vk_cursor WHERE community=?", (group,)).fetchone()
            if saved:
                poll.ts = saved[0]
            else:
                db.execute("INSERT INTO vk_cursor VALUES (?,?)", (group, str(poll.ts)))
            logging.info("Соединение Bots Long Poll установлено")
            while True:
                poll_once(db, poll, owner, group)
                remind(db, owner)
                deliver(db, api, owner)
                delay = 1
        except (requests.RequestException, vk_api.exceptions.VkApiError) as exc:
            logging.warning("Соединение ВК: %s, код %s. Повтор через %d с",
                            type(exc).__name__, getattr(exc, "code", "—"), delay)
            time.sleep(delay)
            delay = min(120, delay * 2)
        finally:
            if poll is not None:
                poll.session.close()
