"""OpenAPI 3.1 description of the read-only API (app/routes/api.py).

Hand-written but kept honest by a test: every /api/v1 route must appear here and
every path here must exist. Paste /api/v1/openapi.json into any OpenAPI viewer.
"""

from __future__ import annotations


def _ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


def _json(schema: dict, description: str = "OK") -> dict:
    return {
        "description": description,
        "content": {"application/json": {"schema": schema}},
    }


_ERRORS = {
    "401": {"$ref": "#/components/responses/Unauthorized"},
    "429": {"$ref": "#/components/responses/TooManyRequests"},
}
_NOT_FOUND = {"404": {"$ref": "#/components/responses/NotFound"}}


def _int_param(name: str, where: str, description: str, required: bool = False) -> dict:
    return {
        "name": name,
        "in": where,
        "required": required or where == "path",
        "description": description,
        "schema": {"type": "integer"},
    }


_DAY = _int_param("day", "query", "Само уроците в този ден (1 = понеделник).")


def _list(tag: str, key: str, item: dict, summary: str) -> dict:
    return {
        "get": {
            "tags": [tag],
            "summary": summary,
            "responses": {
                "200": _json(
                    {
                        "type": "object",
                        "properties": {key: {"type": "array", "items": item}},
                    }
                ),
                **_ERRORS,
            },
        }
    }


def spec() -> dict:
    timetable_ok = _json(_ref("Timetable"))
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "SATurn API",
            "version": "1.0",
            "description": (
                "API само за четене на публикуваното училищно разписание. "
                "Достъп: вход в системата (бисквитка на сесията) или "
                "заглавка `Authorization: Bearer <API_TOKEN>`. "
                "Пътят /public/{token} не изисква вход и връща само един клас."
            ),
        },
        "servers": [{"url": "/api/v1"}],
        "security": [{"bearer": []}, {"session": []}],
        "paths": {
            "/": {
                "get": {
                    "tags": ["Общи"],
                    "summary": "Версия и връзки към ресурсите",
                    "security": [],
                    "responses": {"200": _json({"type": "object"})},
                }
            },
            "/openapi.json": {
                "get": {
                    "tags": ["Общи"],
                    "summary": "Това описание",
                    "security": [],
                    "responses": {"200": _json({"type": "object"})},
                }
            },
            "/timetable": {
                "get": {
                    "tags": ["Разписание"],
                    "summary": "Цялото публикувано разписание",
                    "parameters": [
                        _int_param("class_id", "query", "Само уроците на този клас."),
                        _int_param(
                            "teacher_id", "query", "Само уроците на този учител."
                        ),
                        _int_param("room_id", "query", "Само уроците в тази стая."),
                        _DAY,
                    ],
                    "responses": {"200": timetable_ok, **_ERRORS, **_NOT_FOUND},
                }
            },
            "/classes": _list("Данни", "classes", _ref("SchoolClass"), "Класове"),
            "/classes/{class_id}/timetable": {
                "get": {
                    "tags": ["Разписание"],
                    "summary": "Разписание на един клас",
                    "parameters": [
                        _int_param("class_id", "path", "Номер на класа."),
                        _DAY,
                    ],
                    "responses": {"200": timetable_ok, **_ERRORS, **_NOT_FOUND},
                }
            },
            "/teachers": _list("Данни", "teachers", _ref("Teacher"), "Учители"),
            "/teachers/{teacher_id}/timetable": {
                "get": {
                    "tags": ["Разписание"],
                    "summary": "Разписание на един учител",
                    "parameters": [
                        _int_param("teacher_id", "path", "Номер на учителя."),
                        _DAY,
                    ],
                    "responses": {"200": timetable_ok, **_ERRORS, **_NOT_FOUND},
                }
            },
            "/rooms": _list("Данни", "rooms", _ref("Room"), "Стаи"),
            "/subjects": _list("Данни", "subjects", _ref("Named"), "Предмети"),
            "/periods": {
                "get": {
                    "tags": ["Данни"],
                    "summary": "Учебни дни и часове (звънец)",
                    "responses": {
                        "200": _json(
                            {
                                "type": "object",
                                "properties": {
                                    "days": {"type": "array", "items": _ref("Day")},
                                    "periods": {
                                        "type": "array",
                                        "items": _ref("Period"),
                                    },
                                },
                            }
                        ),
                        **_ERRORS,
                    },
                }
            },
            "/public/{token}": {
                "get": {
                    "tags": ["Публично"],
                    "summary": "Разписание на един клас по публичната му връзка",
                    "security": [],
                    "parameters": [
                        {
                            "name": "token",
                            "in": "path",
                            "required": True,
                            "description": "Тайният ключ от публичната връзка /p/{token}.",
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": timetable_ok,
                        "429": _ERRORS["429"],
                        **_NOT_FOUND,
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {
                "bearer": {"type": "http", "scheme": "bearer"},
                "session": {"type": "apiKey", "in": "cookie", "name": "session"},
            },
            "responses": {
                "Unauthorized": _json(_ref("Error"), "Нужен е вход или API ключ"),
                "NotFound": _json(
                    _ref("Error"), "Не е намерено или няма публикувано разписание"
                ),
                "TooManyRequests": _json(_ref("Error"), "Твърде много заявки"),
            },
            "schemas": {
                "Error": {
                    "type": "object",
                    "properties": {
                        "error": {
                            "type": "object",
                            "properties": {
                                "code": {"type": "integer"},
                                "message": {"type": "string"},
                            },
                        }
                    },
                },
                "Named": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                    },
                },
                "Teacher": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "short_name": {"type": ["string", "null"]},
                    },
                },
                "SchoolClass": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "grade": {"type": ["integer", "null"]},
                        "student_count": {"type": ["integer", "null"]},
                        "class_teacher_id": {"type": ["integer", "null"]},
                    },
                },
                "Room": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "type": {"type": "string"},
                        "seats": {"type": ["integer", "null"]},
                    },
                },
                "Day": {
                    "type": "object",
                    "properties": {
                        "number": {"type": "integer"},
                        "name": {"type": "string"},
                    },
                },
                "Period": {
                    "type": "object",
                    "properties": {
                        "number": {"type": "integer"},
                        "start": {"type": "string", "example": "07:30"},
                        "end": {"type": "string", "example": "08:15"},
                    },
                },
                "Group": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "class_id": {"type": "integer"},
                        "class_name": {"type": "string"},
                        "name": {"type": "string"},
                        "whole_class": {"type": "boolean"},
                    },
                },
                "Lesson": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "day": {"type": "integer"},
                        "start_period": {"type": "integer"},
                        "length": {
                            "type": "integer",
                            "description": "Брой поредни часове.",
                        },
                        "week": {
                            "type": ["string", "null"],
                            "enum": ["A", "B", None],
                            "description": "null = всяка седмица.",
                        },
                        "subject": _ref("Named"),
                        "teachers": {"type": "array", "items": _ref("Named")},
                        "groups": {"type": "array", "items": _ref("Group")},
                        "room": {"oneOf": [_ref("Named"), {"type": "null"}]},
                    },
                },
                "Timetable": {
                    "type": "object",
                    "properties": {
                        "timetable": {
                            "type": "object",
                            "properties": {
                                "job_id": {"type": "integer"},
                                "solver_status": {"type": "string"},
                                "published_at": {
                                    "type": "string",
                                    "format": "date-time",
                                },
                            },
                        },
                        "days": {"type": "array", "items": _ref("Day")},
                        "periods": {"type": "array", "items": _ref("Period")},
                        "lessons": {"type": "array", "items": _ref("Lesson")},
                    },
                },
            },
        },
    }
