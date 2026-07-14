import socketio
import json
import random
import socket
import os
from typing import List, Dict, Set
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# ==========================================
# 1. SERVER SETUP
# ==========================================
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*', logger=False, engineio_logger=False)
app = FastAPI()
socket_app = socketio.ASGIApp(sio, app)

# ==========================================
# 2. DIAGNOSTICS
# ==========================================
print("\n" + "="*40)
print("🚀 SERVER STARTING...")

if os.path.exists("questions.json"):
    try:
        with open("questions.json", "r", encoding="utf-8") as f:
            questions_data = json.load(f)
        print("✅ QUESTIONS: Loaded.")
    except:
        print("❌ QUESTIONS: JSON file is corrupt!")
else:
    print("❌ QUESTIONS: File missing!")

print("="*40 + "\n")

# ==========================================
# 3. GAME LOGIC
# ==========================================
MONEY_LEVELS = [10, 25, 50, 75, 100, 250, 500, 750, 1000, 1500, 2000, 2500, 3000, 4000, 5000]
global_used_ids: Set[str] = set()
questions_data = {}

try:
    with open("questions.json", "r", encoding="utf-8") as f:
        questions_data = json.load(f)
except: pass

def get_initial_state():
    return {
        "phase": "waiting", "category": None, "levelIndex": 0, "currentQuestion": None,
        "activeMoney": 0, "selectedAnswer": None, "hiddenAnswers": [],
        "lifelines": {"fifty": True, "phone": True, "audience": True},
        "phoneResult": None, "audienceData": None, "popup": None, "gameQueue": []
    }

game_state = get_initial_state()

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 1))
        IP = s.getsockname()[0]
    except: IP = '127.0.0.1'
    finally: s.close()
    return IP

# 🟢 NEW SMART SELECTOR: "Reshuffles the Deck" when empty
def select_unique_questions(pool, q_type, count):
    # 1. Get all valid candidates of this type
    candidates = [q for q in pool if q.get("type") == q_type]
    
    # Safety: If no questions exist at all, return empty
    if not candidates: return []

    # 2. Separate Fresh vs Used
    fresh = [q for q in candidates if q["id"] not in global_used_ids]
    selected = []

    # 3. SCENARIO A: We have enough fresh questions
    if len(fresh) >= count:
        random.shuffle(fresh)
        selected = fresh[:count]
    
    # 4. SCENARIO B: Not enough fresh questions -> Time to RESHUFFLE!
    else:
        # Take whatever fresh ones are left
        selected.extend(fresh)
        
        # Now, "forget" the used history for this specific category (Reshuffle)
        candidate_ids = {q["id"] for q in candidates}
        global_used_ids.difference_update(candidate_ids)
        
        # Get the pool again (excluding the ones we JUST picked in 'selected')
        selected_ids = {q["id"] for q in selected}
        refreshed_pool = [q for q in candidates if q["id"] not in selected_ids]
        random.shuffle(refreshed_pool)
        
        needed = count - len(selected)
        
        # SCENARIO C: The file is tiny (Total questions < Request count)
        if len(refreshed_pool) < needed:
             # We forced to repeat within same game because file is too small
             selected.extend(refreshed_pool)
             while len(selected) < count:
                 selected.append(random.choice(candidates))
        else:
            # Normal fill from reshuffled deck
            selected.extend(refreshed_pool[:needed])

    # 5. Mark these new picks as used
    for q in selected: global_used_ids.add(q["id"])
    
    return selected

# ==========================================
# 4. SOCKET EVENTS
# ==========================================
@sio.event
async def connect(sid, environ):
    await sio.emit('state_update', game_state, to=sid)

@sio.on("host:start")
async def host_start(sid, category_id):
    global game_state
    if category_id not in questions_data: return
    
    print(f"🎲 NEW GAME STARTED: {category_id}")
    cat_data = questions_data[category_id]
    
    easy_pool = cat_data.get("easy", [])
    hard_pool = cat_data.get("hard", [])
    
    # --- STAGE 1: EASY (5 Questions) ---
    stage1_questions = select_unique_questions(easy_pool, 'text', 5)

    # --- STAGE 2: HARD (10 Questions: Media + Text) ---
    q_img = select_unique_questions(hard_pool, 'image', 3)
    q_aud = select_unique_questions(hard_pool, 'audio', 2)
    
    slots_filled = len(q_img) + len(q_aud)
    text_needed = 10 - slots_filled
    
    q_txt = select_unique_questions(hard_pool, 'text', text_needed)
    
    stage2_questions = q_img + q_aud + q_txt
    random.shuffle(stage2_questions)
    
    # Combine
    final_game_set = stage1_questions + stage2_questions
    
    # Safety Check: Ensure we have exactly 15 (Fill with random hard if short)
    while len(final_game_set) < 15:
         final_game_set.append(random.choice(hard_pool) if hard_pool else final_game_set[0])

    final_game_set = final_game_set[:15]
    
    game_state = get_initial_state()
    game_state.update({
        "phase": "question", "category": category_id,
        "currentQuestion": final_game_set[0], "gameQueue": final_game_set
    })
    
    print(f"   👉 Loaded: {len(stage1_questions)} Easy / {len(stage2_questions)} Hard")
    await sio.emit("state_update", game_state)

@sio.on("host:reset")
async def host_reset(sid):
    print("🔄 GAME RESET")
    global game_state
    game_state = get_initial_state()
    await sio.emit("state_update", game_state)

@sio.on("player:answer")
async def player_answer(sid, index):
    if game_state["phase"] == "question" and game_state["selectedAnswer"] is None:
        print(f"👤 Player Answered: {index}")
        game_state["selectedAnswer"] = int(index)
        await sio.emit("state_update", game_state)

@sio.on("host:reveal")
async def host_reveal(sid):
    if game_state["phase"] != "question" or game_state["selectedAnswer"] is None: return
    correct = game_state["currentQuestion"]["answer"]
    
    if game_state["selectedAnswer"] == correct:
        print("   ✅ Correct Answer!")
        game_state["activeMoney"] = MONEY_LEVELS[game_state["levelIndex"]]
        game_state["phase"] = "revealed"
    else:
        print("   ❌ Wrong Answer!")
        prize = MONEY_LEVELS[game_state["levelIndex"] - 1] if game_state["levelIndex"] > 0 else 0
        game_state["activeMoney"] = prize
        game_state["phase"] = "gameover"
        game_state["popup"] = {"title": "❌ WRONG ANSWER", "text": f"Correct: {game_state['currentQuestion']['options'][correct]}\nYou win: ₹{prize}"}
    await sio.emit("state_update", game_state)

@sio.on("host:next")
async def host_next(sid):
    MAX = 14
    if game_state["levelIndex"] == MAX:
        if game_state["phase"] == "revealed":
            print("🏆 WINNER!")
            game_state["phase"] = "gameover"
            game_state["popup"] = {"title": "🏆 WINNER!", "text": f"Top Prize: ₹{MONEY_LEVELS[MAX]}!"}
            await sio.emit("state_update", game_state)
        else:
            print("⚠️ Click REVEAL first!")
        return

    if game_state["levelIndex"] < MAX:
        print(f"⏩ Moving to Level {game_state['levelIndex'] + 2}")
        game_state["levelIndex"] += 1
        game_state["currentQuestion"] = game_state["gameQueue"][game_state["levelIndex"]]
        game_state["phase"] = "question"
        game_state["selectedAnswer"] = None
        game_state["hiddenAnswers"] = []
        game_state["phoneResult"] = None
        game_state["audienceData"] = None
        await sio.emit("state_update", game_state)

@sio.on("host:prev")
async def host_prev(sid):
    if game_state["levelIndex"] > 0:
        game_state["levelIndex"] -= 1
        game_state["currentQuestion"] = game_state["gameQueue"][game_state["levelIndex"]]
        game_state["phase"] = "question"
        game_state["selectedAnswer"] = None
        await sio.emit("state_update", game_state)

@sio.on("lifeline:fifty")
async def fifty(sid):
    if game_state["lifelines"]["fifty"]:
        print("   💡 Lifeline: 50:50")
        game_state["lifelines"]["fifty"] = False
        wrong = [i for i in range(4) if i != game_state["currentQuestion"]["answer"]]
        game_state["hiddenAnswers"] = random.sample(wrong, 2)
        await sio.emit("state_update", game_state)

@sio.on("lifeline:phone")
async def phone(sid):
    if game_state["lifelines"]["phone"]:
        print("   📞 Lifeline: Phone")
        game_state["lifelines"]["phone"] = False
        game_state["phoneResult"] = game_state["currentQuestion"]["answer"]
        await sio.emit("state_update", game_state)

@sio.on("lifeline:audience")
async def audience(sid):
    if game_state["lifelines"]["audience"]:
        print("   📊 Lifeline: Audience")
        game_state["lifelines"]["audience"] = False
        p = [10, 10, 10, 10]
        p[game_state["currentQuestion"]["answer"]] = 70
        game_state["audienceData"] = p
        await sio.emit("state_update", game_state)

# ==========================================
# 5. ROUTES
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
    ip = get_local_ip()
    print(f"👉 OPEN BROWSER: http://{ip}:8000")
    print("(Press Ctrl+C to stop)")
    uvicorn.run(socket_app, host="0.0.0.0", port=8000)