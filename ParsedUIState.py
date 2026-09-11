from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime

@dataclass
class ParsedUIState:
    # === БАЗОВАЯ ИНФОРМАЦИЯ ===
    timestamp: float = 0.0
    window_width: int = 0
    window_height: int = 0
    ui_root_address: int = 0
    
    # === HP КОРАБЛЯ ===
    # None = "нет данных" (в доке, при загрузке, или показатель отсутствует в дампе).
    # capacitor считается по кольцу ячеек pmark в CapacitorContainer
    # (см. _parse_capacitor_cells в full_parser.py).
    shield_pct: Optional[float] = None
    armor_pct: Optional[float] = None
    structure_pct: Optional[float] = None
    capacitor_pct: Optional[float] = None
    
    # === ДВИЖЕНИЕ ===
    speed: float = 0.0  # м/с
    is_warp: bool = False
    is_establishing: bool = False
    is_orbiting: bool = False
    is_docked: bool = False
    
    # === МОДУЛИ ===
    modules: Dict[str, str] = field(default_factory=dict)  # {slot_name: "ACTIVE"|"READY"|"OFFLINE"}
    repairers: Dict[str, str] = field(default_factory=dict)  # Активные ремонтники
    prop_modules: Dict[str, str] = field(default_factory=dict)  # Двигатель (afterburner/MWD)
    
    # === ДРОНЫ ===
    drones_in_bay: int = 0
    drones_in_space: int = 0
    drones_max: int = 0
    drones_idle: bool = False
    drones_fighting: bool = False
    drones_returning: bool = False
    drones_under_attack: bool = False
    drone_entries: List[Dict[str, Any]] = field(default_factory=list)
    # [{"name": str, "status": str, "hp": float}]
    
    # === ЦЕЛИ (l_target) ===
    targets: List[Dict[str, Any]] = field(default_factory=list)
    # [{"name": str, "distance": str, "is_active": bool, "x": int, "y": int}]
    active_target: Optional[Dict[str, Any]] = None
    
    # === ВЫБРАННЫЙ ОБЪЕКТ (SelectedItemWnd) ===
    selected_item: Optional[Dict[str, Any]] = field(default_factory=dict)
    # {"name": str, "distance": float, "is_target": bool, "icon": str,
    #  "x": int, "y": int, "w": int, "h": int,
    #  "actions": [{"name": str, "action": str, "x": int, "y": int,
    #               "w": int, "h": int, "icon": str}]}
    
    # === ОВЕРВЬЮ (OverviewWindow) ===
    overview_entries: List[Dict[str, Any]] = field(default_factory=list)
    # [{"name": str, "distance": str, "type": str, "x": int, "y": int}]
    
    # === NPC ===
    npc_all: List[Tuple[float, float]] = field(default_factory=list)
    npc_frigate: List[Tuple[float, float]] = field(default_factory=list)
    npc_battleship: List[Tuple[float, float]] = field(default_factory=list)
    npc_cruiser: List[Tuple[float, float]] = field(default_factory=list)
    npc_battlecruiser: List[Tuple[float, float]] = field(default_factory=list)
    
    # === ЛОКАЛЬНЫЙ ЧАТ ===
    local_player_count: int = 0
    local_has_neutral: bool = False
    local_has_hostile: bool = False
    local_players: List[Dict[str, Any]] = field(default_factory=list)
    # [{"name": str, "standing": str, "alliance": bool, "x": int, "y": int}]
    
    # === ПРОБ-СКАНЕР ===
    scan_results: List[Dict[str, Any]] = field(default_factory=list)
    # [{"id": str, "name": str, "group": str, "distance": str, "signal": str, "type": str, "x": int, "y": int}]
    probe_scanner_open: bool = False          # окно Probe Scanner открыто (для Alt+P, если нет)
    undock_button: Optional[Dict[str, Any]] = None  # {'x','y','w','h'} — кнопка Undock (абсолютные коорд.)
    
    # === ИНФО-ПАНЕЛЬ (SidePanel) ===
    current_system: str = ""
    security_status: float = 0.0
    nearest_location: str = ""
    sovereignty_holder: str = ""
    route_destination: str = ""
    bounty_risk: float = 0.0
    
    # === КОНТЕКСТНОЕ МЕНЮ (l_menu) ===
    menu_open: bool = False
    menu_type: str = ""  # "context_menu" | "context_submenu"
    menu_items: List[Dict[str, Any]] = field(default_factory=list)
    # [{"name": str, "text": str, "x": int, "y": int, "w": int, "h": int}]
    asteroid_belts: List[Dict[str, Any]] = field(default_factory=list)
    # Пояса из открытого подменю 'Asteroid Belts' (правый клик в космосе):
    # [{"name": "4GQ-XQ III - Asteroid Belt 1", "planet": "4GQ-XQ III",
    #   "belt": 1, "x": int, "y": int, "w": int, "h": int}]
    
    # === НЕОКОМ (Neocom) ===
    neocom_buttons: List[Dict[str, Any]] = field(default_factory=list)
    # [{"name": str, "has_notification": bool, "notification_count": int}]
    clock_time: str = ""
    notification_count: int = 0
    
    # === БОЕВОЙ РЕЖИМ ===
    combat_mode_active: str = "IDLE"  # "GUNS" | "DRONES" | "SAFETY" | "IDLE"
    is_pvp: bool = False
    
    # === ПРОЧЕЕ ===
    is_loading: bool = False
    lock_range_km: Optional[float] = None
    # Максимальная дистанция захвата цели (из QuickMessage клиента),
    # в километрах. None = клиент ещё не показывал такое сообщение.
    chat_channels: List[str] = field(default_factory=list)
    fps: int = 0

    # === МОДАЛЬНЫЕ ОКНА (blocking popup, например 'Information') ===
    modal_info: Dict[str, Any] = field(default_factory=dict)
    # из full_parser.parse_modal_info:
    # {
    #   'open': bool,            # есть модалка Telecom с заголовком Information
    #   'name': str,             # заголовок окна
    #   'x': int, 'y': int, 'w': int, 'h': int,
    #   'close': None | {'x','y','w','h'}   # кнопка Close
    # }