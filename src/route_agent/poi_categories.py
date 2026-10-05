"""Bounded OSM tag queries, independent of a fixed list of business types."""
import re
from .tools import ToolFailure

POI_KEYS = ("amenity", "shop", "leisure", "tourism", "healthcare")
CATEGORY_PATTERN = r"^(?:(?:amenity|shop|leisure|tourism|healthcare):)?[a-z][a-z0-9_]{0,63}$"

def category_tag(category):
    if not isinstance(category,str) or not re.fullmatch(CATEGORY_PATTERN,category):
        raise ToolFailure("经停类别需使用受支持的 OSM 键和值，例如 amenity:fuel、shop:supermarket。", "invalid_arguments")
    if ":" in category:
        return tuple(category.split(":",1))
    # Preserve existing request records; new types should specify their key.
    return ("shop" if category=="bakery" else "amenity",category)

def category_label(category):
    labels={"bakery":"面包店","cafe":"咖啡店","restaurant":"餐厅","hospital":"医院",
        "fuel":"加油站","pharmacy":"药店","supermarket":"超市","charging_station":"充电站"}
    value=(category or "").split(":")[-1]
    return labels.get(value,"所需经停地点")
