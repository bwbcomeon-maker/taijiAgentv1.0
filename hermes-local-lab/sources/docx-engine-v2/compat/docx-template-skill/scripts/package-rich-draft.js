#!/usr/bin/env node

const { forwardResult, runEngine } = require('./lib/engine-cli');

forwardResult(runEngine('package-rich-draft.js', process.argv.slice(2), { stdio: 'inherit' }));
