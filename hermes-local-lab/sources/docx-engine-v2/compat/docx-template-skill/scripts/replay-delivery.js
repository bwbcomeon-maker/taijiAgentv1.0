#!/usr/bin/env node

const { forwardResult, runEngine } = require('./lib/engine-cli');

forwardResult(runEngine('replay-delivery.js', process.argv.slice(2), { stdio: 'inherit' }));
