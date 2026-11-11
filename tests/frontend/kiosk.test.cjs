const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.resolve(__dirname, '../../src/frontend/app.js'), 'utf8');
const settings = source.slice(source.indexOf('async function loadRuntimeSettings()'), source.indexOf('\nasync function sendQuestion'));
const initialize = source.slice(source.indexOf('async function initialize()'), source.lastIndexOf('\ninitialize();'));

for (const [name, enabled, search, expected] of [
  ['dedicated PC opens chat directly', true, '?kiosk=1', 'chat'],
  ['ordinary URL keeps the gate', true, '', 'password'],
  ['default configuration keeps the gate', false, '?kiosk=1', 'password'],
  ['other query values keep the gate', true, '?kiosk=0', 'password'],
]) {
  test(name, async () => {
    let screen = '';
    const context = vm.createContext({
      fetch: async () => ({ ok: true, json: async () => ({ kiosk_auto_login: enabled }) }),
      window: { location: { search } }, URLSearchParams,
      HEALTH_URL: '/api/health', configuredKioskAutoLogin: false,
      configuredInactivityResetSeconds: 30, configuredHistoryMaxExchanges: 5, configuredHistoryWindowMs: 120000,
      pruneExchangeHistory() {}, resetQuickQuestions() {}, bindEvents() {}, initAvatarPuppet() {},
      screenPassword: 'password', passwordInput: { focus() {} },
      showScreen(value) { screen = value; }, openChatScreen() { screen = 'chat'; },
    });
    vm.runInContext(settings + '\n' + initialize, context);
    await vm.runInContext('initialize()', context);
    assert.equal(screen, expected);
  });
}
