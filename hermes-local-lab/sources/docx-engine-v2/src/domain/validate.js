const Ajv = require('ajv');
const { schemas } = require('./schemas');

const validator = new Ajv({ allErrors: true });
const compiledValidators = new Map();

function getValidator(schemaName) {
  const schema = schemas[schemaName];
  if (!schema) {
    throw new Error(`Unknown schema: ${schemaName}`);
  }

  if (!compiledValidators.has(schemaName)) {
    compiledValidators.set(schemaName, validator.compile(schema));
  }

  return compiledValidators.get(schemaName);
}

function validateDomainObject(schemaName, value) {
  const validate = getValidator(schemaName);
  const ok = validate(value);

  return {
    ok,
    errors: ok
      ? []
      : (validate.errors || []).map((error) => ({
          path: error.instancePath || '/',
          message: error.message || 'invalid value',
          keyword: error.keyword,
        })),
  };
}

module.exports = { validateDomainObject };
