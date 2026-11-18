"""Win probability and WPA (Win Probability Added) of solo queue games (REQ-WPA, docs/requirements.md).

Method of the thesis "Win probability estimation for strategic decision-making in esports" (xPetu,
Aalto University, 2024, CC BY-NC-SA 4.0): a model estimates the probability w(x) that Blue wins from
a game state x; the value of an action is w(z) - w(x) between the state x where it is taken and the
state z where its effect stops. Actions are never model variables (action != state).

The model (gradient-boosted trees) is trained offline by `backend/tools/train_wpa.py` (scikit-learn,
never shipped) and exported to `wpa_model.json` in the data folder (format: docs/api.md); this module
evaluates it with the standard library only.

Per-game results (curve, kills, objectives, purchases) are cached in `soloq.db`, table `soloq_wpa`,
under the key "<SHA-256 of the model file>:<WPA_REVISION>"; a background job fills it.
"""
import hashlib
import json
import math
import sqlite3
import struct
import threading
import zlib
from array import array
from collections import OrderedDict, defaultdict
from pathlib import Path

from . import soloq
from .config import DATA_DIR
from .riot import TIERS

MODEL_PATH = DATA_DIR / "wpa_model.json"
MODEL_FORMAT = "lpc-wpa-gbt"
MODEL_VERSION = 1
FEATURE_VERSION = 1  # definition of the variables: a model trained with another one is refused
WPA_REVISION = 1  # increment whenever a code change alters a cached result (variables, item or event rules)
SOURCES = ("soloq", "synthetic")
MAX_MODEL_BYTES = 5 * 1024 * 1024
MAX_DEPTH = 64
MAX_MINUTE = 60
TRUNCATED_GAP = 120  # seconds between the last frame and the end beyond which a timeline is unusable

FRAME_STATS = ("gold", "xp", "level", "cs", "damage_dealt")
KDA = ("kills", "deaths", "assists")
OBJECTIVES = ("tower", "inhibitor", "plate", "dragon", "herald", "grub", "baron", "atakhan")
FEATURES = (
    "minute",
    "gold_diff", "xp_diff", "level_diff", "cs_diff", "damage_diff",
    "kills_diff", "deaths_diff", "assists_diff",
    "towers_diff", "inhibitors_diff", "plates_diff", "dragons_diff", "heralds_diff", "grubs_diff", "barons_diff",
    "atakhans_diff",
    "blue_dragons", "red_dragons",
    "rank",
)
_DIFFS = (*FRAME_STATS, *KDA, *OBJECTIVES)  # same order as the *_diff features

BEHIND, AHEAD = 0.4, 0.6  # W of the buyer's team at the purchase
CONTEXTS = ("behind", "even", "ahead")
PROFILE_GAP = 0.02
PROFILE_MIN = 10

VALIDATED_GAMES, VALIDATED_ACCURACY, VALIDATED_ECE = 50_000, 0.70, 0.03
TRAIN_HINT = "Pour en entraîner un : pip install -r backend/requirements-ml.txt puis python backend/tools/train_wpa.py"
WARNINGS = {
    "soloq": "Modèle entraîné sur des parties solo queue : il n'est pas directement transposable au jeu pro "
             "(aucune timeline de partie pro n'est publique).",
    "selection": "Biais de sélection : un objet ou un événement est choisi selon la situation de la partie ; "
                 "le WPA mesure une association, pas un effet causal.",
    "synthetic": "Modèle entraîné sur des données synthétiques : ses probabilités ne reflètent pas de vraies parties.",
}


# --- game state (shared by training and inference) ---------------------------------


def rank_of(tier: str | None) -> int:
    """Emerald 0 ... Challenger 4; an unknown tier counts as Emerald, the lowest imported."""
    return TIERS.index(tier) if tier in TIERS else 0


def side_of(participant: int) -> int:
    """Participants 1-5 are Blue (team 100), 6-10 Red (team 200), as in Match-V5."""
    return 1 if participant <= 5 else 2


def usable(timeline: dict | None, duration: float) -> bool:
    """A timeline with frames up to (nearly) the end of a game lasting `duration` seconds."""
    frames = (timeline or {}).get("frames") or []
    return bool(frames) and duration - (len(frames) - 1) * 60 <= TRUNCATED_GAP


def ordered_events(timeline: dict) -> list[dict]:
    """Kills and objectives in the conventions' order: by second, kills before objectives, then as stored.
    A kill's `side` is the team that benefits (the victim's opponents, also for an execution)."""
    cached = timeline.get("_events")
    if cached is not None:
        return cached
    events = []
    for i, k in enumerate(timeline.get("kills") or []):
        if not 1 <= k["victim"] <= 10:
            continue
        killer = k["killer"] if 1 <= k["killer"] <= 10 else 0
        events.append({"key": (k["t"], 0, i), "t": k["t"], "kind": "kill", "side": 3 - side_of(k["victim"]),
                       "detail": None, "killer": killer, "victim": k["victim"],
                       "assists": [p for p in k.get("assists") or [] if 1 <= p <= 10]})
    for i, o in enumerate(timeline.get("objectives") or []):
        if o["side"] in (1, 2) and o["kind"] in OBJECTIVES:
            events.append({"key": (o["t"], 1, i), "t": o["t"], "kind": o["kind"], "side": o["side"],
                           "detail": o["detail"], "killer": 0, "victim": 0, "assists": []})
    events.sort(key=lambda e: e["key"])
    timeline["_events"] = events
    return events


def _apply(state: dict, e: dict) -> None:
    if e["kind"] == "kill":
        state[side_of(e["victim"])]["deaths"] += 1
        if e["killer"]:
            state[e["side"]]["kills"] += 1
        for pid in e["assists"]:
            state[side_of(pid)]["assists"] += 1
    else:
        state[e["side"]][e["kind"]] += 1


def state_at(timeline: dict, t: float, tier: str | None = None, before: tuple | None = None) -> dict:
    """State of a game at second `t` (timeline from `soloq.load_timeline`): team totals of gold, XP,
    levels, CS and damage of the frame of minute floor(t/60) (the last frame past the end), kills and
    objectives up to `t` included. `before` (an event key) keeps only the events preceding that event."""
    minute = int(t // 60)
    state = {"minute": minute, "rank": rank_of(tier), 1: dict.fromkeys(_DIFFS, 0), 2: dict.fromkeys(_DIFFS, 0)}
    frames = timeline.get("frames") or []
    if frames:
        for pid, values in frames[min(minute, len(frames) - 1)].items():
            team = state[side_of(int(pid))]
            for k in FRAME_STATS:
                team[k] += values.get(k) or 0
    for e in ordered_events(timeline):
        if e["t"] > t or (before is not None and e["key"] >= before):
            break
        _apply(state, e)
    return state


def with_event(state: dict, event: dict) -> dict:
    out = {**state, 1: dict(state[1]), 2: dict(state[2])}
    _apply(out, event)
    return out


def features(state: dict) -> list[float]:
    """Model variables of a state, in FEATURES order, from Blue's point of view (REQ-WPA-1)."""
    blue, red = state[1], state[2]
    return [min(state["minute"], MAX_MINUTE), *(blue[k] - red[k] for k in _DIFFS), blue["dragon"], red["dragon"],
            state["rank"]]


# --- model -------------------------------------------------------------------------


class ModelError(ValueError):
    """Why a model file cannot be used (French, shown to the user)."""


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    e = math.exp(z)
    return e / (1 + e)


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _int_fr(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _pct_fr(x: float) -> str:
    return f"{x * 100:.1f} %".replace(".", ",")


class Model:
    """Gradient-boosted trees, flattened into arrays for a fast pure-Python evaluation."""

    def __init__(self, data: dict, ident: str):
        self.id = ident
        self.key = f"{ident}:{WPA_REVISION}"
        self.data = data
        self.features = tuple(data["features"])
        self.init = float(data["init"])
        self.learning_rate = float(data["learning_rate"])
        index = [FEATURES.index(name) for name in self.features]
        self.trees = [self._flatten(tree, index) for tree in data["trees"]]

    @staticmethod
    def _flatten(root: dict, index: list[int]) -> tuple[list, list, list, list, list]:
        feature, threshold, left, right, value = [], [], [], [], []

        def add(node: dict) -> int:
            i = len(feature)
            feature.append(-1)
            threshold.append(0.0)
            left.append(-1)
            right.append(-1)
            value.append(0.0)
            if "v" in node:
                value[i] = float(node["v"])
            else:
                feature[i] = index[node["f"]]
                threshold[i] = float(node["t"])
                left[i] = add(node["l"])
                right[i] = add(node["r"])
            return i

        add(root)
        return feature, threshold, left, right, value

    def raw(self, x: list[float]) -> float:
        """Log-odds of a Blue win for the FEATURES vector `x` (a split sends x <= threshold left)."""
        out = self.init
        lr = self.learning_rate
        for feature, threshold, left, right, value in self.trees:
            node = 0
            while feature[node] >= 0:
                node = left[node] if x[feature[node]] <= threshold[node] else right[node]
            out += lr * value[node]
        return out

    def predict(self, x: list[float]) -> float:
        return _sigmoid(self.raw(x))

    def win_probability(self, state: dict) -> float:
        return self.predict(features(state))

    def describe(self) -> dict:
        d = self.data
        return {"id": self.id, "source": d["source"], "trained_at": d.get("trained_at"), "games": d.get("games"),
                "samples": d.get("samples"), "feature_version": d["feature_version"], "features": list(self.features),
                "minute_coverage": d.get("minute_coverage"), "params": d.get("params") or {},
                "metrics": d.get("metrics") or {}}


def quality(data: dict) -> tuple[str, str | None]:
    """("validated", None) or ("provisional", first unmet condition) for a model file's content, REQ-WPA-11."""
    metrics = data.get("metrics") or {}
    if data.get("source") == "synthetic":
        return "provisional", "entraîné sur des données synthétiques"
    games = data.get("games") if _number(data.get("games")) else 0
    if games < VALIDATED_GAMES:
        return "provisional", f"entraîné sur {_int_fr(int(games))} parties ({_int_fr(VALIDATED_GAMES)} au moins)"
    accuracy, ece = metrics.get("accuracy"), metrics.get("ece")
    if not _number(accuracy) or not _number(ece):
        return "provisional", "métriques d'évaluation absentes"
    if accuracy < VALIDATED_ACCURACY:
        return "provisional", f"accuracy de {_pct_fr(accuracy)} (70 % au moins)"
    if ece > VALIDATED_ECE:
        return "provisional", f"erreur de calibration de {_pct_fr(ece)} (3 % au plus)"
    return "validated", None


def _check_tree(node, n_features: int, depth: int = 0) -> None:
    if not isinstance(node, dict):
        raise ModelError("nœud qui n'est pas un objet")
    if depth > MAX_DEPTH:
        raise ModelError("arbre trop profond")
    if "v" in node:
        if not _number(node["v"]):
            raise ModelError("valeur de feuille invalide")
        return
    f = node.get("f")
    if not isinstance(f, int) or isinstance(f, bool) or not 0 <= f < n_features:
        raise ModelError("indice de variable invalide")
    if not _number(node.get("t")):
        raise ModelError("seuil invalide")
    if "l" not in node or "r" not in node:
        raise ModelError("nœud sans ses deux branches")
    _check_tree(node["l"], n_features, depth + 1)
    _check_tree(node["r"], n_features, depth + 1)


def parse_model(data, ident: str = "") -> Model:
    """Validate a decoded model file (REQ-WPA-4); raises ModelError with the reason."""
    if not isinstance(data, dict) or data.get("format") != MODEL_FORMAT:
        raise ModelError(f"format inconnu (attendu : {MODEL_FORMAT})")
    if data.get("version") != MODEL_VERSION:
        raise ModelError(f"version {data.get('version')} non prise en charge (attendue : {MODEL_VERSION})")
    fv = data.get("feature_version")
    if fv != FEATURE_VERSION:
        raise ModelError(f"modèle entraîné avec une autre définition des variables "
                         f"(v{'?' if fv is None else fv}, attendue v{FEATURE_VERSION}) : réentraînez-le")
    names = data.get("features")
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names) \
            or len(set(names)) != len(names):
        raise ModelError("liste de variables absente ou en double")
    unknown = [n for n in names if n not in FEATURES]
    if unknown:
        raise ModelError(f"variables inconnues : {', '.join(unknown)}")
    if not _number(data.get("init")) or not _number(data.get("learning_rate")):
        raise ModelError("init ou learning_rate invalide")
    trees = data.get("trees")
    if not isinstance(trees, list) or not trees:
        raise ModelError("aucun arbre")
    for i, tree in enumerate(trees):
        try:
            _check_tree(tree, len(names))
        except ModelError as exc:
            raise ModelError(f"arbre n°{i + 1} mal formé : {exc}") from None
    if data.get("source") not in SOURCES:
        raise ModelError("source inconnue")
    if not isinstance(data.get("metrics", {}), dict):
        raise ModelError("métriques invalides")
    return Model(data, ident)


_lock = threading.Lock()
_loaded: dict = {"key": None, "id": None, "model": None, "error": None}


def load_model(path: Path | str | None = None) -> tuple[Model | None, str | None]:
    """The model of `wpa_model.json`, or why there is none (first sentence of the message). Re-read
    whenever the file's date or size changes, never falling back to a previous model."""
    path = Path(path) if path else MODEL_PATH
    try:
        st = path.stat()
    except OSError:
        return None, f"Aucun modèle de probabilité de victoire (fichier introuvable : {path})."
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _lock:
        if _loaded["key"] == key:
            return _loaded["model"], _loaded["error"]
        if st.st_size > MAX_MODEL_BYTES:
            model, error, ident = None, f"Modèle de probabilité de victoire invalide (fichier de plus de 5 Mo) : {path}.", None
        else:
            try:
                raw = path.read_bytes()
            except OSError:
                return None, f"Modèle de probabilité de victoire illisible (JSON invalide) : {path}."
            ident = hashlib.sha256(raw).hexdigest()
            if ident == _loaded["id"]:
                _loaded["key"] = key
                return _loaded["model"], _loaded["error"]
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                model, error = None, f"Modèle de probabilité de victoire illisible (JSON invalide) : {path}."
            else:
                try:
                    model, error = parse_model(data, ident), None
                except ModelError as exc:
                    model, error = None, f"Modèle de probabilité de victoire invalide ({exc}) : {path}."
        _loaded.update(key=key, id=ident, model=model, error=error)
        return model, error


def unavailable_message(error: str | None) -> str:
    return f"{error} {TRAIN_HINT}"


def status() -> dict:
    """Body of GET /api/wpa (REQ-WPA-5, 10, 11)."""
    model, error = load_model()
    warnings = [{"key": k, "text": WARNINGS[k]} for k in ("soloq", "selection")]
    if not model:
        return {"available": False, "message": unavailable_message(error), "status": None, "status_reason": None,
                "model": None, "cache": None, "warnings": warnings}
    quality_status, reason = quality(model.data)
    if model.data["source"] == "synthetic":
        warnings.append({"key": "synthetic", "text": WARNINGS["synthetic"]})
    if quality_status == "provisional":
        warnings.append({"key": "provisional", "text": f"Modèle non validé : {reason}. Les probabilités sont indicatives."})
    cache = {"computed": 0, "total": 0, "running": False}
    if soloq.SOLOQ_DB_PATH.exists():
        conn = soloq.connect()
        try:
            ids = [r[0] for r in conn.execute("SELECT match_id FROM soloq_games")]
            cache = {**coverage(conn, ids, model.key), "running": job.running()}
        finally:
            conn.close()
        if cache["computed"] < cache["total"]:
            job.ensure(model)
            cache["running"] = True
    return {"available": True, "message": None, "status": quality_status, "status_reason": reason,
            "model": model.describe(), "cache": cache, "warnings": warnings}


# --- evaluation ----------------------------------------------------------------------


def calibration(probs: list[float], labels: list[int], bins: int = 10) -> dict:
    """Accuracy, log loss, Brier score, expected calibration error and reliability curve (REQ-WPA-3)."""
    n = len(probs)
    if not n:
        return {"n": 0, "accuracy": None, "log_loss": None, "brier": None, "ece": None, "reliability": []}
    eps = 1e-15
    accuracy = sum((p >= 0.5) == bool(y) for p, y in zip(probs, labels)) / n
    log_loss = -sum(math.log(min(max(p if y else 1 - p, eps), 1 - eps)) for p, y in zip(probs, labels)) / n
    brier = sum((p - y) ** 2 for p, y in zip(probs, labels)) / n
    groups: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for p, y in zip(probs, labels):
        groups[min(int(p * bins), bins - 1)].append((p, y))
    reliability, ece = [], 0.0
    for i, group in enumerate(groups):
        if not group:
            continue
        predicted = sum(p for p, _ in group) / len(group)
        observed = sum(y for _, y in group) / len(group)
        ece += len(group) / n * abs(predicted - observed)
        reliability.append({"lo": i / bins, "hi": (i + 1) / bins, "n": len(group),
                            "predicted": round(predicted, 4), "observed": round(observed, 4)})
    return {"n": n, "accuracy": round(accuracy, 4), "log_loss": round(log_loss, 4), "brier": round(brier, 4),
            "ece": round(ece, 4), "reliability": reliability}


# --- per-game WPA ------------------------------------------------------------------


def item_spans(items: list[dict], end: float) -> list[tuple[float, int, int, float]]:
    """(purchase second, participant, item, end second) of every purchase that was not undone (REQ-WPA-7).

    A purchase ends at the first sale or destruction of the item by the same player (each one ends
    the oldest still-open purchase of that item), else at the end of the game. An undone purchase is
    ignored and the destructions of the same player at its second (consumed components) are reopened;
    an undone sale reopens the purchase it had ended; a sale or destruction of nothing open is ignored."""
    records: list[dict] = []
    open_: dict[tuple[int, int], list[dict]] = defaultdict(list)
    sold: dict[tuple[int, int], list[dict]] = defaultdict(list)
    destroyed_at: dict[tuple[int, float], list[dict]] = defaultdict(list)

    def reopen(rec: dict) -> None:
        rec["end"] = None
        queue = open_[(rec["pid"], rec["item"])]
        queue.append(rec)
        queue.sort(key=lambda r: r["n"])

    for e in items:
        pid, action, t = e["participant"], e["action"], e["t"]
        if not 1 <= pid <= 10:
            continue
        if action == "P":
            rec = {"n": len(records), "t": t, "pid": pid, "item": e["item"], "end": None, "undone": False}
            records.append(rec)
            open_[(pid, e["item"])].append(rec)
        elif action in ("S", "D"):
            queue = open_.get((pid, e["item"]))
            if queue:
                rec = queue.pop(0)
                rec["end"] = t
                (sold[(pid, e["item"])] if action == "S" else destroyed_at[(pid, t)]).append(rec)
        elif action == "U":
            before, after = e["item"], e.get("after") or 0
            if before:
                queue = open_.get((pid, before))
                if queue:
                    rec = queue.pop()
                    rec["undone"] = True
                    for component in destroyed_at.pop((pid, rec["t"]), []):
                        reopen(component)
            elif after and sold.get((pid, after)):
                reopen(sold[(pid, after)].pop())
    return [(r["t"], r["pid"], r["item"], end if r["end"] is None else r["end"]) for r in records if not r["undone"]]


def game_wpa(model: Model, timeline: dict, tier: str | None, duration: float,
             players: dict[int, tuple] | None = None) -> dict:
    """Win probability curve and WPA of every kill, objective and purchase of one game.

    `duration` in seconds; `players`: participant -> (side, role, champion, Riot ID). Probabilities
    are Blue's; each ΔW is from the point of view of the team that acts."""
    memo: dict[float, float] = {}

    def w_at(t: float) -> float:
        if t not in memo:
            memo[t] = model.win_probability(state_at(timeline, t, tier))
        return memo[t]

    def view(side: int, dw: float) -> float:
        return dw if side == 1 else -dw

    minutes = [round(w_at(60 * m), 6) for m in range(int(duration // 60) + 1)]
    events = []
    for e in ordered_events(timeline):
        without = state_at(timeline, e["t"], tier, before=e["key"])
        w_without = model.win_probability(without)
        w_with = model.win_probability(with_event(without, e))
        events.append([e["t"], e["kind"], e["side"], e["detail"], e["killer"], e["victim"], round(w_with, 6),
                       round(view(e["side"], w_with - w_without), 6)])
    purchases = []
    for t, pid, item, end in item_spans(timeline.get("items") or [], duration):
        side = side_of(pid)
        w_x, w_z = w_at(t), w_at(end)
        purchases.append([t, pid, item, end, round(w_x if side == 1 else 1 - w_x, 6), round(view(side, w_z - w_x), 6)])
    return {"minutes": minutes, "events": events, "items": purchases,
            "players": {str(pid): list(p) for pid, p in (players or {}).items()}}


# --- storage format of a game's result ------------------------------------------------------
#
# `soloq_wpa.data` holds three separately compressed sections, so that each route decodes only
# what it needs: a small JSON summary (players, count and ΔW sum per event kind, kills), the
# purchases grouped by (item, context) as binary arrays, and the curve (minutes, events) as JSON.

SECTIONS = ("summary", "items", "curve")
_ITEM_CODES = ("i", "b", "i", "d", "d")  # item, context index, purchases, sum of W, sum of ΔW


def summarise(result: dict) -> dict:
    """The decoded form of a game's result (from `game_wpa`) that the aggregates read."""
    kinds: dict[str, list] = {}
    kills = []
    for _t, kind, _side, _detail, killer, victim, _w, dw in result["events"]:
        acc = kinds.setdefault(kind, [0, 0.0])
        acc[0] += 1
        acc[1] += dw
        if kind == "kill":
            kills.append([killer, victim, dw])
    groups: dict[tuple[int, int], list] = {}
    for _t, _pid, item, _end, w, dw in result["items"]:
        acc = groups.setdefault((item, CONTEXTS.index(_context(w))), [0, 0.0, 0.0])
        acc[0] += 1
        acc[1] += w
        acc[2] += dw
    items = tuple(array(code) for code in _ITEM_CODES)
    for (item, ctx), (n, w_sum, dw_sum) in groups.items():
        for column, value in zip(items, (item, ctx, n, w_sum, dw_sum)):
            column.append(value)
    return {"players": result["players"], "kinds": kinds, "kills": kills, "items": items,
            "curve": {"minutes": result["minutes"], "events": result["events"]}}


def encode(result: dict) -> bytes:
    s = summarise(result)
    summary = json.dumps({"players": s["players"], "kinds": s["kinds"], "kills": s["kills"]}, separators=(",", ":"))
    items = struct.pack("<I", len(s["items"][0])) + b"".join(column.tobytes() for column in s["items"])
    curve = json.dumps(s["curve"], separators=(",", ":"))
    parts = [zlib.compress(p, 6) for p in (summary.encode(), items, curve.encode())]
    return struct.pack("<III", *map(len, parts)) + b"".join(parts)


def decode(blob: bytes, parts=SECTIONS) -> dict:
    sizes = struct.unpack_from("<III", blob)
    offset, out = 12, {}
    for name, size in zip(SECTIONS, sizes):
        if name in parts:
            raw = zlib.decompress(blob[offset:offset + size])
            if name == "summary":
                out.update(json.loads(raw))
            elif name == "curve":
                out["curve"] = json.loads(raw)
            else:
                count, pos, columns = struct.unpack_from("<I", raw)[0], 4, []
                for code in _ITEM_CODES:
                    column = array(code)
                    column.frombytes(raw[pos:pos + count * column.itemsize])
                    pos += count * column.itemsize
                    columns.append(column)
                out["items"] = tuple(columns)
        offset += size
    return out


# --- cache (soloq_wpa) ------------------------------------------------------------------


def compute_game(conn: sqlite3.Connection, match_id: str, model: Model) -> dict | None:
    """WPA of a stored game, None when it has no usable timeline."""
    row = conn.execute("SELECT tier, duration FROM soloq_games WHERE match_id = ?", (match_id,)).fetchone()
    timeline = soloq.load_timeline(conn, match_id) if row else None
    duration = round((row["duration"] or 0) * 60) if row else 0
    if not usable(timeline, duration):
        return None
    players = {r["participant"]: (r["side"], r["role"], r["champion"], r["player"]) for r in conn.execute(
        "SELECT participant, side, role, champion, player FROM soloq_players WHERE match_id = ?", (match_id,))}
    return game_wpa(model, timeline, row["tier"], duration, players)


def _store(conn: sqlite3.Connection, match_id: str, key: str, result: dict | None) -> None:
    """Cache a result; a NULL `data` marks a game whose timeline is unusable."""
    try:
        conn.execute("INSERT OR REPLACE INTO soloq_wpa (match_id, model, data) VALUES (?, ?, ?)",
                     (match_id, key, encode(result) if result is not None else None))
    except sqlite3.IntegrityError:  # deleted by an import meanwhile
        pass


def purge_other_keys(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM soloq_wpa WHERE model != ?", (key,))


def results(conn: sqlite3.Connection, match_ids, model: Model, compute: bool = True,
            parts=SECTIONS) -> dict[str, dict]:
    """Cached WPA of the given games with a usable timeline, decoded (`parts` of `SECTIONS`); with
    `compute`, the missing ones are computed and stored (results of other keys are dropped)."""
    wanted = set(match_ids)
    out, known = {}, set()
    if len(wanted) <= 500:
        marks = ",".join("?" for _ in wanted)
        rows = conn.execute(f"SELECT match_id, data FROM soloq_wpa WHERE model = ? AND match_id IN ({marks})",
                            (model.key, *wanted))
    else:
        rows = conn.execute("SELECT match_id, data FROM soloq_wpa WHERE model = ?", (model.key,))
    for match_id, blob in rows:
        if match_id in wanted:
            known.add(match_id)
            if blob is not None:
                out[match_id] = decode(blob, parts)
    missing = wanted - known
    if compute and missing:
        purge_other_keys(conn, model.key)
        with_timeline = {r[0] for r in conn.execute("SELECT match_id FROM soloq_timelines")}
        for match_id in sorted(missing & with_timeline):
            result = compute_game(conn, match_id, model)
            _store(conn, match_id, model.key, result)
            if result is not None:
                out[match_id] = summarise(result)
        conn.commit()
    return out


def coverage(conn: sqlite3.Connection, match_ids, key: str) -> dict:
    """`computed` games of `match_ids` already in cache, out of the `total` with a usable timeline
    (games not processed yet count as usable)."""
    with_timeline = {r[0] for r in conn.execute("SELECT match_id FROM soloq_timelines")}
    scope = set(match_ids) & with_timeline
    computed = unusable = 0
    for match_id, has_data in conn.execute("SELECT match_id, data IS NOT NULL FROM soloq_wpa WHERE model = ?", (key,)):
        if match_id in scope:
            if has_data:
                computed += 1
            else:
                unusable += 1
    return {"computed": computed, "total": len(scope) - unusable}


class CacheJob:
    """Background computation of the games missing from `soloq_wpa` for a model (REQ-WPA-12)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.key: str | None = None

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def ensure(self, model: Model) -> None:
        """Start the job for `model` unless it already runs for it (a job for another model is stopped)."""
        if not soloq.SOLOQ_DB_PATH.exists():
            return
        with self._lock:
            if self.running():
                if self.key == model.key:
                    return
                self._stop.set()
                self._thread.join()
            self._stop = threading.Event()
            self.key = model.key
            self._thread = threading.Thread(target=self._run, args=(model, self._stop), daemon=True, name="wpa-cache")
            self._thread.start()

    def stop(self, timeout: float = 10) -> None:
        with self._lock:
            self._stop.set()
            if self._thread:
                self._thread.join(timeout)

    def join(self, timeout: float = 30) -> None:
        if self._thread:
            self._thread.join(timeout)

    @staticmethod
    def _run(model: Model, stop: threading.Event) -> None:
        conn = soloq.connect()
        try:
            purge_other_keys(conn, model.key)
            conn.commit()
            todo = [r[0] for r in conn.execute(
                "SELECT t.match_id FROM soloq_timelines t LEFT JOIN soloq_wpa w"
                " ON w.match_id = t.match_id AND w.model = ? WHERE w.match_id IS NULL ORDER BY t.match_id",
                (model.key,))]
            for i, match_id in enumerate(todo):
                if stop.is_set():
                    break
                _store(conn, match_id, model.key, compute_game(conn, match_id, model))
                if i % 50 == 49:
                    conn.commit()
            conn.commit()
        except sqlite3.Error:  # best effort: the next request starts it again
            pass
        finally:
            conn.close()


job = CacheJob()


# --- aggregates -----------------------------------------------------------------------------


def curve(conn: sqlite3.Connection, match_id: str, model: Model) -> dict | None:
    """Body of GET /api/wpa/game/{id}; None when the game has no usable timeline (raises KeyError if unknown)."""
    row = conn.execute("SELECT tier, duration, winner, patch FROM soloq_games WHERE match_id = ?", (match_id,)).fetchone()
    if row is None:
        raise KeyError(match_id)
    result = results(conn, [match_id], model, parts=("summary", "curve")).get(match_id)
    if not result:
        return None
    players = {int(pid): p for pid, p in result["players"].items()}

    def champion(pid: int) -> str | None:
        return players[pid][2] if pid in players else None

    events = [{"t": t, "kind": kind, "side": side, "detail": detail, "champion": champion(killer) if killer else None,
               "victim": champion(victim) if victim else None, "w": w, "dw": dw}
              for t, kind, side, detail, killer, victim, w, dw in result["curve"]["events"]]
    return {"minutes": result["curve"]["minutes"], "events": events, "winner": row["winner"],
            "duration": row["duration"], "tier": row["tier"], "patch": row["patch"]}


def _mean(total: float, n: int) -> float | None:
    return round(total / n, 4) if n else None


def _context(w: float) -> str:
    return "behind" if w < BEHIND else "ahead" if w > AHEAD else "even"


def items_table(games: dict[str, dict], min_n: int = 20, limit: int = 200) -> dict:
    """WPA per item (REQ-WPA-7), from the purchases of each game grouped by (item, context)."""
    per_item: dict[int, list] = {}  # [purchases, sum of W, sum of ΔW] for each context, in CONTEXTS order
    for result in games.values():
        for item, ctx, n, w, dw in zip(*result["items"]):
            row = per_item.get(item)
            if row is None:
                row = per_item[item] = [0, 0.0, 0.0] * len(CONTEXTS)
            i = 3 * ctx
            row[i] += n
            row[i + 1] += w
            row[i + 2] += dw
    base = [sum(row[i] for row in per_item.values()) for i in range(3 * len(CONTEXTS))]
    base_mean = [base[3 * c + 2] / base[3 * c] if base[3 * c] else None for c in range(len(CONTEXTS))]
    rows = []
    for item, row in per_item.items():
        n = row[0] + row[3] + row[6]
        if n < min_n:
            continue
        contexts, excess = {}, []
        for c, name in enumerate(CONTEXTS):
            cn, cdw = row[3 * c], row[3 * c + 2]
            excess.append(cdw / cn - base_mean[c] if cn else None)
            contexts[name] = {"n": cn, "dw": _mean(cdw, cn),
                              "excess": round(excess[-1], 4) if cn else None}
        profile = None
        if contexts["behind"]["n"] >= PROFILE_MIN and contexts["ahead"]["n"] >= PROFILE_MIN:
            gap = excess[0] - excess[2]
            profile = "comeback" if gap >= PROFILE_GAP - 1e-12 else "snowball" if gap <= -PROFILE_GAP + 1e-12 else "neutral"
        rows.append({"item": item, "n": n, "w": _mean(row[1] + row[4] + row[7], n),
                     "dw": _mean(row[2] + row[5] + row[8], n), "contexts": contexts, "profile": profile})
    rows.sort(key=lambda r: (-r["n"], r["item"]))
    base_n = base[0] + base[3] + base[6]
    baseline = {"n": base_n, "dw": _mean(base[2] + base[5] + base[8], base_n),
                **{name: {"n": base[3 * c], "dw": _mean(base[3 * c + 2], base[3 * c])} for c, name in enumerate(CONTEXTS)}}
    return {"baseline": baseline, "items": rows[:limit]}


def events_table(games: dict[str, dict]) -> dict:
    """WPA of kills and of each objective type (REQ-WPA-8)."""
    totals: dict[str, list] = {k: [0, 0.0] for k in ("kill", *OBJECTIVES)}
    for result in games.values():
        for kind, (n, dw) in result["kinds"].items():
            totals[kind][0] += n
            totals[kind][1] += dw
    kills = totals.pop("kill")
    return {"kills": {"n": kills[0], "dw": _mean(kills[1], kills[0])},
            "objectives": [{"kind": k, "n": n, "dw": _mean(s, n)} for k, (n, s) in totals.items() if n]}


def players_table(games: dict[str, dict], by: str = "champion", role: str | None = None,
                  min_games: int = 5, limit: int = 100) -> list[dict]:
    """Mean WPA per game of the kills and deaths of each champion or player (REQ-WPA-8)."""
    acc: dict[str, list] = defaultdict(lambda: [0, 0.0, 0.0])  # games, kills WPA, deaths WPA
    column = 2 if by == "champion" else 3
    for result in games.values():
        keys = {int(pid): p[column] for pid, p in result["players"].items() if p[column] and (not role or p[1] == role)}
        for key in set(keys.values()):
            acc[key][0] += 1
        for killer, victim, dw in result["kills"]:
            if killer in keys:
                acc[keys[killer]][1] += dw
            if victim in keys:
                acc[keys[victim]][2] -= dw  # the victim's team loses what the killer's team gains
    rows = [{by: key, "games": n, "wpa": round((k + d) / n, 4), "kills_wpa": round(k / n, 4),
             "deaths_wpa": round(d / n, 4)} for key, (n, k, d) in acc.items() if n >= min_games]
    rows.sort(key=lambda r: (-r["wpa"], -r["games"]))
    return rows[:limit]


_agg_cache: "OrderedDict[tuple, object]" = OrderedDict()
_agg_lock = threading.Lock()
_TABLES = {"items": items_table, "events": events_table, "players": players_table}
_PARTS = {"items": ("items",), "events": ("summary",), "players": ("summary",)}


def aggregate(kind: str, match_ids: list[str], model: Model, **params) -> dict:
    """An aggregate over the cached games of a scope, with its `coverage`; never waits for the
    computation of missing games, which the background job does."""
    if not soloq.SOLOQ_DB_PATH.exists() or not match_ids:
        cov, games = {"computed": 0, "total": 0}, {}
    else:
        conn = soloq.connect()
        try:
            cov = coverage(conn, match_ids, model.key)
            key = (kind, model.key, hash(tuple(sorted(match_ids))), len(match_ids), cov["computed"],
                   tuple(sorted(params.items())))
            with _agg_lock:
                if key in _agg_cache:
                    _agg_cache.move_to_end(key)
                    table = _agg_cache[key]
                    games = None
                else:
                    games = {}
            if games is not None:
                games = results(conn, match_ids, model, compute=False, parts=_PARTS[kind])
        finally:
            conn.close()
        if cov["computed"] < cov["total"]:
            job.ensure(model)
        if games is None:
            return _with_coverage(kind, table, cov)
    table = _TABLES[kind](games, **params)
    if games:
        with _agg_lock:
            _agg_cache[key] = table
            while len(_agg_cache) > 32:
                _agg_cache.popitem(last=False)
    return _with_coverage(kind, table, cov)


def _with_coverage(kind: str, table, cov: dict) -> dict:
    return {"coverage": cov, "rows": table} if kind == "players" else {"coverage": cov, **table}
