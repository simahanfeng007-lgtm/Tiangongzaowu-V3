import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../app/main.js', import.meta.url), 'utf8');
const network = source.slice(source.indexOf('function configureModelNetworkEnvironment('),
  source.indexOf('configureModelNetworkEnvironment(process.env);'));
const probe = source.slice(source.indexOf('async function probeProviderApiConnection('),
  source.indexOf('function modelRuntimeServiceName('));

test('desktop probe sends only an empty authenticated backend request, not renderer targets or keys', async () => {
  const calls = [];
  const context = vm.createContext({backendControlJsonRequest: async (...args) => {
    calls.push(JSON.parse(JSON.stringify(args)));
    return {statusCode: 200, payload: {ok: true, http_status: 200, native_tools_supported: false}};
  }});
  vm.runInContext(probe, context);
  const result = await context.probeProviderApiConnection({url: 'https://wrong.example', apiKey: 'do-not-send'});
  assert.equal(result.ok, true);
  assert.deepEqual(calls, [['POST', '/api/v1/llm/probe', {}, 30000]]);
});

test('uncertain backend failure is surfaced once without an automatic paid replay', async () => {
  let count = 0;
  const context = vm.createContext({backendControlJsonRequest: async () => {
    count += 1;
    return {statusCode: 0, payload: null};
  }});
  vm.runInContext(probe, context);
  const result = await context.probeProviderApiConnection();
  assert.equal(result.ok, false);
  assert.equal(result.error, 'model_probe_backend_unavailable');
  assert.equal(count, 1);
});

test('default mode stays direct and clears inherited proxy settings', () => {
  const context = vm.createContext({});
  vm.runInContext(network, context);
  const env = {HTTPS_PROXY: 'http://proxy.example:8080', http_proxy: 'http://other.example', ALL_PROXY: 'socks5://third.example'};
  context.configureModelNetworkEnvironment(env);
  assert.equal(env.NO_PROXY, '*');
  assert.equal(env.no_proxy, '*');
  assert.equal(env.HTTPS_PROXY, undefined);
  assert.equal(env.http_proxy, undefined);
  assert.equal(env.ALL_PROXY, undefined);
});

test('explicit proxy mode retains user settings and exempts loopback control channels', () => {
  const context = vm.createContext({});
  vm.runInContext(network, context);
  const env = {TIANGONG_HTTP_TRUST_ENV: '1', HTTPS_PROXY: 'http://proxy.example:8080',
    NO_PROXY: 'internal.example', no_proxy: '.intranet.example'};
  context.configureModelNetworkEnvironment(env);
  assert.equal(env.HTTPS_PROXY, 'http://proxy.example:8080');
  assert.equal(env.NO_PROXY, env.no_proxy);
  assert.deepEqual(new Set(env.NO_PROXY.split(',')), new Set(['internal.example', '.intranet.example', '127.0.0.1', 'localhost', '::1']));
});
