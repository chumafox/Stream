class PcmPlaybackProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const config = options.processorOptions || {};
    this.sampleRate = config.sampleRate || 24000;
    this.capacity = this.sampleRate * 30;
    this.buffer = new Float32Array(this.capacity);
    this.readIndex = 0;
    this.writeIndex = 0;
    this.count = 0;
    this.prebuffer = Math.floor(this.sampleRate * 0.12);
    this.started = false;
    this.draining = false;
    this.levelSum = 0;
    this.levelPeak = 0;
    this.levelCount = 0;
    this.statusSamples = 0;
    this.underruns = 0;
    this.dropped = 0;
    this.active = true;
    this.port.onmessage = (event) => {
      const data = event.data;
      if (data.type === "audio") this.write(data.buffer);
      if (data.type === "flush") this.reset();
      if (data.type === "drain") {
        // Конец сессии: доигрываем остаток, даже если он короче prebuffer.
        this.draining = true;
        if (this.count > 0) this.started = true;
        else this.port.postMessage({ type: "drained" });
      }
      if (data.type === "stop") this.active = false;
    };
  }

  reset() {
    this.readIndex = 0;
    this.writeIndex = 0;
    this.count = 0;
    this.started = false;
    this.draining = false;
    this.underruns = 0;
    this.dropped = 0;
  }

  dropOldest(amount) {
    this.readIndex = (this.readIndex + amount) % this.capacity;
    this.count -= amount;
    this.dropped += amount;
  }

  write(arrayBuffer) {
    let pcm = new Int16Array(arrayBuffer);
    if (pcm.length > this.capacity) {
      const overflow = pcm.length - this.capacity;
      pcm = pcm.subarray(overflow);
      this.reset();
      this.dropped += overflow;
    }
    const required = pcm.length;
    if (required > this.capacity - this.count) {
      this.dropOldest(required - (this.capacity - this.count));
    }
    for (let i = 0; i < pcm.length; i += 1) {
      this.buffer[this.writeIndex] = pcm[i] / 32768;
      this.writeIndex = (this.writeIndex + 1) % this.capacity;
    }
    this.count += required;
  }

  postStatus() {
    this.port.postMessage({
      type: "playback_status",
      rms: this.levelCount > 0 ? Math.sqrt(this.levelSum / this.levelCount) : 0,
      peak: this.levelPeak,
      bufferedMs: Math.round((this.count / this.sampleRate) * 1000),
      underruns: this.underruns,
      dropped: this.dropped,
    });
    this.levelCount = 0;
    this.levelSum = 0;
    this.levelPeak = 0;
  }

  process(_inputs, outputs) {
    if (!this.active) return false;
    const output = outputs[0] && outputs[0][0];
    if (!output) return true;
    output.fill(0);
    if (!this.started && this.count >= this.prebuffer) this.started = true;

    let produced = 0;
    if (this.started) {
      const available = Math.min(output.length, this.count);
      for (; produced < available; produced += 1) {
        const value = this.buffer[this.readIndex];
        output[produced] = value;
        this.readIndex = (this.readIndex + 1) % this.capacity;
        this.count -= 1;
        this.levelSum += value * value;
        this.levelPeak = Math.max(this.levelPeak, Math.abs(value));
      }
      this.levelCount += produced;
      if (this.count === 0) {
        this.started = false;
        this.underruns += 1;
        if (this.draining) {
          this.draining = false;
          this.postStatus();
          this.port.postMessage({ type: "drained" });
        }
      }
    }

    // Статус уходит регулярно по времени рендера, а не только при nonzero RMS:
    // иначе UI не узнает о застрявшем буфере и об окончании доигрывания.
    this.statusSamples += output.length;
    if (this.statusSamples >= this.sampleRate / 10) {
      this.statusSamples = 0;
      this.postStatus();
    }
    return true;
  }
}

registerProcessor("pcm-playback-processor", PcmPlaybackProcessor);
