const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const frontend = path.resolve(__dirname, "../../src/frontend");
const source = fs.readFileSync(path.join(frontend, "omikuji.js"), "utf8");

function configuredSource(probability) {
  return source.replace(/const SUPER_RARE_PROBABILITY = [^;]+;/, `const SUPER_RARE_PROBABILITY = ${probability};`);
}

function loadFortunes(code = source) {
  const rolls = [];
  let seed = 173;
  const math = Object.create(Math);
  math.random = () => {
    if (rolls.length) return rolls.shift();
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 4294967296;
  };
  const context = vm.createContext({ window: {}, Math: math });
  vm.runInContext(code, context);
  return { ...context.window.ComoOmikuji, rolls };
}

function loadTally(storage) {
  const listeners = new Map();
  const context = vm.createContext({ window: {
    localStorage: storage,
    addEventListener(event, callback) { listeners.set(event, callback); },
  } });
  vm.runInContext(fs.readFileSync(path.join(frontend, "omikuji-tally.js"), "utf8"), context);
  return { Tally: context.window.ComoFortuneTally, listeners };
}

function memoryStorage() {
  const values = new Map();
  return { values, getItem(key) { return values.get(key) ?? null; }, setItem(key, value) { values.set(key, value); } };
}

test("one immutable special result; 150 normal texts and one special text", () => {
  const data = loadFortunes();
  assert.ok(Number.isFinite(data.superRareProbability));
  assert.ok(data.superRareProbability >= 0 && data.superRareProbability <= 1);
  assert.equal(data.superRareFortune.label, "スーパー超大吉");
  assert.ok(Object.isFrozen(data.superRareFortune));
  assert.equal(data.fortunes.length, 5);
  const messages = Array.from(data.fortunes).flatMap(fortune => Array.from(fortune.messages));
  assert.equal(messages.length, 150);
  assert.equal(new Set([...messages, data.superRareFortune.message]).size, 151);
});

test("rare probability boundary and 0/1 configuration", () => {
  const data = loadFortunes();
  if (data.superRareProbability > 0) {
    for (const roll of [0, data.superRareProbability * (1 - 1e-6)]) {
      data.rolls.push(roll);
      assert.equal(data.draw(), data.superRareFortune);
      assert.equal(data.rolls.length, 0);
    }
  }
  if (data.superRareProbability < 1) {
    data.rolls.push(data.superRareProbability, 0.5);
    assert.equal(data.draw().id, "chukichi");
  }
  for (const probability of [0, 1]) {
    const changed = loadFortunes(configuredSource(probability));
    changed.rolls.push(0.5, 0.5);
    assert.equal(!!changed.draw().isSuperRare, probability === 1);
  }
});

test("normal decks stay unique even with interspersed rare draws", () => {
  const data = loadFortunes(configuredSource(0.001));
  for (let rank = 0; rank < 5; rank += 1) {
    let previous = null;
    for (let round = 0; round < 12; round += 1) {
      const seen = new Set();
      for (let index = 0; index < 30; index += 1) {
        data.rolls.push(0);
        assert.equal(data.draw(), data.superRareFortune);
        data.rolls.push(0.5, (rank + 0.5) / 5);
        const result = data.draw();
        assert.equal(result.id, data.fortunes[rank].id);
        assert.notEqual(result.message, previous);
        assert.ok(!seen.has(result.message));
        seen.add(result.message);
        previous = result.message;
      }
      assert.equal(seen.size, 30);
    }
  }
});

test("rare gate leaves the normal five-category distribution unchanged", () => {
  const data = loadFortunes(configuredSource(0.001));
  const counts = new Map();
  let normalIndex = 0;
  for (let index = 0; index < 10000; index += 1) {
    const rare = index % 1000 === 0;
    data.rolls.push(rare ? 0 : 0.5);
    if (!rare) data.rolls.push(((normalIndex++ % 5) + 0.5) / 5);
    const result = data.draw();
    counts.set(result.id, (counts.get(result.id) || 0) + 1);
  }
  assert.equal(counts.get("super-daikichi"), 10);
  for (const fortune of data.fortunes) assert.equal(counts.get(fortune.id), 1998);
});

test("page counters add exactly once per revealed result and ignore cancelled callbacks", () => {
  const appSource = fs.readFileSync(path.join(frontend, "app.js"), "utf8");
  const start = appSource.indexOf("function revealOmikuji(result) {");
  const end = appSource.indexOf("\nasync function typeMessage", start);
  assert.ok(start >= 0 && end > start);
  const data = loadFortunes();
  const results = [...Array.from(data.fortunes, fortune => ({
    id: fortune.id, label: fortune.label, message: fortune.messages[0],
  })), data.superRareFortune];
  const countNodes = new Map(results.map(result => [result.id, { textContent: "0" }]));
  const counts = Object.fromEntries(results.map(result => [result.id, 0]));
  const element = { dataset: { phase: "ready" } };
  const total = { textContent: "0回" };
  const { Tally } = loadTally(memoryStorage());
  const tally = new Tally(results.map(result => result.id), {
    onChange(snapshot) {
      Object.assign(counts, snapshot.counts);
      for (const [id, node] of countNodes) node.textContent = String(counts[id]);
      total.textContent = `${snapshot.total}回`;
    },
  });
  const context = vm.createContext({
    omikuji: element, omikujiCountNodes: countNodes, omikujiTally: tally,
    omikujiRank: {}, omikujiSlipRank: { replaceChildren() {} }, omikujiMessage: {},
    omikujiDraw: {}, omikujiMachine: {}, omikujiDrawLabel: {}, noteUserActivity() {},
    document: { createElementNS() { return { setAttribute() {} }; } },
  });
  vm.runInContext(appSource.slice(start, end), context);
  for (const result of results) {
    context.result = result;
    element.dataset.phase = result.isSuperRare ? "charging" : "drawing";
    vm.runInContext("revealOmikuji(result); revealOmikuji(result);", context);
    assert.equal(counts[result.id], 1);
    assert.equal(countNodes.get(result.id).textContent, "1");
  }
  assert.equal(total.textContent, "6回");
  assert.equal(tally.snapshot().total, 6);
  element.dataset.phase = "ready";
  vm.runInContext("revealOmikuji(result)", context);
  assert.equal(total.textContent, "6回");
  assert.equal(counts["super-daikichi"], 1);
});

test("only aggregate counters persist across reload/restart and update between tabs", () => {
  const storage = memoryStorage();
  const ids = [...Array.from(loadFortunes().fortunes, fortune => fortune.id), "super-daikichi"];
  const first = loadTally(storage);
  const a = new first.Tally(ids);
  assert.equal(a.snapshot().total, 0, "no migration of old unsaved counts");
  a.record("daikichi");
  a.record("super-daikichi");
  a.record("super-daikichi");
  const key = [...storage.values.keys()][0];
  const saved = storage.values.get(key);
  assert.ok(saved.length < 400);
  assert.deepEqual(Object.keys(JSON.parse(saved)).sort(), ["counts", "version"]);
  const restarted = loadTally(storage);
  const b = new restarted.Tally(ids);
  assert.equal(b.snapshot().total, 3);
  assert.equal(b.snapshot().counts["super-daikichi"], 2);
  b.record("chukichi");
  a.record("daikichi"); // Reads the other tab's latest total before adding.
  assert.equal(a.snapshot().total, 5);
  restarted.listeners.get("storage")({ key, storageArea: storage });
  assert.equal(b.snapshot().total, 5);
  assert.equal(b.record("unknown"), false);
  assert.equal(b.snapshot().total, 5);
});

test("corrupt/blocked storage remains usable, and a failed save retains in-memory increments", () => {
  const storage = memoryStorage();
  const key = "chat-como.omikuji-tally.v1";
  storage.values.set(key, "broken JSON");
  const { Tally } = loadTally(storage);
  const repaired = new Tally(["daikichi", "super-daikichi"]);
  assert.equal(repaired.snapshot().total, 0);
  assert.doesNotThrow(() => repaired.record("daikichi"));
  assert.equal(repaired.snapshot().persistent, true);
  const setItem = storage.setItem;
  storage.setItem = () => { throw new Error("Quota exceeded"); };
  repaired.record("daikichi");
  assert.equal(repaired.snapshot().total, 2);
  assert.equal(repaired.snapshot().persistent, false);
  storage.setItem = setItem;
  repaired.record("daikichi");
  assert.equal(repaired.snapshot().total, 3, "failed increment is not lost when storage recovers");
  assert.equal(new Tally(["daikichi", "super-daikichi"]).snapshot().total, 3);
  const blocked = { getItem() { throw new Error("Blocked"); }, setItem() { throw new Error("Blocked"); } };
  const fallback = new Tally(["daikichi"], { storage: blocked });
  assert.doesNotThrow(() => { fallback.record("daikichi"); fallback.record("daikichi"); });
  assert.equal(fallback.snapshot().total, 2);
  assert.equal(fallback.snapshot().persistent, false);
  storage.values.set(key, JSON.stringify({ version: 1, counts: { daikichi: -2, "super-daikichi": 3, unknown: 999 } }));
  const normalized = new Tally(["daikichi", "super-daikichi"]);
  assert.equal(normalized.snapshot().total, 3);
  assert.equal(normalized.snapshot().counts.daikichi, 0);
});

test("special choreography joins the draw, jumps as a whole, and settles", () => {
  const context = vm.createContext({ window: {} });
  vm.runInContext(fs.readFileSync(path.join(frontend, "avatar-puppet.js"), "utf8"), context);
  const actor = Object.create(context.window.KomoPuppet.prototype);
  Object.assign(actor, {
    elapsed: 0, emotion: "neutral", stageOffsetX: 0, fortuneMotion: null,
    characterRig: { position: { x: 0, y: 0 } }, refreshFace() {},
  });
  actor.playFortuneMotion("draw", { duration: 2 });
  actor.elapsed = 2;
  const held = actor.getFortunePose();
  actor.playFortuneMotion("super", { duration: 3.1 });
  assert.deepEqual(actor.getFortunePose(), held);
  assert.equal(actor.emotion, "surprised");
  let previous = held;
  let highestJump = 0;
  let highestArms = 0;
  for (let index = 1; index < 240; index += 1) {
    actor.elapsed = 2 + 3.1 * index / 240;
    const pose = actor.getFortunePose();
    for (const key of Object.keys(pose)) {
      assert.ok(Number.isFinite(pose[key]));
      assert.ok(Math.abs(pose[key] - previous[key]) < 3.5, key);
    }
    highestJump = Math.min(highestJump, pose.characterY);
    highestArms = Math.max(highestArms, pose.leftArmDelta);
    previous = pose;
  }
  assert.ok(highestJump < -35);
  assert.ok(highestArms > 1.3);
  assert.equal(actor.emotion, "happy");
  actor.elapsed = 5.11;
  assert.equal(actor.getFortunePose(), null);
  assert.equal(actor.emotion, "neutral");
});

test("each celebration phase has reading time; aligned light fades at 31s and ends at 33s", () => {
  let clock = 0;
  let nextId = 0;
  const scheduled = new Map();
  const nodes = new Map();
  const lightOrigin = {};
  const element = {
    hidden: true, dataset: {},
    style: { setProperty(key, value) { lightOrigin[key] = value; } },
    querySelector(selector) {
      if (!nodes.has(selector)) nodes.set(selector, {
        textContent: "", appendChild() {}, offsetLeft: 100, offsetTop: 100, offsetWidth: 200, offsetHeight: 50,
      });
      return nodes.get(selector);
    },
  };
  const context = vm.createContext({
    window: {
      addEventListener() {},
      setTimeout(callback, delay) {
        const id = ++nextId;
        scheduled.set(id, { callback, at: clock + delay });
        return id;
      },
      clearTimeout(id) { scheduled.delete(id); },
    },
    document: {
      createDocumentFragment() { return { appendChild() {} }; },
      createElement() { return { style: { setProperty() {} }, appendChild() {} }; },
    },
  });
  vm.runInContext(fs.readFileSync(path.join(frontend, "omikuji-celebration.js"), "utf8"), context);
  const celebration = new context.window.ComoFortuneCelebration(element);
  function advanceTo(time) {
    while (scheduled.size) {
      const [id, task] = [...scheduled].sort((a, b) => a[1].at - b[1].at)[0];
      if (task.at > time) break;
      scheduled.delete(id);
      clock = task.at;
      task.callback();
    }
    clock = time;
  }
  for (const reduceMotion of [false, true]) {
    clock = 0;
    let reveals = 0;
    let reactions = 0;
    let encores = 0;
    celebration.play(loadFortunes().superRareFortune, {
      reduceMotion,
      onReveal() { reveals += 1; },
      onReaction() { reactions += 1; },
      onEncore() { encores += 1; },
    });
    assert.equal(lightOrigin["--fortune-origin-x"], "300px");
    assert.equal(lightOrigin["--fortune-origin-y"], "225px");
    advanceTo(3199);
    assert.equal(element.dataset.phase, "charge", "the hint has more than three seconds");
    assert.equal(reveals, 0);
    advanceTo(3200);
    assert.equal(element.dataset.phase, "flash");
    advanceTo(4799);
    assert.equal(reveals, 0);
    advanceTo(4800);
    assert.equal(element.dataset.revealed, "true");
    assert.equal(element.dataset.phase, "reveal");
    advanceTo(6499);
    assert.equal(reactions, 0, "title is readable before the reaction");
    advanceTo(6500);
    assert.equal(element.dataset.phase, "celebrate");
    advanceTo(12999);
    assert.equal(encores, 0);
    advanceTo(13000);
    assert.equal(element.dataset.encore, "true");
    assert.equal(encores, reduceMotion ? 0 : 1);
    advanceTo(20999);
    assert.equal(element.dataset.finale, undefined);
    advanceTo(21000);
    assert.equal(element.dataset.phase, "celebrate");
    assert.equal(element.hidden, false);
    assert.equal(element.dataset.finale, "true");
    advanceTo(31000);
    assert.equal(element.dataset.phase, "settle");
    advanceTo(32999);
    assert.equal(element.hidden, false);
    advanceTo(33000);
    assert.equal(element.hidden, true);
    assert.equal(celebration.timerIds.size, 0);
    assert.equal(reveals, 1);
    assert.equal(reactions, 1);
  }
  celebration.play(loadFortunes().superRareFortune, {
    onReveal() { assert.fail("cancelled effect revealed an award"); },
    onReaction() { assert.fail("cancelled effect played a reaction"); },
    onEncore() { assert.fail("cancelled effect played an encore"); },
  });
  celebration.cancel();
  advanceTo(70000);
  assert.equal(scheduled.size, 0);
  assert.equal(element.hidden, true);
});

test("encore joy has two smooth whole-body jumps, raised arms, and settles", () => {
  const context = vm.createContext({ window: {} });
  vm.runInContext(fs.readFileSync(path.join(frontend, "avatar-puppet.js"), "utf8"), context);
  const actor = Object.create(context.window.KomoPuppet.prototype);
  Object.assign(actor, {
    elapsed: 0, emotion: "neutral", stageOffsetX: 0, fortuneMotion: null,
    characterRig: { position: { x: 0, y: 0 } }, refreshFace() {},
  });
  actor.playFortuneMotion("super", { duration: 3.3, encore: true });
  let previous = actor.getFortunePose();
  let firstJump = 0;
  let secondJump = 0;
  for (let index = 1; index < 240; index += 1) {
    actor.elapsed = 3.3 * index / 240;
    const pose = actor.getFortunePose();
    for (const key of Object.keys(pose)) {
      assert.ok(Number.isFinite(pose[key]));
      assert.ok(Math.abs(pose[key] - previous[key]) < 3.5, key);
    }
    if (index < 120) firstJump = Math.min(firstJump, pose.characterY);
    else secondJump = Math.min(secondJump, pose.characterY);
    assert.ok(pose.bodyScaleY >= 0.94);
    previous = pose;
  }
  assert.ok(firstJump < -35 && secondJump < -25);
  actor.elapsed = 3.31;
  assert.equal(actor.getFortunePose(), null);
  assert.equal(actor.emotion, "neutral");
});
