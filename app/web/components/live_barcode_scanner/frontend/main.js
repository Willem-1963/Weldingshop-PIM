let rendered = false;
let scanner = null;
let scanning = false;
let resultSent = false;
let audioContext = null;

const statusNode = document.getElementById("status");
const retryButton = document.getElementById("retry");
const placeholder = document.getElementById("placeholder");

function setStatus(message, kind = "") {
  statusNode.textContent = message;
  statusNode.className = kind;
  window.setTimeout(resizeFrame, 0);
}

function resizeFrame() {
  const contentHeight = Math.ceil(document.body.getBoundingClientRect().height);
  const wanted = Math.min(420, Math.max(330, contentHeight + 4));
  Streamlit.setFrameHeight(wanted);
}

function unlockAudio() {
  try {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return;
    if (!audioContext) audioContext = new AudioContextClass();
    audioContext.resume().catch(() => {});
  } catch (_) {}
}

function signalSuccess() {
  try {
    if (navigator.vibrate) navigator.vibrate([120, 60, 120]);
  } catch (_) {}
  try {
    if (!audioContext) return;
    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(880, audioContext.currentTime);
    gain.gain.setValueAtTime(0.18, audioContext.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, audioContext.currentTime + 0.16);
    oscillator.connect(gain);
    gain.connect(audioContext.destination);
    oscillator.start();
    oscillator.stop(audioContext.currentTime + 0.16);
  } catch (_) {}
}

async function stopScanner() {
  if (!scanner) return;
  if (scanning) {
    try { await scanner.stop(); } catch (_) {}
  }
  try { scanner.clear(); } catch (_) {}
  scanner = null;
  scanning = false;
}

async function onScanSuccess(decodedText) {
  const value = String(decodedText || "").replace(/\s+/g, "");
  if (!value || resultSent) return;
  resultSent = true;
  setStatus(`Barcode herkend: ${value}`, "success");
  signalSuccess();
  await stopScanner();
  Streamlit.setComponentValue(value);
  resizeFrame();
}

async function startScanner() {
  unlockAudio();
  await stopScanner();
  retryButton.style.display = "none";
  placeholder.style.display = "none";
  setStatus("Camera starten…");
  resultSent = false;
  try {
    scanner = new Html5Qrcode("reader");
    const width = Math.max(240, Math.min(window.innerWidth || 360, 720));
    const qrWidth = Math.max(210, Math.min(width - 36, 420));
    await scanner.start(
      { facingMode: "environment" },
      {
        fps: 12,
        qrbox: { width: qrWidth, height: Math.max(90, Math.round(qrWidth * 0.34)) },
        aspectRatio: 4 / 3,
        disableFlip: true,
      },
      onScanSuccess,
      () => {},
    );
    scanning = true;
    setStatus("Richt één barcode binnen het kader");
    resizeFrame();
  } catch (error) {
    scanning = false;
    const detail = error && error.message ? error.message : String(error || "onbekende fout");
    setStatus(`Camera kon niet starten: ${detail}`, "error");
    placeholder.textContent = "Geen camerabeeld";
    placeholder.style.display = "grid";
    retryButton.textContent = "Camera opnieuw proberen";
    retryButton.style.display = "block";
    resizeFrame();
  }
}

function onRender() {
  if (rendered) return;
  rendered = true;
  resizeFrame();
}

retryButton.addEventListener("click", async () => {
  await startScanner();
});
window.addEventListener("pagehide", () => { stopScanner(); });
window.addEventListener("resize", resizeFrame);
Streamlit.events.addEventListener(Streamlit.RENDER_EVENT, onRender);
Streamlit.setComponentReady();
resizeFrame();
