import { describe, expect, test } from "vitest";
import type {
  EvaluationMetricCatalog,
  EvaluationMetricCatalogItem,
  EvaluationMetricCategory
} from "../../features/evaluations/evaluationTypes";
import { groupMetricsByCategory, metricCategoryLabel } from "./MetricTaxonomy";

function catalogItem(
  metricName: string,
  category: EvaluationMetricCategory
): EvaluationMetricCatalogItem {
  return {
    metric_name: metricName,
    category,
    display_name: metricName,
    description: `${metricName} description`,
    higher_is_better: true,
    value_unit: "ratio",
    alias_of: null
  };
}

const catalog: EvaluationMetricCatalog = {
  schema_version: "phase3.evaluation_metric_taxonomy.v1",
  metrics: [
    catalogItem("mrr", "retrieval"),
    catalogItem("recall_at_k", "retrieval"),
    catalogItem("faithfulness", "answer"),
    catalogItem("citation_presence", "citation"),
    catalogItem("p95_latency", "performance")
  ]
};

describe("MetricTaxonomy", () => {
  test("groups in taxonomy order and preserves metric priority within a category", () => {
    const groups = groupMetricsByCategory(
      ["unknown_metric", "mrr", "p95_latency", "faithfulness", "recall_at_k", "citation_presence"],
      catalog,
      (metricName) => metricName
    );

    expect(groups.map((group) => group.category)).toEqual([
      "retrieval",
      "answer",
      "citation",
      "performance",
      "other"
    ]);
    expect(groups[0]?.items).toEqual(["recall_at_k", "mrr"]);
    expect(groups[groups.length - 1]?.items).toEqual(["unknown_metric"]);
  });

  test("uses other when the catalog is unavailable or a future category is unknown", () => {
    const withoutCatalog = groupMetricsByCategory(["future_metric"], undefined, (name) => name);
    const invalidCatalog = {
      ...catalog,
      metrics: [{ ...catalog.metrics[0], category: "future" }]
    } as unknown as EvaluationMetricCatalog;

    expect(withoutCatalog[0]?.category).toBe("other");
    expect(groupMetricsByCategory(["mrr"], invalidCatalog, (name) => name)[0]?.category).toBe(
      "other"
    );
    expect(metricCategoryLabel("other")).toBe("その他");
  });
});
