import socketio
import json
import random
import string
import os
from typing import Dict, Any, List
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# ==========================================
# 1. SERVER SETUP & INSTANTIATION
# ==========================================
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*', logger=False, engineio_logger=False)
app = FastAPI()
socket_app = socketio.ASGIApp(sio, app)

# ==========================================
# 2. QUESTIONS DATA INGESTION
# ==========================================
QUESTIONS_FILE = "questions.json"
questions_data: Dict[str, Any] = {}

if os.path.exists(QUESTIONS_FILE):
    try:
        with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
            questions_data = json.load(f)
        print("✅ QUESTIONS: Successfully loaded database into memory.")
    except Exception as e:
        print(f"❌ QUESTIONS: JSON file is corrupt or invalid! Error: {e}")
else:
    print(f"❌ QUESTIONS: Required file '{QUESTIONS_FILE}' is missing!")

# ==========================================
# 3. STATIC CONSTANTS & ROOM STATE MANAGER
# ==========================================
MONEY_LEVELS = [10, 25, 50, 75, 100, 250, 500, 750, 1000, 1500, 2000, 2500, 3000, 4000, 5000]

# Global dictionary tracking every active room state
# Key: room_code (str) -> Value: state (dict)
active_rooms: Dict[str, Dict[str, Any]] = {}

def generate_room_code(length: int = 5) -> str:
    """Generates a secure, readable random uppercase room code (e.g., BEE99)."""
    while True:
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=length))
        # Ensure lowercase combinations or bad lookalikes can be excluded if desired,
        # and prevent collisions with active rooms.
        if code not in active_rooms:
            return code

def get_initial_room_state(category: str = None) -> Dict[str, Any]:
    """Generates a fresh, sandboxed room state."""
    return {
        "phase": "waiting",
        "category": category,
        "levelIndex": 0,
        "currentQuestion": None,  # Shuffled option format sent to clients
        "activeMoney": 0,
        "selectedAnswer": None,   # Represents selected index of the *shuffled* options
        "hiddenAnswers": [],      # Indexes to hide during 50:50
        "lifelines": {"fifty": True, "phone": True, "audience": True},
        "phoneResult": None,
        "audienceData": None,
        "popup": None,
        "gameQueue": [],          # List of structured questions with their un-shuffled state
        "used_question_ids": set() # Sandboxed repeat protection per room
    }

# ==========================================
# 4. RANDOMIZATION & OPTION SHUFFLING ENGINE
# ==========================================
def prepare_shuffled_question(question_blueprint: Dict[str, Any]) -> Dict[str, Any]:
    """
    Takes a master question dictionary, extracts the correct answer,
    shuffles options dynamically, and updates the correct index relative
    to the brand new layout structure.
    """
    # Create a deep copy to avoid mutating the master dataset pool
    q_copy = json.loads(json.dumps(question_blueprint))
    
    options = q_copy["options"]
    original_answer_index = q_copy["answer"]
    correct_answer_text = options[original_answer_index]
    
    # Pair choices and shuffle
    shuffled_options = list(options)
    random.shuffle(shuffled_options)
    
    # Re-calculate correct answer's index based on its new layout position
    new_answer_index = shuffled_options.index(correct_answer_text)
    
    q_copy["options"] = shuffled_options
    q_copy["answer"] = new_answer_index
    # We strip explanation from payloads broadcasted to players to block client-side cheating
    if "explanation" in q_copy:
        del q_copy["explanation"]
        
    return q_copy

def select_room_questions(pool: List[Dict[str, Any]], q_type: str, count: int, used_ids: set) -> List[Dict[str, Any]]:
    """
    Filters, deduplicates, and extracts questions using session memory.
    Reshuffles the category deck if the pool is fully exhausted.
    """
    # Filter candidates matches
    candidates = [q for q in pool if q.get("type") == q_type]
    if not candidates:
        return []

    # Filter unused questions
    fresh = [q for q in candidates if q["id"] not in used_ids]
    selected = []

    if len(fresh) >= count:
        random.shuffle(fresh)
        selected = fresh[:count]
    else:
        # Take whatever unused questions are left
        selected.extend(fresh)
        
        # Reset memory for this specific candidate pool (Reshuffle)
        candidate_ids = {q["id"] for q in candidates}
        used_ids.difference_update(candidate_ids)
        
        # Pull remaining from refreshed candidate pool, skipping what was just selected
        selected_ids = {q["id"] for q in selected}
        refreshed_pool = [q for q in candidates if q["id"] not in selected_ids]
        random.shuffle(refreshed_pool)
        
        needed = count - len(selected)
        if len(refreshed_pool) < needed:
            # Absolute fallback if pool is critically small
            selected.extend(refreshed_pool)
            while len(selected) < count:
                selected.append(random.choice(candidates))
        else:
            selected.extend(refreshed_pool[:needed])

    # Record choices into room session history
    for q in selected:
        used_ids.add(q["id"])
        
    return selected

# ==========================================
# 5. ASYNC SOCKET.IO EVENT HANDLERS
# ==========================================
@sio.event
async def connect(sid, environ):
    # Sends handshake. Handled globally or dynamically once players provide room codes.
    pass

@sio.on("room:create")
async def create_room(sid, data=None):
    """Generates a dynamic digital room, registering the Host socket."""
    room_code = generate_room_code()
    active_rooms[room_code] = get_initial_room_state()
    
    await sio.enter_room(sid, room_code)
    print(f"🏠 Host ({sid}) established Room: {room_code}")
    
    # Notify host and send initial state sandbox
    return {"status": "success", "room": room_code, "state": active_rooms[room_code]}

@sio.on("room:join")
async def join_room(sid, data):
    """Adds player/viewers dynamically to a cloud room."""
    room_code = data.get("room_code", "").upper()
    if room_code not in active_rooms:
        return {"status": "error", "message": "Invalid Room Code!"}
        
    await sio.enter_room(sid, room_code)
    print(f"👤 Player ({sid}) entered Room: {room_code}")
    
    # Send state slice to newly joined client
    room_state = active_rooms[room_code].copy()
    if "used_question_ids" in room_state:
        del room_state["used_question_ids"] # Strip metadata from client update
        
    await sio.emit("state_update", room_state, to=sid)
    await sio.emit("player_joined", {"player_id": sid}, room=room_code, skip_sid=sid)
    return {"status": "success"}

@sio.on("host:start")
async def host_start(sid, data):
    """Initializes and kicks off gameplay within a specific room room context."""
    room_code = data.get("room_code")
    category_id = data.get("category_id")
    
    if room_code not in active_rooms or category_id not in questions_data:
        return

    print(f"🎲 Game starting inside Room {room_code} (Category: {category_id})")
    
    room = active_rooms[room_code]
    cat_data = questions_data[category_id]
    
    easy_pool = cat_data.get("easy", [])
    hard_pool = cat_data.get("hard", [])
    
    # Stage 1: Easy (5 Questions)
    stage1 = select_room_questions(easy_pool, 'text', 5, room["used_question_ids"])
    
    # Stage 2: Hard (10 Questions: 3 Image, 2 Audio, 5 Text)
    q_img = select_room_questions(hard_pool, 'image', 3, room["used_question_ids"])
    q_aud = select_room_questions(hard_pool, 'audio', 2, room["used_question_ids"])
    
    text_needed = 10 - (len(q_img) + len(q_aud))
    q_txt = select_room_questions(hard_pool, 'text', text_needed, room["used_question_ids"])
    
    stage2 = q_img + q_aud + q_txt
    random.shuffle(stage2)
    
    final_set = stage1 + stage2
    
    # Robustness Fallback: Pad to 15 questions if needed
    while len(final_set) < 15:
        fallback = random.choice(hard_pool) if hard_pool else final_set[0]
        final_set.append(fallback)
        
    final_set = final_set[:15]
    
    # Live Options Shuffling applied to the very first question
    current_shuffled = prepare_shuffled_question(final_set[0])
    
    # Reset room status
    room.update({
        "phase": "question",
        "category": category_id,
        "levelIndex": 0,
        "currentQuestion": current_shuffled,
        "gameQueue": final_set,
        "activeMoney": 0,
        "selectedAnswer": None,
        "hiddenAnswers": [],
        "lifelines": {"fifty": True, "phone": True, "audience": True},
        "phoneResult": None,
        "audienceData": None,
        "popup": None
    })
    
    emit_state = room.copy()
    del emit_state["used_question_ids"]
    await sio.emit("state_update", emit_state, room=room_code)

@sio.on("player:answer")
async def player_answer(sid, data):
    """Fires when user locks an answer inside their room."""
    room_code = data.get("room_code")
    index = int(data.get("index"))
    
    if room_code not in active_rooms:
        return
        
    room = active_rooms[room_code]
    if room["phase"] == "question" and room["selectedAnswer"] is None:
        print(f"👤 Answer Selection in Room {room_code}: Choice {index}")
        room["selectedAnswer"] = index
        
        emit_state = room.copy()
        del emit_state["used_question_ids"]
        await sio.emit("state_update", emit_state, room=room_code)

@sio.on("host:reveal")
async def host_reveal(sid, data):
    room_code = data.get("room_code")
    if room_code not in active_rooms:
        return
        
    room = active_rooms[room_code]
    if room["phase"] != "question" or room["selectedAnswer"] is None:
        return
        
    correct = room["currentQuestion"]["answer"]
    
    if room["selectedAnswer"] == correct:
        print(f"✅ Correct Choice in Room {room_code}!")
        room["activeMoney"] = MONEY_LEVELS[room["levelIndex"]]
        room["phase"] = "revealed"
    else:
        print(f"❌ Wrong Choice in Room {room_code}!")
        prize = MONEY_LEVELS[room["levelIndex"] - 1] if room["levelIndex"] > 0 else 0
        room["activeMoney"] = prize
        room["phase"] = "gameover"
        room["popup"] = {
            "title": "❌ WRONG ANSWER",
            "text": f"Correct: {room['currentQuestion']['options'][correct]}\nYou win: ₹{prize}"
        }
        
    emit_state = room.copy()
    del emit_state["used_question_ids"]
    await sio.emit("state_update", emit_state, room=room_code)

@sio.on("host:next")
async def host_next(sid, data):
    room_code = data.get("room_code")
    if room_code not in active_rooms:
        return
        
    room = active_rooms[room_code]
    MAX = 14
    
    if room["levelIndex"] == MAX:
        if room["phase"] == "revealed":
            room["phase"] = "gameover"
            room["popup"] = {"title": "🏆 WINNER!", "text": f"Top Prize: ₹{MONEY_LEVELS[MAX]}!"}
            emit_state = room.copy()
            del emit_state["used_question_ids"]
            await sio.emit("state_update", emit_state, room=room_code)
        return

    if room["levelIndex"] < MAX:
        room["levelIndex"] += 1
        # Prepare and shuffle options for the next sequential deck question dynamically[cite: 1]
        next_raw_question = room["gameQueue"][room["levelIndex"]]
        room["currentQuestion"] = prepare_shuffled_question(next_raw_question)
        
        room["phase"] = "question"
        room["selectedAnswer"] = None
        room["hiddenAnswers"] = []
        room["phoneResult"] = None
        room["audienceData"] = None
        
        emit_state = room.copy()
        del emit_state["used_question_ids"]
        await sio.emit("state_update", emit_state, room=room_code)

@sio.on("lifeline:fifty")
async def fifty(sid, data):
    room_code = data.get("room_code")
    if room_code not in active_rooms:
        return
    room = active_rooms[room_code]
    
    if room["lifelines"]["fifty"] and room["phase"] == "question":
        room["lifelines"]["fifty"] = False
        correct = room["currentQuestion"]["answer"]
        wrong = [i for i in range(4) if i != correct]
        room["hiddenAnswers"] = random.sample(wrong, 2)
        
        emit_state = room.copy()
        del emit_state["used_question_ids"]
        await sio.emit("state_update", emit_state, room=room_code)

@sio.on("lifeline:phone")
async def phone(sid, data):
    room_code = data.get("room_code")
    if room_code not in active_rooms:
        return
    room = active_rooms[room_code]
    
    if room["lifelines"]["phone"] and room["phase"] == "question":
        room["lifelines"]["phone"] = False
        room["phoneResult"] = room["currentQuestion"]["answer"]
        
        emit_state = room.copy()
        del emit_state["used_question_ids"]
        await sio.emit("state_update", emit_state, room=room_code)

@sio.on("lifeline:audience")
async def audience(sid, data):
    room_code = data.get("room_code")
    if room_code not in active_rooms:
        return
    room = active_rooms[room_code]
    
    if room["lifelines"]["audience"] and room["phase"] == "question":
        room["lifelines"]["audience"] = False
        correct_idx = room["currentQuestion"]["answer"]
        
        p = [10, 10, 10, 10]
        p[correct_idx] = 70  # Audience heavily favors correct answer
        room["audienceData"] = p
        
        emit_state = room.copy()
        del emit_state["used_question_ids"]
        await sio.emit("state_update", emit_state, room=room_code)

@sio.on("host:reset")
async def host_reset(sid, data):
    room_code = data.get("room_code")
    if room_code not in active_rooms:
        return
    print(f"🔄 Resetting room {room_code}")
    active_rooms[room_code] = get_initial_room_state()
    
    emit_state = active_rooms[room_code].copy()
    del emit_state["used_question_ids"]
    await sio.emit("state_update", emit_state, room=room_code)

# ==========================================
# 6. ROUTE DEFINITIONS
# ==========================================
@app.get("/")
async def get_host(): return FileResponse("public/host.html")
@app.get("/player")
async def get_player(): return FileResponse("public/player.html")
@app.get("/viewer")
async def get_viewer(): return FileResponse("public/viewer.html")
@app.get("/favicon.ico")
async def favicon(): return FileResponse("public/img/logo.jpg")

app.mount("/", StaticFiles(directory="public"), name="public")

if __name__ == "__main__":
    import uvicorn
    # This server binds globally on Port 8000. Under cloud deployment platforms
    # like Render, Railway, or Heroku, the port should fall back to the dynamic $PORT environment variable.
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 CENTRAL SIGNALING SERVER INITIALIZED ON PORT {port}")
    uvicorn.run(socket_app, host="0.0.0.0", port=port)
