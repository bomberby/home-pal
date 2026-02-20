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
    if (data.response)
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

recognition.continuous = false;
recognition.interimResults = false;
recognition.lang = 'en-US';
recognition.processLocally = true;

const wakeWords = ['hey assistant', 'hey agent', 'hey miku', 'aziz'];
const missheardWakeWords = ['pay agent']

// Phrases bias
const phraseData = wakeWords.map(
  (p) => new Object({ phrase: p, boost: 9.0 })
);

const recognitionPhraseObjects = phraseData.map(
  (p) => new SpeechRecognitionPhrase(p.phrase, p.boost),
);

recognition.phrases = recognitionPhraseObjects;


let isListening = false;

function startListening() {
  if (isListening) return;
  isListening = true;
  recognition.start();
  console.log('Speech recognition started...');
  updateMicUI();
}

function stopListening() {
  if (!isListening) return;
  isListening = false;
  recognition.stop();
  console.log('Stop listening command detected. Recognition stopped.');
  updateMicUI();
}


recognition.onresult = event => {
  if (!event.results[event.resultIndex].isFinal) {
    return;
  }
  const transcript = event.results[event.resultIndex][0].transcript.toLowerCase().trim();
  console.log('Transcript:', transcript);

  if (transcript.includes('stop listening')) {
    stopListening();

    return;
  }

  if (!isListening) {
    // Ignore any results if we've stopped listening
    return;
  }

  const wakeword = wakeWords.concat(missheardWakeWords).find(v => transcript.includes(v))
  if (wakeword) {
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
  if (event.error === 'no-speech')
    return;
  if (event.error === 'language-not-supported')
    checkMissingLanguagePack();
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
// document.addEventListener('click', unlockAudio);

function checkMissingLanguagePack() {
  // check availability of target language
  SpeechRecognition.available({ langs: [recognition.lang], processLocally: true }).then(
    (result) => {
      if (result === "unavailable") {
        stopListening();
        console.log("Language pack not available to download at this time.");
      } else if (result === "available") {
        console.log("Language pack not missing.");
      } else {
        stopListening();
        console.log(`language pack is downloading...`);
        SpeechRecognition.install({
          langs: [recognition.lang],
          processLocally: true,
        }).then((result) => {
          if (result) {
            console.log(`en-US language pack downloaded. Start recognition again.`);
            startListening();
          } else {
            console.log(`en-US language pack failed to download. Try again later.`);
          }
        });
      }
    },
  );
}


// Visual indicator of listening
const micToggle = document.getElementById("mic-toggle");

micToggle.addEventListener("click", () => {
    if (isListening) {
      stopListening();
    } else {
      startListening();
    }
});

function updateMicUI() {
    if (isListening) {
        micToggle.classList.remove("mic-off");
        micToggle.classList.add("mic-on", "listening");
        micToggle.title = "Agent is listening";
    } else {
        micToggle.classList.remove("mic-on", "listening");
        micToggle.classList.add("mic-off");
        micToggle.title = "Agent is not listening";
    }
    micUiAnimate();
}

let waveTimeout = null;
function micUiAnimate(){
    if (isListening) {
        // Start the 2-second waveform animation
        micToggle.classList.add("wave-show");

        // Clear any previous timer
        if (waveTimeout) clearTimeout(waveTimeout);

        // Remove the animation after 2 seconds
        waveTimeout = setTimeout(() => {
            micToggle.classList.remove("wave-show");
        }, 1700);
    } else {
        // Ensure animation is removed immediately if turning off
        micToggle.classList.remove("wave-show");

        if (waveTimeout) {
            clearTimeout(waveTimeout);
            waveTimeout = null;
        }
    }
  }
