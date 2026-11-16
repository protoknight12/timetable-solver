"""Bulgarian texts for solve jobs and checker violations (keys come from checker/core.py)."""

JOB_STATUS = {
    "queued": "На опашка",
    "running": "Изчислява се",
    "finished": "Завършено",
    "failed": "Неуспешно",
}

SOLVER_STATUS = {
    "OPTIMAL": "Оптимално решение",
    "FEASIBLE": "Допустимо решение (не е доказано, че е най-доброто)",
    "INFEASIBLE": "Няма решение",
    "MODEL_INVALID": "Невалиден модел",
    "UNKNOWN": "Не е намерено решение в зададеното време",
}

MESSAGES = {
    "infeasible_core": "Ограничението участва в конфликт, който прави разписанието невъзможно.",
    "no_timetable": "Няма изграден график.",
    "unknown_course": "Урок към несъществуващ учебен план.",
    "unknown_part": "Урок към несъществуваща част от учебен план.",
    "unknown_room": "Урок в несъществуваща стая.",
    "wrong_block_count": "Грешен брой блокове на част от учебен план.",
    "wrong_block_length": "Блок с грешна продължителност.",
    "block_outside_grid": "Блок извън учебните дни или часове.",
    "blocks_overlap": "Блокове на един учебен план се припокриват.",
    "parts_not_simultaneous": "Частите на един учебен план не са в един и същ час.",
    "teacher_double_booked": "Учител има два урока по едно и също време.",
    "groups_double_booked": "Група има два урока по едно и също време.",
    "room_missing": "Урок, който изисква стая, няма стая.",
    "room_mismatch": "Стаята не отговаря на изискванията на урока.",
    "room_over_capacity": "Стаята е заета над капацитета си.",
    "teacher_unavailable_day": "Учител има урок в ден, в който не работи.",
    "teacher_unavailable_slot": "Учител има урок в час, в който не е на разположение.",
    "room_type_not_met": "Урокът не е в стая от изискания вид.",
    "too_many_lessons": "Класът има повече часове на ден от допустимото.",
    "too_many_same_subject": "Класът има повече часове по предмета на ден от допустимото.",
    "teacher_too_many_lessons": "Учителят има повече часове на ден от допустимото.",
    "gap_in_day": "Класът има прозорец през деня.",
    "day_starts_late": "Денят на класа не започва от първия час.",
    "period_reserved_for_other_subjects": "Урок по друг предмет е в час, запазен за определени предмети.",
    "block_not_at_fixed_slot": "Блокът не е във фиксирания си час.",
    "invalid_constraint_params": "Ограничението има невалидни параметри.",
    "unknown_constraint_type": "Ограничението е от непознат вид.",
}
FALLBACK_MESSAGE = "Нарушение на правило."
