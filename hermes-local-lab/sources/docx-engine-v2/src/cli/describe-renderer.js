#!/usr/bin/env node

const path = require('node:path');
const { describeRendererIdentity } = require('../workflow/run-document-job');

const profileIndex = process.argv.indexOf('--profile-id');
const profileId = profileIndex >= 0 ? process.argv[profileIndex + 1] : 'enterprise-default';
process.stdout.write(`${JSON.stringify({
  ok: true,
  rendererIdentity: describeRendererIdentity({
    engineRoot: path.resolve(__dirname, '../..'),
    profileId: profileId || 'enterprise-default',
  }),
})}\n`);
