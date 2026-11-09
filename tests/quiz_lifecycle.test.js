'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {compileFunction} = require('node:vm');

function fixture() {
    const timers = [], requests = [], sent = [], writes = [];
    const module = {exports: {}};
    const fakeFs = {existsSync: () => false, writeFileSync: (_file, data) => writes.push(JSON.parse(data))};
    const fakeFetch = () => new Promise(resolve => requests.push(resolve));
    const load = compileFunction(fs.readFileSync(path.join(__dirname, '../quiz.js'), 'utf8'),
        ['require', 'module', 'exports', 'fetch', 'setTimeout']);
    load(name => name === 'fs' ? fakeFs : require(name), module, module.exports, fakeFetch,
        callback => timers.push(callback));
    const log = {info() {}, warn() {}, error() {}};
    const quiz = new module.exports.QuizHandler({chat: text => sent.push(text)}, log, log,
        {quiz: {enabled: true, minDelay: 1}, llm: {endpoint: 'https://llm.invalid', apiKey: 'fake'}});
    const finish = async (index, answer) => {
        requests[index]({ok: true, json: async () => ({choices: [{message: {content: answer}}]})});
        await new Promise(resolve => setImmediate(resolve));
    };
    // Exercise the actual engine command branch, including on the old baseline.
    const engine = fs.readFileSync(path.join(__dirname, '../bot_engine.js'), 'utf8');
    const command = engine.match(/case 'setQuiz':([\s\S]*?)\bbreak;/)[1];
    const runtime = {quiz: {enabled: true}};
    const setEnabled = enabled => compileFunction(command, ['cmd', 'runtime', 'quiz', 'log'])(
        {enabled}, runtime, quiz, () => {});
    return {quiz, timers, requests, sent, writes, finish, setEnabled};
}

test('late provider result cannot save or answer a newer question', async () => {
    const f = fixture();
    f.quiz.onActionBar('math: Q1');
    f.quiz.onActionBar('math: Q2');
    await f.finish(0, 'old');
    assert.equal(f.timers.length, 0);
    assert.equal(f.writes.length, 0);
    assert.equal(f.quiz.session.answered, false);
    await f.finish(1, 'new');
    f.timers.shift()();
    assert.deepEqual(f.sent, ['new']);
    assert.equal(f.quiz.questionBank.questions['math: Q1'], undefined);
    assert.equal(f.writes[0].questions['math: Q2'].answer, 'new');
});

test('changing question before delayed cached answer invalidates callback', async () => {
    const f = fixture();
    f.quiz.questionBank.questions['math: Q1'] = {answer: 'old'};
    f.quiz.onActionBar('math: Q1');
    f.quiz.onActionBar('math: Q2');
    f.timers.shift()();
    assert.deepEqual(f.sent, []);
    await f.finish(0, 'new');
    f.timers.shift()();
    assert.deepEqual(f.sent, ['new']);
});

test('disabled pending provider result is neither saved nor published', async () => {
    const f = fixture();
    f.quiz.onActionBar('math: Q1');
    f.setEnabled(false);
    await f.finish(0, 'old');
    assert.equal(f.quiz.session, null);
    assert.deepEqual(f.writes, []);
    assert.deepEqual(f.timers, []);
    assert.deepEqual(f.sent, []);
});

test('disabled cached timer cannot send and repeated disable stays inactive', () => {
    const f = fixture();
    f.quiz.questionBank.questions['math: Q1'] = {answer: 'old'};
    f.quiz.onActionBar('math: Q1');
    f.setEnabled(false);
    f.setEnabled(false);
    f.timers.shift()();
    assert.deepEqual(f.sent, []);
    assert.equal(f.quiz.session, null);
    f.quiz.onActionBar('math: ignored');
    assert.equal(f.requests.length, 0);
});

test('disable then enable cannot revive a cached timer or provider result', async () => {
    for (const cached of [false, true]) {
        const f = fixture();
        if (cached) f.quiz.questionBank.questions['math: Q1'] = {answer: 'old'};
        f.quiz.onActionBar('math: Q1');
        f.setEnabled(false);
        f.setEnabled(true);
        if (cached) f.timers.shift()();
        else await f.finish(0, 'old');
        assert.deepEqual(f.sent, []);
        assert.deepEqual(f.writes, []);
        assert.equal(f.quiz.session, null);
        f.quiz.onActionBar('math: Q2');
        await f.finish(cached ? 0 : 1, 'new');
        f.timers.shift()();
        assert.deepEqual(f.sent, ['new']);
    }
});

test('normal cache and provider answers send once and preserve bank', async () => {
    const f = fixture();
    f.quiz.questionBank.questions['math: cached'] = {answer: 'cached'};
    f.quiz.onActionBar('math: cached');
    const timer = f.timers.shift();
    timer(); timer();
    assert.deepEqual(f.sent, ['cached']);
    assert.equal(f.requests.length, 0);
    f.quiz.onActionBar('math: fresh');
    await f.finish(0, '<think>reason</think>答案是：2');
    f.timers.shift()();
    assert.deepEqual(f.sent, ['cached', '2']);
    assert.equal(f.writes[0].questions['math: fresh'].answer, '2');
    f.quiz.onActionBar('math: empty');
    await f.finish(1, '');
    assert.equal(f.timers.length, 0);
    assert.equal(f.writes.length, 1);
});
