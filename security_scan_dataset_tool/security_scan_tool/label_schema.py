"""人工标注字段定义，界面与 CSV/JSON 共用同一份配置。"""

SCAN_REGIONS = [
    ("left_hand", "左手 left_hand"),
    ("right_hand", "右手 right_hand"),
    ("abdomen", "腹部 abdomen"),
    ("left_leg", "左腿 left_leg"),
    ("right_leg", "右腿 right_leg"),
    ("back", "背面 back"),
    ("left_foot", "左脚 left_foot"),
    ("right_foot", "右脚 right_foot"),
    ("shoes", "鞋子 shoes"),
]

SCAN_REGION_KEYS = [key for key, _ in SCAN_REGIONS]
REGION_TITLES = {key: title.split()[0] for key, title in SCAN_REGIONS}
