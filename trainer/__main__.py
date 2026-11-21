import argparse
import logging
import os

from .storage import backup, database, import_vocabulary


def main():
    parser = argparse.ArgumentParser(description="Тренажёр китайского языка")
    parser.add_argument("--db", default=os.getenv("TRAINER_DB", "data/trainer.sqlite3"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("import").add_argument("tsv")
    sub.add_parser("backup").add_argument("directory")
    sub.add_parser("check")
    sub.add_parser("gif-status")
    sub.add_parser("topic-status")
    sub.add_parser("run")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with database(args.db, create=args.command == "import") as db:
        if args.command == "import":
            print("Импортировано карточек:", import_vocabulary(db, args.tsv))
        elif args.command == "backup":
            print(backup(db, args.directory))
        elif args.command == "gif-status":
            from .worker import gif_status
            gif_status(db)
        elif args.command == "topic-status":
            from .topics import print_topic_status
            print_topic_status(db)
        elif args.command == "check":
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
                raise SystemExit("Ошибка проверки SQLite")
            print("SQLite OK. Карточек по урокам:")
            print(dict(db.execute("SELECT lesson,count(*) FROM lesson_cards GROUP BY lesson")))
        else:
            from .vk import run
            run(db)


if __name__ == "__main__":
    main()
