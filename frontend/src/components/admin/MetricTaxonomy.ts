import type {
  EvaluationMetricCatalog,
  EvaluationMetricCategory
} from "../../features/evaluations/evaluationTypes";
import { compareMetricNames } from "./MetricHelp";

export type DisplayMetricCategory = EvaluationMetricCategory | "other";

export type MetricCategoryGroup<T> = {
  category: DisplayMetricCategory;
  label: string;
  items: T[];
};

export const METRIC_CATEGORY_ORDER: DisplayMetricCategory[] = [
  "retrieval",
  "answer",
  "citation",
  "routing",
  "graph",
  "performance",
  "other"
];

const METRIC_CATEGORY_LABELS: Record<DisplayMetricCategory, string> = {
  retrieval: "検索品質",
  answer: "回答品質",
  citation: "引用品質",
  routing: "ルーティング",
  graph: "GraphRAG",
  performance: "性能",
  other: "その他"
};

const KNOWN_METRIC_CATEGORIES = new Set<EvaluationMetricCategory>([
  "retrieval",
  "answer",
  "citation",
  "routing",
  "graph",
  "performance"
]);

export function metricCategoryLabel(category: DisplayMetricCategory): string {
  return METRIC_CATEGORY_LABELS[category];
}

export function groupMetricsByCategory<T>(
  items: T[],
  catalog: EvaluationMetricCatalog | undefined,
  metricName: (item: T) => string
): MetricCategoryGroup<T>[] {
  const catalogCategories = new Map(
    (catalog?.metrics ?? []).map((metric) => [metric.metric_name, metric.category])
  );
  const grouped = new Map<DisplayMetricCategory, T[]>();

  for (const item of items) {
    const configuredCategory = catalogCategories.get(metricName(item));
    const category =
      configuredCategory && KNOWN_METRIC_CATEGORIES.has(configuredCategory)
        ? configuredCategory
        : "other";
    grouped.set(category, [...(grouped.get(category) ?? []), item]);
  }

  return METRIC_CATEGORY_ORDER.flatMap((category) => {
    const categoryItems = grouped.get(category);
    if (!categoryItems?.length) {
      return [];
    }
    return [
      {
        category,
        label: metricCategoryLabel(category),
        items: [...categoryItems].sort((left, right) =>
          compareMetricNames(metricName(left), metricName(right))
        )
      }
    ];
  });
}
