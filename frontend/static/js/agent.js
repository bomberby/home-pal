// TTS functionality
function speakText(text) {
  fetch('/tts/speak', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json'
    },
    body: JSON.stringify({ text })
  })
  .then(response => {
    if (!response.ok) throw new Error("Network response was not OK");
    return response.blob();
  })
  .then(blob => {
    const audioUrl = URL.createObjectURL(blob);
    const audio = new Audio(audioUrl);
    audio.play();
  })
  .catch(error => {
    console.error('Error generating speech:', error);
    alert('Error generating speech: ' + error.message);
  });
}


function askAgent(query) {
  fetch('/agent/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query })
  })
  .then(response => response.json())
  .then(data => {
    console.log("Agent response:", data.response);
    speakText(data.response);  // reuse your TTS
  })
  .catch(error => {
    console.error("Error:", error);
  });
}





// Listening model

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

if (!SpeechRecognition) {
  alert('Speech Recognition API not supported in this browser.');
}

const recognition = new SpeechRecognition();

recognition.continuous = true;
recognition.interimResults = false;
recognition.lang = 'en-US';

const wakeWords = ['hey assistant', 'hey agent', 'hey miku'];
let isListening = true;

function startListening() {
  if (!isListening) return;
  recognition.start();
  console.log('Speech recognition started...');
}

recognition.onresult = event => {
  const transcript = event.results[event.resultIndex][0].transcript.toLowerCase().trim();
  console.log('Transcript:', transcript);

  if (transcript.includes('stop listening')) {
    isListening = false;
    recognition.stop();
    console.log('Stop listening command detected. Recognition stopped.');
    // Optionally update your UI here to show stopped status
    return;
  }

  if (!isListening) {
    // Ignore any results if we've stopped listening
    return;
  }

  const wakeword = wakeWords.find(v => transcript.includes(v))
  if (wakeword) {
  // if (transcript.includes(wakeWord)) {
    const query = transcript.replace(wakeword, '').trim();

    if (!query) {
      console.log('Wake word detected but no query after it.');
      return;
    }

    console.log('Wake word detected! Query:', query);

    askAgent(query)
  }
};

recognition.onerror = event => {
  console.error('Speech recognition error:', event.error);
};

recognition.onend = () => {
  if (isListening) {
    console.log('Speech recognition ended, restarting...');
    recognition.start();
  } else {
    console.log('Recognition stopped by user command.');
  }
};

// startListening();


let audioUnlocked = false;

function unlockAudio() {
  // Create silent audio to unlock playback
  const ctx = new AudioContext();
  const oscillator = ctx.createOscillator();
  oscillator.connect(ctx.destination);
  oscillator.start();
  oscillator.stop(ctx.currentTime + 0.01);
  
  ctx.close().then(() => {
    audioUnlocked = true;
    console.log('Audio unlocked!');
    document.removeEventListener('click', unlockAudio);
    startListening();
  });
}

// Wait for user interaction to unlock audio
document.addEventListener('click', unlockAudio);
