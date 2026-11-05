#!/usr/bin/env python3
"""PortaSplit-Bot: prüft Shops auf die Midea PortaSplit und meldet Treffer per Telegram.

Aufruf:
    python checker.py                 normaler Lauf (prüfen, melden, Zustand speichern)
    python checker.py --trocken       nur prüfen und anzeigen, nichts senden/speichern
    python checker.py --test-telegram Testnachricht an Telegram schicken
    python checker.py --chat-id       deine Telegram-Chat-ID anzeigen (vorher dem Bot schreiben)
    python checker.py --karte _site   deutschlandweite Übersichtsseite erzeugen
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests
import yaml

BASIS = Path(__file__).resolve().parent
CONFIG_DATEI = BASIS / "config.yaml"
GEHEIM_DATEI = BASIS / "geheim.env"  # nur für den lokalen Betrieb, nie hochladen

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
TIMEOUT = 25

TOOM = "https://toom.de"
OBI = "https://www.obi.de"

# "InStoreOnly" zählt bewusst NICHT: Baumärkte melden das auch bei leerem Lager.
VERFUEGBAR = {"instock", "limitedavailability", "onlineonly"}
VORBESTELLUNG = {"preorder", "backorder", "presale"}


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

class QuellenFehler(Exception):
    """Eine Quelle konnte nicht abgefragt werden."""


def jetzt() -> datetime:
    return datetime.now(timezone.utc)


def oeffentlich() -> bool:
    """Auf GitHub Actions sind die Logs öffentlich: dann nichts ausgeben, was den Standort verrät."""
    return os.environ.get("GITHUB_ACTIONS") == "true"


def zustand_ordner() -> Path:
    return Path(os.environ.get("ZUSTAND_ORDNER") or BASIS)


def log(text: str) -> None:
    print(text, flush=True)


def lade_geheimnisse() -> None:
    """Liest geheim.env (KEY=VALUE) für den lokalen Betrieb, ohne bestehende Variablen zu überschreiben."""
    if not GEHEIM_DATEI.exists():
        return
    for zeile in GEHEIM_DATEI.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        k, v = zeile.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def entfernung_km(lat1, lon1, lat2, lon2) -> float | None:
    if None in (lat1, lon1, lat2, lon2):
        return None
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def zahl(v) -> float | None:
    """Preis aus Zahl oder Text ("1.299,00", "799.99") lesen."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    s = re.sub(r"[^0-9.,]", "", str(v))
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        f = float(s)
    except ValueError:
        return None
    return f if f > 0 else None


def euro(p: float | None) -> str:
    if p is None:
        return ""
    return f"{p:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


class Http:
    """requests-Session mit Drosselung pro Quelle."""

    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": BROWSER_UA, "Accept-Language": "de-DE,de;q=0.9"})
        self._letzte: dict[str, float] = {}

    def warte(self, gruppe: str, abstand: float) -> None:
        dt = self._letzte.get(gruppe, 0) + abstand - time.monotonic()
        if dt > 0:
            time.sleep(dt)
        self._letzte[gruppe] = time.monotonic()

    def get(self, url, gruppe, abstand=0.5, **kw):
        self.warte(gruppe, abstand)
        return self.s.get(url, timeout=TIMEOUT, **kw)

    def post(self, url, gruppe, abstand=0.5, **kw):
        self.warte(gruppe, abstand)
        return self.s.post(url, timeout=TIMEOUT, **kw)


def json_antwort(r: requests.Response, was: str):
    if r.status_code in (403, 429):
        raise QuellenFehler(f"{was}: HTTP {r.status_code} (gesperrt oder zu viele Anfragen)")
    if r.status_code >= 400:
        raise QuellenFehler(f"{was}: HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError:
        raise QuellenFehler(f"{was}: keine JSON-Antwort (Bot-Schutz oder Wartung?)")


# ---------------------------------------------------------------------------
# Märkte laden und Standort bestimmen
# ---------------------------------------------------------------------------

def toom_maerkte(http: Http) -> list[dict]:
    daten = json_antwort(http.get(f"{TOOM}/public/api/markets", "toom", headers={"Accept": "application/json"}), "toom Marktliste")
    out = []
    for m in daten.get("markets", []):
        a = m.get("address") or {}
        try:
            out.append({
                "id": int(m["id"]), "name": str(m.get("name", "")).strip(),
                "plz": str(a.get("zip", "")), "ort": str(a.get("city", "")), "strasse": str(a.get("street", "")),
                "lat": zahl_oder_none(a.get("latitude")), "lon": zahl_oder_none(a.get("longitude")),
                "telefon": str(m.get("phone") or ""),
                "link": f"{TOOM}/{str(m.get('link') or '').lstrip('/')}",
            })
        except (KeyError, TypeError, ValueError):
            continue
    if not out:
        raise QuellenFehler("toom Marktliste leer")
    return out


def obi_maerkte(http: Http) -> list[dict]:
    daten = json_antwort(http.get(f"{OBI}/api/disc/store/locator/country/de", "obi", headers={"Accept": "application/json"}), "OBI Marktliste")
    out = []
    for m in daten.get("stores", []):
        if not m.get("isActive", True) or not m.get("storeNumber"):
            continue
        a = m.get("address") or {}
        out.append({
            "id": str(m["storeNumber"]), "name": str(m.get("name", "")).strip(),
            "plz": str(a.get("zip") or m.get("zip") or ""), "ort": str(a.get("city") or m.get("city") or ""),
            "strasse": str(a.get("street") or m.get("street") or ""),
            "lat": zahl_oder_none(a.get("lat", m.get("lat"))), "lon": zahl_oder_none(a.get("lon", m.get("lon"))),
            "telefon": str(m.get("phone") or ""),
            "link": m.get("url") or f"{OBI}{m.get('path', '')}",
        })
    if not out:
        raise QuellenFehler("OBI Marktliste leer")
    return out


def zahl_oder_none(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def bestimme_standort(cfg: dict, maerkte: list[dict]) -> tuple[float | None, float | None, str]:
    """Standort aus dem Secret/der Variable STANDORT ("52.52,13.40" oder "12345"), sonst aus config.yaml."""
    roh = os.environ.get("STANDORT", "").strip()
    st = cfg.get("standort") or {}
    quelle = "Secret STANDORT" if roh else "config.yaml"
    if roh:
        teile = [t.strip() for t in roh.replace(";", ",").split(",")]
        if len(teile) == 2:
            try:
                return float(teile[0]), float(teile[1]), f"Koordinaten aus {quelle}"
            except ValueError:
                return None, None, "STANDORT hat ein ungültiges Format (erwartet z. B. 52.52,13.40 oder 12345)"
        plz = roh
    elif st.get("lat") is not None and st.get("lon") is not None:
        return float(st["lat"]), float(st["lon"]), f"Koordinaten aus {quelle}"
    else:
        plz = str(st.get("plz") or "").strip()
    if not plz:
        return None, None, "kein Standort angegeben"
    # Näherung: Mittelpunkt der Märkte mit möglichst langem gleichen PLZ-Anfang
    for laenge in range(5, 1, -1):
        treffer = [(m["lat"], m["lon"]) for m in maerkte if m["plz"].startswith(plz[:laenge]) and m["lat"] is not None]
        if treffer:
            lat = sum(t[0] for t in treffer) / len(treffer)
            lon = sum(t[1] for t in treffer) / len(treffer)
            return lat, lon, f"aus PLZ in {quelle} geschätzt (über Märkte mit gleichem PLZ-Anfang)"
    return None, None, f"PLZ aus {quelle} nicht zuzuordnen"


def im_umkreis(maerkte: list[dict], lat, lon, umkreis: float) -> list[dict]:
    for m in maerkte:
        m["entfernung"] = entfernung_km(lat, lon, m["lat"], m["lon"])
    if not umkreis or lat is None:
        return maerkte
    return [m for m in maerkte if m["entfernung"] is not None and m["entfernung"] <= umkreis]


# ---------------------------------------------------------------------------
# Quellen
# ---------------------------------------------------------------------------

def toom_bestaende(http: Http, maerkte: list[dict], artikel: list[str]) -> dict[tuple, int | None]:
    """Bestand je (Markt-ID, Artikel). None = Abfrage für diesen Markt fehlgeschlagen."""
    out: dict[tuple, int | None] = {}
    fehler, erfolge = 0, 0
    body = [{"articleId": a, "quantity": 10, "deliveryType": "marketPickup_marketPickup"} for a in artikel]
    for m in maerkte:
        cookies = {"market_id": str(m["id"]), "market_name": quote(m["name"])}
        try:
            r = http.post(f"{TOOM}/shop/rest/V1/toom/stocks/availability", "toom", json=body, cookies=cookies,
                          headers={"Accept": "application/json", "Content-Type": "application/json"})
            daten = json_antwort(r, "toom Bestand")
        except (requests.RequestException, QuellenFehler) as e:
            fehler += 1
            log(f"  toom-Markt: {e}" if oeffentlich() else f"  toom {m['name']}: {e}")
            if fehler >= 5 and erfolge == 0:  # früh abbrechen, wenn von Anfang an alles scheitert
                raise QuellenFehler(f"toom: {e}")
            for a in artikel:
                out[(m["id"], a)] = None
            continue
        erfolge += 1
        for a in artikel:
            out[(m["id"], a)] = 0
        for x in daten if isinstance(daten, list) else []:
            out[(m["id"], str(x.get("articleId")))] = int(x.get("availableQuantity") or 0)
    if maerkte and erfolge == 0:
        raise QuellenFehler("toom: alle Märkte fehlgeschlagen")
    return out


def obi_bestaende(http: Http, maerkte: list[dict], artikel: list[str], streng: bool = True) -> dict[tuple, int | None]:
    """Bestand je (Markt-Nummer, Artikel). streng=True: jeder Fehler bricht ab."""
    out: dict[tuple, int | None] = {}
    ids = [m["id"] for m in maerkte]
    fehler, erfolge = 0, 0
    for sku in artikel:
        for i in range(0, len(ids), 10):  # OBI erlaubt max. 10 Märkte pro Anfrage
            teil = ids[i:i + 10]
            try:
                r = http.get(f"{OBI}/api/pdp/v1/stock/{sku}?storeIds={','.join(teil)}", "obi", headers={"Accept": "application/json"})
                daten = json_antwort(r, "OBI Bestand")
            except (requests.RequestException, QuellenFehler) as e:
                if streng:
                    raise QuellenFehler(str(e))
                fehler += 1
                if fehler >= 3 and erfolge == 0:
                    raise QuellenFehler(str(e))
                for mid in teil:
                    out[(mid, sku)] = None
                continue
            erfolge += 1
            for mid in teil:
                out[(mid, sku)] = 0
            for x in daten if isinstance(daten, list) else []:
                out[(str(x.get("storeId")), sku)] = int(x.get("availableQuantity") or 0)
    return out


def markt_treffer(kette: str, m: dict, sku: str, menge: int) -> dict:
    if kette == "toom":
        haendler, url = f"toom {m['name']}", f"{TOOM}/s/{sku}"
    else:
        haendler, url = f"OBI {m['name']}", f"{OBI}/p/{sku}"
    return {
        "key": f"{kette}:{m['id']}:{sku}", "quelle": kette, "haendler": haendler,
        "art": "Markt", "menge": menge, "menge_text": "10+" if (kette == "toom" and menge >= 10) else str(menge),
        "adresse": f"{m['strasse']}, {m['plz']} {m['ort']}", "telefon": m["telefon"],
        "entfernung": m.get("entfernung"), "url": url, "markt_url": m["link"], "preis": None,
    }


def pruefe_toom(http: Http, maerkte: list[dict], artikel: list[str]) -> list[dict]:
    nach_id = {m["id"]: m for m in maerkte}
    return [markt_treffer("toom", nach_id[mid], sku, n)
            for (mid, sku), n in toom_bestaende(http, maerkte, artikel).items() if n and mid in nach_id]


def pruefe_obi(http: Http, maerkte: list[dict], artikel: list[str]) -> list[dict]:
    nach_id = {m["id"]: m for m in maerkte}
    return [markt_treffer("obi", nach_id[mid], sku, n)
            for (mid, sku), n in obi_bestaende(http, maerkte, artikel).items() if n and mid in nach_id]


LD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)


def _verfuegbarkeit(v) -> str:
    return str(v or "").lower().replace("\\/", "/").rsplit("/", 1)[-1].strip()


def angebote_aus_jsonld(seite: str) -> list[tuple[str, float | None]]:
    """Liefert [(verfügbarkeit, preis)] aus allen schema.org-Offers der Seite."""
    out: list[tuple[str, float | None]] = []

    def besuche(o):
        if isinstance(o, list):
            for x in o:
                besuche(x)
        elif isinstance(o, dict):
            if "availability" in o:
                preis = zahl(o.get("price") if o.get("price") is not None else o.get("lowPrice"))
                if preis is None and isinstance(o.get("priceSpecification"), dict):
                    preis = zahl(o["priceSpecification"].get("price"))
                out.append((_verfuegbarkeit(o["availability"]), preis))
            for v in o.values():
                if isinstance(v, (dict, list)):
                    besuche(v)

    for block in LD_RE.findall(seite):
        try:
            besuche(json.loads(block.strip(), strict=False))
        except ValueError:
            for v in re.findall(r'"availability"\s*:\s*"([^"]+)"', block):
                out.append((_verfuegbarkeit(v), None))
    # Mikrodaten (itemprop="price"/"availability") als Rückfall bzw. für fehlende Preise
    mikro_preis = None
    m = re.search(r'itemprop=["\']price["\'][^>]*content=["\']([0-9][0-9.,]*)', seite, re.I) or \
        re.search(r'content=["\']([0-9][0-9.,]*)["\'][^>]*itemprop=["\']price["\']', seite, re.I)
    if m:
        mikro_preis = zahl(m.group(1))
    if not out:
        for v in re.findall(r'itemprop=["\']availability["\'][^>]*(?:href|content)=["\']([^"\']+)', seite, re.I):
            out.append((_verfuegbarkeit(v), mikro_preis))
    elif mikro_preis is not None:
        out = [(v, p if p is not None else mikro_preis) for v, p in out]
    return out


def _umgeleitet_auf_anderes(angefragt: str, endgueltig: str) -> bool:
    """True, wenn der Shop vom Produkt weg (z. B. auf eine Kategorie) umgeleitet hat."""
    a, z = urlsplit(angefragt), urlsplit(endgueltig)
    if a.path.rstrip("/") == z.path.rstrip("/"):
        return False
    letzter = a.path.rstrip("/").rsplit("/", 1)[-1].lower()
    nummern = re.findall(r"\d{4,}", letzter)
    if nummern:
        return not any(n in endgueltig for n in nummern)
    return letzter not in z.path.lower()


def pruefe_shopseite(http: Http, shop: dict, max_preis: float) -> tuple[str, dict | None]:
    """Gibt (Statustext, Treffer oder None) zurück. Wirft QuellenFehler bei technischen Problemen."""
    try:
        r = http.get(shop["url"], "web", abstand=1.0, headers={"Accept": "text/html,application/xhtml+xml"})
    except requests.RequestException as e:
        raise QuellenFehler(f"nicht erreichbar ({type(e).__name__})")
    if r.status_code in (404, 410):
        return "Seite nicht gefunden (404) – Link prüfen", None
    if r.status_code in (403, 429, 503):
        raise QuellenFehler(f"HTTP {r.status_code} (Bot-Schutz?)")
    if r.status_code >= 400:
        raise QuellenFehler(f"HTTP {r.status_code}")
    seite = r.text
    if r.history and _umgeleitet_auf_anderes(shop["url"], r.url):
        return "Produkt nicht (mehr) gelistet – Shop leitet woanders hin", None
    pruefwort = str(shop.get("pruefwort") or "").strip()
    if pruefwort and pruefwort.lower() not in seite.lower():
        return "Produkt nicht (mehr) auf der Seite", None

    if shop.get("typ") == "amazon":
        if "validatecaptcha" in seite.lower() or "api-services-support@amazon.com" in seite:
            raise QuellenFehler("Amazon zeigt ein Captcha (Bot-Schutz)")
        if 'id="add-to-cart-button"' not in seite:
            return "nicht verfügbar", None
        m = re.search(r'class="a-offscreen">\s*([0-9.,]+)\s*(?:&nbsp;)?€', seite)
        angebote = [("instock", zahl(m.group(1)) if m else None)]
    else:
        angebote = angebote_aus_jsonld(seite)
        if not angebote:
            return "keine Verfügbarkeitsdaten auf der Seite", None

    lieferbar = [p for v, p in angebote if v in VERFUEGBAR]
    vorbest = [p for v, p in angebote if v in VORBESTELLUNG]
    kandidaten, vorbestellung = (lieferbar, False) if lieferbar else (vorbest, True)
    if lieferbar and re.search(r"vorbestellung möglich|vorbestellbar|jetzt vorbestellen", seite, re.I):
        vorbestellung = True  # manche Shops melden "InStock", schreiben aber "Vorbestellung" dazu
    if not kandidaten:
        arten = sorted({v for v, _ in angebote})
        return f"nicht verfügbar ({', '.join(arten)})", None
    preise = [p for p in kandidaten if p is not None]
    preis = min(preise) if preise else None
    if max_preis and preis is not None and preis > max_preis:
        return f"verfügbar, aber zu teuer ({euro(preis)})", None
    treffer = {
        "key": f"web:{shop['name']}", "quelle": "online", "haendler": shop["name"], "art": "Online",
        "menge": 1, "menge_text": "", "adresse": "", "telefon": "", "entfernung": None,
        "url": shop["url"], "markt_url": "", "preis": preis, "vorbestellung": vorbestellung,
    }
    return ("Vorbestellung möglich" if vorbestellung else "VERFÜGBAR") + (f" ({euro(preis)})" if preis else ""), treffer


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def telegram(text: str) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        if oeffentlich():
            log("  Telegram nicht eingerichtet: Secrets TELEGRAM_BOT_TOKEN und TELEGRAM_CHAT_ID fehlen!")
        else:
            log("  (Telegram nicht eingerichtet – Nachricht nur hier angezeigt)")
            log(text)
        return
    # Telegram erlaubt max. 4096 Zeichen pro Nachricht
    teile, aktuell = [], ""
    for zeile in text.split("\n"):
        if len(aktuell) + len(zeile) + 1 > 3900:
            teile.append(aktuell)
            aktuell = ""
        aktuell += zeile + "\n"
    teile.append(aktuell)
    for teil in teile:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=TIMEOUT, data={
            "chat_id": chat, "text": teil, "parse_mode": "HTML", "disable_web_page_preview": "true"})
        if r.status_code != 200:
            raise RuntimeError(f"Telegram-Fehler {r.status_code}: {r.text[:200]}")


def e(s) -> str:
    return html.escape(str(s or ""))


def nachricht_treffer(neu: list[dict]) -> str:
    neu = sorted(neu, key=lambda t: (t["entfernung"] is None, t["entfernung"] or 0))
    zeilen = ["🟢 <b>Midea PortaSplit verfügbar!</b>", ""]
    for t in neu[:40]:
        kopf = f"<b>{e(t['haendler'])}</b>"
        if t.get("entfernung") is not None:
            kopf += f" · {t['entfernung']:.0f} km"
        details = []
        if t.get("menge_text"):
            details.append(f"{e(t['menge_text'])} Stück")
        if t.get("preis"):
            details.append(euro(t["preis"]))
        if t.get("vorbestellung"):
            details.append("Vorbestellung")
        zeilen.append(kopf + (" – " + ", ".join(details) if details else ""))
        if t.get("adresse"):
            zeilen.append(e(t["adresse"]) + (f" · ☎ {e(t['telefon'])}" if t.get("telefon") else ""))
        zeilen.append(f'<a href="{e(t["url"])}">Zum Produkt</a>' + (f' · <a href="{e(t["markt_url"])}">Markt</a>' if t.get("markt_url") else ""))
        zeilen.append("")
    if len(neu) > 40:
        zeilen.append(f"… und {len(neu) - 40} weitere.")
    if any(t["art"] == "Markt" for t in neu):
        zeilen.append("Tipp: Marktbestände ändern sich schnell – am besten vorher anrufen.")
    return "\n".join(zeilen).strip()


# ---------------------------------------------------------------------------
# Hauptablauf
# ---------------------------------------------------------------------------

def lade_json(pfad: Path, standard):
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return standard


def schreibe_json(pfad: Path, daten) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(json.dumps(daten, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def lauf(trocken: bool) -> int:
    cfg = yaml.safe_load(CONFIG_DATEI.read_text(encoding="utf-8")) or {}
    umkreis = float(cfg.get("umkreis_km") or 0)
    max_preis = float(cfg.get("max_preis") or 0)
    warn_nach = int(cfg.get("fehler_warnung_nach") or 6)
    http = Http()

    zustand_datei = zustand_ordner() / "state.json"
    status_datei = zustand_ordner() / "status.json"
    zustand = lade_json(zustand_datei, {})
    alte_angebote: dict = zustand.get("angebote", {})
    fehlerzaehler: dict = zustand.get("fehler", {})
    gewarnt: dict = zustand.get("gewarnt", {})

    quellen: dict[str, dict] = {}       # Name -> {"ok": bool, "info": str, "treffer": [...] }
    shop_status: list[dict] = []

    # Marktlisten
    t_maerkte, o_maerkte = [], []
    toom_cfg, obi_cfg = cfg.get("toom") or {}, cfg.get("obi") or {}
    if toom_cfg.get("aktiv", True):
        try:
            t_maerkte = toom_maerkte(http)
        except (requests.RequestException, QuellenFehler) as ex:
            quellen["toom"] = {"ok": False, "info": str(ex), "treffer": []}
    if obi_cfg.get("aktiv", True):
        try:
            o_maerkte = obi_maerkte(http)
        except (requests.RequestException, QuellenFehler) as ex:
            quellen["obi"] = {"ok": False, "info": str(ex), "treffer": []}

    lat, lon, standort_info = bestimme_standort(cfg, t_maerkte + o_maerkte)
    if oeffentlich():
        log(f"Standort: {'gesetzt' if lat is not None else 'FEHLT'} ({standort_info.split(' (')[0]}; Details verborgen, weil die Logs öffentlich sind)")
    else:
        log(f"Standort: {standort_info}" + (f" ({lat:.3f}, {lon:.3f})" if lat is not None else ""))
    if umkreis and lat is None:
        log("  Achtung: Standort unbekannt – prüfe alle Märkte in Deutschland. Secret STANDORT setzen!")

    if t_maerkte:
        auswahl = im_umkreis(t_maerkte, lat, lon, umkreis)
        log(f"toom: prüfe {len(auswahl)} Märkte …")
        try:
            tr = pruefe_toom(http, auswahl, [str(a) for a in toom_cfg.get("artikel", ["9350668"])])
            quellen["toom"] = {"ok": True, "info": f"{len(auswahl)} {'Markt' if len(auswahl) == 1 else 'Märkte'} geprüft", "treffer": tr}
        except (requests.RequestException, QuellenFehler) as ex:
            quellen["toom"] = {"ok": False, "info": str(ex), "treffer": []}

    if o_maerkte:
        auswahl = im_umkreis(o_maerkte, lat, lon, umkreis)
        log(f"OBI: prüfe {len(auswahl)} Märkte …")
        try:
            tr = pruefe_obi(http, auswahl, [str(a) for a in obi_cfg.get("artikel", ["8620890"])])
            quellen["obi"] = {"ok": True, "info": f"{len(auswahl)} {'Markt' if len(auswahl) == 1 else 'Märkte'} geprüft", "treffer": tr}
        except (requests.RequestException, QuellenFehler) as ex:
            quellen["obi"] = {"ok": False, "info": str(ex), "treffer": []}

    shops = [sh for sh in (cfg.get("online_shops") or []) if sh.get("aktiv", True)]
    if shops:
        log(f"Online-Shops: prüfe {len(shops)} Seiten …")
    for shop in shops:
        name = f"web:{shop['name']}"
        try:
            text, treffer = pruefe_shopseite(http, shop, max_preis)
            quellen[name] = {"ok": True, "info": text, "treffer": [treffer] if treffer else []}
        except QuellenFehler as ex:
            text = f"Fehler: {ex}"
            quellen[name] = {"ok": False, "info": str(ex), "treffer": []}
        shop_status.append({"name": shop["name"], "url": shop["url"], "status": text})

    # Auswertung
    for name, q in quellen.items():
        log(f"  {'✔' if q['ok'] else '✖'} {name}: {q['info']}" + (f" → {len(q['treffer'])} Treffer" if q["treffer"] else ""))

    neue_angebote: dict = {}
    neu: list[dict] = []
    for name, q in quellen.items():
        if q["ok"]:
            for t in q["treffer"]:
                vorher = alte_angebote.get(t["key"])
                t["seit"] = vorher["seit"] if vorher else jetzt().isoformat(timespec="seconds")
                neue_angebote[t["key"]] = t
                if not vorher:
                    neu.append(t)
        else:  # bei Fehlern alte Treffer behalten, damit es nach der Störung keine Doppelmeldung gibt
            praefix = name if name.startswith("web:") else f"{name}:"
            for k, v in alte_angebote.items():
                if k == name or k.startswith(praefix):
                    neue_angebote[k] = v

    warnungen = []
    for name, q in quellen.items():
        if q["ok"]:
            if gewarnt.get(name):
                warnungen.append(f"✅ <b>{e(name.removeprefix('web:'))}</b> funktioniert wieder.")
            fehlerzaehler.pop(name, None)
            gewarnt.pop(name, None)
        else:
            fehlerzaehler[name] = fehlerzaehler.get(name, 0) + 1
            if fehlerzaehler[name] >= warn_nach and not gewarnt.get(name):
                warnungen.append(f"⚠️ <b>{e(name.removeprefix('web:'))}</b> schlägt seit {fehlerzaehler[name]} Läufen fehl: {e(q['info'])}")
                gewarnt[name] = True

    log(f"Ergebnis: {len(neue_angebote)} Angebote aktuell, davon {len(neu)} neu.")

    if trocken:
        if neu:
            log("\n--- Diese Nachricht würde gesendet: ---\n" + nachricht_treffer(neu))
        for w in warnungen:
            log("Warnung: " + w)
        return 0

    if neu:
        telegram(nachricht_treffer(neu))
    if warnungen:
        telegram("\n".join(["<b>PortaSplit-Bot: Hinweis</b>", ""] + warnungen))

    zustand = {"angebote": neue_angebote, "fehler": fehlerzaehler, "gewarnt": gewarnt}
    schreibe_json(zustand_datei, zustand)

    status = {
        "standort": standort_info, "umkreis_km": umkreis, "max_preis": max_preis,
        "quellen": [{"name": n, "ok": q["ok"], "info": q["info"], "treffer": len(q["treffer"])}
                    for n, q in quellen.items() if not n.startswith("web:")],
        "shops": shop_status,
        "angebote": sorted(neue_angebote.values(), key=lambda t: (t.get("entfernung") is None, t.get("entfernung") or 0)),
    }
    schreibe_json(status_datei, {"aktualisiert": jetzt().isoformat(timespec="seconds"), **status})
    return 0


PBKDF2_RUNDEN = 310_000


def verschluessele(klartext: bytes, passwort: str) -> dict:
    """AES-256-GCM mit einem per PBKDF2-SHA256 aus dem Passwort abgeleiteten Schlüssel.

    Das Format passt zur WebCrypto-Entschlüsselung in karte/index.html. Das Salz ist pro Repository fest,
    damit ein im Browser gemerkter Schlüssel auch nach dem nächsten Lauf noch passt.
    """
    import base64
    import hashlib
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    salz = hashlib.sha256(("portasplit-radar:" + os.environ.get("GITHUB_REPOSITORY", "lokal")).encode()).digest()[:16]
    schluessel = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salz, iterations=PBKDF2_RUNDEN).derive(passwort.encode("utf-8"))
    iv = os.urandom(12)
    ct = AESGCM(schluessel).encrypt(iv, klartext, None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return {"v": 1, "runden": PBKDF2_RUNDEN, "salz": b64(salz), "iv": b64(iv), "daten": b64(ct)}


def karte(ausgabe: Path) -> int:
    """Deutschlandweite Übersicht für die Webseite: alle toom- und OBI-Märkte + Online-Shops.

    Schreibt <ausgabe>/daten.json und kopiert karte/index.html dazu. Enthält keine Standortdaten des Nutzers;
    Entfernungen berechnet die Webseite im Browser.
    """
    cfg = yaml.safe_load(CONFIG_DATEI.read_text(encoding="utf-8")) or {}
    max_preis = float(cfg.get("max_preis") or 0)
    http = Http()
    verlauf_datei = zustand_ordner() / "verlauf.json"
    verlauf = lade_json(verlauf_datei, {})
    alt_maerkte: dict = verlauf.get("maerkte", {})
    alt_shops: dict = verlauf.get("shops", {})
    ereignisse: list = verlauf.get("ereignisse", [])
    zeit = jetzt().isoformat(timespec="seconds")

    neue_maerkte: dict = {}
    quellen = []
    ketten = [
        ("toom", cfg.get("toom") or {}, toom_maerkte, toom_bestaende, ["9350668"], f"{TOOM}/s/"),
        ("obi", cfg.get("obi") or {}, obi_maerkte, lambda h, m, a: obi_bestaende(h, m, a, streng=False), ["8620890"], f"{OBI}/p/"),
    ]
    produkte = {}
    for kette, kcfg, lade, bestand_fn, std_artikel, produkt_basis in ketten:
        if not kcfg.get("aktiv", True):
            continue
        artikel = [str(a) for a in kcfg.get("artikel", std_artikel)]
        produkte[kette] = produkt_basis + artikel[0]
        try:
            ms = lade(http)
            log(f"{kette}: prüfe alle {len(ms)} Märkte …")
            best = bestand_fn(http, ms, artikel)
        except (requests.RequestException, QuellenFehler) as ex:
            quellen.append({"name": kette, "ok": False, "info": str(ex)})
            for k, v in alt_maerkte.items():  # letzte bekannte Daten behalten
                if k.startswith(kette + ":"):
                    neue_maerkte[k] = v
            continue
        mit_bestand, unbekannt = 0, 0
        for m in ms:
            key = f"{kette}:{m['id']}"
            werte = [best.get((m["id"], a)) for a in artikel]
            menge = sum(w for w in werte if w) if any(w for w in werte) else (None if None in werte else 0)
            alt = alt_maerkte.get(key, {})
            eintrag = {
                "kette": kette, "id": m["id"], "name": m["name"], "strasse": m["strasse"], "plz": m["plz"], "ort": m["ort"],
                "lat": m["lat"], "lon": m["lon"], "telefon": m["telefon"], "link": m["link"],
                "bestand": menge if menge is not None else alt.get("bestand"),
                "geprueft": zeit if menge is not None else alt.get("geprueft"),
                "zuletzt_bestand": alt.get("zuletzt_bestand"), "zuletzt_menge": alt.get("zuletzt_menge"),
            }
            if menge is None:
                unbekannt += 1
            elif menge > 0:
                mit_bestand += 1
                eintrag["zuletzt_bestand"], eintrag["zuletzt_menge"] = zeit, menge
                if not alt.get("bestand"):
                    ereignisse.append({"zeit": zeit, "art": "markt", "kette": kette, "key": key, "name": m["name"],
                                       "ort": m["ort"], "plz": m["plz"], "lat": m["lat"], "lon": m["lon"], "menge": menge})
            neue_maerkte[key] = eintrag
        info = f"{len(ms)} Märkte geprüft" + (f", {unbekannt} ohne Antwort" if unbekannt else "")
        quellen.append({"name": kette, "ok": True, "info": info, "maerkte": len(ms), "mit_bestand": mit_bestand})

    shops_out, neue_shops = [], {}
    for shop in [sh for sh in (cfg.get("online_shops") or []) if sh.get("aktiv", True)]:
        alt = alt_shops.get(shop["name"], {})
        try:
            text, treffer = pruefe_shopseite(http, shop, max_preis)
            ok = True
        except QuellenFehler as ex:
            text, treffer, ok = f"Fehler: {ex}", None, False
        eintrag = {"name": shop["name"], "url": shop["url"], "status": text, "ok": ok,
                   "verfuegbar": bool(treffer), "preis": treffer["preis"] if treffer else None,
                   "vorbestellung": bool(treffer and treffer.get("vorbestellung")),
                   "zuletzt_verfuegbar": zeit if treffer else alt.get("zuletzt_verfuegbar")}
        if treffer and not alt.get("verfuegbar"):
            ereignisse.append({"zeit": zeit, "art": "online", "name": shop["name"], "url": shop["url"], "preis": eintrag["preis"]})
        if not ok:  # bei Fehlern den letzten Verfügbarkeitsstand behalten
            eintrag["verfuegbar"] = alt.get("verfuegbar", False)
        neue_shops[shop["name"]] = eintrag
        shops_out.append(eintrag)

    # Ereignisse: nur die letzten 30 Tage, höchstens 200
    grenze = (jetzt() - timedelta(days=30)).isoformat(timespec="seconds")
    ereignisse = [e for e in ereignisse if e.get("zeit", "") >= grenze][-200:]

    schreibe_json(verlauf_datei, {"maerkte": neue_maerkte, "shops": neue_shops, "ereignisse": ereignisse})

    daten = {
        "aktualisiert": zeit, "max_preis": max_preis, "produkte": produkte, "quellen": quellen,
        "maerkte": sorted(neue_maerkte.values(), key=lambda m: (m["kette"], m["plz"])),
        "shops": shops_out, "ereignisse": list(reversed(ereignisse)),
    }
    ausgabe.mkdir(parents=True, exist_ok=True)
    klartext = json.dumps(daten, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    passwort = os.environ.get("KARTE_PASSWORT", "")
    for alt in ("daten.json", "daten.enc.json"):
        (ausgabe / alt).unlink(missing_ok=True)
    if passwort:
        (ausgabe / "daten.enc.json").write_text(json.dumps(verschluessele(klartext, passwort)), encoding="utf-8")
        log("Daten verschlüsselt (Passwortschutz aktiv).")
    else:
        (ausgabe / "daten.json").write_bytes(klartext)
        log("Hinweis: Kein KARTE_PASSWORT gesetzt – die Seite ist ohne Passwort für alle lesbar.")
    seite = BASIS / "karte" / "index.html"
    if seite.exists():
        (ausgabe / "index.html").write_text(seite.read_text(encoding="utf-8"), encoding="utf-8")
    for q in quellen:
        log(f"  {'✔' if q['ok'] else '✖'} {q['name']}: {q['info']}" + (f" → {q['mit_bestand']} mit Bestand" if q.get("mit_bestand") else ""))
    log(f"Online-Shops: {sum(1 for s in shops_out if s['verfuegbar'])} von {len(shops_out)} verfügbar (unter Preisgrenze)")
    log(f"Webseite geschrieben nach {ausgabe}")
    return 0


def zeige_chat_id() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        log("Bitte zuerst TELEGRAM_BOT_TOKEN setzen (z. B. in geheim.env).")
        return 1
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=TIMEOUT)
    daten = r.json()
    if not daten.get("ok"):
        log(f"Telegram meldet: {daten}")
        return 1
    chats = {}
    for u in daten.get("result", []):
        msg = u.get("message") or u.get("channel_post") or {}
        c = msg.get("chat") or {}
        if c.get("id") is not None:
            chats[c["id"]] = c.get("first_name") or c.get("title") or c.get("username") or ""
    if not chats:
        log("Keine Nachricht gefunden. Schreib deinem Bot in Telegram zuerst etwas (z. B. 'Hallo') und versuch es nochmal.")
        return 1
    for cid, name in chats.items():
        log(f"Chat-ID: {cid}   ({name})")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Umlaute/Emojis in der Windows-Konsole
    p = argparse.ArgumentParser(description="PortaSplit-Bot")
    p.add_argument("--trocken", action="store_true", help="nur prüfen und anzeigen, nichts senden/speichern")
    p.add_argument("--test-telegram", action="store_true", help="Testnachricht senden")
    p.add_argument("--chat-id", action="store_true", help="Telegram-Chat-ID anzeigen")
    p.add_argument("--karte", metavar="ORDNER", help="deutschlandweite Übersichtsseite in ORDNER erzeugen")
    a = p.parse_args()
    lade_geheimnisse()
    if a.chat_id:
        return zeige_chat_id()
    if a.karte:
        return karte(Path(a.karte))
    if a.test_telegram:
        telegram("✅ <b>PortaSplit-Bot</b>: Telegram funktioniert! Du bekommst hier eine Nachricht, sobald eine PortaSplit auftaucht.")
        log("Testnachricht gesendet.")
        return 0
    return lauf(a.trocken)


if __name__ == "__main__":
    sys.exit(main())
