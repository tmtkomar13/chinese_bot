import json
import logging
import os
import time

from .dialog import respond
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
    reply = respond(db, owner, message.get("text", ""), command if isinstance(command, str) else None)
    db.execute("UPDATE vk_messages SET reply=? WHERE id=?",
               (json.dumps(reply, ensure_ascii=False), inserted.lastrowid))


def keyboard(buttons):
    rows = []
    for row in buttons:
        rows.append([{"action": {"type": "text", "label": label,
                                 "payload": json.dumps({"action": command})},
                      "color": "secondary"} for label, command in row])
    return json.dumps({"one_time": False, "buttons": rows}, ensure_ascii=False)


def deliver(db, api, owner):
    for msg in db.execute("SELECT id, peer, reply FROM vk_messages WHERE reply IS NOT NULL AND peer=? ORDER BY id",
                          (owner,)).fetchall():
        reply = json.loads(msg["reply"])
        api.messages.send(peer_id=msg["peer"], random_id=msg["id"], message=reply["text"],
                          keyboard=keyboard(reply["buttons"]))
        db.execute("UPDATE vk_messages SET reply=NULL WHERE id=?", (msg["id"],))


def poll_once(db, poll, owner, group):
    events = poll.check()
    with transaction(db):
        for event in events:
            if event.group_id == group and event.raw["type"] == "message_new" and event.message:
                accept(db, owner, event.message)
        db.execute("INSERT OR REPLACE INTO vk_cursor VALUES (?,?)", (group, str(poll.ts)))


def run(db):
    import requests
    import vk_api
    from vk_api.bot_longpoll import VkBotLongPoll

    token = os.environ["VK_TOKEN"]
    group, owner = int(os.environ["VK_GROUP_ID"]), int(os.environ["VK_OWNER_ID"])
    if min(group, owner) <= 0 or not token.strip():
        raise ValueError("Проверьте VK_GROUP_ID, VK_OWNER_ID и VK_TOKEN")
    client = vk_api.VkApi(token=token)
    api = client.get_api()
    delay = 1
    while True:
        poll = None
        try:
            deliver(db, api, owner)
            poll = VkBotLongPoll(client, group)
            saved = db.execute("SELECT ts FROM vk_cursor WHERE community=?", (group,)).fetchone()
            if saved:
                poll.ts = saved[0]
            else:
                db.execute("INSERT INTO vk_cursor VALUES (?,?)", (group, str(poll.ts)))
            logging.info("Соединение Bots Long Poll установлено")
            while True:
                poll_once(db, poll, owner, group)
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
