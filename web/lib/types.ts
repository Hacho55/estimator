export type ProjectType = "mobile_app" | "web_saas" | "internal_tool" | "data_pipeline";
export type DetailLevel = "summary" | "medium" | "detailed";
export type OutputFormat = "phases_table" | "line_items" | "narrative";

export type FormValues = {
  description: string;
  project_type: ProjectType;
  detail_level: DetailLevel;
  output_format: OutputFormat;
};

export const PROJECT_TYPE_OPTIONS: { value: ProjectType; label: string }[] = [
  { value: "mobile_app", label: "App móvil" },
  { value: "web_saas", label: "Web / SaaS" },
  { value: "internal_tool", label: "Herramienta interna" },
  { value: "data_pipeline", label: "Pipeline de datos" },
];

export const DETAIL_LEVEL_OPTIONS: { value: DetailLevel; label: string }[] = [
  { value: "summary", label: "Resumen" },
  { value: "medium", label: "Medio" },
  { value: "detailed", label: "Detallado" },
];

export const OUTPUT_FORMAT_OPTIONS: { value: OutputFormat; label: string }[] = [
  { value: "phases_table", label: "Tabla por fases" },
  { value: "line_items", label: "Partidas" },
  { value: "narrative", label: "Relato" },
];
