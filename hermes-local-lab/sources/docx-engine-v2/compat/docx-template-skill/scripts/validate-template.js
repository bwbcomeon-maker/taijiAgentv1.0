#!/usr/bin/env node

const { forwardResult, runEngine } = require('./lib/engine-cli');

forwardResult(runEngine('validate-template.js', process.argv.slice(2), { stdio: 'inherit' }));
