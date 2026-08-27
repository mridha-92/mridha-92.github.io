let ws = null;
let mediaRecorder = null;
let audioChunks = [];
let playbackQueue = [];
let isPlaying = false;

const statusBadge = document.getElementById('statusBadge');
const urlInput = document.getElementById('serverUrlInput');
const connectBtn = document.getElementById('connectBtn');
const micBtn = document.getElementById('micBtn');
const transcriptLog = document.getElementById('transcriptLog');

// Auto-fill tunnel URL from query param (for widget integration)
const urlParams = new URLSearchParams(window.location.search);
if (urlParams.has('tunnel')) {
    urlInput.value = `wss://${urlParams.get('tunnel')}/ws/chat`;
}

connectBtn.addEventListener('click', connectWebSocket);

function updateStatus(status) {
    statusBadge.textContent = status;
    statusBadge.style.background = status === 'Connected' || status === 'Idle' ? '#10b981' : '#f59e0b';
}

function appendMessage(sender, text) {
    const div = document.createElement('div');
    div.className = `message ${sender.toLowerCase()}`;
    div.textContent = `${sender === 'user' ? 'You' : 'AI'}: ${text}`;
    transcriptLog.appendChild(div);
    transcriptLog.scrollTop = transcriptLog.scrollHeight;
}

function connectWebSocket() {
    if (ws) ws.close();
    const url = urlInput.value.trim();
    if (!url) return;

    ws = new WebSocket(url);
    ws.binaryType = 'blob'; // We expect binary WAV chunks from server

    ws.onopen = async () => {
        updateStatus('Connected');
        micBtn.disabled = false;
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });

            mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
            mediaRecorder.onstop = () => {
                if (ws.readyState === WebSocket.OPEN) {
                    const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                    ws.send(audioBlob); // Send entire sentence blob to backend
                }
                audioChunks = [];
            };
        } catch (err) {
            console.error('Microphone access denied:', err);
            updateStatus('Mic Error');
        }
    };

    ws.onmessage = async (event) => {
        if (typeof event.data === "string") {
            const data = JSON.parse(event.data);
            if (data.type === "status") updateStatus(data.value);
            if (data.type === "transcript") appendMessage('user', data.text);
            if (data.type === "llm_reply") appendMessage('ai', data.text);
        } else {
            // Audio binary received (WAV format)
            playbackQueue.push(event.data);
            if (!isPlaying) playQueue();
        }
    };

    ws.onclose = () => {
        updateStatus('Disconnected');
        micBtn.disabled = true;
    };
}

async function playQueue() {
    if (playbackQueue.length === 0) {
        isPlaying = false;
        return;
    }

    isPlaying = true;
    const blob = playbackQueue.shift();
    const audioUrl = URL.createObjectURL(blob);
    const audio = new Audio(audioUrl);

    audio.onended = () => {
        URL.revokeObjectURL(audioUrl);
        playQueue();
    };

    await audio.play();
}

// Push-to-talk logic
micBtn.addEventListener('mousedown', () => {
    if (mediaRecorder && mediaRecorder.state === 'inactive') {
        mediaRecorder.start();
        micBtn.classList.add('recording');
    }
});

micBtn.addEventListener('mouseup', () => {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
        micBtn.classList.remove('recording');
    }
});
