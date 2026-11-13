from flask import Flask, render_template

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
        title, message = MESSAGES[code]
        return (
            render_template("error.html", code=code, title=title, message=message),
            code,
        )

    for code in MESSAGES:
        app.register_error_handler(code, handler)
