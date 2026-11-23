from flask import Flask, render_template, request

# Short Bulgarian texts per status code.
MESSAGES = {
    400: (
        "Невалидна заявка",
        "Заявката не може да бъде обработена. Презаредете страницата и опитайте отново.",
    ),
    403: ("Нямате достъп", "Нямате право да извършите това действие."),
    404: (
        "Страницата не е намерена",
        "Адресът е грешен или страницата вече не съществува.",
    ),
    405: (
        "Действието не е разрешено",
        "Този адрес не приема такава заявка.",
    ),
    413: (
        "Файлът е твърде голям",
        "Файлът надвишава позволения размер (до 2 MB, а за експорт от aSc — до 32 MB).",
    ),
    429: (
        "Твърде много опити",
        "Направихте твърде много заявки. Изчакайте малко и опитайте отново.",
    ),
    500: (
        "Възникна грешка",
        "Нещо се обърка от наша страна. Опитайте отново по-късно.",
    ),
}


def register_error_pages(app: Flask) -> None:
    def handler(error):
        code = getattr(error, "code", None)
        code = code if code in MESSAGES else 500
        if request.path.startswith("/api/"):
            # API clients get JSON, also for URLs that match no API route.
            from app.routes.api import ERRORS, _error

            return _error(code if code in ERRORS else 500)
        title, message = MESSAGES[code]
        return (
            render_template("error.html", code=code, title=title, message=message),
            code,
        )

    for code in MESSAGES:
        app.register_error_handler(code, handler)
