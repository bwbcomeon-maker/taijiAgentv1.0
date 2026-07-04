#!/usr/bin/env node

const { forwardResult, runEngine } = require('./lib/engine-cli');

forwardResult(runEngine('record-wps-visual.js', process.argv.slice(2), { stdio: 'inherit' }));
