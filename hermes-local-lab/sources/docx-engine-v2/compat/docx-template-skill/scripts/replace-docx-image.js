#!/usr/bin/env node

const { forwardResult, runEngine } = require('./lib/engine-cli');

forwardResult(runEngine('replace-asset.js', process.argv.slice(2), { stdio: 'inherit' }));
