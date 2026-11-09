import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'justprice.db'}")
if os.getenv("VERCEL") and not os.getenv("DATABASE_URL"):
    raise RuntimeError("Set DATABASE_URL in the Vercel project environment variables.")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine_options: dict[str, Any] = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite:"):
    engine_options["connect_args"] = {"check_same_thread": False}
else:
    engine_options["connect_args"] = {"prepare_threshold": None}
engine = create_engine(DATABASE_URL, **engine_options)
mutation_lock = threading.RLock()
TURN_SECONDS = 15
ROUND_BREAK_SECONDS = 15
ROOM_CAPACITY = 6
STARTING_TOKENS = 200
DAILY_TOKENS = 20
TOKENS_PER_POINT = 10
TOKENS_PER_CONVERTED_POINT = 7

app = FastAPI(title="Just Price Dexter API")
origins = [
    item.strip()
    for item in os.getenv(
        "CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000,http://localhost:5500"
    ).split(",")
    if item.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=8, max_length=128)


class RoomCreate(BaseModel):
    name: str = Field(min_length=2, max_length=32)
    private: bool = False


class JoinRoom(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class Guess(BaseModel):
    number: int = Field(ge=1, le=1000)


class Quantity(BaseModel):
    quantity: int = Field(gt=0, le=10000)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return now_utc().isoformat()


def init_db() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    tokens INTEGER NOT NULL,
                    points INTEGER NOT NULL,
                    daily_claimed_on TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS rooms (
                    id TEXT PRIMARY KEY,
                    code TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    is_private INTEGER NOT NULL,
                    host_id TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
        )


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def password_digest(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000).hex()


def make_session(conn: Any, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires = (now_utc() + timedelta(days=30)).isoformat()
    conn.execute(
        text("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (:hash, :user, :expires)"),
        {"hash": token_hash, "user": user_id, "expires": expires},
    )
    return token


def current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Connecte-toi pour continuer.")
    token_hash = hashlib.sha256(authorization[7:].encode("utf-8")).hexdigest()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT accounts.id, accounts.username, accounts.tokens, accounts.points
                FROM sessions JOIN accounts ON accounts.id = sessions.user_id
                WHERE sessions.token_hash = :hash AND sessions.expires_at > :now
                """
            ),
            {"hash": token_hash, "now": iso_now()},
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=401, detail="Session expirée. Reconnecte-toi.")
    return dict(row)


def account(conn: Any, user_id: str) -> dict[str, Any]:
    row = conn.execute(
        text("SELECT id, username, tokens, points, daily_claimed_on FROM accounts WHERE id = :id"),
        {"id": user_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Compte introuvable.")
    return dict(row)


def get_room(conn: Any, room_id: str) -> dict[str, Any]:
    row = conn.execute(
        text("SELECT * FROM rooms WHERE id = :id"), {"id": room_id}
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Room introuvable.")
    room = dict(row)
    room["state"] = json.loads(room.pop("state_json"))
    return room


def save_room(conn: Any, room: dict[str, Any]) -> None:
    conn.execute(
        text("UPDATE rooms SET state_json = :state, host_id = :host WHERE id = :id"),
        {"id": room["id"], "state": json.dumps(room["state"]), "host": room["host_id"]},
    )


def member_ids(state: dict[str, Any]) -> list[str]:
    return [player["user_id"] for player in state["players"]]


def start_round(conn: Any, room: dict[str, Any]) -> None:
    state = room["state"]
    eligible = []
    for player in state["players"]:
        if account(conn, player["user_id"])["points"] > 0:
            eligible.append(player["user_id"])
    if len(eligible) < 2:
        state["phase"] = "waiting"
        state["current_turn_id"] = None
        state["deadline"] = None
        state["message"] = "En attente d’au moins 2 joueurs avec des points."
        return
    secrets.SystemRandom().shuffle(eligible)
    state["round_number"] += 1
    state["turn_order"] = eligible
    state["last_turn_id"] = None
    state["current_turn_id"] = eligible[0]
    state["secret"] = secrets.randbelow(1000) + 1
    state["history"] = []
    state["eliminated"] = []
    state["winner_id"] = None
    state["winner_name"] = None
    state["phase"] = "playing"
    state["deadline"] = (now_utc() + timedelta(seconds=TURN_SECONDS)).isoformat()
    state["message"] = f"Manche {state['round_number']} : à toi de jouer !"


def select_next_turn(conn: Any, state: dict[str, Any], previous_id: str | None) -> None:
    order = state["turn_order"]
    if not order:
        state["phase"] = "waiting"
        state["current_turn_id"] = None
        state["deadline"] = None
        state["message"] = "Tous les joueurs sont éliminés. La cagnotte est reportée."
        return
    start = order.index(previous_id) if previous_id in order else -1
    for offset in range(1, len(order) + 1):
        candidate = order[(start + offset) % len(order)]
        if account(conn, candidate)["points"] > 0:
            state["current_turn_id"] = candidate
            state["last_turn_id"] = previous_id
            state["deadline"] = (now_utc() + timedelta(seconds=TURN_SECONDS)).isoformat()
            return
        if candidate not in state["eliminated"]:
            state["eliminated"].append(candidate)
    state["phase"] = "waiting"
    state["current_turn_id"] = None
    state["deadline"] = None
    state["message"] = "Tous les joueurs sont éliminés. La cagnotte est reportée."


def apply_guess(
    conn: Any, room: dict[str, Any], player_id: str, number: int, automatic: bool = False
) -> None:
    state = room["state"]
    player = account(conn, player_id)
    if player["points"] <= 0:
        if player_id not in state["eliminated"]:
            state["eliminated"].append(player_id)
        select_next_turn(conn, state, player_id)
        return

    conn.execute(
        text("UPDATE accounts SET points = points - 1 WHERE id = :id"), {"id": player_id}
    )
    state["pot"] += 1
    result = "trouvé !" if number == state["secret"] else (
        "plus grand" if number < state["secret"] else "plus petit"
    )
    state["history"].append(
        {
            "username": player["username"],
            "number": number,
            "result": result,
            "automatic": automatic,
            "created_at": iso_now(),
        }
    )
    if number == state["secret"]:
        conn.execute(
            text("UPDATE accounts SET points = points + :pot WHERE id = :id"),
            {"id": player_id, "pot": state["pot"]},
        )
        state["winner_id"] = player_id
        state["winner_name"] = player["username"]
        state["phase"] = "finished"
        state["current_turn_id"] = None
        state["deadline"] = None
        state["message"] = f"{player['username']} a trouvé le nombre et remporte {state['pot']} points !"
        state["pot"] = 0
        state["next_round_at"] = (now_utc() + timedelta(seconds=ROUND_BREAK_SECONDS)).isoformat()
        return

    if player["points"] - 1 == 0 and player_id not in state["eliminated"]:
        state["eliminated"].append(player_id)
    state["message"] = f"{player['username']} propose {number} : {result}."
    select_next_turn(conn, state, player_id)


def tick_room(conn: Any, room: dict[str, Any]) -> None:
    state = room["state"]
    now = now_utc()
    if state["phase"] == "waiting":
        start_round(conn, room)
    if state["phase"] == "playing" and state["deadline"]:
        if datetime.fromisoformat(state["deadline"]) <= now:
            player_id = state["current_turn_id"]
            if player_id:
                apply_guess(conn, room, player_id, secrets.randbelow(1000) + 1, automatic=True)
    if state["phase"] == "finished" and state["next_round_at"]:
        if datetime.fromisoformat(state["next_round_at"]) <= now:
            state["phase"] = "waiting"
            state["next_round_at"] = None
            start_round(conn, room)


def room_payload(conn: Any, room: dict[str, Any], user_id: str) -> dict[str, Any]:
    state = room["state"]
    players = []
    for player in state["players"]:
        wallet = account(conn, player["user_id"])
        players.append(
            {
                "user_id": player["user_id"],
                "username": wallet["username"],
                "tokens": wallet["tokens"],
                "points": wallet["points"],
                "is_host": player["user_id"] == room["host_id"],
                "eliminated": player["user_id"] in state["eliminated"],
            }
        )
    return {
        "id": room["id"],
        "code": room["code"],
        "name": room["name"],
        "private": bool(room["is_private"]),
        "host_id": room["host_id"],
        "round_number": state["round_number"],
        "phase": state["phase"],
        "current_turn_id": state["current_turn_id"],
        "deadline": state["deadline"],
        "message": state["message"],
        "pot": state["pot"],
        "turn_order": state["turn_order"],
        "players": players,
        "history": state["history"],
        "winner_id": state.get("winner_id"),
        "winner_name": state.get("winner_name"),
        "next_round_at": state.get("next_round_at"),
        "target": state["secret"] if state["phase"] == "finished" else None,
        "is_member": user_id in member_ids(state),
    }


def require_member(room: dict[str, Any], user_id: str) -> None:
    if user_id not in member_ids(room["state"]):
        raise HTTPException(status_code=403, detail="Tu ne fais pas partie de cette room.")


def member_record(user: dict[str, Any]) -> dict[str, str]:
    return {"user_id": user["id"], "username": user["username"]}


@app.post("/api/register")
def register(credentials: Credentials) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", credentials.username):
        raise HTTPException(
            status_code=400, detail="Le pseudo doit contenir 3 à 20 lettres, chiffres ou _."
        )
    salt = secrets.token_bytes(16)
    user_id = str(uuid.uuid4())
    with mutation_lock, engine.begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM accounts WHERE lower(username) = lower(:username)"),
            {"username": credentials.username},
        ).first()
        if exists:
            raise HTTPException(status_code=409, detail="Ce pseudo est déjà utilisé.")
        conn.execute(
            text(
                """
                INSERT INTO accounts (id, username, salt, password_hash, tokens, points, created_at)
                VALUES (:id, :username, :salt, :password_hash, :tokens, 0, :created_at)
                """
            ),
            {
                "id": user_id,
                "username": credentials.username,
                "salt": salt.hex(),
                "password_hash": password_digest(credentials.password, salt),
                "tokens": STARTING_TOKENS,
                "created_at": iso_now(),
            },
        )
        token = make_session(conn, user_id)
    return {"token": token, "user": {"id": user_id, "username": credentials.username, "tokens": STARTING_TOKENS, "points": 0}}


@app.post("/api/login")
def login(credentials: Credentials) -> dict[str, Any]:
    with mutation_lock, engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM accounts WHERE lower(username) = lower(:username)"),
            {"username": credentials.username},
        ).mappings().first()
        if row is None or not hmac.compare_digest(
            row["password_hash"],
            password_digest(credentials.password, bytes.fromhex(row["salt"])),
        ):
            raise HTTPException(status_code=401, detail="Pseudo ou mot de passe incorrect.")
        token = make_session(conn, row["id"])
        user = {"id": row["id"], "username": row["username"], "tokens": row["tokens"], "points": row["points"]}
    return {"token": token, "user": user}


@app.get("/api/me")
def get_me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with engine.begin() as conn:
        row = account(conn, user["id"])
        today = now_utc().date().isoformat()
        return {
            "id": row["id"],
            "username": row["username"],
            "tokens": row["tokens"],
            "points": row["points"],
            "daily_available": row["daily_claimed_on"] != today,
        }


@app.post("/api/daily")
def claim_daily(user: dict[str, Any] = Depends(current_user)) -> dict[str, int]:
    today = now_utc().date().isoformat()
    with mutation_lock, engine.begin() as conn:
        row = account(conn, user["id"])
        if row["daily_claimed_on"] == today:
            raise HTTPException(status_code=409, detail="Bonus quotidien déjà récupéré aujourd’hui.")
        conn.execute(
            text(
                "UPDATE accounts SET tokens = tokens + :bonus, daily_claimed_on = :today WHERE id = :id"
            ),
            {"bonus": DAILY_TOKENS, "today": today, "id": user["id"]},
        )
        updated = account(conn, user["id"])
    return {"tokens": updated["tokens"], "points": updated["points"]}


@app.post("/api/store/buy")
def buy_points(
    body: Quantity, user: dict[str, Any] = Depends(current_user)
) -> dict[str, int]:
    cost = body.quantity * TOKENS_PER_POINT
    with mutation_lock, engine.begin() as conn:
        wallet = account(conn, user["id"])
        if wallet["tokens"] < cost:
            raise HTTPException(status_code=400, detail=f"Il te faut {cost} tokens pour cet achat.")
        conn.execute(
            text("UPDATE accounts SET tokens = tokens - :cost, points = points + :qty WHERE id = :id"),
            {"cost": cost, "qty": body.quantity, "id": user["id"]},
        )
        for room_row in conn.execute(text("SELECT * FROM rooms")).mappings().all():
            room = dict(room_row)
            room["state"] = json.loads(room.pop("state_json"))
            state = room["state"]
            if user["id"] not in member_ids(state):
                continue
            if state["phase"] == "playing" and user["id"] in state["eliminated"]:
                state["eliminated"].remove(user["id"])
                state["turn_order"] = [pid for pid in state["turn_order"] if pid != user["id"]]
                state["turn_order"].append(user["id"])
                state["message"] = f"{wallet['username']} a acheté des points et rejoint la fin de l’ordre des tours."
                save_room(conn, room)
            elif state["phase"] == "waiting":
                start_round(conn, room)
                save_room(conn, room)
        updated = account(conn, user["id"])
    return {"tokens": updated["tokens"], "points": updated["points"]}


@app.post("/api/store/convert")
def convert_points(
    body: Quantity, user: dict[str, Any] = Depends(current_user)
) -> dict[str, int]:
    with mutation_lock, engine.begin() as conn:
        wallet = account(conn, user["id"])
        if wallet["points"] < body.quantity:
            raise HTTPException(status_code=400, detail="Tu n’as pas assez de points à convertir.")
        conn.execute(
            text(
                "UPDATE accounts SET points = points - :qty, tokens = tokens + :tokens WHERE id = :id"
            ),
            {
                "qty": body.quantity,
                "tokens": body.quantity * TOKENS_PER_CONVERTED_POINT,
                "id": user["id"],
            },
        )
        updated = account(conn, user["id"])
    return {"tokens": updated["tokens"], "points": updated["points"]}


@app.get("/api/rooms")
def list_rooms(user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    result = []
    with mutation_lock, engine.begin() as conn:
        for row in conn.execute(text("SELECT * FROM rooms WHERE is_private = 0")).mappings().all():
            room = dict(row)
            room["state"] = json.loads(room.pop("state_json"))
            tick_room(conn, room)
            save_room(conn, room)
            count = len(room["state"]["players"])
            if count < ROOM_CAPACITY:
                result.append(
                    {
                        "id": room["id"],
                        "code": room["code"],
                        "name": room["name"],
                        "players": count,
                        "phase": room["state"]["phase"],
                    }
                )
    return result


@app.post("/api/rooms")
def create_room(
    body: RoomCreate, user: dict[str, Any] = Depends(current_user)
) -> dict[str, Any]:
    room_id = str(uuid.uuid4())
    state = {
        "players": [member_record(user)],
        "phase": "waiting",
        "round_number": 0,
        "turn_order": [],
        "current_turn_id": None,
        "last_turn_id": None,
        "deadline": None,
        "secret": None,
        "history": [],
        "eliminated": [],
        "pot": 0,
        "message": "En attente d’un deuxième joueur.",
        "next_round_at": None,
    }
    with mutation_lock, engine.begin() as conn:
        code = secrets.token_hex(3).upper()
        while conn.execute(text("SELECT 1 FROM rooms WHERE code = :code"), {"code": code}).first():
            code = secrets.token_hex(3).upper()
        conn.execute(
            text(
                """
                INSERT INTO rooms (id, code, name, is_private, host_id, state_json, created_at)
                VALUES (:id, :code, :name, :private, :host, :state, :created_at)
                """
            ),
            {
                "id": room_id,
                "code": code,
                "name": body.name,
                "private": int(body.private),
                "host": user["id"],
                "state": json.dumps(state),
                "created_at": iso_now(),
            },
        )
    return {"id": room_id, "code": code}


@app.post("/api/rooms/join")
def join_room(body: JoinRoom, user: dict[str, Any] = Depends(current_user)) -> dict[str, str]:
    with mutation_lock, engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM rooms WHERE code = :code"), {"code": body.code.upper()}
        ).mappings().first()
        if row is None:
            raise HTTPException(status_code=404, detail="Aucune room ne correspond à ce code.")
        room = dict(row)
        room["state"] = json.loads(room.pop("state_json"))
        state = room["state"]
        if user["id"] not in member_ids(state):
            if len(state["players"]) >= ROOM_CAPACITY:
                raise HTTPException(status_code=409, detail="Cette room est complète (6 joueurs maximum).")
            state["players"].append(member_record(user))
            if state["phase"] == "playing" and user["id"] not in state["turn_order"]:
                state["turn_order"].append(user["id"])
                if account(conn, user["id"])["points"] <= 0:
                    state["eliminated"].append(user["id"])
            elif state["phase"] == "waiting":
                start_round(conn, room)
        save_room(conn, room)
    return {"id": room["id"]}


@app.get("/api/rooms/{room_id}")
def get_room_state(
    room_id: str, user: dict[str, Any] = Depends(current_user)
) -> dict[str, Any]:
    with mutation_lock, engine.begin() as conn:
        room = get_room(conn, room_id)
        require_member(room, user["id"])
        tick_room(conn, room)
        save_room(conn, room)
        return room_payload(conn, room, user["id"])


@app.post("/api/rooms/{room_id}/guess")
def submit_guess(
    room_id: str, body: Guess, user: dict[str, Any] = Depends(current_user)
) -> dict[str, Any]:
    with mutation_lock, engine.begin() as conn:
        room = get_room(conn, room_id)
        require_member(room, user["id"])
        tick_room(conn, room)
        state = room["state"]
        if state["phase"] != "playing":
            raise HTTPException(status_code=409, detail="Aucune manche n’est en cours.")
        if state["current_turn_id"] != user["id"]:
            raise HTTPException(status_code=409, detail="Ce n’est pas ton tour.")
        apply_guess(conn, room, user["id"], body.number)
        save_room(conn, room)
        return room_payload(conn, room, user["id"])


@app.delete("/api/rooms/{room_id}/leave")
def leave_room(
    room_id: str, user: dict[str, Any] = Depends(current_user)
) -> dict[str, str]:
    with mutation_lock, engine.begin() as conn:
        room = get_room(conn, room_id)
        require_member(room, user["id"])
        state = room["state"]
        was_turn = state["phase"] == "playing" and state["current_turn_id"] == user["id"]
        old_turn_index = (
            state["turn_order"].index(user["id"])
            if was_turn and user["id"] in state["turn_order"]
            else None
        )
        state["players"] = [
            player for player in state["players"] if player["user_id"] != user["id"]
        ]
        state["turn_order"] = [pid for pid in state["turn_order"] if pid != user["id"]]
        if user["id"] in state["eliminated"]:
            state["eliminated"].remove(user["id"])
        if room["host_id"] == user["id"] and state["players"]:
            room["host_id"] = state["players"][0]["user_id"]
        if was_turn:
            previous_id = (
                state["turn_order"][(old_turn_index - 1) % len(state["turn_order"])]
                if state["turn_order"] and old_turn_index is not None
                else None
            )
            select_next_turn(conn, state, previous_id)
        if not state["players"]:
            conn.execute(text("DELETE FROM rooms WHERE id = :id"), {"id": room_id})
        else:
            save_room(conn, room)
    return {"message": "Tu as quitté la room."}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
