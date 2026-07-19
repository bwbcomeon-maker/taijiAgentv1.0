import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  Code,
  Download,
  FormInput,
  RotateCcw,
  Search,
  Upload,
  X,
  Settings2,
  FileText,
  Settings,
  Bot,
  Monitor,
  Palette,
  Users,
  Brain,
  Package,
  Lock,
  Globe,
  Mic,
  Volume2,
  Ear,
  ClipboardList,
  MessageCircle,
  Wrench,
  FileQuestion,
  Filter,
  Cloud,
  Sparkles,
  LayoutDashboard,
  BookOpen,
  Route,
  History,
  Shield,
  FileOutput,
  RefreshCw,
} from "lucide-react";
import { api, isConfigurationConflict } from "@/lib/api";
import { getNestedValue, setNestedValue } from "@/lib/nested";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { AutoField } from "@/components/AutoField";
import { Button } from "@nous-research/ui/ui/components/button";
import { ListItem } from "@nous-research/ui/ui/components/list-item";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Card, CardContent, CardHeader, CardTitle } from "@nous-research/ui/ui/components/card";
import { ConfirmDialog } from "@nous-research/ui/ui/components/confirm-dialog";
import { Input } from "@nous-research/ui/ui/components/input";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { useI18n } from "@/i18n";
import { usePageHeader } from "@/contexts/usePageHeader";
import { PluginSlot } from "@/plugins";

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const CATEGORY_ICONS: Record<
  string,
  React.ComponentType<{ className?: string }>
> = {
  general: Settings,
  agent: Bot,
  terminal: Monitor,
  display: Palette,
  delegation: Users,
  memory: Brain,
  compression: Package,
  security: Lock,
  browser: Globe,
  voice: Mic,
  tts: Volume2,
  stt: Ear,
  logging: ClipboardList,
  discord: MessageCircle,
  auxiliary: Wrench,
  bedrock: Cloud,
  curator: Sparkles,
  kanban: LayoutDashboard,
  model_catalog: BookOpen,
  openrouter: Route,
  sessions: History,
  tool_loop_guardrails: Shield,
  tool_output: FileOutput,
  updates: RefreshCw,
};

function CategoryIcon({
  category,
  className,
}: {
  category: string;
  className?: string;
}) {
  const Icon = CATEGORY_ICONS[category] ?? FileQuestion;
  return <Icon className={className ?? "h-4 w-4"} />;
}

async function fetchConfigDependencies() {
  const [draft, schemaResponse] = await Promise.all([
    api.getConfigDraft(),
    api.getSchema(),
  ]);
  return { draft, schemaResponse };
}

const CONFIG_VALUE_MISSING = Symbol("config-value-missing");
type ConfigMergeValue = unknown | typeof CONFIG_VALUE_MISSING;

function isConfigRecord(value: ConfigMergeValue): value is Record<string, unknown> {
  return (
    value !== CONFIG_VALUE_MISSING &&
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value)
  );
}

function configValuesEqual(
  left: ConfigMergeValue,
  right: ConfigMergeValue,
): boolean {
  if (left === right) return true;
  if (left === CONFIG_VALUE_MISSING || right === CONFIG_VALUE_MISSING) {
    return false;
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) =>
        configValuesEqual(value, right[index]),
      )
    );
  }
  if (isConfigRecord(left) || isConfigRecord(right)) {
    if (!isConfigRecord(left) || !isConfigRecord(right)) return false;
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return (
      leftKeys.length === rightKeys.length &&
      leftKeys.every(
        (key, index) =>
          key === rightKeys[index] &&
          configValuesEqual(left[key], right[key]),
      )
    );
  }
  return false;
}

function rebaseConfigValue(
  base: ConfigMergeValue,
  local: ConfigMergeValue,
  server: ConfigMergeValue,
): { conflict: boolean; value: ConfigMergeValue } {
  if (configValuesEqual(local, base)) {
    return { conflict: false, value: server };
  }
  if (
    configValuesEqual(server, base) ||
    configValuesEqual(local, server)
  ) {
    return { conflict: false, value: local };
  }
  if (
    !isConfigRecord(base) ||
    !isConfigRecord(local) ||
    !isConfigRecord(server)
  ) {
    return { conflict: true, value: local };
  }

  const merged: Record<string, unknown> = {};
  const keys = new Set([
    ...Object.keys(base),
    ...Object.keys(local),
    ...Object.keys(server),
  ]);
  for (const key of keys) {
    const result = rebaseConfigValue(
      Object.hasOwn(base, key) ? base[key] : CONFIG_VALUE_MISSING,
      Object.hasOwn(local, key) ? local[key] : CONFIG_VALUE_MISSING,
      Object.hasOwn(server, key) ? server[key] : CONFIG_VALUE_MISSING,
    );
    if (result.conflict) return result;
    if (result.value !== CONFIG_VALUE_MISSING) {
      merged[key] = result.value;
    }
  }
  return { conflict: false, value: merged };
}

function rebaseConfigDraft(
  base: Record<string, unknown>,
  local: Record<string, unknown>,
  server: Record<string, unknown>,
): Record<string, unknown> | null {
  const result = rebaseConfigValue(base, local, server);
  return result.conflict || !isConfigRecord(result.value)
    ? null
    : result.value;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function ConfigPage() {
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [configLoadError, setConfigLoadError] = useState(false);
  const [configSnapshotToken, setConfigSnapshotToken] = useState<string | null>(
    null,
  );
  const [schema, setSchema] = useState<Record<
    string,
    Record<string, unknown>
  > | null>(null);
  const [categoryOrder, setCategoryOrder] = useState<string[]>([]);
  const [defaults, setDefaults] = useState<Record<string, unknown> | null>(
    null,
  );
  const [saving, setSaving] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [yamlMode, setYamlMode] = useState(false);
  const [yamlText, setYamlText] = useState("");
  const [yamlSnapshotToken, setYamlSnapshotToken] = useState<string | null>(
    null,
  );
  const [yamlEditable, setYamlEditable] = useState(false);
  const [yamlBlockedCode, setYamlBlockedCode] = useState<string | null>(
    null,
  );
  const [yamlBlockedFallback, setYamlBlockedFallback] = useState<string | null>(
    null,
  );
  const [yamlLoadError, setYamlLoadError] = useState(false);
  const [yamlLoading, setYamlLoading] = useState(false);
  const [yamlSaving, setYamlSaving] = useState(false);
  const [configPath, setConfigPath] = useState<string | null>(null);
  const [selectedCategory, setSelectedCategory] = useState<string>("");
  const [confirmReset, setConfirmReset] = useState(false);
  const [confirmReloadDraft, setConfirmReloadDraft] = useState<
    "config" | "yaml" | null
  >(null);
  const { toast, showToast } = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const latestConfigRef = useRef<Record<string, unknown> | null>(config);
  const configRevisionRef = useRef(0);
  const configSyncedRevisionRef = useRef(0);
  const configRequestGenerationRef = useRef(0);
  const yamlRevisionRef = useRef(0);
  const yamlRequestGenerationRef = useRef(0);
  const yamlDraftLoadedRef = useRef(false);
  const { t } = useI18n();
  const { setEnd } = usePageHeader();

  useLayoutEffect(() => {
    latestConfigRef.current = config;
  }, [config]);

  useLayoutEffect(() => {
    if (!config || !schema) {
      setEnd(null);
      return;
    }
    setEnd(
      <div className="relative w-full min-w-0 sm:max-w-xs">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
        <Input
          className="h-8 pl-8 pr-7 text-xs"
          placeholder={t.common.search}
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
        {searchQuery && (
          <Button
            ghost
            size="xs"
            className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            onClick={() => setSearchQuery("")}
            aria-label={t.common.clear}
          >
            <X />
          </Button>
        )}
      </div>,
    );
    return () => setEnd(null);
  }, [config, schema, searchQuery, setEnd, t.common.clear, t.common.search]);

  function prettyCategoryName(cat: string): string {
    const key = cat as keyof typeof t.config.categories;
    if (t.config.categories[key]) return t.config.categories[key];
    return cat.charAt(0).toUpperCase() + cat.slice(1);
  }

  useEffect(() => {
    const generation = ++configRequestGenerationRef.current;
    void fetchConfigDependencies()
      .then(({ draft, schemaResponse }) => {
        if (generation !== configRequestGenerationRef.current) return;
        setConfig(draft.config);
        setConfigSnapshotToken(draft.snapshot_token);
        setSchema(
          schemaResponse.fields as Record<
            string,
            Record<string, unknown>
          >,
        );
        setCategoryOrder(schemaResponse.category_order ?? []);
        setConfigLoadError(false);
        configSyncedRevisionRef.current = configRevisionRef.current;
      })
      .catch(() => {
        if (generation !== configRequestGenerationRef.current) return;
        setConfig(null);
        setConfigSnapshotToken(null);
        setSchema(null);
        setCategoryOrder([]);
        setConfigLoadError(true);
      });
    api
      .getDefaults()
      .then(setDefaults)
      .catch(() => {});
    api
      .getStatus()
      .then((resp) => setConfigPath(resp.config_path))
      .catch(() => {});
    return () => {
      if (generation === configRequestGenerationRef.current) {
        configRequestGenerationRef.current += 1;
      }
    };
  }, []);

  /* ---- Categories ---- */
  const categories = (() => {
    if (!schema) return [];
    const allCats = [
      ...new Set(
        Object.values(schema).map((s) => String(s.category ?? "general")),
      ),
    ];
    const ordered = categoryOrder.filter((c) => allCats.includes(c));
    const extra = allCats.filter((c) => !categoryOrder.includes(c)).sort();
    return [...ordered, ...extra];
  })();
  const activeCategory = selectedCategory || categories[0] || "";

  /* ---- Category field counts ---- */
  const categoryCounts = (() => {
    if (!schema) return {};
    const counts: Record<string, number> = {};
    for (const s of Object.values(schema)) {
      const cat = String(s.category ?? "general");
      counts[cat] = (counts[cat] || 0) + 1;
    }
    return counts;
  })();

  /* ---- Search ---- */
  const isSearching = searchQuery.trim().length > 0;
  const lowerSearch = searchQuery.toLowerCase();

  const searchMatchedFields = (() => {
    if (!isSearching || !schema) return [];
    return Object.entries(schema).filter(([key, s]) => {
      const label = key.split(".").pop() ?? key;
      const humanLabel = label.replace(/_/g, " ");
      return (
        key.toLowerCase().includes(lowerSearch) ||
        humanLabel.toLowerCase().includes(lowerSearch) ||
        String(s.category ?? "")
          .toLowerCase()
          .includes(lowerSearch) ||
        String(s.description ?? "")
          .toLowerCase()
          .includes(lowerSearch)
      );
    });
  })();

  /* ---- Active tab fields ---- */
  const activeFields = (() => {
    if (!schema || isSearching) return [];
    return Object.entries(schema).filter(
      ([, s]) => String(s.category ?? "general") === activeCategory,
    );
  })();

  /* ---- Handlers ---- */
  const handleSave = async () => {
    if (!config || !configSnapshotToken) return;
    const configAtSave = structuredClone(config);
    const revisionAtSave = configRevisionRef.current;
    const generation = ++configRequestGenerationRef.current;
    setSaving(true);
    try {
      await api.saveConfig(config, configSnapshotToken);
    } catch (e) {
      if (isConfigurationConflict(e)) {
        // Keep the unsaved draft visible, but invalidate its stale write
        // authority.  The explicit Retry button reloads only on user action.
        setConfigSnapshotToken(null);
      }
      showToast(`${t.config.failedToSave}: ${e}`, "error");
      if (generation === configRequestGenerationRef.current) {
        setSaving(false);
      }
      return;
    }

    setConfigSnapshotToken(null);
    showToast(t.config.configSaved, "success");
    if (revisionAtSave !== configRevisionRef.current) {
      try {
        const refreshed = await api.getConfigDraft();
        if (generation !== configRequestGenerationRef.current) return;
        const latestConfig = latestConfigRef.current;
        const rebased = latestConfig
          ? rebaseConfigDraft(
              configAtSave,
              latestConfig,
              refreshed.config,
            )
          : null;
        if (!rebased) {
          showToast(
            `${t.config.configSaved}. ${t.common.refresh}: ${t.common.retry}.`,
            "error",
          );
          return;
        }
        latestConfigRef.current = rebased;
        setConfig(rebased);
        setConfigSnapshotToken(refreshed.snapshot_token);
      } catch (e) {
        if (generation === configRequestGenerationRef.current) {
          showToast(
            `${t.config.configSaved}. ${t.common.refresh}: ${e}. ${t.common.retry}.`,
            "error",
          );
        }
      } finally {
        if (generation === configRequestGenerationRef.current) {
          setSaving(false);
        }
      }
      return;
    }
    try {
      const refreshed = await api.getConfigDraft();
      if (
        generation !== configRequestGenerationRef.current ||
        revisionAtSave !== configRevisionRef.current
      ) {
        return;
      }
      setConfig(refreshed.config);
      setConfigSnapshotToken(refreshed.snapshot_token);
      configSyncedRevisionRef.current = configRevisionRef.current;
    } catch (e) {
      if (generation === configRequestGenerationRef.current) {
        showToast(
          `${t.config.configSaved}. ${t.common.refresh}: ${e}. ${t.common.retry}.`,
          "error",
        );
      }
    } finally {
      if (generation === configRequestGenerationRef.current) {
        setSaving(false);
      }
    }
  };

  const handleYamlSave = async () => {
    if (!yamlSnapshotToken) return;
    const revisionAtSave = yamlRevisionRef.current;
    const yamlGeneration = ++yamlRequestGenerationRef.current;
    const configRevisionAtSave = configRevisionRef.current;
    const formWasClean =
      configRevisionAtSave === configSyncedRevisionRef.current;
    setYamlSaving(true);
    try {
      await api.saveConfigRaw(yamlText, yamlSnapshotToken);
    } catch (e) {
      if (isConfigurationConflict(e)) {
        setYamlSnapshotToken(null);
      }
      showToast(`${t.config.failedToSaveYaml}: ${e}`, "error");
      if (yamlGeneration === yamlRequestGenerationRef.current) {
        setYamlSaving(false);
      }
      return;
    }

    setYamlSnapshotToken(null);
    setConfigSnapshotToken(null);
    showToast(t.config.yamlConfigSaved, "success");
    if (revisionAtSave !== yamlRevisionRef.current) {
      setYamlSaving(false);
      return;
    }
    const configGeneration = ++configRequestGenerationRef.current;
    try {
      const [rawDraft, formDraft] = await Promise.all([
        api.getConfigRaw(),
        formWasClean ? api.getConfigDraft() : Promise.resolve(null),
      ]);
      if (
        yamlGeneration === yamlRequestGenerationRef.current &&
        revisionAtSave === yamlRevisionRef.current
      ) {
        setYamlText(rawDraft.yaml);
        setYamlSnapshotToken(rawDraft.snapshot_token);
        setYamlEditable(rawDraft.editable);
        setYamlBlockedCode(rawDraft.blocked_code);
        setYamlBlockedFallback(rawDraft.blocked_reason);
        setYamlLoadError(false);
      }
      if (
        formDraft &&
        configGeneration === configRequestGenerationRef.current &&
        configRevisionAtSave === configRevisionRef.current
      ) {
        setConfig(formDraft.config);
        setConfigSnapshotToken(formDraft.snapshot_token);
        configSyncedRevisionRef.current = configRevisionRef.current;
      }
    } catch (e) {
      if (yamlGeneration === yamlRequestGenerationRef.current) {
        showToast(
          `${t.config.yamlConfigSaved}. ${t.common.refresh}: ${e}. ${t.common.retry}.`,
          "error",
        );
      }
    } finally {
      if (yamlGeneration === yamlRequestGenerationRef.current) {
        setYamlSaving(false);
      }
    }
  };

  const handleReloadConfigDraft = async () => {
    const generation = ++configRequestGenerationRef.current;
    configRevisionRef.current += 1;
    const revisionAtLoad = configRevisionRef.current;
    setSaving(true);
    try {
      const refreshed = await api.getConfigDraft();
      if (
        generation !== configRequestGenerationRef.current ||
        revisionAtLoad !== configRevisionRef.current
      ) {
        return;
      }
      setConfig(refreshed.config);
      setConfigSnapshotToken(refreshed.snapshot_token);
      setConfigLoadError(false);
      configSyncedRevisionRef.current = configRevisionRef.current;
    } catch (e) {
      if (generation === configRequestGenerationRef.current) {
        showToast(`${t.common.refresh}: ${e}`, "error");
      }
    } finally {
      if (generation === configRequestGenerationRef.current) {
        setSaving(false);
      }
    }
  };

  const handleReloadYamlDraft = async () => {
    const generation = ++yamlRequestGenerationRef.current;
    yamlDraftLoadedRef.current = false;
    setYamlLoading(true);
    setYamlLoadError(false);
    setYamlEditable(false);
    setYamlSnapshotToken(null);
    setYamlBlockedCode(null);
    setYamlBlockedFallback(null);
    try {
      const refreshed = await api.getConfigRaw();
      if (generation !== yamlRequestGenerationRef.current) return;
      setYamlText(refreshed.yaml);
      setYamlSnapshotToken(refreshed.snapshot_token);
      setYamlEditable(refreshed.editable);
      setYamlBlockedCode(refreshed.blocked_code);
      setYamlBlockedFallback(refreshed.blocked_reason);
      setYamlLoadError(false);
      yamlDraftLoadedRef.current = true;
      yamlRevisionRef.current += 1;
    } catch (e) {
      if (generation === yamlRequestGenerationRef.current) {
        setYamlLoadError(true);
        showToast(`${t.common.refresh}: ${e}`, "error");
      }
    } finally {
      if (generation === yamlRequestGenerationRef.current) {
        setYamlLoading(false);
      }
    }
  };

  const handleToggleYamlMode = () => {
    if (yamlMode) {
      yamlRequestGenerationRef.current += 1;
      setYamlLoading(false);
      setYamlMode(false);
      return;
    }
    setYamlMode(true);
    if (!yamlDraftLoadedRef.current) {
      void handleReloadYamlDraft();
    }
  };

  const handleRetryInitialConfig = async () => {
    if (config && schema) return;
    const generation = ++configRequestGenerationRef.current;
    setSaving(true);
    setConfigLoadError(false);
    try {
      const { draft, schemaResponse } = await fetchConfigDependencies();
      if (generation !== configRequestGenerationRef.current) return;
      setConfig(draft.config);
      setConfigSnapshotToken(draft.snapshot_token);
      setSchema(
        schemaResponse.fields as Record<string, Record<string, unknown>>,
      );
      setCategoryOrder(schemaResponse.category_order ?? []);
      setConfigLoadError(false);
      configSyncedRevisionRef.current = configRevisionRef.current;
    } catch {
      if (generation === configRequestGenerationRef.current) {
        setConfig(null);
        setConfigSnapshotToken(null);
        setSchema(null);
        setCategoryOrder([]);
        setConfigLoadError(true);
      }
    } finally {
      if (generation === configRequestGenerationRef.current) {
        setSaving(false);
      }
    }
  };

  const executeReloadDraft = () => {
    const target = confirmReloadDraft;
    setConfirmReloadDraft(null);
    if (target === "config") {
      void handleReloadConfigDraft();
    } else if (target === "yaml") {
      void handleReloadYamlDraft();
    }
  };

  const handleReset = () => {
    if (!defaults || !config) return;
    // Scope the reset to what the user is currently looking at:
    //   - search mode → the matched fields
    //   - form mode   → the active category's fields
    // Resetting the whole config here was a footgun (issue reported by @ykmfb001):
    // the button sits next to the category tabs and users reasonably assumed
    // "reset this tab", not "wipe my entire config.yaml".
    const scopedFields = isSearching ? searchMatchedFields : activeFields;
    if (scopedFields.length === 0) return;
    setConfirmReset(true);
  };

  const executeReset = () => {
    if (!defaults || !config) return;
    setConfirmReset(false);
    const scopedFields = isSearching ? searchMatchedFields : activeFields;
    if (scopedFields.length === 0) return;
    const scopeLabel = isSearching
      ? t.config.searchResults
      : prettyCategoryName(activeCategory);
    let next: Record<string, unknown> = config;
    for (const [key] of scopedFields) {
      next = setNestedValue(next, key, getNestedValue(defaults, key));
    }
    configRevisionRef.current += 1;
    setConfig(next);
    showToast(
      t.config.resetScopeToast.replace("{scope}", scopeLabel),
      "success",
    );
  };

  const handleExport = () => {
    if (!config) return;
    const blob = new Blob([JSON.stringify(config, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "hermes-config.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const imported = JSON.parse(reader.result as string);
        configRevisionRef.current += 1;
        setConfig(imported);
        showToast(t.config.configImported, "success");
      } catch {
        showToast(t.config.invalidJson, "error");
      }
    };
    reader.readAsText(file);
  };

  const yamlBlockedMessages: Record<string, string> = {
    invalid_utf8: t.config.rawBlockedInvalidUtf8,
    literal_credentials: t.config.rawBlockedLiteralCredentials,
    too_large: t.config.rawBlockedTooLarge,
    unsafe_file: t.config.rawBlockedUnsafeFile,
    unsafe_yaml: t.config.rawBlockedUnsafeYaml,
  };
  const yamlBlockedMessage = yamlBlockedCode
    ? (yamlBlockedMessages[yamlBlockedCode] ??
      yamlBlockedFallback ??
      t.config.rawBlockedUnknown)
    : yamlBlockedFallback;

  /* ---- Loading ---- */
  if (!config && configLoadError) {
    return (
      <div
        className="flex flex-col items-center justify-center gap-3 py-24"
        role="alert"
      >
        <p className="text-sm text-destructive">
          {t.config.configLoadFailed}
        </p>
        <Button outlined onClick={handleRetryInitialConfig} disabled={saving}>
          {t.config.retryConfigLoad}
        </Button>
      </div>
    );
  }
  if (!config || !schema) {
    return (
      <div
        className="flex items-center justify-center py-24"
        role="status"
        aria-live="polite"
      >
        <Spinner className="text-2xl text-primary" />
        <span className="sr-only">{t.config.loadingConfig}</span>
      </div>
    );
  }

  /* ---- Render field list (shared between search & normal) ---- */
  const renderFields = (
    fields: [string, Record<string, unknown>][],
    showCategory = false,
  ) => {
    let lastSection = "";
    let lastCat = "";
    return fields.map(([key, s]) => {
      const parts = key.split(".");
      const section = parts.length > 1 ? parts[0] : "";
      const cat = String(s.category ?? "general");
      const showCatBadge = showCategory && cat !== lastCat;
      const showSection =
        !showCategory &&
        section &&
        section !== lastSection &&
        section !== activeCategory;
      lastSection = section;
      lastCat = cat;

      return (
        <div key={key}>
          {showCatBadge && (
            <div className="flex items-center gap-2 pt-4 pb-2 first:pt-0">
              <CategoryIcon
                category={cat}
                className="h-4 w-4 text-muted-foreground"
              />
              <span className="font-mondwest text-display text-xs font-semibold tracking-wider text-muted-foreground">
                {prettyCategoryName(cat)}
              </span>
              <div className="flex-1 border-t border-border" />
            </div>
          )}
          {showSection && (
            <div className="flex items-center gap-2 pt-4 pb-2 first:pt-0">
              <span className="font-mondwest text-display text-xs font-semibold tracking-wider text-muted-foreground">
                {section.replace(/_/g, " ")}
              </span>
              <div className="flex-1 border-t border-border" />
            </div>
          )}
          <div className="py-1">
            <AutoField
              schemaKey={key}
              schema={s}
              value={getNestedValue(config, key)}
              onChange={(v) => {
                configRevisionRef.current += 1;
                const next = setNestedValue(config, key, v);
                setConfig(next);
              }}
            />
          </div>
        </div>
      );
    });
  };

  return (
    <div className="flex flex-col gap-4">
      <PluginSlot name="config:top" />
      <Toast toast={toast} />

      <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
        <div className="flex min-w-0 items-center gap-2 sm:flex-1">
          <Settings2 className="h-4 w-4 shrink-0 text-muted-foreground" />
          <code className="min-w-0 flex-1 break-words text-xs text-muted-foreground bg-muted/50 px-2 py-0.5">
            {configPath ?? t.config.configPath}
          </code>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 sm:shrink-0">
          <Button
            ghost
            size="icon"
            onClick={handleExport}
            title={t.config.exportConfig}
            aria-label={t.config.exportConfig}
          >
            <Download />
          </Button>
          <Button
            ghost
            size="icon"
            onClick={() => fileInputRef.current?.click()}
            title={t.config.importConfig}
            aria-label={t.config.importConfig}
          >
            <Upload />
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".json"
            className="hidden"
            onChange={handleImport}
          />
          {!yamlMode &&
            (() => {
              const resetScopeLabel = isSearching
                ? t.config.searchResults
                : prettyCategoryName(activeCategory);
              const resetTitle = t.config.resetScopeTooltip.replace(
                "{scope}",
                resetScopeLabel,
              );
              return (
                <Button
                  ghost
                  size="icon"
                  onClick={handleReset}
                  title={resetTitle}
                  aria-label={resetTitle}
                >
                  <RotateCcw />
                </Button>
              );
            })()}

          <div className="w-px h-5 bg-border mx-1" />

          <Button
            size="sm"
            outlined={!yamlMode}
            onClick={handleToggleYamlMode}
            disabled={saving || yamlSaving}
            prefix={yamlMode ? <FormInput /> : <Code />}
          >
            {yamlMode ? t.common.form : "YAML"}
          </Button>

          {yamlMode ? (
            <>
              {(yamlLoadError || yamlBlockedMessage) && !yamlLoading ? (
                <Button
                  size="sm"
                  outlined
                  onClick={handleReloadYamlDraft}
                >
                  {t.config.retryRawLoad}
                </Button>
              ) : (
                !yamlSnapshotToken &&
                !yamlLoading &&
                !yamlBlockedMessage && (
                  <Button
                    size="sm"
                    outlined
                    onClick={() => setConfirmReloadDraft("yaml")}
                  >
                    {t.config.reloadDiscard}
                  </Button>
                )
              )}
              {!yamlBlockedMessage && (
                <Button
                  size="sm"
                  className="uppercase"
                  onClick={handleYamlSave}
                  disabled={
                    yamlSaving || !yamlEditable || !yamlSnapshotToken
                  }
                >
                  {yamlSaving ? t.common.saving : t.common.save}
                </Button>
              )}
            </>
          ) : (
            <>
              {!configSnapshotToken && !saving && (
                <Button
                  size="sm"
                  outlined
                  onClick={() => setConfirmReloadDraft("config")}
                >
                  {t.config.reloadDiscard}
                </Button>
              )}
              <Button
                size="sm"
                className="uppercase"
                onClick={handleSave}
                disabled={saving || !configSnapshotToken}
              >
                {saving ? t.common.saving : t.common.save}
              </Button>
            </>
          )}
        </div>
      </div>

      {yamlMode ? (
        <Card>
          <CardHeader className="py-3 px-4">
            <CardTitle className="text-sm flex items-center gap-2">
              <FileText className="h-4 w-4" />
              {t.config.rawYaml}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {yamlLoading ? (
              <div
                className="flex items-center justify-center py-12"
                role="status"
                aria-live="polite"
              >
                <Spinner className="text-xl text-primary" />
                <span className="sr-only">{t.config.loadingConfig}</span>
              </div>
            ) : yamlLoadError ? (
              <div
                className="border-t border-border px-4 py-6 text-sm text-destructive"
                role="alert"
              >
                {t.config.failedToLoadRaw}
              </div>
            ) : yamlBlockedMessage ? (
              <div
                className="border-t border-border px-4 py-6 text-sm text-destructive"
                role="alert"
              >
                {yamlBlockedMessage}
              </div>
            ) : (
              <textarea
                className="flex min-h-[600px] w-full bg-transparent px-4 py-3 text-sm font-mono leading-relaxed placeholder:text-muted-foreground focus-visible:outline-none border-t border-border"
                aria-label={t.config.rawYaml}
                value={yamlText}
                onChange={(e) => {
                  yamlRevisionRef.current += 1;
                  setYamlText(e.target.value);
                }}
                spellCheck={false}
              />
            )}
          </CardContent>
        </Card>
      ) : (
        <div className="flex flex-col sm:flex-row gap-4">
          <aside aria-label={t.config.filters} className="sm:w-56 sm:shrink-0">
            <div className="sm:sticky sm:top-4">
              <div className="flex flex-col border border-border bg-muted/20">
                <div className="hidden sm:flex items-center gap-2 px-3 py-2 border-b border-border">
                  <Filter className="h-3 w-3 text-text-tertiary" />
                  <span className="font-mondwest text-display text-xs tracking-[0.12em] text-text-secondary">
                    {t.config.filters}
                  </span>
                </div>

                <div className="hidden sm:block px-3 pt-2 pb-1 font-mondwest text-display text-xs tracking-[0.12em] text-text-tertiary">
                  {t.config.sections}
                </div>

                <div className="flex sm:flex-col gap-1 sm:gap-px p-2 sm:pt-1 overflow-x-auto sm:overflow-x-visible scrollbar-none sm:max-h-[calc(100vh-260px)] sm:overflow-y-auto">
                  {categories.map((cat) => {
                    const isActive = !isSearching && activeCategory === cat;

                    return (
                      <ListItem
                        key={cat}
                        active={isActive}
                        onClick={() => {
                          setSearchQuery("");
                          setSelectedCategory(cat);
                        }}
                        className="rounded-none whitespace-nowrap px-2 py-1 text-xs"
                      >
                        <CategoryIcon
                          category={cat}
                          className="h-3.5 w-3.5 shrink-0"
                        />
                        <span className="flex-1 truncate">
                          {prettyCategoryName(cat)}
                        </span>
                        <span
                          className={`text-xs tabular-nums ${
                            isActive
                              ? "text-text-secondary"
                              : "text-text-tertiary"
                          }`}
                        >
                          {categoryCounts[cat] || 0}
                        </span>
                      </ListItem>
                    );
                  })}
                </div>
              </div>
            </div>
          </aside>

          <div className="flex-1 min-w-0">
            {isSearching ? (
              <Card>
                <CardHeader className="py-3 px-4">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-sm flex items-center gap-2">
                      <Search className="h-4 w-4" />
                      {t.config.searchResults}
                    </CardTitle>
                    <Badge tone="secondary" className="text-xs">
                      {searchMatchedFields.length}{" "}
                      {t.config.fields.replace(
                        "{s}",
                        searchMatchedFields.length !== 1 ? "s" : "",
                      )}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent className="grid gap-2 px-4 pb-4">
                  {searchMatchedFields.length === 0 ? (
                    <p className="text-sm text-muted-foreground text-center py-8">
                      {t.config.noFieldsMatch.replace("{query}", searchQuery)}
                    </p>
                  ) : (
                    renderFields(searchMatchedFields, true)
                  )}
                </CardContent>
              </Card>
            ) : (
              /* Active category */
              <Card>
                <CardHeader className="py-3 px-4">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-sm flex items-center gap-2">
                      <CategoryIcon
                        category={activeCategory}
                        className="h-4 w-4"
                      />
                      {prettyCategoryName(activeCategory)}
                    </CardTitle>
                    <Badge tone="secondary" className="text-xs">
                      {activeFields.length}{" "}
                      {t.config.fields.replace(
                        "{s}",
                        activeFields.length !== 1 ? "s" : "",
                      )}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent className="grid gap-2 px-4 pb-4">
                  {renderFields(activeFields)}
                </CardContent>
              </Card>
            )}
          </div>
        </div>
      )}
      <PluginSlot name="config:bottom" />
      <ConfirmDialog
        open={confirmReset}
        onCancel={() => setConfirmReset(false)}
        onConfirm={executeReset}
        title={t.config.confirmResetScope.replace(
          "{scope}",
          isSearching
            ? t.config.searchResults
            : prettyCategoryName(activeCategory),
        )}
        description={t.config.resetDescription.replace(
          "{count}",
          String((isSearching ? searchMatchedFields : activeFields).length),
        )}
        destructive
        confirmLabel={t.config.resetDefaults}
      />
      <ConfirmDialog
        open={confirmReloadDraft !== null}
        onCancel={() => setConfirmReloadDraft(null)}
        onConfirm={executeReloadDraft}
        title={t.config.reloadDiscardTitle}
        description={t.config.reloadDiscardDescription}
        destructive
        confirmLabel={t.config.reloadDiscardConfirm}
      />
    </div>
  );
}
