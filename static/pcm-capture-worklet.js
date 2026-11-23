class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const config = (options && options.processorOptions) || {};
    this.targetRate = config.targetSampleRate || 16000;
    this.frameSamples = config.frameSamples || 1600;
    this.ratio = sampleRate / this.targetRate;
    this.position = 0;
    this.frame = new Int16Array(this.frameSamples);
    this.frameOffset = 0;
    this.levelCount = 0;
    this.levelSum = 0;
    this.levelPeak = 0;
    this.active = true;
    this.prevSample = 0;
    this.port.onmessage = (event) => {
      if (event.data && event.data.type === "stop") {
        if (this.frameOffset > 0) {
          const tail = this.frame.slice(0, this.frameOffset);
          this.port.postMessage({ type: "audio", buffer: tail.buffer }, [tail.buffer]);
          this.frame = new Int16Array(this.framesSamples);
          this.frameOffset = 0;
        }
        this.active = false;
      }
    };
  }

  pushSample(sample) {
    const clamped = Math.max(-1, Math.min(1, sample));
    this.frame[this.frameOffset] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
    this.frameOffset += 1;
    if (this.frameOffset === this.frameSamples) {
      const buffer = this.frame.buffer;
      this.port.postMessage({ type: "audio", buffer }, [buffer]);
      this.frame = new Int16Array(this.frameSamples);
      this.frameOffset = 0;
    }
  }

  process(inputs, outputs) {
    if (!this.active) return false;
    const input = inputs[0] && inputs[0][0];
    const output = outputs[0] && outputs[0][0];
    if (output) output.fill(0);
    if (!input || input.length === 0) return true;

    for (let i = 0; i < input.length; i += 1) {
      const value = input[i];
      this.levelSum += value * value;
      this.levelPeak = Math.max(this.levelPeak, Math.abs(value));
    }
    this.levelCount += input.length;

    while (this.position < input.length {
      const index = Math.floor(this.position);
      const fraction = this.position - index;
      const current = index < 0 ? this.prevSample : input[index];
      const next = index + 1 < input.length ? input[index + 1] : current;
      this.pushSample(current + (next - current) * fraction);
      this.position += this.ratio;
    }
    this.position -= input.length;
    this.prevSample = input[input.length - 1];

    if (this.levelCount >= sampleRate / 10) {
      this.port.postMessage({
        type: "level",
        rms: Math.sqrt(this.levelSum / this.levelCount),
        peak: this.levelPeak,
      });
      this.levelCount = 0;
      this.levelSum = 0;
      this.levelPeak = 0;
    }
    return true;
  }
}

registerProcessor("pcm-capture-processor", PcmCaptureProcessor);
