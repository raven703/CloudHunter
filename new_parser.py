"""Тонкий фасад над parser.py (обратная совместимость импортов).

Всё содержимое парсера теперь в parser.py. Этот модуль оставлен только
для того, чтобы существующие импорты из client.py / main.py продолжали
работать без изменений.
"""
from parser import (  # noqa: F401
    parse_ui_root, parse_modal_info, npc_class_from_values,
    parse_local_chat_new, parse_npc_lists,
    EveParserNew,
    STANDING_NEUTRAL, STANDING_HOSTILE, STANDING_ALLIANCE,
    STANDING_GOOD, STANDING_CORPORATION, STANDING_UNKNOWN,
    STANDING_COLORS, build_debug_blocks,
)
