// ==========================================================================
// 1. CONNECT TO SOCKET.IO
// ==========================================================================
// 🟢 This automatically finds the server. No manual IP needed.
const socket = io(); 
const ROLE = document.body.dataset.role;

// ==========================================================================
// 2. STATE FLAGS & DOM ELEMENTS (Unchanged)
// ==========================================================================
window.hasPlayedReveal = false;
window.hasShownPopup = false;
window.isGameOverDelaying = false;
window.hasPlayedWin = false; 
let isMuted = true;

const elems = {
    preStart: document.getElementById("pre-start"),
    game: document.getElementById("game"),
    gameOver: document.getElementById("game-over"),
    
    finalMessage: document.getElementById("final-message"),
    finalScore: document.getElementById("final-score"),
    questionText: document.getElementById("question-text"),
    mediaBox: document.getElementById("media-container"),
    moneyVal: document.getElementById("money-val"),

    moneyPopup: document.getElementById("money-popup"),
    popupAmount: document.getElementById("popup-amount"),

    answers: [
        document.getElementById("answer-one"),
        document.getElementById("answer-two"),
        document.getElementById("answer-three"),
        document.getElementById("answer-four")
    ],

    levelsList: document.getElementById("levels"),
    audioBtn: document.getElementById("btn-audio")
};

// ==========================================================================
// 3. AUDIO ENGINE (Unchanged)
// ==========================================================================
function startSound(id, loop) {
    if (isMuted) return; 
    
    var soundHandle = document.getElementById(id);
    if (!soundHandle) return;
    
    // Unmute & Reset
    soundHandle.muted = false;
    soundHandle.volume = 1.0; 
    
    if(id === 'background') soundHandle.volume = 0.3;

    if (loop) soundHandle.setAttribute('loop', 'loop');
    else soundHandle.removeAttribute('loop');

    try {
        soundHandle.currentTime = 0;
        var promise = soundHandle.play();
        if (promise !== undefined) promise.catch(e => console.warn("Audio blocked", e));
    } catch (e) {}
}

function stopSound(id) {
    var soundHandle = document.getElementById(id);
    if(soundHandle) {
        soundHandle.pause();
        soundHandle.currentTime = 0;
    }
}

function stopAllSounds() {
    stopSound('background');
    stopSound('rightsound');
    stopSound('wrongsound');
    stopSound('winsound');
}

function toggleGlobalAudio() {
    isMuted = !isMuted;
    
    if(elems.audioBtn) {
        elems.audioBtn.innerHTML = isMuted ? "🔇" : "🔊";
        elems.audioBtn.style.background = isMuted ? "#8b0000" : "#006400";
    }

    if(!isMuted) {
        startSound('background', true);
    } else {
        stopAllSounds(); 
    }
}
if(elems.audioBtn) elems.audioBtn.onclick = toggleGlobalAudio;

// ==========================================================================
// 4. MAIN RENDER LOOP (Unchanged)
// ==========================================================================
function render(state) {
    
    // --- HANDLE GAME OVER / WIN SEQUENCE ---
    if (state.phase === "gameover") {
        if (!window.isGameOverDelaying) {
            window.isGameOverDelaying = true; 
            
            stopAllSounds(); 

            // CHECK WIN vs LOSS
            if (state.popup && state.popup.title && state.popup.title.includes("WINNER")) {
                if (!window.hasPlayedWin) {
                    startSound('winsound', false);
                    window.hasPlayedWin = true;
                }
            } else {
                startSound('wrongsound', false);
            }

            // Visuals
            if (elems.game) elems.game.style.display = "flex";
            if (elems.gameOver) elems.gameOver.style.display = "none";
            
            setTimeout(() => {
                if (elems.game) elems.game.style.display = "none";
                if (elems.gameOver) {
                    elems.gameOver.style.display = "flex";
                    elems.finalMessage.innerText = state.popup?.title || "GAME OVER";
                    elems.finalScore.innerText = state.popup?.text || "";
                }
            }, 4000); 
        }
        
        updateQuestionBoard(state); 
        return; 
    } 
    
    window.isGameOverDelaying = false;
    window.hasPlayedWin = false;

    // --- SCREEN SWITCHING ---
    if (elems.preStart) elems.preStart.style.display = (state.phase === "waiting") ? "flex" : "none";
    if (elems.gameOver) elems.gameOver.style.display = "none"; 
    if (elems.game) elems.game.style.display = (state.phase !== "waiting") ? "flex" : "none";

    const hostControls = document.getElementById("host-controls");
    if(hostControls) hostControls.style.display = (state.phase === "waiting") ? "none" : "flex";

    // --- UPDATES ---
    updateQuestionBoard(state);
    updateLadder(state);

    // --- MONEY POPUP ---
    if (state.phase === "revealed" && state.activeMoney > 0 && !window.hasShownPopup) {
        window.hasShownPopup = true;
        if (elems.moneyPopup) {
            elems.popupAmount.innerText = `₹${state.activeMoney}`;
            elems.moneyPopup.classList.add("show");
            setTimeout(() => elems.moneyPopup.classList.remove("show"), 3000);
        }
    } 
    if (state.phase === "question") {
        window.hasShownPopup = false;
        if (elems.moneyPopup) elems.moneyPopup.classList.remove("show");
    }

    // --- LIFELINES ---
    if (state.lifelines) {
        const setLife = (id, active) => {
            const el = document.getElementById(id);
            if (el) {
                if (!active) el.classList.add("used");
                else el.classList.remove("used");
            }
        };
        setLife("fifty", state.lifelines.fifty);
        setLife("phone-friend", state.lifelines.phone);
        setLife("audience", state.lifelines.audience);
    }

    // --- RIGHT ANSWER SOUND ---
    if (state.phase === "revealed" && !window.hasPlayedReveal) {
        window.hasPlayedReveal = true;
        
        var bg = document.getElementById('background');
        if(bg) bg.volume = 0.1; 

        startSound('rightsound', false);
    }
    
    if (state.phase === "question") {
        window.hasPlayedReveal = false;
        var bg = document.getElementById('background');
        if(bg && !bg.paused) bg.volume = 0.3;
    }
}

// --- HELPER: UPDATE BOARD VISUALS ---
function updateQuestionBoard(state) {
    const q = state.currentQuestion;
    
    if (q) {
        if (elems.questionText) elems.questionText.innerHTML = q.question;

        const currentMediaId = elems.mediaBox.getAttribute("data-qid");
        if (currentMediaId !== q.id) {
            elems.mediaBox.setAttribute("data-qid", q.id);
            elems.mediaBox.innerHTML = ""; 

            if (q.type === "image") {
                document.body.classList.add("has-media"); 
                elems.mediaBox.innerHTML = `<img src="${q.media}">`;
            } else {
                document.body.classList.remove("has-media");
                if (q.type === "audio") {
                    elems.mediaBox.innerHTML = `<audio controls><source src="${q.media}"></audio>`;
                }
            }
        }

        if (q.options) {
            q.options.forEach((txt, idx) => {
                const btn = elems.answers[idx];
                if(!btn) return;
                
                const textSpan = btn.querySelector(".text");
                if(textSpan) textSpan.innerText = txt;
                
                btn.className = "answer"; 
                
                if (state.hiddenAnswers.includes(idx)) btn.classList.add("opacity-hide");
                if (state.selectedAnswer === idx) btn.classList.add("selected");
                
                if (state.phase === "revealed" || state.phase === "gameover") {
                    if (idx === q.answer) btn.classList.add("correct");
                    else if (state.selectedAnswer === idx) btn.classList.add("wrong");
                }
            });
        }
    }
}

// --- HELPER: UPDATE LADDER ---
function updateLadder(state) {
    const lis = elems.levelsList ? elems.levelsList.querySelectorAll("li") : [];
    let currentPrize = "0";
    lis.forEach(li => {
        const lvl = parseInt(li.getAttribute("data-lvl") || li.dataset.lvl);
        li.className = "";
        if (lvl === state.levelIndex) {
            li.classList.add("active");
            currentPrize = li.innerText;
        } else if (lvl < state.levelIndex) li.classList.add("won");
    });
    if (elems.moneyVal) elems.moneyVal.innerText = currentPrize.replace(/[^0-9]/g, '');
}

// ==========================================================================
// 5. SOCKET LISTENERS & INTERACTIONS
// ==========================================================================

// Listen for updates from Python
socket.on("state_update", render);

if (ROLE === "host") {
    // Helper to bind clicks to socket.emit
    const bind = (id, ev) => { 
        const el = document.getElementById(id);
        if(el) el.onclick = () => socket.emit(ev); 
    };

    bind("btn-next", "host:next");
    bind("btn-prev", "host:prev");
    bind("btn-reveal", "host:reveal");
    bind("btn-reset", "host:reset");
    bind("btn-reset-gameover", "host:reset");
    
    const startBtn = document.getElementById("start-btn");
    if(startBtn) startBtn.onclick = () => {
        const cat = document.getElementById("problem-set").value;
        // 🟢 EMIT START EVENT
        socket.emit("host:start", cat);
        if(isMuted) toggleGlobalAudio();
    };
    
    bind("fifty", "lifeline:fifty");
    bind("phone-friend", "lifeline:phone");
    bind("audience", "lifeline:audience");
}

if (ROLE === "player" || ROLE === "viewer") {
    elems.answers.forEach((btn, idx) => {
        if(btn && ROLE === "player") {
            btn.onclick = () => socket.emit("player:answer", idx);
        }
    });
    
    if (ROLE === "player") {
        const bindLife = (id, ev) => { 
            const el = document.getElementById(id);
            if(el) el.onclick = () => socket.emit(ev);
        };
        bindLife("fifty", "lifeline:fifty");
        bindLife("phone-friend", "lifeline:phone");
        bindLife("audience", "lifeline:audience");
    }
}