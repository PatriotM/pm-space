#!/usr/bin/python3
"""
PM-Space - Python Implementation
Disk space management script for moving/archiving releases
Version: 2.6

CHANGELOG (neueste zuerst - bei jeder Änderung hier ergänzen)
---------------------------------------------------------------------------
2.6  - Umbenannt: tur-space.py -> pm-space.py (Logfile pm-space.log, Lockfile pm-space.lock)
     - Beispiel-Devices auf /dev/sdX umgestellt (sda1, sdb1, sdc1)
     - Beispiel-Groups anonymisiert (GRP1, GRP2, ...)
     - Group-Regex-Beispiele matchen jetzt auch Groups am Namensende: ([-._]|$)
     - Changelog in der Kopfzeile
2.5  - Neu: optionales min_age pro Section ('12h', '7d', '2w'); jüngere Releases werden
       weder verschoben noch gelöscht
2.4  - Fix: PRIORITY['exclude'] ist absolut (wird nie verschoben/gelöscht, auch nicht als
       letztes Release) und wirkt auch ohne weitere Prioritäten
     - Fix: %incfree im TSM-Announce zeigt den freien Platz des Incoming-Devices (war immer 0)
     - go dryrun listet alle Releases bis target (vorher nur das älteste je Section)
2.3  - Config umgebaut: gebündelte Blöcke GENERAL / ANNOUNCE / DEVICES / SECTIONS / PRIORITY /
       TEST_RELEASES; Größen mit Einheit ('500G', '1T'); Section = Pfad + Archive + Prio;
       Prioritäten als (prio, regex)-Listen; Config-Prüfung beim Start
2.2  - Priority Deletion Edition: Regex-Kompilierung, Prioritäts-Cache, Multi-Match,
       Exclude-Patterns, Section-Wildcards, Dry-Run, erweitertes Logging
"""

################################################################################
# CONFIGURATION - Alles Einstellbare steht hier oben
################################################################################

# ── GENERAL ───────────────────────────────────────────────────────────────────
GENERAL = {
    'tmp':      '/tmp',                                    # Verzeichnis für das Lockfile
    'logfile':  '/mnt/ftpd/ftp-data/logs/pm-space.log',   # '' = kein Logfile
    'security': r'/mnt/ftpd/site/.*/',                     # Regex: außerhalb davon wird nie gelöscht/verschoben
    'max_loop': 10,                                        # max. Releases pro Lauf und Device
    'ignore':   ['pre', 'lost+found', 'incompletes', 'incoming', 'recent'],
    'chown':    None,                                      # None oder (uid, gid), z.B. (0, 400)
    'chmod':    None,                                      # None oder Oktalwert, z.B. 0o755
}

# ── ANNOUNCE (IRC über Sitebot) ───────────────────────────────────────────────
# Setup für dZSbot/pzs-ng siehe Kommentarblock unter der Config.
ANNOUNCE = {
    'gllog':      '/mnt/ftpd/ftp-data/logs/glftpd.log',    # '' = keine IRC-Announces
    'tag_move':   'TSM',                                   # msgtype für Verschieben ins Archiv
    'tag_delete': 'TSD',                                   # msgtype für Löschen
}

# ── DEVICES (Trigger) ─────────────────────────────────────────────────────────
# Device so schreiben, dass es in der 'df'-Ausgabe eindeutig ist.
# trigger : Verarbeitung startet, sobald der freie Platz darunter fällt
# target  : es wird verschoben/gelöscht, bis so viel frei ist
# Größen mit Einheit: '500M', '15G', '1T' (1G = 1024M, 1T = 1024G)
DEVICES = {
    '/dev/mapper/recent': {'trigger': '500G', 'target': '1000G'},
    # '/dev/sdc1':        {'trigger': '15G',   'target': '20G'},
}

# ── SECTIONS (Incoming + Archive + Section-Priorität an EINER Stelle) ─────────
# device   : muss in DEVICES existieren
# path     : Incoming-Pfad
# dated    : True = Sortierung nach Nummer (DATED), False = nach Datum
# prio     : Section-Priorität 0-100 (niedrig = zuerst weg), optional
# min_age  : optional - Mindestalter, bevor ein Release überhaupt angefasst
#            (verschoben ODER gelöscht) wird: '12h', '7d', '2w'.
#            Fehlt oder None = kein Mindestalter. Basis ist die Änderungszeit des Ordners.
# archives : Liste von Zielen {'device': ..., 'path': ...}; das Ziel mit dem
#            meisten freien Platz wird genommen. Leer/fehlt = Release wird
#            gelöscht statt verschoben.
SECTIONS = {
    'bd-movie': {
        'device': '/dev/mapper/recent',
        'path':   '/mnt/ftpd/site/recent/bd/movie',
        'dated':  False,
        'prio':   70,                                      # Movies später löschen
        # 'min_age': '7d',                                 # optional: erst nach 7 Tagen anfassen
        'archives': [
            # {'device': '/dev/sda1', 'path': '/mnt/ftpd/site/Archive/BD-MOVIE/1'},
            # {'device': '/dev/sdb1', 'path': '/mnt/ftpd/site/Archive/BD-MOVIE/2'},
        ],
    },
    'bd-tv': {
        'device': '/dev/mapper/recent',
        'path':   '/mnt/ftpd/site/recent/bd/tv',
        'dated':  False,
        'prio':   40,                                      # TV-Shows früher löschen
        # 'min_age': '2d',
        'archives': [],
    },
}

# ── PRIORITY (Löschreihenfolge) ───────────────────────────────────────────────
# Ablauf: Exclude -> Groups -> Tags -> Section-Prio -> Methode
# Skala : 0 = zuerst weg ... 100 = nie löschen. Ohne Treffer gilt 50.
# Format: (prio, regex) - Regex ist case-insensitive und wird beim Start geprüft.
PRIORITY = {
    # Wie mehrere Treffer kombiniert werden:
    #   'lowest'  = niedrigster Wert (aggressiv)
    #   'average' = Durchschnitt
    #   'highest' = höchster Wert (konservativ)
    'method':      'lowest',

    # True  = jeder passende Eintrag in groups/tags zählt
    # False = pro Liste nur der erste Treffer (schneller, einfacher)
    'multi_match': True,

    # Niemals löschen/verschieben: diese Releases werden komplett übersprungen,
    # auch wenn sonst nichts mehr übrig ist (Prio 100 über Groups/Tags/Section = nur 'zuletzt')
    'exclude': [
        # r'[-._](PROPER|REPACK)[-._]',
        # r'[-._]iNTERNAL[-._]',
    ],

    # Gruppen
    'groups': [
        (10, r'[-._](GRP1|GRP2|GRP3|GRP4)([-._]|$)'),      # diese Groups zuerst löschen
        (30, r'[-._](GRP5|GRP6|GRP7)([-._]|$)'),           # mittlere Priorität
        (80, r'[-._](GRP8|GRP9|GRP10|GRP11)([-._]|$)'),    # diese später löschen
    ],

    # Qualität / Tags
    'tags': [
        (20, r'[-._](HDTV|PDTV)[-._]'),                    # Low quality zuerst
        (50, r'[-._](WEB-DL|WEBRip)[-._]'),                # mittlere Priorität
        (90, r'[-._](BluRay|Remux)[-._]'),                 # High quality später
    ],
}

# ── TEST (./pm-space.py test) ────────────────────────────────────────────────
# Format: (section, release) - Prioritäten testen, ohne echte Releases anzufassen
TEST_RELEASES = [
    # Niedrige Priorität (sollten zuerst gelöscht werden)
    ('bd-movie', 'Movie.Name.2024.1080p.BluRay.x264-GRP4'),
    ('bd-movie', 'Another.Film.2024.720p.HDTV.x264-GRP1'),
    ('bd-movie', 'Old.Classic.1999.PDTV.XviD-GRP2'),

    # Mittlere Priorität
    ('bd-tv',    'Series.S02E05.HDTV.x264-GRP5'),
    ('bd-tv',    'Documentary.S01E01.WEBRip.x264-GRP7'),
    ('bd-tv',    'Show.S01E01.2160p.WEB-DL.x265-GRP8'),

    # Hohe Priorität (sollten länger bleiben)
    ('bd-movie', 'Action.Movie.2024.1080p.BluRay.Remux-GRP9'),
    ('bd-movie', 'Horror.Film.2024.2160p.WEB-DL.DDP5.1.x265-GRP10'),

    # Beispiele für Exclude-Patterns (wenn oben aktiviert)
    # ('bd-movie', 'Special.Release.2024.PROPER.1080p.BluRay-GRP8'),
    # ('bd-movie', 'Important.Film.2024.iNTERNAL.1080p.BluRay-GRP11'),
]

################################################################################
# IRC ANNOUNCE SETUP (via sitebot) - nur Doku, kein Code
################################################################################
#
# 1. ANNOUNCE['gllog'] oben auf den Pfad deiner glftpd.log setzen, z.B.
#    '/glftpd/ftp-data/logs/glftpd.log'
#
# 2. Für pzs-ng (ngBot.conf) die Tags aus ANNOUNCE['tag_move'] / ['tag_delete']
#    (Standard: TSM / TSD) eintragen:
#
#    set msgtypes(DEFAULT)  "NEWDIR DELDIR NUKE UNNUKE WIPE ... TSM TSD"
#    set chanlist(TSM)      "#yourchannel"
#    set chanlist(TSD)      "#yourchannel"
#    set disable(TSM)       0
#    set disable(TSD)       0
#    set variables(TSM)     "%release %relsize %incfree %destdir %destfree %destname"
#    set variables(TSD)     "%release %relsize %destdir %destname"
#
#    Dann ins Theme-File (z.B. default.zst):
#    announce.TSM = "\[SPACE\] - Freeing %bold%relsize MB%bold by moving %bold%release%bold to %destname archive (%destfree MB free)"
#    announce.TSD = "\[SPACE\] - Freeing %bold%relsize MB%bold from %destname archive by deleting %bold%release%bold"
#
# 3. Bot rehashen: .rehash
#
# Verfügbare Variablen:
#   %release   - Release name
#   %relsize   - Release size in MB
#   %incfree   - Free space on incoming device (TSM only)
#   %destdir   - Full destination path
#   %destfree  - Free space on destination (TSM only)
#   %destname  - Section name
#
################################################################################
# END OF CONFIGURATION - Don't modify below this line unless you know what you're doing
################################################################################

import os
import sys
import re
import shutil
import subprocess
import time
import fcntl
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Callable

VERSION = "2.6"

_SIZE_RE = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*([MGT])B?\s*$', re.IGNORECASE)
_SIZE_FACTORS = {'M': 1, 'G': 1024, 'T': 1024 * 1024}


_AGE_RE = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*([hdw])\s*$', re.IGNORECASE)
_AGE_FACTORS = {'h': 3600, 'd': 86400, 'w': 7 * 86400}


def parse_age(value) -> float:
    """Convert an age string like '12h', '7d' or '2w' to seconds. Raises ValueError."""
    match = _AGE_RE.match(str(value))
    if not match:
        raise ValueError(f"invalid age '{value}' (expected e.g. '12h', '7d', '2w')")
    return float(match.group(1)) * _AGE_FACTORS[match.group(2).lower()]


def parse_size(value) -> float:
    """Convert a size string like '500M', '15G' or '1T' to MB. Raises ValueError."""
    match = _SIZE_RE.match(str(value))
    if not match:
        raise ValueError(f"invalid size '{value}' (expected e.g. '500M', '15G', '1T')")
    return float(match.group(1)) * _SIZE_FACTORS[match.group(2).upper()]


class TurSpace:
    """Main PM-Space application"""
    
    def __init__(self, debug: bool = False, dryrun: bool = False):
        self.debug = debug
        self.dryrun = dryrun
        self.lock_file = None
        self.lock_fd = None

        # Problems found while reading the config (reported by _preflight_checks)
        self.config_errors: List[str] = []

        # GENERAL / ANNOUNCE (optional keys fall back to defaults)
        self.tmp_dir = GENERAL.get('tmp') or '/tmp'
        self.logfile = GENERAL.get('logfile') or ''
        self.security = GENERAL.get('security') or ''
        self.max_loop = GENERAL.get('max_loop', 10)
        self.ignore_dirs = set(GENERAL.get('ignore') or [])
        self.chmod = GENERAL.get('chmod')
        chown = GENERAL.get('chown')
        if isinstance(chown, (tuple, list)) and len(chown) == 2:
            self.chown_uid, self.chown_gid = chown
        else:
            self.chown_uid, self.chown_gid = None, None
        self.gllog_path = ANNOUNCE.get('gllog') or ''
        self.tag_move = ANNOUNCE.get('tag_move') or 'TSM'
        self.tag_delete = ANNOUNCE.get('tag_delete') or 'TSD'

        # PRIORITY settings
        self.prio_method = PRIORITY.get('method', 'lowest')
        self.prio_multi = PRIORITY.get('multi_match', True)
        self.priority_cache = {}  # Cache for calculated priorities

        # DEVICES / SECTIONS -> internal structures (sizes converted to MB)
        self.triggers = self._build_triggers()
        self.sections = self._build_sections()
        self.section_prio = {s['name']: s['prio'] for s in self.sections if s['prio'] is not None}

        # Compile regex patterns for performance
        self.compiled_exclude_patterns = self._compile_exclude()
        self.compiled_group_patterns = self._compile_rules('groups')
        self.compiled_tag_patterns = self._compile_rules('tags')

        # Priority system is active as soon as anything is configured
        self.priority_enabled = bool(self.compiled_exclude_patterns or self.compiled_group_patterns
                                     or self.compiled_tag_patterns or self.section_prio)

        # Perform pre-flight checks
        self._preflight_checks()

    def _build_triggers(self) -> List[Dict]:
        """Turn DEVICES into a list of {'device', 'trigger', 'target', 'trigger_mb', 'target_mb'}"""
        triggers = []
        for device, cfg in DEVICES.items():
            if not isinstance(cfg, dict):
                self.config_errors.append(f"DEVICES {device}: must be a dict with 'trigger' and 'target'")
                continue
            try:
                trigger_mb = parse_size(cfg['trigger'])
                target_mb = parse_size(cfg['target'])
            except KeyError as e:
                self.config_errors.append(f"DEVICES {device}: missing key {e}")
                continue
            except ValueError as e:
                self.config_errors.append(f"DEVICES {device}: {e}")
                continue
            if trigger_mb >= target_mb:
                self.config_errors.append(f"DEVICES {device}: trigger must be less than target")
                continue
            triggers.append({
                'device': device,
                'trigger': str(cfg['trigger']),
                'target': str(cfg['target']),
                'trigger_mb': trigger_mb,
                'target_mb': target_mb,
            })
        return triggers

    def _build_sections(self) -> List[Dict]:
        """Turn SECTIONS into a list of {'name', 'device', 'path', 'dated', 'prio', 'min_age', 'min_age_sec', 'archives'}"""
        sections = []
        for name, cfg in SECTIONS.items():
            if not isinstance(cfg, dict):
                self.config_errors.append(f"SECTIONS '{name}': must be a dict")
                continue
            missing = [key for key in ('device', 'path') if key not in cfg]
            if missing:
                self.config_errors.append(f"SECTIONS '{name}': missing key(s): {', '.join(missing)}")
                continue

            archives = []
            for arc in cfg.get('archives') or []:
                if not isinstance(arc, dict) or 'device' not in arc or 'path' not in arc:
                    self.config_errors.append(f"SECTIONS '{name}': archive needs 'device' and 'path': {arc}")
                    continue
                archives.append(arc)

            prio = cfg.get('prio')
            if prio is not None and (isinstance(prio, bool) or not isinstance(prio, int) or not 0 <= prio <= 100):
                self.config_errors.append(f"SECTIONS '{name}': invalid prio {prio!r} (must be an integer 0-100)")
                prio = None

            min_age = cfg.get('min_age')
            min_age_sec = None
            if min_age not in (None, '', 0):
                try:
                    min_age_sec = parse_age(min_age)
                except ValueError as e:
                    self.config_errors.append(f"SECTIONS '{name}': {e}")

            sections.append({
                'name': name,
                'device': cfg['device'],
                'path': cfg['path'],
                'dated': bool(cfg.get('dated', False)),
                'prio': prio,
                'min_age': str(min_age) if min_age_sec else None,
                'min_age_sec': min_age_sec,
                'archives': archives,
            })
        return sections

    def _compile_exclude(self) -> List[Tuple]:
        """Compile PRIORITY['exclude'] -> [(compiled_re, pattern)]"""
        rules = []
        for pattern in PRIORITY.get('exclude') or []:
            try:
                rules.append((re.compile(pattern, re.IGNORECASE), pattern))
            except (re.error, TypeError) as e:
                self.config_errors.append(f"PRIORITY['exclude']: invalid regex {pattern!r} - {e}")
        return rules

    def _compile_rules(self, key: str) -> List[Tuple]:
        """Compile PRIORITY['groups'] / PRIORITY['tags'] -> [(compiled_re, prio, pattern)]"""
        rules = []
        for entry in PRIORITY.get(key) or []:
            try:
                prio, pattern = entry
            except (TypeError, ValueError):
                self.config_errors.append(f"PRIORITY['{key}']: entry must be (prio, regex), got {entry!r}")
                continue
            if isinstance(prio, bool) or not isinstance(prio, int) or not 0 <= prio <= 100:
                self.config_errors.append(f"PRIORITY['{key}']: invalid priority {prio!r} for '{pattern}' (must be 0-100)")
                continue
            try:
                rules.append((re.compile(pattern, re.IGNORECASE), prio, pattern))
            except (re.error, TypeError) as e:
                self.config_errors.append(f"PRIORITY['{key}']: invalid regex {pattern!r} - {e}")
        return rules
    
    def _preflight_checks(self):
        """Check if all required files and directories exist before starting"""
        errors = list(self.config_errors)
        warnings = []

        # Check GENERAL['tmp']
        if not os.path.exists(self.tmp_dir):
            errors.append(f"GENERAL['tmp'] directory does not exist: {self.tmp_dir}")
        elif not os.access(self.tmp_dir, os.W_OK):
            errors.append(f"GENERAL['tmp'] directory is not writable: {self.tmp_dir}")

        # Check GENERAL['logfile'] directory
        if self.logfile:
            logfile_dir = os.path.dirname(self.logfile)
            if not os.path.exists(logfile_dir):
                errors.append(f"GENERAL['logfile'] directory does not exist: {logfile_dir}")
            elif not os.access(logfile_dir, os.W_OK):
                errors.append(f"GENERAL['logfile'] directory is not writable: {logfile_dir}")

        # Check ANNOUNCE['gllog'] if configured
        if self.gllog_path:
            gllog_dir = os.path.dirname(self.gllog_path)
            if not os.path.exists(gllog_dir):
                errors.append(f"ANNOUNCE['gllog'] directory does not exist: {gllog_dir}")
            elif not os.access(gllog_dir, os.W_OK):
                warnings.append(f"ANNOUNCE['gllog'] directory is not writable: {gllog_dir}")

        # Check other GENERAL values
        if isinstance(self.max_loop, bool) or not isinstance(self.max_loop, int) or self.max_loop < 1:
            errors.append(f"GENERAL['max_loop'] must be an integer >= 1, got {self.max_loop!r}")

        if self.security:
            try:
                re.compile(self.security)
            except re.error as e:
                errors.append(f"GENERAL['security']: invalid regex {self.security!r} - {e}")
        else:
            warnings.append("GENERAL['security'] is empty - no path restriction for deleting/moving")

        chown = GENERAL.get('chown')
        if chown is not None:
            if not (isinstance(chown, (tuple, list)) and len(chown) == 2
                    and all(isinstance(x, int) and not isinstance(x, bool) for x in chown)):
                errors.append(f"GENERAL['chown'] must be None or (uid, gid), got {chown!r}")

        if self.chmod is not None and (isinstance(self.chmod, bool) or not isinstance(self.chmod, int)):
            errors.append(f"GENERAL['chmod'] must be None or an octal int like 0o755, got {self.chmod!r}")

        # Check if df command is available
        if not shutil.which('df'):
            errors.append("'df' command not found in PATH")

        # Check if du command is available
        if not shutil.which('du'):
            errors.append("'du' command not found in PATH")

        # Check DEVICES / SECTIONS
        if not DEVICES:
            errors.append("No DEVICES defined")
        if not SECTIONS:
            errors.append("No SECTIONS defined")

        for inc in self.sections:
            if inc['device'] not in DEVICES:
                errors.append(f"SECTIONS '{inc['name']}' references unknown device: {inc['device']}")

            if not os.path.exists(inc['path']):
                warnings.append(f"SECTIONS '{inc['name']}': path does not exist: {inc['path']}")

            for arc in inc['archives']:
                if not os.path.exists(arc['path']):
                    warnings.append(f"SECTIONS '{inc['name']}': archive path does not exist: {arc['path']}")

        # Check priority method
        if self.prio_method not in ['lowest', 'average', 'highest']:
            errors.append(f"PRIORITY['method'] must be 'lowest', 'average', or 'highest', got '{self.prio_method}'")

        # Print errors and warnings
        if errors or warnings:
            if errors:
                print("\n" + "=" * 60)
                print("CONFIGURATION ERRORS:")
                print("=" * 60)
                for error in errors:
                    print(f"  ❌ {error}")

            if warnings:
                print("\n" + "=" * 60)
                print("CONFIGURATION WARNINGS:")
                print("=" * 60)
                for warning in warnings:
                    print(f"  ⚠️  {warning}")

            if errors:
                print("\nPlease fix the errors before running PM-Space.")
                sys.exit(1)
    
    @staticmethod
    def _format_size(mb_value: Optional[float]) -> str:
        """Format MB value as MB / GB / TB depending on magnitude"""
        if mb_value is None:
            return "n/a"
        if mb_value >= 1024 * 1024:
            return f"{mb_value / (1024 * 1024):.2f} TB"
        if mb_value >= 1024:
            return f"{mb_value / 1024:.2f} GB"
        return f"{mb_value:.2f} MB"
    
    def calculate_release_priority(self, release_name: str, section_name: str, verbose: bool = False) -> int:
        """
        Calculate priority for a release based on groups, tags, and section.
        Lower values = delete first. Returns 0-100.
        
        Args:
            release_name: Name of the release
            section_name: Section the release belongs to
            verbose: If True, print debug info about matches
        """
        # Check cache first
        cache_key = f"{section_name}:{release_name}"
        if cache_key in self.priority_cache:
            if verbose:
                print(f"   [DEBUG] Using cached priority: {self.priority_cache[cache_key]}%")
            return self.priority_cache[cache_key]
        
        priorities = []
        matches = []
        
        # Check if any priority config is set (auto-enable if configured)
        if not self.priority_enabled:
            if verbose:
                print(f"   [DEBUG] No priority config - using default 50")
            self.priority_cache[cache_key] = 50
            return 50  # Default neutral priority if nothing configured
        
        # Check exclude patterns first
        for compiled_re, pattern in self.compiled_exclude_patterns:
            if compiled_re.search(release_name):
                if verbose:
                    print(f"   [DEBUG] Matched EXCLUDE pattern: {pattern} - Priority set to 100 (never delete)")
                matches.append(f"EXCLUDE:{pattern}=100%")
                self.priority_cache[cache_key] = 100
                return 100  # Never delete
        
        # Check group priorities (using compiled patterns)
        for compiled_re, priority, original_pattern in self.compiled_group_patterns:
            if compiled_re.search(release_name):
                priorities.append(priority)
                matches.append(f"Group:{original_pattern}={priority}%")
                if not self.prio_multi:
                    break  # Only first match
        
        # Check tag priorities (using compiled patterns)
        for compiled_re, priority, original_pattern in self.compiled_tag_patterns:
            if compiled_re.search(release_name):
                priorities.append(priority)
                matches.append(f"Tag:{original_pattern}={priority}%")
                if not self.prio_multi:
                    break  # Only first match
        
        # Check section priority (SECTIONS[name]['prio'])
        section_priority = self.section_prio.get(section_name)
        if section_priority is not None:
            priorities.append(section_priority)
            matches.append(f"Section:{section_name}={section_priority}%")
        
        # Calculate final priority based on method
        if not priorities:
            if verbose:
                print(f"   [DEBUG] No matches - using default 50")
            result = 50  # Default neutral priority
        else:
            if self.prio_method == 'lowest':
                result = min(priorities)
            elif self.prio_method == 'average':
                result = int(sum(priorities) / len(priorities))
            elif self.prio_method == 'highest':
                result = max(priorities)
            else:
                result = min(priorities)  # Default to lowest
        
        if verbose:
            print(f"   [DEBUG] Matched: {', '.join(matches)} → {self.prio_method} = {result}%")
        
        # Cache result
        self.priority_cache[cache_key] = result
        return result
    
    def debug_print(self, msg: str):
        """Print debug message if in debug mode"""
        if self.debug:
            print(msg)
    
    def log(self, message: str):
        """Write message to logfile"""
        if not self.logfile:
            return
        
        try:
            timestamp = datetime.now().strftime("%a %b %d %H:%M:%S %Y")
            with open(self.logfile, 'a') as f:
                f.write(f"{timestamp} {message}\n")
        except Exception as e:
            print(f"Warning: Could not write to logfile: {e}")
    
    def gllog(self, msg_type: str, variables: Dict[str, str]):
        """Write IRC announcement to glftpd.log"""
        if not self.gllog_path:
            return
        
        try:
            timestamp = datetime.now().strftime("%a %b %d %H:%M:%S %Y")
            
            # Format variables for glftpd.log
            var_str = " ".join([f"{k}=\"{v}\"" for k, v in variables.items()])
            
            with open(self.gllog_path, 'a') as f:
                f.write(f"{timestamp} {msg_type}: {var_str}\n")
        except Exception as e:
            self.log(f"Warning: Could not write to gllog: {e}")
    
    def get_free_space(self, device: str) -> Optional[float]:
        """Get free space in MB for a device"""
        try:
            result = subprocess.run(['df', '-m'], capture_output=True, text=True)
            for line in result.stdout.splitlines():
                if device in line:
                    parts = line.split()
                    if len(parts) >= 4:
                        return float(parts[3])
            return None
        except Exception as e:
            self.log(f"Error getting free space for {device}: {e}")
            return None
    
    def get_dir_size(self, path: str) -> Optional[float]:
        """Get directory size in MB"""
        try:
            result = subprocess.run(['du', '-sm', path], capture_output=True, text=True)
            size_str = result.stdout.split()[0]
            return float(size_str)
        except Exception as e:
            self.log(f"Error getting size for {path}: {e}")
            return None
    
    def is_excluded(self, release_name: str) -> bool:
        """True if the release matches PRIORITY['exclude'] (never moved/deleted)"""
        return any(rx.search(release_name) for rx, _ in self.compiled_exclude_patterns)
    
    def find_oldest_dir(self, base_path: str, section_name: str, dated: bool = False,
                        skip: Optional[Callable[[str], bool]] = None,
                        min_age_sec: Optional[float] = None) -> Optional[Tuple[str, float]]:
        """
        Find oldest directory in base_path (directories for which skip(name) is True are ignored,
        directories younger than min_age_sec are ignored)
        Returns: (dirname, size_in_mb) or None
        """
        if not os.path.exists(base_path):
            return None
        
        try:
            dirs = []
            for entry in os.listdir(base_path):
                full_path = os.path.join(base_path, entry)
                
                # Skip non-directories and ignored directories
                if not os.path.isdir(full_path):
                    continue
                if entry in self.ignore_dirs:
                    continue
                if skip and skip(entry):
                    continue
                
                # Get mtime
                try:
                    mtime = os.stat(full_path).st_mtime
                    if min_age_sec and (time.time() - mtime) < min_age_sec:
                        continue  # too young - not touched yet
                    size = self.get_dir_size(full_path)
                    if size is not None:
                        dirs.append((entry, mtime, size))
                except:
                    continue
            
            if not dirs:
                return None
            
            # Sort by mtime (oldest first)
            dirs.sort(key=lambda x: x[1])
            
            # Return oldest
            oldest_name, oldest_mtime, oldest_size = dirs[0]
            return (oldest_name, oldest_size)
        
        except Exception as e:
            self.log(f"Error finding oldest dir in {base_path}: {e}")
            return None
    
    def move_release(self, src_path: str, dirname: str, dst_path: str, 
                    size: float, section_name: str, dst_free: float, priority: int = 50,
                    inc_device: Optional[str] = None):
        """Move a release to archive"""
        src = os.path.join(src_path, dirname)
        dst = os.path.join(dst_path, dirname)
        
        # Security check
        if self.security:
            if not re.match(self.security, src):
                self.log(f"Security: Refusing to move {src} (not in GENERAL['security'])")
                return
        
        self.log(f"Moving {dirname} (priority: {priority}%, size: {self._format_size(size)}) to {section_name} archive")
        self.debug_print(f"[MOVE] {dirname} (priority: {priority}%, size: {self._format_size(size)}) → {dst_path}")
        
        if self.dryrun:
            self.debug_print(f"[DRYRUN] Would move {dirname} to {dst_path}")
            return
        
        if not self.debug:
            try:
                # Move directory
                shutil.move(src, dst)
                
                # Apply ownership if configured
                if self.chown_uid is not None and self.chown_gid is not None:
                    for root, dirs, files in os.walk(dst):
                        os.chown(root, self.chown_uid, self.chown_gid)
                        for d in dirs:
                            os.chown(os.path.join(root, d), self.chown_uid, self.chown_gid)
                        for f in files:
                            os.chown(os.path.join(root, f), self.chown_uid, self.chown_gid)
                
                # Apply permissions if configured
                if self.chmod is not None:
                    for root, dirs, files in os.walk(dst):
                        os.chmod(root, self.chmod)
                        for d in dirs:
                            os.chmod(os.path.join(root, d), self.chmod)
                        for f in files:
                            os.chmod(os.path.join(root, f), self.chmod)
                
                # IRC announce
                inc_free = (self.get_free_space(inc_device) if inc_device else None) or 0
                self.gllog(self.tag_move, {
                    "release": dirname,
                    "relsize": str(int(size)),
                    "incfree": str(int(inc_free)),
                    "destdir": dst,
                    "destfree": str(int(dst_free)),
                    "destname": section_name
                })
                
            except Exception as e:
                self.log(f"Error moving {src} to {dst}: {e}")
    
    def delete_release(self, base_path: str, dirname: str, size: float, section_name: str, priority: int = 50):
        """Delete a release"""
        path = os.path.join(base_path, dirname)
        
        # Security check
        if self.security:
            if not re.match(self.security, path):
                self.log(f"Security: Refusing to delete {path} (not in GENERAL['security'])")
                return
        
        self.log(f"Deleting {dirname} (priority: {priority}%, size: {self._format_size(size)}) from {section_name}")
        self.debug_print(f"[DELETE] {dirname} (priority: {priority}%, size: {self._format_size(size)})")
        
        if self.dryrun:
            self.debug_print(f"[DRYRUN] Would delete {path}")
            return
        
        if not self.debug:
            try:
                shutil.rmtree(path)
                
                # IRC announce
                self.gllog(self.tag_delete, {
                    "release": dirname,
                    "relsize": str(int(size)),
                    "destdir": base_path,
                    "destname": section_name
                })
                
            except Exception as e:
                self.log(f"Error deleting {path}: {e}")
    
    def acquire_lock(self) -> bool:
        """Acquire exclusive lock"""
        self.lock_file = os.path.join(self.tmp_dir, "pm-space.lock")
        
        try:
            self.lock_fd = open(self.lock_file, 'w')
            fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_fd.write(str(os.getpid()))
            self.lock_fd.flush()
            return True
        except BlockingIOError:
            return False
        except Exception as e:
            print(f"Error acquiring lock: {e}")
            return False
    
    def release_lock(self):
        """Release lock"""
        if self.lock_fd:
            try:
                fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_UN)
                self.lock_fd.close()
            except:
                pass
            self.lock_fd = None
        
        if self.lock_file and os.path.exists(self.lock_file):
            try:
                os.unlink(self.lock_file)
            except:
                pass
    
    def remove_lock(self):
        """Remove lock file (for sanity check)"""
        lock_file = os.path.join(self.tmp_dir, "pm-space.lock")
        if os.path.exists(lock_file):
            try:
                os.unlink(lock_file)
            except Exception as e:
                print(f"Warning: Could not remove lock file: {e}")
    
    def _print_dryrun_summary(self, actions: List[Dict]):
        """Print summary of what would be done in dryrun mode"""
        print("\n" + "=" * 80)
        print("DRYRUN SUMMARY - Actions that would be performed:")
        print("=" * 80)
        
        # Sort by priority (lowest first)
        sorted_actions = sorted(actions, key=lambda x: x['priority'])
        
        total_size = sum(a['size'] for a in sorted_actions)
        deletes = [a for a in sorted_actions if a['action'] == 'DELETE']
        moves = [a for a in sorted_actions if a['action'] == 'MOVE']
        
        print(f"\nTotal actions: {len(sorted_actions)}")
        print(f"  - Deletions: {len(deletes)}")
        print(f"  - Moves to archive: {len(moves)}")
        print(f"  - Total size to free: {self._format_size(total_size)}")
        
        print("\n" + "-" * 80)
        print("DETAILED ACTION LIST (sorted by priority, lowest first):")
        print("-" * 80)
        
        for idx, action in enumerate(sorted_actions, 1):
            action_type = action['action']
            priority = action['priority']
            release = action['release']
            section = action['section']
            size = self._format_size(action['size'])
            
            # Priority indicator
            if priority < 30:
                indicator = "🗑️ "
            elif priority < 70:
                indicator = "⏳"
            else:
                indicator = "✓ "
            
            print(f"\n{idx}. {indicator} [{priority:3d}%] {action_type}")
            print(f"   Release: {release}")
            print(f"   Section: {section}")
            print(f"   Size: {size}")
            
            if action_type == 'DELETE':
                print(f"   Reason: {action.get('reason', 'N/A')}")
            else:
                print(f"   Destination: {action.get('destination', 'N/A')}")
        
        print("\n" + "=" * 80)
        print("Legend: 🗑️  = High priority to delete | ⏳ = Medium | ✓  = Low priority")
        print("=" * 80)

    
    def sanity_check(self):
        """Perform sanity check of configuration"""
        print("\n" + "=" * 60)
        print("PM-SPACE SANITY CHECK")
        print("=" * 60)
        
        print(f"\nVersion: {VERSION}")
        print(f"Priority System: {'ENABLED' if self.priority_enabled else 'DISABLED'}")
        if self.priority_enabled:
            print(f"Priority Method: {self.prio_method}")
            print(f"Multi-Match: {self.prio_multi}")
            print(f"Exclude Patterns: {len(self.compiled_exclude_patterns)} defined")
        
        # Check devices
        print("\n" + "=" * 60)
        print("DEVICES:")
        print("=" * 60)
        for trigger in self.triggers:
            device = trigger['device']
            free_space = self.get_free_space(device)
            
            if free_space is None:
                print(f"\n❌ {device}")
                print(f"   ERROR: Cannot get free space")
            else:
                status = "OK" if free_space >= trigger['trigger_mb'] else "BELOW TRIGGER"
                print(f"\n✓ {device}")
                print(f"   Free: {self._format_size(free_space)}")
                print(f"   Trigger: {trigger['trigger']}")
                print(f"   Target: {trigger['target']}")
                print(f"   Status: {status}")
        
        # Check sections
        print("\n" + "=" * 60)
        print("SECTIONS:")
        print("=" * 60)
        for inc in self.sections:
            path = inc['path']
            exists = os.path.exists(path)
            
            print(f"\n{'✓' if exists else '❌'} {inc['name']}")
            print(f"   Device: {inc['device']}")
            print(f"   Path: {path}")
            print(f"   Dated: {inc['dated']}")
            print(f"   Section Priority: {str(inc['prio']) + '%' if inc['prio'] is not None else '-'}")
            print(f"   Min Age: {inc['min_age'] or '-'}")
            print(f"   Archives: {len(inc['archives'])}")
            
            if exists:
                # Count releases
                try:
                    releases = [d for d in os.listdir(path) 
                               if os.path.isdir(os.path.join(path, d)) and d not in self.ignore_dirs]
                    print(f"   Releases: {len(releases)}")
                    if inc['min_age_sec']:
                        now = time.time()
                        young = sum(1 for d in releases
                                    if now - os.stat(os.path.join(path, d)).st_mtime < inc['min_age_sec'])
                        print(f"   Younger than min age (protected): {young}")
                    
                    # Show oldest if priority enabled
                    if self.priority_enabled and releases:
                        oldest = self.find_oldest_dir(path, inc['name'], inc['dated'], skip=self.is_excluded,
                                                     min_age_sec=inc['min_age_sec'])
                        if oldest:
                            oldest_name, oldest_size = oldest
                            priority = self.calculate_release_priority(oldest_name, inc['name'])
                            print(f"   Oldest: {oldest_name}")
                            print(f"   Size: {self._format_size(oldest_size)}")
                            print(f"   Priority: {priority}%")
                except Exception as e:
                    print(f"   Error: {e}")
        
        # Check archives
        print("\n" + "=" * 60)
        print("ARCHIVES:")
        print("=" * 60)
        for inc in self.sections:
            if not inc['archives']:
                continue
            print(f"\n{inc['name']}:")
            for arc in inc['archives']:
                device = arc['device']
                path = arc['path']
                exists = os.path.exists(path)
                free_space = self.get_free_space(device)
                
                print(f"  {'✓' if exists else '❌'} {device}")
                print(f"     Path: {path}")
                if free_space is not None:
                    print(f"     Free: {self._format_size(free_space)}")
        
        # Check for sections without archives
        print("\n" + "=" * 60)
        print("SECTIONS WITHOUT ARCHIVES (will be deleted):")
        print("=" * 60)
        for inc in self.sections:
            if not inc['archives']:
                print(f"  • {inc['name']}")
        
        # Priority configuration
        if self.priority_enabled:
            print("\n" + "=" * 60)
            print("PRIORITY CONFIGURATION:")
            print("=" * 60)
            
            if self.compiled_exclude_patterns:
                print("\nExclude Patterns (Never Delete):")
                for _, pattern in self.compiled_exclude_patterns:
                    print(f"  100% - {pattern}")
            
            if self.compiled_group_patterns:
                print("\nGroups:")
                for _, prio, pattern in sorted(self.compiled_group_patterns, key=lambda x: x[1]):
                    print(f"  {prio:3d}% - {pattern}")
            
            if self.compiled_tag_patterns:
                print("\nTags:")
                for _, prio, pattern in sorted(self.compiled_tag_patterns, key=lambda x: x[1]):
                    print(f"  {prio:3d}% - {pattern}")
            
            if self.section_prio:
                print("\nSections:")
                for section, prio in sorted(self.section_prio.items(), key=lambda x: x[1]):
                    print(f"  {prio:3d}% - {section}")
        
        print("\n" + "=" * 60)
        self.remove_lock()
    
    def test_priorities(self):
        """Test priority calculation with TEST_RELEASES"""
        if not TEST_RELEASES:
            print("\n" + "=" * 80)
            print("No test releases defined in TEST_RELEASES")
            print("=" * 80)
            print("\nAdd some releases to test priority calculation.")
            print("\nExample:")
            print("TEST_RELEASES = [")
            print("    ('bd-movie', 'Movie.2024.1080p.BluRay-GRP8'),")
            print("    ('bd-tv',    'Show.S01E01.HDTV-GRP1'),")
            print("]")
            return
        
        print("\n" + "=" * 80)
        print("PRIORITY TEST MODE - Testing release priorities")
        print("=" * 80)
        
        # Check if priority system is configured
        if not self.priority_enabled:
            print("\n⚠️  WARNING: No priority configuration found!")
            print("   PRIORITY['groups'], PRIORITY['tags'], PRIORITY['exclude'] and all section 'prio' values are empty.")
            print("   All releases will get default priority of 50.")
        
        print(f"\nPriority Method: {self.prio_method}")
        print(f"Priority System: {'ENABLED' if self.priority_enabled else 'DISABLED'}")
        print(f"Multi-Match: {self.prio_multi}")
        
        if self.priority_enabled:
            print("\nConfigured Priorities:")
            
            if self.compiled_exclude_patterns:
                print("\n  Exclude Patterns (Never Delete):")
                for _, pattern in self.compiled_exclude_patterns:
                    print(f"    100% - {pattern}")
            
            if self.compiled_group_patterns:
                print("\n  Groups:")
                for _, prio, pattern in sorted(self.compiled_group_patterns, key=lambda x: x[1]):
                    print(f"    {prio:3d}% - {pattern}")
            
            if self.compiled_tag_patterns:
                print("\n  Tags:")
                for _, prio, pattern in sorted(self.compiled_tag_patterns, key=lambda x: x[1]):
                    print(f"    {prio:3d}% - {pattern}")
            
            if self.section_prio:
                print("\n  Sections:")
                for section, prio in sorted(self.section_prio.items(), key=lambda x: x[1]):
                    print(f"    {prio:3d}% - {section}")
        
        print("\n" + "=" * 80)
        print("TEST RESULTS:")
        print("=" * 80)
        
        results = []
        
        for idx, test in enumerate(TEST_RELEASES, 1):
            try:
                section, release = test
            except (TypeError, ValueError):
                print(f"\n{idx}. [SKIPPED] Entry must be (section, release): {test!r}")
                continue
            
            if not release:
                print(f"\n{idx}. [SKIPPED] Empty release name")
                continue
            
            # Calculate priority
            priority = self.calculate_release_priority(release, section)
            
            # Find what matched (respecting multi_match)
            excluded = [p for rx, p in self.compiled_exclude_patterns if rx.search(release)]
            matched_groups = []
            for rx, prio, pattern in self.compiled_group_patterns:
                if rx.search(release):
                    matched_groups.append((pattern, prio))
                    if not self.prio_multi:
                        break
            matched_tags = []
            for rx, prio, pattern in self.compiled_tag_patterns:
                if rx.search(release):
                    matched_tags.append((pattern, prio))
                    if not self.prio_multi:
                        break
            matched_section = None
            if section in self.section_prio:
                matched_section = (section, self.section_prio[section])
            
            results.append({
                'idx': idx,
                'section': section,
                'release': release,
                'priority': priority,
                'excluded': excluded,
                'groups': matched_groups,
                'tags': matched_tags,
                'section_match': matched_section
            })
        
        # Sort by priority (lowest first = deleted first)
        results_sorted = sorted(results, key=lambda x: x['priority'])
        
        for r in results_sorted:
            print(f"\n{r['idx']}. Section: {r['section']}")
            print(f"   Release: {r['release']}")
            print(f"   → Final Priority: {r['priority']}% (lower = deleted first)")
            
            matches = []
            for pattern in r['excluded']:
                matches.append(f"Exclude '{pattern}' = 100%")
            for pattern, prio in r['groups']:
                matches.append(f"Group '{pattern}' = {prio}%")
            for pattern, prio in r['tags']:
                matches.append(f"Tag '{pattern}' = {prio}%")
            if r['section_match']:
                matches.append(f"Section '{r['section_match'][0]}' = {r['section_match'][1]}%")
            
            if matches:
                print(f"   Matched: {', '.join(matches)}")
            else:
                print(f"   Matched: None (using default 50%)")
        
        print("\n" + "=" * 80)
        print("DELETION ORDER (based on priority):")
        print("=" * 80)
        
        for order, r in enumerate(results_sorted, 1):
            indicator = "🗑️ " if r['priority'] < 50 else "⏳" if r['priority'] == 50 else "✓ "
            print(f"{order}. {indicator} [{r['priority']:3d}%] {r['release'][:60]}")
        
        print("\n" + "=" * 80)
        print(f"Total test releases: {len(results)}")
        print("Legend: 🗑️ = High priority to delete | ⏳ = Medium | ✓ = Low priority")
        print("=" * 80)
    
    def process(self):
        """Main processing loop"""
        # Track what would be deleted/moved in dryrun mode
        dryrun_actions = []
        
        for trigger in self.triggers:
            device = trigger['device']
            free_space = self.get_free_space(device)
            
            if free_space is None:
                self.log(f"Error: Cannot get free space for {device}")
                self.debug_print(f"Error: Cannot get free space for {device}")
                continue
            
            trigger_free_mb = trigger['trigger_mb']
            target_free_mb = trigger['target_mb']
            
            if free_space >= trigger_free_mb:
                self.debug_print(f"{device} has enough space ({self._format_size(free_space)} >= {trigger['trigger']})")
                continue
            
            self.log(f"Processing {device} (free: {self._format_size(free_space)}, target: {trigger['target']})")
            self.debug_print(f"\nProcessing {device}: {self._format_size(free_space)}, need {trigger['target']}")
            
            if self.priority_enabled:
                self.debug_print(f"Priority deletion: ENABLED (method: {self.prio_method}, multi-match: {self.prio_multi})")
            
            if self.dryrun:
                self.debug_print(f"[DRYRUN MODE] No actual changes will be made\n")
            
            # Track processed releases in debug mode
            processed_releases = set()
            
            counter = 0
            while free_space < target_free_mb:
                processed_something = False
                
                # Find release with LOWEST priority (and oldest within same priority)
                oldest_release = None
                lowest_priority = 101  # Start higher than max
                oldest_time_in_priority = None
                oldest_section = None
                
                for inc in self.sections:
                    if inc['device'] != device:
                        continue
                    
                    result = self.find_oldest_dir(
                        inc['path'], inc['name'], inc['dated'],
                        min_age_sec=inc['min_age_sec'],
                        skip=lambda name, base=inc['path']: (self.is_excluded(name)
                                                              or os.path.join(base, name) in processed_releases))
                    if not result:
                        continue
                    
                    release_name, size = result
                    release_path = os.path.join(inc['path'], release_name)
                    
                    # Skip if already processed in this run
                    if release_path in processed_releases:
                        continue
                    
                    try:
                        mtime = os.stat(release_path).st_mtime
                        priority = self.calculate_release_priority(release_name, inc['name'])
                        
                        # Select release with lowest priority, or oldest within same priority
                        if priority < lowest_priority or \
                           (priority == lowest_priority and (oldest_time_in_priority is None or mtime < oldest_time_in_priority)):
                            lowest_priority = priority
                            oldest_time_in_priority = mtime
                            oldest_release = (inc, release_name, size, priority)
                            oldest_section = inc
                    except:
                        continue
                
                if not oldest_release:
                    self.debug_print(f"No more releases found to process on {device}")
                    break
                
                inc, release_name, size, priority = oldest_release
                release_path = os.path.join(inc['path'], release_name)
                
                # Mark as processed
                processed_releases.add(release_path)
                
                # Debug output with priority
                if self.priority_enabled:
                    self.debug_print(f"Selected: {release_name} (priority: {priority}%, size: {self._format_size(size)})")
                
                # Check if archives exist for this section
                if inc['archives']:
                    # Try to find archive with enough space
                    archives = inc['archives']
                    best_archive = None
                    best_free = 0
                    
                    for arc in archives:
                        arc_free = self.get_free_space(arc['device'])
                        if arc_free and arc_free >= size and arc_free > best_free:
                            best_free = arc_free
                            best_archive = arc
                    
                    if best_archive:
                        if self.dryrun:
                            dryrun_actions.append({
                                'action': 'MOVE',
                                'release': release_name,
                                'section': inc['name'],
                                'priority': priority,
                                'size': size,
                                'destination': best_archive['path']
                            })
                        self.move_release(inc['path'], release_name, best_archive['path'], 
                                        size, inc['name'], best_free, priority, inc['device'])
                        processed_something = True
                    else:
                        # Need to free space in archives first
                        self.debug_print(f"No archive has space for {release_name} ({self._format_size(size)})")
                        if self.dryrun:
                            dryrun_actions.append({
                                'action': 'DELETE',
                                'release': release_name,
                                'section': inc['name'],
                                'priority': priority,
                                'size': size,
                                'reason': 'No archive space'
                            })
                        self.delete_release(inc['path'], release_name, size, inc['name'], priority)
                        processed_something = True
                else:
                    # No archive, just delete
                    if self.dryrun:
                        dryrun_actions.append({
                            'action': 'DELETE',
                            'release': release_name,
                            'section': inc['name'],
                            'priority': priority,
                            'size': size,
                            'reason': 'No archive defined'
                        })
                    self.delete_release(inc['path'], release_name, size, inc['name'], priority)
                    processed_something = True
                
                if not processed_something:
                    break
                
                # Refresh free space (in debug/dryrun mode, simulate the freed space)
                if self.debug or self.dryrun:
                    free_space += size
                    self.debug_print(f"[DEBUG/DRYRUN] Simulated free space: {self._format_size(free_space)}")
                else:
                    free_space = self.get_free_space(device)
                    if free_space is None:
                        self.log(f"Error: Cannot get free space for {device}")
                        break
                
                counter += 1
                if (self.debug or self.dryrun) and counter >= 20:
                    self.debug_print("\n[DEBUG/DRYRUN] Breaking after 20 loops")
                    break
                
                if counter >= self.max_loop:
                    self.log(f"Warning: Reached max loop count ({self.max_loop})")
                    self.debug_print(f"Warning: Reached max loop count ({self.max_loop})")
                    break
            
            if self.debug or self.dryrun:
                self.debug_print(f"\n[DEBUG/DRYRUN] Processed {counter} releases")
                self.debug_print(f"[DEBUG/DRYRUN] Final simulated free space: {self._format_size(free_space)}")
            else:
                final_free = self.get_free_space(device)
                self.log(f"Done processing {device}. Now {self._format_size(final_free)}")
                self.debug_print(f"Done processing {device}. Now {self._format_size(final_free)}\n")
        
        # Print dryrun summary
        if self.dryrun and dryrun_actions:
            self._print_dryrun_summary(dryrun_actions)
        
        self.release_lock()


def main():
    if len(sys.argv) < 2:
        print(f"\nPM-Space {VERSION} - Python Implementation with Priority Deletion")
        print("=" * 60)
        print("\nUsage:")
        print(f"  {sys.argv[0]} sanity       - Check configuration")
        print(f"  {sys.argv[0]} test         - Test priority calculation (TEST_RELEASES)")
        print(f"  {sys.argv[0]} go           - Process disk space")
        print(f"  {sys.argv[0]} go debug     - Debug mode (no actual changes)")
        print(f"  {sys.argv[0]} go dryrun    - Dry-run mode (show what would be done)")
        print("\nConfiguration is set at the top of this script.")
        print("=" * 60)
        sys.exit(0)
    
    command = sys.argv[1].lower()
    debug = len(sys.argv) > 2 and sys.argv[2].lower() == "debug"
    dryrun = len(sys.argv) > 2 and sys.argv[2].lower() == "dryrun"
    
    ts = None
    try:
        ts = TurSpace(debug=debug, dryrun=dryrun)
        
        if command == "sanity":
            # Sanity check doesn't need lock
            ts.sanity_check()
        elif command == "test":
            # Test mode doesn't need lock
            ts.test_priorities()
        elif command == "go":
            # Acquire lock - this ensures only one instance runs
            if not ts.acquire_lock():
                print("Another instance of PM-Space is already running.")
                print("If you're sure no other instance is running, remove:")
                print(f"  {os.path.join(ts.tmp_dir, 'pm-space.lock')}")
                sys.exit(0)
            
            try:
                ts.process()
            finally:
                ts.release_lock()
        else:
            print(f"Unknown command: {command}")
            print("Use 'sanity', 'test', or 'go'")
            sys.exit(1)
    
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        if ts:
            ts.release_lock()
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        if ts:
            ts.release_lock()
        sys.exit(1)


if __name__ == "__main__":
    main()
