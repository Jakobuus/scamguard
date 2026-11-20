// Live call audio player. Vapi's listenUrl streams raw 16-bit PCM over a WebSocket: a JSON "start"
// message names the format (default linear16, 16 kHz, 2 channels interleaved), then binary frames.
// Both channels (caller and assistant) are mixed so the whole room hears both sides.
// window.KinCheckListen = { start(url), stop(), isOn() }; never throws.
(function () {
  var ws = null, ctx = null, gain = null, nextT = 0, url = null;
  var rate = 16000, channels = 2, LEAD = 0.25, MAX_LAG = 1.0;

  function play(buf) {
    try {
      var n = Math.floor(buf.byteLength / 2), s = new Int16Array(buf, 0, n);
      var frames = Math.floor(n / channels);
      if (!frames || !ctx) return;
      var ab = ctx.createBuffer(1, frames, rate), out = ab.getChannelData(0);
      for (var i = 0; i < frames; i++) {
        var sum = 0;
        for (var c = 0; c < channels; c++) sum += s[i * channels + c];
        out[i] = Math.max(-1, Math.min(1, sum / 32768));
      }
      var now = ctx.currentTime;
      // Jitter buffer: start LEAD seconds ahead; if we fell behind or drifted too far, resync.
      if (nextT < now + 0.02 || nextT > now + MAX_LAG) nextT = now + LEAD;
      var src = ctx.createBufferSource();
      src.buffer = ab; src.connect(gain); src.start(nextT);
      nextT += ab.duration;
    } catch (e) {}
  }

  function stop() {
    try { if (ws) { ws.onmessage = ws.onclose = ws.onerror = null; ws.close(); } } catch (e) {}
    try { if (ctx) ctx.close(); } catch (e) {}
    ws = null; ctx = null; gain = null; nextT = 0; url = null;
  }

  function start(u) {
    try {
      if (!u || typeof u !== "string") return false;
      if (ws && url === u) return true;
      stop();
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return false;
      ctx = new AC(); // call start() from a click so the browser allows sound
      if (ctx.resume) ctx.resume();
      gain = ctx.createGain(); gain.gain.value = 1; gain.connect(ctx.destination);
      url = u;
      ws = new WebSocket(u);
      ws.binaryType = "arraybuffer";
      ws.onmessage = function (ev) {
        if (typeof ev.data === "string") {
          try {
            var m = JSON.parse(ev.data);
            if (m && m.type === "start") {
              if (m.sampleRate) rate = Number(m.sampleRate) || 16000;
              if (m.channels) channels = Number(m.channels) || 2;
            }
          } catch (e) {}
          return;
        }
        play(ev.data);
      };
      ws.onclose = ws.onerror = function () { stop(); };
      return true;
    } catch (e) { stop(); return false; }
  }

  window.KinCheckListen = {
    start: start,
    stop: function () { try { stop(); } catch (e) {} },
    isOn: function () { return !!ws; }
  };
})();
