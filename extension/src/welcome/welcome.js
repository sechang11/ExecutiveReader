/**
 * First run.
 *
 * One thing to try and one decision to make. A reader with no obvious first
 * step gets uninstalled before it reads anything, and the voice choice matters
 * enough to ask once rather than bury in settings.
 */

const $ = (id) => document.getElementById(id);
const send = (msg) => chrome.runtime.sendMessage(msg).catch(() => null);

$('get-voices').addEventListener('click', async () => {
  $('get-voices').disabled = true;
  $('later').disabled = true;
  $('progress-wrap').hidden = false;
  $('progress-text').textContent = 'Starting the download…';

  const res = await send({ type: 'neural-download' });

  $('progress-wrap').hidden = true;
  $('later').disabled = false;
  if (res?.ok) {
    $('get-voices').textContent = 'Natural voices are ready';
    // Select one immediately: downloading and then still hearing the robotic
    // voice would read as the download having failed.
    const voices = await send({ type: 'get-voices' });
    const neural = Array.isArray(voices) && voices.find((v) => v.engineId === 'kokoro' && v.ready);
    if (neural) await send({ type: 'set-voice', voiceKey: neural.key });
  } else {
    $('get-voices').disabled = false;
    $('get-voices').textContent = 'Download failed — try again';
  }
});

$('later').addEventListener('click', () => {
  $('later').textContent = 'Staying on system voices';
  $('later').disabled = true;
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type !== 'neural-progress') return;
  const pct = msg.total ? Math.round((msg.loaded / msg.total) * 100) : null;
  if (pct === null) $('progress').removeAttribute('value');
  else $('progress').value = pct;
  $('progress-text').textContent = pct === null
    ? `${(msg.loaded / 1e6).toFixed(0)} MB downloaded`
    : `${pct}% — ${(msg.loaded / 1e6).toFixed(0)} of ${(msg.total / 1e6).toFixed(0)} MB`;
});
