# PortaSplit-Bot 🌬️

Prüft alle 10 Minuten, ob die **Midea PortaSplit 12.000 BTU** (mit Heizfunktion) irgendwo verfügbar ist, und schickt dir eine **Telegram-Nachricht**. Er läuft kostenlos auf GitHub Actions, dein PC muss dafür nicht an sein.

**Was geprüft wird**
- **toom:** echter Bestand in jedem Markt im Umkreis (Stückzahl, Adresse, Telefon)
- **OBI:** echter Bestand in jedem Markt im Umkreis
- **Online-Shops:** MediaMarkt, Saturn, Hagebau, Euronics, Amazon und rund 15 kleinere Shops

**Was du bekommst**
- Eine Telegram-Nachricht, sobald etwas neu auftaucht. Pro Angebot kommt nur eine Meldung; ist es weg und kommt wieder, gibt es eine neue.
- Eine Warnung, wenn eine Quelle länger nicht funktioniert, z. B. weil ein Shop den Bot sperrt.

---

## Einrichtung (ca. 15 Minuten)

### 1. Telegram-Bot anlegen
1. Öffne in Telegram den Chat mit **@BotFather** und sende `/newbot`.
2. Vergib einen Namen und einen Benutzernamen, der auf `bot` endet, z. B. `meinportasplit_bot`.
3. BotFather schickt dir einen **Token**, etwa `123456789:AAH...`. Gut aufheben und niemandem zeigen.
4. Öffne deinen neuen Bot und schreib ihm „Hallo“.
5. Öffne im Browser diese Adresse und setze deinen Token ein:
   `https://api.telegram.org/bot<DEIN_TOKEN>/getUpdates`
   Dort steht `"chat":{"id":123456789,...`. Diese Zahl ist deine **Chat-ID**.

### 2. Einstellungen prüfen
In `config.yaml` stehen `umkreis_km` (Standard: 50 km; `0` = ganz Deutschland) und `max_preis` (Standard: 1.000 €). Dein **Standort steht absichtlich nicht dort**, sondern kommt in Schritt 4 als Secret dazu, weil das Repo öffentlich ist.

### 3. Auf GitHub hochladen
1. Erstelle ein Konto auf [github.com](https://github.com), falls du noch keins hast.
2. Klicke oben rechts auf **+ → New repository**.
   - Name: z. B. `portasplit-bot`
   - Sichtbarkeit: **Public**. Nur öffentliche Repos haben unbegrenzt kostenlose Actions-Minuten; bei privaten reichen die 2.000 Freiminuten im Monat nicht für einen 10-Minuten-Takt. Token, Chat-ID und Standort liegen in den Secrets und bleiben trotzdem geheim.
3. Klicke im neuen Repo auf **uploading an existing file** und zieh **den gesamten Inhalt dieses Ordners** hinein, einschließlich des Ordners `.github`. **Nicht** hochladen: `geheim.env`, falls du die Datei angelegt hast.
4. Klicke auf **Commit changes**.

> Tipp: Falls der Ordner `.github` beim Hochladen fehlt, lege die Datei manuell an: **Add file → Create new file**, als Namen `.github/workflows/check.yml` eingeben und den Inhalt aus der Datei einfügen.

### 4. Geheimnisse hinterlegen
Im Repo unter **Settings → Secrets and variables → Actions → New repository secret** drei Einträge anlegen:

| Name | Wert |
|---|---|
| `TELEGRAM_BOT_TOKEN` | dein Token aus Schritt 1 |
| `TELEGRAM_CHAT_ID` | deine Chat-ID aus Schritt 1 |
| `STANDORT` | Koordinaten als `Breite,Länge`, z. B. `52.5200,13.4050`, oder einfach deine PLZ (ungenauer) |

Koordinaten findest du in Google Maps mit einem Rechtsklick auf deinen Ort. Die erste Zeile im Menü ist das Zahlenpaar.

### 5. Starten
1. Öffne den Reiter **Actions**. Falls GitHub fragt, die Workflows aktivieren.
2. Klicke links auf **PortaSplit-Check → Run workflow**.
3. Klicke nach etwa einer Minute auf den Lauf und dann auf **Shops prüfen**. Im Log siehst du jede Quelle mit ✔ (funktioniert) oder ✖ (Fehler) und ob der Standort gesetzt ist. Der Standort selbst und die Namen der Märkte stehen absichtlich nicht im Log, weil die Logs öffentlich sind.

Danach läuft der Bot automatisch alle 10 Minuten. GitHub startet geplante Läufe manchmal ein paar Minuten zu spät; das ist normal.

### Was öffentlich ist und was nicht
- **Öffentlich:** der Code, `config.yaml` (Umkreis, Preisgrenze, Shop-Links) und die Logs. Daraus geht nur hervor, *dass* jemand eine PortaSplit sucht, aber nicht wo.
- **Privat:** Token, Chat-ID und Standort liegen in den Secrets. Gefundene Angebote kommen nur per Telegram zu dir, und der Merkzettel, was schon gemeldet wurde, liegt im internen Cache von GitHub statt im Repo.

---

## Übersichtsseite mit Karte (optional)

Eine Webseite ähnlich wie braucheklima.de: eine Karte mit **allen toom- und OBI-Märkten in Deutschland**, jeweils mit Bestand und Telefonnummer. Dazu zeigt sie, wann ein Markt zuletzt Bestand hatte, was zuletzt neu aufgetaucht ist, und den Stand der Online-Shops. Sie wird etwa alle 30 Minuten aktualisiert und ist kostenlos.

**Datenschutz:** Die Seite enthält nur deutschlandweite Daten. Deinen Standort gibst du auf der Seite ein. Er bleibt in deinem Browser, und Entfernungen und Umkreis werden dort berechnet. Nur die Suche nach einer PLZ fragt kurz bei OpenStreetMap nach, wo diese liegt.

**Einrichten**
1. Lade den Ordner `karte` (mit `index.html`) und die neue `checker.py` ins Repo hoch.
2. Lege die Datei `.github/workflows/karte.yml` an, genauso wie `check.yml` (**Add file → Create new file**, Inhalt aus der Datei kopieren).
3. Wähle unter **Settings → Pages →** bei „Source“ **GitHub Actions** aus.
4. Starte unter **Actions → PortaSplit-Karte → Run workflow** den ersten Lauf. Er dauert etwa 4 bis 5 Minuten, weil alle rund 650 Märkte geprüft werden.
5. Die Seite ist danach unter `https://<dein-name>.github.io/<repo-name>/` erreichbar. Den genauen Link zeigt **Settings → Pages**.

**Passwortschutz (empfohlen)**
Lege unter **Settings → Secrets and variables → Actions** ein weiteres Secret `KARTE_PASSWORT` an. Ab dem nächsten Lauf werden die Daten mit AES-256 verschlüsselt, und die Seite fragt nach dem Passwort. Ohne das Passwort sind die Daten nicht lesbar, auch nicht für jemanden, der den Quelltext anschaut. Mit „Auf diesem Gerät merken“ musst du das Passwort pro Gerät nur einmal eingeben.
- Nimm ein **langes** Passwort, z. B. vier zufällige Wörter. Die verschlüsselte Datei ist öffentlich abrufbar, ein kurzes Passwort ließe sich durchprobieren.
- Passwort ändern: einfach das Secret ändern. Ab dem nächsten Lauf gilt das neue, und gemerkte Geräte fragen erneut.

---

## Wenn toom oder OBI den Bot aus GitHub sperren

Manche Shops blocken Anfragen aus Rechenzentren. Im Log steht dann z. B. `✖ toom: HTTP 403`, und nach sechs Fehlversuchen kommt eine Telegram-Warnung. Dann gibt es zwei Möglichkeiten:

**A) Gesperrte Quelle auf deinem Windows-PC laufen lassen**
1. Installiere Python von [python.org](https://www.python.org/downloads/) und setz beim Installieren den Haken bei **„Add python.exe to PATH“**.
2. Öffne den Projektordner und tippe oben in die Adresszeile `cmd`, dann Enter. Danach:
   `pip install -r requirements.txt`
3. Kopier `geheim.env.beispiel` zu `geheim.env` und trag Token, Chat-ID und Standort ein.
4. Doppelklick auf **`test_lauf.bat`**: Das zeigt nur an, was gefunden wird, und sendet nichts.
5. Doppelklick auf **`telegram_test.bat`**: Das schickt eine Testnachricht.
6. Für den Dauerbetrieb im Windows-Startmenü die **Aufgabenplanung** öffnen und unter **Einfache Aufgabe erstellen** Folgendes einstellen: Trigger täglich, danach in den Eigenschaften „Wiederholen alle 10 Minuten“. Als Aktion `pruefen.bat` aus diesem Ordner wählen.

Tipp: In der Cloud-Kopie die gesperrte Quelle in `config.yaml` mit `aktiv: false` abschalten, damit keine doppelten Meldungen kommen.

**B) Raspberry Pi oder kleiner Server zu Hause:** wie A, nur mit `cron` statt Aufgabenplanung.

---

## Shops anpassen

Alle Shops stehen in `config.yaml` unter `online_shops`. Ein neuer Eintrag sieht so aus:

```yaml
  - name: Mein Shop
    url: https://www.beispiel.de/produkt/portasplit
    pruefwort: "4048164116478"   # optional: Text, der auf der Seite stehen muss (z. B. die EAN)
```

Im Log steht pro Shop, was der Bot sieht:

| Anzeige | Bedeutung |
|---|---|
| **VERFÜGBAR** | Bestellbar → du bekommst eine Telegram-Nachricht |
| **verfügbar, aber zu teuer** | Liegt über `max_preis`; es kommt keine Meldung |
| **nicht verfügbar (instoreonly)** | Wird nur im Markt verkauft, online gibt es keinen Bestand |
| **Seite nicht gefunden (404)** | Der Link ist veraltet und muss ersetzt werden |
| **Fehler: HTTP 403 / Captcha** | Der Shop blockt den Bot; das ist bei Amazon üblich |
| **Produkt nicht (mehr) gelistet** | Der Link führt nicht mehr zum Produkt, sondern z. B. auf eine Kategorie |

Shops, die Anfragen von GitHub blockieren (MediaMarkt, Saturn, Euronics, Amazon, Klimafy), sind in `config.yaml` mit `aktiv: false` abgeschaltet. Lässt du den Bot auf deinem PC laufen, kannst du sie dort wieder auf `true` setzen.

Hinweis: Die bisher bekannten Links zu **Hornbach**, **Bauhaus** und **Globus** waren am 05.11.2026 tot, und **expert** liefert keine auswertbaren Daten. Sie sind in `config.yaml` auskommentiert, bis jemand neue findet.

## Befehle

```
python checker.py                 normaler Lauf
python checker.py --trocken       nur anzeigen, nichts senden oder speichern
python checker.py --test-telegram Testnachricht senden
python checker.py --chat-id       Chat-ID anzeigen (vorher dem Bot schreiben)
python checker.py --karte _site   Übersichtsseite erzeugen (lokal ansehen: cd _site, dann python -m http.server, dann http://localhost:8000)
```

## Gut zu wissen
- Der Bot fragt höflich ab: zwischen zwei Anfragen an denselben Shop liegt mindestens eine halbe Sekunde, und es werden nur Märkte im eingestellten Umkreis geprüft.
- Wird das Repo 60 Tage lang nicht verändert, pausiert GitHub geplante Läufe. Der Bot ändert deshalb etwa alle 50 Tage die Datei `.wachhalten` (nur ein Datum). Falls trotzdem eine E-Mail von GitHub kommt, kannst du den Workflow dort mit einem Klick wieder einschalten.
- Lokal (Windows) liegen Merkzettel und Status in `state.json` und `status.json` im Projektordner.
- Shops ändern ihre Seiten gelegentlich. Wenn eine Quelle dauerhaft Fehler zeigt, muss der Link oder das Prüfverfahren angepasst werden.
