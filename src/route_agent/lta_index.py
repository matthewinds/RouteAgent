"""Static LinkID -> page index for LTA TrafficSpeedBands.

The index stores only link geometry and the page offset where each link was
listed. It never stores speeds: speed values used for a trip are always fetched
live for that trip. The index only lets the agent request the pages that cover
actual candidate geometry instead of paging the whole island sequentially.
"""
import json
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from shapely.geometry import LineString
from shapely.strtree import STRtree

from .traffic import PROJECT

PAGE_SIZE = 500
ENDPOINT = "v4/TrafficSpeedBands"
_lock = threading.Lock()
_loaded = {}


def index_path(settings):
    return settings.root/"data/cache/lta_speedband_index.json"


def _link(row):
    try:
        values = [float(row[k]) for k in ("StartLat","StartLon","EndLat","EndLon")]
    except (KeyError,TypeError,ValueError):
        return None
    return [str(row.get("LinkID","")),*values]


def build_index(settings, workers=16, max_pages=1000):
    """Full maintenance scan; not part of any trip's evidence or budget."""
    key = settings.credential("LTA_API_KEY")
    if not key:
        raise RuntimeError("LTA_API_KEY is not configured")
    url = settings.lta_base.rstrip("/")+"/"+ENDPOINT
    headers = {"AccountKey":key,"accept":"application/json"}
    pages = {}
    with httpx.Client(timeout=30,trust_env=False,follow_redirects=False) as client:
        def fetch(skip):
            for attempt in range(3):
                reply = client.get(url,params={"$skip":skip},headers=headers)
                if reply.status_code in (429,500,502,503,504) and attempt<2:
                    time.sleep(2**attempt)
                    continue
                reply.raise_for_status()
                rows = reply.json().get("value")
                if not isinstance(rows,list):
                    raise ValueError("invalid LTA page")
                return skip,rows
        start = 0
        with ThreadPoolExecutor(workers) as pool:
            while start<max_pages*PAGE_SIZE:
                batch = list(pool.map(fetch,range(start,start+workers*PAGE_SIZE,PAGE_SIZE)))
                for skip,rows in batch:
                    if rows:
                        pages[skip] = [link for link in map(_link,rows) if link]
                if any(len(rows)<PAGE_SIZE for _,rows in batch):
                    break
                start += workers*PAGE_SIZE
    if not pages:
        raise RuntimeError("LTA returned no speed band pages")
    payload = {"created":time.time(),"page_size":PAGE_SIZE,"endpoint":ENDPOINT,
        "pages":[{"skip":skip,"links":pages[skip]} for skip in sorted(pages)]}
    path = index_path(settings)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary = tempfile.mkstemp(dir=path.parent,suffix=".tmp")
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            json.dump(payload,stream,separators=(",",":"))
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    with _lock:
        _loaded.pop(str(path),None)
    return {"pages":len(pages),"links":sum(len(v) for v in pages.values())}


class LinkIndex:
    def __init__(self, payload):
        self.created = payload["created"]
        self.page_ids, lines, owners = {}, [], []
        for page in payload["pages"]:
            self.page_ids[page["skip"]] = {link[0] for link in page["links"]}
            for ident,slat,slon,elat,elon in page["links"]:
                line = LineString([PROJECT(slon,slat),PROJECT(elon,elat)])
                if line.length>0:
                    lines.append(line)
                    owners.append(page["skip"])
        self.lines, self.owners = lines, owners
        self.tree = STRtree(lines) if lines else None

    def age_s(self):
        return time.time()-self.created

    def pages_for(self, geometries, buffer_m=25):
        """Page offsets listing a link within the matching buffer of any geometry.

        Pages are ordered by the first geometry needing them, so callers that
        pass candidates best-first get complete coverage for those first.
        """
        needed = {}
        if self.tree is None:
            return []
        for coords in geometries:
            if len(coords)<2:
                continue
            area = LineString([PROJECT(*p[:2]) for p in coords]).buffer(buffer_m)
            pages = sorted({self.owners[int(i)] for i in self.tree.query(area) if self.lines[int(i)].intersects(area)})
            for page in pages:
                needed.setdefault(page,None)
        return list(needed)

    def page_matches(self, skip, rows):
        expected = self.page_ids.get(skip)
        return expected is not None and expected=={str(r.get("LinkID","")) for r in rows}


def load_index(settings):
    path = index_path(settings)
    try:
        stamp = path.stat().st_mtime
    except FileNotFoundError:
        return None
    with _lock:
        cached = _loaded.get(str(path))
        if cached and cached[0]==stamp:
            return cached[1]
        try:
            index = LinkIndex(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError,KeyError,TypeError):
            return None
        _loaded[str(path)] = (stamp,index)
        return index


_building = threading.Event()


def ensure_index_async(settings, max_age_s=7*86400):
    """Start one background maintenance build when the index is missing or old."""
    if not settings.credential("LTA_API_KEY") or _building.is_set():
        return False
    path = index_path(settings)
    if path.exists() and time.time()-path.stat().st_mtime<max_age_s:
        return False
    _building.set()
    def run():
        try:
            build_index(settings)
        except Exception:
            # Trips keep using sequential paging; the next app start retries.
            pass
        finally:
            _building.clear()
    threading.Thread(target=run,name="lta-link-index",daemon=True).start()
    return True
