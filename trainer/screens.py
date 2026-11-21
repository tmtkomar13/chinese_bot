MENU = [[("Начать тренировку", "start"), ("Повторить урок", "review")],
        [("Открытые уроки", "lessons"), ("Поиск", "search")],
        [("Точно знаю", "excluded"), ("Статистика", "stats")], [("Настройки", "settings")]]
BACK = [("Меню", "menu")]


def screen(text, buttons=None):
    return {"text": text, "buttons": MENU if buttons is None else buttons}


def offer_resume():
    return screen("У вас уже есть незавершённая тренировка.",
                  [[("Продолжить тренировку", "resume")], BACK])
