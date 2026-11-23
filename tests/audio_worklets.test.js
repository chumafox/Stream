// Проверки аудио-worklet без браузера: детерминированный рендеринг блоками.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");

function loadWorklet(relativePath, sampleRate) {
  const registry = {};
  const sandbox = {
    sampleRate,
    AudioWorkletProcessor: class {
      constructor() {
        this.port = {
          sent: [],
          onmessage: null,
          postMessage(message) {
            this.sent.push(message);
          },
        };
      }
    },
    registerProcessor: (name, cls) => {
      registry[name] = cls;
    },
    Math,
    Int16Array,
    Float32Array,
  };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(ROOT, relativePath), "utf8"), sandbox);
  return registry;
}

function outputBlock(size = 128) {
  return [[new Float32Array(size)]];
}

function countNonZero(block) {
  return block.reduce((total, value) => total + (value !== 0 ? 1 : 0), 0);
}

function play(proc) {
  const block = outputBlock();
  proc.process([], block);
  return countNonZero(block[0][0]);
}

// 1. Захват: ресемплинг не должен накапливать дрейф.
for (const rate of [48000, 44100]) {
  const registry = loadWorklet("static/pcm-capture-worklet.js", rate);
  const proc = new registry["pcm-capture-processor"]({
    processorOptions: { targetSampleRate: 16000, frameSamples: 1600 },
  });
  let sent = 0;
  for (let offset = 0; offset < rate * 3; offset += 128) {
    const block = new Float32Array(128).fill(0.25);
    const before = proc.port.sent.length;
    proc.process([[block]], outputBlock());
    for (const message of proc.port.sent.slice(before)) {
      if (message.type === "audio") sent += message.buffer.byteLength / 2;
    }
  }
  assert.equal(sent, 48000, `дрейф ресемплинга при ${rate} Гц: ${sent}`);
}

// 2. Обычный поток доигрывается полностью.
{
  const registry = loadWorklet("static/pcm-playback-worklet.js", 24000);
  const proc = new registry["pcm-playback-processor"]({ processorOptions: { sampleRate: 24000 } });
  const chunk = new Int16Array(4800).fill(2000);
  proc.write(chunk.buffer);
  let played = 0;
  for (let i = 0; i < 60; i += 1) played += play(proc);
  assert.equal(played, 4800, `проиграно ${played} из 4800`);
}

// 3. Регрессия HIGH-1: хвост короче prebuffer должен доигрываться по drain.
{
  const registry = loadWorklet("static/pcm-playback-worklet.js", 24000);
  const proc = new registry["pcm-playback-processor"]({ processorOptions: { sampleRate: 24000 } });
  const tail = new Int16Array(2400).fill(1000); // 100 мс < prebuffer 120 мс
  proc.write(tail.buffer);

  // Без drain хвост не звучит — это ожидаемое поведение prebuffer-гейта.
  let played = 0;
  for (let i = 0; i < 40; i += 1) played += play(proc);
  assert.equal(played, 0, "prebuffer-гейт не должен запускать хвост сам");

  proc.port.onmessage({ data: { type: "drain" } });
  for (let i = 0; i < 40; i += 1) played += play(proc);
  assert.equal(played, 2400, `после drain проиграно ${played} из 2400`);
  assert.ok(proc.port.sent.some((message) => message.type === "drained"), "нет события drained");
}

// 4. Статус буфера приходит регулярно даже без ненулевого сигнала.
{
  const registry = loadWorklet("static/pcm-playback-worklet.js", 24000);
  const proc = new registry["pcm-playback-processor"]({ processorOptions: { sampleRate: 24000 } });
  for (let i = 0; i < 20; i += 1) proc.process([], outputBlock());
  const statuses = proc.port.sent.filter((message) => message.type === "playback_status");
  assert.ok(statuses.length >= 1, `статусов: ${statuses.length}`);
  assert.equal(statuses.at(-1).bufferedMs, 0);
}

// 5. Переполнение кольцевого буфера не ломает инварианты.
{
  const registry = loadWorklet("static/pcm-playback-worklet.js", 24000);
  const proc = new registry["pcm-playback-processor"]({ processorOptions: { sampleRate: 24000 } });
  proc.write(new Int16Array(800000).buffer);
  assert.equal(proc.count, proc.capacity);
  for (let i = 0; i < 20; i += 1) proc.write(new Int16Array(48000).buffer);
  for (let i = 0; i < 7000; i += 1) {
    proc.process([], outputBlock());
    assert.ok(proc.count >= 0 && proc.count <= proc.capacity, "count вне диапазона");
  }
  assert.equal(proc.count, 0);
}

console.log("audio worklet tests: ok");
