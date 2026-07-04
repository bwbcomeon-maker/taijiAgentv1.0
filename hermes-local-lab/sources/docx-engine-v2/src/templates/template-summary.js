function summarizeTemplate(template) {
  const manifest = template.manifest || {};
  return {
    id: template.id,
    name: manifest.name || template.id,
    description: manifest.description || '',
    documentTypes: manifest.documentTypes || [],
    capabilities: manifest.capabilities || [],
    sourceRequirements: manifest.sourceRequirements || {},
    requiredAssets: manifest.requiredAssets || [],
    qualityGates: manifest.qualityGates || [],
    compatibility: manifest.compatibility || {},
  };
}

module.exports = { summarizeTemplate };
