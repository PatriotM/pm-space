# PM-Space v2.6 - Priority Deletion Edition

Python-Version (Basis: das ursprüngliche tur-space): hält auf den Incoming-Devices Platz frei, indem Releases
ins Archiv verschoben oder gelöscht werden - in der Reihenfolge einer konfigurierbaren Priorität.

Alles steht in **einer Datei** (`pm-space.py`), die komplette Konfiguration ganz oben im Kopf.
Keine externe Config.

## Usage

```bash
./pm-space.py sanity        # Config + Devices + Sections prüfen
./pm-space.py test          # Prioritäten der TEST_RELEASES berechnen
./pm-space.py go dryrun     # zeigen, was passieren würde
./pm-space.py go debug      # simulieren, nichts ändern
./pm-space.py go            # wirklich ausführen
```

## Config-Aufbau

| Block           | Inhalt                                                                  |
|-----------------|-------------------------------------------------------------------------|
| `GENERAL`       | tmp, logfile, security (Regex), max_loop, ignore, chown, chmod          |
| `ANNOUNCE`      | gllog-Pfad und die msgtypes für den Sitebot (`TSM` / `TSD`)             |
| `DEVICES`       | pro Device `trigger` und `target` (Größen mit Einheit: `'500M'`, `'15G'`, `'1T'`) |
| `SECTIONS`      | pro Section: device, path, dated, prio, min_age (optional), archives    |
| `PRIORITY`      | method, multi_match, exclude, groups, tags                              |
| `TEST_RELEASES` | Testfälle für `./pm-space.py test`                                     |

Größen: `1G = 1024M`, `1T = 1024G`. Fehler in der Config (falsche Größe, ungültiger Regex,
Prio außerhalb 0-100, unbekanntes Device) werden beim Start gemeldet, bevor etwas angefasst wird.

### DEVICES

```python
DEVICES = {
    '/dev/mapper/recent': {'trigger': '500G', 'target': '1000G'},
    # '/dev/sdc1':        {'trigger': '15G',   'target': '20G'},
}
```

### SECTIONS (Incoming + Archive + Prio an einer Stelle)

```python
SECTIONS = {
    'bd-movie': {
        'device': '/dev/mapper/recent',
        'path':   '/mnt/ftpd/site/recent/bd/movie',
        'dated':  False,        # True = nach Nummer (DATED), False = nach Datum
        'prio':   70,           # optional, 0-100, niedrig = zuerst weg
        'min_age': '7d',        # optional, Release muss mind. so alt sein ('12h', '7d', '2w')
        'archives': [
            {'device': '/dev/sda1', 'path': '/mnt/ftpd/site/Archive/BD-MOVIE/1'},
            {'device': '/dev/sdb1', 'path': '/mnt/ftpd/site/Archive/BD-MOVIE/2'},
        ],
    },
    'bd-tv': {
        'device': '/dev/mapper/recent',
        'path':   '/mnt/ftpd/site/recent/bd/tv',
        'prio':   40,
        'archives': [],         # leer = Releases werden gelöscht statt verschoben
    },
}
```

**Mindestalter (`min_age`):** optional und pro Section einstellbar. Ein Release, das jünger ist,
wird weder verschoben noch gelöscht - auch dann nicht, wenn der Platz knapp ist. Fehlt der Eintrag
(oder `None`), gibt es kein Mindestalter. Einheiten: `h` (Stunden), `d` (Tage), `w` (Wochen).
Basis ist die Änderungszeit des Release-Ordners, also dieselbe, nach der auch "das älteste" bestimmt wird.
`./pm-space.py sanity` zeigt pro Section, wie viele Releases dadurch gerade geschützt sind.

Bei mehreren Archiven wird das mit dem meisten freien Platz genommen, das das Release aufnehmen kann.
Passt es in keins, wird es gelöscht.

### PRIORITY

```python
PRIORITY = {
    'method':      'lowest',     # lowest | average | highest
    'multi_match': True,         # False = pro Liste nur der erste Treffer
    'exclude': [                 # werden NIE verschoben oder gelöscht
        r'[-._](PROPER|REPACK)[-._]',
    ],
    'groups': [                  # (prio, regex), case-insensitive
        (10, r'[-._](GRP1|GRP2|GRP3|GRP4)([-._]|$)'),
        (80, r'[-._](GRP8|GRP9|GRP10|GRP11)([-._]|$)'),
    ],
    'tags': [
        (20, r'[-._](HDTV|PDTV)[-._]'),
        (90, r'[-._](BluRay|Remux)[-._]'),
    ],
}
```

Regex-Beispiele: `'[-._](GRP1|GRP2)([-._]|$)'` (Group mit Trenner oder am Namensende), `'-(GRP4|GRP5)$'` (nur am Ende),
`r'\.(720p|1080p)\.'` (Auflösung).

## Wie die Priorität berechnet wird

Skala: **0 = zuerst weg ... 100 = zuletzt**. Ohne Treffer gilt 50.

1. `exclude` trifft → Release wird komplett übersprungen (in jedem Fall, auch wenn nichts anderes mehr übrig ist)
2. `groups` → jeder Treffer liefert seine Prio
3. `tags` → jeder Treffer liefert seine Prio
4. `prio` der Section → wird mitgezählt
5. `method` kombiniert alles: niedrigster Wert / Durchschnitt / höchster Wert

Ablauf pro Device: In jeder Section ist das **älteste** Release (das nicht von `exclude` oder `min_age`
ausgeschlossen ist) der Kandidat. Von diesen Kandidaten wird der mit der niedrigsten Prio verarbeitet
(bei Gleichstand der älteste), dann beginnt die Runde von vorn - bis `target` erreicht ist oder
`max_loop` Releases durch sind. Die Prio entscheidet also zwischen den Sections; innerhalb einer
Section gilt "ältestes zuerst".

```
Movie.2024.1080p.WEB-DL.x264-GRP1   Group GRP1=10, Tag WEB-DL=50, Section bd-movie=70  → lowest = 10
Show.S01E01.1080p.BluRay.x264-GRP8  Group GRP8=80, Tag BluRay=90, Section bd-tv=40     → lowest = 40
Film.2024.PROPER.1080p.BluRay-GRP9  Exclude PROPER                                     → wird nie angefasst
```

Eine Prio von 100 aus Groups/Tags/Section heißt nur "ganz am Schluss" - wer wirklich geschützt sein
soll, gehört in `exclude`.

## IRC-Announces

`ANNOUNCE['gllog']` auf die `glftpd.log` setzen und die Tags (`TSM`/`TSD`) im Sitebot eintragen.
Der komplette Setup-Block für pzs-ng/ngBot steht als Kommentar direkt unter der Config in `pm-space.py`.

Variablen: `%release %relsize %incfree %destdir %destfree %destname`

## Änderungen gegenüber v2.2

Die vollständige Versionshistorie steht als CHANGELOG in der Kopfzeile von `pm-space.py`.

Neu in v2.6: Umbenennung in `pm-space.py` (Log: `pm-space.log`, Lock: `pm-space.lock`), Beispiel-Devices `/dev/sdX`,
anonymisierte Beispiel-Groups (`GRP1`, `GRP2`, ...), Group-Regex matcht auch am Namensende.

Neu in v2.5: optionales `min_age` pro Section.

Die alte Config (`TRIGGERS`, `INCOMING`, `ARCHIVES`, `DELETE_PRIORITY_*`, ...) wird **nicht mehr** gelesen.

| v2.2                                         | v2.4                                         |
|----------------------------------------------|----------------------------------------------|
| `SIZE_UNIT` + `trigger_free`/`target_free`   | `DEVICES[...]['trigger'/'target']` mit Einheit |
| `INCOMING` + `ARCHIVES` + `DELETE_PRIORITY_SECTIONS` | `SECTIONS` (path, archives, prio)     |
| `DELETE_PRIORITY_GROUPS/TAGS` als Dict       | `PRIORITY['groups'/'tags']` als `(prio, regex)`-Liste |
| `PRIORITY_METHOD`, `PRIORITY_MULTI_MATCH`    | `PRIORITY['method'/'multi_match']`           |
| `DELETE_EXCLUDE_PATTERNS`                    | `PRIORITY['exclude']`                        |
| `TMP`, `LOGFILE`, `SECURITY_PATH`, `MAX_LOOP`, `IGNORE_DIRS`, `CHOWN_*`, `CHMOD_MODE` | `GENERAL` |
| `GLLOG`                                      | `ANNOUNCE['gllog']`                          |
| `DEBUG_TEST_RELEASES` (Dicts)                | `TEST_RELEASES` (Tupel `(section, release)`) |
| Wildcards bei Sections (`'bd-*': 60`)        | entfallen, jede Section hat ihr eigenes `prio` |

Verhalten:

- `exclude` ist jetzt absolut (v2.2: nur Prio 100, am Ende wurde trotzdem verschoben/gelöscht).
- `exclude` wirkt auch, wenn sonst keine Prioritäten konfiguriert sind.
- `%incfree` im TSM-Announce zeigt jetzt den freien Platz des Incoming-Devices (v2.2: immer 0).
- `go dryrun` listet alle Releases, die bis `target` verarbeitet würden (v2.2: nur das jeweils älteste je Section).
- Größen werden automatisch als MB/GB/TB ausgegeben.

## Troubleshooting

**Config-Fehler beim Start**
- Die Meldung nennt Block und Schlüssel, z. B. `SECTIONS 'bd-tv': invalid prio 400`

**Priorität passt nicht**
- `./pm-space.py test` zeigt alle Treffer pro Release
- `PRIORITY['method']` prüfen (`lowest` nimmt immer den niedrigsten Treffer)

**Releases werden nicht angefasst**
- Treffen sie auf `PRIORITY['exclude']`?
- Sind sie jünger als das `min_age` der Section?
- Stehen sie in `GENERAL['ignore']`?
- Liegt der Pfad außerhalb von `GENERAL['security']`? (steht dann im Logfile)
- Ist der freie Platz überhaupt unter dem `trigger`? (`./pm-space.py sanity`)
